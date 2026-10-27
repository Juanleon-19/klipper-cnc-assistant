"""One read-only execution assessment. It is not a physical motion permit."""
from copy import deepcopy
from datetime import datetime, timezone

PREPARABLE_STATES = frozenset({'JOB_DRAFT', 'JOB_VALIDATING', 'JOB_READY'})


def pending_index(run):
    return next((i for i, op in enumerate((run or {}).get('operations', []))
                 if op.get('execution_status') != 'COMPLETED'), None)


def reference_candidate(run, index):
    """Use a proven preceding installation, never a historical map alias.

    Only unexecuted consecutive operations may inherit the immediately previous
    completed operation's exact evidence. The caller must still validate it.
    """
    operation = deepcopy(run['operations'][index])
    if index and operation.get('execution_status') == 'PENDING' and not operation.get('remote_file'):
        previous = run['operations'][index - 1]
        if (previous.get('execution_status') == 'COMPLETED'
                and previous.get('tool_key') == operation.get('tool_key') == run.get('current_tool_key')
                and previous.get('installation_revision') and previous.get('physical_reference_token')):
            operation['installation_revision'] = previous['installation_revision']
            operation['physical_reference_token'] = deepcopy(previous['physical_reference_token'])
    return operation


def ownership_compatible(plan, ownership):
    return not ownership.get('active') or (
        ownership.get('kind') == 'JOB_EXECUTION' and ownership.get('owner_id') == '/'.join(
            str(plan.get(k)) for k in ('project_id', 'setup_id', 'face')))


def evaluate_readiness(plan, run, snapshot, ownership, physical_session, *, observation_failed=False):
    checks, blockers = [], []

    def check(name, ok, message, action, category, code=None):
        checks.append({'name': name, 'ok': bool(ok), 'detail': message})
        if not ok:
            blockers.append({'code': code or name.upper(), 'category': category,
                             'message': message, 'action': action})

    moon = snapshot.get('moonraker') or {}
    klipper = snapshot.get('klipper') or {}
    check('modo_fisico', snapshot.get('mode') == 'PHYSICAL',
          'Se requiere runtime físico.', 'Seleccione el runtime físico.', 'runtime')
    check('runtime_conectado', moon.get('http_connected'), 'Moonraker HTTP debe estar conectado.',
          'Revise la conexión del runtime con Moonraker.', 'runtime')
    check('websocket', moon.get('websocket_connected'), 'La telemetría WebSocket debe estar conectada.',
          'Restablezca la conexión de telemetría.', 'runtime')
    fresh = (snapshot.get('safety') or {}).get('telemetry_recent') is True
    check('telemetria_vigente', fresh and not observation_failed,
          ('No fue posible obtener una observación actual de la máquina. '
           'No se pudo confirmar la posición actual de Moonraker.' if observation_failed
           else 'Se requiere una observación de posición vigente.'),
          'Revise la conexión Moonraker/Klipper y vuelva a actualizar el estado.', 'runtime')
    check('klipper_ready', klipper.get('ready'), 'Klipper debe estar listo para ejecutar.',
          'Resuelva el estado de Klipper antes de ejecutar.', 'runtime')
    check('homing', set('xyz').issubset(set(klipper.get('homed_axes') or '')),
          'Se requiere homing XYZ vigente.', 'Verifique el homing en la sesión actual.', 'runtime')
    recovery = bool(ownership.get('recovery_pending') or snapshot.get('recovery_pending'))
    check('recuperacion_fisica', not recovery, ownership.get('reason') or 'No debe existir recuperación física pendiente.',
          'Revise y resuelva la recuperación física antes de continuar.', 'runtime', 'PHYSICAL_RECOVERY_REQUIRED')
    check('ownership_compatible', ownership_compatible(plan, ownership),
          'Otro workflow conserva el control físico.', 'Espere su cierre o resuelva su recuperación.', 'runtime')
    operations = plan.get('operations') or []
    active_map = plan.get('active_map') or {}
    map_ready = bool(active_map and active_map.get('source') == 'MEASURED' and active_map.get('status') == 'MESH_COMPLETE'
                     and (active_map.get('validation') or {}).get('status') == 'VALID'
                     and not active_map.get('archived_at')
                     and active_map.get('placement_revision') == plan.get('placement_revision'))
    check('mapa_activo', map_ready, 'Se requiere el mapa único completo y validado del montaje vigente.',
          'Revise el mapa y su cobertura para este montaje/superficie.', 'geometry', 'MAP_NOT_READY')
    check('plan_generado', bool(operations), 'Se requieren operaciones con G-code analizado.',
          'Añada y analice las operaciones del montaje.', 'geometry')
    # Geometric feasibility of the planned job is distinct from future tool Z.
    blocked = [op for op in operations if op.get('blocking')]
    check('operaciones_bloqueadas', not blocked,
          '; '.join(f"{op['name']}: {', '.join(op['blocking_reasons'])}" for op in blocked)
          or 'Archivos, análisis y cobertura verificados.',
          'Corrija los archivos, análisis o cobertura indicados.', 'geometry', 'GEOMETRY_BLOCKED')
    check('compensacion_jit', True, 'Legacy se generará al iniciar cada operación.', '', 'geometry')
    historical = bool(run and run.get("state") in {"JOB_CANCELLED", "JOB_COMPLETE", "JOB_ERROR"})
    index = pending_index(run) if run and not historical else (0 if operations else None)
    candidate = reference_candidate(run, index) if run and not historical and index is not None else None
    operation_id = candidate.get('operation_id') if candidate else (operations[0]['operation_id'] if operations else None)
    row = next((op for op in operations if op['operation_id'] == operation_id), None)
    reference = deepcopy((row or {}).get('reference_diagnostic') or {
        'valid': False, 'code': 'REFERENCE_MEASUREMENT_REQUIRED', 'status': 'REQUIERE MEDICIÓN',
        'message': 'Se requiere referencia de la herramienta actual.'})
    token = ((row or {}).get('tool_installation_calibration') or {}).get('physical_reference_token')
    if reference['valid'] and candidate:
        if (candidate.get('installation_revision') != (token or {}).get('installation_id')
                or candidate.get('physical_reference_token') != token):
            reference = {'valid': False, 'code': 'REFERENCE_INSTALLATION_CHANGED', 'status': 'CONTEXTO CAMBIÓ',
                         'message': 'La instalación o medición ya no coincide con la preparada para esta operación.'}
    tool = (row or {}).get('tool_name') or 'actual'
    check('referencia_inicial', reference['valid'], reference['message'],
          f'Verifique la instalación y mida Z con la herramienta {tool} instalada.' if not reference['valid'] else '',
          'tool', reference['code'])
    conditions_ready = not blockers
    state = (run or {}).get('state')
    can_prepare = run is None or (state in PREPARABLE_STATES and not run.get('started_at'))
    startable = bool(run and state in {'JOB_READY', 'JOB_VALIDATING'} and not run.get('started_at'))
    if not startable and can_prepare:
        blockers.append({'code': 'JOB_NOT_PREPARED' if can_prepare else 'EXECUTION_STATE', 'category': 'execution',
                         'message': 'Prepare el trabajo antes de iniciar.' if can_prepare else f'No puede iniciar desde {state}.',
                         'action': 'Preparar trabajo.' if can_prepare else 'Use la acción permitida para el estado actual; no vuelva a preparar.'})
    return {'can_start': conditions_ready and startable, 'conditions_ready': conditions_ready,
            'can_prepare': can_prepare, 'blocking_reasons': blockers, 'checks': checks,
            'evaluated_at': datetime.now(timezone.utc).isoformat(),
            'physical_session_id': physical_session, 'ownership_session_id': ownership.get('session'),
            'run_id': (run or {}).get('run_id'), 'run_revision': (run or {}).get('revision'),
            'current_operation': {'operation_id': operation_id, 'index': index, 'total': len(operations),
                                  'name': (row or {}).get('name'), 'tool': tool,
                                  'installation_id': (token or {}).get('installation_id'),
                                  'reference_revision': (token or {}).get('reference_revision')},
            'reference': reference, 'map_ready': map_ready,
            'geometry': {'covered': sum(op.get('coverage_status') == 'VALIDA' for op in operations),
                         'blocked': len(blocked)}}
