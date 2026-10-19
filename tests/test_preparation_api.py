import unittest
from unittest.mock import patch
from tests import test_api
from klipper_cnc_assistant.application.errors import ApplicationError

class PreparationAPITest(unittest.TestCase):
    def setUp(self):
        self.fixture=test_api.ApiTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.runtime=self.fixture.app.state.machine_runtime

    def test_routes_are_distinct_and_forward_profiles(self):
        for route,method,payload,args in [('/home','home_xyz',{},()),('/prepare-position','prepare_position',{'tool_reference_profile':'long_tool'},('long_tool',)),('/tool-change-position','move_to_tool_change_position',{'tool_reference_profile':'long_tool'},('long_tool',))]:
            with self.subTest(route=route), patch.object(self.runtime,method,return_value=self.runtime.snapshot()) as move:
                response=self.fixture.client.post('/api/machine'+route,json=payload)
                self.assertEqual(response.status_code,200,response.text)
                move.assert_called_once_with(*args)

    def test_active_job_blocks_every_standalone_route(self):
        with patch.object(self.fixture.app.state.job_service,'assert_machine_settings_update_allowed',side_effect=ApplicationError('Trabajo activo')):
            for route,method in [('/home','home_xyz'),('/prepare-position','prepare_position'),('/tool-change-position','move_to_tool_change_position'),('/initialize','initialize')]:
                with self.subTest(route=route),patch.object(self.runtime,method) as move:
                    response=self.fixture.client.post('/api/machine'+route,json={})
                    self.assertEqual(response.status_code,400,response.text)
                    move.assert_not_called()

    def test_settings_api_preserves_null_and_distinguishes_omission(self):
        response=self.fixture.client.get('/api/machine/settings')
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()['long_tool_reference_approach_z_mm'])
        for payload,expected in [({'long_tool_reference_approach_z_mm':125},125),({'reference_probe_step_mm':0.1},125),({'long_tool_reference_approach_z_mm':None},None)]:
            response=self.fixture.client.put('/api/machine/settings',json=payload)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['long_tool_reference_approach_z_mm'],expected)
            snapshot=self.fixture.client.get('/api/machine/status')
            self.assertEqual(snapshot.status_code,200,snapshot.text)
            self.assertEqual(snapshot.json()['settings']['long_tool_reference_approach_z_mm'],expected)
