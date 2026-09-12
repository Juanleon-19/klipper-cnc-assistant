"""Atomic JobRun CAS. No callback, transport or workflow runs under a store lock."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

class JobRunConflict(RuntimeError):
    """The caller's run, domain revision or cancellation epoch is obsolete."""


class JobRunStore:
    _registry_lock = threading.Lock()
    _locks = {}
    OBSERVATIONS = frozenset({'eta_ratio_ema'})
    OPERATION_OBSERVATIONS = frozenset({
        'progress', 'machine_status', 'moonraker_filename', 'moonraker_state',
    })
    CANCELLED = frozenset({'JOB_CANCELLED', 'CANCELLED'})

    @contextmanager
    def _locked(self, path):
        path = Path(path).absolute()
        with self._registry_lock:
            lock = self._locks.setdefault(str(path), threading.RLock())
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_suffix('.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self, path):
        # Atomic replace makes an unlocked read a consistent snapshot. Missing
        # reads must not create directories (execution/live is read-only).
        path = Path(path)
        try:
            run = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if path.is_symlink():
                raise
            return None
        run.setdefault('revision', 0)
        run.setdefault('cancellation_epoch', 0)
        return run

    @staticmethod
    def _write(path, run):
        path = Path(path)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.job-run-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(run, handle, ensure_ascii=True, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _check(current, expected, *, allow_cancelled=False):
        if current is None or any(current.get(k, 0) != expected.get(k, 0)
                                  for k in ('run_id', 'revision', 'cancellation_epoch')):
            raise JobRunConflict('JobRun reemplazado o revisión obsoleta.')
        if not allow_cancelled and current.get('state') in JobRunStore.CANCELLED:
            raise JobRunConflict('JobRun cancelado; transición rechazada.')

    def validate(self, path, expected, *, allow_cancelled=False):
        with self._locked(path):
            current = self.load(path)
            self._check(current, expected, allow_cancelled=allow_cancelled)
            return current

    def create(self, path, run, *, previous=None):
        with self._locked(path):
            current = self.load(path)
            if previous is None:
                if current is not None:
                    raise JobRunConflict('Ya existe un JobRun.')
            else:
                self._check(current, previous, allow_cancelled=True)
                if current['run_id'] == run['run_id']:
                    raise JobRunConflict('Un nuevo JobRun requiere otra identidad.')
            payload = deepcopy(run)
            payload.update(revision=1, cancellation_epoch=0)
            self._write(path, payload)
            return payload

    def save(self, path, run):
        with self._locked(path):
            current = self.load(path)
            self._check(current, run)
            payload = deepcopy(run)
            # Observations and dispatch evidence cannot be overwritten by a
            # domain writer carrying a snapshot taken before those updates.
            for key in self.OBSERVATIONS | {'start_attempt', 'cancel_remote_status'}:
                if key in current:
                    payload[key] = deepcopy(current[key])
            payload['revision'] = current['revision'] + 1
            if payload.get('state') in self.CANCELLED:
                payload['cancellation_epoch'] = current['cancellation_epoch'] + 1
            self._write(path, payload)
            return payload

    def observe(self, path, expected, fields, *, operation_id=None, operation_fields=None):
        if set(fields) - self.OBSERVATIONS or set(operation_fields or {}) - self.OPERATION_OBSERVATIONS:
            raise ValueError('Campo de observación no permitido.')
        with self._locked(path):
            current = self.load(path)
            self._check(current, expected)
            current.update(deepcopy(fields))
            if operation_id is not None:
                operation = self._operation(current)
                if operation.get('operation_id') != operation_id:
                    raise JobRunConflict('La operación observada cambió.')
                operation.update(deepcopy(operation_fields or {}))
            self._write(path, current)
            return current

    @staticmethod
    def _operation(run):
        try:
            return run['operations'][int(run['current_operation_index'])]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise JobRunConflict('JobRun sin operación actual válida.') from error

    def begin_start(self, path, expected, operation_id, remote_file):
        with self._locked(path):
            current = self.load(path)
            self._check(current, expected)
            operation = self._operation(current)
            if (current.get('state') != 'WAITING_FOR_KLIPPER'
                    or current.get('current_operation_id') != operation_id
                    or operation.get('operation_id') != operation_id
                    or operation.get('remote_file') != remote_file):
                raise JobRunConflict('La operación o archivo autorizado cambió antes del start.')
            attempt = {'id': uuid4().hex, 'operation_id': operation_id,
                       'remote_file': remote_file, 'status': 'dispatching',
                       'may_have_been_sent': True}
            current['start_attempt'] = attempt
            current['revision'] += 1
            self._write(path, current)
            return current

    def finish_start(self, path, run_id, attempt_id, *, status):
        with self._locked(path):
            current = self.load(path)
            if (current is None or current['run_id'] != run_id
                    or current.get('start_attempt', {}).get('id') != attempt_id):
                return
            current['start_attempt']['status'] = status
            if current['state'] in self.CANCELLED:
                current['recovery_state'] = 'CANCEL_RECONCILIATION_REQUIRED'
            self._write(path, current)

    def cancel(self, path, run_id):
        with self._locked(path):
            current = self.load(path)
            if current is None or current['run_id'] != run_id:
                raise JobRunConflict('El JobRun a cancelar fue reemplazado.')
            if current['state'] in self.CANCELLED:
                return current
            current.update(state='JOB_CANCELLED', revision=current['revision'] + 1,
                           cancellation_epoch=current['cancellation_epoch'] + 1,
                           completed_at=datetime.now(timezone.utc).isoformat(),
                           available_actions=[], next_action='Trabajo cancelado')
            current['updated_at'] = current['completed_at']
            current['recovery_state'] = 'CANCEL_RECONCILIATION_REQUIRED'
            current['cancel_remote_status'] = 'pending_identity_check'
            self._write(path, current)
            return current

    def cancel_result(self, path, expected, result):
        with self._locked(path):
            current = self.load(path)
            self._check(current, expected, allow_cancelled=True)
            current['cancel_remote_status'] = result
            self._write(path, current)
            return current

    def remove(self, path, expected):
        with self._locked(path):
            self._check(self.load(path), expected, allow_cancelled=True)
            Path(path).unlink()
