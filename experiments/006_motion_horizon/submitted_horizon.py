import time
from typing import Optional


class SubmittedHorizon:
    def __init__(self):
        self._remaining_time: Optional[float] = None
        self._last_update_time: Optional[float] = None

    @property
    def remaining_time(self) -> Optional[float]:
        self._consume_elapsed_time()

        return self._remaining_time

    @property
    def remaining_time_ms(self) -> Optional[float]:
        remaining = self.remaining_time

        if remaining is None:
            return None

        return remaining * 1000.0

    def reset(self):
        self._remaining_time = None
        self._last_update_time = None

    def synchronize(
        self,
        remaining_time: Optional[float],
    ):
        if remaining_time is None:
            return

        self._remaining_time = max(
            remaining_time,
            0.0,
        )

        self._last_update_time = time.monotonic()

    def observe_floor(
        self,
        remaining_time: Optional[float],
    ):
        if remaining_time is None:
            return

        self._consume_elapsed_time()

        observed = max(
            remaining_time,
            0.0,
        )

        if self._remaining_time is None:
            self._remaining_time = observed

        else:
            self._remaining_time = max(
                self._remaining_time,
                observed,
            )

        self._last_update_time = time.monotonic()

    def submit(
        self,
        duration: float,
    ):
        if duration <= 0.0:
            return

        self._consume_elapsed_time()

        if self._remaining_time is None:
            self._remaining_time = 0.0

        self._remaining_time += duration

        self._last_update_time = time.monotonic()

    def _consume_elapsed_time(self):
        if self._last_update_time is None:
            return

        if self._remaining_time is None:
            return

        now = time.monotonic()

        elapsed = (
            now
            - self._last_update_time
        )

        self._remaining_time = max(
            self._remaining_time - elapsed,
            0.0,
        )

        self._last_update_time = now
