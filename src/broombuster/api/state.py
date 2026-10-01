"""Region GeoDataFrames (EPSG:3857), loaded and indexed once at startup."""

from broombuster import analysis, data_loader
from broombuster.cities import REGIONS

_regions: dict = {}  # region_key -> GeoDataFrame (EPSG:3857)


def load_all() -> None:
    """Load every region and build its request indexes; raises on bad data."""
    for rk in REGIONS:
        gdf = data_loader.load_region_data(rk).to_crs("EPSG:3857")
        gdf.sindex  # noqa: B018 — builds the STRtree
        analysis.segment_index(gdf)
        _regions[rk] = gdf


def region_gdf(region_key: str):
    return _regions[region_key]
