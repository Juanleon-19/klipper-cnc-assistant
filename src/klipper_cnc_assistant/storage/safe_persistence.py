"""Short filesystem transactions; never call workflow/hardware code under locks."""
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading


class PersistenceConflict(RuntimeError):
    """A stale writer changed a field already changed by another writer."""


_registry_guard = threading.Lock()
_locks = {}
_snapshots = {}
_local = threading.local()
_MISSING = object()


@contextmanager
def storage_lock(path):
    """Per canonical path RLock + process lock, reentrant within this thread."""
    path = Path(path).resolve()
    key = str(path)
    with _registry_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_local, 'held', None)
        if held is None:
            held = _local.held = set()
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name('.' + path.name + '.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_write(path, content, *, durable=False):
    """Replace only after a complete write; critical data also syncs directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content.encode('utf-8') if isinstance(content, str) else content)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(name, path)
        if durable:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, payload, *, durable=False):
    atomic_write(path, json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), durable=durable)


def remember_snapshot(path, payload):
    """Bounded merge bases. Unavailable bases cause rejection, never overwrite."""
    key = str(Path(path).resolve())
    with _registry_guard:
        history = _snapshots.setdefault(key, OrderedDict())
        history[payload.get('storage_revision', 0)] = deepcopy(payload)
        while len(history) > 32:
            history.popitem(last=False)


def read_json_snapshot(path):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    remember_snapshot(path, payload)
    return payload


def _merge(base, proposed, current, field=''):
    if proposed == base:
        return deepcopy(current) if current is not _MISSING else _MISSING
    if current == base or current == proposed:
        return deepcopy(proposed) if proposed is not _MISSING else _MISSING
    if all(isinstance(value, dict) for value in (base, proposed, current)):
        result = {}
        for key in base.keys() | proposed.keys() | current.keys():
            # These are observation timestamps, not physical/domain authority.
            if key in {'updated_at', 'actualizado_en', 'last_opened_at'}:
                value = max((v for v in (proposed.get(key), current.get(key)) if v is not None), default=None)
            elif key == 'storage_revision':
                value = current.get(key, 0)
            elif key == 'physical_reference_token':
                # A physical identity is indivisible; never synthesize a token.
                b, p, c = base.get(key), proposed.get(key), current.get(key)
                if p != b and c != b and p != c:
                    raise PersistenceConflict('Referencias físicas concurrentes incompatibles.')
                value = deepcopy(c if p == b else p)
            else:
                value = _merge(base.get(key, _MISSING), proposed.get(key, _MISSING), current.get(key, _MISSING), field + '/' + str(key))
            if value is not _MISSING:
                result[key] = value
        return result
    if all(isinstance(value, list) for value in (base, proposed, current)):
        # Domain collections have stable ids; permit independent item changes.
        if all(isinstance(item, dict) and 'id' in item for values in (base, proposed, current) for item in values):
            maps = [{item['id']: item for item in values} for values in (base, proposed, current)]
            if all(len(mapping) == len(values) for mapping, values in zip(maps, (base, proposed, current))):
                merged = _merge(*maps, field)
                order = list(dict.fromkeys([item['id'] for item in current + proposed]))
                return [merged[key] for key in order if key in merged]
        # Map samples without ids can still have independent index updates.
        if len(base) == len(proposed) == len(current):
            return [_merge(b, p, c, field + '/' + str(i)) for i, (b, p, c) in enumerate(zip(base, proposed, current))]
    raise PersistenceConflict('Actualización concurrente incompatible: ' + field)


def merge_snapshot(path, proposed, current):
    if current is None:
        remember_snapshot(path, proposed)
        result = deepcopy(proposed)
    elif proposed.get('storage_revision', 0) == current.get('storage_revision', 0):
        result = deepcopy(proposed)
    else:
        with _registry_guard:
            base = deepcopy(_snapshots.get(str(Path(path).resolve()), {}).get(proposed.get('storage_revision', 0)))
        if base is None:
            raise PersistenceConflict('Snapshot obsoleto sin base de fusión; recargue el recurso.')
        result = _merge(base, proposed, current)
    result['storage_revision'] = (current or {}).get('storage_revision', 0) + 1
    return result


def patch_metadata(path, patch):
    """Enrichment cannot modify identity, provenance or physical authority."""
    allowed = {'time_estimate', 'enrichment', 'warnings'}
    if not patch.keys() <= allowed:
        raise PersistenceConflict('El patch intenta modificar identidad o autoridad del artefacto.')
    with storage_lock(path):
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        _apply_patch(payload, patch)
        atomic_json(path, payload)
        return payload


def _apply_patch(payload, patch):
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            _apply_patch(payload[key], value)
        else:
            payload[key] = deepcopy(value)


def validate_artifact(path, metadata):
    expected = metadata.get('generated_hash')
    if not expected or hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
        raise PersistenceConflict('El archivo compensado no coincide con su metadata.')


def read_artifact_metadata(path, metadata_path, *, expected=None):
    metadata = json.loads(Path(metadata_path).read_text(encoding='utf-8'))
    validate_artifact(path, metadata)
    if expected is not None:
        identity_fields = {
            'generated_hash', 'physical_reference_token', 'audit_fingerprint',
            'original_hash', 'map_hash', 'operation_id', 'placement_revision',
            'reference_required', 'reference_frame', 'executable',
        }
        if any(metadata.get(key) != expected.get(key) for key in identity_fields):
            raise PersistenceConflict('La metadata publicada cambió la identidad del artefacto JIT.')
    return metadata


def validate_generation(plan, manifest):
    # Legacy pairs are readable, but must be regenerated before paired use.
    generation = plan.get('generation_id')
    if not generation or generation != manifest.get('generation_id') or plan.get('plan_id') != manifest.get('plan_id'):
        raise PersistenceConflict('Plan y manifest pertenecen a generaciones distintas.')
