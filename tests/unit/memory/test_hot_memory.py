"""Tests for the optional Redis hot-memory layer."""

from __future__ import annotations

import json

from grimoire.core.config import GrimoireConfig
from grimoire.memory.architecture import build_memory_architecture_status
from grimoire.memory.backends.base import BackendStatus
from grimoire.memory.hot import RedisHotMemory


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []

    def ping(self) -> bool:
        return True

    def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.expirations.pop(key, None)
        return int(existed)

    def expire(self, key: str, ttl: int) -> bool:
        if key not in self.values:
            return False
        self.expirations[key] = ttl
        return True

    def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


def _config() -> GrimoireConfig:
    return GrimoireConfig.from_dict({
        "project": {"name": "redis-test"},
        "memory": {
            "backend": "local",
            "collection_prefix": "forge:test",
            "short_term_backend": "redis",
            "redis_url": "redis://localhost:6379/0",
        },
    })


def test_redis_hot_memory_stores_ttl_payload() -> None:
    fake = FakeRedis()
    hot = RedisHotMemory(
        "redis://localhost:6379/0",
        namespace="forge test",
        default_ttl_seconds=60,
        lease_ttl_seconds=15,
        client=fake,
    )

    payload = hot.store("ctx", "draft context", metadata={"task": "R8"})
    recalled = hot.recall("ctx")

    assert payload["metadata"] == {"task": "R8"}
    assert recalled is not None
    assert recalled["value"] == "draft context"
    assert fake.expirations["forge-test:hot:ctx"] == 60


def test_redis_hot_memory_lease_requires_matching_token() -> None:
    fake = FakeRedis()
    hot = RedisHotMemory("redis://localhost:6379/0", namespace="forge", client=fake)

    token = hot.acquire_lease("task")

    assert token is not None
    assert hot.acquire_lease("task") is None
    assert hot.release_lease("task", "wrong") is False
    assert hot.release_lease("task", token) is True


def test_redis_hot_memory_publish_uses_namespaced_channel() -> None:
    fake = FakeRedis()
    hot = RedisHotMemory("redis://localhost:6379/0", namespace="forge", client=fake)

    published = hot.publish("events", {"kind": "memory.promoted"})

    assert published == 1
    channel, payload = fake.published[0]
    assert channel == "forge:stream:events"
    assert json.loads(payload) == {"kind": "memory.promoted"}


def test_memory_architecture_marks_redis_hot_layer_ready() -> None:
    status = BackendStatus(
        backend="local",
        healthy=True,
        entries=0,
        detail={
            "hot_memory": {
                "backend": "redis",
                "enabled": True,
                "healthy": True,
                "detail": {
                    "namespace": "forge:test",
                    "default_ttl_seconds": 3600,
                    "lease_ttl_seconds": 120,
                },
            }
        },
    )

    architecture = build_memory_architecture_status(_config(), backend_status=status)

    short_term = next(layer for layer in architecture.layers if layer.id == "short_term")
    assert short_term.state == "ready"
    assert short_term.implemented is True
    assert short_term.evidence["namespace"] == "forge:test"
