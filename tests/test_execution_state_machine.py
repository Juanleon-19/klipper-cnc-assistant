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
            self.assertEqual(len(f.adapter.started),1)
            waiting=self.job.get_run(**self.args)
            self.assertEqual(waiting['state'],'READY_TO_RESUME')
            self.assertIsNone(waiting['operations'][1].get('generated_file'))
            self.job._run_worker(self.context)
            self.assertEqual(len(f.adapter.started),1)
            self.job.run_action(**self.args,action='continue')
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
                if expected == 4:
                    self.assertEqual(len(f.adapter.started),3)
                    self.assertEqual(self.job.get_run(**self.args)['state'],'READY_TO_RESUME')
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

    def test_uncertain_transition_reports_motion_cause_not_pending_reference(self):
        from klipper_cnc_assistant.execution.health import execution_health
        run = {'state':'RECOVERY_REQUIRED', 'operations':[], 'available_actions':['retry-tool-change-transition','cancel'],
               'last_transition_error':{'code':'TOOL_CHANGE_TRANSITION_FAILED','message':'Llegada física no confirmada'}}
        readiness={'reference':{'valid':False,'code':'REFERENCE_MEASUREMENT_REQUIRED','message':'Falta nueva Z'},
                   'blocking_reasons':[{'category':'runtime','code':'PHYSICAL_RECOVERY_REQUIRED'}]}
        health=execution_health(run,{'connected':True,'is_active':False,'print_state':'complete'},readiness,False)
        self.assertEqual(health['recovery']['code'],'TOOL_CHANGE_TRANSITION_FAILED')
        self.assertEqual(health['detail'],'Llegada física no confirmada')
        self.assertEqual(health['available_actions'],['cancel'])

    def test_five_distinct_tools_require_four_explicit_continuations(self):
        f = self.fixture
        project = self.repo.load_project(f.project_id)
        ops = sorted(project.operations_for_setup(f.setup_id), key=lambda o:o.orden)
        for index, op in enumerate(ops[1:], 1):
            project = project.replace_operation(replace(op, tool_id=f"unique-{index}",
                tool_reference_profile='long_tool' if index % 2 else 'standard'))
        self.repo.save_project(project)
        f._create_operation('Final fifth', 'corte exterior', 4, 'unique-4', 'Fifth',
            'G21\nG90\nG0 X12 Y12\nG1 X18 Y18 Z-0.120 F120\n')
        active = f.physical_map_service.get_active(f.project_id, ops[0].id)
        active['operation_ids'] = [o.id for o in self.repo.load_project(f.project_id).operations_for_setup(f.setup_id)]
        self.repo.save_height_map_payload(f.project_id, active['map_id'], active)
        self.job.prepare_run(**self.args)
        with patch.object(self.job, '_start_worker'), patch.object(f.compensated_service, 'generate', wraps=f.compensated_service.generate) as generate:
            self.job.start_run(**self.args, spindle_prepared=True)
            self.job._run_worker(self.context)
            for completed in range(1, 5):
                self.assertEqual(len(f.adapter.started), completed)
                self.assertEqual(self.job.get_run(**self.args)['state'], 'SPINDLE_STOP_REQUIRED')
                self.job.run_action(**self.args, action='confirm-spindle-stopped')
                self.job._run_worker(self.context)
                live = self.job.live_execution(**self.args)
                self.assertEqual(live['run']['status'], 'TOOL_CHANGE_REQUIRED')
                self.assertEqual(live['active_execution_health']['title'], 'ESPERANDO OPERADOR')
                self.assertEqual(len(f.adapter.started), completed)
                self.job.run_action(**self.args, action='confirm-tool-change')
                self.job._run_worker(self.context)
                self.assertEqual(self.job.get_run(**self.args)['state'], 'READY_TO_RESUME')
                self.job._run_worker(self.context)  # A re-observation/reload cannot start.
                self.assertEqual(generate.call_count, completed)
                self.assertEqual(len(f.adapter.started), completed)
                self.job.run_action(**self.args, action='continue')
                self.job._run_worker(self.context)
            self.assertEqual(generate.call_count, 5)
        run = self.job.get_run(**self.args)
        self.assertEqual(run['state'], 'JOB_COMPLETE')
        self.assertEqual(f.adapter.probe_calls, 4)
        self.assertEqual(len(set(f.adapter.started)), 5)
        self.assertEqual(len({o['physical_reference_token']['installation_id'] for o in run['operations']}), 5)
        self.assertEqual(f.adapter.reference_moves, [(active['machine_origin_x'], active['machine_origin_y'])] * 4)

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
            before_send.verify()
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
            before_send.verify()
            before_send()
            self.fail('Must never send')
        with patch.object(self.fixture.adapter,'start_file',side_effect=racing_start):
            with self.assertRaises(ApplicationError):self.job._execute_next_operation(self.context,run)
        self.assertEqual(self.fixture.adapter.started,[])

    def test_real_callbacks_have_no_io_after_context_start_and_resume(self):
        from contextlib import ExitStack
        from pathlib import Path
        from types import SimpleNamespace
        import time
        from tests.test_motion_authorization import observed_machine
        from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        run = self.started('JOB_STARTING')
        adapter = self.fixture.adapter
        owner = self.runtime.physical_ownership
        events = []
        clock = [time.monotonic()]
        original_digest = hashlib.file_digest
        def slow_digest(*args, **kwargs):
            clock[0] += 3.0  # The old post-context hash would expire a 2s frame.
            events.append('digest')
            return original_digest(*args, **kwargs)
        def dispatch(action, callback):
            machine = observed_machine(owner)
            guards = ExitStack()
            authorizer = MotionAuthorizer(owner, machine)
            require = authorizer.require_context
            def capture(*args, **kwargs):
                context = require(*args, **kwargs)
                events.append('context')
                for target in ('read_bytes', 'read_text', 'open'):
                    guards.enter_context(patch.object(Path, target, side_effect=AssertionError('file IO after context')))
                guards.enter_context(patch('hashlib.file_digest', side_effect=AssertionError('hash after context')))
                guards.enter_context(patch('json.loads', side_effect=AssertionError('JSON after context')))
                guards.enter_context(patch.object(adapter, 'print_status', side_effect=AssertionError('HTTP after context')))
                guards.enter_context(patch('klipper_cnc_assistant.execution.job_service.reference_context', side_effect=AssertionError('reference context rebuilt')))
                return context
            def refresh():
                events.append('refresh')
                machine.update_motion((10, 10, 10), 0, 'fake')
                machine.update_klippy('ready')
            def send():
                events.append('send')
                guards.close()
            runtime = SimpleNamespace(_refresh_machine=refresh, _motion_authorizer=lambda: authorizer)
            real = MoonrakerJobAdapter(runtime)
            real.permit = adapter.permit
            try:
                with patch.object(authorizer, 'require_context', side_effect=capture):
                    real._dispatch(action, send, before_send=callback)
            finally:
                guards.close()
        def start(remote, *, before_send):
            dispatch('job_start', before_send)
            adapter.current_filename = remote
            adapter.state = 'paused'
        def resume(*, before_send):
            dispatch('job_resume', before_send)
        with patch('time.monotonic', side_effect=lambda: clock[0]), patch('hashlib.file_digest', side_effect=slow_digest):
            with patch.object(adapter, 'start_file', side_effect=start):
                self.job._execute_next_operation(self.context, run)
            run = self.job.run_store.load(self.path)
            with patch.object(adapter, 'resume', side_effect=resume):
                self.assertTrue(self.job._existing_print_action(self.context, run, adapter, 'resume', {'paused'}))
        self.assertEqual(events.count('send'), 2)
        for index, event in enumerate(events):
            if event == 'context':
                self.assertEqual(events[index-1:index+2], ['refresh', 'context', 'send'])

    def test_post_context_concurrent_changes_block_send(self):
        from klipper_cnc_assistant.storage.safe_persistence import atomic_write
        # Exercise the actual start callback; mutation is injected after all IO
        # phases, at the same boundary as a freshly captured MotionContext.
        for change in ('revision', 'cancel', 'session', 'ownership', 'project'):
            with self.subTest(change=change):
                fixture = ExecutionStateMachineTest()
                fixture.setUp()
                try:
                    run = fixture.started('JOB_STARTING')
                    mutations = []
                    def mutate():
                        mutations.append(change)
                        if change == 'revision':
                            latest = fixture.job.run_store.load(fixture.path)
                            fixture.job.run_store.save(fixture.path, latest)
                        elif change == 'cancel':
                            fixture.job.run_store.cancel(fixture.path, run['run_id'])
                        elif change == 'session':
                            fixture.runtime.reference_serial_generation += 1
                        elif change == 'ownership':
                            fixture.runtime.physical_ownership.enter_recovery('concurrent change')
                        else:
                            path = fixture.repo.project_dir(fixture.fixture.project_id) / 'project.json'
                            atomic_write(path, path.read_bytes())  # Even identical-byte rewrite invalidates generation.
                    def start(remote, *, before_send):
                        from types import SimpleNamespace
                        from tests.test_motion_authorization import observed_machine
                        from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
                        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
                        owner = fixture.runtime.physical_ownership
                        machine = observed_machine(owner)
                        authorizer = MotionAuthorizer(owner, machine)
                        require = authorizer.require_context
                        def capture(*args, **kwargs):
                            context = require(*args, **kwargs)
                            mutate()
                            return context
                        def refresh():
                            machine.update_motion((10, 10, 10), 0, 'fake')
                            machine.update_klippy('ready')
                        runtime = SimpleNamespace(_refresh_machine=refresh,
                                                  _motion_authorizer=lambda: authorizer)
                        real = MoonrakerJobAdapter(runtime)
                        real.permit = fixture.fixture.adapter.permit
                        with patch.object(authorizer, 'require_context', side_effect=capture):
                            real._dispatch('job_start', lambda: self.fail('send must not occur'), before_send=before_send)
                    with patch.object(fixture.fixture.adapter, 'start_file', side_effect=start):
                        with self.assertRaises(Exception) as error:
                            fixture.job._execute_next_operation(fixture.context, run)
                        self.assertNotIsInstance(error.exception, AssertionError)
                    self.assertEqual(mutations, [change])
                    self.assertEqual(fixture.fixture.adapter.started, [])
                finally:
                    fixture.doCleanups()

    def test_mutation_guard_rejects_writer_already_in_progress(self):
        from klipper_cnc_assistant.storage.safe_persistence import FileRevisionGuard, dispatch_mutation, PersistenceConflict
        with dispatch_mutation(self.path):
            guard = FileRevisionGuard([self.path])
            with self.assertRaises(PersistenceConflict):
                guard.validate()
        with self.assertRaises(PersistenceConflict):
            guard.validate()

    def exercise_start_outcome(self, failure):
        from types import SimpleNamespace
        from tests.test_motion_authorization import observed_machine
        from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        fixture = ExecutionStateMachineTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        run = fixture.started('JOB_STARTING')
        owner = fixture.runtime.physical_ownership
        machine = observed_machine(owner)
        calls = []
        def start_print(remote):
            persisted = fixture.job.run_store.load(fixture.path)
            self.assertEqual(persisted['start_attempt']['status'], 'sending')
            self.assertIs(persisted['start_attempt']['may_have_been_sent'], True)
            calls.append(remote)
            if failure == 'transport':
                raise OSError('fake transport failed after entry')
            return {'ok': True}
        client = Mock(start_print=start_print)
        def refresh():
            self.assertIs(fixture.job.run_store.load(fixture.path)['start_attempt']['may_have_been_sent'], True)
            if failure == 'refresh':
                raise RuntimeError('fake observation failed')
            machine.update_motion((10, 10, 10), 0, 'fake')
            machine.update_klippy('ready')
        authorizer = MotionAuthorizer(owner, machine)
        require = authorizer.require_context
        def capture(*args, **kwargs):
            if failure == 'context':
                raise RuntimeError('fake context failed')
            context = require(*args, **kwargs)
            if failure == 'final':
                fixture.runtime.config.reference_prep_z_mm += 1
            return context
        runtime = SimpleNamespace(_client=client, require_physical_access=lambda: None,
                                  _refresh_machine=refresh, _motion_authorizer=lambda: authorizer)
        real = MoonrakerJobAdapter(runtime)
        if failure == 'after_transport':
            end = owner.end_dispatch
            def failed_cleanup(permit):
                end(permit)
                raise OSError('fake post-transport failure')
            owner.end_dispatch = failed_cleanup
        def start(remote, *, before_send):
            real.permit = fixture.fixture.adapter.permit
            return real.start_file(remote, before_send=before_send)
        with patch.object(authorizer, 'require_context', side_effect=capture), patch.object(fixture.fixture.adapter, 'start_file', side_effect=start):
            if failure:
                with self.assertRaises(Exception) as error:
                    fixture.job._execute_next_operation(fixture.context, run)
                self.assertNotIsInstance(error.exception, AssertionError)
            else:
                fixture.job._execute_next_operation(fixture.context, run)
        persisted = fixture.job.run_store.load(fixture.path)
        expected = 'uncertain' if failure in {'transport', 'after_transport'} else 'not_sent' if failure else 'sent'
        self.assertEqual(persisted['start_attempt']['status'], expected)
        self.assertEqual(persisted['start_attempt']['may_have_been_sent'], expected != 'not_sent')
        self.assertEqual(len(calls), int(failure in {None, 'transport', 'after_transport'}))
        status = {'connected': True, 'print_state': 'standby', 'is_active': False, 'filename': None}
        self.assertEqual(preparation_retry_safe(persisted, status), expected == 'not_sent')
        return fixture, persisted

    def test_start_refresh_failure_is_not_sent(self):
        fixture, run = self.exercise_start_outcome('refresh')
        live = fixture.job.live_execution(**fixture.args)
        self.assertIn('retry-operation-preparation', live['run']['available_actions'])

    def test_start_context_and_final_failures_are_not_sent(self):
        for stage in ('context', 'final'):
            with self.subTest(stage=stage):
                self.exercise_start_outcome(stage)

    def test_start_transport_exception_is_uncertain_and_journal_precedes_call(self):
        for failure in ('transport', 'after_transport'):
            with self.subTest(failure=failure):
                fixture, run = self.exercise_start_outcome(failure)
                self.assertNotIn('retry-operation-preparation', fixture.job.live_execution(**fixture.args)['run']['available_actions'])

    def test_start_success_is_sent_and_journal_precedes_call(self):
        self.exercise_start_outcome(None)

    def test_not_sent_explicit_retry_regenerates_and_preserves_attempt(self):
        fixture, run = self.exercise_start_outcome('refresh')
        old_attempt = deepcopy(run['start_attempt'])
        run.update(state='JOB_ERROR', completed_at='previous-failure', last_watcher_error='previous-error')
        fixture.job._save_run(fixture.context, run)
        self.assertIn('retry-operation-preparation', fixture.job.live_execution(**fixture.args)['run']['available_actions'])
        uploads = len(fixture.fixture.adapter.uploads)
        with patch.object(fixture.job, '_start_worker') as worker:
            retried = fixture.job.run_action(**fixture.args, action='retry-operation-preparation')
        worker.assert_called_once()
        self.assertEqual(retried['start_attempt'], old_attempt)
        self.assertIsNone(retried['completed_at'])
        self.assertIsNone(retried['last_watcher_error'])
        self.assertNotIn('remote_file', retried['operations'][0])
        self.assertNotIn('generated_file', retried['operations'][0])
        self.assertEqual(fixture.fixture.adapter.started, [])
        with patch.object(fixture.fixture.compensated_service, 'generate', wraps=fixture.fixture.compensated_service.generate) as generate:
            fixture.job._execute_next_operation(fixture.context, retried)
        generate.assert_called_once()
        self.assertEqual(len(fixture.fixture.adapter.uploads), uploads + 1)
        self.assertEqual(len(fixture.fixture.adapter.started), 1)
        final = fixture.job.run_store.load(fixture.path)
        self.assertIn(old_attempt, final['start_attempt_history'])
        self.assertNotEqual(final['start_attempt']['id'], old_attempt['id'])

    def test_retry_rejects_ambiguous_journal_and_non_idle_even_if_not_sent(self):
        fixture, run = self.exercise_start_outcome('refresh')
        status = {'connected': True, 'print_state': 'standby', 'is_active': False}
        for attempt_status in ('dispatching', 'sending', 'sent', 'uncertain'):
            changed = deepcopy(run)
            changed['start_attempt']['status'] = attempt_status
            self.assertFalse(preparation_retry_safe(changed, status))
        for delta in ({'is_active': True}, {'print_state': 'printing'}, {'connected': False}, {'filename': 'other.gcode'}):
            self.assertFalse(preparation_retry_safe(run, status | delta))


    def test_sending_journal_transition_rejects_cancelled_revision(self):
        from klipper_cnc_assistant.storage.job_run_store import JobRunConflict
        run = self.started('WAITING_FOR_KLIPPER')
        operation = run['operations'][0]
        operation['remote_file'] = 'fake.gcode'
        run['current_operation_id'] = operation['operation_id']
        self.job._save_run(self.context, run)
        intent = self.job.run_store.begin_start(self.path, run, operation['operation_id'], 'fake.gcode')
        self.assertFalse(intent['start_attempt']['may_have_been_sent'])
        self.assertEqual(intent['start_attempt']['status'], 'dispatching')
        cancelled = self.job.run_store.cancel(self.path, run['run_id'])
        with self.assertRaises(JobRunConflict):
            self.job.run_store.mark_start_sending(self.path, intent, intent['start_attempt']['id'])
        self.assertEqual(self.job.run_store.load(self.path), cancelled)
