from __future__ import annotations

import math
from dataclasses import replace

from klipper_cnc_assistant.domain import (
    AnalysisIssue,
    Bounds3D,
    IssueSeverity,
    MaterialBruto,
    MaterialOverflow,
    OperationAnalysis,
    PreviewPoint,
    PreviewSegment,
)

from .models import GCodeLine, GCodeToken, ModalState
from .tokenizer import tokenize_gcode


SUPPORTED_LINEAR_CODES = {"G0", "G1"}
SUPPORTED_ARC_CODES = {"G2", "G3"}
SUPPORTED_SETUP_CODES = {"G20", "G21", "G90", "G91", "G94"}
CRITICAL_CODES = {"G28", "G92"}
MANUAL_SPINDLE_CODES = {"M3", "M4", "M5"}
MANUAL_TOOLCHANGE_CODES = {"M6"}
ARC_CHORD_TOLERANCE_MM = 0.05
ARC_MAX_SEGMENTS = 720
FULL_CIRCLE_EPSILON = 1e-6


def _normalize_g_code(token: GCodeToken) -> str:
    number = token.numeric_value()
    if number.is_integer():
        return f"G{int(number)}"
    return f"G{number:g}"


def _normalize_m_code(token: GCodeToken) -> str:
    number = token.numeric_value()
    if number.is_integer():
        return f"M{int(number)}"
    return f"M{number:g}"


def _normalize_t_code(token: GCodeToken) -> str:
    if token.raw_value is None:
        return "T"
    return f"T{token.raw_value}"


def _to_mm(value: float, units: str) -> float:
    if units == "mm":
        return value
    return value * 25.4


def _update_bounds(
    bounds: dict[str, float | None],
    x_mm: float,
    y_mm: float,
    z_mm: float,
) -> None:
    for key, value in (
        ("min_x", x_mm),
        ("max_x", x_mm),
        ("min_y", y_mm),
        ("max_y", y_mm),
        ("min_z", z_mm),
        ("max_z", z_mm),
    ):
        current = bounds[key]
        if current is None:
            bounds[key] = value
            continue
        if key.startswith("min_"):
            bounds[key] = min(current, value)
        else:
            bounds[key] = max(current, value)


def _update_bounds_from_points(
    bounds: dict[str, float | None],
    points: tuple[PreviewPoint, ...],
    z_mm: float,
) -> None:
    for point in points:
        _update_bounds(bounds, point.x_mm, point.y_mm, z_mm)


def _build_bounds(bounds: dict[str, float | None]) -> Bounds3D | None:
    if bounds["min_x"] is None:
        return None
    return Bounds3D(
        min_x_mm=float(bounds["min_x"]),
        max_x_mm=float(bounds["max_x"]),
        min_y_mm=float(bounds["min_y"]),
        max_y_mm=float(bounds["max_y"]),
        min_z_mm=float(bounds["min_z"]),
        max_z_mm=float(bounds["max_z"]),
    )


def _append_issue(
    issues: list[AnalysisIssue],
    *,
    severity: IssueSeverity,
    code: str,
    message: str,
    line: int | None = None,
    command: str | None = None,
) -> None:
    issues.append(
        AnalysisIssue(
            severidad=severity,
            codigo=code,
            mensaje=message,
            linea=line,
            comando=command,
        )
    )


def _distance_3d(
    start_x: float,
    start_y: float,
    start_z: float,
    end_x: float,
    end_y: float,
    end_z: float,
) -> float:
    return math.dist((start_x, start_y, start_z), (end_x, end_y, end_z))


def _classify_motion(command: str) -> str:
    if command == "G0":
        return "desplazamiento_rapido"
    if command == "G1":
        return "movimiento_lineal"
    if command == "G2":
        return "arco_horario"
    if command == "G3":
        return "arco_antihorario"
    return "movimiento_desconocido"


def _segment_points_for_line(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
) -> tuple[PreviewPoint, ...]:
    return (
        PreviewPoint(x_mm=start_x, y_mm=start_y),
        PreviewPoint(x_mm=end_x, y_mm=end_y),
    )


def _fit_angle_step(radius_mm: float) -> float:
    if radius_mm <= ARC_CHORD_TOLERANCE_MM:
        return math.pi / 6
    ratio = max(-1.0, min(1.0, 1.0 - ARC_CHORD_TOLERANCE_MM / radius_mm))
    return max(math.pi / 180, 2 * math.acos(ratio))


def _build_arc_points(
    *,
    center_x: float,
    center_y: float,
    radius_mm: float,
    start_angle: float,
    sweep_angle: float,
) -> tuple[PreviewPoint, ...]:
    step_angle = _fit_angle_step(radius_mm)
    segment_count = max(12, math.ceil(abs(sweep_angle) / step_angle))
    segment_count = min(segment_count, ARC_MAX_SEGMENTS)
    points: list[PreviewPoint] = []
    for index in range(segment_count + 1):
        progress = index / segment_count
        angle = start_angle + sweep_angle * progress
        points.append(
            PreviewPoint(
                x_mm=center_x + math.cos(angle) * radius_mm,
                y_mm=center_y + math.sin(angle) * radius_mm,
            )
        )
    return tuple(points)


def _resolve_target_value(
    current_value: float,
    raw_value: float | None,
    positioning: str,
) -> float:
    if raw_value is None:
        return current_value
    if positioning == "absolute":
        return raw_value
    return current_value + raw_value


def _build_arc_segment(
    *,
    command: str,
    line: GCodeLine,
    state: ModalState,
    axes: dict[str, float],
    arc_parameters: dict[str, float],
) -> tuple[PreviewSegment | None, float, float, float, str | None]:
    start_x = state.x_mm
    start_y = state.y_mm
    start_z = state.z_mm

    target_x = _resolve_target_value(state.x_mm, axes.get("X"), state.positioning)
    target_y = _resolve_target_value(state.y_mm, axes.get("Y"), state.positioning)
    target_z = _resolve_target_value(state.z_mm, axes.get("Z"), state.positioning)

    if "R" in arc_parameters:
        return None, target_x, target_y, target_z, "El arco usa parametro R y no es representable con seguridad en esta fase."
    if "I" not in arc_parameters and "J" not in arc_parameters:
        return None, target_x, target_y, target_z, "El arco no incluye I/J suficientes para reconstruir su geometria."
    if "K" in arc_parameters:
        return None, target_x, target_y, target_z, "El arco usa K y no es representable en el visor XY actual."

    center_x = start_x + arc_parameters.get("I", 0.0)
    center_y = start_y + arc_parameters.get("J", 0.0)
    radius_start = math.dist((start_x, start_y), (center_x, center_y))
    radius_end = math.dist((target_x, target_y), (center_x, center_y))

    if radius_start <= FULL_CIRCLE_EPSILON:
        return None, target_x, target_y, target_z, "El arco tiene radio inicial nulo o ambiguo."
    if abs(radius_start - radius_end) > max(ARC_CHORD_TOLERANCE_MM, radius_start * 0.01):
        return None, target_x, target_y, target_z, "El arco no mantiene un radio consistente entre inicio y fin."

    full_circle = (
        math.isclose(start_x, target_x, abs_tol=FULL_CIRCLE_EPSILON)
        and math.isclose(start_y, target_y, abs_tol=FULL_CIRCLE_EPSILON)
    )
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

    points = _build_arc_points(
        center_x=center_x,
        center_y=center_y,
        radius_mm=radius_start,
        start_angle=start_angle,
        sweep_angle=sweep_angle,
    )
    arc_length = abs(sweep_angle) * radius_start
    distance_mm = math.hypot(arc_length, target_z - start_z)

    segment = PreviewSegment(
        tipo=command,
        tipo_movimiento=_classify_motion(command),
        numero_linea=line.line_number,
        inicio_x_mm=start_x,
        inicio_y_mm=start_y,
        fin_x_mm=target_x,
        fin_y_mm=target_y,
        z_mm=target_z,
        avance_mm_min=state.feed_mm_min,
        distancia_mm=distance_mm,
        puntos=points,
    )
    return segment, target_x, target_y, target_z, None


def _calculate_material_overflows(
    limits: Bounds3D,
    material: MaterialBruto,
) -> tuple[MaterialOverflow, ...]:
    overflows: list[MaterialOverflow] = []
    comparisons = (
        ("X", "minimo", 0.0, limits.min_x_mm),
        ("Y", "minimo", 0.0, limits.min_y_mm),
        ("X", "maximo", material.ancho_mm, limits.max_x_mm),
        ("Y", "maximo", material.alto_mm, limits.max_y_mm),
    )
    for axis, direction, limit_value, actual_value in comparisons:
        if direction == "minimo" and actual_value < limit_value:
            overflows.append(
                MaterialOverflow(
                    eje=axis,
                    direccion=direction,
                    limite_mm=limit_value,
                    valor_mm=actual_value,
                    exceso_mm=limit_value - actual_value,
                )
            )
        if direction == "maximo" and actual_value > limit_value:
            overflows.append(
                MaterialOverflow(
                    eje=axis,
                    direccion=direction,
                    limite_mm=limit_value,
                    valor_mm=actual_value,
                    exceso_mm=actual_value - limit_value,
                )
            )
    return tuple(overflows)


def _segment_warnings_for_material(
    segment: PreviewSegment,
    material: MaterialBruto,
) -> tuple[str, ...]:
    warnings: list[str] = []
    for point in segment.puntos:
        if point.x_mm < 0:
            warnings.append("fuera_material_x_min")
            break
    for point in segment.puntos:
        if point.x_mm > material.ancho_mm:
            warnings.append("fuera_material_x_max")
            break
    for point in segment.puntos:
        if point.y_mm < 0:
            warnings.append("fuera_material_y_min")
            break
    for point in segment.puntos:
        if point.y_mm > material.alto_mm:
            warnings.append("fuera_material_y_max")
            break
    return tuple(warnings)


def analyze_gcode_text(
    text: str,
    *,
    material: MaterialBruto | None = None,
) -> OperationAnalysis:
    lines = tokenize_gcode(text)
    state = ModalState()
    bounds: dict[str, float | None] = {
        "min_x": 0.0,
        "max_x": 0.0,
        "min_y": 0.0,
        "max_y": 0.0,
        "min_z": 0.0,
        "max_z": 0.0,
    }
    feeds: set[float] = set()
    spindle_actions: list[str] = []
    tool_changes: list[str] = []
    unknown_commands: list[str] = []
    unsupported_commands: list[str] = []
    issues: list[AnalysisIssue] = []
    preview_segments: list[PreviewSegment] = []
    linear_segments: list[PreviewSegment] = []
    movement_count = 0
    analysis_incomplete = False

    for line in lines:
        if not line.tokens:
            continue
        movement_count, line_incomplete = _handle_line(
            line=line,
            state=state,
            bounds=bounds,
            feeds=feeds,
            spindle_actions=spindle_actions,
            tool_changes=tool_changes,
            unknown_commands=unknown_commands,
            unsupported_commands=unsupported_commands,
            issues=issues,
            preview_segments=preview_segments,
            linear_segments=linear_segments,
            movement_count=movement_count,
        )
        analysis_incomplete = analysis_incomplete or line_incomplete

    limits = _build_bounds(bounds)
    analysis = OperationAnalysis(
        limites=limits,
        avances_mm_min=tuple(sorted(feeds)),
        profundidad_min_mm=None if limits is None else limits.min_z_mm,
        profundidad_max_mm=None if limits is None else limits.max_z_mm,
        cantidad_movimientos=movement_count,
        comandos_desconocidos=tuple(unknown_commands),
        comandos_no_compatibles=tuple(unsupported_commands),
        acciones_husillo=tuple(spindle_actions),
        cambios_herramienta=tuple(tool_changes),
        unidades_detectadas=tuple(sorted(state.seen_units)),
        modos_posicionamiento=tuple(sorted(state.seen_positioning)),
        incidencias=tuple(issues),
        analisis_incompleto=analysis_incomplete,
        segmentos_lineales=tuple(linear_segments),
        segmentos_vista_previa=tuple(preview_segments),
        tolerancia_arco_mm=ARC_CHORD_TOLERANCE_MM,
    )

    if material is None or limits is None:
        return analysis

    overflows = _calculate_material_overflows(limits, material)
    cabe = len(overflows) == 0
    if cabe:
        message = "El G-code cabe dentro del material definido."
        preview_with_material = tuple(
            replace(segment, advertencias=segment.advertencias)
            for segment in preview_segments
        )
        linear_with_material = tuple(
            replace(segment, advertencias=segment.advertencias)
            for segment in linear_segments
        )
    else:
        details = ", ".join(
            f"{overflow.eje} {overflow.direccion}: {overflow.exceso_mm:.3f} mm"
            for overflow in overflows
        )
        message = f"El G-code excede los limites del material definido ({details})."
        for overflow in overflows:
            _append_issue(
                issues,
                severity=IssueSeverity.ADVERTENCIA,
                code="trayectoria_fuera_material",
                message=(
                    f"La trayectoria excede el material en {overflow.eje} ({overflow.direccion}) por {overflow.exceso_mm:.3f} mm."
                ),
            )
        preview_with_material = tuple(
            replace(
                segment,
                advertencias=tuple(
                    sorted(
                        set(segment.advertencias)
                        | set(_segment_warnings_for_material(segment, material))
                    )
                ),
            )
            for segment in preview_segments
        )
        linear_with_material = tuple(
            replace(
                segment,
                advertencias=tuple(
                    sorted(
                        set(segment.advertencias)
                        | set(_segment_warnings_for_material(segment, material))
                    )
                ),
            )
            for segment in linear_segments
        )

    return replace(
        analysis,
        incidencias=tuple(issues),
        cabe_en_material=cabe,
        mensaje_material=message,
        desbordes_material=overflows,
        segmentos_vista_previa=preview_with_material,
        segmentos_lineales=linear_with_material,
    )


def _handle_line(
    *,
    line: GCodeLine,
    state: ModalState,
    bounds: dict[str, float | None],
    feeds: set[float],
    spindle_actions: list[str],
    tool_changes: list[str],
    unknown_commands: list[str],
    unsupported_commands: list[str],
    issues: list[AnalysisIssue],
    preview_segments: list[PreviewSegment],
    linear_segments: list[PreviewSegment],
    movement_count: int,
) -> tuple[int, bool]:
    analysis_incomplete = False
    motion_command = state.active_motion
    axes: dict[str, float] = {}
    arc_parameters: dict[str, float] = {}

    for token in line.tokens:
        if token.letter == "G":
            command = _normalize_g_code(token)
            if command in SUPPORTED_LINEAR_CODES | SUPPORTED_ARC_CODES:
                motion_command = command
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
            elif command in CRITICAL_CODES:
                _append_issue(
                    issues,
                    severity=IssueSeverity.ERROR_CRITICO,
                    code=command.lower(),
                    message=f"El comando {command} no esta permitido en G-code importado.",
                    line=line.line_number,
                    command=command,
                )
            elif command not in SUPPORTED_SETUP_CODES:
                unknown_commands.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="gcode_desconocido",
                    message=f"Se encontro un comando G no reconocido: {command}.",
                    line=line.line_number,
                    command=command,
                )
        elif token.letter in {"X", "Y", "Z"}:
            axes[token.letter] = _to_mm(token.numeric_value(), state.units)
        elif token.letter in {"I", "J", "K", "R"}:
            arc_parameters[token.letter] = _to_mm(token.numeric_value(), state.units)
        elif token.letter == "F":
            feed = _to_mm(token.numeric_value(), state.units)
            state.feed_mm_min = feed
            feeds.add(feed)
        elif token.letter == "M":
            command = _normalize_m_code(token)
            if command in MANUAL_SPINDLE_CODES:
                spindle_actions.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="accion_manual_husillo",
                    message=f"El comando {command} requiere accion manual del husillo.",
                    line=line.line_number,
                    command=command,
                )
            elif command in MANUAL_TOOLCHANGE_CODES:
                tool_changes.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="cambio_manual_herramienta",
                    message=f"El comando {command} requiere cambio manual de herramienta.",
                    line=line.line_number,
                    command=command,
                )
            else:
                unknown_commands.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="gcode_desconocido",
                    message=f"Se encontro un comando M no reconocido: {command}.",
                    line=line.line_number,
                    command=command,
                )
        elif token.letter == "S":
            command = token.command
            spindle_actions.append(command)
            _append_issue(
                issues,
                severity=IssueSeverity.ADVERTENCIA,
                code="accion_manual_husillo",
                message="Se encontro una velocidad de husillo que requiere accion manual.",
                line=line.line_number,
                command=command,
            )
        elif token.letter == "T":
            command = _normalize_t_code(token)
            tool_changes.append(command)
            _append_issue(
                issues,
                severity=IssueSeverity.ADVERTENCIA,
                code="cambio_manual_herramienta",
                message="Se encontro una seleccion manual de herramienta.",
                line=line.line_number,
                command=command,
            )
        else:
            command = token.command
            unknown_commands.append(command)
            _append_issue(
                issues,
                severity=IssueSeverity.ADVERTENCIA,
                code="gcode_desconocido",
                message=f"Se encontro un token no reconocido: {command}.",
                line=line.line_number,
                command=command,
            )

    if motion_command in SUPPORTED_LINEAR_CODES and not axes:
        return movement_count, analysis_incomplete

    if motion_command in SUPPORTED_ARC_CODES and not axes and not arc_parameters:
        return movement_count, analysis_incomplete

    if motion_command in SUPPORTED_ARC_CODES:
        segment, target_x, target_y, target_z, warning = _build_arc_segment(
            command=motion_command,
            line=line,
            state=state,
            axes=axes,
            arc_parameters=arc_parameters,
        )
        if warning is not None:
            unsupported_commands.append(motion_command)
            _append_issue(
                issues,
                severity=IssueSeverity.ADVERTENCIA,
                code="arco_no_representable",
                message=warning,
                line=line.line_number,
                command=motion_command,
            )
            analysis_incomplete = True
            state.x_mm = target_x
            state.y_mm = target_y
            state.z_mm = target_z
            _update_bounds(bounds, target_x, target_y, target_z)
            return movement_count + 1, analysis_incomplete
        preview_segments.append(segment)
        _update_bounds_from_points(bounds, segment.puntos, target_z)
        state.x_mm = target_x
        state.y_mm = target_y
        state.z_mm = target_z
        return movement_count + 1, analysis_incomplete

    start_x = state.x_mm
    start_y = state.y_mm
    start_z = state.z_mm
    target_x = _resolve_target_value(state.x_mm, axes.get("X"), state.positioning)
    target_y = _resolve_target_value(state.y_mm, axes.get("Y"), state.positioning)
    target_z = _resolve_target_value(state.z_mm, axes.get("Z"), state.positioning)

    state.x_mm = target_x
    state.y_mm = target_y
    state.z_mm = target_z
    _update_bounds(bounds, target_x, target_y, target_z)

    if motion_command not in SUPPORTED_LINEAR_CODES:
        return movement_count, analysis_incomplete

    segment = PreviewSegment(
        tipo=motion_command,
        tipo_movimiento=_classify_motion(motion_command),
        numero_linea=line.line_number,
        inicio_x_mm=start_x,
        inicio_y_mm=start_y,
        fin_x_mm=target_x,
        fin_y_mm=target_y,
        z_mm=target_z,
        avance_mm_min=state.feed_mm_min,
        distancia_mm=_distance_3d(start_x, start_y, start_z, target_x, target_y, target_z),
        puntos=_segment_points_for_line(start_x, start_y, target_x, target_y),
    )
    preview_segments.append(segment)
    linear_segments.append(segment)
    return movement_count + 1, analysis_incomplete
