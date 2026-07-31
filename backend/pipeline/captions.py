"""Step 4a — generate animated word-by-word .ass captions for one clip.

Style: bold white text with heavy black outline, current word highlighted in
amber, groups of up to CAPTION_MAX_WORDS words shown at once, positioned in the
lower third. This is the standard "Opus Clip / Alex Hormozi" caption look.
"""
from __future__ import annotations

from pathlib import Path

import config


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# BorderStyle 4 = box behind text (libass); falls back visually to 3 on old
# libass. With a box we use a thin outline; without, a thick one for contrast.
_BORDER = (
    f"4,4,0" if config.CAPTION_BOX else "1,7,3"
)
_BACK = config.CAPTION_BOX_COLOR if config.CAPTION_BOX else "&H80000000"

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.OUT_WIDTH}
PlayResY: {config.OUT_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{config.CAPTION_FONT},{config.CAPTION_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,{_BACK},{_BACK},-1,0,0,0,100,100,1,0,{_BORDER},2,60,60,{config.CAPTION_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _pages(words: list[dict]) -> list[list[dict]]:
    """Group words into caption pages: break on word count, char budget
    (so long words get fewer words per page and fit on screen), or pauses."""
    pages, cur, cur_chars = [], [], 0
    for w in words:
        wlen = len(w["word"])
        if cur and (
            len(cur) >= config.CAPTION_MAX_WORDS
            or cur_chars + 1 + wlen > config.CAPTION_MAX_CHARS
            or w["start"] - cur[-1]["end"] > 0.7
        ):
            pages.append(cur)
            cur, cur_chars = [], 0
        cur.append(w)
        cur_chars += (1 if cur_chars else 0) + wlen
    if cur:
        pages.append(cur)
    return pages


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "").replace("}", "").upper()


def build_ass(words: list[dict], clip_start: float, clip_end: float, out_path: Path) -> Path:
    """words: word timestamps in ORIGINAL video time; shifted here to clip time."""
    local = [
        {
            "start": w["start"] - clip_start,
            "end": w["end"] - clip_start,
            "word": w["word"],
        }
        for w in words
        if w["end"] > clip_start and w["start"] < clip_end and w["word"]
    ]

    lines = [HEADER]
    hi = config.CAPTION_HIGHLIGHT
    for page in _pages(local):
        page_end = page[-1]["end"]
        for i, w in enumerate(page):
            start = w["start"]
            # Hold each word until the next word begins (no flicker in pauses)
            end = page[i + 1]["start"] if i + 1 < len(page) else page_end
            end = max(end, start + 0.08)
            parts = []
            for j, pw in enumerate(page):
                token = _esc(pw["word"])
                if j == i:
                    parts.append(f"{{\\1c{hi}}}{token}{{\\1c&H00FFFFFF}}")
                else:
                    parts.append(token)
            text = " ".join(parts)
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{text}\n"
            )

    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path
