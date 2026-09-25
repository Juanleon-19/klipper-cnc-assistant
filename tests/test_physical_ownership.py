import unittest

from klipper_cnc_assistant.machine.physical_ownership import (
    OwnerKind, OwnershipError, PhysicalMachineCoordinator,
)


class PhysicalOwnershipTest(unittest.TestCase):
    def test_job_excludes_manual_and_mesh_excludes_job(self):
        for first, second in [(OwnerKind.JOB_EXECUTION, OwnerKind.RUNTIME_MOTION),
                              (OwnerKind.MESH, OwnerKind.JOB_EXECUTION)]:
            with self.subTest(first=first):
                coordinator = PhysicalMachineCoordinator()
                coordinator.acquire(first, 'workflow')
                with self.assertRaises(OwnershipError):
                    coordinator.acquire(second, 'incompatible')

    def test_job_delegation_does_not_create_second_root(self):
        coordinator = PhysicalMachineCoordinator()
        root = coordinator.acquire(OwnerKind.JOB_EXECUTION, 'job')
        child = coordinator.delegate(root, 'tool-change')
        coordinator.validate(child)
        self.assertEqual(child.root, root.root)
        with self.assertRaises(OwnershipError):
            coordinator.release(root, quiescent=True)
        coordinator.release(child, quiescent=True)
        coordinator.release(root, quiescent=True)
        self.assertEqual(coordinator.snapshot()['kind'], 'IDLE')

    def test_previous_session_and_released_lease_fail_closed(self):
        coordinator = PhysicalMachineCoordinator()
        permit = coordinator.acquire(OwnerKind.RUNTIME_MOTION, 'motion')
        coordinator.retire(permit)
        coordinator.reconcile_quiescent(expected_session=coordinator.session, quiescent=True)
        with self.assertRaises(OwnershipError):
            coordinator.validate(permit)
        replacement = coordinator.acquire(OwnerKind.RUNTIME_MOTION, 'motion')
        self.assertNotEqual(replacement.session, permit.session)

    def test_timeout_and_cancel_never_release_ownership(self):
        for cancel in [False, True]:
            with self.subTest(cancel=cancel):
                coordinator = PhysicalMachineCoordinator()
                lease = coordinator.acquire(OwnerKind.MESH, 'mesh')
                if cancel:
                    coordinator.request_cancel(lease)
                else:
                    coordinator.enter_recovery('timeout', lease)
                self.assertEqual(coordinator.snapshot()['kind'], 'RECOVERY')
                self.assertEqual(coordinator.snapshot()['owner_id'], 'mesh')
                with self.assertRaises(OwnershipError):
                    coordinator.release(lease, quiescent=True)
                with self.assertRaises(OwnershipError):
                    coordinator.acquire(OwnerKind.JOB_EXECUTION, 'job')

    def test_previous_process_exit_is_not_remote_idle_evidence(self):
        coordinator = PhysicalMachineCoordinator(uncertain=True)
        with self.assertRaises(OwnershipError):
            coordinator.acquire(OwnerKind.RUNTIME_MOTION, 'new-process')
        with self.assertRaises(OwnershipError):
            coordinator.reconcile_quiescent(expected_session=coordinator.session, quiescent=False)
        self.assertEqual(coordinator.snapshot()['kind'], 'RECOVERY')

    def test_reconciliation_cannot_race_an_inflight_dispatch(self):
        coordinator = PhysicalMachineCoordinator()
        permit = coordinator.acquire(OwnerKind.RUNTIME_MOTION, 'motion')
        coordinator.begin_dispatch(permit)
        with self.assertRaises(OwnershipError):
            coordinator.reconcile_quiescent(expected_session=coordinator.session, quiescent=True)
        coordinator.end_dispatch(permit)


class WorkflowCompletionTest(unittest.TestCase):
    def test_normal_completion_preserves_epoch_but_revokes_old_lease(self):
        c = PhysicalMachineCoordinator()
        lease = c.acquire(OwnerKind.MESH, 'mesh')
        epoch = c.session
        c.complete_quiescent(lease, expected_revision=c.snapshot()['revision'], quiescent=True)
        self.assertEqual(c.session, epoch)
        self.assertEqual(c.snapshot()['kind'], 'IDLE')
        with self.assertRaises(OwnershipError):
            c.validate(lease)
        replacement = c.acquire(OwnerKind.JOB_EXECUTION, 'job')
        self.assertEqual(replacement.session, epoch)
        self.assertNotEqual(replacement.token, lease.token)

    def test_normal_completion_rejects_uncertainty_cancel_children_dispatch_and_stale_observation(self):
        for cause in ('recovery', 'cancel', 'child', 'dispatch', 'stale', 'not_idle'):
            with self.subTest(cause=cause):
                c = PhysicalMachineCoordinator()
                lease = c.acquire(OwnerKind.MESH, 'mesh')
                revision = c.snapshot()['revision']
                if cause == 'recovery': c.enter_recovery('uncertain')
                if cause == 'cancel': c.request_cancel(lease)
                if cause == 'child': c.delegate(lease, 'child')
                if cause == 'dispatch': c.begin_dispatch(lease)
                if cause == 'stale': revision -= 1
                with self.assertRaises(OwnershipError):
                    c.complete_quiescent(lease, expected_revision=revision, quiescent=cause != 'not_idle')
                self.assertTrue(c.snapshot()['active'])
