# Single source of truth for the package version. pyproject.toml reads it at build time,
# and binaries.py derives the GitHub release / container image tag ("v<version>") from
# it, so every wheel pairs with the LCModel artifacts built alongside it.
__version__ = "0.3.2"
