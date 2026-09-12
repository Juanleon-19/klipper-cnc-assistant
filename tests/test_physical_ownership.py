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
