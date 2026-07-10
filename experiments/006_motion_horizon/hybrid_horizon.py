class HybridMotionHorizon:
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

        self.target_time = float(
            target_time
        )

        self.renewal_time = float(
            renewal_time
        )

        self._planned_position = None

    def reset(
        self,
        position,
    ):
        self._planned_position = float(
            position
        )

    def queued_distance(
        self,
        estimated_position,
    ):
        if self._planned_position is None:
            raise RuntimeError(
                "Motion horizon has not been initialized"
            )

        return (
            self._planned_position
            - float(estimated_position)
        )

    def queued_time(
        self,
        estimated_position,
        velocity,
    ):
        if abs(velocity) < 1e-9:
            return 0.0

        distance = self.queued_distance(
            estimated_position
        )

        return (
            abs(distance)
            / abs(velocity)
        )

    def needs_extension(
        self,
        estimated_position,
        velocity,
    ):
        return (
            self.queued_time(
                estimated_position,
                velocity,
            )
            <= self.renewal_time
        )

    def calculate_extension(
        self,
        estimated_position,
        velocity,
    ):
        current_time = self.queued_time(
            estimated_position,
            velocity,
        )

        missing_time = max(
            0.0,
            self.target_time
            - current_time,
        )

        return (
            velocity
            * missing_time
        )

    def register_extension(
        self,
        distance,
    ):
        if self._planned_position is None:
            raise RuntimeError(
                "Motion horizon has not been initialized"
            )

        self._planned_position += float(
            distance
        )

    def planned_position(self):
        return self._planned_position
