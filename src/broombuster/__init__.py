"""BroomBuster — street-sweeping (and other city-data) schedule alerts.

The package is organised as:

  broombuster.<module>            — shared library (analysis, resolve, …)
  broombuster.api.<module>        — HTTP server (FastAPI)
  broombuster.cli.<module>        — command-line entry point
  broombuster.domains.<plugin>    — city-data domain plugins (sweeping, trash, …)
"""

__version__ = "0.1.0"
