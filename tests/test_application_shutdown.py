"""No physical clients: deterministic events and temporary persistence only."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from uvicorn import Config
from uvicorn.lifespan.on import LifespanOn
from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.application.shutdown import ApplicationShutdown, ApplicationShutdownError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict('os.environ', MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false'):
            self.app = create_app(data_dir=Path(self.temp.name))
        self.runtime = self.app.state.machine_runtime
        self.manager = Mock()
        self.telemetry = Mock()
        self.client = Mock()
        self.lock = Mock()
        self.runtime._connection_manager = self.manager
        self.runtime._telemetry = self.telemetry
        self.runtime._client = self.client
        self.runtime._process_lock = self.lock
        self.shutdown = self.app.state.application_shutdown
        self.shutdown.producer_timeout = 0.05
        self.shutdown.cleanup_timeout = 0.05
        self.network = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def producer(self, *, cooperate=True, signal_error=False):
        release = threading.Event()
        entered = threading.Event()
        def work():
            entered.set()
            release.wait()
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        self.addCleanup(lambda: (release.set(), thread.join(1)))
        producer = Mock()
        producer.application_workers.return_value = (thread,)
        if signal_error:
            producer.request_application_shutdown.side_effect = RuntimeError('producer failed')
        elif cooperate:
            producer.request_application_shutdown.side_effect = release.set
        self.shutdown.producers += (producer,)
        return producer, release, thread

    async def test_normal_and_idempotent(self):
        await self.shutdown.close()
        await self.shutdown.close()
        self.manager.stop.assert_called_once()
        self.telemetry.stop.assert_called_once()
        self.client.session.close.assert_called_once()
        self.lock.close.assert_called_once()

    async def test_original_uvicorn_reproduction_now_passes_without_releasing_owner(self):
        # Exact original lifecycle, fake retained root, manager and byte assertion.
        path = Path(self.temp.name) / 'synthetic-current-run.json'
        path.write_text(json.dumps({'state': 'SPINDLE_STOP_REQUIRED',
                                   'operations': ['COMPLETED'] + ['PENDING'] * 4}))
        before = path.read_bytes()
        life = LifespanOn(Config(self.app, lifespan='on', log_level='critical'))
        await life.startup()
        self.assertFalse(life.startup_failed)
        permit = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'synthetic/setup/superior')
        self.runtime.physical_ownership.enter_recovery('synthetic retained owner')
        await life.shutdown()
        self.assertFalse(life.shutdown_failed)
        self.manager.stop.assert_called_once()
        self.assertEqual(path.read_bytes(), before)
        owner = self.runtime.physical_ownership.snapshot()
        self.assertEqual(owner['kind'], 'RECOVERY')
        self.assertTrue(owner['producer_active'])
        self.assertEqual(owner['owner_id'], permit.owner_id)
        with self.assertRaises(OwnershipError):
            self.runtime.physical_ownership.begin_dispatch(permit)

    async def test_cooperative_worker_finishes_before_transport(self):
        producer, release, thread = self.producer()
        self.manager.stop.side_effect = lambda: self.assertFalse(thread.is_alive())
        await self.shutdown.close()
        producer.request_application_shutdown.assert_called_once()

    async def test_unresponsive_worker_fails_closed(self):
        self.producer(cooperate=False)
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_not_called()
        self.client.session.close.assert_not_called()
        self.lock.close.assert_not_called()
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()

    async def test_signal_exception_still_signals_other_producers(self):
        producer, _, _ = self.producer(signal_error=True)
        other, release, thread = self.producer()
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        other.request_application_shutdown.assert_called_once()
        self.manager.stop.assert_not_called()

    async def test_transport_exception_continues_independent_cleanup(self):
        self.manager.stop.side_effect = RuntimeError('serial stop failed')
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.telemetry.stop.assert_called_once()
        self.client.session.close.assert_called_once()
        self.lock.close.assert_not_called()
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_called_once()

    async def test_transport_timeout_bounded_no_double_close(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.manager.stop.side_effect = release.wait
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.telemetry.stop.assert_called_once()
        self.client.session.close.assert_called_once()
        self.lock.close.assert_not_called()
        release.set()
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_called_once()

    async def test_dispatch_in_flight_prevents_transport_close(self):
        permit = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'p/s/f')
        self.runtime.physical_ownership.begin_dispatch(permit)
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_not_called()
        self.runtime.physical_ownership.end_dispatch(permit)

    async def test_children_prevent_transport_close(self):
        permit = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'p/s/f')
        self.runtime.physical_ownership.delegate(permit, 'child')
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_not_called()

    async def test_new_operations_and_reconcile_blocked(self):
        await self.shutdown.close()
        owner = self.runtime.physical_ownership
        for action in [lambda: owner.acquire(OwnerKind.JOB_EXECUTION, 'p/s/f'),
                       lambda: owner.begin_job_update(project_id='p'),
                       lambda: owner.begin_project_edit('p'),
                       lambda: owner.begin_administration(expected_revision=owner.snapshot()['revision']),
                       lambda: owner.reconcile_quiescent(expected_session=owner.session, quiescent=True),
                       self.runtime.require_physical_access, self.runtime.start, self.runtime.connect]:
            with self.assertRaises(OwnershipError):
                action()

    async def test_http_rejected_after_shutdown_begins(self):
        import httpx
        await self.shutdown.close()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            result = await client.post('/api/machine/connect')
        self.assertEqual(result.status_code, 503)

    async def test_active_request_prevents_transport_cleanup(self):
        self.shutdown.active_requests = 1
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.manager.stop.assert_not_called()

    async def test_real_waiting_job_worker_preserves_current_run(self):
        from klipper_cnc_assistant.execution.job_service import JobContext
        service = self.app.state.job_service
        context = JobContext('synthetic', 'setup', 'superior')
        run = {'run_id': 'r', 'state': 'SPINDLE_STOP_REQUIRED'}
        service._load_run = Mock(return_value=run)
        service._save_run = Mock(side_effect=AssertionError('Must not rewrite waiting job'))
        key = (context.project_id, context.setup_id, context.face)
        permit = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, '/'.join(key))
        service._physical_leases[key] = permit
        service._run_worker(context, 'r')
        self.assertTrue(self.runtime.physical_ownership.snapshot()['producer_active'])
        await self.shutdown.close()
        service._save_run.assert_not_called()
        self.assertEqual(run, {'run_id': 'r', 'state': 'SPINDLE_STOP_REQUIRED'})

    async def test_actual_job_store_and_all_project_files_preserved(self):
        from tests.test_job_service import JobServiceTest
        fixture = JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        service = fixture.job_service
        args = dict(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        run = service.prepare_run(**args)
        context = service._context(**args)
        run['state'] = 'SPINDLE_STOP_REQUIRED'
        run['operations'][0]['execution_status'] = 'COMPLETED'
        service._save_run(context, run)
        before = {p: p.read_bytes() for p in fixture.repository.base_dir.rglob('*') if p.is_file()}
        service.runtime = self.runtime
        permit = service._job_permit(context)
        self.runtime.physical_ownership.enter_recovery('retained waiting JOB')
        self.shutdown.producers = (service, self.app.state.mesh_execution_service)
        await self.shutdown.close()
        after = {p: p.read_bytes() for p in fixture.repository.base_dir.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(fixture.adapter.started, [])
        self.assertEqual(fixture.adapter.cancel_calls, 0)
        self.assertEqual(self.runtime.physical_ownership.snapshot()['owner_id'], permit.owner_id)

    async def test_real_job_observer_exits_without_poll_result_write(self):
        from klipper_cnc_assistant.execution.job_service import JobContext
        service = self.app.state.job_service
        context = JobContext('p', 's', 'f')
        run = {'run_id': 'r', 'state': 'OPERATION_RUNNING', 'current_operation_index': 0,
               'current_operation_id': 'op', 'operations': [{'operation_id': 'op'}]}
        service._load_run = Mock(return_value=run)
        service._save_run = Mock(side_effect=AssertionError('No poll result writes after shutdown'))
        entered = threading.Event()
        def observation():
            entered.set()
            if not service._shutdown.wait(1):
                raise AssertionError('Shutdown signal missing')
            return {'state': 'printing'}
        adapter = Mock()
        adapter.print_status.side_effect = observation
        service.adapter_factory = lambda runtime: adapter
        key = ('p', 's', 'f')
        service._physical_leases[key] = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'p/s/f')
        thread = threading.Thread(target=service._run_worker, args=(context, 'r'), daemon=True)
        service._threads[key] = thread
        thread.start()
        self.assertTrue(entered.wait(1))
        await self.shutdown.close()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        service._save_run.assert_not_called()
        adapter.start_file.assert_not_called()
        adapter.cancel.assert_not_called()

    async def test_movement_lock_blocks_cleanup(self):
        self.runtime._movement_lock.acquire()
        try:
            with self.assertRaises(ApplicationShutdownError):
                await self.shutdown.close()
            self.manager.stop.assert_not_called()
        finally:
            self.runtime._movement_lock.release()

    async def test_project_writer_blocks_cleanup(self):
        token = self.runtime.physical_ownership.begin_project_edit('project')
        try:
            with self.assertRaises(ApplicationShutdownError):
                await self.shutdown.close()
            self.manager.stop.assert_not_called()
        finally:
            self.runtime.physical_ownership.end_project_edit(token)

    async def test_telemetry_failure_does_not_skip_http_cleanup(self):
        self.telemetry.stop.side_effect = RuntimeError('websocket failed')
        with self.assertRaises(ApplicationShutdownError):
            await self.shutdown.close()
        self.client.session.close.assert_called_once()
        self.lock.close.assert_not_called()

    async def test_caller_cancellation_does_not_cancel_shutdown(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        def stop():
            entered.set()
            release.wait(1)
        self.manager.stop.side_effect = stop
        self.shutdown.cleanup_timeout = 1
        caller = asyncio.create_task(self.shutdown.close())
        while not entered.is_set():
            await asyncio.sleep(0)
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        release.set()
        await self.shutdown.close()
        self.manager.stop.assert_called_once()
        self.lock.close.assert_called_once()

    async def test_motion_context_captured_before_shutdown_cannot_dispatch(self):
        from tests.test_motion_authorization import observed_machine
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        owner = self.runtime.physical_ownership
        permit = owner.acquire(OwnerKind.JOB_EXECUTION, 'p/s/f')
        authorizer = MotionAuthorizer(owner, observed_machine(owner))
        context = authorizer.require_context(permit, 'job_start')
        send = Mock(side_effect=AssertionError('No physical send allowed'))
        await self.shutdown.close()
        with self.assertRaises(OwnershipError):
            authorizer.dispatch(context, send)
        send.assert_not_called()

    async def test_runtime_operation_gets_cooperative_signal(self):
        from types import SimpleNamespace
        cancelled = threading.Event()
        self.runtime._active_operation = SimpleNamespace(cancel_event=cancelled)
        self.runtime._movement_lock.acquire()
        def finish():
            cancelled.wait(1)
            with self.runtime._lock:
                self.runtime._active_operation = None
            self.runtime._movement_lock.release()
        thread = threading.Thread(target=finish, daemon=True)
        thread.start()
        await self.shutdown.close()
        thread.join(1)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(thread.is_alive())
        self.manager.stop.assert_called_once()

    async def test_concurrent_shutdown_callers_share_one_cleanup(self):
        await asyncio.gather(self.shutdown.close(), self.shutdown.close())
        self.manager.stop.assert_called_once()
        self.client.session.close.assert_called_once()
        self.lock.close.assert_called_once()

    async def test_lifecycle_in_progress_blocks_cleanup(self):
        entered = threading.Event()
        release = threading.Event()
        def connecting():
            with self.runtime._lifecycle_lock:
                entered.set()
                release.wait(1)
        thread = threading.Thread(target=connecting, daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(ApplicationShutdownError):
                await self.shutdown.close()
            self.manager.stop.assert_not_called()
        finally:
            release.set()
            thread.join(1)

    async def test_direct_stop_cannot_double_close_after_application_shutdown(self):
        await self.shutdown.close()
        with self.assertRaises(OwnershipError):
            self.runtime.stop()
        self.manager.stop.assert_called_once()
