"""Advisory recovery preserves only a proven failed tool transition."""
from copy import deepcopy
import unittest
from klipper_cnc_assistant.application import ApplicationError  # initialize public services
from klipper_cnc_assistant.execution.health import execution_health


class DeferredExecutionHealthTest(unittest.TestCase):
    def test_ambiguous_recovery_never_exposes_transition_retry(self):
        run = {'state': 'RECOVERY_REQUIRED', 'available_actions': ['retry-tool-change-transition', 'cancel'],
               'last_transition_error': {'code': 'TOOL_CHANGE_TRANSITION_FAILED', 'message': 'failed'},
               'current_operation_index': 0, 'operations': [
                   {'operation_id': 'a', 'tool_key': 'A', 'execution_status': 'COMPLETED',
                    'physical_reference_token': {'physical_session_id': 'session'}, 'remote_file': 'a.gcode'},
                   {'operation_id': 'b', 'tool_key': 'B', 'execution_status': 'PENDING'}]}
        status = {'connected': True, 'print_state': 'complete', 'is_active': False, 'filename': 'a.gcode'}
        readiness = {'physical_session_id': 'session', 'reference': {'valid': True, 'code': 'VALID', 'message': 'Lista'}, 'blocking_reasons': []}
        self.assertIn('retry-tool-change-transition', execution_health(run, status, readiness, False)['available_actions'])
        variants = [
            ('status', {'connected': False}), ('status', {'is_active': True}),
            ('status', {'print_state': 'paused'}), ('status', {'filename': 'other.gcode'}),
            ('readiness', {'physical_session_id': 'other'}),
            ('readiness', {'blocking_reasons': [{'category': 'runtime', 'code': 'PHYSICAL_RECOVERY_REQUIRED'}]}),
            ('readiness', {'blocking_reasons': [{'category': 'runtime', 'code': 'OWNERSHIP_COMPATIBLE'}]}),
            ('run', {'start_attempt': {'status': 'uncertain', 'operation_id': 'a', 'may_have_been_sent': True}}),
            ('run', {'start_attempt': {'status': 'dispatching', 'operation_id': 'a', 'may_have_been_sent': False}}),
            ('run', {'start_attempt': {'status': 'sending', 'operation_id': 'a', 'may_have_been_sent': True}}),
            ('run', {'start_attempt': {'status': 'sent', 'operation_id': 'b', 'may_have_been_sent': True}}),
            ('run', {'last_transition_error': {'code': 'OTHER'}}),
        ]
        for target, mutation in variants:
            with self.subTest(target=target, mutation=mutation):
                values = deepcopy({'run': run, 'status': status, 'readiness': readiness})
                values[target].update(mutation)
                self.assertNotIn('retry-tool-change-transition', execution_health(**values, worker_alive=False)['available_actions'])
        self.assertNotIn('retry-tool-change-transition', execution_health(run, status, readiness, True)['available_actions'])
