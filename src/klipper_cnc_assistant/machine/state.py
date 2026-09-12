from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4
import time


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

    @classmethod
    def from_iterable(cls, values):
        return cls(float(values[0]), float(values[1]), float(values[2]))

    def as_tuple(self):
        return (self.x, self.y, self.z)


@dataclass(frozen=True)
class PositionFrame:
    position: tuple[float, float, float] | None
    timestamp: float | None
    age_s: float | None
    frame: str | None
    source: str | None
    homed_axes: str
    session: str
    klippy_state: str | None
    klippy_updated_at: float | None
    limits: tuple[tuple[float, float], ...]
    max_velocity: float
    max_accel: float


@dataclass
class MachineState:
    position: MachinePosition

    x_limits: AxisLimits
    y_limits: AxisLimits
    z_limits: AxisLimits

    homed_axes: str

    max_velocity: float
    max_accel: float
    max_z_velocity: float | None = None

    live_velocity: float = 0.0
    live_position: MachinePosition | None = None
    commanded_position: MachinePosition | None = None
    gcode_position: MachinePosition | None = None
    gcode_move_position: MachinePosition | None = None
    absolute_coordinates: bool | None = None
    homing_origin: MachinePosition | None = None
    live_position_updated_at: float | None = None
    live_position_source: str | None = None
    commanded_position_updated_at: float | None = None
    gcode_position_updated_at: float | None = None

    gcode_move_position_updated_at: float | None = None
    physical_session: str = field(default_factory=lambda: uuid4().hex)
    klippy_state: str | None = None
    klippy_updated_at: float | None = None
    live_velocity_updated_at: float | None = None

    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self):
        if self.commanded_position is None:
            self.commanded_position = MachinePosition(self.position.x, self.position.y, self.position.z)

    @property
    def is_homed(self):
        return {"x", "y", "z"}.issubset(set(self.homed_axes))

    def axis_is_homed(self, axis):
        return axis.lower() in self.homed_axes

    def update_toolhead(self, *, position=None, homed_axes=None, axis_minimum=None, axis_maximum=None, max_velocity=None, max_accel=None, max_z_velocity=None):
        with self._lock:
            now = time.monotonic()
            if position is not None:
                self.commanded_position = MachinePosition.from_iterable(position)
                self.commanded_position_updated_at = now
                if self.live_position is None:
                    self.position = MachinePosition(self.commanded_position.x, self.commanded_position.y, self.commanded_position.z)
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
            if max_z_velocity is not None:
                self.max_z_velocity = float(max_z_velocity)

    def update_motion(self, live_position=None, live_velocity=None, source=None):
        with self._lock:
            now = time.monotonic()
            if live_position is not None:
                self.live_position = MachinePosition.from_iterable(live_position)
                self.live_position_updated_at = now
                self.live_position_source = None if source is None else str(source)
                self.position = MachinePosition(self.live_position.x, self.live_position.y, self.live_position.z)
            if live_velocity is not None:
                self.live_velocity = float(live_velocity)
                self.live_velocity_updated_at = now

    def update_gcode_move(self, *, gcode_position=None, position=None, absolute_coordinates=None, homing_origin=None):
        with self._lock:
            now = time.monotonic()
            if gcode_position is not None:
                self.gcode_position = MachinePosition.from_iterable(gcode_position)
                self.gcode_position_updated_at = now
            if position is not None:
                self.gcode_move_position = MachinePosition.from_iterable(position)
                self.gcode_move_position_updated_at = now
            if absolute_coordinates is not None:
                self.absolute_coordinates = bool(absolute_coordinates)
            if homing_origin is not None:
                self.homing_origin = MachinePosition.from_iterable(homing_origin)

    def update_klippy(self, state):
        with self._lock:
            self.klippy_state = str(state)
            self.klippy_updated_at = time.monotonic()

    def bind_physical_session(self, session, *, observed_in_session=False):
        with self._lock:
            if session != self.physical_session and not observed_in_session:
                self.live_position_updated_at = None
                self.commanded_position_updated_at = None
                self.gcode_position_updated_at = None
                self.gcode_move_position_updated_at = None
                self.live_velocity_updated_at = None
                self.klippy_updated_at = None
            self.physical_session = session

    def authorization_snapshot(self, frame):
        sources = {'live_position': 'motion_report.live_position',
                   'commanded_position': 'toolhead.position',
                   'gcode_position': 'gcode_move.gcode_position',
                   'gcode_move_position': 'gcode_move.position'}
        if frame is not None and frame not in sources:
            raise ValueError('Frame desconocido.')
        with self._lock:
            value = None if frame is None else getattr(self, frame)
            stamp = None if frame is None else getattr(self, frame + '_updated_at')
            return PositionFrame(None if value is None else value.as_tuple(), stamp,
                                 None if stamp is None else time.monotonic() - stamp,
                                 frame, sources.get(frame), self.homed_axes, self.physical_session,
                                 self.klippy_state, self.klippy_updated_at,
                                 tuple((axis.minimum, axis.maximum) for axis in (self.x_limits, self.y_limits, self.z_limits)),
                                 self.max_velocity, self.max_accel)

    def get_motion_snapshot(self):
        with self._lock:
            live = self.live_position or self.position
            commanded = self.commanded_position
            gcode = self.gcode_position
            gcode_move = self.gcode_move_position
            return {
                "x": live.x,
                "y": live.y,
                "z": live.z,
                "velocity": self.live_velocity,
                "source": "motion_report.live_position" if self.live_position is not None else "toolhead.position",
                "live_position_source": self.live_position_source,
                "live_position": None if self.live_position is None else {"x": live.x, "y": live.y, "z": live.z},
                "commanded_position": None if commanded is None else {"x": commanded.x, "y": commanded.y, "z": commanded.z},
                "gcode_position": None if gcode is None else {"x": gcode.x, "y": gcode.y, "z": gcode.z},
                "gcode_move_position": None if gcode_move is None else {"x": gcode_move.x, "y": gcode_move.y, "z": gcode_move.z},
                "absolute_coordinates": self.absolute_coordinates,
                "gcode_move_position_age_s": None if self.gcode_move_position_updated_at is None else time.monotonic() - self.gcode_move_position_updated_at,
                "velocity_updated_at": self.live_velocity_updated_at,
                "velocity_age_s": None if self.live_velocity_updated_at is None else time.monotonic() - self.live_velocity_updated_at,
                "homing_origin": None if self.homing_origin is None else {"x": self.homing_origin.x, "y": self.homing_origin.y, "z": self.homing_origin.z},
                "live_position_age_s": None if self.live_position_updated_at is None else max(0.0, time.monotonic() - self.live_position_updated_at),
                "commanded_position_age_s": None if self.commanded_position_updated_at is None else max(0.0, time.monotonic() - self.commanded_position_updated_at),
                "gcode_position_age_s": None if self.gcode_position_updated_at is None else max(0.0, time.monotonic() - self.gcode_position_updated_at),
            }
