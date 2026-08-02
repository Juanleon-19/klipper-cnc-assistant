from __future__ import annotations

import math
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


@dataclass(frozen=True)
class ArcDefinition:
    clockwise: bool
    center_x_mm: float
    center_y_mm: float
    radius_mm: float
    start_angle: float
    sweep_angle: float
    full_circle: bool


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
        detail = ", ".join(unsupported_commands[:6])
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
    plane: str,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> dict[str, Any]:
    if not line.tokens:
        return _result([line.raw], plane=plane)

    motion_command = state.active_motion
    explicit_motion: str | None = None
    axes_mm: dict[str, float] = {}
    arc_params_mm: dict[str, float] = {}
    feed_token: str | None = None
    passthrough_tokens: list[str] = []
    unsupported_commands: list[str] = []

    next_plane = plane
    for token in line.tokens:
        if token.letter == "G":
            command = _normalize_g_command(token.raw_value)
            if command in {"G0", "G1", "G2", "G3"}:
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
            elif command in {"G17", "G18", "G19"}:
                next_plane = command
            elif command == "G94":
                state.feed_mode = "units_per_minute"
            elif command == "G4":
                passthrough_tokens.append(command)
            else:
                passthrough_tokens.append(command)
        elif token.letter in {"X", "Y", "Z"}:
            axes_mm[token.letter] = _to_mm(_numeric(token.raw_value), state.units)
        elif token.letter in {"I", "J", "K", "R"}:
            arc_params_mm[token.letter] = _to_mm(_numeric(token.raw_value), state.units)
        elif token.letter == "F":
            feed_mm_min = _to_mm(_numeric(token.raw_value), state.units)
            state.feed_mm_min = feed_mm_min
            feed_token = token.raw_value
        else:
            passthrough_tokens.append(token.command)

    if motion_command is None:
        return _result([line.raw], plane=next_plane)
    if motion_command in {"G0", "G1"} and not axes_mm and explicit_motion is not None:
        return _result([line.raw], plane=next_plane)
    if motion_command in {"G2", "G3"} and not axes_mm and not arc_params_mm and explicit_motion is not None:
        return _result([line.raw], plane=next_plane)

    start_x = state.x_mm
    start_y = state.y_mm
    start_z = state.z_mm
    target_x = _resolve_target_value(state.x_mm, axes_mm.get("X"), state.positioning)
    target_y = _resolve_target_value(state.y_mm, axes_mm.get("Y"), state.positioning)
    target_z = _resolve_target_value(state.z_mm, axes_mm.get("Z"), state.positioning)

    if motion_command not in {"G0", "G1", "G2", "G3"}:
        state.x_mm = target_x
        state.y_mm = target_y
        state.z_mm = target_z
        return _result([line.raw], plane=next_plane)

    if motion_command in {"G2", "G3"}:
        transformed = _transform_arc_line(
            line=line,
            command=motion_command,
            explicit_motion=explicit_motion,
            start_x=start_x,
            start_y=start_y,
            start_z=start_z,
            target_x=target_x,
            target_y=target_y,
            target_z=target_z,
            arc_params_mm=arc_params_mm,
            passthrough_tokens=passthrough_tokens,
            feed_token=feed_token,
            plane=next_plane,
            state=state,
            height_map=height_map,
            reference_frame=reference_frame,
            max_z_error_mm=max_z_error_mm,
            min_segment_length_mm=min_segment_length_mm,
        )
        state.x_mm = target_x
        state.y_mm = target_y
        state.z_mm = target_z
        transformed["plane"] = next_plane
        return transformed

    transformed = _transform_linear_line(
        line=line,
        command=motion_command,
        explicit_motion=explicit_motion,
        start_x=start_x,
        start_y=start_y,
        start_z=start_z,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        passthrough_tokens=passthrough_tokens,
        feed_token=feed_token,
        state=state,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
    )
    state.x_mm = target_x
    state.y_mm = target_y
    state.z_mm = target_z
    transformed["plane"] = next_plane
    return transformed


def _transform_linear_line(
    *,
    line: GCodeLine,
    command: str,
    explicit_motion: str | None,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    passthrough_tokens: list[str],
    feed_token: str | None,
    state: ModalState,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> dict[str, Any]:
    xy_distance = math.dist((start_x, start_y), (target_x, target_y))
    uses_surface = command == "G1" and xy_distance > 0 and float(target_z) < 0.0
    single_line_only = bool(passthrough_tokens)

    if not uses_surface:
        point = _machine_point(
            pcb_x_mm=target_x,
            pcb_y_mm=target_y,
            programmed_z_mm=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            uses_surface=False,
            line_number=line.line_number,
        )
        if command == "G0" and len(passthrough_tokens) > 0:
            return _result(
                [_format_passthrough_motion_line(
                    command=explicit_motion or command,
                    point=point,
                    previous=_machine_point(
                        pcb_x_mm=start_x,
                        pcb_y_mm=start_y,
                        programmed_z_mm=start_z,
                        height_map=height_map,
                        reference_frame=reference_frame,
                        uses_surface=False,
                        line_number=line.line_number,
                    ),
                    state=state,
                    include_feed=feed_token,
                    passthrough_tokens=passthrough_tokens,
                    comment=line.comment,
                )],
                emitted_moves=1,
                trace=[_trace_entry(line, command, point, uses_surface=False, approx_error_mm=0.0)],
                delta_values=[point.delta_z_mm],
                z_values=[point.machine_z_mm],
            )
        return _emit_linear_points(
            line=line,
            command=explicit_motion or command,
            points=[point],
            previous=_machine_point(
                pcb_x_mm=start_x,
                pcb_y_mm=start_y,
                programmed_z_mm=start_z,
                height_map=height_map,
                reference_frame=reference_frame,
                uses_surface=False,
                line_number=line.line_number,
            ),
            state=state,
            include_feed=feed_token,
            passthrough_tokens=passthrough_tokens,
            approx_error_mm=0.0,
            uses_surface=False,
            subdivided=False,
            fused_count=0,
        )

    polyline, subdivisions, max_error = _adaptive_line_points(
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
        line_number=line.line_number,
    )
    simplified, fused_count = _simplify_line_points(polyline, max_z_error_mm=max_z_error_mm)
    if single_line_only and len(simplified) > 1:
        raise ApplicationError(
            f"adaptive_fast no puede subdividir la línea {line.line_number} porque mezcla movimiento con efectos laterales: {' '.join(passthrough_tokens)}."
        )
    return _emit_linear_points(
        line=line,
        command=explicit_motion or command,
        points=simplified[1:],
        previous=simplified[0],
        state=state,
        include_feed=feed_token,
        passthrough_tokens=passthrough_tokens,
        approx_error_mm=max_error,
        uses_surface=True,
        subdivided=subdivisions > 0,
        fused_count=fused_count,
    )


def _transform_arc_line(
    *,
    line: GCodeLine,
    command: str,
    explicit_motion: str | None,
    start_x: float,
    start_y: float,
    start_z: float,
    target_x: float,
    target_y: float,
    target_z: float,
    arc_params_mm: dict[str, float],
    passthrough_tokens: list[str],
    feed_token: str | None,
    plane: str,
    state: ModalState,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
) -> dict[str, Any]:
    uses_surface = math.dist((start_x, start_y), (target_x, target_y)) > 0 and float(target_z) < 0.0
    if plane != "G17" and uses_surface:
        raise ApplicationError(f"adaptive_fast no soporta compensación de arcos fuera del plano G17 en la línea {line.line_number}.")
    if "R" in arc_params_mm and uses_surface:
        raise ApplicationError(f"adaptive_fast bloquea arcos con parámetro R en la línea {line.line_number}.")
    if "K" in arc_params_mm and uses_surface:
        raise ApplicationError(f"adaptive_fast bloquea arcos con parámetro K en la línea {line.line_number}.")
    if ("I" not in arc_params_mm and "J" not in arc_params_mm) and uses_surface:
        raise ApplicationError(f"adaptive_fast requiere I/J para compensar el arco de la línea {line.line_number}.")

    if not uses_surface:
        point = _machine_point(
            pcb_x_mm=target_x,
            pcb_y_mm=target_y,
            programmed_z_mm=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            uses_surface=False,
            line_number=line.line_number,
        )
        emitted = _emit_arc_segments(
            line=line,
            command=explicit_motion or command,
            arc_segments=[{
                "end": point,
                "i_mm": arc_params_mm.get("I", 0.0),
                "j_mm": arc_params_mm.get("J", 0.0),
                "approx_error_mm": 0.0,
            }],
            start_point=_machine_point(
                pcb_x_mm=start_x,
                pcb_y_mm=start_y,
                programmed_z_mm=start_z,
                height_map=height_map,
                reference_frame=reference_frame,
                uses_surface=False,
                line_number=line.line_number,
            ),
            state=state,
            include_feed=feed_token,
            passthrough_tokens=passthrough_tokens,
            uses_surface=False,
            subdivided=False,
            fused_count=0,
        )
        emitted["lines"] = [_append_comment(emitted["lines"][0], line.comment)] if emitted["lines"] else []
        return emitted

    definition = _arc_definition(
        command=command,
        start_x=start_x,
        start_y=start_y,
        target_x=target_x,
        target_y=target_y,
        arc_params_mm=arc_params_mm,
        line_number=line.line_number,
    )
    segments, subdivision_count, max_error = _adaptive_arc_segments(
        definition=definition,
        start_z=start_z,
        target_z=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        max_z_error_mm=max_z_error_mm,
        min_segment_length_mm=min_segment_length_mm,
        line_number=line.line_number,
    )
    if passthrough_tokens and len(segments) > 1:
        raise ApplicationError(
            f"adaptive_fast no puede subdividir el arco {line.line_number} porque mezcla movimiento con efectos laterales: {' '.join(passthrough_tokens)}."
        )
    fused_count = 0
    return _emit_arc_segments(
        line=line,
        command=explicit_motion or command,
        arc_segments=segments,
        start_point=_machine_point(
            pcb_x_mm=start_x,
            pcb_y_mm=start_y,
            programmed_z_mm=start_z,
            height_map=height_map,
            reference_frame=reference_frame,
            uses_surface=True,
            line_number=line.line_number,
        ),
        state=state,
        include_feed=feed_token,
        passthrough_tokens=passthrough_tokens,
        uses_surface=True,
        subdivided=subdivision_count > 0,
        fused_count=fused_count,
        max_error=max_error,
    )


def _adaptive_line_points(
    *,
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
    line_number: int,
) -> tuple[list[AdaptivePoint], int, float]:
    start = _machine_point(
        pcb_x_mm=start_x,
        pcb_y_mm=start_y,
        programmed_z_mm=start_z,
        height_map=height_map,
        reference_frame=reference_frame,
        uses_surface=True,
        line_number=line_number,
    )
    end = _machine_point(
        pcb_x_mm=target_x,
        pcb_y_mm=target_y,
        programmed_z_mm=target_z,
        height_map=height_map,
        reference_frame=reference_frame,
        uses_surface=True,
        line_number=line_number,
    )
    points = [start]
    stats = {"subdivisions": 0, "max_error": 0.0}

    def recurse(left: AdaptivePoint, right: AdaptivePoint, depth: int) -> None:
        nonlocal points
        segment_length = math.dist((left.pcb_x_mm, left.pcb_y_mm), (right.pcb_x_mm, right.pcb_y_mm))
        if segment_length <= min_segment_length_mm or depth >= MAX_RECURSION_DEPTH:
            points.append(right)
            return
        mid = _line_point(0.5, left, right, height_map=height_map, reference_frame=reference_frame, line_number=line_number)
        mid_error = abs(mid.delta_z_mm - _lerp(left.delta_z_mm, right.delta_z_mm, 0.5))
        quarter_error = 0.0
        if mid_error > max_z_error_mm * 0.5:
            quarter_1 = _line_point(0.25, left, right, height_map=height_map, reference_frame=reference_frame, line_number=line_number)
            quarter_3 = _line_point(0.75, left, right, height_map=height_map, reference_frame=reference_frame, line_number=line_number)
            quarter_error = max(
                abs(quarter_1.delta_z_mm - _lerp(left.delta_z_mm, right.delta_z_mm, 0.25)),
                abs(quarter_3.delta_z_mm - _lerp(left.delta_z_mm, right.delta_z_mm, 0.75)),
            )
        local_error = max(mid_error, quarter_error)
        if local_error <= max_z_error_mm:
            stats["max_error"] = max(stats["max_error"], local_error)
            points.append(right)
            return
        stats["subdivisions"] += 1
        recurse(left, mid, depth + 1)
        recurse(mid, right, depth + 1)

    recurse(start, end, 0)
    return points, int(stats["subdivisions"]), float(stats["max_error"])


def _adaptive_arc_segments(
    *,
    definition: ArcDefinition,
    start_z: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    max_z_error_mm: float,
    min_segment_length_mm: float,
    line_number: int,
) -> tuple[list[dict[str, Any]], int, float]:
    stats = {"subdivisions": 0, "max_error": 0.0}
    segments: list[dict[str, Any]] = []

    def recurse(start_progress: float, end_progress: float, depth: int) -> None:
        sweep = definition.sweep_angle * (end_progress - start_progress)
        arc_length = abs(sweep) * definition.radius_mm
        start_point = _arc_point(
            definition=definition,
            progress=start_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
        )
        end_point = _arc_point(
            definition=definition,
            progress=end_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
        )
        if arc_length <= min_segment_length_mm or depth >= MAX_RECURSION_DEPTH:
            segments.append(
                {
                    "end": end_point,
                    "i_mm": definition.center_x_mm - start_point.pcb_x_mm,
                    "j_mm": definition.center_y_mm - start_point.pcb_y_mm,
                    "approx_error_mm": 0.0,
                }
            )
            return
        mid_progress = (start_progress + end_progress) / 2.0
        quarter_1_progress = start_progress + (end_progress - start_progress) * 0.25
        quarter_3_progress = start_progress + (end_progress - start_progress) * 0.75
        mid = _arc_point(
            definition=definition,
            progress=mid_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
        )
        quarter_1 = _arc_point(
            definition=definition,
            progress=quarter_1_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
        )
        quarter_3 = _arc_point(
            definition=definition,
            progress=quarter_3_progress,
            start_z=start_z,
            target_z=target_z,
            height_map=height_map,
            reference_frame=reference_frame,
            line_number=line_number,
        )
        local_error = max(
            abs(mid.delta_z_mm - _lerp(start_point.delta_z_mm, end_point.delta_z_mm, 0.5)),
            abs(quarter_1.delta_z_mm - _lerp(start_point.delta_z_mm, end_point.delta_z_mm, 0.25)),
            abs(quarter_3.delta_z_mm - _lerp(start_point.delta_z_mm, end_point.delta_z_mm, 0.75)),
        )
        if local_error <= max_z_error_mm:
            stats["max_error"] = max(stats["max_error"], local_error)
            segments.append(
                {
                    "end": end_point,
                    "i_mm": definition.center_x_mm - start_point.pcb_x_mm,
                    "j_mm": definition.center_y_mm - start_point.pcb_y_mm,
                    "approx_error_mm": local_error,
                }
            )
            return
        stats["subdivisions"] += 1
        recurse(start_progress, mid_progress, depth + 1)
        recurse(mid_progress, end_progress, depth + 1)

    recurse(0.0, 1.0, 0)
    return segments, int(stats["subdivisions"]), float(stats["max_error"])


def _simplify_line_points(points: list[AdaptivePoint], *, max_z_error_mm: float) -> tuple[list[AdaptivePoint], int]:
    if len(points) <= 2:
        return points, 0

    kept = [points[0]]
    removed = 0
    index = 1
    while index < len(points) - 1:
        current = points[index]
        nxt = points[index + 1]
        previous = kept[-1]
        if _can_skip_point(previous, current, nxt, max_z_error_mm=max_z_error_mm):
            removed += 1
            index += 1
            continue
        kept.append(current)
        index += 1
    kept.append(points[-1])
    return kept, removed


def _can_skip_point(left: AdaptivePoint, middle: AdaptivePoint, right: AdaptivePoint, *, max_z_error_mm: float) -> bool:
    xy_left = (left.pcb_x_mm, left.pcb_y_mm)
    xy_middle = (middle.pcb_x_mm, middle.pcb_y_mm)
    xy_right = (right.pcb_x_mm, right.pcb_y_mm)
    if math.dist(xy_left, xy_right) <= 0:
        return False
    cross = abs((xy_middle[0] - xy_left[0]) * (xy_right[1] - xy_left[1]) - (xy_middle[1] - xy_left[1]) * (xy_right[0] - xy_left[0]))
    if cross > 1e-9:
        return False
    total = math.dist(xy_left, xy_right)
    progress = math.dist(xy_left, xy_middle) / total
    expected_delta = _lerp(left.delta_z_mm, right.delta_z_mm, progress)
    return abs(middle.delta_z_mm - expected_delta) <= max_z_error_mm


def _emit_linear_points(
    *,
    line: GCodeLine,
    command: str,
    points: list[AdaptivePoint],
    previous: AdaptivePoint,
    state: ModalState,
    include_feed: str | None,
    passthrough_tokens: list[str],
    approx_error_mm: float,
    uses_surface: bool,
    subdivided: bool,
    fused_count: int,
) -> dict[str, Any]:
    emitted_lines: list[str] = []
    trace: list[dict[str, Any]] = []
    delta_values: list[float] = []
    z_values: list[float] = []
    current_previous = previous
    for index, point in enumerate(points):
        line_text = _format_motion_line(
            command=command,
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
        trace.append(_trace_entry(line, command, point, uses_surface=uses_surface, approx_error_mm=approx_error_mm))
        delta_values.append(point.delta_z_mm)
        z_values.append(point.machine_z_mm)
        current_previous = point
    return _result(
        emitted_lines,
        trace=trace,
        delta_values=delta_values,
        z_values=z_values,
        emitted_moves=len(emitted_lines),
        segments_subdivided=1 if subdivided else 0,
        segments_fused=fused_count,
        max_approximation_error_mm=approx_error_mm,
    )


def _emit_arc_segments(
    *,
    line: GCodeLine,
    command: str,
    arc_segments: list[dict[str, Any]],
    start_point: AdaptivePoint,
    state: ModalState,
    include_feed: str | None,
    passthrough_tokens: list[str],
    uses_surface: bool,
    subdivided: bool,
    fused_count: int,
    max_error: float = 0.0,
) -> dict[str, Any]:
    emitted_lines: list[str] = []
    trace: list[dict[str, Any]] = []
    delta_values: list[float] = []
    z_values: list[float] = []
    current_start = start_point
    for index, segment in enumerate(arc_segments):
        end_point = segment["end"]
        line_text = _format_arc_line(
            command=command,
            point=end_point,
            previous=current_start,
            state=state,
            i_mm=float(segment["i_mm"]),
            j_mm=float(segment["j_mm"]),
            include_feed=include_feed if index == 0 else None,
        )
        if index == 0 and passthrough_tokens:
            line_text = f"{line_text} {' '.join(passthrough_tokens)}"
        if index == 0:
            line_text = _append_comment(line_text, line.comment)
        emitted_lines.append(line_text)
        trace.append(_trace_entry(line, command, end_point, uses_surface=uses_surface, approx_error_mm=float(segment.get("approx_error_mm", 0.0))))
        delta_values.append(end_point.delta_z_mm)
        z_values.append(end_point.machine_z_mm)
        current_start = end_point
    return _result(
        emitted_lines,
        trace=trace,
        delta_values=delta_values,
        z_values=z_values,
        emitted_moves=len(emitted_lines),
        segments_subdivided=1 if subdivided else 0,
        segments_fused=fused_count,
        max_approximation_error_mm=max_error,
    )


def _format_passthrough_motion_line(
    *,
    command: str,
    point: AdaptivePoint,
    previous: AdaptivePoint,
    state: ModalState,
    include_feed: str | None,
    passthrough_tokens: list[str],
    comment: str | None,
) -> str:
    line = _format_motion_line(command=command, point=point, previous=previous, state=state, include_feed=include_feed)
    if passthrough_tokens:
        line = f"{line} {' '.join(passthrough_tokens)}"
    return _append_comment(line, comment)


def _format_motion_line(*, command: str, point: AdaptivePoint, previous: AdaptivePoint, state: ModalState, include_feed: str | None) -> str:
    parts = [command]
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
    point: AdaptivePoint,
    previous: AdaptivePoint,
    state: ModalState,
    i_mm: float,
    j_mm: float,
    include_feed: str | None,
) -> str:
    parts = [command]
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


def _machine_point(
    *,
    pcb_x_mm: float,
    pcb_y_mm: float,
    programmed_z_mm: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    uses_surface: bool,
    line_number: int,
    progress: float = 1.0,
) -> AdaptivePoint:
    delta_z_mm = 0.0
    if uses_surface:
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
    )


def _line_point(
    progress: float,
    left: AdaptivePoint,
    right: AdaptivePoint,
    *,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    line_number: int,
) -> AdaptivePoint:
    pcb_x_mm = _lerp(left.pcb_x_mm, right.pcb_x_mm, progress)
    pcb_y_mm = _lerp(left.pcb_y_mm, right.pcb_y_mm, progress)
    programmed_z_mm = _lerp(left.programmed_z_mm, right.programmed_z_mm, progress)
    return _machine_point(
        pcb_x_mm=pcb_x_mm,
        pcb_y_mm=pcb_y_mm,
        programmed_z_mm=programmed_z_mm,
        height_map=height_map,
        reference_frame=reference_frame,
        uses_surface=True,
        line_number=line_number,
        progress=_lerp(left.progress, right.progress, progress),
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
    full_circle = math.isclose(start_x, target_x, abs_tol=FULL_CIRCLE_EPSILON) and math.isclose(start_y, target_y, abs_tol=FULL_CIRCLE_EPSILON)
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(target_y - center_y, target_x - center_x)
    if full_circle:
        sweep_angle = -2 * math.pi if command == "G2" else 2 * math.pi
    else:
        sweep_angle = end_angle - start_angle
        if command == "G2" and sweep_angle >= 0:
            sweep_angle -= 2 * math.pi
        if command == "G3" and sweep_angle <= 0:
            sweep_angle += 2 * math.pi
    return ArcDefinition(
        clockwise=command == "G2",
        center_x_mm=center_x,
        center_y_mm=center_y,
        radius_mm=radius_start,
        start_angle=start_angle,
        sweep_angle=sweep_angle,
        full_circle=full_circle,
    )


def _arc_point(
    *,
    definition: ArcDefinition,
    progress: float,
    start_z: float,
    target_z: float,
    height_map: HeightMap,
    reference_frame: ReferenceFrame,
    line_number: int,
) -> AdaptivePoint:
    angle = definition.start_angle + definition.sweep_angle * progress
    pcb_x_mm = definition.center_x_mm + math.cos(angle) * definition.radius_mm
    pcb_y_mm = definition.center_y_mm + math.sin(angle) * definition.radius_mm
    programmed_z_mm = _lerp(start_z, target_z, progress)
    return _machine_point(
        pcb_x_mm=pcb_x_mm,
        pcb_y_mm=pcb_y_mm,
        programmed_z_mm=programmed_z_mm,
        height_map=height_map,
        reference_frame=reference_frame,
        uses_surface=True,
        line_number=line_number,
        progress=progress,
    )


def _trace_entry(line: GCodeLine, command: str, point: AdaptivePoint, *, uses_surface: bool, approx_error_mm: float) -> dict[str, Any]:
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
        "uses_surface_map": uses_surface,
        "approximation_error_mm": approx_error_mm,
    }


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
