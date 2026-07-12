from __future__ import annotations

import time


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
    ):
        self.client = moonraker_client
        self.machine = machine_state

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
    ):
        axis = axis.lower()

        current_position = (
            self._get_axis_position(axis)
        )

        limits = self._get_axis_limits(axis)

        requested_target = (
            current_position + distance
        )

        target = max(
            limits.minimum,
            min(
                requested_target,
                limits.maximum,
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
    ):
        axis = axis.lower()

        if axis not in self.AXES:
            raise JogError(
                f"Unsupported axis: {axis}"
            )

        if distance == 0:
            raise JogError(
                "Jog distance cannot be zero"
            )

        if speed <= 0:
            raise JogError(
                "Jog speed must be positive"
            )

        if speed > self.machine.max_velocity:
            raise JogError(
                "Requested jog speed exceeds "
                "the machine maximum velocity"
            )

        (
            current_position,
            requested_target,
            target,
        ) = self.calculate_target(
            axis,
            distance,
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

        self.client.send_gcode(
            script
        )

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
