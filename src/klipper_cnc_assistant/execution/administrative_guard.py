"""Non-motion reservation and durable failure evidence for administrative writes.

The coordinator mutex never spans HTTP, filesystem IO or callbacks. The token is
not a PhysicalPermit and cannot authorize motion. A pending marker is deliberately
not auto-replayed or cleared on restart.
"""
from contextlib import contextmanager
from functools import wraps
from uuid import uuid4
import time
import logging
from .administrative_recovery import inventory, transaction_dir

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
from klipper_cnc_assistant.storage.safe_persistence import atomic_json


class AdministrativeGuard:
    def __init__(self, service, token, session, action, remote, observed_at):
        self.service, self.token, self.session, self.action = service, token, session, action
        self.dirty = False
        self.disconnected = False
        self.remote = remote
        self.observed_at = observed_at
        self._validate_remote_identity = None
        self.journal = None

    def check(self):
        runtime = self.service.runtime
        try:
            runtime.physical_ownership.validate_administration(self.token, disconnected=self.disconnected)
        except OwnershipError as error:
            raise ApplicationError(str(error)) from error
        if runtime.current_physical_session_id() != self.session:
            raise ApplicationError('La sesión física cambió durante la administración.')
        if self.service._movement_lock_state() is not False or getattr(runtime, '_active_operation', None) is not None:
            raise ApplicationError('No se puede verificar movement_lock libre durante administración.')

    def observe_before_commit(self):
        """Acquire evidence at mutation boundaries, never rejuvenate a cache.

        The reservation excludes local producers throughout IO. No bookkeeping
        mutex spans HTTP. After the authorized disconnect, finish only local
        persistence under the reservation; never reconnect hardware for cleanup.
        """
        self.check()
        if not self.disconnected:
            self.remote = self.service._observe_administrative_remote()
            self.observed_at = time.monotonic()
            self.check()
            if self._validate_remote_identity is not None:
                self._validate_remote_identity()
        self.check()

    def begin_mutation(self, *, validate_remote_identity=None, **evidence):
        self._validate_remote_identity = validate_remote_identity
        self.observe_before_commit()
        self.check()
        if not self.dirty:
            before = inventory(self.service.administrative_pending_path.parent)
            self.check()
            self.journal = {'schema_version': 2, 'id': uuid4().hex, 'action': self.action,
                            'status': 'PENDING', 'phase': 'PREPARED', 'evidence': evidence,
                            'session': self.session, 'before': before, 'completed_steps': []}
            atomic_json(self.service.administrative_pending_path, self.journal, durable=True)
            self.dirty = True
        self.observe_before_commit()


    def checkpoint(self, phase):
        if not self.dirty:
            return
        self.check()
        after = inventory(self.service.administrative_pending_path.parent)
        self.check()
        self.journal.update(phase=phase, verified_after=after)
        if not phase.startswith('FAILED'):
            self.journal['completed_steps'].append(phase)
        before = self.journal['before']
        self.journal['file_changes'] = {
            'added': sorted(set(after) - set(before)),
            'removed': sorted(set(before) - set(after)),
            'modified': sorted(key for key in before.keys() & after.keys() if before[key] != after[key]),
        }
        atomic_json(self.service.administrative_pending_path, self.journal, durable=True)


@contextmanager
def administrative_mutation(service, action, *, allow_started_runs=False):
    service.assert_administration_available()
    runtime = service.runtime
    coordinator = runtime.physical_ownership
    owner = coordinator.snapshot()
    session = runtime.current_physical_session_id()
    remote = service._assert_preparation_reset_safe()
    observed_at = time.monotonic()
    if not allow_started_runs:
        service._assert_no_started_runs()
    try:
        token = coordinator.begin_administration(expected_revision=owner['revision'])
    except OwnershipError as error:
        raise ApplicationError(str(error)) from error
    guard = AdministrativeGuard(service, token, session, action, remote, observed_at)
    try:
        guard.check()
        if not allow_started_runs:
            service._assert_no_started_runs()
        yield guard
        guard.check()
        if guard.dirty:
            guard.checkpoint('COMPLETED')
            directory = transaction_dir(service, guard.journal)
            directory.mkdir(parents=True, exist_ok=True)
            atomic_json(directory / 'completed.json', guard.journal, durable=True)
            service.administrative_pending_path.unlink()
    except BaseException as error:
        if guard.dirty:
            try:
                guard.checkpoint('FAILED_WITH_VERIFIED_FILES')
                guard.journal['failure'] = {'type': type(error).__name__,
                    'message': str(error)[:500] if isinstance(error, ApplicationError) else (getattr(error, 'strerror', None) or 'Fallo de persistencia; revisar registro del servicio.')}
                atomic_json(service.administrative_pending_path, guard.journal, durable=True)
            except BaseException:
                logging.getLogger(__name__).exception('No se pudo persistir checkpoint administrativo; se conserva evidencia anterior.')
            coordinator.administration_failed('Mutación administrativa incompleta; conservar histórico y revisar journal pendiente.')
        raise
    finally:
        coordinator.end_administration(token)


def execution_write(function):
    """Logical exclusion only; no coordinator mutex spans execution work."""
    @wraps(function)
    def wrapped(service, *args, **kwargs):
        coordinator = service.runtime.physical_ownership
        try:
            token = coordinator.begin_job_update(project_id=kwargs.get("project_id"))
        except OwnershipError as error:
            raise ApplicationError(str(error)) from error
        try:
            return function(service, *args, **kwargs)
        finally:
            coordinator.end_job_update(token)
    return wrapped


@contextmanager
def project_mutation(service, project_id, *, read_only_fallback=False):
    """Offline project IO, excluded from execution of the same project.

    No HTTP, movement permit, recovery clearing or physical evidence mutation.
    Fallback catches authorization rejection only, never mutation failures.
    """
    coordinator = service.runtime.physical_ownership
    token = None
    try:
        if service.administrative_pending_path.exists():
            raise ApplicationError('Journal administrativo pendiente; escritura bloqueada.')
        token = coordinator.begin_project_edit(project_id)
        service.assert_project_edit_safe(project_id)
    except (OwnershipError, ApplicationError) as error:
        if token is not None:
            coordinator.end_project_edit(token)
            token = None
        if read_only_fallback:
            yield False
            return
        raise ApplicationError(str(error)) from error
    except BaseException:
        if token is not None:
            coordinator.end_project_edit(token)
        raise
    try:
        yield True
    finally:
        coordinator.end_project_edit(token)
