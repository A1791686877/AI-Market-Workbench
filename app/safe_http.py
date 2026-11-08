from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.logging_config import audit_event


class NetworkDisabledError(RuntimeError):
    pass


class UnsafeEndpointError(RuntimeError):
    pass


@dataclass(frozen=True)
class JsonResponse:
    payload: Any
    sha256: str
    status_code: int


class ReadOnlyHttpClient:
    def __init__(self, allowed_hosts: set[str], timeout_seconds: float = 15.0) -> None:
        self.allowed_hosts = {item.lower() for item in allowed_hosts}
        self.timeout_seconds = timeout_seconds
        self._client = httpx.Client(timeout=self.timeout_seconds, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int | float] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonResponse:
        if not settings.network_enabled:
            raise NetworkDisabledError("network access is disabled by AMW_NETWORK_ENABLED=false")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise UnsafeEndpointError("only absolute HTTPS endpoints are allowed")
        if parsed.username or parsed.password or parsed.hostname.lower() not in self.allowed_hosts:
            raise UnsafeEndpointError(f"host is not allowlisted: {parsed.hostname}")
        if parsed.port not in {None, 443}:
            raise UnsafeEndpointError("non-standard ports are not allowed")

        audit_event(
            "data_request",
            method="GET",
            host=parsed.hostname,
            path=parsed.path,
            query_keys=sorted((params or {}).keys()),
        )
        response = self._client.get(url, params=params, headers=headers)
        if 300 <= response.status_code < 400:
            audit_event(
                "data_request_failed",
                method="GET",
                host=parsed.hostname,
                path=parsed.path,
                status_code=response.status_code,
                reason="REDIRECT_REJECTED",
            )
            raise UnsafeEndpointError("redirects are rejected to preserve the host allowlist")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            audit_event(
                "data_request_failed",
                method="GET",
                host=parsed.hostname,
                path=parsed.path,
                status_code=response.status_code,
                reason="HTTP_STATUS",
            )
            raise
        digest = hashlib.sha256(response.content).hexdigest()
        host_dir = settings.root / "cache" / "http" / re.sub(r"[^a-z0-9.-]", "-", parsed.hostname.lower())
        host_dir.mkdir(parents=True, exist_ok=True)
        raw_path = host_dir / f"{digest}.json"
        if not raw_path.exists():
            temporary = raw_path.with_suffix(".tmp")
            temporary.write_bytes(response.content)
            temporary.replace(raw_path)
        audit_event(
            "data_response",
            method="GET",
            host=parsed.hostname,
            path=parsed.path,
            status_code=response.status_code,
            byte_count=len(response.content),
            raw_sha256=digest,
            raw_cache_path=str(raw_path),
        )
        return JsonResponse(
            payload=response.json(),
            sha256=digest,
            status_code=response.status_code,
        )
