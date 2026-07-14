from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GCodeToken:
    letter: str
    raw_value: str | None
    line_number: int

    @property
    def command(self) -> str:
        if self.raw_value is None:
            return self.letter.upper()
        return f"{self.letter.upper()}{self.raw_value}"

    def numeric_value(self) -> float:
        if self.raw_value is None:
            raise ValueError(
                f"El token {self.command} no contiene un valor numerico."
            )
        return float(self.raw_value)


@dataclass(frozen=True)
class GCodeLine:
    line_number: int
    raw: str
    code: str
    comment: str | None
    tokens: tuple[GCodeToken, ...]


@dataclass
class ModalState:
    units: str = "mm"
    positioning: str = "absolute"
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    feed_mm_min: float | None = None
    active_motion: str | None = None
    seen_units: set[str] = field(default_factory=lambda: {"mm"})
    seen_positioning: set[str] = field(
        default_factory=lambda: {"absolute"}
    )
