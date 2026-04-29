"""
Smoke tests for the broombuster package surface.

After Step 2.7 the codebase lives under a single importable package. These
tests verify that every public entry point is reachable through that one
import root, so the test suite catches breakage if files get renamed or
moved without updating the package metadata.
"""

import os

import pytest

# DEV_MODE so importing broombuster.api.app does not fail JWT_SECRET check.
os.environ.setdefault("DEV_MODE", "1")


def test_root_import_and_version():
    import broombuster

    assert isinstance(broombuster.__version__, str) and broombuster.__version__


def test_api_app_exposes_fastapi_instance():
    from broombuster.api import app as api_module

    # The module exposes a FastAPI `app` attribute that uvicorn binds to.
    assert hasattr(api_module, "app"), "broombuster.api.app must export `app`"
    fastapi_app = api_module.app
    # Light duck-typing rather than importing fastapi just to isinstance-check.
    assert hasattr(fastapi_app, "routes"), "expected a FastAPI instance"
    assert any(
        getattr(r, "path", None) == "/health" for r in fastapi_app.routes
    ), "broombuster.api.app must register /health"


def test_cli_main_callable_is_present():
    from broombuster.cli import main as cli_main

    assert callable(cli_main.main), (
        "broombuster.cli.main must define a `main()` entry point so "
        "`python -m broombuster.cli.main` works"
    )


def test_domains_package_exports_sweeping():
    from broombuster.domains import sweeping

    assert callable(sweeping.compose_message), (
        "broombuster.domains.sweeping must export compose_message"
    )


@pytest.mark.parametrize(
    "module_path",
    [
        "broombuster.analysis",
        "broombuster.car",
        "broombuster.cities",
        "broombuster.config",
        "broombuster.data_loader",
        "broombuster.email_alerts",
        "broombuster.gps",
        "broombuster.maps",
        "broombuster.normalize",
        "broombuster.resolve",
    ],
)
def test_top_level_modules_importable(module_path):
    """Every top-level module is reachable via the broombuster package."""
    import importlib

    mod = importlib.import_module(module_path)
    assert mod is not None
