import time


class SubmittedTrajectory:
    def __init__(
        self,
        target_time=0.100,
        renewal_time=0.050,
    ):
        if target_time <= 0:
            raise ValueError(
                "Target time must be positive"
            )

        if renewal_time <= 0:
            raise ValueError(
                "Renewal time must be positive"
            )

        if renewal_time >= target_time:
            raise ValueError(
                "Renewal time must be smaller "
                "than target time"
            )

        self.target_time = float(
            target_time
        )

        self.renewal_time = float(
            renewal_time
        )

        self._velocity = 0.0

        self._submitted_distance = 0.0
        self._consumed_distance = 0.0

        self._motion_start_time = None

    def reset(
        self,
        velocity,
    ):
        self._velocity = float(
            velocity
        )

        self._submitted_distance = 0.0
        self._consumed_distance = 0.0

        self._motion_start_time = None

    def motion_started(self):
        return (
            self._motion_start_time
            is not None
        )

    def register_motion_start(self):
        if self._motion_start_time is None:
            self._motion_start_time = (
                time.monotonic()
            )

    def register_submission(
        self,
        distance,
    ):
        self._submitted_distance += abs(
            float(distance)
        )

    def _update_consumption(self):
        if self._motion_start_time is None:
            return

        elapsed = (
            time.monotonic()
            - self._motion_start_time
        )

        estimated_consumed = (
            abs(self._velocity)
            * elapsed
        )

        self._consumed_distance = min(
            estimated_consumed,
            self._submitted_distance,
        )

    def remaining_distance(self):
        self._update_consumption()

        return max(
            0.0,
            self._submitted_distance
            - self._consumed_distance,
        )

    def remaining_time(self):
        if abs(self._velocity) < 1e-9:
            return 0.0

        return (
            self.remaining_distance()
            / abs(self._velocity)
        )

    def needs_extension(self):
        return (
            self.remaining_time()
            <= self.renewal_time
        )

    def calculate_extension(self):
        missing_time = max(
            0.0,
            self.target_time
            - self.remaining_time(),
        )

        return (
            self._velocity
            * missing_time
        )

    def submitted_distance(self):
        return self._submitted_distance

    def consumed_distance(self):
        self._update_consumption()

        return self._consumed_distance
