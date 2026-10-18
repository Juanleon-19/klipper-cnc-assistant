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
