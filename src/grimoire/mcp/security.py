"""Grimoire MCP — security helpers for MCP server policy classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from grimoire.mcp._instance import _DEFAULT_MCP_POLICY_PATH, _MCP_TRUSTED_REMOTE_HOSTS


def _looks_like_placeholder(value: str) -> bool:
    """Detect template placeholders rather than literal secrets."""
    return "${" in value or value.startswith("YOUR_") or value.endswith(("_TOKEN", "_API_KEY"))


def _secret_field_name(name: str) -> bool:
    """Return whether a field name is likely carrying a secret."""
    lowered = name.lower()
    return any(token in lowered for token in ("authorization", "token", "api_key", "apikey", "secret", "key"))


def _infer_auth_mode(server_name: str, config: dict[str, Any], host: str) -> str:
    """Infer how a server is authenticated, if at all."""
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    for key, value in headers.items():
        if not isinstance(value, str):
            continue
        if _secret_field_name(key):
            return "indirect-header-secret" if _looks_like_placeholder(value) else "hardcoded-header-secret"

    env = config.get("env") if isinstance(config.get("env"), dict) else {}
    for key, value in env.items():
        if not isinstance(value, str):
            continue
        if _secret_field_name(key):
            return "indirect-env-secret" if _looks_like_placeholder(value) else "hardcoded-env-secret"

    args = config.get("args") if isinstance(config.get("args"), list) else []
    for index, arg in enumerate(args):
        if not isinstance(arg, str):
            continue
        lowered = arg.lower()
        if lowered in {"--api-key", "--token", "--auth-token"} and index + 1 < len(args):
            candidate = args[index + 1]
            if isinstance(candidate, str):
                return "indirect-arg-secret" if _looks_like_placeholder(candidate) else "hardcoded-arg-secret"

    trusted = _MCP_TRUSTED_REMOTE_HOSTS.get(host, {})
    if trusted.get("auth_mode"):
        return str(trusted["auth_mode"])
    if host:
        return "none"
    return "not-applicable"


def _infer_mutability(server_name: str, host: str) -> str:
    """Infer an approximate mutability profile for a server."""
    trusted = _MCP_TRUSTED_REMOTE_HOSTS.get(host, {})
    if trusted.get("mutability"):
        return str(trusted["mutability"])

    lowered = server_name.lower()
    if any(token in lowered for token in ("context7", "docs", "documentation")):
        return "read-mostly"
    if any(token in lowered for token in ("github", "grimoire", "playwright", "browser", "git")):
        return "read-write"
    return "unknown"


def _infer_trust_level(config: dict[str, Any], host: str) -> str:
    """Infer whether a server is trusted local, trusted remote, or unreviewed."""
    if host in _MCP_TRUSTED_REMOTE_HOSTS:
        return str(_MCP_TRUSTED_REMOTE_HOSTS[host]["trust_level"])

    command = config.get("command")
    if isinstance(command, str) and command:
        if "${workspaceFolder}" in command or command.startswith(("./", "/")):
            return "workspace-local"
        return "ambient-local"

    if host:
        return "unreviewed-remote"
    return "unknown"


def _infer_transport(config: dict[str, Any]) -> str:
    """Infer transport from config shape."""
    explicit = config.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit
    if isinstance(config.get("command"), str):
        return "stdio"
    if isinstance(config.get("url"), str):
        return "http"
    return "unknown"


def _mcp_server_endpoint(config: dict[str, Any]) -> str:
    """Return the primary endpoint or command for display."""
    for key in ("url", "serverUrl", "httpUrl"):
        value = config.get(key)
        if isinstance(value, str) and value:
            return value
    command = config.get("command")
    if isinstance(command, str):
        return command
    return ""


def _mcp_policy_notes(
    *,
    transport: str,
    trust_level: str,
    auth_mode: str,
    mutability: str,
    risk_flags: list[str],
) -> list[str]:
    """Generate human-readable policy notes for a server."""
    notes: list[str] = []
    if transport == "stdio":
        notes.append("Serveur local: toute commande expose une surface d'exécution locale.")
    if transport in {"http", "streamable-http", "streamableHttp"}:
        notes.append("Serveur distant: les réponses entrent depuis le réseau et doivent rester traitées comme données.")
    if trust_level == "trusted-remote":
        notes.append("Hôte distant reconnu et explicitement autorisé par la policy locale.")
    elif trust_level == "trusted-local":
        notes.append("Serveur local explicitement autorisé par la policy du repo.")
    elif trust_level in {"workspace-local", "ambient-local"}:
        notes.append("Serveur local: vérifier que la commande lancée correspond bien au binaire attendu.")
    elif trust_level == "unreviewed-remote":
        notes.append("Serveur distant non revu: traiter comme non fiable tant qu'une allowlist n'existe pas.")
    if auth_mode.startswith("hardcoded"):
        notes.append("Secret en clair détecté dans la configuration MCP.")
    elif auth_mode.startswith("indirect"):
        notes.append("Secret injecté indirectement: acceptable si la source reste hors dépôt.")
    if "fail-closed-remote-deny" in risk_flags:
        notes.append("Remote refusé par la policy fail-closed du repo.")
    if mutability == "read-write":
        notes.append("Ce serveur expose probablement des opérations mutables; l'usage doit rester intentionnel.")
    if "package-runtime-install" in risk_flags:
        notes.append("La commande dépend d'un exécutable résolu à l'exécution (`npx`).")
    return notes


def _coerce_string_list(value: Any) -> list[str]:
    """Normalize a list of strings from user config."""
    if not isinstance(value, (list, tuple, set)):
        return []
    items: list[str] = []
    for entry in value:
        text = str(entry).strip()
        if text:
            items.append(text)
    return items


def _load_mcp_config(config_path: Path) -> dict[str, Any]:
    """Load a VS Code MCP config file."""
    import json
    with config_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("MCP config root must be an object")
    servers = data.get("servers")
    if servers is None:
        return {"servers": {}}
    if not isinstance(servers, dict):
        raise ValueError("MCP config 'servers' must be an object")
    return data


def _load_mcp_policy(project_root: Path, policy_path: str = _DEFAULT_MCP_POLICY_PATH) -> dict[str, Any]:
    """Load optional MCP policy overrides from the project runtime config."""
    from grimoire.mcp._helpers import _relative_display_path, _resolve_path

    resolved = _resolve_path(policy_path, base=project_root)
    policy: dict[str, Any] = {
        "loaded": False,
        "source": _relative_display_path(resolved, base=project_root),
        "trusted_remote_hosts": sorted(_MCP_TRUSTED_REMOTE_HOSTS),
        "trusted_workspace_servers": [],
        "fail_closed_remote_hosts": False,
        "server_overrides": {},
    }
    if not resolved.is_file():
        return policy

    from grimoire.tools._common import load_yaml

    raw = load_yaml(resolved)
    if raw is None:
        policy["loaded"] = True
        return policy
    if not isinstance(raw, dict):
        msg = f"MCP policy root must be an object: {resolved}"
        raise ValueError(msg)

    combined_hosts = sorted(
        set(policy["trusted_remote_hosts"]) | set(_coerce_string_list(raw.get("trusted_remote_hosts")))
    )
    server_overrides = raw.get("server_overrides")
    policy.update(
        {
            "loaded": True,
            "trusted_remote_hosts": combined_hosts,
            "trusted_workspace_servers": _coerce_string_list(raw.get("trusted_workspace_servers")),
            "fail_closed_remote_hosts": bool(raw.get("fail_closed_remote_hosts", False)),
            "server_overrides": server_overrides if isinstance(server_overrides, dict) else {},
        }
    )
    return policy


def _classify_mcp_server(
    server_name: str,
    config: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a configured MCP server into policy dimensions."""
    effective_policy = policy or _load_mcp_policy(Path.cwd())
    endpoint = _mcp_server_endpoint(config)
    parsed = urlparse(endpoint) if endpoint.startswith(("http://", "https://")) else None
    host = parsed.netloc.lower() if parsed else ""
    transport = _infer_transport(config)
    auth_mode = _infer_auth_mode(server_name, config, host)
    mutability = _infer_mutability(server_name, host)
    trust_level = _infer_trust_level(config, host)
    overrides = effective_policy.get("server_overrides", {}).get(server_name, {})
    if isinstance(overrides, dict):
        if isinstance(overrides.get("auth_mode"), str) and overrides["auth_mode"]:
            auth_mode = overrides["auth_mode"]
        if isinstance(overrides.get("mutability"), str) and overrides["mutability"]:
            mutability = overrides["mutability"]
        if isinstance(overrides.get("trust_level"), str) and overrides["trust_level"]:
            trust_level = overrides["trust_level"]

    trusted_remote_hosts = set(_coerce_string_list(effective_policy.get("trusted_remote_hosts")))
    trusted_workspace_servers = set(_coerce_string_list(effective_policy.get("trusted_workspace_servers")))
    if host and host in trusted_remote_hosts:
        trust_level = "trusted-remote"
    if server_name in trusted_workspace_servers and trust_level in {"workspace-local", "ambient-local", "unknown"}:
        trust_level = "trusted-local"

    risk_flags: list[str] = []

    if transport == "stdio":
        risk_flags.append("local-command-execution")
    if transport in {"http", "streamable-http", "streamableHttp"}:
        risk_flags.append("remote-network")
    command = config.get("command")
    if command == "npx":
        risk_flags.append("package-runtime-install")
    if transport == "stdio" and isinstance(command, str) and "${workspaceFolder}" not in command and "/" not in command:
        risk_flags.append("ambient-executable")
    if trust_level == "unreviewed-remote":
        risk_flags.append("unreviewed-remote")
    if auth_mode.startswith("hardcoded"):
        risk_flags.append("hardcoded-secret")
    if host and trust_level == "unreviewed-remote" and auth_mode == "none":
        risk_flags.append("unauthenticated-remote")
    if host and trust_level == "unreviewed-remote" and effective_policy.get("fail_closed_remote_hosts"):
        risk_flags.append("fail-closed-remote-deny")
    if mutability == "read-write":
        risk_flags.append("write-capable")

    if "hardcoded-secret" in risk_flags or "fail-closed-remote-deny" in risk_flags:
        status = "fail"
    elif trust_level in {"trusted-remote", "trusted-local"} and "ambient-executable" not in risk_flags:
        status = "pass"
    elif "local-command-execution" in risk_flags or "unreviewed-remote" in risk_flags or "ambient-executable" in risk_flags:
        status = "warn"
    else:
        status = "pass"

    return {
        "name": server_name,
        "transport": transport,
        "endpoint": endpoint,
        "host": host or None,
        "auth_mode": auth_mode,
        "mutability": mutability,
        "trust_level": trust_level,
        "status": status,
        "risk_flags": risk_flags,
        "notes": _mcp_policy_notes(
            transport=transport,
            trust_level=trust_level,
            auth_mode=auth_mode,
            mutability=mutability,
            risk_flags=risk_flags,
        ),
    }
