from __future__ import annotations

import time
import math
from klipper_cnc_assistant.machine.motion_authorization import MotionRequirements, MotionAuthorizationError


class JogError(Exception):
    pass


class JogController:
    AXES = {
        "x": 0,
        "y": 1,
        "z": 2,
    }

    def __init__(
        self,
        moonraker_client,
        machine_state,
        *, authorizer=None, before_send=None,
    ):
        self.client = moonraker_client
        self.machine = machine_state
        self.authorizer = authorizer
        self.before_send = before_send

        self._continuous_x = 0
        self._continuous_y = 0
        self._continuous_speed = 0.0
        self._continuous_step = 0.2
        self._last_continuous_update = 0.0

    def _get_axis_limits(
        self,
        axis,
    ):
        axis = axis.lower()

        if axis == "x":
            return self.machine.x_limits

        if axis == "y":
            return self.machine.y_limits

        if axis == "z":
            return self.machine.z_limits

        raise JogError(
            f"Unsupported axis: {axis}"
        )

    def _get_axis_position(
        self,
        axis,
    ):
        state = (
            self.machine.get_motion_snapshot()
        )

        axis = axis.lower()

        if axis not in self.AXES:
            raise JogError(
                f"Unsupported axis: {axis}"
            )

        return state[axis]

    def calculate_target(
        self,
        axis,
        distance,
        *, motion_context=None,
    ):
        axis = axis.lower()

        if motion_context is None:
            raise JogError("Cálculo de jog sin contexto físico.")
        current_position = motion_context.frame.position[self.AXES[axis]]

        minimum, maximum = motion_context.frame.limits[self.AXES[axis]]

        requested_target = (
            current_position + distance
        )

        target = max(
            minimum,
            min(
                requested_target,
                maximum,
            ),
        )

        return (
            current_position,
            requested_target,
            target,
        )

    def move_relative(
        self,
        axis,
        distance,
        speed,
        *, permit=None, motion_context=None,
    ):
        axis = axis.lower()

        if axis not in self.AXES:
            raise JogError(
                f"Unsupported axis: {axis}"
            )

        if self.authorizer is None or not all(math.isfinite(v) for v in (distance, speed)):
            raise JogError('Jog sin autorizador o parámetros no finitos.')
        context = motion_context or self.authorizer.require_context(permit, 'jog', MotionRequirements(homed_axes=axis))
        if context.permit != permit or context.requirements.frame != 'live_position':
            raise MotionAuthorizationError('El permiso/frame de jog no coincide.')
        self.authorizer.revalidate(context)

        if distance == 0:
            raise JogError(
                "Jog distance cannot be zero"
            )

        if not self.machine.axis_is_homed(axis):
            raise JogError(
                f"Axis {axis.upper()} must be homed "
                "before jogging"
            )

        if speed <= 0:
            raise JogError(
                "Jog speed must be positive"
            )

        if not math.isfinite(context.frame.max_velocity) or speed > context.frame.max_velocity:
            raise JogError(
                "Requested jog speed exceeds "
                "the machine maximum velocity"
            )

        if not math.isfinite(context.frame.max_accel) or context.frame.max_accel <= 0:
            raise JogError(
                "Machine maximum acceleration must be positive"
            )

        (
            current_position,
            requested_target,
            target,
        ) = self.calculate_target(
            axis,
            distance,
            motion_context=context,
        )

        effective_distance = (
            target - current_position
        )

        if abs(effective_distance) < 1e-9:
            raise JogError(
                f"Axis {axis.upper()} is already "
                "at the configured machine limit"
            )

        feedrate = speed * 60.0

        script = (
            "SAVE_GCODE_STATE "
            "NAME=cnc_assistant_jog\n"
            "G91\n"
            f"G1 {axis.upper()}"
            f"{effective_distance:.6f} "
            f"F{feedrate:.3f}\n"
            "RESTORE_GCODE_STATE "
            "NAME=cnc_assistant_jog"
        )

        def send():
            if self.before_send is not None:
                self.before_send()
            return self.client.send_gcode(script)

        self.authorizer.dispatch(context, send)

        return {
            "axis": axis,
            "current_position": current_position,
            "requested_target": requested_target,
            "target": target,
            "effective_distance": effective_distance,
            "speed": speed,
            "limit_applied": (
                abs(
                    requested_target - target
                ) > 1e-9
            ),
        }

    def set_continuous_state(
        self,
        x_dir,
        y_dir,
        speed,
        step=0.2,
    ):
        self._continuous_x = int(x_dir)
        self._continuous_y = int(y_dir)
        self._continuous_speed = float(speed)
        self._continuous_step = float(step)

    def stop_continuous(
        self,
    ):
        self._continuous_x = 0
        self._continuous_y = 0
        self._continuous_speed = 0.0
        self._continuous_step = 0.2

    def has_continuous_motion(
        self,
    ):
        return (
            self._continuous_x != 0
            or self._continuous_y != 0
        )

    def update_continuous(
        self,
    ):
        if not self.has_continuous_motion():
            return False

        now = time.time()

        if now - self._last_continuous_update < 0.05:
            return False

        self._last_continuous_update = now

        moved = False

        if self._continuous_x != 0:
            self.move_relative(
                axis="x",
                distance=self._continuous_x * self._continuous_step,
                speed=self._continuous_speed,
            )
            moved = True

        if self._continuous_y != 0:
            self.move_relative(
                axis="y",
                distance=self._continuous_y * self._continuous_step,
                speed=self._continuous_speed,
            )
            moved = True

        return moved
