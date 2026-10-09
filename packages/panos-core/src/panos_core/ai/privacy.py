"""Privacy enforcement primitives (ADR-008, BR-004, NFR-SEC-005).

Two independent enforcement points use these:
1. the router never selects a cloud model for LOCAL_ONLY content;
2. every cloud adapter calls :func:`assert_cloud_egress_allowed` immediately before any
   network I/O, so a code path that skips the router still cannot leak.
"""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlparse

from panos_core.ai.types import ChatRequest, Locality
from panos_core.domain.enums import PrivacyMode
from panos_core.redaction import redact_text


class PrivacyViolationError(RuntimeError):
    """A LOCAL_ONLY (or unredacted) payload was about to leave the machine."""


def max_label(labels: Iterable[PrivacyMode]) -> PrivacyMode:
    """LOCAL_ONLY dominates: anything derived from local-only content stays local-only."""
    return (
        PrivacyMode.LOCAL_ONLY
        if any(label == PrivacyMode.LOCAL_ONLY for label in labels)
        else PrivacyMode.STANDARD
    )


def assert_cloud_egress_allowed(request: ChatRequest, provider_id: str) -> None:
    if request.privacy == PrivacyMode.LOCAL_ONLY:
        raise PrivacyViolationError(
            f"refusing to send LOCAL_ONLY content to cloud provider {provider_id!r}"
        )
    if not request.redacted:
        raise PrivacyViolationError(
            f"refusing to send an unredacted request to cloud provider {provider_id!r}"
        )


def assert_cloud_texts_allowed(labels: Iterable[PrivacyMode], provider_id: str) -> None:
    """Egress check for embedding requests (texts + their labels)."""
    if max_label(labels) == PrivacyMode.LOCAL_ONLY:
        raise PrivacyViolationError(
            f"refusing to embed LOCAL_ONLY content with cloud provider {provider_id!r}"
        )


def redact_for_cloud(text: str) -> str:
    """Secret redaction for STANDARD content sent to a cloud model (NFR-SEC-004).

    Uses known credential formats only (no entropy layer): code legitimately contains
    long identifiers and hashes, and redacting them would corrupt the request.
    """
    return redact_text(text, entropy=False, code_safe=True)


def endpoint_locality(base_url: str | None, *, trusted_lan: bool) -> Locality:
    """Classify an endpoint (ADR-009): loopback is local; LAN only if explicitly trusted."""
    if not base_url:
        return Locality.CLOUD
    host = (urlparse(base_url).hostname or "").lower()
    if host in ("localhost", "localhost.localdomain"):
        return Locality.LOCAL
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return Locality.CLOUD  # DNS names other than localhost may resolve anywhere
    if ip.is_loopback:
        return Locality.LOCAL
    if ip.is_private and trusted_lan:
        return Locality.LOCAL
    return Locality.CLOUD
