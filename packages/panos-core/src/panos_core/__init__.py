"""Panos core package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("panos-core")
except PackageNotFoundError:  # pragma: no cover - only when running from an unbuilt tree
    __version__ = "0.0.0"

API_VERSION = "v1"

__all__ = ["API_VERSION", "__version__"]
