"""Host Bridge — capability manifests and host detection for Grimoire Agent OS."""

from grimoire.bridges.host import HostBridge
from grimoire.bridges.schemas import (
    FallbackMode,
    HostCapabilityManifest,
    HostHooks,
    HostId,
)

__all__ = [
    "FallbackMode",
    "HostBridge",
    "HostCapabilityManifest",
    "HostHooks",
    "HostId",
]
