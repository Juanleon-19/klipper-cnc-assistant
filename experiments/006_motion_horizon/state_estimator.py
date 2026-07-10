import time


class MotionStateEstimator:
    def __init__(
        self,
        position_tolerance=0.001,
        velocity_tolerance=0.01,
    ):
        self.position_tolerance = float(
            position_tolerance
        )

        self.velocity_tolerance = float(
            velocity_tolerance
        )

        self._sample_position = None
        self._sample_velocity = 0.0
        self._sample_time = None

        self._last_observed_position = None
        self._last_observed_velocity = None

        self._sample_count = 0

    def reset(
        self,
        position,
        velocity,
    ):
        now = time.monotonic()

        self._sample_position = float(
            position
        )

        self._sample_velocity = float(
            velocity
        )

        self._sample_time = now

        self._last_observed_position = float(
            position
        )

        self._last_observed_velocity = float(
            velocity
        )

        self._sample_count = 1

    def update(
        self,
        position,
        velocity,
    ):
        position = float(position)
        velocity = float(velocity)

        if self._sample_time is None:
            self.reset(
                position=position,
                velocity=velocity,
            )

            return True

        position_changed = (
            abs(
                position
                - self._last_observed_position
            )
            > self.position_tolerance
        )

        velocity_changed = (
            abs(
                velocity
                - self._last_observed_velocity
            )
            > self.velocity_tolerance
        )

        if not (
            position_changed
            or velocity_changed
        ):
            return False

        now = time.monotonic()

        self._sample_position = position
        self._sample_velocity = velocity
        self._sample_time = now

        self._last_observed_position = position
        self._last_observed_velocity = velocity

        self._sample_count += 1

        return True

    def estimated_position(self):
        if self._sample_time is None:
            raise RuntimeError(
                "State estimator has not been initialized"
            )

        now = time.monotonic()

        elapsed = (
            now - self._sample_time
        )

        return (
            self._sample_position
            + self._sample_velocity
            * elapsed
        )

    def sample_position(self):
        return self._sample_position

    def sample_velocity(self):
        return self._sample_velocity

    def sample_age(self):
        if self._sample_time is None:
            return 0.0

        return (
            time.monotonic()
            - self._sample_time
        )

    def sample_count(self):
        return self._sample_count
