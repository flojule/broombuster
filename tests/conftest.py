import pytest

from broombuster import data_loader


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
