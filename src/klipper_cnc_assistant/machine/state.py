from dataclasses import dataclass


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
