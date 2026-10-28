"""Two synthetic projects, one fake CNC; no production files or transports."""
from copy import deepcopy
import json
import math
import unittest
from unittest.mock import patch
from tests import test_job_service as fixtures
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
from klipper_cnc_assistant.machine.physical_reference import require_current_reference, PhysicalReferenceError


class ProjectReferenceIsolationTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.JobServiceTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.s=self.f.job_service;self.runtime=self.f.runtime
        self.a=dict(project_id=self.f.project_id,setup_id=self.f.setup_id,face='superior')
        b,setup,_=self.f._create_same_diameter_distinct_tool_project()
        self.b=dict(project_id=b,setup_id=setup,face='superior')
        self.ca=self.s._context(**self.a);self.cb=self.s._context(**self.b)
        self.run_a=self.s.prepare_run(**self.a)
        op=self.run_a['operations'][1]
        op.update(execution_status='RUNNING',remote_file='synthetic/a.gcode',observed_printing=True,finished_at=None)
        self.run_a.update(state='JOB_CANCELLED',current_operation_index=1,
            start_attempt=dict(id='a-attempt',status='sent',may_have_been_sent=True,operation_id=op['operation_id'],remote_file=op['remote_file']))
        self.path_a=self.s._run_file(self.ca);self.path_a.write_text(json.dumps(self.run_a))
        self.before_a=self.path_a.read_bytes()

    def test_cancelled_A_does_not_block_valid_B_or_duplicate_initial_probe(self):
        run=self.s.prepare_run(**self.b)
        self.assertEqual(run['state'],'JOB_READY')
        token=deepcopy(run['operations'][0]['physical_reference_token'])
        with patch.object(self.s,'_start_worker'):
            started=self.s.start_run(**self.b,spindle_prepared=True)
        self.s._execute_next_operation(self.cb,started)
        current=self.s._load_run(self.cb)
        self.assertEqual(self.f.adapter.probe_calls,0)
        self.assertEqual(len(self.f.adapter.started),1)
        self.assertEqual(current['operations'][0]['physical_reference_token'],token)
        self.assertEqual(self.path_a.read_bytes(),self.before_a)

    def test_global_active_or_recovery_owner_A_blocks_B(self):
        owner=self.runtime.physical_ownership
        owner.acquire(OwnerKind.JOB_EXECUTION,self.a['project_id']+'/setup-main/superior')
        for recovery in (False,True):
            if recovery: owner.enter_recovery('synthetic ambiguous physical action')
            before=owner.snapshot()
            run=self.s.prepare_run(**self.b)
            self.assertFalse(run['ready'])
            with self.assertRaises(ApplicationError):self.s.start_run(**self.b,spindle_prepared=True)
            self.assertEqual(owner.snapshot(),before)
        self.assertEqual(self.f.adapter.started,[])
        self.assertEqual(self.path_a.read_bytes(),self.before_a)

    def test_B_rejects_A_token_without_adoption(self):
        run=self.s.prepare_run(**self.b);op=run['operations'][0]
        maps=self.f.physical_map_service
        payload=maps.get_active(self.b['project_id'],op['operation_id'])
        token=payload['tool_references'][op['tool_key']]['physical_reference_token']
        token['project_id']=self.a['project_id']
        with self.assertRaises(PhysicalReferenceError):
            require_current_reference(self.f.repository,self.runtime,self.b['project_id'],op['operation_id'],payload)
        self.assertEqual(self.f.adapter.started,[])

    def test_roundoff_only_never_rewrites_token_or_accepts_real_displacement(self):
        run=self.s.prepare_run(**self.b);op=run['operations'][0]
        payload=self.f.physical_map_service.get_active(self.b['project_id'],op['operation_id'])
        ref=payload['tool_references'][op['tool_key']];token=ref['physical_reference_token']
        y=token['measured_position']['y_mm']
        ref['reference_y']=math.nextafter(y,math.inf)
        token['measured_position']['y_mm']=ref['reference_y']
        before=deepcopy(payload)
        valid=require_current_reference(self.f.repository,self.runtime,self.b['project_id'],op['operation_id'],payload)
        self.assertEqual(valid,token);self.assertEqual(payload,before)
        ref['reference_y']=y+.001;token['measured_position']['y_mm']=y+.001
        with self.assertRaisesRegex(PhysicalReferenceError,'X/Y'):
            require_current_reference(self.f.repository,self.runtime,self.b['project_id'],op['operation_id'],payload)

    def test_synchronous_reference_failure_retires_own_producer_keeps_recovery(self):
        self.s.prepare_run(**self.b)
        def failed(context,run):
            self.s._job_permit(context)
            raise ApplicationError('synthetic canonical mismatch')
        with patch.object(self.s,'_measure_tool_reference',side_effect=failed):
            with self.assertRaisesRegex(ApplicationError,'canonical mismatch'):
                self.s.run_action(**self.b,action='update-tool-reference')
        owner=self.runtime.physical_ownership.snapshot()
        self.assertFalse(owner['producer_active']);self.assertTrue(owner['recovery_pending'])
        self.assertEqual(owner['owner_id'],self.b['project_id']+'/'+self.b['setup_id']+'/superior')
        self.assertEqual(self.s._load_run(self.cb)['state'],'RECOVERY_REQUIRED')
        self.assertEqual(self.path_a.read_bytes(),self.before_a)
        self.assertEqual(self.f.adapter.started,[])

    def test_finished_worker_retires_producer_without_releasing_recovery(self):
        run=self.s.prepare_run(**self.b)
        permit=self.s._job_permit(self.cb)
        run['state']='RECOVERY_REQUIRED'
        self.s._save_run(self.cb,run)
        self.runtime.physical_ownership.enter_recovery('synthetic unresolved observation')
        self.s._run_worker(self.cb,run['run_id'])
        snapshot=self.runtime.physical_ownership.snapshot()
        self.assertFalse(snapshot['producer_active'])
        self.assertTrue(snapshot['recovery_pending'])
        self.assertEqual(snapshot['owner_id'],permit.owner_id)
        self.assertEqual(self.f.adapter.started,[])
        self.assertEqual(self.path_a.read_bytes(),self.before_a)

    def test_wrong_initial_tool_explains_measurement_without_rebinding(self):
        run=self.s.prepare_run(**self.b)
        operation=self.f.repository.load_project(self.b['project_id']).get_operation(run['operations'][1]['operation_id'])
        payload=self.f.physical_map_service.get_active(self.b['project_id'],operation.id)
        payload['tool_references'].pop(operation.tool_id, None)
        payload['initial_reference_binding']={'tool_id':'different-installed-tool'}
        before=deepcopy(payload)
        result=self.s._reference_diagnostic(payload,operation)
        self.assertFalse(result['valid'])
        self.assertIn('different-installed-tool',result['message'])
        self.assertIn(operation.id,result['message'])
        self.assertEqual(payload,before)
        self.assertEqual(self.f.adapter.probe_calls,0)
