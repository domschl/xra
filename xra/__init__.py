"""xra — rsync-based backup tool.

The package version is defined once, in pyproject.toml.
It is exposed here via installed package metadata.
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("xra")
except PackageNotFoundError:  # running from a source tree without install metadata
    __version__ = "unknown"
