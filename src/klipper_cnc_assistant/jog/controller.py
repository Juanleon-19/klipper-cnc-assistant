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
