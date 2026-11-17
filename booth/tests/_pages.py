"""A booth page with the scripts it loads from /static, as one text.

The admin, help and template pages keep their script in files since the
kiosk CSP (R2-U1); tests that look for a piece of page logic read both.
"""
from __future__ import annotations

import re
from pathlib import Path


def page(path: Path) -> str:
    text = Path(path).read_text(encoding="utf-8")
    for src in re.findall(r'<script src="/static/([^"]+)"', text):
        text += "\n" + (Path(path).parent / "static" / src).read_text(encoding="utf-8")
    return text
