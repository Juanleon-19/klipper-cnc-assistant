"""Authorize the exact immutable frame used for calculation and dispatch."""
from dataclasses import dataclass
import math
import time

from .physical_ownership import PhysicalPermit


class MotionAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class MotionRequirements:
    frame: str | None = 'live_position'
    homed_axes: str = 'xyz'
    max_age_s: float = 2.0


@dataclass(frozen=True)
class MotionContext:
    permit: PhysicalPermit
    action: str
    requirements: MotionRequirements
    frame: object
    dependencies: tuple = ()


class MotionAuthorizer:
    def __init__(self, coordinator, machine, *, require_access=lambda: None):
        self.coordinator = coordinator
        self.machine = machine
        self.require_access = require_access

    def require_context(self, permit, action, requirements=MotionRequirements()):
        self.require_access()
        self.coordinator.validate(permit)
        frame = self.machine.authorization_snapshot(requirements.frame)
        context = MotionContext(permit, action, requirements, frame)
        self.revalidate(context)
        return context

    def revalidate(self, context):
        for dependency in context.dependencies:
            self.revalidate(dependency)
        self.require_access()
        self.coordinator.validate(context.permit)
        snapshot = context.frame
        required = context.requirements
        current = self.machine.authorization_snapshot(required.frame)
        if snapshot.session != current.session or snapshot.session != context.permit.session:
            raise MotionAuthorizationError('El frame pertenece a otra sesión física.')
        if not set(required.homed_axes).issubset(snapshot.homed_axes) or not set(required.homed_axes).issubset(current.homed_axes):
            raise MotionAuthorizationError('Falta homing antes de emitir movimiento.')
        now = time.monotonic()
        for state in (snapshot, current):
            if not all(math.isfinite(v) for bounds in state.limits for v in bounds) or any(lo > hi for lo, hi in state.limits):
                raise MotionAuthorizationError("Límites físicos no finitos o inválidos.")
            if state.klippy_state != 'ready' or state.klippy_updated_at is None or not math.isfinite(state.klippy_updated_at) or not 0 <= now - state.klippy_updated_at <= required.max_age_s:
                raise MotionAuthorizationError('Klippy state ausente, stale o no ready.')
        if required.frame is not None:
            if snapshot.position is None or snapshot.timestamp is None:
                raise MotionAuthorizationError('Frame requerido o timestamp inexistente.')
            if not all(math.isfinite(v) for v in (*snapshot.position, snapshot.timestamp)) or not 0 <= now - snapshot.timestamp <= required.max_age_s:
                raise MotionAuthorizationError('Frame requerido stale o no finito.')
            if current.position is None or current.timestamp is None or not all(math.isfinite(v) for v in (*current.position, current.timestamp)) or not 0 <= now - current.timestamp <= required.max_age_s:
                raise MotionAuthorizationError("El frame actual requerido es inválido o stale.")
            if current.position != snapshot.position or current.limits != snapshot.limits:
                raise MotionAuthorizationError('El frame cambió después del cálculo; recalcule antes de emitir.')
        return context

    def dispatch(self, context, send):
        self.revalidate(context)
        self.coordinator.begin_dispatch(context.permit)
        try:
            self.revalidate(context)
            return send()
        except BaseException:
            self.coordinator.dispatch_failed(context.permit)
            raise
        finally:
            self.coordinator.end_dispatch(context.permit)
