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
