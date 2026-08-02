from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.gcode.models import ModalState
from klipper_cnc_assistant.gcode.tokenizer import tokenize_gcode
from klipper_cnc_assistant.moonraker.client import MoonrakerClient, MoonrakerError
from klipper_cnc_assistant.storage import JsonProjectRepository


@dataclass(frozen=True)
class MotionSegment:
    motion: str
    distance_mm: float
    feed_mm_s: float
    end_offset: int
    vector: tuple[float, float, float]


@dataclass(frozen=True)
class DwellEvent:
    duration_s: float
    end_offset: int


@dataclass(frozen=True)
class UnknownTimeEvent:
    command: str
    end_offset: int


@dataclass(frozen=True)
class MachineLimits:
    max_velocity_mm_s: float
    max_accel_mm_s2: float
    minimum_cruise_ratio: float
    square_corner_velocity_mm_s: float
    z_min_mm: float | None
    z_max_mm: float | None
    max_z_velocity_mm_s: float | None = None


UNSUPPORTED_ESTIMATOR_G_CODES = {"G10", "G28", "G53", "G90.1", "G91.1", "G92"}
MOTION_G_CODES = {"G0", "G1", "G2", "G3"}
MACRO_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TimeEstimationService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        runtime: Any,
        *,
        client_factory: Callable[..., MoonrakerClient] = MoonrakerClient,
        macro_time_offset_s: float = 0.0,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.client_factory = client_factory
        self.macro_time_offset_s = max(0.0, float(macro_time_offset_s))

    def estimate_project_file(
        self,
        *,
        project_id: str,
        relative_path: str,
        remote_filename: str | None = None,
    ) -> dict[str, Any]:
        text = self.repository.read_project_file(project_id, relative_path)
        return self.estimate_text(text, remote_filename=remote_filename, source_path=relative_path)

    def estimate_text(
        self,
        text: str,
        *,
        remote_filename: str | None = None,
        source_path: str | None = None,
    ) -> dict[str, Any]:
        if remote_filename:
            external = self._estimate_with_moonraker(remote_filename)
            if external is not None:
                internal = self._estimate_internal(text)
                external["offset_table"] = self._scale_offset_table(
                    list(internal.get("offset_table") or []),
                    float(external["estimated_time_s"]),
                )
                external["distribution_method"] = "internal_scaled"
                external["distribution_detail"] = (
                    "Moonraker aporta el tiempo total; la distribución temporal por file_position "
                    "proviene del estimador interno escalado."
                )
                external["unsupported_commands"] = list(internal.get("unsupported_commands") or [])
                external["unknown_time_commands"] = list(internal.get("unknown_time_commands") or [])
                external["distance_xyz_mm"] = internal.get("distance_xyz_mm")
                external["distance_cut_mm"] = internal.get("distance_cut_mm")
                external["distance_rapid_mm"] = internal.get("distance_rapid_mm")
                external["dwell_time_s"] = internal.get("dwell_time_s")
                external["macro_time_offset_s"] = internal.get("macro_time_offset_s")
                external["machine_limits"] = internal.get("machine_limits")
                return external | {"source_path": source_path}
        internal = self._estimate_internal(text)
        internal["source_path"] = source_path
        return internal

    def _estimate_with_moonraker(self, remote_filename: str) -> dict[str, Any] | None:
        client = self._client()
        if client is None:
            return None
        try:
            status = client.get_analysis_status()
        except MoonrakerError:
            return None
        if not bool(status.get("estimator_ready")):
            return None
        try:
            estimate = client.estimate_analysis(remote_filename)
        except MoonrakerError:
            return None
        total = estimate.get("time") or estimate.get("estimated_time") or estimate.get("total_time")
        try:
            estimated_time_s = float(total)
        except (TypeError, ValueError):
            return None
        return {
            "method": "moonraker_analysis",
            "confidence": "high",
            "estimated_time_s": estimated_time_s,
            "distance_xyz_mm": None,
            "distance_cut_mm": None,
            "distance_rapid_mm": None,
            "unsupported_commands": [],
            "unknown_time_commands": [],
            "offset_table": [],
            "distribution_method": "moonraker_analysis",
            "distribution_detail": "Moonraker aporta el tiempo total y la distribución temporal.",
            "estimator_ready": True,
            "analysis_status": status,
            "raw": estimate,
        }

    def _estimate_internal(self, text: str) -> dict[str, Any]:
        state = ModalState()
        offsets = _line_offsets(text)
        lines = tokenize_gcode(text)
        events: list[MotionSegment | DwellEvent | UnknownTimeEvent] = []
        offset_table: list[dict[str, float]] = []
        unsupported_commands: list[str] = []
        unknown_time_commands: list[str] = []
        distance_xyz_mm = 0.0
        distance_cut_mm = 0.0
        distance_rapid_mm = 0.0
        dwell_time_s = 0.0

        for index, line in enumerate(lines):
            if not line.tokens:
                continue
            end_offset = offsets[min(index, len(offsets) - 1)]
            parsed = _parse_line(line=line, state=state)
            if parsed["dwell_time_s"] > 0:
                dwell_time_s += parsed["dwell_time_s"]
                events.append(DwellEvent(duration_s=float(parsed["dwell_time_s"]), end_offset=end_offset))
            if parsed["unknown_time_command"]:
                unknown_time_commands.append(parsed["unknown_time_command"])
                events.append(UnknownTimeEvent(command=str(parsed["unknown_time_command"]), end_offset=end_offset))
            if parsed["unsupported_command"]:
                unsupported_commands.append(parsed["unsupported_command"])
            segment = parsed["segment"]
            if segment is None:
                continue
            events.append(MotionSegment(end_offset=end_offset, **segment))
            distance_xyz_mm += segment["distance_mm"]
            if segment["motion"] == "G0":
                distance_rapid_mm += segment["distance_mm"]
            else:
                distance_cut_mm += segment["distance_mm"]

        limits = self._machine_limits()
        cumulative = 0.0
        previous_vector: tuple[float, float, float] | None = None
        previous_nominal = 0.0
        for idx, event in enumerate(events):
            if isinstance(event, MotionSegment):
                next_vector, next_nominal = self._next_motion_context(events, idx, limits)
                nominal = min(event.feed_mm_s, self._velocity_limit_for_segment(event, limits))
                entry = _junction_velocity(previous_vector, event.vector, previous_nominal, nominal, limits.square_corner_velocity_mm_s)
                exit_velocity = _junction_velocity(event.vector, next_vector, nominal, next_nominal, limits.square_corner_velocity_mm_s)
                seconds = _trapezoid_time(event.distance_mm, nominal, entry, exit_velocity, limits.max_accel_mm_s2, limits.minimum_cruise_ratio)
                cumulative += seconds
                previous_vector = event.vector
                previous_nominal = nominal
                offset_table.append({"file_byte_offset": float(event.end_offset), "predicted_cumulative_seconds": cumulative})
                continue
            if isinstance(event, DwellEvent):
                cumulative += event.duration_s
                previous_vector = None
                previous_nominal = 0.0
                offset_table.append({"file_byte_offset": float(event.end_offset), "predicted_cumulative_seconds": cumulative})
                continue
            cumulative += self.macro_time_offset_s
            previous_vector = None
            previous_nominal = 0.0
            offset_table.append({"file_byte_offset": float(event.end_offset), "predicted_cumulative_seconds": cumulative})

        unknown_penalty = self.macro_time_offset_s * len(unknown_time_commands)
        confidence = "low" if unknown_time_commands or unsupported_commands else "medium"
        return {
            "method": "internal",
            "confidence": confidence,
            "estimated_time_s": cumulative,
            "distance_xyz_mm": distance_xyz_mm,
            "distance_cut_mm": distance_cut_mm,
            "distance_rapid_mm": distance_rapid_mm,
            "unsupported_commands": _unique(unsupported_commands),
            "unknown_time_commands": _unique(unknown_time_commands),
            "offset_table": offset_table,
            "dwell_time_s": dwell_time_s,
            "macro_time_offset_s": unknown_penalty,
            "distribution_method": "internal",
            "distribution_detail": "Distribución temporal calculada por el estimador interno.",
            "machine_limits": limits.__dict__,
        }

    def _client(self) -> MoonrakerClient | None:
        config = self.runtime.config
        if not getattr(config, "moonraker_url", None):
            return None
        return self.client_factory(config.moonraker_url, timeout=config.moonraker_request_timeout_s)

    def _machine_limits(self) -> MachineLimits:
        client = self._client()
        if client is not None:
            try:
                status = client.query_objects(
                    {
                        "toolhead": [
                            "max_velocity",
                            "max_accel",
                            "minimum_cruise_ratio",
                            "square_corner_velocity",
                            "axis_minimum",
                            "axis_maximum",
                        ]
                    }
                )
                toolhead = status.get("toolhead") or {}
                axis_minimum = toolhead.get("axis_minimum") or (None, None, None)
                axis_maximum = toolhead.get("axis_maximum") or (None, None, None)
                return MachineLimits(
                    max_velocity_mm_s=float(toolhead.get("max_velocity") or 100.0),
                    max_accel_mm_s2=float(toolhead.get("max_accel") or 1000.0),
                    minimum_cruise_ratio=float(toolhead.get("minimum_cruise_ratio") or 0.5),
                    square_corner_velocity_mm_s=float(toolhead.get("square_corner_velocity") or 5.0),
                    z_min_mm=None if axis_minimum[2] is None else float(axis_minimum[2]),
                    z_max_mm=None if axis_maximum[2] is None else float(axis_maximum[2]),
                    max_z_velocity_mm_s=None,
                )
            except Exception:
                pass
        snapshot = self.runtime.snapshot()
        klipper = snapshot.get("klipper", {}) if isinstance(snapshot, dict) else {}
        limits = klipper.get("limits", {}) if isinstance(klipper, dict) else {}
        z_limits = limits.get("z", {}) if isinstance(limits, dict) else {}
        return MachineLimits(
            max_velocity_mm_s=float(klipper.get("max_velocity") or 100.0),
            max_accel_mm_s2=float(klipper.get("max_accel") or 1000.0),
            minimum_cruise_ratio=0.5,
            square_corner_velocity_mm_s=5.0,
            z_min_mm=None if z_limits.get("min") is None else float(z_limits["min"]),
            z_max_mm=None if z_limits.get("max") is None else float(z_limits["max"]),
            max_z_velocity_mm_s=None if klipper.get("max_z_velocity") is None else float(klipper["max_z_velocity"]),
        )

    def _velocity_limit_for_segment(self, segment: MotionSegment, limits: MachineLimits) -> float:
        dx, dy, dz = segment.vector
        vector_length = math.dist((0.0, 0.0, 0.0), segment.vector)
        if vector_length <= 0:
            return limits.max_velocity_mm_s
        axis_limits = [limits.max_velocity_mm_s]
        if abs(dz) > 1e-9 and limits.max_z_velocity_mm_s is not None:
            axis_limits.append(limits.max_z_velocity_mm_s * vector_length / abs(dz))
        return max(1e-6, min(axis_limits))

    def _next_motion_context(
        self,
        events: list[MotionSegment | DwellEvent | UnknownTimeEvent],
        index: int,
        limits: MachineLimits,
    ) -> tuple[tuple[float, float, float] | None, float]:
        for event in events[index + 1:]:
            if isinstance(event, MotionSegment):
                nominal = min(event.feed_mm_s, self._velocity_limit_for_segment(event, limits))
                return event.vector, nominal
            if isinstance(event, DwellEvent):
                break
            if isinstance(event, UnknownTimeEvent):
                break
        return None, 0.0

    def _scale_offset_table(self, offset_table: list[dict[str, float]], target_total_s: float) -> list[dict[str, float]]:
        if not offset_table:
            return []
        current_total = float(offset_table[-1].get("predicted_cumulative_seconds") or 0.0)
        if current_total <= 0:
            return [
                {"file_byte_offset": float(row.get("file_byte_offset") or 0.0), "predicted_cumulative_seconds": target_total_s}
                for row in offset_table
            ]
        scale = target_total_s / current_total
        return [
            {
                "file_byte_offset": float(row.get("file_byte_offset") or 0.0),
                "predicted_cumulative_seconds": float(row.get("predicted_cumulative_seconds") or 0.0) * scale,
            }
            for row in offset_table
        ]


def _line_offsets(text: str) -> list[int]:
    offsets: list[int] = []
    total = 0
    for raw_line in text.splitlines(keepends=True):
        total += len(raw_line.encode("utf-8"))
        offsets.append(total)
    if not offsets:
        offsets.append(0)
    return offsets


def _parse_line(*, line, state: ModalState) -> dict[str, Any]:
    code = line.code.strip()
    if not code:
        return {"segment": None, "dwell_time_s": 0.0, "unknown_time_command": None, "unsupported_command": None}
    if _looks_like_macro_line(code):
        return {"segment": None, "dwell_time_s": 0.0, "unknown_time_command": code.split()[0], "unsupported_command": None}

    motion_command = state.active_motion
    explicit_motion: str | None = None
    axes_mm: dict[str, float] = {}
    arc_params: dict[str, float] = {}
    dwell_time_s = 0.0
    unknown_time_command: str | None = None
    unsupported_command: str | None = None
    has_dwell = False

    for token in line.tokens:
        if token.letter == "G":
            command = _normalize_g_command(token.raw_value)
            if command in MOTION_G_CODES:
                motion_command = command
                explicit_motion = command
                state.active_motion = command
            elif command == "G20":
                state.units = "inch"
                state.seen_units.add("inch")
            elif command == "G21":
                state.units = "mm"
                state.seen_units.add("mm")
            elif command == "G90":
                state.positioning = "absolute"
                state.seen_positioning.add("absolute")
            elif command == "G91":
                state.positioning = "relative"
                state.seen_positioning.add("relative")
            elif command == "G94":
                state.feed_mode = "units_per_minute"
            elif command == "G4":
                has_dwell = True
            elif command in UNSUPPORTED_ESTIMATOR_G_CODES:
                unsupported_command = f"L{line.line_number}:{command}: no soportado por el estimador interno"
            else:
                unsupported_command = f"L{line.line_number}:{command}: comando G no soportado por el estimador interno"
        elif token.letter in {"X", "Y", "Z"}:
            axes_mm[token.letter] = _to_mm(float(token.raw_value), state.units)
        elif token.letter in {"I", "J", "K", "R"}:
            arc_params[token.letter] = _to_mm(float(token.raw_value), state.units)
        elif token.letter == "F":
            state.feed_mm_min = _to_mm(float(token.raw_value), state.units)
        elif token.letter == "P" and has_dwell:
            dwell_time_s = float(token.raw_value) / 1000.0
        elif token.letter == "S" and has_dwell:
            dwell_time_s = float(token.raw_value)
        elif token.letter in {"M", "T"}:
            continue

    has_motion_coordinates = bool(axes_mm or arc_params)
    if unsupported_command is not None:
        return {"segment": None, "dwell_time_s": dwell_time_s, "unknown_time_command": unknown_time_command, "unsupported_command": unsupported_command}
    if has_dwell:
        if has_motion_coordinates or explicit_motion is not None:
            return {
                "segment": None,
                "dwell_time_s": 0.0,
                "unknown_time_command": unknown_time_command,
                "unsupported_command": f"L{line.line_number}:G4 incompatible con movimiento en la misma línea",
            }
        return {"segment": None, "dwell_time_s": dwell_time_s, "unknown_time_command": unknown_time_command, "unsupported_command": None}

    if not has_motion_coordinates:
        return {"segment": None, "dwell_time_s": dwell_time_s, "unknown_time_command": unknown_time_command, "unsupported_command": unsupported_command}
    if motion_command is None:
        return {
            "segment": None,
            "dwell_time_s": dwell_time_s,
            "unknown_time_command": unknown_time_command,
            "unsupported_command": f"L{line.line_number}: coordenadas sin movimiento modal activo",
        }

    start_x = state.x_mm
    start_y = state.y_mm
    start_z = state.z_mm
    target_x = _resolve_target_value(state.x_mm, axes_mm.get("X"), state.positioning)
    target_y = _resolve_target_value(state.y_mm, axes_mm.get("Y"), state.positioning)
    target_z = _resolve_target_value(state.z_mm, axes_mm.get("Z"), state.positioning)
    state.x_mm = target_x
    state.y_mm = target_y
    state.z_mm = target_z

    if motion_command == "G0":
        distance = math.dist((start_x, start_y, start_z), (target_x, target_y, target_z))
        return {
            "segment": {
                "motion": "G0",
                "distance_mm": distance,
                "feed_mm_s": 1_000_000.0,
                "vector": (target_x - start_x, target_y - start_y, target_z - start_z),
            },
            "dwell_time_s": dwell_time_s,
            "unknown_time_command": unknown_time_command,
            "unsupported_command": unsupported_command,
        }
    if motion_command == "G1":
        if state.feed_mm_min is None:
            raise ApplicationError(f"La línea {line.line_number} usa G1 sin feed modal disponible.")
        distance = math.dist((start_x, start_y, start_z), (target_x, target_y, target_z))
        return {
            "segment": {
                "motion": "G1",
                "distance_mm": distance,
                "feed_mm_s": max(1e-6, float(state.feed_mm_min) / 60.0),
                "vector": (target_x - start_x, target_y - start_y, target_z - start_z),
            },
            "dwell_time_s": dwell_time_s,
            "unknown_time_command": unknown_time_command,
            "unsupported_command": unsupported_command,
        }
    if "R" in arc_params or "K" in arc_params or ("I" not in arc_params and "J" not in arc_params):
        unsupported_command = motion_command
        return {"segment": None, "dwell_time_s": dwell_time_s, "unknown_time_command": unknown_time_command, "unsupported_command": unsupported_command}
    center_x = start_x + arc_params.get("I", 0.0)
    center_y = start_y + arc_params.get("J", 0.0)
    radius_start = math.dist((start_x, start_y), (center_x, center_y))
    radius_end = math.dist((target_x, target_y), (center_x, center_y))
    if radius_start <= 1e-6 or abs(radius_start - radius_end) > max(0.05, radius_start * 0.01):
        unsupported_command = motion_command
        return {"segment": None, "dwell_time_s": dwell_time_s, "unknown_time_command": unknown_time_command, "unsupported_command": unsupported_command}
    full_circle = math.isclose(start_x, target_x, abs_tol=1e-6) and math.isclose(start_y, target_y, abs_tol=1e-6)
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(target_y - center_y, target_x - center_x)
    if full_circle:
        sweep_angle = -2 * math.pi if motion_command == "G2" else 2 * math.pi
    else:
        sweep_angle = end_angle - start_angle
        if motion_command == "G2" and sweep_angle >= 0:
            sweep_angle -= 2 * math.pi
        if motion_command == "G3" and sweep_angle <= 0:
            sweep_angle += 2 * math.pi
    arc_length = abs(sweep_angle) * radius_start
    distance = math.hypot(arc_length, target_z - start_z)
    if state.feed_mm_min is None:
        raise ApplicationError(f"La línea {line.line_number} usa {motion_command} sin feed modal disponible.")
    return {
        "segment": {
            "motion": motion_command,
            "distance_mm": distance,
            "feed_mm_s": max(1e-6, float(state.feed_mm_min) / 60.0),
            "vector": (target_x - start_x, target_y - start_y, target_z - start_z),
        },
        "dwell_time_s": dwell_time_s,
        "unknown_time_command": unknown_time_command,
        "unsupported_command": unsupported_command,
    }


def _looks_like_macro_line(code: str) -> bool:
    first = code.split()[0]
    return bool("_" in first and MACRO_WORD_RE.match(first))


def _normalize_g_command(raw_value: str | None) -> str:
    if raw_value is None:
        return "G"
    value = float(raw_value)
    if value.is_integer():
        return f"G{int(value)}"
    return f"G{value:g}"


def _to_mm(value: float, units: str) -> float:
    return value if units == "mm" else value * 25.4


def _resolve_target_value(current: float, raw_value: float | None, positioning: str) -> float:
    if raw_value is None:
        return current
    if positioning == "absolute":
        return raw_value
    return current + raw_value


def _junction_velocity(
    previous_vector: tuple[float, float, float] | None,
    current_vector: tuple[float, float, float] | None,
    previous_nominal: float,
    current_nominal: float,
    square_corner_velocity_mm_s: float,
) -> float:
    if previous_vector is None or current_vector is None:
        return 0.0
    prev_norm = math.dist((0.0, 0.0, 0.0), previous_vector)
    curr_norm = math.dist((0.0, 0.0, 0.0), current_vector)
    if prev_norm <= 1e-9 or curr_norm <= 1e-9:
        return 0.0
    dot = sum(left * right for left, right in zip(previous_vector, current_vector))
    cosine = max(-1.0, min(1.0, dot / (prev_norm * curr_norm)))
    if cosine > 0.999:
        return min(previous_nominal, current_nominal)
    angle = math.acos(cosine)
    turn_factor = max(0.1, math.sin(max(0.05, angle) / 2.0))
    return min(previous_nominal, current_nominal, square_corner_velocity_mm_s / turn_factor)


def _trapezoid_time(
    distance_mm: float,
    nominal_mm_s: float,
    entry_mm_s: float,
    exit_mm_s: float,
    accel_mm_s2: float,
    minimum_cruise_ratio: float,
) -> float:
    if distance_mm <= 0:
        return 0.0
    accel = max(1e-6, accel_mm_s2)
    nominal = max(1e-6, nominal_mm_s)
    entry = max(0.0, min(entry_mm_s, nominal))
    exit_velocity = max(0.0, min(exit_mm_s, nominal))
    accel_distance = max(0.0, (nominal * nominal - entry * entry) / (2.0 * accel))
    decel_distance = max(0.0, (nominal * nominal - exit_velocity * exit_velocity) / (2.0 * accel))
    if accel_distance + decel_distance <= distance_mm:
        cruise_distance = distance_mm - accel_distance - decel_distance
        min_cruise_distance = distance_mm * max(0.0, min(minimum_cruise_ratio, 1.0))
        if cruise_distance < min_cruise_distance:
            cruise_distance = min_cruise_distance
        return (nominal - entry) / accel + cruise_distance / nominal + (nominal - exit_velocity) / accel
    peak_sq = max((2.0 * accel * distance_mm + entry * entry + exit_velocity * exit_velocity) / 2.0, 0.0)
    peak = math.sqrt(peak_sq)
    return max(0.0, (peak - entry) / accel) + max(0.0, (peak - exit_velocity) / accel)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
