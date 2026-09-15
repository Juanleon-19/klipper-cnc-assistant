"""One stable unit regime once geometry starts; block word order is irrelevant."""
from dataclasses import dataclass
from .models import GCodeLine


@dataclass(frozen=True)
class UnitRegimeError:
    line: int
    command: str
    message: str


def block_units(line: GCodeLine, current: str) -> str:
    for token in line.tokens:
        if token.letter == "G" and token.numeric_value() in (20, 21):
            current = "inch" if token.numeric_value() == 20 else "mm"
    return current


def unit_regime_error(lines: list[GCodeLine]) -> UnitRegimeError | None:
    units = "mm"
    geometry_started = False
    for line in lines:
        commands = [token for token in line.tokens
                    if token.letter == "G" and token.numeric_value() in (20, 21)]
        requested = block_units(line, units)
        conflicting = len({token.numeric_value() for token in commands}) > 1
        if conflicting or (geometry_started and requested != units):
            command = "/".join(f"G{int(token.numeric_value())}" for token in commands)
            reason = "unidades contradictorias en el mismo bloque" if conflicting else "cambio de unidades después de comenzar la geometría"
            return UnitRegimeError(line.line_number, command,
                f"Línea {line.line_number}, {command}: {reason} no soportado. "
                "Use un régimen estable G20 o G21 desde el preámbulo. No se generó G-code compensado.")
        units = requested
        geometry_started |= any(token.letter in {"X", "Y", "Z", "I", "J", "K", "R"} for token in line.tokens)
    return None
