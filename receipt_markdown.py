"""A deliberately small Markdown-like formatter for native ESC/POS text."""

from __future__ import annotations

from dataclasses import dataclass, replace


PRINT_WIDTH = 504
FONT_METRICS = {
    "a": {"cell_width": 12, "cell_height": 24},
    "b": {"cell_width": 9, "cell_height": 17},
}


@dataclass(frozen=True)
class TextStyle:
    font: str = "a"
    underline: int = 0
    invert: bool = False
    width: int = 1
    height: int = 1

    def as_dict(self) -> dict:
        metrics = FONT_METRICS[self.font]
        return {
            "font": self.font,
            "underline": self.underline,
            "invert": self.invert,
            "width": self.width,
            "height": self.height,
            "cell_width": metrics["cell_width"],
            "cell_height": metrics["cell_height"],
        }


@dataclass(frozen=True)
class Run:
    text: str
    style: TextStyle


@dataclass(frozen=True)
class LogicalLine:
    runs: tuple[Run, ...]
    align: str = "left"


def normalize_source(source: str) -> str:
    """Normalize newlines and keep the native-printer character set explicit."""
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    invalid = [char for char in source if char != "\n" and not 32 <= ord(char) <= 126]
    if invalid:
        raise ValueError("Receipt Markdown supports printable ASCII and newlines only.")
    return source


def _coalesce(runs: list[Run]) -> tuple[Run, ...]:
    combined: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if combined and combined[-1].style == run.style:
            previous = combined[-1]
            combined[-1] = Run(previous.text + run.text, previous.style)
        else:
            combined.append(run)
    return tuple(combined)


def parse_inline(text: str, style: TextStyle | None = None) -> tuple[Run, ...]:
    """Parse the supported inline spans; unmatched marks stay literal."""
    style = style or TextStyle()
    delimiters = (
        ("++", "underline"),
        ("==", "invert"),
    )
    runs: list[Run] = []
    cursor = 0

    while cursor < len(text):
        matches = [
            (text.find(mark, cursor), mark, attribute)
            for mark, attribute in delimiters
            if text.find(mark, cursor) != -1
        ]
        if not matches:
            runs.append(Run(text[cursor:], style))
            break
        index, mark, attribute = min(matches, key=lambda match: match[0])
        closing = text.find(mark, index + len(mark))
        if closing == -1:
            runs.append(Run(text[cursor:], style))
            break
        if index > cursor:
            runs.append(Run(text[cursor:index], style))

        inner = text[index + len(mark) : closing]
        if attribute == "underline":
            inner_style = replace(style, underline=1)
        else:
            inner_style = replace(style, invert=True)

        runs.extend(parse_inline(inner, inner_style))
        cursor = closing + len(mark)

    return _coalesce(runs)


def _directive_style(tokens: list[str]) -> tuple[TextStyle, str]:
    style = TextStyle()
    align = "left"
    supported = {
        "font-b",
        "center",
        "right",
        "double-size",
    }
    unknown = [token for token in tokens if token not in supported]
    if unknown:
        raise ValueError(f"Unsupported receipt directive: {unknown[0]}.")

    for token in tokens:
        if token == "font-b":
            style = replace(style, font="b")
        elif token in {"center", "right"}:
            align = token
        elif token == "double-size":
            style = replace(style, width=2, height=2)
    return style, align


def _source_blocks(source: str):
    """Yield source lines with their block style and alignment."""
    directive_style: TextStyle | None = None
    directive_align = "left"
    for raw_line in normalize_source(source).split("\n"):
        stripped = raw_line.strip()
        if stripped.startswith(":::"):
            if stripped == ":::":
                directive_style = None
                directive_align = "left"
            elif directive_style is not None:
                raise ValueError("Receipt directives cannot be nested.")
            else:
                directive_style, directive_align = _directive_style(
                    stripped[3:].strip().split()
                )
            continue

        yield (
            raw_line,
            directive_style or TextStyle(),
            directive_align if directive_style is not None else "left",
        )

    if directive_style is not None:
        raise ValueError("Close the receipt directive with :::.")


def parse_document(source: str) -> list[LogicalLine]:
    """Parse the supported receipt-Markdown blocks into styled logical lines."""
    parsed: list[LogicalLine] = []
    for raw_line, style, align in _source_blocks(source):
        if raw_line.strip() == "---":
            parsed.append(LogicalLine((Run("-" * 42, TextStyle()),), "left"))
        else:
            parsed.append(LogicalLine(parse_inline(raw_line, style), align))

    return parsed


def editor_document(source: str) -> dict:
    """Return semantic blocks for the single-pane browser editor.

    The editor deliberately exposes only constructs that the native printer
    renderer understands. Inline runs are relative to the block style so the
    browser can edit them without exposing Markdown punctuation.
    """
    blocks: list[dict] = []
    for raw_line, style, align in _source_blocks(source):
        is_rule = raw_line.strip() == "---"
        runs = parse_inline("") if is_rule else parse_inline(raw_line)
        blocks.append(
            {
                "kind": "rule" if is_rule else "paragraph",
                "align": align,
                "style": style.as_dict(),
                "runs": [
                    {"text": run.text, **run.style.as_dict()}
                    for run in runs
                ],
            }
        )
    return {"width": PRINT_WIDTH, "blocks": blocks}


def layout_document(source: str, width: int = PRINT_WIDTH) -> list[LogicalLine]:
    """Wrap mixed-style runs against the printer's dot width."""
    laid_out: list[LogicalLine] = []
    for logical in parse_document(source):
        if not logical.runs:
            laid_out.append(LogicalLine((), logical.align))
            continue

        current: list[Run] = []
        used = 0
        for run in logical.runs:
            metrics = FONT_METRICS[run.style.font]
            character_width = metrics["cell_width"] * run.style.width
            for character in run.text:
                if used and used + character_width > width:
                    laid_out.append(LogicalLine(_coalesce(current), logical.align))
                    current = []
                    used = 0
                current.append(Run(character, run.style))
                used += character_width
        laid_out.append(LogicalLine(_coalesce(current), logical.align))
    return laid_out


def print_document(printer, source: str) -> None:
    """Send laid-out runs using native ESC/POS text modes."""
    printer.text("\n")
    for line in layout_document(source):
        for run in line.runs:
            printer.set(
                align=line.align,
                font=run.style.font,
                underline=run.style.underline,
                invert=run.style.invert,
                custom_size=True,
                width=run.style.width,
                height=run.style.height,
            )
            printer.text(run.text)
        printer.text("\n")
    printer.cut(feed=False)
