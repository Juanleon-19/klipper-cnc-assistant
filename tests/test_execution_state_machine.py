"""Execution restart and multi-tool contracts. Temporary repositories/fake transport only."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch, Mock

from tests import test_execution_readiness
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.execution.health import preparation_retry_safe


class ExecutionStateMachineTest(unittest.TestCase):
    setUp = test_execution_readiness.ExecutionReadinessTest.setUp
    tree = test_execution_readiness.ExecutionReadinessTest.tree

    def started(self, state='OPERATION_PREFLIGHT'):
        self.job.prepare_run(**self.args)
        with patch.object(self.job, '_start_worker'):
            run = self.job.start_run(**self.args, spindle_prepared=True)
        run['state'] = state
        self.job._save_run(self.context, run)
        return run

    def test_active_preflight_has_health_not_start_blocker(self):
        self.started()
        with patch.object(self.job, '_supervisor_thread', return_value=Mock(is_alive=lambda: True)):
            live = self.job.live_execution(**self.args)
        self.assertEqual(live['active_execution_health']['title'], 'PREPARANDO')
        self.assertIsNone(live['start_readiness'])
        self.assertNotIn('EXECUTION_STATE', [r['code'] for r in live['readiness']['blocking_reasons']])

    def test_live_all_domain_states_returns_snapshot(self):
        run = self.job.prepare_run(**self.args)
        states = ['JOB_READY','JOB_STARTING','OPERATION_PREFLIGHT','OPERATION_UPLOADING',
                  'WAITING_FOR_KLIPPER','PRINT_QUEUED','OPERATION_RUNNING','SPINDLE_STOP_REQUIRED',
                  'TOOL_CHANGE_REQUIRED','MOVING_TO_REFERENCE','CALIBRATING_TOOL','READY_TO_RESUME',
                  'RECOVERY_REQUIRED','JOB_COMPLETE','JOB_ERROR']
        for state in states:
            with self.subTest(state=state):
                run = self.job.run_store.load(self.path)
                run['state'] = state
                self.job._save_run(self.context, run)
                live = self.job.live_execution(**self.args)
                self.assertEqual(live['run']['status'], state)
                self.assertIn('active_execution_health', live)
        self.assertEqual(self.fixture.adapter.started, [])

    def test_restart_before_and_after_artifact_explicit_retry(self):
        run = self.started()
        for artifact in (None, 'old-diagnostic.gcode'):
            with self.subTest(artifact=artifact):
                run = self.job.run_store.load(self.path)
                run['state'] = 'OPERATION_PREFLIGHT'
                run['operations'][0]['generated_file'] = artifact
                self.job._save_run(self.context, run)
                live = self.job.live_execution(**self.args)
                self.assertIn('retry-operation-preparation',live['run']['available_actions'])
                with patch.object(self.job, '_start_worker') as worker:
                    result = self.job.run_action(**self.args, action='retry-operation-preparation')
                worker.assert_called_once()
                self.assertEqual(result['state'], 'JOB_STARTING')
                self.assertNotIn('generated_file',result['operations'][0])
                self.assertEqual(self.fixture.adapter.started, [])

    def test_restart_upload_and_running_never_offer_retry_or_duplicate_start(self):
        run = self.started()
        for state, remote in [('OPERATION_UPLOADING',None),('WAITING_FOR_KLIPPER','remote.gcode'),
                              ('OPERATION_RUNNING','remote.gcode')]:
            with self.subTest(state=state):
                run = self.job.run_store.load(self.path)
                run['state'] = state
                run['operations'][0]['remote_file'] = remote
                self.job._save_run(self.context, run)
                live = self.job.live_execution(**self.args)
                self.assertNotIn('retry-operation-preparation',live['run']['available_actions'])
                before = self.path.read_bytes()
                with self.assertRaises(ApplicationError):
                    self.job.run_action(**self.args, action='retry-operation-preparation')
                self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(self.fixture.adapter.started, [])

    def test_new_session_requires_reference_not_jit_retry(self):
        self.started()
        self.runtime.reference_serial_generation += 1
        live = self.job.live_execution(**self.args)
        self.assertEqual(live['readiness']['reference']['code'],'REFERENCE_SESSION_CHANGED')
        self.assertIn('update-tool-reference',live['run']['available_actions'])
        before=self.path.read_bytes()
        with self.assertRaises(ApplicationError):
            self.job.run_action(**self.args,action='retry-operation-preparation')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.fixture.adapter.uploads,[])

    def test_restart_wait_states_require_current_context(self):
        self.started()
        for state in ('JOB_READY','TOOL_CHANGE_REQUIRED','READY_TO_RESUME'):
            run=self.job.run_store.load(self.path);run['state']=state
            self.job._save_run(self.context,run)
            self.runtime.reference_serial_generation += 1
            live=self.job.live_execution(**self.args)
            self.assertEqual(live['active_execution_health']['title'],'RECUPERACIÓN REQUERIDA')
            self.assertNotIn('retry-operation-preparation',live['run']['available_actions'])
            self.assertEqual(self.fixture.adapter.started,[])

    def test_spindle_confirmation_and_recovery_still_block_retry(self):
        run=self.started();run['spindle_confirmations']={};self.job._save_run(self.context,run)
        with self.assertRaises(ApplicationError):
            self.job.run_action(**self.args,action='retry-operation-preparation')
        self.runtime.physical_ownership.enter_recovery('uncertain')
        with self.assertRaises(ApplicationError):
            self.job.run_action(**self.args,action='update-tool-reference')
        self.assertEqual(self.fixture.adapter.probe_calls,0)

    def test_manual_legacy_cannot_touch_active_run(self):
        run=self.started();before=self.tree()
        with self.assertRaises(ApplicationError):
            self.job.assert_manual_compensation_allowed(self.args['project_id'],run['operations'][0]['operation_id'])
        self.assertEqual(self.tree(),before)

    def test_a_a_b_b_c_single_map_jit_and_profile_contract(self):
        self.multi_tool_flow('standard')

    def test_long_to_long_uses_same_compensation_and_clearance_contract(self):
        self.multi_tool_flow('long_tool')

    def multi_tool_flow(self, final_profile):
        f=self.fixture
        project=self.repo.load_project(f.project_id)
        ops=sorted(project.operations_for_setup(f.setup_id),key=lambda o:o.orden)
        project=project.replace_operation(replace(ops[3],tool_id=ops[2].tool_id,herramienta=ops[2].herramienta,
                                                  tool_reference_profile='long_tool'))
        self.repo.save_project(project)
        f._create_operation('Final C','corte exterior',4,'tool-c','C standard','G21\nG90\nG0 X12 Y12\nG1 X18 Y18 Z-0.120 F120\n',tool_reference_profile=final_profile)
        map_before=deepcopy(f.physical_map_service.get_active(f.project_id,ops[0].id))
        map_before['operation_ids'] = [o.id for o in self.repo.load_project(f.project_id).operations_for_setup(f.setup_id)]
        self.repo.save_height_map_payload(f.project_id,map_before['map_id'],map_before)
        run=self.job.prepare_run(**self.args)
        with patch.object(self.job,'_start_worker'), patch.object(f.compensated_service,'generate',wraps=f.compensated_service.generate) as generate:
            self.job.start_run(**self.args,spindle_prepared=True)
            self.job._run_worker(self.context)
            self.assertEqual(len(f.adapter.started),2)
            self.assertEqual(f.adapter.probe_calls,0)
            for expected in (4,5):
                self.job.run_action(**self.args,action='confirm-spindle-stopped')
                self.job._run_worker(self.context)
                self.job.run_action(**self.args,action='confirm-tool-change')
                self.job._run_worker(self.context)
                self.job.run_action(**self.args,action='continue')
                self.job._run_worker(self.context)
                self.assertEqual(len(f.adapter.started),expected)
            self.assertEqual(generate.call_count,5)
        run=self.job.get_run(**self.args)
        self.assertEqual(run['state'],'JOB_COMPLETE')
        self.assertEqual(f.adapter.probe_calls,2)
        self.assertEqual(f.adapter.tool_change_profiles,['standard','long_tool'])
        self.assertEqual(f.adapter.reference_profiles,['long_tool',final_profile])
        self.assertEqual(f.adapter.reference_moves,[(map_before['machine_origin_x'],map_before['machine_origin_y'])]*2)
        tokens=[o['physical_reference_token'] for o in run['operations']]
        self.assertEqual(tokens[0],tokens[1]);self.assertEqual(tokens[2],tokens[3])
        self.assertEqual(len({t['installation_id'] for t in tokens}),3)
        for op in run['operations']:
            meta=json.loads((self.repo.project_dir(f.project_id)/op['generated_metadata_path']).read_text())
            self.assertEqual(meta['physical_reference_token'],op['physical_reference_token'])
            self.assertEqual(meta['operation_id'],op['operation_id'])
            self.assertEqual(meta['map_id'],map_before['map_id'])
            source=self.repo.project_dir(f.project_id)/meta['original_path']
            self.assertEqual(meta['original_hash'],hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertIn('F120', (self.repo.project_dir(f.project_id)/op['generated_file']).read_text())
        after=f.physical_map_service.get_active(f.project_id,ops[0].id)
        for key in ('map_id','points','height_map','machine_origin_x','machine_origin_y'):
            self.assertEqual(after[key],map_before[key])

    def test_live_inspection_error_does_not_hide_monitor(self):
        self.started()
        with patch.object(self.job,'inspect_execution',side_effect=ApplicationError('Mapa requiere revisión')):
            live=self.job.live_execution(**self.args)
        self.assertEqual(live['run']['status'],'OPERATION_PREFLIGHT')
        self.assertEqual(live['readiness']['blocking_reasons'][0]['code'],'EXECUTION_INSPECTION_UNAVAILABLE')
        self.assertFalse(live['readiness']['can_start'])

    def test_expensive_checks_precede_fresh_frame_start_and_resume(self):
        from types import SimpleNamespace
        from tests.test_motion_authorization import observed_machine
        from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator, OwnerKind
        for action in ('start','resume'):
            owner=PhysicalMachineCoordinator();permit=owner.acquire(OwnerKind.JOB_EXECUTION,'test')
            machine=observed_machine(owner);events=[]
            def prepare():
                events.append('slow_io');machine.klippy_updated_at=0
            def refresh():
                events.append('fresh_http');machine.update_klippy('ready')
            def callback():
                events.append('final_cas')
            callback.prepare=prepare
            client=Mock();client.start_print.side_effect=lambda _:events.append('send')
            client.resume_print.side_effect=lambda:events.append('send')
            runtime=SimpleNamespace(_client=client,_refresh_machine=refresh,require_physical_access=lambda:None,
                _motion_authorizer=lambda:MotionAuthorizer(owner,machine))
            adapter=MoonrakerJobAdapter(runtime);adapter.permit=permit
            if action=='start':adapter.start_file('fake.gcode',before_send=callback)
            else:adapter.resume(before_send=callback)
            self.assertEqual(events,['slow_io','fresh_http','final_cas','send'])

    def test_source_changed_after_jit_blocks_upload(self):
        run=self.started('JOB_STARTING')
        original=self.fixture.compensated_service.generate
        def changed(*args,**kwargs):
            generated=original(*args,**kwargs)
            meta=generated['metadata']
            path=self.repo.project_dir(self.fixture.project_id)/meta['original_path']
            path.write_text(path.read_text()+'\n; source changed\n')
            return generated
        with patch.object(self.fixture.compensated_service,'generate',side_effect=changed):
            with self.assertRaises(ApplicationError):self.job._execute_next_operation(self.context,run)
        self.assertEqual(self.fixture.adapter.uploads,[])
        self.assertEqual(self.fixture.adapter.started,[])

    def test_explicit_new_session_reference_updates_only_z_not_map(self):
        run=self.started()
        op=run['operations'][0]
        before=deepcopy(self.fixture.physical_map_service.get_active(self.fixture.project_id,op['operation_id']))
        self.runtime.reference_serial_generation += 1
        result=self.job.run_action(**self.args,action='update-tool-reference')
        self.assertEqual(result['state'],'READY_TO_RESUME')
        self.assertEqual(self.fixture.adapter.probe_calls,1)
        self.assertEqual(self.fixture.adapter.reference_moves,[(before['machine_origin_x'],before['machine_origin_y'])])
        self.assertEqual(self.fixture.adapter.started,[])
        after=self.fixture.physical_map_service.get_active(self.fixture.project_id,op['operation_id'])
        for key in ('map_id','height_map','points','machine_origin_x','machine_origin_y'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(result['operations'][0]['physical_reference_token']['physical_session_id'],self.runtime.current_physical_session_id())

    def test_ready_restart_can_explicitly_measure_initial_reference(self):
        self.job.prepare_run(**self.args)
        self.runtime.reference_serial_generation += 1
        live=self.job.live_execution(**self.args)
        self.assertIn('update-tool-reference',live['run']['available_actions'])
        result=self.job.run_action(**self.args,action='update-tool-reference')
        self.assertEqual(result['state'],'JOB_READY')
        self.assertIsNone(result['started_at'])
        self.assertTrue(self.job.inspect_execution(**self.args)['readiness']['can_start'])
        self.assertEqual(self.fixture.adapter.started,[])

    def test_api_restart_recovery_and_manual_diagnostic_guards(self):
        from tests import test_api
        fixture=test_api.ApiTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.app.state.job_service=self.job
        fixture.app.state.compensated_gcode_service=self.fixture.compensated_service
        run=self.started()
        base=f'/api/projects/{self.fixture.project_id}'
        url=base+'/operations/'+run['operations'][0]['operation_id']+'/compensated-gcode/generate?mode=legacy&diagnostic=true'
        before=self.tree()
        self.assertEqual(fixture.client.post(url).status_code,400)
        self.assertEqual(self.tree(),before)
        live=fixture.client.get(base+'/execution/live',params={'setup_id':self.fixture.setup_id,'face':'superior'})
        self.assertEqual(live.status_code,200,live.text)
        self.assertEqual(live.json()['active_execution_health']['title'],'RECUPERACIÓN REQUERIDA')
        with patch.object(self.job,'_start_worker'):
            response=fixture.client.post(base+'/job-run/action',json={'setup_id':self.fixture.setup_id,'face':'superior','action':'retry-operation-preparation'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.fixture.adapter.started,[])
        run=self.job.run_store.load(self.path);run.update(state='JOB_CANCELLED')
        self.job._save_run(self.context,run)
        before=self.path.read_bytes()
        response=fixture.client.post(url)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['executable'])
        self.assertEqual(before,self.path.read_bytes())

    def test_changed_runtime_config_after_preparation_cannot_dispatch(self):
        run=self.started('JOB_STARTING')
        def racing_start(remote, *, before_send):
            before_send.prepare()
            self.runtime.config.reference_prep_z_mm += 1
            before_send()
            self.fail('Must never send')
        with patch.object(self.fixture.adapter,'start_file',side_effect=racing_start):
            with self.assertRaises(ApplicationError):self.job._execute_next_operation(self.context,run)
        self.assertEqual(self.fixture.adapter.started,[])

    def test_changed_project_after_preparation_cannot_dispatch(self):
        run=self.started('JOB_STARTING')
        def racing_start(remote, *, before_send):
            before_send.prepare()
            path=self.repo.project_dir(self.fixture.project_id)/'project.json'
            path.write_text(path.read_text()+'\n')
            before_send()
            self.fail('Must never send')
        with patch.object(self.fixture.adapter,'start_file',side_effect=racing_start):
            with self.assertRaises(ApplicationError):self.job._execute_next_operation(self.context,run)
        self.assertEqual(self.fixture.adapter.started,[])
