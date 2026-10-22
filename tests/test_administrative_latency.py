"""Deterministic administrative latency regressions; temporary data and fakes only."""
import unittest
from unittest.mock import patch

from tests import test_administrative_remote_identity as identity


class AdministrativeLatencyTest(unittest.TestCase):
    def setUp(self):
        self.fixture = identity.AdministrativeIdentityTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.clock = 100.0

    def test_slow_http_response_is_not_already_expired(self):
        f = self.fixture
        def slow_observation():
            self.clock += 3.0
            return dict(f.remote)
        with patch('klipper_cnc_assistant.execution.administrative_guard.time.monotonic',
                   side_effect=lambda: self.clock), patch.object(
                       f.f.fixture.adapter, 'print_status', side_effect=slow_observation):
            result = f.f.reset()
        self.assertEqual(result['current_runs_cleared'], 1)
        self.assertFalse(f.s.administrative_pending_path.exists())
        self.assertEqual(f.f.fixture.adapter.started, [])

    def test_slow_history_write_does_not_expire_transaction(self):
        f = self.fixture
        original = f.s._write_archived_run
        def slow_write(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock += 3.0
            return result
        with patch('klipper_cnc_assistant.execution.administrative_guard.time.monotonic',
                   side_effect=lambda: self.clock), patch.object(
                       f.s, '_write_archived_run', side_effect=slow_write):
            result = f.f.reset()
        self.assertEqual(result['current_runs_cleared'], 1)
        self.assertFalse(f.s.administrative_pending_path.exists())
        self.assertEqual(f.f.fixture.adapter.started, [])

    def test_remote_change_after_history_preserves_current_and_failure_journal(self):
        f = self.fixture
        from klipper_cnc_assistant.application.errors import ApplicationError
        original = f.s._write_archived_run
        before = f.f.path.read_bytes()
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            f.remote['filename'] = 'synthetic/other.gcode'
            return result
        with patch.object(f.s, '_write_archived_run', side_effect=changed):
            with self.assertRaisesRegex(ApplicationError, 'archivo distinto'):
                f.f.reset()
        self.assertEqual(before, f.f.path.read_bytes())
        self.assertTrue(f.s.administrative_pending_path.exists())
        self.assertTrue(f.f.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertEqual(f.f.fixture.adapter.started, [])

    def test_new_observation_timestamp_is_response_completion(self):
        f = self.fixture
        def response():
            self.clock += 3.0
            return dict(f.remote)
        with patch('klipper_cnc_assistant.execution.administrative_guard.time.monotonic',
                   side_effect=lambda: self.clock), patch.object(
                       f.f.fixture.adapter, 'print_status', side_effect=response):
            with f.s.administrative_mutation('test', allow_started_runs=True) as guard:
                self.assertEqual(guard.observed_at, self.clock)
                previous = guard.observed_at
                guard.observe_before_commit()
                self.assertGreater(guard.observed_at, previous)
                self.assertEqual(guard.observed_at, self.clock)
