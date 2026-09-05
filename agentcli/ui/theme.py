"""Design system, color palette, badges, and box drawing primitives for AgentCLI UI."""

from __future__ import annotations

import os
import sys

# ANSI Escape Sequences
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

COLOR_ACCENT = "\033[38;5;45m"       # Bright Cyan
COLOR_SUCCESS = "\033[38;5;48m"      # Emerald Green
COLOR_WARNING = "\033[38;5;214m"     # Warm Amber
COLOR_ERROR = "\033[38;5;203m"       # Crimson Red
COLOR_MUTED = "\033[38;5;242m"       # Slate Gray
COLOR_HEADER = "\033[48;5;236;38;5;255m"  # Dark Gray Background with White Text

# Box Drawing Characters
BOX_ROUNDED = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│", "vl": "├", "vr": "┤", "hu": "┴", "hd": "┬",
}

BOX_SQUARE = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "h": "─", "v": "│", "vl": "├", "vr": "┤", "hu": "┴", "hd": "┬",
}

BOX_ASCII = {
    "tl": "+", "tr": "+", "bl": "+", "br": "+",
    "h": "-", "v": "|", "vl": "+", "vr": "+", "hu": "+", "hd": "+",
}


def is_unicode_supported() -> bool:
    """Check if standard output supports UTF-8 box drawing characters."""
    if os.environ.get("AGENTCLI_PLAIN", "").lower() in ("1", "true"):
        return False
    encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    return "utf" in encoding.lower()


def render_badge(label: str, style: str = "accent", no_color: bool = False) -> str:
    """Render a styled terminal pill / badge (e.g. [ gemma-4-31b ])."""
    if no_color:
        return f"[{label}]"

    color_map = {
        "accent": COLOR_ACCENT,
        "success": COLOR_SUCCESS,
        "warning": COLOR_WARNING,
        "error": COLOR_ERROR,
        "muted": COLOR_MUTED,
    }
    c = color_map.get(style, COLOR_ACCENT)
    return f"{c}{BOLD}[{RESET}{c} {label} {RESET}{c}{BOLD}]{RESET}"


def render_progress_bar(
    current: float,
    total: float,
    width: int = 16,
    no_color: bool = False,
) -> str:
    """Render a visual terminal progress gauge (e.g. [████░░░░] 50.0%)."""
    if total <= 0.0:
        pct = 0.0
    else:
        pct = min(1.0, max(0.0, current / total))

    filled = int(pct * width)
    unfilled = width - filled

    if is_unicode_supported():
        fill_char = "█"
        empty_char = "░"
    else:
        fill_char = "#"
        empty_char = "-"

    bar = (fill_char * filled) + (empty_char * unfilled)
    pct_text = f"{pct * 100:.1f}%"

    if no_color:
        return f"[{bar}] {pct_text}"

    color = COLOR_SUCCESS if pct < 0.75 else (COLOR_WARNING if pct < 0.90 else COLOR_ERROR)
    return f"{COLOR_MUTED}[{RESET}{color}{bar}{RESET}{COLOR_MUTED}] {RESET}{BOLD}{pct_text}{RESET}"


def draw_box(
    title: str,
    content: str,
    width: int = 80,
    rounded: bool = True,
    no_color: bool = False,
) -> str:
    """Draw a framed container box around content with title header."""
    use_utf8 = is_unicode_supported()
    chars = BOX_ROUNDED if (use_utf8 and rounded) else (BOX_SQUARE if use_utf8 else BOX_ASCII)

    color_border = "" if no_color else COLOR_MUTED
    color_title = "" if no_color else (COLOR_ACCENT + BOLD)
    color_reset = "" if no_color else RESET

    # Top border with title
    title_str = f" {title} " if title else ""
    rem_width = max(0, width - 3 - len(title_str))
    top_line = f"{color_border}{chars['tl']}{chars['h']}{color_title}{title_str}{color_border}{chars['h'] * rem_width}{chars['tr']}{color_reset}"

    lines = [top_line]

    # Inner lines
    inner_width = width - 4
    for raw_line in content.splitlines():
        # Truncate or pad line to inner width
        line_clean = raw_line[:inner_width]
        padding = " " * max(0, inner_width - len(line_clean))
        lines.append(f"{color_border}{chars['v']}{color_reset} {line_clean}{padding} {color_border}{chars['v']}{color_reset}")

    # Bottom border
    bottom_line = f"{color_border}{chars['bl']}{chars['h'] * (width - 2)}{chars['br']}{color_reset}"
    lines.append(bottom_line)

    return "\n".join(lines)
