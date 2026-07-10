import asyncio
from enum import Enum, auto

from submitted_horizon import SubmittedHorizon
from trapq_horizon import TrapQHorizon


class ControllerState(Enum):
    IDLE = auto()
    BOOTSTRAP = auto()
    WAITING_FOR_TRAPQ = auto()
    TRACKING = auto()


class MotionHorizonController:
    def __init__(
        self,
        horizon: TrapQHorizon,
        target_horizon: float = 0.100,
        renewal_interval: float = 0.010,
    ):
        if target_horizon <= 0.0:
            raise ValueError(
                "target_horizon must be positive"
            )

        if renewal_interval <= 0.0:
            raise ValueError(
                "renewal_interval must be positive"
            )

        self.horizon = horizon

        self.target_horizon = target_horizon
        self.renewal_interval = renewal_interval

        self.submitted_horizon = SubmittedHorizon()

        self.velocity = 0.0
        self.direction = 1.0

        self.commands_sent = 0

        self.state = ControllerState.IDLE

        self._task = None
        self._running = False

        self._bootstrap_trapq_end_time = None
        self._last_observed_trapq_end_time = None

    @property
    def active(self):
        return self.state != ControllerState.IDLE

    @property
    def control_horizon(self):
        return self.submitted_horizon.remaining_time

    @property
    def control_horizon_ms(self):
        return self.submitted_horizon.remaining_time_ms

    async def start(self):
        if self._running:
            return

        self._running = True

        self._task = asyncio.create_task(
            self._control_loop()
        )

    async def close(self):
        self._running = False
        self.state = ControllerState.IDLE

        if self._task is None:
            return

        self._task.cancel()

        try:
            await self._task

        except asyncio.CancelledError:
            pass

        self._task = None

    def activate(
        self,
        velocity: float,
        direction: float = 1.0,
    ):
        if velocity <= 0.0:
            raise ValueError(
                "velocity must be positive"
            )

        if direction not in (-1.0, 1.0):
            raise ValueError(
                "direction must be -1 or +1"
            )

        self.velocity = velocity
        self.direction = direction

        self.submitted_horizon.reset()

        self._bootstrap_trapq_end_time = (
            self.horizon.trapq_end_time
        )

        self._last_observed_trapq_end_time = (
            self.horizon.trapq_end_time
        )

        self.state = ControllerState.BOOTSTRAP

    def release(self):
        self.state = ControllerState.IDLE

    async def _control_loop(self):
        while self._running:
            if self.state == ControllerState.IDLE:
                await asyncio.sleep(
                    self.renewal_interval
                )

                continue

            if self.state == ControllerState.BOOTSTRAP:
                await self._send_extension(
                    self.target_horizon
                )

                self.state = (
                    ControllerState.WAITING_FOR_TRAPQ
                )

                await asyncio.sleep(
                    self.renewal_interval
                )

                continue

            if (
                self.state
                == ControllerState.WAITING_FOR_TRAPQ
            ):
                trapq_end_time = (
                    self.horizon.trapq_end_time
                )

                if (
                    trapq_end_time is not None
                    and trapq_end_time
                    != self._bootstrap_trapq_end_time
                ):
                    observed_remaining = (
                        self.horizon.remaining_time
                    )

                    self.submitted_horizon.observe_floor(
                        observed_remaining
                    )

                    self._last_observed_trapq_end_time = (
                        trapq_end_time
                    )

                    self.state = (
                        ControllerState.TRACKING
                    )

                await asyncio.sleep(
                    self.renewal_interval
                )

                continue

            if self.state == ControllerState.TRACKING:
                self._reconcile_observation()

                remaining = (
                    self.submitted_horizon.remaining_time
                )

                if remaining is None:
                    await asyncio.sleep(
                        self.renewal_interval
                    )

                    continue

                if remaining < self.target_horizon:
                    extension_time = (
                        self.target_horizon
                        - remaining
                    )

                    await self._send_extension(
                        extension_time
                    )

                await asyncio.sleep(
                    self.renewal_interval
                )

    def _reconcile_observation(self):
        trapq_end_time = (
            self.horizon.trapq_end_time
        )

        if trapq_end_time is None:
            return

        if (
            trapq_end_time
            == self._last_observed_trapq_end_time
        ):
            return

        observed_remaining = (
            self.horizon.remaining_time
        )

        self.submitted_horizon.observe_floor(
            observed_remaining
        )

        self._last_observed_trapq_end_time = (
            trapq_end_time
        )

    async def _send_extension(
        self,
        extension_time: float,
    ):
        if extension_time <= 0.0:
            return

        distance = (
            self.velocity
            * extension_time
            * self.direction
        )

        if abs(distance) < 0.000001:
            return

        feedrate = self.velocity * 60.0

        script = "\n".join(
            [
                (
                    "SAVE_GCODE_STATE "
                    "NAME=motion_horizon_controller"
                ),
                "G91",
                (
                    f"G1 X{distance:.6f} "
                    f"F{feedrate:.3f}"
                ),
                (
                    "RESTORE_GCODE_STATE "
                    "NAME=motion_horizon_controller"
                ),
            ]
        )

        request = {
            "id": (
                20000
                + self.commands_sent
                + 1
            ),
            "method": "gcode/script",
            "params": {
                "script": script,
            },
        }

        await self.horizon.client.send(
            request
        )

        self.submitted_horizon.submit(
            extension_time
        )

        self.commands_sent += 1
