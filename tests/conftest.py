import os
import tempfile

# Set before broombuster modules are imported (they read the env at import):
# DEV_MODE skips JWT, and the SQLite DB lives in a throwaway directory so a
# test run never writes the repo's data/app.sqlite.
os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="bb-test-"), "app.sqlite"))

import pytest  # noqa: E402

from broombuster import data_loader  # noqa: E402


@pytest.fixture(scope="session")
def bay_area_gdf():
    return data_loader.load_region_data("bay_area")


@pytest.fixture(scope="session")
def bay_area_3857(bay_area_gdf):
    return bay_area_gdf.to_crs("EPSG:3857")


@pytest.fixture(scope="session")
def chicago_gdf():
    return data_loader.load_region_data("chicago")


@pytest.fixture(scope="session")
def chicago_3857(chicago_gdf):
    return chicago_gdf.to_crs("EPSG:3857")


@pytest.fixture(scope="session")
def app_client():
    """One TestClient for the session: the app's startup loads every city once."""
    from fastapi.testclient import TestClient

    from broombuster.api.app import app

    with TestClient(app) as client:
        yield client
