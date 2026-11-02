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


def _fully_archived_preparation_reset(service, journal, current, after):
    """Recognize a fully checkpointed reset, without forgiving other mutations.

    This only permits journal reconciliation. It does not authorize motion,
    release physical recovery, or retire any unrelated current execution.
    """
    if (journal.get('schema_version') != 2
            or journal.get('action') != 'reset-preparation'
            or journal.get('phase') != 'CURRENT_RUN_ARCHIVED'
            or not isinstance(after, dict) or current != after):
        return False
    evidence = journal.get('evidence')
    if not isinstance(evidence, dict):
        return False
    project_id, setup_id = evidence.get('project_id'), evidence.get('setup_id')
    runs = evidence.get('runs')
    if (not isinstance(project_id, str) or not isinstance(setup_id, str)
            or not project_id or not setup_id
            or any(c in project_id for c in '/\\')
            or any(c in setup_id for c in '/\\')
            or project_id in {'.', '..'} or setup_id in {'.', '..'}
            or not isinstance(runs, list) or not runs
            or any(not isinstance(r, dict) or not isinstance(r.get('run_id'), str)
                   or not r.get('run_id') or not isinstance(r.get('revision'), int)
                   for r in runs)):
        return False
    expected = {r['run_id'] for r in runs}
    if len(expected) != len(runs):
        return False

    project_dir = service.repository.projects_dir / project_id
    setup_dir = project_dir / 'reports' / 'jobs' / setup_id
    if any(setup_dir.glob('*/current_run.json')):
        # CURRENT_RUN_ARCHIVED is written once per face; another face may
        # remain after an interrupted multi-face reset.
        return False
    found = set()
    for history_file in setup_dir.glob('*/history/*.json'):
        relative = str(history_file.relative_to(service.administrative_pending_path.parent))
        if relative not in current:
            continue
        item = json.loads(history_file.read_text(encoding='utf-8'))
        if item.get('archive_reason') == 'preparation_reset' and item.get('run_id') in expected:
            found.add(item['run_id'])
    return found == expected



def _verified_uncheckpointed_run_retirement(service, journal, current, after):
    """Recognize only a completed, identity-proven current_run removal gap.

    The reset writes HISTORY_SAVED, resets dependencies, checkpoints
    DEPENDENCIES_RESET and *then* removes current_run before the next
    checkpoint. A crash in that window is not a new or arbitrary file
    mutation: it is reconcilable only if every expected run was retired,
    every archived receipt is already checkpointed, and no other byte changed.
    This function has no writes and never authorizes physical actions.
    """
    if (journal.get('schema_version') != 2
            or journal.get('action') != 'reset-preparation'
            or journal.get('phase') != 'DEPENDENCIES_RESET'
            or journal.get('completed_steps') != ['HISTORY_SAVED', 'DEPENDENCIES_RESET']
            or not isinstance(after, dict)
            or not isinstance(journal.get('before'), dict)):
        return False
    evidence = journal.get('evidence')
    if not isinstance(evidence, dict):
        return False
    project_id, setup_id, runs = (evidence.get('project_id'),
                                   evidence.get('setup_id'), evidence.get('runs'))
    if (not isinstance(project_id, str) or not isinstance(setup_id, str)
            or any(not v or v in {'.', '..'} or '/' in v or '\\' in v
                   for v in (project_id, setup_id))
            or not isinstance(runs, list) or not runs
            or any(not isinstance(run, dict)
                   or not isinstance(run.get('run_id'), str) or not run['run_id']
                   or not isinstance(run.get('revision'), int) or isinstance(run['revision'], bool)
                   for run in runs)):
        return False
    expected_ids = {run['run_id'] for run in runs}
    if len(expected_ids) != len(runs):
        return False

    # All surviving files must match the durable DEPENDENCIES_RESET inventory.
    # Nothing may be added, modified or removed except one recorded current_run
    # per archived operation, in this exact project and setup.
    if any(after.get(path) != value for path, value in current.items()):
        return False
    missing = set(after) - set(current)
    if len(missing) != len(runs) or not missing:
        return False
    before = journal['before']
    faces = set()
    for rel in missing:
        parts = tuple(Path(rel).parts)
        if (len(parts) != 7
                or parts[:5] != ('projects', project_id, 'reports', 'jobs', setup_id)
                or parts[5] not in {'superior', 'inferior'}
                or parts[6] != 'current_run.json'
                or before.get(rel) != after[rel]):
            return False
        faces.add(parts[5])
    if len(faces) != len(missing):
        return False

    # The existing archive must bind *exactly* to each (run_id, revision,
    # project, setup, face). Its byte hash must be the one already preserved
    # in the checkpoint, not an unverified or subsequently changed file.
    matched_ids = set()
    for face in faces:
        archive_dir = (service.repository.projects_dir / project_id / 'reports'
                       / 'jobs' / setup_id / face / 'history')
        for run in runs:
            run_id, revision = run['run_id'], run['revision']
            archive_name = f"{run_id.replace('/', '_')}__preparation-reset_r{revision}.json"
            archive = archive_dir / archive_name
            rel = str(archive.relative_to(service.administrative_pending_path.parent))
            if rel not in current or rel not in after or not archive.is_file():
                continue
            try:
                payload = json.loads(archive.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return False
            if (payload.get('archive_reason') == 'preparation_reset'
                    and payload.get('run_id') == run_id
                    and payload.get('revision') == revision
                    and payload.get('project_id') == project_id
                    and payload.get('setup_id') == setup_id
                    and payload.get('face') == face):
                if run_id in matched_ids:
                    return False
                matched_ids.add(run_id)
    if matched_ids != expected_ids:
        return False
    # No unlisted current run of this setup may remain.
    target = service.repository.projects_dir / project_id / 'reports' / 'jobs' / setup_id
    return not any(target.glob('*/current_run.json'))



def _audited_partial_preparation_project(service, journal, raw_journal, current, after, attestation):
    """Explicit operator attestation of a single uncheckpointed project reset.

    Without the SHA-matching preimage, no automatic proof that the project
    changed *only* by the intended reset is possible. This deliberately does
    not claim such proof. Accept an operator's exact, locally witnessed
    project/operation hashes for ONE bounded reset window; retain that fact in
    a durable audit receipt. All other files MUST still match the checkpoint.
    """
    if not isinstance(attestation, dict):
        return None
    if (journal.get('schema_version') != 2
            or journal.get('status') != 'PENDING'
            or journal.get('action') != 'reset-preparation'
            or journal.get('phase') != 'HISTORY_SAVED'
            or journal.get('completed_steps') != ['HISTORY_SAVED']
            or journal.get('failure') is not None
            or not isinstance(journal.get('before'), dict)
            or not isinstance(after, dict)
            or journal['before'] != after):
        return None
    evidence = journal.get('evidence')
    if not isinstance(evidence, dict) or evidence.get('runs') != [] or evidence.get('abandoned_run'):
        return None
    project_id, setup_id = evidence.get('project_id'), evidence.get('setup_id')
    if (not isinstance(project_id, str) or not isinstance(setup_id, str)
            or any(not value or value in {'.', '..'} or '/' in value or '\\' in value
                   for value in (project_id, setup_id))):
        return None

    project_rel = f'projects/{project_id}/project.json'
    if project_rel not in after or set(current) != set(after):
        return None
    changed = {rel for rel in current if current[rel] != after[rel]}
    if changed != {project_rel}:
        return None

    project_path = service.repository.projects_dir / project_id / 'project.json'
    raw_project = project_path.read_bytes()
    project_hash = digest(raw_project)
    if (current[project_rel] != project_hash
            or attestation.get('journal_sha256') != digest(raw_journal)
            or attestation.get('project_sha256') != project_hash
            or attestation.get('acknowledgement') !=
               'ACEPTO_CONCILIAR_RESET_PARCIAL_SIN_PREIMAGEN'):
        return None

    try:
        project = json.loads(raw_project)
    except (TypeError, ValueError):
        return None
    if not isinstance(project, dict) or project.get('id') != project_id:
        return None
    operations = project.get('operaciones')
    if not isinstance(operations, list) or not operations:
        return None
    count = attestation.get('operation_count')
    if type(count) is not int or count != len(operations):
        return None
    operation_hash = digest(json.dumps(
        operations, sort_keys=True, separators=(',', ':'), ensure_ascii=True
    ).encode('utf-8'))
    if attestation.get('operations_sha256') != operation_hash:
        return None

    # Check all model invariants (including unique operation identity and
    # order) without saving, normalizing or restoring any project data.
    try:
        domain_project = service.repository.load_project(project_id)
        setup = domain_project.get_setup(setup_id)
    except Exception:
        return None
    if (len(domain_project.operaciones) != count or
            str(setup.preparation_status) != 'sin_iniciar' or
            setup.active_reference_id is not None or setup.active_map_id is not None or
            setup.last_prepared_at is not None):
        return None
    preparation = project.get('montajes')
    if not isinstance(preparation, list):
        return None
    matching = [s for s in preparation if isinstance(s, dict) and s.get('id') == setup_id]
    if len(matching) != 1:
        return None
    raw_setup = matching[0]
    fields = raw_setup.get('preparacion')
    if not isinstance(fields, dict):
        return None
    must_be_null = (
        'physical_reference', 'origen_trabajo', 'referencia_z',
        'region_sondeable_configurada_en', 'mapa_disponible_en',
        'mapa_validado_en', 'compensacion_previsualizada_en'
    )
    if any(fields.get(name, object()) is not None for name in must_be_null):
        return None
    if not isinstance(fields.get('motivo_invalidacion'), str) or not fields['motivo_invalidacion']:
        return None
    if (not isinstance(raw_setup.get('placement_revision'), str) or
            not raw_setup['placement_revision'].startswith('placement-') or
            not raw_setup['placement_revision'].split('-')[-1].isdigit()):
        return None

    # With no runs recorded, the target setup must not contain an unrecorded
    # current execution. No attempt is silently retired by this recovery.
    runs_dir = service.repository.projects_dir / project_id / 'reports' / 'jobs' / setup_id
    if any(runs_dir.glob('*/current_run.json')):
        return None

    return {
        'verification': 'OPERATOR_ATTESTED_NO_EXACT_PREIMAGE',
        'project': project_rel,
        'project_before_sha256': after[project_rel],
        'project_observed_sha256': project_hash,
        'operations_sha256': operation_hash,
        'operation_count': count,
        'setup_id': setup_id,
        'warning': 'No se dispone de preimagen exacta: se conserva el reset parcial observado; no se certifica igualdad de operaciones con el estado previo.',
    }


def recover(service, transaction_id, *, partial_reset_attestation=None):
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
        elif _verified_uncheckpointed_run_retirement(service, journal, current, after):
            outcome = 'CURRENT_RUN_RETIRED_WITH_VERIFIED_ARCHIVE'
        elif (audit := _audited_partial_preparation_project(
                service, journal, original, current, after, partial_reset_attestation)) is not None:
            outcome = 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED'
        else:
            raise ApplicationError('Archivos cambiados sin checkpoint verificable; se conserva el journal y el histórico. Requiere conciliación específica del respaldo.')
        check()
        # Fresh physical evidence is obtained AFTER expensive disk verification.
        remote = service._observe_administrative_remote()
        guard = SimpleNamespace(remote=remote, check=check)
        if not (_fully_archived_preparation_reset(service, journal, current, after)
                or outcome in {'CURRENT_RUN_RETIRED_WITH_VERIFIED_ARCHIVE',
                               'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED'}):
            # For earlier/partial phases with potentially affected current
            # runs, retain strict remote-identity checks. They must never be
            # invoked against unrelated, byte-identical executions for an
            # explicitly attested reset with runs=[] and no target current_run:
            # Moonraker standby/filename='' is not evidence about the other
            # project's historical print. The attested branch already checks
            # the complete inventory (except the one acknowledged project.json),
            # enforces no target runs, and still requires fresh remote idle,
            # velocity, exclusive administrative ownership and worker quiescence.
            # No foreign run is archived, changed or declared completed.
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
        # A fully archived reset is checked against the exact durable inventory
        # and archived identities. Other projects' current runs are untouched
        # and cannot be required to match Moonraker's empty standby filename.
        check()
        directory.mkdir(parents=True, exist_ok=True)
        receipt = {'id': transaction_id, 'outcome': outcome, 'status': 'ARCHIVE_PREPARED',
                   'journal_sha256': digest(original), 'files': current,
                   'physical_recovery_required': True}
        if outcome == 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED':
            receipt['audit'] = audit
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
        next_action = ('Verificar reposo y recuperar controles; revisar estado de preparación antes de trabajar. No repetir Reiniciar proceso automáticamente.'
                       if outcome == 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED'
                       else 'Verificar reposo y recuperar controles; después Reiniciar proceso. No se reanuda ningún trabajo.')
        return {'status': 'reconciled', 'outcome': outcome, 'physical_recovery_required': True,
                'next_action': next_action}
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
