"""Optional hot-memory adapters for transient Memory OS state."""

from __future__ import annotations

import importlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HotMemoryStatus:
    """Health report for the optional hot-memory layer."""

    backend: str
    enabled: bool
    healthy: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "enabled": self.enabled,
            "healthy": self.healthy,
            "detail": dict(self.detail),
        }


class RedisHotMemory:
    """Redis-backed TTL memory for session-local, non-authoritative state."""

    def __init__(
        self,
        redis_url: str,
        *,
        namespace: str = "grimoire",
        default_ttl_seconds: int = 3600,
        lease_ttl_seconds: int = 120,
        client: Any | None = None,
    ) -> None:
        if not redis_url:
            raise ValueError("redis_url is required for Redis hot memory")
        if default_ttl_seconds <= 0:
            raise ValueError("default_ttl_seconds must be greater than zero")
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be greater than zero")
        self.redis_url = redis_url
        self.namespace = _normalize_namespace(namespace)
        self.default_ttl_seconds = default_ttl_seconds
        self.lease_ttl_seconds = lease_ttl_seconds
        self._client = client if client is not None else _load_redis_client(redis_url)

    def health_check(self) -> HotMemoryStatus:
        try:
            pong = self._client.ping()
        except (ConnectionError, TimeoutError, OSError) as exc:
            return HotMemoryStatus(
                backend="redis",
                enabled=True,
                healthy=False,
                detail={
                    "namespace": self.namespace,
                    "default_ttl_seconds": self.default_ttl_seconds,
                    "lease_ttl_seconds": self.lease_ttl_seconds,
                    "reason": str(exc),
                },
            )
        return HotMemoryStatus(
            backend="redis",
            enabled=True,
            healthy=bool(pong),
            detail={
                "namespace": self.namespace,
                "default_ttl_seconds": self.default_ttl_seconds,
                "lease_ttl_seconds": self.lease_ttl_seconds,
            },
        )

    def store(
        self,
        key: str,
        value: str,
        *,
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ttl = ttl_seconds or self.default_ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        now = int(time.time())
        payload = {
            "key": key,
            "value": value,
            "namespace": self.namespace,
            "metadata": dict(metadata or {}),
            "created_at": now,
            "expires_at": now + ttl,
        }
        self._client.set(self._hot_key(key), json.dumps(payload, sort_keys=True), ex=ttl)
        return payload

    def recall(self, key: str) -> dict[str, Any] | None:
        raw = self._client.get(self._hot_key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        loaded = json.loads(str(raw))
        if not isinstance(loaded, dict):
            raise ValueError(f"Invalid Redis hot-memory payload for key {key!r}")
        return loaded

    def delete(self, key: str) -> bool:
        return int(self._client.delete(self._hot_key(key))) > 0

    def touch(self, key: str, *, ttl_seconds: int | None = None) -> bool:
        ttl = ttl_seconds or self.default_ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        return bool(self._client.expire(self._hot_key(key), ttl))

    def acquire_lease(self, key: str, *, token: str | None = None, ttl_seconds: int | None = None) -> str | None:
        lease_token = token or uuid.uuid4().hex
        ttl = ttl_seconds or self.lease_ttl_seconds
        if ttl <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        acquired = bool(self._client.set(self._lease_key(key), lease_token, ex=ttl, nx=True))
        return lease_token if acquired else None

    def release_lease(self, key: str, token: str) -> bool:
        lease_key = self._lease_key(key)
        current = self._client.get(lease_key)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if current != token:
            return False
        return int(self._client.delete(lease_key)) > 0

    def publish(self, channel: str, payload: dict[str, Any]) -> int:
        return int(self._client.publish(self._channel(channel), json.dumps(payload, sort_keys=True)))

    def _hot_key(self, key: str) -> str:
        return f"{self.namespace}:hot:{key}"

    def _lease_key(self, key: str) -> str:
        return f"{self.namespace}:lease:{key}"

    def _channel(self, channel: str) -> str:
        return f"{self.namespace}:stream:{channel}"


def _normalize_namespace(namespace: str) -> str:
    normalized = "".join(char if char.isalnum() or char in {"-", "_", ":"} else "-" for char in namespace.strip())
    return normalized or "grimoire"


def _load_redis_client(redis_url: str) -> Any:
    try:
        redis_module = importlib.import_module("redis")
    except ImportError as exc:
        raise RuntimeError("Redis hot memory requires the 'redis' extra: install grimoire-kit[redis]") from exc
    return redis_module.Redis.from_url(redis_url, decode_responses=True)
