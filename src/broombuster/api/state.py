"""Region GeoDataFrames (EPSG:3857), loaded and indexed once at startup."""

from broombuster import data_loader, layers
from broombuster.cities import REGIONS

_regions: dict = {}  # region_key -> GeoDataFrame (EPSG:3857)


def load_all() -> None:
    """Load every region and build its request indexes; raises on bad data."""
    for rk in REGIONS:
        gdf = data_loader.load_region_data(rk, crs="EPSG:3857", compact=True)
        gdf.sindex  # noqa: B018 — builds the STRtree
        _regions[rk] = gdf
    layers.load_all()


def region_gdf(region_key: str):
    return _regions[region_key]
