import time


class PredictiveMotionHorizon:
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
        self._remaining_time = 0.0
        self._last_update = None

    def reset(
        self,
        observed_position,
    ):
        self._planned_position = float(
            observed_position
        )

        self._remaining_time = 0.0
        self._last_update = time.monotonic()

    def _update_time(self):
        now = time.monotonic()

        if self._last_update is None:
            self._last_update = now
            return

        elapsed = (
            now - self._last_update
        )

        self._remaining_time = max(
            0.0,
            self._remaining_time - elapsed,
        )

        self._last_update = now

    def remaining_time(self):
        self._update_time()

        return self._remaining_time

    def needs_extension(self):
        return (
            self.remaining_time()
            <= self.renewal_time
        )

    def calculate_extension(
        self,
        velocity,
    ):
        remaining_time = (
            self.remaining_time()
        )

        missing_time = max(
            0.0,
            self.target_time
            - remaining_time,
        )

        return (
            velocity * missing_time
        )

    def register_extension(
        self,
        distance,
        velocity,
    ):
        self._update_time()

        if abs(velocity) < 1e-9:
            return

        motion_time = (
            abs(distance)
            / abs(velocity)
        )

        self._remaining_time += motion_time

        self._planned_position += distance

    def get_planned_position(self):
        return self._planned_position
