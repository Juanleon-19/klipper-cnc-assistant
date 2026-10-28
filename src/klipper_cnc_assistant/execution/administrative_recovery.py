"""Evidence-only reconciliation. Never replay writes or release physical ownership."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.storage.safe_persistence import atomic_json


def digest(content):
    return hashlib.sha256(content).hexdigest()


def inventory(root):
    """Include all data dependencies; private transaction evidence is separate."""
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if relative.parts[0] in {'administrative-transactions', 'administrative-reset.pending.json'}:
            continue
        if path.is_symlink():
            raise ApplicationError('Dependencia simbólica no verificable; recuperación bloqueada.')
        if path.is_file():
            result[str(relative)] = digest(path.read_bytes())
    return result


def transaction_dir(service, journal):
    identity = journal.get('id', '')
    if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
        raise ApplicationError('Identificador de journal administrativo inválido.')
    return service.administrative_pending_path.parent / 'administrative-transactions' / identity


def status(service):
    path = service.administrative_pending_path
    if not path.exists():
        if not service.runtime.physical_ownership.snapshot().get('administrative_incomplete'):
            return {'pending': False}
        candidates = []
        for receipt in (path.parent / 'administrative-transactions').glob('*/resolution.json'):
            resolution = json.loads(receipt.read_text())
            if resolution.get('status') == 'ARCHIVE_PREPARED' and (receipt.parent / 'original.json').exists():
                candidates.append(receipt.parent / 'original.json')
        if len(candidates) != 1:
            raise ApplicationError('Evidencia de cierre administrativo ambigua; se conserva el bloqueo.')
        journal = json.loads(candidates[0].read_text())
    else:
        journal = json.loads(path.read_text())
    return {'pending': True, 'id': journal.get('id'), 'action': journal.get('action'),
            'phase': journal.get('phase', 'LEGACY_UNKNOWN'),
            'failure': journal.get('failure'),
            'next_action': 'Recuperar transacción administrativa; después verificar reposo y reiniciar proceso.'}


def recover(service, transaction_id):
    path = service.administrative_pending_path
    coordinator = service.runtime.physical_ownership
    # Idempotent response never clears newly acquired ownership or a newer journal.
    directory = transaction_dir(service, {'id': transaction_id})
    archived = False
    if not path.exists():
        if not ((directory / 'original.json').exists() and (directory / 'resolution.json').exists()):
            raise ApplicationError('No existe esa transacción pendiente.')
        if not coordinator.snapshot().get('administrative_incomplete'):
            return {'status': 'already_reconciled', 'physical_recovery_required': True}
        archived = True  # retry a failure after durable archive but before memory completion
    source = directory / 'original.json' if archived else path
    original = source.read_bytes()
    journal = json.loads(original)
    if journal.get('id') != transaction_id:
        raise ApplicationError('La transacción cambió; vuelva a consultar su identidad.')
    owner = coordinator.snapshot()
    token = coordinator.begin_administrative_recovery(expected_revision=owner['revision'])
    session = service.runtime.current_physical_session_id()
    def check():
        coordinator.validate_administrative_recovery(token)
        if service.runtime.current_physical_session_id() != session:
            raise ApplicationError('La sesión cambió durante la recuperación administrativa.')
        if service._movement_lock_state() is not False or service.runtime._active_operation is not None:
            raise ApplicationError('Operación activa o movement_lock no observable/libre.')
        with service._lock:
            if any(t.is_alive() for t in service._threads.values()):
                raise ApplicationError('Supervisor de ejecución activo.')
        if service.mesh_execution_service is not None and service.mesh_execution_service.active_execution_snapshot().get('active'):
            raise ApplicationError('Worker de malla activo.')
        if source.read_bytes() != original or (archived and path.exists()):
            raise ApplicationError('El journal cambió durante la recuperación.')
    try:
        check()
        if journal.get('schema_version') == 2:
            evidence = journal
        else:
            proof_path = directory / 'legacy-evidence.json'
            if not proof_path.exists():
                raise ApplicationError('Journal antiguo: falta evidencia local verificada del respaldo; no se elimina ni se fuerza recuperación.')
            evidence = json.loads(proof_path.read_text())
            if evidence.get('journal_sha256') != digest(original):
                raise ApplicationError('La evidencia no corresponde al journal pendiente.')
        current = inventory(path.parent)
        before = evidence.get('before')
        after = evidence.get('verified_after')
        if current == before:
            outcome = 'NO_DATA_CHANGE'
        elif after is not None and current == after:
            outcome = 'PARTIAL_PRESERVED_RETRY_RESET' if evidence.get('phase') != 'COMPLETED' else 'COMPLETED'
        else:
            raise ApplicationError('Archivos cambiados sin checkpoint verificable; se conserva el journal y el histórico. Requiere conciliación específica del respaldo.')
        check()
        # Fresh physical evidence is obtained AFTER expensive disk verification.
        remote = service._observe_administrative_remote()
        guard = SimpleNamespace(remote=remote, check=check)
        for file in service.repository.projects_dir.glob('*/reports/jobs/*/*/current_run.json'):
            run = service.run_store.load(file)
            if run:
                reset_evidence = journal.get('evidence') or {}
                abandoned = reset_evidence.get('abandoned_run')
                if (journal.get('schema_version') == 2 and journal.get('action') == 'reset-preparation'
                        and abandoned and run.get('run_id') == abandoned.get('run_id')):
                    if (run.get('project_id') != reset_evidence.get('project_id')
                            or run.get('setup_id') != reset_evidence.get('setup_id')):
                        raise ApplicationError('La confirmación de abandono no pertenece a esta preparación.')
                    service._assert_abandoned_cancelled_run(run, guard, abandoned)
                else:
                    service._assert_administrative_print_identity(run, guard)
        check()
        directory.mkdir(parents=True, exist_ok=True)
        receipt = {'id': transaction_id, 'outcome': outcome, 'status': 'ARCHIVE_PREPARED',
                   'journal_sha256': digest(original), 'files': current,
                   'physical_recovery_required': True}
        atomic_json(directory / 'resolution.json', receipt, durable=True)
        check()
        destination = directory / 'original.json'
        if destination.exists() and destination.read_bytes() != original:
            raise ApplicationError('Evidencia histórica distinta; recuperación bloqueada.')
        if not archived:
            os.replace(path, destination)
        for folder in (directory, path.parent):
            fd = os.open(folder, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        coordinator.finish_administrative_recovery(token)
        receipt['status'] = 'RECONCILED'
        atomic_json(directory / 'resolution.json', receipt, durable=True)
        return {'status': 'reconciled', 'outcome': outcome, 'physical_recovery_required': True,
                'next_action': 'Verificar reposo y recuperar controles; después Reiniciar proceso. No se reanuda ningún trabajo.'}
    finally:
        coordinator.end_administration(token)


def import_legacy_evidence(data_dir, backup_dir):
    """Operator-local proof import; no state clearing, no restoration, no hardware.

    Only legacy reset-map has an audited two-file write set. All other dependencies
    are pinned at import and must remain byte-identical until online reconciliation.
    """
    data, backup = Path(data_dir), Path(backup_dir)
    path = data / 'administrative-reset.pending.json'
    original = path.read_bytes(); journal = json.loads(original)
    if journal.get('action') != 'reset_setup_map' or journal.get('schema_version') == 2:
        raise ApplicationError('Importación limitada al reset-map antiguo auditado.')
    evidence = journal['evidence']; project_id = evidence['project_id']; setup_id = evidence['setup_id']
    project_path = data / 'projects' / project_id / 'project.json'
    if project_path.resolve().parent.parent != (data / 'projects').resolve():
        raise ApplicationError('Proyecto inválido.')
    baseline = {r['path']: r['sha256'] for r in json.loads((backup / 'baseline.json').read_text())}
    project = json.loads(project_path.read_text())
    setup = next(s for s in project['montajes'] if s['id'] == setup_id)
    targets = [project_path]
    if setup.get('active_map_id'):
        targets.append(project_path.parent / 'maps' / setup['active_map_id'] / 'height_map.json')
    proven = {}
    for target in targets:
        if not target.resolve().is_relative_to(project_path.parent.resolve()):
            raise ApplicationError('Mapa fuera del proyecto.')
        relative = 'data/' + str(target.relative_to(data))
        saved = backup / relative
        if relative not in baseline or digest(saved.read_bytes()) != baseline[relative] or target.read_bytes() != saved.read_bytes():
            raise ApplicationError('El proyecto/mapa no coincide con el respaldo íntegro; no se acredita ausencia de escrituras.')
        proven[relative] = baseline[relative]
    # Original inputs, configuration, maps and artifacts must also match the
    # trusted backup. Job journals may have a legitimate later cancellation:
    # preserve their current bytes and validate remote identity during recovery.
    prefix = 'data/projects/' + project_id + '/'
    known = set()
    preserved_reports = {}
    for relative, expected in baseline.items():
        if not relative.startswith(prefix) or '/reports/jobs/' in relative:
            continue
        local = relative.removeprefix('data/')
        known.add(local)
        saved, target = backup / relative, data / local
        if digest(saved.read_bytes()) != expected:
            raise ApplicationError('Respaldo de dependencia no íntegro.')
        actual = digest(target.read_bytes())
        if actual != expected:
            # reset_map writes ONLY the project and active map, not the earlier
            # validation report. Preserve and pin that independently updated
            # report; it is never adopted as evidence of physical authorization.
            if '/reports/mesh-validation/' not in relative:
                raise ApplicationError('Dependencia del proyecto distinta al respaldo; requiere conciliación específica.')
            preserved_reports[relative] = {'backup': expected, 'current': actual}
        else:
            proven[relative] = expected
    snapshot = inventory(data)
    for local in snapshot:
        if local.startswith('projects/' + project_id + '/') and '/reports/jobs/' not in local and local not in known:
            raise ApplicationError('Dependencia nueva sin respaldo; no se adopta silenciosamente.')
    if path.read_bytes() != original:
        raise ApplicationError('Journal cambiado durante importación.')
    service = SimpleNamespace(administrative_pending_path=path)
    directory = transaction_dir(service, journal); directory.mkdir(parents=True, exist_ok=True)
    proof = directory / 'legacy-evidence.json'
    if proof.exists():
        raise ApplicationError('Ya existe evidencia importada; no se sobrescribe.')
    atomic_json(proof, {'journal_sha256': digest(original), 'before': snapshot,
                       'proven_reset_write_set': proven, 'preserved_validation_reports': preserved_reports,
                       'phase': 'LEGACY_NO_DATA_CHANGE'}, durable=True)
    return {'status': 'evidence_imported', 'dependencies': len(snapshot)}
