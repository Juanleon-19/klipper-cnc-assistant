import multiprocessing
from pathlib import Path
import tempfile
import unittest

from klipper_cnc_assistant.machine.process_lock import PhysicalProcessLock, PhysicalProcessLockError


def hold_lock(directory, ready, stop):
    lock = PhysicalProcessLock('test-machine', directory=Path(directory))
    lock.acquire()
    ready.set()
    try:
        stop.wait(10)
    finally:
        lock.close()


class PhysicalProcessLockTest(unittest.TestCase):
    def test_two_processes_exclude_and_controlled_exit_releases(self):
        context = multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            ready, stop = context.Event(), context.Event()
            holder = context.Process(target=hold_lock, args=(directory, ready, stop))
            holder.start()
            contender = PhysicalProcessLock('test-machine', directory=Path(directory))
            try:
                self.assertTrue(ready.wait(5))
                with self.assertRaises(PhysicalProcessLockError):
                    contender.acquire()
                inode = contender.path.stat().st_ino
                stop.set()
                holder.join(5)
                self.assertEqual(holder.exitcode, 0)
                contender.acquire()
                self.assertTrue(contender.held)
                self.assertEqual(contender.path.stat().st_ino, inode)
            finally:
                stop.set()
                holder.join(5)
                contender.close()

    def test_different_data_directories_do_not_change_machine_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            a = PhysicalProcessLock('shared-machine', directory=Path(directory))
            b = PhysicalProcessLock('shared-machine', directory=Path(directory))
            a.acquire()
            try:
                with self.assertRaises(PhysicalProcessLockError):
                    b.acquire()
            finally:
                a.close()
                b.close()
