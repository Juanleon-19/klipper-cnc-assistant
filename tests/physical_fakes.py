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
        self.reference_serial_generation = 1

    def current_physical_session_id(self):
        return f"test-runtime#serial-{self.reference_serial_generation}"

    def reconcile_physical_ownership(self):
        if getattr(self, 'movement_lock', False):
            return False
        self.physical_ownership.reconcile_quiescent(
            expected_session=self.physical_ownership.session, quiescent=True)
        return True


def measure_test_tool_reference(repository, maps, runtime, project_id, operation_id, map_id):
    """Model a new successful tool measurement explicitly; never migrate old evidence."""
    payload = maps.get_by_id(project_id, map_id)
    operation = repository.load_project(project_id).get_operation(operation_id)
    reference = (payload['tool_references'].get(operation.tool_id) or
                 next(iter(payload['tool_references'].values())))
    return maps.record_tool_reference(
        project_id=project_id, operation_id=operation_id, map_id=map_id,
        position={'x_mm': payload['machine_origin_x'], 'y_mm': payload['machine_origin_y'],
                  'z_mm': reference['reference_z']}, machine_label='simulated-test', homed_axes='xyz',
        session_id=runtime.current_physical_session_id(), installation_id=reference['installation_id'],
        runtime=runtime,
    )
