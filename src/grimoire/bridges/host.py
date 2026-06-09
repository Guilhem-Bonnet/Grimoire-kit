"""HostBridge — detect current host and retrieve its capability manifest."""

from __future__ import annotations

import os

from grimoire.bridges.schemas import (
    _HOST_REGISTRY,
    CLAUDE_CODE_CLI_MANIFEST,
    CODEX_MANIFEST,
    GITHUB_COPILOT_MANIFEST,
    FallbackMode,
    HostCapabilityManifest,
    HostFallback,
    HostHooks,
    HostId,
)


class HostBridge:
    """Provides host detection and capability manifest lookup.

    Usage::

        bridge = HostBridge()
        manifest = bridge.detect()
        if manifest.hooks.pre_tool_use:
            # Wire PreToolUse hook
    """

    def __init__(self, override_host_id: HostId | None = None) -> None:
        self._override = override_host_id

    def detect(self) -> HostCapabilityManifest:
        """Detect the current host environment and return its manifest.

        Detection order:
        1. Override (for testing)
        2. GRIMOIRE_HOST_ID env var
        3. Heuristics from environment variables
        4. Fallback: UNKNOWN → minimal manifest
        """
        if self._override is not None:
            return _HOST_REGISTRY.get(self._override, self._unknown_manifest())
        env_host = os.environ.get("GRIMOIRE_HOST_ID", "").strip()
        if env_host:
            try:
                host_id = HostId(env_host)
                return _HOST_REGISTRY.get(host_id, self._unknown_manifest())
            except ValueError:
                pass
        # Heuristic detection
        if os.environ.get("CLAUDE_CODE_ENTRYPOINT") or os.environ.get("ANTHROPIC_API_KEY"):
            return CLAUDE_CODE_CLI_MANIFEST
        if os.environ.get("CODEX_ENV") or os.environ.get("OPENAI_API_KEY"):
            return CODEX_MANIFEST
        if os.environ.get("GITHUB_TOKEN") or os.environ.get("COPILOT_AGENT"):
            return GITHUB_COPILOT_MANIFEST
        return self._unknown_manifest()

    def get_manifest(self, host_id: HostId) -> HostCapabilityManifest:
        return _HOST_REGISTRY.get(host_id, self._unknown_manifest())

    def supports_hook(self, hook_name: str, host_id: HostId | None = None) -> bool:
        manifest = self.get_manifest(host_id) if host_id else self.detect()
        return bool(getattr(manifest.hooks, hook_name, False))

    @staticmethod
    def _unknown_manifest() -> HostCapabilityManifest:
        return HostCapabilityManifest(
            host_id=HostId.UNKNOWN,
            display_name="Unknown Host",
            hooks=HostHooks(),
            fallback=HostFallback(
                mode=FallbackMode.PREVIEW_ONLY,
                required_controls=("preview_before_write", "explicit_proof_for_risky_changes"),
            ),
        )

    def all_manifests(self) -> list[HostCapabilityManifest]:
        return list(_HOST_REGISTRY.values())
