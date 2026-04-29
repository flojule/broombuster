"""
Layout invariants for the BroomBuster repository.

After Step 2.7 the source code lives under a single importable package
(`src/broombuster/`). Tests, scripts, and the editable install all reach
into that one tree — there is no longer any reason for production code
to insert paths at runtime.

Scripts under scripts/ are intentionally allowed to keep sys.path tweaks
because they may be run before `pip install -e .` has been executed.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "src" / "broombuster"


def _sources_in_package():
    for p in _PKG.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def test_no_sys_path_insert_in_production_code():
    """src/broombuster/** must not contain sys.path.insert.

    Adding `sys.path.insert(0, …)` is a code smell that only the test or
    script entry points needed before pyproject.toml configured the
    interpreter path. New contributors copy-paste it without realising;
    this guard fails CI when that happens.
    """
    offenders = []
    for path in _sources_in_package():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "sys.path.insert" in stripped:
                offenders.append(f"{path.relative_to(_ROOT)}:{lineno}: {stripped}")

    assert not offenders, (
        "sys.path.insert calls found in production code. The editable "
        "install (pip install -e .) makes these unnecessary. Remove them.\n  "
        + "\n  ".join(offenders)
    )


def test_pyproject_toml_present():
    """pyproject.toml is the single source of truth for deps + tool config."""
    assert (_ROOT / "pyproject.toml").is_file()


def test_legacy_requirements_files_absent():
    """The split requirements.txt × 2 are gone (replaced by pyproject deps)."""
    assert not (_ROOT / "requirements.txt").exists(), (
        "Top-level requirements.txt is obsolete; deps live in pyproject.toml"
    )
    assert not (_ROOT / "api" / "requirements.txt").exists(), (
        "api/requirements.txt is obsolete; api deps live under "
        "[project.optional-dependencies].api in pyproject.toml"
    )
    assert not (_ROOT / "ruff.toml").exists(), (
        "ruff.toml is folded into pyproject.toml under [tool.ruff]"
    )


def test_legacy_top_level_module_dirs_absent():
    """src/, api/, cli/ no longer hold standalone modules — everything is
    under src/broombuster/."""
    assert not (_ROOT / "api").exists(), (
        "Top-level api/ is gone — moved to src/broombuster/api/"
    )
    assert not (_ROOT / "cli").exists(), (
        "Top-level cli/ is gone — moved to src/broombuster/cli/"
    )
    # No bare modules directly under src/ — only the broombuster package.
    bare_modules = [
        p.name for p in (_ROOT / "src").iterdir()
        if p.is_file() and p.suffix == ".py"
    ]
    assert not bare_modules, (
        f"Bare modules under src/ should live in src/broombuster/ instead: "
        f"{bare_modules}"
    )
    assert not (_ROOT / "src" / "notification.py").exists(), (
        "The notification.py compat shim was removed; callers import "
        "compose_message from broombuster.domains.sweeping directly."
    )
