"""Physical sequences tested only against in-memory motion transport."""
from dataclasses import replace
import unittest
from tests import test_machine_runtime as fixtures

class SeparatePreparationTest(unittest.TestCase):
    def runtime(self, **kwargs):
        return fixtures.ReferencePointMoveTest()._runtime(**kwargs)

    def test_home_does_not_travel(self):
        runtime, client = self.runtime()
        runtime.home_xyz()
        self.assertEqual(client.scripts,['G28'])
        self.assertEqual(runtime.snapshot()['state'],'HOMED')

    def test_prepare_standard_and_long_without_home(self):
        for profile,z in [('standard',115),('long_tool',130)]:
            with self.subTest(profile=profile):
                runtime, client = self.runtime()
                runtime.config=replace(runtime.config,long_tool_change_clearance_z_mm=130)
                runtime.prepare_position(profile)
                self.assertEqual(len(client.scripts),2)
                self.assertIn(f'Z{z:.6f}',client.scripts[0])
                self.assertNotIn('G28',' '.join(client.scripts))
                self.assertEqual(runtime.snapshot()['state'],'WAITING_FOR_XY_REFERENCE')

    def test_prepare_without_homing_emits_nothing(self):
        runtime,client=self.runtime(homed_axes='')
        with self.assertRaisesRegex(Exception,'Homing incompleto'):
            runtime.prepare_position()
        self.assertEqual(client.scripts,[])

    def test_stale_preparation_refresh_failure_emits_nothing(self):
        from unittest.mock import patch
        runtime, client = self.runtime()
        runtime._machine.live_position_updated_at -= 60
        self.assertFalse(runtime.snapshot()['safety']['telemetry_recent'])
        with patch.object(runtime, '_refresh_machine', side_effect=RuntimeError('HTTP unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP unavailable'):
                runtime.prepare_position()
        self.assertEqual(client.scripts, [])

    def test_long_reference_clearance_then_xy_then_approach(self):
        runtime,client=self.runtime()
        runtime.config=replace(runtime.config,long_tool_change_clearance_z_mm=140,long_tool_reference_approach_z_mm=125)
        result=runtime.go_to_reference_point(reference_x=42,reference_y=67,tool_reference_profile='long_tool')
        self.assertEqual(result['preparation_z'],125)
        self.assertEqual(len(client.scripts),3)
        self.assertIn('Z140.000000',client.scripts[0])
        self.assertIn('X42.000000',client.scripts[1])
        self.assertIn('Z125.000000',client.scripts[2])

    def test_invalid_clearance_blocks_before_motion(self):
        runtime,client=self.runtime()
        runtime.config=replace(runtime.config,long_tool_change_clearance_z_mm=500)
        with self.assertRaises(Exception):runtime.prepare_position('long_tool')
        self.assertEqual(client.scripts,[])

    def test_negative_z_direction_does_not_lower_for_travel(self):
        runtime,client=self.runtime()
        runtime.config=replace(runtime.config,tool_change_z_positive_up=False,tool_change_clearance_z_mm=100,long_tool_change_clearance_z_mm=80,safe_z_mm=100)
        runtime.prepare_position('long_tool')
        self.assertIn('Z30.000000',client.scripts[0])
        self.assertEqual(runtime.snapshot()['state'],'WAITING_FOR_XY_REFERENCE')

    def test_failed_z_never_starts_xy(self):
        from unittest.mock import patch
        runtime,client=self.runtime()
        with patch.object(runtime,'_move_absolute',side_effect=RuntimeError('fake failed Z')) as move:
            with self.assertRaisesRegex(RuntimeError,'fake failed Z'):
                runtime.prepare_position('standard')
        self.assertEqual(move.call_count,1)
        self.assertNotIn('x',move.call_args.kwargs)
        self.assertEqual(runtime.snapshot()['state'],'ERROR')
        self.assertEqual(client.scripts,[])

    def test_long_approach_persists_separately_from_clearance(self):
        import tempfile,json
        from pathlib import Path
        runtime,_=self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            runtime._settings_path=Path(directory)/'settings.json'
            runtime.update_machine_settings({'long_tool_reference_approach_z_mm':125,'long_tool_change_clearance_z_mm':140})
            stored=json.loads(runtime._settings_path.read_text())
            self.assertEqual(stored['long_tool_reference_approach_z_mm'],125)
            self.assertEqual(stored['long_tool_change_clearance_z_mm'],140)
            loaded=runtime._load_persisted_config(runtime.config)
            self.assertEqual(loaded.long_tool_reference_approach_z_mm,125)
            self.assertEqual(runtime.reference_preparation_z('long_tool'),125)
            self.assertEqual(runtime.reference_preparation_z('standard'),115)

    def test_inherited_approach_follows_standard_and_unrelated_save(self):
        runtime,_=self.runtime()
        self.assertIsNone(runtime.machine_settings()['long_tool_reference_approach_z_mm'])
        runtime.update_machine_settings({'reference_prep_z_mm':110})
        self.assertEqual(runtime.reference_preparation_z('long_tool'),110)
        settings=runtime.machine_settings()
        settings['reference_probe_step_mm']=0.1
        runtime.update_machine_settings(settings)
        self.assertIsNone(runtime.config.long_tool_reference_approach_z_mm)
        runtime.update_machine_settings({'reference_prep_z_mm':120})
        self.assertEqual(runtime.reference_preparation_z('long_tool'),120)

    def test_override_is_independent_and_reset_removes_persisted_value(self):
        import tempfile,json
        from pathlib import Path
        runtime,_=self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            runtime._settings_path=Path(directory)/'settings.json'
            runtime.update_machine_settings({'long_tool_reference_approach_z_mm':125})
            runtime.update_machine_settings({'reference_prep_z_mm':110})
            self.assertEqual(runtime.reference_preparation_z('long_tool'),125)
            runtime.update_machine_settings({'long_tool_reference_approach_z_mm':None})
            self.assertNotIn('long_tool_reference_approach_z_mm',json.loads(runtime._settings_path.read_text()))
            self.assertIsNone(runtime._load_persisted_config(replace(runtime.config,long_tool_reference_approach_z_mm=125)).long_tool_reference_approach_z_mm)
            runtime.update_machine_settings({'reference_prep_z_mm':120})
            self.assertEqual(runtime.reference_preparation_z('long_tool'),120)

    def test_legacy_file_inherits_without_reinterpreting_old_clearance_alias(self):
        import tempfile,json
        from pathlib import Path
        runtime,_=self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            runtime._settings_path=Path(directory)/'settings.json'
            runtime._settings_path.write_text(json.dumps({'reference_prep_z_mm':110,'long_tool_reference_prep_z_mm':140}))
            runtime.config=runtime._load_persisted_config(runtime.config)
            self.assertEqual(runtime.tool_change_clearance_z('long_tool'),140)
            self.assertEqual(runtime.reference_preparation_z('long_tool'),110)
            self.assertIsNone(runtime.machine_settings()['long_tool_reference_approach_z_mm'])
