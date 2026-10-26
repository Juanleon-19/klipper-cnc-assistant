"""Reference approach, mesh clearance and contact are independent coordinates."""
import unittest
from unittest.mock import patch
from tests.test_machine_runtime import config, physical_runtime_with_machine, MachineMode
from klipper_cnc_assistant.machine.state import MachineState, MachinePosition, AxisLimits
from klipper_cnc_assistant.machine.runtime import ProbeResult, MachineRuntimeError, MachineRuntimeState

class ReferencePhaseContractTest(unittest.TestCase):
    def runtime(self, **overrides):
        machine=MachineState(MachinePosition(10,20,105), AxisLimits(0,200),AxisLimits(0,200),AxisLimits(0,200),'xyz',100,500)
        runtime,client=physical_runtime_with_machine(machine,cfg=config(MachineMode.PHYSICAL, reference_prep_z_mm=105, **overrides))
        runtime._state=MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        return runtime,client

    def test_reference_probe_does_not_reapply_historical_mesh_travel_height(self):
        runtime,client=self.runtime()
        with patch.object(runtime,'_perform_probe_descent',return_value=ProbeResult(10,20,99,'synthetic')) as descent:
            result=runtime.confirm_probe(expected_xy=(10,20),probe_config={'source':'machine_reference_profile','reference_z_mm':50,'safe_z_mm':65})
        self.assertEqual(client.scripts,[])
        self.assertEqual(result['last_probe_result']['z_mm'],99)
        self.assertEqual(descent.call_count,1)
        self.assertEqual(descent.call_args.kwargs['profile'].probe_feed_mm_min,runtime._reference_probe_profile().probe_feed_mm_min)

    def test_wrong_reference_xy_blocks_before_probe(self):
        runtime,client=self.runtime()
        with patch.object(runtime,'_perform_probe_descent') as descent:
            with self.assertRaisesRegex(MachineRuntimeError,'punto X/Y'):
                runtime.confirm_probe(expected_xy=(30,40))
        descent.assert_not_called();self.assertEqual(client.scripts,[])

    def test_standard_long_approach_and_mesh_height_independent(self):
        runtime,_=self.runtime(long_tool_reference_approach_z_mm=125,long_tool_change_clearance_z_mm=140)
        self.assertEqual(runtime.reference_preparation_z('standard'),105)
        self.assertEqual(runtime.reference_preparation_z('long_tool'),125)
        self.assertEqual(runtime.tool_change_clearance_z('standard'),115)
        self.assertEqual(runtime.tool_change_clearance_z('long_tool'),140)
        self.assertEqual(runtime._mesh_safe_z(runtime._machine,probe_config={'reference_z_mm':99.5,'safe_z_mm':10}),109.5)

    def test_auxiliary_direction_uses_existing_feeds_not_probe_feed(self):
        runtime,_=self.runtime(z_clearance_feed_mm_min=180,reference_approach_z_feed_mm_min=90)
        self.assertEqual(runtime._reference_target_z_feed(current_z=99.5,target_z=109.5),180)
        self.assertEqual(runtime._reference_target_z_feed(current_z=125,target_z=109.5),90)
