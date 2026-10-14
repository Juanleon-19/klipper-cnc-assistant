"""Idle execution inspections use real discovery/state with a fake HTTP boundary."""
from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from tests import test_execution_readiness
from tests import test_machine_runtime
from klipper_cnc_assistant.machine.discovery import discover_machine
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError
from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter


class ExecutionFreshPositionTest(unittest.TestCase):
    setUp = test_execution_readiness.ExecutionReadinessTest.setUp
    tree = test_execution_readiness.ExecutionReadinessTest.tree

    def observed_runtime(self):
        runtime, _ = test_machine_runtime.ReferencePointMoveTest()._runtime()
        runtime._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        runtime._last_klippy_state = 'ready'
        status = {
            'toolhead': {'position': [4, 5, 30], 'homed_axes': 'xyz',
                         'axis_minimum': [0, 0, 0], 'axis_maximum': [100, 100, 200],
                         'max_velocity': 100, 'max_accel': 500},
            'motion_report': {'live_position': [4, 5, 30], 'live_velocity': 0},
        }
        client = Mock()
        client.get_server_info.return_value = {'klippy_state': 'ready'}
        client.query_objects.side_effect = lambda _: deepcopy(status)
        runtime._client = client
        runtime._discovery = discover_machine
        # Fake only HTTP/serial boundaries. Readiness uses real runtime snapshots,
        # discovery and MachineState timestamps without connecting any transport.
        def snapshot():
            result = runtime.snapshot()
            # The stored fixture token was measured with no discovered limits.
            # Keep that fixture identity; only observation fields are delegated.
            for key in ('limits', 'max_velocity', 'max_z_velocity', 'max_accel'):
                result['klipper'].pop(key, None)
            return result
        self.snapshot_patch = patch.object(self.runtime, 'snapshot', side_effect=snapshot)
        self.snapshot_patch.start()
        self.addCleanup(self.snapshot_patch.stop)
        self.refresh_patch = patch.object(self.runtime, 'refresh_observed_state', side_effect=runtime.refresh_observed_state)
        self.refresh = self.refresh_patch.start()
        self.addCleanup(self.refresh_patch.stop)
        return runtime, client, status

    def stale(self, runtime):
        age = runtime.config.telemetry_fresh_timeout_s + 30
        runtime._last_telemetry_at = time.monotonic() - age
        runtime._machine.live_position_updated_at = time.monotonic() - age
        runtime._last_http_observation_at = time.monotonic() - age
        runtime._last_packet_at = time.monotonic()  # independent Arduino heartbeat
        self.assertFalse(runtime.snapshot()['safety']['telemetry_recent'])

    def inspect(self):
        return self.job.inspect_execution(**self.args)['readiness']

    def codes(self, result):
        return [r['code'] for r in result['blocking_reasons']]

    def test_idle_timeout_refreshes_once_without_writes_or_reconnect(self):
        self.job.prepare_run(**self.args)
        runtime, client, _ = self.observed_runtime()
        self.assertTrue(self.inspect()['can_start'])
        self.refresh.assert_not_called()
        before, owner = self.tree(), self.runtime.physical_ownership.snapshot()
        self.stale(runtime)
        cached = runtime.snapshot()
        self.assertTrue(cached['moonraker']['websocket_connected'])
        self.assertTrue(cached['klipper']['ready'])
        self.assertEqual(cached['klipper']['homed_axes'], 'xyz')
        self.assertTrue(cached['safety']['serial_recent'])
        from klipper_cnc_assistant.execution.readiness import evaluate_readiness
        plan = self.job._resolved_plan(self.context, publish=False)
        old = evaluate_readiness(plan, self.job.run_store.load(self.path), cached, owner,
                                 self.runtime.current_physical_session_id())
        self.assertIn('TELEMETRIA_VIGENTE', self.codes(old))
        self.assertTrue(self.inspect()['can_start'])
        self.assertTrue(self.inspect()['can_start'])
        self.refresh.assert_called_once()
        client.query_objects.assert_called_once()
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)
        self.assertEqual([c[0] for c in client.method_calls], ['get_server_info', 'query_objects'])
        self.assertTrue(runtime.snapshot()['safety']['telemetry_recent'])

    def test_inspection_without_run_never_creates_one(self):
        runtime, client, _ = self.observed_runtime()
        before = self.tree()
        self.stale(runtime)
        self.assertTrue(self.inspect()['conditions_ready'])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.tree(), before)
        client.send_gcode.assert_not_called()

    def test_api_diagnostic_refreshes_idle_without_persisting(self):
        from tests import test_api
        fixture = test_api.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.app.state.job_service = self.job
        runtime, client, _ = self.observed_runtime()
        self.stale(runtime)
        before, owner = self.tree(), self.runtime.physical_ownership.snapshot()
        response = fixture.client.get(
            f'/api/projects/{self.fixture.project_id}/execution/diagnostic',
            params={'setup_id': self.fixture.setup_id, 'face': 'superior'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['readiness']['conditions_ready'])
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)
        self.assertFalse(self.path.exists())
        client.send_gcode.assert_not_called()

    def test_failed_http_blocks_with_actionable_reason(self):
        runtime, client, _ = self.observed_runtime()
        self.stale(runtime)
        client.query_objects.side_effect = TimeoutError('unavailable')
        result = self.inspect()
        self.assertFalse(result['conditions_ready'])
        reason = next(r for r in result['blocking_reasons'] if r['code'] == 'TELEMETRIA_VIGENTE')
        self.assertIn('No fue posible obtener', reason['message'])
        self.assertIn('Moonraker/Klipper', reason['action'])

    def test_not_ready_remains_blocked(self):
        runtime, client, _ = self.observed_runtime()
        self.stale(runtime)
        client.get_server_info.return_value = {'klippy_state': 'shutdown'}
        self.assertIn('KLIPPER_READY', self.codes(self.inspect()))
        client.query_objects.assert_not_called()

    def test_empty_homing_has_no_false_position_block(self):
        runtime, _, status = self.observed_runtime()
        status['toolhead']['homed_axes'] = ''
        self.stale(runtime)
        result = self.inspect()
        self.assertIn('HOMING', self.codes(result))
        self.assertNotIn('TELEMETRIA_VIGENTE', self.codes(result))

    def test_invalid_http_position_or_velocity_never_publishes_freshness(self):
        runtime, client, status = self.observed_runtime()
        good = deepcopy(status)
        for field, value in [('live_position', [float('nan'), 5, 30]),
                             ('live_position', [4, float('inf'), 30]),
                             ('live_position', None), ('live_position', []),
                             ('live_velocity', float('nan')), ('live_velocity', float('inf')),
                             ('live_velocity', None), ('live_velocity', -1)]:
            with self.subTest(field=field, value=value):
                status.clear(); status.update(deepcopy(good))
                status['motion_report'][field] = value
                self.stale(runtime)
                stamp = runtime._last_http_observation_at
                self.assertIn('TELEMETRIA_VIGENTE', self.codes(self.inspect()))
                self.assertEqual(runtime._last_http_observation_at, stamp)
                self.assertFalse(runtime.snapshot()['safety']['telemetry_recent'])
        client.send_gcode.assert_not_called()

    def test_recovery_and_foreign_owner_block_without_refresh(self):
        runtime, _, _ = self.observed_runtime()
        self.stale(runtime)
        coordinator = self.runtime.physical_ownership
        coordinator.acquire(OwnerKind.MESH, 'other')
        self.assertIn('OWNERSHIP_COMPATIBLE', self.codes(self.inspect()))
        self.refresh.assert_not_called()
        coordinator.enter_recovery('uncertain')
        self.assertIn('PHYSICAL_RECOVERY_REQUIRED', self.codes(self.inspect()))
        self.refresh.assert_not_called()

    def test_recovery_during_http_still_blocks(self):
        runtime, client, _ = self.observed_runtime()
        self.stale(runtime)
        query = client.query_objects.side_effect
        def changed(fields):
            self.runtime.physical_ownership.enter_recovery('changed during observation')
            return query(fields)
        client.query_objects.side_effect = changed
        self.assertIn('PHYSICAL_RECOVERY_REQUIRED', self.codes(self.inspect()))

    def test_disconnected_runtime_does_not_reconnect(self):
        runtime, client, _ = self.observed_runtime()
        self.stale(runtime)
        runtime._client = None
        self.assertIn('RUNTIME_CONECTADO', self.codes(self.inspect()))
        self.refresh.assert_not_called()
        self.assertEqual(client.method_calls, [])

    def test_start_and_resume_keep_real_authorizer_after_last_callback(self):
        from tests.test_motion_authorization import observed_machine
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator
        for action in ('start', 'resume'):
            with self.subTest(action=action):
                coordinator = PhysicalMachineCoordinator()
                permit = coordinator.acquire(OwnerKind.JOB_EXECUTION, 'test')
                machine = observed_machine(coordinator)
                authorizer = MotionAuthorizer(coordinator, machine)
                events = []
                client = Mock()
                client.start_print.side_effect = lambda _: events.append('send')
                client.resume_print.side_effect = lambda: events.append('send')
                runtime = SimpleNamespace(_client=client, require_physical_access=lambda: None,
                    _refresh_machine=lambda: events.append('refresh'),
                    _motion_authorizer=lambda: authorizer)
                adapter = MoonrakerJobAdapter(runtime); adapter.permit = permit
                invoke = (lambda callback: adapter.start_file('fake.gcode', before_send=callback)) if action == 'start' else (lambda callback: adapter.resume(before_send=callback))
                original = authorizer.require_context
                def require(*args):
                    events.append('authorize')
                    return original(*args)
                with patch.object(authorizer, 'require_context', side_effect=require):
                    invoke(lambda: events.append('last_callback'))
                    self.assertEqual(events, ['refresh', 'authorize', 'last_callback', 'send'])
                    client.reset_mock()
                    with self.assertRaises(OwnershipError):
                        invoke(lambda: coordinator.request_cancel(permit))
                    client.start_print.assert_not_called()
                    client.resume_print.assert_not_called()
