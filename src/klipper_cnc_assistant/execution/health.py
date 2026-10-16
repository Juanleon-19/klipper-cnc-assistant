"""Read-only presentation and explicit restart recovery classification."""
INITIAL = frozenset({'JOB_DRAFT', 'JOB_VALIDATING', 'JOB_READY'})
PREUPLOAD = frozenset({'JOB_STARTING', 'OPERATION_PREFLIGHT'})
WAITING = frozenset({'SPINDLE_STOP_REQUIRED', 'TOOL_CHANGE_REQUIRED', 'READY_TO_RESUME'})
TERMINAL = frozenset({'JOB_COMPLETE', 'JOB_CANCELLED', 'JOB_ERROR'})


def preparation_retry_safe(run, status):
    index = int(run.get('current_operation_index') or 0)
    operations = run.get('operations') or []
    op = operations[index] if 0 <= index < len(operations) else {}
    attempt = run.get('start_attempt') or {}
    not_sent = (bool(attempt.get('id')) and attempt.get('status') == 'not_sent' and attempt.get('may_have_been_sent') is False
                and attempt.get('operation_id') == op.get('operation_id'))
    retry_state = run.get('state') in PREUPLOAD or (run.get('state') in INITIAL and not run.get('started_at'))
    if not_sent:
        retry_state = retry_state or run.get('state') in {'WAITING_FOR_KLIPPER', 'JOB_ERROR', 'RECOVERY_REQUIRED'}
    remote_safe = not op.get('remote_file') or (not_sent and op['remote_file'] == attempt.get('remote_file')
                                               and status.get('filename') in {None, '', op['remote_file']})
    return bool(op and retry_state and remote_safe and not op.get('observed_printing')
                and not op.get('started_at') and (not attempt or not_sent)
                and status.get('connected') is True
                and status.get('print_state') == 'standby'
                and status.get('is_active') is False)



def tool_change_retry_safe(run, status, readiness, worker_alive):
    """Advisory action, never a movement permit or automatic retry.

    Only the failed transition after a proven completed operation qualifies.
    A journal for that completed operation may be sent, but never ambiguous.
    """
    operations = run.get('operations') or []
    index = int(run.get('current_operation_index') or 0)
    if not 0 <= index < len(operations) - 1:
        return False
    current, following = operations[index:index + 2]
    token = current.get('physical_reference_token') or {}
    attempt = run.get('start_attempt') or {}
    if attempt and (attempt.get('status') != 'sent'
                    or attempt.get('operation_id') != current.get('operation_id')):
        return False
    runtime_blocked = any(issue.get('category') == 'runtime' or issue.get('code') == 'EXECUTION_INSPECTION_UNAVAILABLE'
                          for issue in readiness.get('blocking_reasons', []))
    return bool(run.get('state') == 'RECOVERY_REQUIRED' and not worker_alive
                and 'retry-tool-change-transition' in (run.get('available_actions') or [])
                and (run.get('last_transition_error') or {}).get('code') == 'TOOL_CHANGE_TRANSITION_FAILED'
                and current.get('execution_status') == 'COMPLETED'
                and following.get('execution_status') == 'PENDING'
                and current.get('tool_key') != following.get('tool_key')
                and not following.get('remote_file') and not following.get('started_at')
                and not following.get('observed_printing')
                and token.get('physical_session_id') == readiness.get('physical_session_id')
                and bool(token.get('physical_session_id'))
                and not runtime_blocked
                and status.get('connected') is True and status.get('is_active') is False
                and status.get('print_state') in {'standby', 'complete'}
                and status.get('filename') in {None, '', current.get('remote_file')})


def execution_health(run, status, readiness, worker_alive):
    state = run.get('state', 'JOB_DRAFT')
    recovery = None
    actions = list(run.get('available_actions') or [])
    if state in INITIAL and not run.get('started_at'):
        if not readiness['reference']['valid'] and preparation_retry_safe(run, status):
            actions = ['update-tool-reference']
        return {'phase': 'START', 'title': 'PUEDE INICIAR' if readiness['can_start'] else 'NO PUEDE INICIAR',
                'severity': 'SUCCESS' if readiness['can_start'] else 'WARNING', 'recovery': None,
                'available_actions': actions, 'detail': ''}
    if state == 'JOB_COMPLETE': title, severity = 'COMPLETADO', 'SUCCESS'
    elif state == 'JOB_ERROR': title, severity = 'ERROR', 'ERROR'
    elif state == 'JOB_CANCELLED': title, severity = 'CANCELADO', 'WARNING'
    elif state in {'JOB_PAUSED', 'OPERATION_PAUSED'}: title, severity = 'PAUSADO', 'WARNING'
    elif state in WAITING: title, severity = 'ESPERANDO OPERADOR', 'SUCCESS' if state == 'READY_TO_RESUME' else 'WARNING'
    elif state == 'OPERATION_RUNNING': title, severity = 'EJECUTANDO', 'INFO'
    else: title, severity = 'PREPARANDO', 'INFO'
    changing_tool = state in {'SPINDLE_STOP_REQUIRED', 'SPINDLE_STOP_CONFIRMED', 'RETRACTING',
                              'TOOL_CHANGE_REQUIRED', 'TOOL_CHANGE_CONFIRMED', 'MOVING_TO_REFERENCE',
                              'RETURNING_TO_REFERENCE_SAFE_Z', 'RETURNING_TO_REFERENCE_XY', 'CALIBRATING_TOOL'}
    operations = run.get('operations') or []
    index = int(run.get('current_operation_index') or 0)
    token = (operations[index].get('physical_reference_token') or {}) if 0 <= index < len(operations) else {}
    session_changed = bool(token and token.get('physical_session_id') != readiness.get('physical_session_id'))
    reference_invalid = session_changed or (not changing_tool and not readiness['reference']['valid'])
    if tool_change_retry_safe(run, status, readiness, worker_alive):
        error = run['last_transition_error']
        return {'phase': 'ACTIVE', 'title': 'RECUPERACIÓN REQUERIDA', 'severity': 'ERROR',
                'recovery': {'code': error['code'], 'message': error['message'],
                             'action': 'Reintentar transición; confirme de nuevo spindle detenido.'},
                'available_actions': ['retry-tool-change-transition', 'cancel'],
                'detail': error['message']}
    retryable_error = state == 'JOB_ERROR' and preparation_retry_safe(run, status)
    if (state not in TERMINAL or retryable_error) and (retryable_error or state == 'RECOVERY_REQUIRED' or reference_invalid
            or (not worker_alive and state not in WAITING | INITIAL | {'JOB_PAUSED', 'OPERATION_PAUSED'})):
        title, severity = 'RECUPERACIÓN REQUERIDA', 'ERROR'
        safe = preparation_retry_safe(run, status)
        ref = readiness['reference']
        recovery = {'code': ref['code'] if not ref['valid'] else 'SUPERVISOR_MISSING',
                    'message': ref['message'] if not ref['valid'] else 'El supervisor no está activo; revise la preparación interrumpida.',
                    'action': 'Actualizar referencia de herramienta' if safe and not ref['valid'] else
                              'Reintentar preparación de operación' if safe else
                              'Verifique identidad remota y recuperación física; no se reintentará start automáticamente.'}
        actions = ['cancel']
        if safe:
            actions.insert(0, 'retry-operation-preparation' if ref['valid'] else 'update-tool-reference')
    return {'phase': 'ACTIVE', 'title': title, 'severity': severity, 'recovery': recovery,
            'available_actions': actions, 'detail': (recovery or {}).get('message') or run.get('next_action') or title}
