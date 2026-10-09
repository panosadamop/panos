"""Shared adapter behaviour: the egress guard every adapter applies before network I/O."""

from collections.abc import Sequence

from panos_core.ai.privacy import assert_cloud_egress_allowed, assert_cloud_texts_allowed
from panos_core.ai.types import ChatRequest, Locality
from panos_core.domain.enums import PrivacyMode


class AdapterBase:
    """Every adapter calls ``_guard`` first. For a CLOUD endpoint (including an Ollama or
    OpenAI-compatible server that is *not* on loopback/trusted LAN) LOCAL_ONLY or unredacted
    payloads raise :class:`PrivacyViolationError` before any byte leaves the process."""

    id: str
    kind: str
    locality: Locality

    def _guard(self, request: ChatRequest) -> None:
        if self.locality == Locality.CLOUD:
            assert_cloud_egress_allowed(request, self.id)

    def _guard_texts(self, labels: Sequence[PrivacyMode]) -> None:
        if self.locality == Locality.CLOUD:
            assert_cloud_texts_allowed(labels, self.id)
