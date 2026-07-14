from dataclasses import dataclass, field
from threading import Lock


@dataclass
class AxisLimits:
    minimum: float
    maximum: float

    @property
    def travel(self):
        return self.maximum - self.minimum


@dataclass
class MachinePosition:
    x: float
    y: float
    z: float


@dataclass
class MachineState:
    position: MachinePosition

    x_limits: AxisLimits
    y_limits: AxisLimits
    z_limits: AxisLimits

    homed_axes: str

    max_velocity: float
    max_accel: float

    live_velocity: float = 0.0

    _lock: Lock = field(
        default_factory=Lock,
        repr=False,
    )

    @property
    def is_homed(self):
        required_axes = {"x", "y", "z"}

        return required_axes.issubset(
            set(self.homed_axes)
        )

    def axis_is_homed(
        self,
        axis,
    ):
        return axis.lower() in self.homed_axes

    def update_toolhead(
        self,
        *,
        position=None,
        homed_axes=None,
        axis_minimum=None,
        axis_maximum=None,
        max_velocity=None,
        max_accel=None,
    ):
        with self._lock:
            if position is not None:
                self.position.x = float(position[0])
                self.position.y = float(position[1])
                self.position.z = float(position[2])

            if homed_axes is not None:
                self.homed_axes = str(homed_axes)

            if axis_minimum is not None and axis_maximum is not None:
                self.x_limits.minimum = float(axis_minimum[0])
                self.y_limits.minimum = float(axis_minimum[1])
                self.z_limits.minimum = float(axis_minimum[2])
                self.x_limits.maximum = float(axis_maximum[0])
                self.y_limits.maximum = float(axis_maximum[1])
                self.z_limits.maximum = float(axis_maximum[2])

            if max_velocity is not None:
                self.max_velocity = float(max_velocity)

            if max_accel is not None:
                self.max_accel = float(max_accel)

    def update_motion(
        self,
        live_position=None,
        live_velocity=None,
    ):
        with self._lock:
            if live_position is not None:
                self.position.x = float(
                    live_position[0]
                )

                self.position.y = float(
                    live_position[1]
                )

                self.position.z = float(
                    live_position[2]
                )

            if live_velocity is not None:
                self.live_velocity = float(
                    live_velocity
                )

    def get_motion_snapshot(self):
        with self._lock:
            return {
                "x": self.position.x,
                "y": self.position.y,
                "z": self.position.z,
                "velocity": self.live_velocity,
            }
