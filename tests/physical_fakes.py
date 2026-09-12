"""Explicit test doubles: never acquire the host lock or contact hardware."""
from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator


class FakePhysicalAccess:
    def __init__(self):
        self.held = True

    def acquire(self):
        self.held = True

    def require_held(self):
        if not self.held:
            raise RuntimeError('Fake physical access closed')

    def close(self):
        self.held = False


class FakeWorkflowOwnership:
    def init_ownership(self):
        self.physical_ownership = PhysicalMachineCoordinator()

    def reconcile_physical_ownership(self):
        if getattr(self, 'movement_lock', False):
            return False
        self.physical_ownership.reconcile_quiescent(
            expected_session=self.physical_ownership.session, quiescent=True)
        return True
