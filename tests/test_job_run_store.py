from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest

from klipper_cnc_assistant.storage.job_run_store import JobRunStore, JobRunConflict
from klipper_cnc_assistant.application import ApplicationError
from klipper_cnc_assistant.execution.print_identity import PrintIdentity, PrintIdentityError


class JobRunStoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'current_run.json'
        self.store = JobRunStore()
        self.run = self.store.create(self.path, {
            'run_id': 'A', 'state': 'JOB_READY', 'current_operation_index': 0,
            'current_operation_id': 'op', 'operations': [{'operation_id': 'op', 'remote_file': 'A.gcode'}],
        })

    def test_old_domain_writer_cannot_revive_cancelled(self):
        cancelled = self.store.cancel(self.path, 'A')
        self.run['state'] = 'OPERATION_RUNNING'
        with self.assertRaises(JobRunConflict):
            self.store.save(self.path, self.run)
        self.assertEqual(self.store.load(self.path), cancelled)

    def test_cancel_is_terminal_even_with_current_revision(self):
        cancelled = self.store.cancel(self.path, 'A')
        cancelled['state'] = 'JOB_READY'
        with self.assertRaises(JobRunConflict):
            self.store.save(self.path, cancelled)

    def test_old_observation_cannot_overwrite_cancel(self):
        cancelled = self.store.cancel(self.path, 'A')
        with self.assertRaises(JobRunConflict):
            self.store.observe(self.path, self.run, {'eta_ratio_ema': 2.0})
        self.assertEqual(self.store.load(self.path), cancelled)

    def test_replacement_rejects_old_writer_observer_remover_and_cancel(self):
        replacement = deepcopy(self.run)
        replacement['run_id'] = 'B'
        self.store.create(self.path, replacement, previous=self.run)
        before = self.path.read_bytes()
        for callback in (
            lambda: self.store.save(self.path, self.run),
            lambda: self.store.observe(self.path, self.run, {'eta_ratio_ema': 2}),
            lambda: self.store.remove(self.path, self.run),
            lambda: self.store.cancel(self.path, 'A'),
        ):
            with self.assertRaises(JobRunConflict):
                callback()
            self.assertEqual(self.path.read_bytes(), before)

    def test_two_store_instances_cannot_both_commit_same_revision(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def writer(name):
            store = JobRunStore()
            old = store.load(self.path)
            old['state'] = name
            barrier.wait(timeout=3)
            try:
                store.save(self.path, old)
                outcomes.append('saved')
            except JobRunConflict:
                outcomes.append('conflict')
        threads = [threading.Thread(target=writer, args=(name,)) for name in ('ONE', 'TWO')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ['saved', 'conflict'])

    def test_observations_cannot_change_domain_and_do_not_increment_revision(self):
        for key in ('state', 'revision', 'cancellation_epoch', 'run_id', 'recovery_state'):
            with self.assertRaises(ValueError):
                self.store.observe(self.path, self.run, {key: 'bad'})
        observed = self.store.observe(self.path, self.run, {'eta_ratio_ema': 1.4})
        self.assertEqual(observed['revision'], self.run['revision'])
        self.assertEqual(observed['state'], 'JOB_READY')
        self.run['state'] = 'JOB_STARTING'
        saved = self.store.save(self.path, self.run)
        self.assertEqual(saved['eta_ratio_ema'], 1.4)

    def test_late_start_evidence_cannot_touch_replacement(self):
        self.run['state'] = 'WAITING_FOR_KLIPPER'
        self.run = self.store.save(self.path, self.run)
        dispatched = self.store.begin_start(self.path, self.run, 'op', 'A.gcode')
        replacement = deepcopy(dispatched)
        replacement['run_id'] = 'B'
        self.store.create(self.path, replacement, previous=dispatched)
        before = self.path.read_bytes()
        self.store.finish_start(self.path, 'A', dispatched['start_attempt']['id'], status='sent')
        self.assertEqual(self.path.read_bytes(), before)


class PrintIdentityTest(unittest.TestCase):
    def test_no_basename_or_path_alias_matching(self):
        self.assertTrue(issubclass(PrintIdentityError, ApplicationError))
        identity = PrintIdentity('project/A.gcode')
        for observed in ('other/A.gcode', 'A.gcode', '/project/A.gcode',
                         'project/../project/A.gcode', 'project//A.gcode', 'project\\A.gcode', None):
            with self.subTest(observed=observed), self.assertRaises(PrintIdentityError):
                identity.require_match({'connected': True, 'klipper_state': 'ready',
                                        'state': 'paused', 'filename': observed})
