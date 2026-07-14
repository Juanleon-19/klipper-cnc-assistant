from __future__ import annotations

import re

from .models import GCodeLine, GCodeToken


TOKEN_RE = re.compile(
    r"([A-Za-z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))?"
)


def split_comment(raw_line: str) -> tuple[str, str | None]:
    line = raw_line.rstrip("\n")
    comment_parts: list[str] = []

    if ";" in line:
        line, semicolon_comment = line.split(";", 1)
        comment_parts.append(semicolon_comment.strip())

    def replace_parenthetical(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if content:
            comment_parts.append(content)
        return " "

    line = re.sub(
        r"\(([^)]*)\)",
        replace_parenthetical,
        line,
    )
    comment = " | ".join(
        part for part in comment_parts if part
    )
    return line.strip(), comment or None


def tokenize_gcode(text: str) -> list[GCodeLine]:
    lines: list[GCodeLine] = []

    for line_number, raw_line in enumerate(
        text.splitlines(),
        start=1,
    ):
        code, comment = split_comment(raw_line)
        if not code:
            lines.append(
                GCodeLine(
                    line_number=line_number,
                    raw=raw_line,
                    code="",
                    comment=comment,
                    tokens=(),
                )
            )
            continue

        tokens = tuple(
            GCodeToken(
                letter=match.group(1).upper(),
                raw_value=match.group(2),
                line_number=line_number,
            )
            for match in TOKEN_RE.finditer(code)
        )

        lines.append(
            GCodeLine(
                line_number=line_number,
                raw=raw_line,
                code=code,
                comment=comment,
                tokens=tokens,
            )
        )

    return lines
