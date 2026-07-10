class MotionHorizon:
    def __init__(
        self,
        target_time=0.100,
        renewal_time=0.050,
    ):
        if target_time <= 0:
            raise ValueError(
                "Target horizon time must be positive"
            )

        if renewal_time <= 0:
            raise ValueError(
                "Renewal horizon time must be positive"
            )

        if renewal_time >= target_time:
            raise ValueError(
                "Renewal time must be smaller "
                "than target horizon time"
            )

        self.target_time = target_time
        self.renewal_time = renewal_time

        self._planned_position = {}

    def reset_axis(
        self,
        axis,
        observed_position,
    ):
        axis = axis.lower()

        self._planned_position[axis] = float(
            observed_position
        )

    def get_planned_position(
        self,
        axis,
    ):
        axis = axis.lower()

        return self._planned_position.get(
            axis
        )

    def queued_distance(
        self,
        axis,
        observed_position,
    ):
        axis = axis.lower()

        planned_position = (
            self.get_planned_position(axis)
        )

        if planned_position is None:
            return 0.0

        return (
            planned_position
            - observed_position
        )

    def queued_time(
        self,
        axis,
        observed_position,
        velocity,
    ):
        if abs(velocity) < 1e-9:
            return 0.0

        distance = self.queued_distance(
            axis,
            observed_position,
        )

        if distance * velocity <= 0:
            return 0.0

        return (
            abs(distance)
            / abs(velocity)
        )

    def needs_extension(
        self,
        axis,
        observed_position,
        velocity,
    ):
        remaining_time = self.queued_time(
            axis,
            observed_position,
            velocity,
        )

        return (
            remaining_time
            <= self.renewal_time
        )

    def calculate_extension(
        self,
        axis,
        observed_position,
        velocity,
    ):
        axis = axis.lower()

        if abs(velocity) < 1e-9:
            return 0.0

        planned_position = (
            self.get_planned_position(axis)
        )

        if planned_position is None:
            planned_position = float(
                observed_position
            )

        queued_time = self.queued_time(
            axis,
            observed_position,
            velocity,
        )

        missing_time = max(
            0.0,
            self.target_time - queued_time,
        )

        extension_distance = (
            velocity * missing_time
        )

        return extension_distance

    def register_extension(
        self,
        axis,
        distance,
    ):
        axis = axis.lower()

        planned_position = (
            self.get_planned_position(axis)
        )

        if planned_position is None:
            raise RuntimeError(
                f"Axis {axis.upper()} "
                "has not been initialized"
            )

        self._planned_position[axis] = (
            planned_position + distance
        )
