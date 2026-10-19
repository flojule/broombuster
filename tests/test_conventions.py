"""Rules from CLAUDE.md that the CSP or a reviewer would otherwise be the only check for."""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# `<button onclick=...>` in markup or in a JS template string; the CSP forbids both.
INLINE_HANDLER = re.compile(r"""<[a-zA-Z][^>]*\son[a-z]+\s*=""")
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", re.IGNORECASE)


def test_frontend_has_no_inline_handlers_or_scripts():
    bad = []
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix not in (".html", ".js") or path.name.startswith("maplibre"):
            continue
        text = path.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if INLINE_HANDLER.search(line):
                bad.append(f"{path.name}:{n}: inline event handler")
        if path.suffix == ".html" and INLINE_SCRIPT.search(text):
            bad.append(f"{path.name}: inline <script>")
    assert not bad, "\n".join(bad)


def test_settings_are_read_in_config_only():
    """ruff's TID251 bans os.environ / os.getenv in code; this catches the other spellings."""
    src = Path(__file__).resolve().parent.parent / "src" / "broombuster"
    pat = re.compile(r"\bfrom os import[^\n]*\b(environ|getenv)\b|\bos\.putenv\b|\bgetenv\(")
    bad = [
        f"{p.name}: {m.group(0)}"
        for p in sorted(src.rglob("*.py"))
        if p.name != "config.py"
        for m in [pat.search(p.read_text())]
        if m
    ]
    assert not bad, "\n".join(bad)
