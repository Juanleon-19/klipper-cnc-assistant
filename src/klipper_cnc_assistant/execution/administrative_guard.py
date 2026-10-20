"""Non-motion reservation and durable failure evidence for administrative writes.

The coordinator mutex never spans HTTP, filesystem IO or callbacks. The token is
not a PhysicalPermit and cannot authorize motion. A pending marker is deliberately
not auto-replayed or cleared on restart.
"""
from contextlib import contextmanager
from functools import wraps
from uuid import uuid4
import time

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

    def check(self):
        runtime = self.service.runtime
        if self.remote is not None and time.monotonic() - self.observed_at > float(getattr(runtime.config, 'telemetry_fresh_timeout_s', 2.0)):
            raise ApplicationError('La observación administrativa expiró; no se continúa con evidencia antigua.')
        try:
            runtime.physical_ownership.validate_administration(self.token, disconnected=self.disconnected)
        except OwnershipError as error:
            raise ApplicationError(str(error)) from error
        if runtime.current_physical_session_id() != self.session:
            raise ApplicationError('La sesión física cambió durante la administración.')
        if self.service._movement_lock_state() is not False or getattr(runtime, '_active_operation', None) is not None:
            raise ApplicationError('No se puede verificar movement_lock libre durante administración.')

    def begin_mutation(self, **evidence):
        self.check()
        if not self.dirty:
            atomic_json(self.service.administrative_pending_path, {
                'id': uuid4().hex, 'action': self.action,
                'status': 'PENDING', 'evidence': evidence,
            }, durable=True)
            self.dirty = True
        self.check()


@contextmanager
def administrative_mutation(service, action, *, allow_started_runs=False):
    service.assert_administration_available()
    runtime = service.runtime
    coordinator = runtime.physical_ownership
    owner = coordinator.snapshot()
    session = runtime.current_physical_session_id()
    observed_at = time.monotonic()
    remote = service._assert_preparation_reset_safe()
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
            service.administrative_pending_path.unlink()
    except BaseException:
        if guard.dirty:
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
