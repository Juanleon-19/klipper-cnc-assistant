from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.gcode.models import GCodeLine, ModalState
from klipper_cnc_assistant.gcode.tokenizer import tokenize_gcode
from klipper_cnc_assistant.heightmap import HeightMap, interpolate_height
from klipper_cnc_assistant.heightmap.coverage import check_domain


MAX_RECURSION_DEPTH = 12
MAX_EMITTED_MOVES = 50_000
FULL_CIRCLE_EPSILON = 1e-6
SURFACE_THRESHOLD_Z_MM = 0.0
UNSUPPORTED_G_CODES = {"G10", "G28", "G53", "G90.1", "G91.1", "G92"}
SETUP_G_CODES = {"G17", "G18", "G19", "G20", "G21", "G90", "G91", "G94"}
MOTION_G_CODES = {"G0", "G1", "G2", "G3"}
DWELL_G_CODES = {"G4"}
MACRO_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SPAN_SAMPLE_FACTORS = (0.125, 0.25, 0.5, 0.75, 0.875)


@dataclass(frozen=True)
class ReferenceFrame:
    machine_origin_x_mm: float
    machine_origin_y_mm: float
    surface_reference_z_mm: float


@dataclass(frozen=True)
class AdaptivePoint:
    progress: float
    pcb_x_mm: float
    pcb_y_mm: float
    programmed_z_mm: float
    machine_x_mm: float
    machine_y_mm: float
    machine_z_mm: float
    delta_z_mm: float
    uses_surface_map: bool


@dataclass(frozen=True)
class ArcDefinition:
    command: str
    center_x_mm: float
    center_y_mm: float
    radius_mm: float
    start_angle: float
    sweep_angle: float


@dataclass(frozen=True)
class ParsedAdaptiveLine:
    category: str
    plane: str
    command: str | None
    explicit_motion: str | None
    prefix_tokens: tuple[str, ...]
    passthrough_tokens: tuple[str, ...]
    axes_mm: dict[str, float]
    arc_params_mm: dict[str, float]
    feed_token: str | None
    unsupported_reason: str | None = None


@dataclass
class EmissionState:
    last_emitted_point: AdaptivePoint | None = None


def generate_adaptive_gcode(
    *,
    original_text: str,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    operation_id: str,
    operation_name: str,
    min_segment_length_mm: float,
) -> dict[str, Any]:
    lines = tokenize_gcode(original_text)
    state = ModalState()
    emission_state = EmissionState()
    plane = "G17"
    output_lines = [
        "; Klipper CNC Assistant - plan compensado adaptativo",
        f"; Operacion: {operation_name} ({operation_id})",
        "; Algoritmo: adaptive_fast",
    ]
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    unsupported_commands: list[str] = []
    delta_values: list[float] = []
    z_values: list[float] = []
    outside_points: list[dict[str, Any]] = []
    segments_subdivided = 0
    segments_fused = 0
    emitted_moves = 0
    max_approximation_error_mm = 0.0

    for line in lines:
        result = _transform_line(
            line=line,
            state=state,
            emission_state=emission_state,
            plane=plane,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
        )
        plane = result["plane"]
        output_lines.extend(result["lines"])
        trace.extend(result["trace"])
        warnings.extend(result["warnings"])
        unsupported_commands.extend(result["unsupported_commands"])
        outside_points.extend(result["outside_points"])
        delta_values.extend(result["delta_values"])
        z_values.extend(result["z_values"])
        segments_subdivided += int(result["segments_subdivided"])
        segments_fused += int(result["segments_fused"])
        emitted_moves += int(result["emitted_moves"])
        max_approximation_error_mm = max(max_approximation_error_mm, float(result["max_approximation_error_mm"]))
        if emitted_moves > MAX_EMITTED_MOVES:
            raise ApplicationError("adaptive_fast excedió el límite de movimientos permitido.")

    if outside_points:
        first = outside_points[0]
        raise ApplicationError(
            "adaptive_fast detectó trayectorias fuera de la cobertura del mapa. "
            f"Línea {first['line_number']}, X={first['x_mm']:.3f}, Y={first['y_mm']:.3f}, motivo={first['reason']}."
        )
    if unsupported_commands:
        detail = ", ".join(_unique(unsupported_commands)[:6])
        raise ApplicationError(f"adaptive_fast bloqueado por comandos o construcciones no soportadas: {detail}.")

    return {
        "output": "\n".join(output_lines) + "\n",
        "preview": {
            "engine": "adaptive_fast",
            "trace": trace,
            "warnings": _unique(warnings),
            "emitted_points": len(trace),
            "delta_z_min_mm": min(delta_values) if delta_values else None,
            "delta_z_max_mm": max(delta_values) if delta_values else None,
            "delta_z_rms_mm": _rms(delta_values),
            "z_compensated_min_mm": min(z_values) if z_values else None,
            "z_compensated_max_mm": max(z_values) if z_values else None,
            "segments_subdivided": segments_subdivided,
            "segments_fused": segments_fused,
            "max_approximation_error_mm": max_approximation_error_mm,
            "unsupported_commands": _unique(unsupported_commands),
            "outside_points": outside_points,
            "extrapolations": 0,
            "movement_limit": MAX_EMITTED_MOVES,
        },
    }


def _transform_line(
    *,
    line: GCodeLine,
    state: ModalState,
    emission_state: EmissionState,
    plane: str,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> dict[str, Any]:
    if not line.tokens:
        return _result([line.raw] if line.raw else [], plane=plane)

    parsed = _parse_adaptive_line(line=line, state=state, plane=plane)
    if parsed.category in {"modal_change", "auxiliary", "movement_passthrough"}:
        return _result([line.raw], plane=parsed.plane)
    if parsed.category == "unsupported":
        return _result([line.raw], plane=parsed.plane, unsupported_commands=[str(parsed.unsupported_reason or f"L{line.line_number}: comando no soportado")])

    command = str(parsed.command)
    start_x = state.x_mm
    start_y = state.y_mm
    start_z = state.z_mm
    target_x = _resolve_target_value(state.x_mm, parsed.axes_mm.get("X"), state.positioning)
    target_y = _resolve_target_value(state.y_mm, parsed.axes_mm.get("Y"), state.positioning)
    target_z = _resolve_target_value(state.z_mm, parsed.axes_mm.get("Z"), state.positioning)

    if command in {"G0", "G1"}:
        result = _transform_linear_motion(
            line=line,
            command=command,
            prefix_tokens=list(parsed.prefix_tokens),
            passthrough_tokens=list(parsed.passthrough_tokens),
            feed_token=parsed.feed_token,
            state=state,
            emission_state=emission_state,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
        )
    else:
        result = _transform_arc_motion(
            line=line,
            command=command,
            prefix_tokens=list(parsed.prefix_tokens),
            passthrough_tokens=list(parsed.passthrough_tokens),
            feed_token=parsed.feed_token,
            state=state,
            emission_state=emission_state,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            arc_params_mm=parsed.arc_params_mm,
            plane=parsed.plane,
        )

    state.x_mm = target_x
    state.y_mm = target_y
    state.z_mm = target_z
    last_emitted_point = result.get("last_emitted_point")
    if isinstance(last_emitted_point, AdaptivePoint):
        emission_state.last_emitted_point = last_emitted_point
    return result | {"plane": parsed.plane}


def _parse_adaptive_line(*, line: GCodeLine, state: ModalState, plane: str) -> ParsedAdaptiveLine:
    code = line.code.strip()
    if not code:
        return ParsedAdaptiveLine("auxiliary", plane, None, None, (), (), {}, {}, None)
    if _looks_like_macro_line(code):
        return ParsedAdaptiveLine("auxiliary", plane, None, None, (), (), {}, {}, None)

    motion_command = state.active_motion
    explicit_motion: str | None = None
    axes_mm: dict[str, float] = {}
    arc_params_mm: dict[str, float] = {}
    prefix_tokens: list[str] = []
    passthrough_tokens: list[str] = []
    feed_token: str | None = None
    next_plane = plane
    has_dwell = False
    incompatible_motion_code: str | None = None

    for token in line.tokens:
        if token.letter == "G":
            command = _normalize_g_command(token.raw_value)
            if command in MOTION_G_CODES:
                explicit_motion = command
                motion_command = command
                state.active_motion = command
            elif command in {"G20", "G21"}:
                state.units = "mm" if command == "G21" else "inch"
                state.seen_units.add(state.units)
                prefix_tokens.append(command)
            elif command in {"G90", "G91"}:
                state.positioning = "absolute" if command == "G90" else "relative"
                state.seen_positioning.add(state.positioning)
                prefix_tokens.append(command)
            elif command in {"G17", "G18", "G19"}:
                next_plane = command
                prefix_tokens.append(command)
            elif command == "G94":
                state.feed_mode = "units_per_minute"
                prefix_tokens.append(command)
            elif command in DWELL_G_CODES:
                has_dwell = True
                incompatible_motion_code = command
            elif command in UNSUPPORTED_G_CODES:
                return ParsedAdaptiveLine(
                    category="unsupported",
                    plane=next_plane,
                    command=None,
                    explicit_motion=explicit_motion,
                    prefix_tokens=tuple(prefix_tokens),
                    passthrough_tokens=tuple(passthrough_tokens),
                    axes_mm=axes_mm,
                    arc_params_mm=arc_params_mm,
                    feed_token=feed_token,
                    unsupported_reason=f"L{line.line_number}:{command}: no soportado por adaptive_fast",
                )
            else:
                return ParsedAdaptiveLine(
                    category="unsupported",
                    plane=next_plane,
                    command=None,
                    explicit_motion=explicit_motion,
                    prefix_tokens=tuple(prefix_tokens),
                    passthrough_tokens=tuple(passthrough_tokens),
                    axes_mm=axes_mm,
                    arc_params_mm=arc_params_mm,
                    feed_token=feed_token,
                    unsupported_reason=f"L{line.line_number}:{command}: comando G desconocido o no soportado",
                )
        elif token.letter in {"X", "Y", "Z"}:
            axes_mm[token.letter] = _to_mm(_numeric(token.raw_value), state.units)
        elif token.letter in {"I", "J", "K", "R"}:
            arc_params_mm[token.letter] = _to_mm(_numeric(token.raw_value), state.units)
        elif token.letter == "F":
            state.feed_mm_min = _to_mm(_numeric(token.raw_value), state.units)
            feed_token = token.raw_value
        elif token.letter in {"M", "T"}:
            passthrough_tokens.append(token.command)
        elif token.letter == "S":
            passthrough_tokens.append(token.command)
        else:
            passthrough_tokens.append(token.command)

    has_motion_coordinates = bool(axes_mm or arc_params_mm)
    if has_dwell:
        if has_motion_coordinates or explicit_motion is not None:
            return ParsedAdaptiveLine(
                category="unsupported",
                plane=next_plane,
                command=None,
                explicit_motion=explicit_motion,
                prefix_tokens=tuple(prefix_tokens),
                passthrough_tokens=tuple(passthrough_tokens),
                axes_mm=axes_mm,
                arc_params_mm=arc_params_mm,
                feed_token=feed_token,
                unsupported_reason=f"L{line.line_number}:{incompatible_motion_code}: incompatible con movimiento en la misma línea",
            )
        return ParsedAdaptiveLine("auxiliary", next_plane, None, None, tuple(prefix_tokens), tuple(passthrough_tokens), axes_mm, arc_params_mm, feed_token)

    if not has_motion_coordinates:
        if explicit_motion is not None:
            return ParsedAdaptiveLine("movement_passthrough", next_plane, explicit_motion, explicit_motion, tuple(prefix_tokens), tuple(passthrough_tokens), axes_mm, arc_params_mm, feed_token)
        return ParsedAdaptiveLine("modal_change" if prefix_tokens or feed_token is not None else "auxiliary", next_plane, None, None, tuple(prefix_tokens), tuple(passthrough_tokens), axes_mm, arc_params_mm, feed_token)

    if motion_command is None:
        return ParsedAdaptiveLine(
            category="unsupported",
            plane=next_plane,
            command=None,
            explicit_motion=explicit_motion,
            prefix_tokens=tuple(prefix_tokens),
            passthrough_tokens=tuple(passthrough_tokens),
            axes_mm=axes_mm,
            arc_params_mm=arc_params_mm,
            feed_token=feed_token,
            unsupported_reason=f"L{line.line_number}: coordenadas sin movimiento modal activo",
        )

    if motion_command in {"G2", "G3"} and next_plane != "G17":
        return ParsedAdaptiveLine(
            category="unsupported",
            plane=next_plane,
            command=None,
            explicit_motion=explicit_motion,
            prefix_tokens=tuple(prefix_tokens),
            passthrough_tokens=tuple(passthrough_tokens),
            axes_mm=axes_mm,
            arc_params_mm=arc_params_mm,
            feed_token=feed_token,
            unsupported_reason=f"L{line.line_number}:{next_plane}: adaptive_fast solo soporta arcos en G17",
        )

    return ParsedAdaptiveLine(
        category="movement_with_side_effect" if passthrough_tokens else "movement",
        plane=next_plane,
        command=motion_command,
        explicit_motion=explicit_motion,
        prefix_tokens=tuple(prefix_tokens),
        passthrough_tokens=tuple(passthrough_tokens),
        axes_mm=axes_mm,
        arc_params_mm=arc_params_mm,
        feed_token=feed_token,
    )


def _transform_linear_motion(
    *,
    line: GCodeLine,
    command: str,
    prefix_tokens: list[str],
    passthrough_tokens: list[str],
    feed_token: str | None,
    state: ModalState,
    emission_state: EmissionState,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
) -> dict[str, Any]:
    if command == "G0":
        start_point, end_point = _validate_safe_g0(
            line_number=line.line_number,
            progress=0.0,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
        )
        return _emit_linear_points(
            line=line,
            command=command,
            prefix_tokens=prefix_tokens,
            passthrough_tokens=passthrough_tokens,
            points=[end_point],
            previous=start_point,
            emitted_previous=emission_state.last_emitted_point,
            state=state,
            include_feed=feed_token,
            approx_error_mm=0.0,
            fused_count=0,
        )

    polyline = _adaptive_linear_polyline(
        line_number=line.line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
    )
    simplified, fused_count = _simplify_linear_polyline(
        points=polyline,
        line_number=line.line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
    )
    max_error = _revalidate_linear_polyline(
        line_number=line.line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        points=simplified,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
    )
    if passthrough_tokens and len(simplified) > 2:
        raise ApplicationError(
            f"adaptive_fast no puede subdividir la línea {line.line_number} porque mezcla movimiento con efectos laterales: {' '.join(passthrough_tokens)}."
        )
    return _emit_linear_points(
        line=line,
        command=command,
        prefix_tokens=prefix_tokens,
        passthrough_tokens=passthrough_tokens,
        points=simplified[1:],
        previous=simplified[0],
        emitted_previous=emission_state.last_emitted_point,
        state=state,
        include_feed=feed_token,
        approx_error_mm=max_error,
        fused_count=fused_count,
    )


def _adaptive_linear_polyline(
    *,
    line_number: int,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> list[AdaptivePoint]:
    polyline: list[AdaptivePoint] = []
    for left_progress, right_progress, uses_surface_map in _surface_mode_spans(start_z, target_z):
        left = _evaluate_linear_point(
            progress=left_progress,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
            force_uses_surface_map=uses_surface_map,
        )
        if not polyline or not _same_point(polyline[-1], left):
            polyline.append(left)
        right = _evaluate_linear_point(
            progress=right_progress,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
            force_uses_surface_map=uses_surface_map,
        )
        _split_linear_span(
            sink=polyline,
            left=polyline[-1],
            right=right,
            depth=0,
            line_number=line_number,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
            surface_mode=uses_surface_map,
        )
    return polyline


def _split_linear_span(
    *,
    sink: list[AdaptivePoint],
    left: AdaptivePoint,
    right: AdaptivePoint,
    depth: int,
    line_number: int,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
    surface_mode: bool,
) -> None:
    validation = _validate_linear_span(
        line_number=line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        left=left,
        right=right,
        height_map=height_map,
        reference_frame=reference_frame,
        extra_progresses=(),
        surface_mode=surface_mode,
    )
    if validation["max_error_mm"] <= max_z_error_mm:
        sink.append(right)
        return
    xy_length = math.dist((left.pcb_x_mm, left.pcb_y_mm), (right.pcb_x_mm, right.pcb_y_mm))
    if xy_length <= min_segment_length_mm or depth >= MAX_RECURSION_DEPTH:
        raise ApplicationError(
            f"adaptive_fast no puede cumplir la tolerancia en la línea {line_number}: error real {validation['max_error_mm']:.6f} mm "
            f"al alcanzar {'longitud mínima' if xy_length <= min_segment_length_mm else 'profundidad máxima de recursión'}."
        )
    midpoint = _evaluate_linear_point(
        progress=(left.progress + right.progress) / 2.0,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
        force_uses_surface_map=surface_mode,
    )
    _split_linear_span(
        sink=sink,
        left=left,
        right=midpoint,
        depth=depth + 1,
        line_number=line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
        surface_mode=surface_mode,
    )
    _split_linear_span(
        sink=sink,
        left=midpoint,
        right=right,
        depth=depth + 1,
        line_number=line_number,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
        surface_mode=surface_mode,
    )


def _validate_linear_span(
    *,
    line_number: int,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    left: AdaptivePoint,
    right: AdaptivePoint,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    extra_progresses: tuple[float, ...] | list[float],
    surface_mode: bool,
) -> dict[str, Any]:
    if left.uses_surface_map != right.uses_surface_map:
        return {"max_error_mm": 0.0, "worst_progress": 0.5}
    checkpoints = {0.0, 1.0}
    checkpoints.update(_linear_grid_crossing_params(left, right, height_map))
    for progress in extra_progresses:
        if left.progress < float(progress) < right.progress:
            local_progress = (float(progress) - left.progress) / max(1e-12, right.progress - left.progress)
            checkpoints.add(local_progress)
    max_error = 0.0
    worst_progress = 0.5
    boundaries = _unique_progress([value for value in checkpoints if 0.0 <= value <= 1.0])
    for left_local, right_local in zip(boundaries, boundaries[1:]):
        for local_progress in _interval_sample_points(left_local, right_local):
            global_progress = _lerp(left.progress, right.progress, local_progress)
            point = _evaluate_linear_point(
                progress=global_progress,
                start_x=start_x,
                start_y=start_y,
                start_z=start_z,
                target_x=target_x,
                target_y=target_y,
                target_z=target_z,
                height_map=height_map,
                reference_frame=reference_frame,
                line_number=line_number,
                force_uses_surface_map=surface_mode,
            )
            expected_machine_z = _lerp(left.machine_z_mm, right.machine_z_mm, local_progress)
            error = abs(point.machine_z_mm - expected_machine_z)
            if error > max_error:
                max_error = error
                worst_progress = local_progress
    return {"max_error_mm": max_error, "worst_progress": worst_progress}


def _linear_grid_crossing_params(left: AdaptivePoint, right: AdaptivePoint, height_map: HeightMap) -> list[float]:
    values: set[float] = set()
    values.update(_axis_crossing_params(left.pcb_x_mm, right.pcb_x_mm, height_map.probe_region.min_x_mm, height_map.grid.paso_x_mm))
    values.update(_axis_crossing_params(left.pcb_y_mm, right.pcb_y_mm, height_map.probe_region.min_y_mm, height_map.grid.paso_y_mm))
    return sorted(value for value in values if 0.0 < value < 1.0)


def _axis_crossing_params(start_value: float, end_value: float, base_value: float, step_mm: float) -> list[float]:
    if step_mm <= 0 or math.isclose(start_value, end_value, abs_tol=1e-12):
        return []
    lower = min(start_value, end_value)
    upper = max(start_value, end_value)
    start_index = math.floor((lower - base_value) / step_mm)
    end_index = math.ceil((upper - base_value) / step_mm)
    result: list[float] = []
    for index in range(start_index, end_index + 1):
        crossing = base_value + index * step_mm
        if crossing <= lower or crossing >= upper:
            continue
        progress = (crossing - start_value) / (end_value - start_value)
        if 0.0 < progress < 1.0:
            result.append(progress)
    return result


def _simplify_polyline_globally(
    *,
    points: list[AdaptivePoint],
    validator,
    max_z_error_mm: float,
) -> tuple[list[AdaptivePoint], int]:
    if len(points) <= 2:
        return points, 0

    def recurse(left_index: int, right_index: int) -> list[AdaptivePoint]:
        extras = tuple(point.progress for point in points[left_index + 1:right_index])
        validation = validator(points[left_index], points[right_index], extras)
        if validation["max_error_mm"] <= max_z_error_mm:
            return [points[left_index], points[right_index]]
        if right_index - left_index <= 1:
            return [points[left_index], points[right_index]]
        worst_index = max(
            range(left_index + 1, right_index),
            key=lambda index: abs(
                points[index].machine_z_mm
                - _lerp(
                    points[left_index].machine_z_mm,
                    points[right_index].machine_z_mm,
                    (points[index].progress - points[left_index].progress)
                    / max(1e-12, points[right_index].progress - points[left_index].progress),
                )
            ),
        )
        left = recurse(left_index, worst_index)
        right = recurse(worst_index, right_index)
        return left[:-1] + right

    simplified = recurse(0, len(points) - 1)
    removed = max(0, len(points) - len(simplified))
    return simplified, removed


def _simplify_linear_polyline(
    *,
    points: list[AdaptivePoint],
    line_number: int,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
) -> tuple[list[AdaptivePoint], int]:
    if len(points) <= 2:
        return points, 0
    regions: list[list[AdaptivePoint]] = []
    current = [points[0]]
    for point in points[1:]:
        if point.uses_surface_map != current[-1].uses_surface_map:
            regions.append(current)
            current = [point]
            continue
        current.append(point)
    regions.append(current)

    simplified_regions: list[list[AdaptivePoint]] = []
    removed_total = 0
    for region in regions:
        if len(region) <= 2:
            simplified_regions.append(region)
            continue
        simplified, removed = _simplify_polyline_globally(
            points=region,
            validator=lambda left, right, extras, surface_mode=region[0].uses_surface_map: _validate_linear_span(
                line_number=line_number,
                start_x=start_x,
                start_y=start_y,
                start_z=start_z,
                target_x=target_x,
                target_y=target_y,
                target_z=target_z,
                left=left,
                right=right,
                height_map=height_map,
                reference_frame=reference_frame,
                extra_progresses=extras,
                surface_mode=surface_mode,
            ),
            max_z_error_mm=max_z_error_mm,
        )
        simplified_regions.append(simplified)
        removed_total += removed

    merged: list[AdaptivePoint] = []
    for region in simplified_regions:
        if not region:
            continue
        if not merged:
            merged.extend(region)
            continue
        if _same_point(merged[-1], region[0]):
            merged.extend(region[1:])
        else:
            merged.extend(region)
    return merged, removed_total


def _revalidate_linear_polyline(
    *,
    line_number: int,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    points: list[AdaptivePoint],
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
) -> float:
    max_error = 0.0
    for left, right in zip(points, points[1:]):
        validation = _validate_linear_span(
            line_number=line_number,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            left=left,
            right=right,
            height_map=height_map,
            reference_frame=reference_frame,
            extra_progresses=(),
            surface_mode=_segment_surface_mode(left, right),
        )
        if validation["max_error_mm"] > max_z_error_mm:
            raise ApplicationError(
                f"adaptive_fast revalidó la línea {line_number} y encontró error real {validation['max_error_mm']:.6f} mm por encima de la tolerancia."
            )
        max_error = max(max_error, validation["max_error_mm"])
    return max_error


def _transform_arc_motion(
    *,
    line: GCodeLine,
    command: str,
    prefix_tokens: list[str],
    passthrough_tokens: list[str],
    feed_token: str | None,
    state: ModalState,
    emission_state: EmissionState,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    arc_params_mm: dict[str, float],
    plane: str,
) -> dict[str, Any]:
    if plane != "G17":
        raise ApplicationError(f"adaptive_fast no soporta arcos fuera del plano G17 en la línea {line.line_number}.")
    if "R" in arc_params_mm:
        raise ApplicationError(f"adaptive_fast bloquea arcos con parámetro R en la línea {line.line_number}.")
    if "K" in arc_params_mm:
        raise ApplicationError(f"adaptive_fast bloquea arcos con parámetro K en la línea {line.line_number}.")
    if "I" not in arc_params_mm or "J" not in arc_params_mm:
        raise ApplicationError(f"adaptive_fast requiere I/J para compensar el arco de la línea {line.line_number}.")

    definition = _arc_definition(
        command=command,
        start_x=start_x,
        start_y=start_y,
        target_x=target_x,
        target_y=target_y,
        arc_params_mm=arc_params_mm,
        line_number=line.line_number,
    )
    segments = _adaptive_arc_segments(
        line_number=line.line_number,
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
    )
    if passthrough_tokens and len(segments) > 1:
        raise ApplicationError(
            f"adaptive_fast no puede subdividir el arco {line.line_number} porque mezcla movimiento con efectos laterales: {' '.join(passthrough_tokens)}."
        )
    max_error = _revalidate_arc_segments(
        line_number=line.line_number,
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        segments=segments,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
    )
    return _emit_arc_segments(
        line=line,
        command=command,
        prefix_tokens=prefix_tokens,
        passthrough_tokens=passthrough_tokens,
        definition=definition,
        segments=segments,
        state=state,
        emitted_previous=emission_state.last_emitted_point,
        include_feed=feed_token,
        max_error=max_error,
    )


def _adaptive_arc_segments(
    *,
    line_number: int,
    definition: ArcDefinition,
    start_z: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> list[tuple[AdaptivePoint, AdaptivePoint]]:
    segments: list[tuple[AdaptivePoint, AdaptivePoint]] = []
    for left_progress, right_progress, uses_surface_map in _surface_mode_spans(start_z, target_z):
        left = _evaluate_arc_point(
            definition=definition,
            progress=left_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
            force_uses_surface_map=uses_surface_map,
        )
        right = _evaluate_arc_point(
            definition=definition,
            progress=right_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
            force_uses_surface_map=uses_surface_map,
        )
        _split_arc_span(
            sink=segments,
            left=left,
            right=right,
            depth=0,
            line_number=line_number,
            definition=definition,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
            surface_mode=uses_surface_map,
        )
    return segments


def _split_arc_span(
    *,
    sink: list[tuple[AdaptivePoint, AdaptivePoint]],
    left: AdaptivePoint,
    right: AdaptivePoint,
    depth: int,
    line_number: int,
    definition: ArcDefinition,
    start_z: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
    surface_mode: bool,
) -> None:
    validation = _validate_arc_span(
        line_number=line_number,
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        left=left,
        right=right,
        height_map=height_map,
        reference_frame=reference_frame,
        surface_mode=surface_mode,
    )
    if validation["max_error_mm"] <= max_z_error_mm:
        sink.append((left, right))
        return
    local_sweep = abs(definition.sweep_angle) * max(1e-12, right.progress - left.progress)
    arc_length = local_sweep * definition.radius_mm
    if arc_length <= min_segment_length_mm or depth >= MAX_RECURSION_DEPTH:
        raise ApplicationError(
            f"adaptive_fast no puede cumplir la tolerancia en el arco de la línea {line_number}: error real {validation['max_error_mm']:.6f} mm "
            f"al alcanzar {'longitud mínima' if arc_length <= min_segment_length_mm else 'profundidad máxima de recursión'}."
        )
    midpoint = _evaluate_arc_point(
        definition=definition,
        progress=(left.progress + right.progress) / 2.0,
        start_z=start_z,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
        force_uses_surface_map=surface_mode,
    )
    _split_arc_span(
        sink=sink,
        left=left,
        right=midpoint,
        depth=depth + 1,
        line_number=line_number,
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
        surface_mode=surface_mode,
    )
    _split_arc_span(
        sink=sink,
        left=midpoint,
        right=right,
        depth=depth + 1,
        line_number=line_number,
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
        surface_mode=surface_mode,
    )


def _validate_arc_span(
    *,
    line_number: int,
    definition: ArcDefinition,
    start_z: float,
    target_z: float,
    left: AdaptivePoint,
    right: AdaptivePoint,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    surface_mode: bool,
) -> dict[str, Any]:
    if left.uses_surface_map != right.uses_surface_map:
        return {"max_error_mm": 0.0, "worst_progress": 0.5}
    max_error = 0.0
    worst_progress = 0.5
    checkpoints = {0.0, 1.0}
    checkpoints.update(_arc_grid_crossing_params(left, right, definition, height_map))
    boundaries = _unique_progress([value for value in checkpoints if 0.0 <= value <= 1.0])
    for left_local, right_local in zip(boundaries, boundaries[1:]):
        for local_progress in _interval_sample_points(left_local, right_local):
            global_progress = _lerp(left.progress, right.progress, local_progress)
            point = _evaluate_arc_point(
                definition=definition,
                progress=global_progress,
                start_z=start_z,
                target_z=target_z,
                height_map=height_map,
                reference_frame=reference_frame,
                line_number=line_number,
                force_uses_surface_map=surface_mode,
            )
            expected_machine_z = _lerp(left.machine_z_mm, right.machine_z_mm, local_progress)
            error = abs(point.machine_z_mm - expected_machine_z)
            if error > max_error:
                max_error = error
                worst_progress = local_progress
    return {"max_error_mm": max_error, "worst_progress": worst_progress}


def _revalidate_arc_segments(
    *,
    line_number: int,
    definition: ArcDefinition,
    start_z: float,
    target_z: float,
    segments: list[tuple[AdaptivePoint, AdaptivePoint]],
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
) -> float:
    max_error = 0.0
    for left, right in segments:
        validation = _validate_arc_span(
            line_number=line_number,
            definition=definition,
            start_z=start_z,
            target_z=target_z,
            left=left,
            right=right,
            height_map=height_map,
            reference_frame=reference_frame,
            surface_mode=_segment_surface_mode(left, right),
        )
        if validation["max_error_mm"] > max_z_error_mm:
            raise ApplicationError(
                f"adaptive_fast revalidó el arco de la línea {line_number} y encontró error real {validation['max_error_mm']:.6f} mm por encima de la tolerancia."
            )
        max_error = max(max_error, validation["max_error_mm"])
    return max_error


def _emit_linear_points(
    *,
    line: GCodeLine,
    command: str,
    prefix_tokens: list[str],
    passthrough_tokens: list[str],
    points: list[AdaptivePoint],
    previous: AdaptivePoint,
    emitted_previous: AdaptivePoint | None,
    state: ModalState,
    include_feed: str | None,
    approx_error_mm: float,
    fused_count: int,
) -> dict[str, Any]:
    emitted_lines: list[str] = []
    trace: list[dict[str, Any]] = []
    delta_values: list[float] = []
    z_values: list[float] = []
    current_previous = emitted_previous or previous
    emitted_moves = len(points)
    for index, point in enumerate(points):
        line_text = _format_motion_line(
            command=command,
            prefix_tokens=prefix_tokens if index == 0 else [],
            point=point,
            previous=current_previous,
            state=state,
            include_feed=include_feed if index == 0 else None,
        )
        if index == 0 and passthrough_tokens:
            line_text = f"{line_text} {' '.join(passthrough_tokens)}"
        if index == 0:
            line_text = _append_comment(line_text, line.comment)
        emitted_lines.append(line_text)
        trace.append(_trace_entry(line, command, point, approx_error_mm=approx_error_mm))
        delta_values.append(point.delta_z_mm)
        z_values.append(point.machine_z_mm)
        current_previous = point
    return _result(
        emitted_lines,
        trace=trace,
        delta_values=delta_values,
        z_values=z_values,
        emitted_moves=emitted_moves,
        segments_subdivided=max(0, emitted_moves - 1),
        segments_fused=fused_count,
        max_approximation_error_mm=approx_error_mm,
    ) | {"last_emitted_point": current_previous if points else emitted_previous}


def _emit_arc_segments(
    *,
    line: GCodeLine,
    command: str,
    prefix_tokens: list[str],
    passthrough_tokens: list[str],
    definition: ArcDefinition,
    segments: list[tuple[AdaptivePoint, AdaptivePoint]],
    state: ModalState,
    emitted_previous: AdaptivePoint | None,
    include_feed: str | None,
    max_error: float,
) -> dict[str, Any]:
    emitted_lines: list[str] = []
    trace: list[dict[str, Any]] = []
    delta_values: list[float] = []
    z_values: list[float] = []
    emitted_moves = len(segments)
    current_previous = emitted_previous
    for index, (start_point, end_point) in enumerate(segments):
        line_text = _format_arc_line(
            command=command,
            prefix_tokens=prefix_tokens if index == 0 else [],
            point=end_point,
            previous=current_previous or start_point,
            state=state,
            i_mm=definition.center_x_mm - start_point.pcb_x_mm,
            j_mm=definition.center_y_mm - start_point.pcb_y_mm,
            center_x_mm=definition.center_x_mm,
            center_y_mm=definition.center_y_mm,
            include_feed=include_feed if index == 0 else None,
        )
        if index == 0 and passthrough_tokens:
            line_text = f"{line_text} {' '.join(passthrough_tokens)}"
        if index == 0:
            line_text = _append_comment(line_text, line.comment)
        emitted_lines.append(line_text)
        trace.append(_trace_entry(line, command, end_point, approx_error_mm=max_error))
        delta_values.append(end_point.delta_z_mm)
        z_values.append(end_point.machine_z_mm)
        current_previous = end_point
    return _result(
        emitted_lines,
        trace=trace,
        delta_values=delta_values,
        z_values=z_values,
        emitted_moves=emitted_moves,
        segments_subdivided=max(0, emitted_moves - 1),
        segments_fused=0,
        max_approximation_error_mm=max_error,
    ) | {"last_emitted_point": current_previous}


def _format_motion_line(
    *,
    command: str,
    prefix_tokens: list[str],
    point: AdaptivePoint,
    previous: AdaptivePoint,
    state: ModalState,
    include_feed: str | None,
) -> str:
    parts = [*prefix_tokens, command]
    if state.positioning == "absolute":
        parts.extend(
            [
                _axis_text("X", point.machine_x_mm, state.units),
                _axis_text("Y", point.machine_y_mm, state.units),
                _axis_text("Z", point.machine_z_mm, state.units),
            ]
        )
    else:
        parts.extend(
            [
                _axis_text("X", point.machine_x_mm - previous.machine_x_mm, state.units),
                _axis_text("Y", point.machine_y_mm - previous.machine_y_mm, state.units),
                _axis_text("Z", point.machine_z_mm - previous.machine_z_mm, state.units),
            ]
        )
    if include_feed is not None:
        parts.append(f"F{include_feed}")
    return " ".join(parts)


def _format_arc_line(
    *,
    command: str,
    prefix_tokens: list[str],
    point: AdaptivePoint,
    previous: AdaptivePoint,
    state: ModalState,
    i_mm: float,
    j_mm: float,
    center_x_mm: float | None,
    center_y_mm: float | None,
    include_feed: str | None,
) -> str:
    del center_x_mm, center_y_mm
    parts = [*prefix_tokens, command]
    if state.positioning == "absolute":
        parts.extend(
            [
                _axis_text("X", point.machine_x_mm, state.units),
                _axis_text("Y", point.machine_y_mm, state.units),
                _axis_text("Z", point.machine_z_mm, state.units),
            ]
        )
    else:
        parts.extend(
            [
                _axis_text("X", point.machine_x_mm - previous.machine_x_mm, state.units),
                _axis_text("Y", point.machine_y_mm - previous.machine_y_mm, state.units),
                _axis_text("Z", point.machine_z_mm - previous.machine_z_mm, state.units),
            ]
        )
    parts.append(_axis_text("I", i_mm, state.units))
    parts.append(_axis_text("J", j_mm, state.units))
    if include_feed is not None:
        parts.append(f"F{include_feed}")
    return " ".join(parts)


def _evaluate_linear_point(
    *,
    progress: float,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    line_number: int,
    force_uses_surface_map: bool | None = None,
) -> AdaptivePoint:
    effective_force = _effective_surface_mode(progress, start_z, target_z, force_uses_surface_map)
    return _evaluate_point(
        pcb_x_mm=_lerp(start_x, target_x, progress),
        pcb_y_mm=_lerp(start_y, target_y, progress),
        programmed_z_mm=_lerp(start_z, target_z, progress),
        progress=progress,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
        force_uses_surface_map=effective_force,
    )


def _evaluate_arc_point(
    *,
    definition: ArcDefinition,
    progress: float,
    start_z: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    line_number: int,
    force_uses_surface_map: bool | None = None,
) -> AdaptivePoint:
    angle = definition.start_angle + definition.sweep_angle * progress
    effective_force = _effective_surface_mode(progress, start_z, target_z, force_uses_surface_map)
    pcb_x_mm = definition.center_x_mm + math.cos(angle) * definition.radius_mm
    pcb_y_mm = definition.center_y_mm + math.sin(angle) * definition.radius_mm
    return _evaluate_point(
        pcb_x_mm=pcb_x_mm,
        pcb_y_mm=pcb_y_mm,
        programmed_z_mm=_lerp(start_z, target_z, progress),
        progress=progress,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
        force_uses_surface_map=effective_force,
    )


def _evaluate_point(
    *,
    pcb_x_mm: float,
    pcb_y_mm: float,
    programmed_z_mm: float,
    progress: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    line_number: int,
    force_uses_surface_map: bool | None = None,
) -> AdaptivePoint:
    uses_surface_map = programmed_z_mm < SURFACE_THRESHOLD_Z_MM if force_uses_surface_map is None else force_uses_surface_map
    delta_z_mm = 0.0
    if uses_surface_map:
        interpolation = interpolate_height(height_map, x_mm=pcb_x_mm, y_mm=pcb_y_mm, mode="bruto")
        if interpolation.valor_mm is None:
            raise ApplicationError(f"No se puede compensar la línea {line_number}: mapa sin valor en X={pcb_x_mm:.3f}, Y={pcb_y_mm:.3f}.")
        domain = check_domain(height_map, pcb_x_mm, pcb_y_mm)
        if not domain.inside:
            raise ApplicationError(
                f"Punto fuera de la cobertura del mapa en línea {line_number}: X={pcb_x_mm:.3f}, Y={pcb_y_mm:.3f}, motivo={domain.reason}."
            )
        delta_z_mm = float(interpolation.valor_mm)
    machine_z_mm = reference_frame.surface_reference_z_mm + programmed_z_mm + delta_z_mm
    return AdaptivePoint(
        progress=progress,
        pcb_x_mm=pcb_x_mm,
        pcb_y_mm=pcb_y_mm,
        programmed_z_mm=programmed_z_mm,
        machine_x_mm=reference_frame.machine_origin_x_mm + pcb_x_mm,
        machine_y_mm=reference_frame.machine_origin_y_mm + pcb_y_mm,
        machine_z_mm=machine_z_mm,
        delta_z_mm=delta_z_mm,
        uses_surface_map=uses_surface_map,
    )


def _arc_definition(
    *,
    command: str,
    start_x: float,
    start_y: float,
    target_x: float,
    target_y: float,
    arc_params_mm: dict[str, float],
    line_number: int,
) -> ArcDefinition:
    center_x = start_x + arc_params_mm.get("I", 0.0)
    center_y = start_y + arc_params_mm.get("J", 0.0)
    radius_start = math.dist((start_x, start_y), (center_x, center_y))
    radius_end = math.dist((target_x, target_y), (center_x, center_y))
    if radius_start <= FULL_CIRCLE_EPSILON:
        raise ApplicationError(f"adaptive_fast detectó radio nulo en el arco de la línea {line_number}.")
    if abs(radius_start - radius_end) > max(0.05, radius_start * 0.01):
        raise ApplicationError(f"adaptive_fast detectó un arco inconsistente en la línea {line_number}.")
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(target_y - center_y, target_x - center_x)
    full_circle = math.isclose(start_x, target_x, abs_tol=FULL_CIRCLE_EPSILON) and math.isclose(start_y, target_y, abs_tol=FULL_CIRCLE_EPSILON)
    if full_circle:
        sweep_angle = -2 * math.pi if command == "G2" else 2 * math.pi
    else:
        sweep_angle = end_angle - start_angle
        if command == "G2" and sweep_angle >= 0:
            sweep_angle -= 2 * math.pi
        if command == "G3" and sweep_angle <= 0:
            sweep_angle += 2 * math.pi
    return ArcDefinition(
        command=command,
        center_x_mm=center_x,
        center_y_mm=center_y,
        radius_mm=radius_start,
        start_angle=start_angle,
        sweep_angle=sweep_angle,
    )


def _trace_entry(line: GCodeLine, command: str, point: AdaptivePoint, *, approx_error_mm: float) -> dict[str, Any]:
    return {
        "plan_index": 0,
        "line_number": line.line_number,
        "motion": command,
        "movement_type": command,
        "pcb_x_mm": point.pcb_x_mm,
        "pcb_y_mm": point.pcb_y_mm,
        "machine_x_mm": point.machine_x_mm,
        "machine_y_mm": point.machine_y_mm,
        "programmed_z_mm": point.programmed_z_mm,
        "delta_z_mm": point.delta_z_mm,
        "final_z_mm": point.machine_z_mm,
        "feed_mm_min": None,
        "uses_surface_map": point.uses_surface_map,
        "approximation_error_mm": approx_error_mm,
    }


def _looks_like_macro_line(code: str) -> bool:
    first = code.split()[0]
    return bool("_" in first and MACRO_WORD_RE.match(first))


def _normalize_g_command(raw_value: str | None) -> str:
    value = _numeric(raw_value)
    if float(value).is_integer():
        return f"G{int(value)}"
    return f"G{value:g}"


def _numeric(raw_value: str | None) -> float:
    if raw_value is None:
        raise ApplicationError("Se esperaba un valor numérico en G-code.")
    return float(raw_value)


def _to_mm(value: float, units: str) -> float:
    return value if units == "mm" else value * 25.4


def _from_mm(value: float, units: str) -> float:
    return value if units == "mm" else value / 25.4


def _resolve_target_value(current_value: float, raw_value: float | None, positioning: str) -> float:
    if raw_value is None:
        return current_value
    if positioning == "absolute":
        return raw_value
    return current_value + raw_value


def _axis_text(letter: str, value_mm: float, units: str) -> str:
    value = _from_mm(value_mm, units)
    return f"{letter}{value:.5f}".rstrip("0").rstrip(".")


def _append_comment(line: str, comment: str | None) -> str:
    if not comment:
        return line
    return f"{line} ; {comment}"


def _lerp(left: float, right: float, progress: float) -> float:
    return left + (right - left) * progress


def _surface_mode_spans(start_z: float, target_z: float) -> list[tuple[float, float, bool]]:
    if math.isclose(start_z, target_z, abs_tol=1e-12):
        return [(0.0, 1.0, start_z < SURFACE_THRESHOLD_Z_MM)]
    crossing = (SURFACE_THRESHOLD_Z_MM - start_z) / (target_z - start_z)
    spans = [(0.0, 1.0)]
    if 0.0 < crossing < 1.0:
        spans = [(0.0, crossing), (crossing, 1.0)]
    result: list[tuple[float, float, bool]] = []
    for left, right in spans:
        midpoint = (left + right) / 2.0
        uses_surface_map = _lerp(start_z, target_z, midpoint) < SURFACE_THRESHOLD_Z_MM
        result.append((left, right, uses_surface_map))
    return result


def _surface_transition_progress(start_z: float, target_z: float) -> float | None:
    if math.isclose(start_z, target_z, abs_tol=1e-12):
        return None
    crossing = (SURFACE_THRESHOLD_Z_MM - start_z) / (target_z - start_z)
    if 0.0 < crossing < 1.0:
        return crossing
    return None


def _effective_surface_mode(progress: float, start_z: float, target_z: float, force_uses_surface_map: bool | None) -> bool | None:
    crossing = _surface_transition_progress(start_z, target_z)
    if crossing is None or force_uses_surface_map is None:
        return force_uses_surface_map
    if math.isclose(progress, crossing, abs_tol=1e-9):
        return None
    return force_uses_surface_map


def _segment_surface_mode(left: AdaptivePoint, right: AdaptivePoint) -> bool:
    return left.uses_surface_map or right.uses_surface_map


def _interval_sample_points(left_local: float, right_local: float) -> list[float]:
    if not right_local - left_local > 1e-12:
        return []
    return [
        _lerp(left_local, right_local, factor)
        for factor in SPAN_SAMPLE_FACTORS
        if left_local < _lerp(left_local, right_local, factor) < right_local
    ]


def _validate_safe_g0(
    *,
    line_number: int,
    progress: float,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
) -> tuple[AdaptivePoint, AdaptivePoint]:
    del progress
    start_point = _evaluate_linear_point(
        progress=0.0,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
    )
    end_point = _evaluate_linear_point(
        progress=1.0,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        line_number=line_number,
    )
    x_moves = not math.isclose(start_x, target_x, abs_tol=1e-9)
    y_moves = not math.isclose(start_y, target_y, abs_tol=1e-9)
    z_moves = not math.isclose(start_z, target_z, abs_tol=1e-9)
    xy_moves = x_moves or y_moves

    if xy_moves and not z_moves:
        if start_z < SURFACE_THRESHOLD_Z_MM or target_z < SURFACE_THRESHOLD_Z_MM:
            raise ApplicationError(f"L{line_number}: G0 bloqueado: XY rápido bajo Z=0 no es seguro.")
        return start_point, end_point
    if z_moves and not xy_moves:
        if target_z < start_z - 1e-9 or target_z < SURFACE_THRESHOLD_Z_MM:
            raise ApplicationError(f"L{line_number}: G0 bloqueado: plunge rápido hacia zona de corte no permitido.")
        return start_point, end_point
    if xy_moves and z_moves:
        if min(start_z, target_z) < SURFACE_THRESHOLD_Z_MM or (start_z - SURFACE_THRESHOLD_Z_MM) * (target_z - SURFACE_THRESHOLD_Z_MM) <= 0:
            raise ApplicationError(f"L{line_number}: G0 bloqueado: diagonal rápida que atraviesa la superficie.")
        raise ApplicationError(f"L{line_number}: G0 bloqueado: solo se permite XY seguro o retracción vertical.")
    return start_point, end_point


def _arc_grid_crossing_params(
    left: AdaptivePoint,
    right: AdaptivePoint,
    definition: ArcDefinition,
    height_map: HeightMap,
) -> list[float]:
    values: set[float] = set()
    for crossing_x in _grid_axis_values(height_map.probe_region.min_x_mm, height_map.grid.paso_x_mm, definition.center_x_mm - definition.radius_mm, definition.center_x_mm + definition.radius_mm):
        ratio = (crossing_x - definition.center_x_mm) / max(definition.radius_mm, FULL_CIRCLE_EPSILON)
        if abs(ratio) > 1.0 + 1e-9:
            continue
        ratio = min(1.0, max(-1.0, ratio))
        angle = math.acos(ratio)
        for candidate in (angle, -angle):
            progress = _arc_progress_for_angle(definition, candidate)
            if progress is not None and left.progress < progress < right.progress:
                values.add((progress - left.progress) / max(1e-12, right.progress - left.progress))
    for crossing_y in _grid_axis_values(height_map.probe_region.min_y_mm, height_map.grid.paso_y_mm, definition.center_y_mm - definition.radius_mm, definition.center_y_mm + definition.radius_mm):
        ratio = (crossing_y - definition.center_y_mm) / max(definition.radius_mm, FULL_CIRCLE_EPSILON)
        if abs(ratio) > 1.0 + 1e-9:
            continue
        ratio = min(1.0, max(-1.0, ratio))
        angle = math.asin(ratio)
        for candidate in (angle, math.pi - angle):
            progress = _arc_progress_for_angle(definition, candidate)
            if progress is not None and left.progress < progress < right.progress:
                values.add((progress - left.progress) / max(1e-12, right.progress - left.progress))
    return sorted(value for value in values if 0.0 < value < 1.0)


def _grid_axis_values(base_value: float, step_mm: float, lower: float, upper: float) -> list[float]:
    if step_mm <= 0:
        return []
    start_index = math.floor((lower - base_value) / step_mm)
    end_index = math.ceil((upper - base_value) / step_mm)
    return [base_value + index * step_mm for index in range(start_index, end_index + 1)]


def _arc_progress_for_angle(definition: ArcDefinition, angle: float) -> float | None:
    sweep_abs = abs(definition.sweep_angle)
    if sweep_abs <= FULL_CIRCLE_EPSILON:
        return None
    angle = _normalize_angle(angle)
    start_angle = _normalize_angle(definition.start_angle)
    if definition.command == "G3":
        delta = angle - start_angle
    else:
        delta = start_angle - angle
    while delta < -1e-9:
        delta += 2.0 * math.pi
    while delta > sweep_abs + 1e-9:
        delta -= 2.0 * math.pi
    if delta < -1e-6 or delta > sweep_abs + 1e-6:
        return None
    return min(max(delta / sweep_abs, 0.0), 1.0)


def _normalize_angle(angle: float) -> float:
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    while angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def _same_point(left: AdaptivePoint, right: AdaptivePoint) -> bool:
    return (
        math.isclose(left.progress, right.progress, abs_tol=1e-9)
        and math.isclose(left.machine_x_mm, right.machine_x_mm, abs_tol=1e-9)
        and math.isclose(left.machine_y_mm, right.machine_y_mm, abs_tol=1e-9)
        and math.isclose(left.machine_z_mm, right.machine_z_mm, abs_tol=1e-9)
        and left.uses_surface_map == right.uses_surface_map
    )


def _rms(values: list[float]) -> float | None:
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _unique_progress(values: list[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or not math.isclose(result[-1], value, abs_tol=1e-9):
            result.append(value)
    return result


def _result(
    lines: list[str],
    *,
    plane: str = "G17",
    trace: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    unsupported_commands: list[str] | None = None,
    outside_points: list[dict[str, Any]] | None = None,
    delta_values: list[float] | None = None,
    z_values: list[float] | None = None,
    emitted_moves: int = 0,
    segments_subdivided: int = 0,
    segments_fused: int = 0,
    max_approximation_error_mm: float = 0.0,
) -> dict[str, Any]:
    return {
        "lines": lines,
        "plane": plane,
        "trace": trace or [],
        "warnings": warnings or [],
        "unsupported_commands": unsupported_commands or [],
        "outside_points": outside_points or [],
        "delta_values": delta_values or [],
        "z_values": z_values or [],
        "emitted_moves": emitted_moves,
        "segments_subdivided": segments_subdivided,
        "segments_fused": segments_fused,
        "max_approximation_error_mm": max_approximation_error_mm,
    }
