from __future__ import annotations

from dataclasses import replace

from klipper_cnc_assistant.domain import (
    AnalysisIssue,
    Bounds3D,
    IssueSeverity,
    MaterialBruto,
    OperationAnalysis,
)

from .models import GCodeLine, GCodeToken, ModalState
from .tokenizer import tokenize_gcode


SUPPORTED_MOTION_CODES = {"G0", "G1"}
SUPPORTED_SETUP_CODES = {"G20", "G21", "G90", "G91"}
UNSUPPORTED_ARC_CODES = {"G2", "G3"}
CRITICAL_CODES = {"G28", "G92"}
MANUAL_SPINDLE_CODES = {"M3", "M4", "M5"}
MANUAL_TOOLCHANGE_CODES = {"M6"}


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


def _build_bounds(
    bounds: dict[str, float | None],
) -> Bounds3D | None:
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
    movement_count = 0
    analysis_incomplete = False

    for line in lines:
        if not line.tokens:
            continue
        _handle_line(
            line=line,
            state=state,
            bounds=bounds,
            feeds=feeds,
            spindle_actions=spindle_actions,
            tool_changes=tool_changes,
            unknown_commands=unknown_commands,
            unsupported_commands=unsupported_commands,
            issues=issues,
            movement_count_ref=[movement_count],
        )
        movement_count = _handle_line.last_movement_count
        analysis_incomplete = (
            analysis_incomplete
            or _handle_line.last_analysis_incomplete
        )

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
        unidades_detectadas=tuple(
            sorted(state.seen_units)
        ),
        modos_posicionamiento=tuple(
            sorted(state.seen_positioning)
        ),
        incidencias=tuple(issues),
        analisis_incompleto=analysis_incomplete,
    )

    if material is None or limits is None:
        return analysis

    cabe = (
        limits.min_x_mm >= 0.0
        and limits.min_y_mm >= 0.0
        and limits.max_x_mm <= material.ancho_mm
        and limits.max_y_mm <= material.alto_mm
    )
    mensaje = (
        "El G-code cabe dentro del material definido."
        if cabe
        else "El G-code excede los limites del material definido."
    )
    return replace(
        analysis,
        cabe_en_material=cabe,
        mensaje_material=mensaje,
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
    movement_count_ref: list[int],
) -> None:
    _handle_line.last_analysis_incomplete = False
    motion_command = state.active_motion
    axes: dict[str, float] = {}

    for token in line.tokens:
        if token.letter == "G":
            command = _normalize_g_code(token)
            if command in SUPPORTED_MOTION_CODES:
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
            elif command in CRITICAL_CODES:
                _append_issue(
                    issues,
                    severity=IssueSeverity.ERROR_CRITICO,
                    code=command.lower(),
                    message=(
                        f"El comando {command} no esta permitido en G-code importado."
                    ),
                    line=line.line_number,
                    command=command,
                )
            elif command in UNSUPPORTED_ARC_CODES:
                unsupported_commands.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="gcode_no_compatible",
                    message=(
                        f"El comando {command} aun no esta soportado completamente."
                    ),
                    line=line.line_number,
                    command=command,
                )
                _handle_line.last_analysis_incomplete = True
                motion_command = command
            elif command not in SUPPORTED_SETUP_CODES:
                unknown_commands.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="gcode_desconocido",
                    message=(
                        f"Se encontro un comando G no reconocido: {command}."
                    ),
                    line=line.line_number,
                    command=command,
                )
        elif token.letter in {"X", "Y", "Z"}:
            axes[token.letter] = _to_mm(
                token.numeric_value(),
                state.units,
            )
        elif token.letter == "F":
            feed = _to_mm(
                token.numeric_value(),
                state.units,
            )
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
                    message=(
                        f"El comando {command} requiere accion manual del husillo."
                    ),
                    line=line.line_number,
                    command=command,
                )
            elif command in MANUAL_TOOLCHANGE_CODES:
                tool_changes.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="cambio_manual_herramienta",
                    message=(
                        f"El comando {command} requiere cambio manual de herramienta."
                    ),
                    line=line.line_number,
                    command=command,
                )
            else:
                unknown_commands.append(command)
                _append_issue(
                    issues,
                    severity=IssueSeverity.ADVERTENCIA,
                    code="gcode_desconocido",
                    message=(
                        f"Se encontro un comando M no reconocido: {command}."
                    ),
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
                message=(
                    "Se encontro una velocidad de husillo que requiere accion manual."
                ),
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
                message=(
                    "Se encontro una seleccion manual de herramienta."
                ),
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
                message=(
                    f"Se encontro un token no reconocido: {command}."
                ),
                line=line.line_number,
                command=command,
            )

    if not axes:
        _handle_line.last_movement_count = movement_count_ref[0]
        return

    target_x = state.x_mm
    target_y = state.y_mm
    target_z = state.z_mm

    for axis, raw_value in axes.items():
        if state.positioning == "absolute":
            target_value = raw_value
        else:
            current_value = {
                "X": state.x_mm,
                "Y": state.y_mm,
                "Z": state.z_mm,
            }[axis]
            target_value = current_value + raw_value

        if axis == "X":
            target_x = target_value
        elif axis == "Y":
            target_y = target_value
        elif axis == "Z":
            target_z = target_value

    state.x_mm = target_x
    state.y_mm = target_y
    state.z_mm = target_z
    _update_bounds(bounds, target_x, target_y, target_z)

    if motion_command in SUPPORTED_MOTION_CODES | UNSUPPORTED_ARC_CODES:
        movement_count_ref[0] += 1
    _handle_line.last_movement_count = movement_count_ref[0]


_handle_line.last_movement_count = 0
_handle_line.last_analysis_incomplete = False
