"""HTTP client for the local node, configured from the runtime file."""

import json
from collections.abc import Iterator
from typing import Any

import httpx

from panos_core import API_VERSION
from panos_core.config import PanosSettings
from panos_core.config.runtime import RuntimeInfo, read_runtime_file


class NodeNotRunningError(RuntimeError):
    pass


class NodeClient:
    def __init__(self, info: RuntimeInfo, *, timeout: float = 10.0) -> None:
        self.info = info
        self._client = httpx.Client(
            base_url=f"{info.base_url}/api/{API_VERSION}",
            headers={"Authorization": f"Bearer {info.token}"},
            timeout=timeout,
            trust_env=False,  # never route loopback traffic through a proxy
        )

    @classmethod
    def from_settings(cls, settings: PanosSettings) -> "NodeClient":
        info = read_runtime_file(settings.runtime_file)
        if info is None:
            raise NodeNotRunningError("no running Panos node found")
        return cls(info)

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params={k: v for k, v in params.items() if v})

    def post(self, path: str, body: dict[str, Any], **params: Any) -> dict[str, Any]:
        query = {k: v for k, v in params.items() if v is not None}
        return self._request("POST", path, json=body, params=query or None)

    def delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    def stream(
        self, path: str, body: dict[str, Any], *, timeout: float = 600.0
    ) -> Iterator[dict[str, Any]]:
        """POST and yield NDJSON events as they arrive (model answers can take minutes)."""
        try:
            with self._client.stream("POST", path, json=body, timeout=timeout) as response:
                if response.is_error:
                    response.read()
                    response.raise_for_status()
                for line in response.iter_lines():
                    if line.strip():
                        event: dict[str, Any] = json.loads(line)
                        yield event
        except httpx.ConnectError as exc:
            raise NodeNotRunningError(
                f"Panos node at {self.info.base_url} is not reachable (stale runtime file?)"
            ) from exc

    def patch(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", path, json=body)

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", path, json=body)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise NodeNotRunningError(
                f"Panos node at {self.info.base_url} is not reachable (stale runtime file?)"
            ) from exc
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        data: dict[str, Any] = response.json()
        return data

    def close(self) -> None:
        self._client.close()
