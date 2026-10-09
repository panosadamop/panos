"""Application registry: discovery, aliases, preferred IDEs and launching (FR-003, BR-006).

The service lives in ``panos_core.apps.service`` (imported explicitly to avoid an import
cycle with the platform desktop adapters, which depend on the catalog).
"""

from panos_core.apps.catalog import CATALOG, CatalogEntry, find_entry

__all__ = ["CATALOG", "CatalogEntry", "find_entry"]
