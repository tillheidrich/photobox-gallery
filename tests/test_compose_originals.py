"""docker-compose: the web service knows the NAS offload settings too.

Until 15.11.2026 only ``pruner`` had the ``ORIGINALS_*`` variables: it moved
originals to the NAS, and ``web`` — which streams them back on download —
did not know the NAS and answered 503.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(service: str, text: str) -> set:
    block = re.split(r"\n  (?=[a-z_]+:\n)", text.split("services:", 1)[1])
    body = next(b for b in block if b.lstrip().startswith(service + ":"))
    return set(re.findall(r"- (ORIGINALS_[A-Z_]+)=\$\{\1:-[^}]*\}", body))


def test_web_and_pruner_carry_the_same_originals_settings():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    pruner, web = _env("pruner", text), _env("web", text)
    assert pruner and pruner <= web, (pruner, web)
