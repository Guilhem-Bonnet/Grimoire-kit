"""Machine d'états d'une session ACP — validation des messages, états, permissions.

Port Python de ``rust/grimoire-acp-core`` : mêmes messages en entrée, mêmes
événements et mêmes états en sortie. C'est **ce** module qui est livré (la roue
reste ``py3-none-any``) ; ``grimoire_acp_core`` n'est que l'oracle de parité,
choisi par ``GRIMOIRE_ACP_BACKEND=rust`` quand il est installé.

Aucune E/S ici : ``on_outgoing`` reçoit ce que le *client* envoie à l'agent,
``on_incoming`` ce que l'agent envoie au client, et chacun rend la liste des
événements normalisés (dicts à clé ``type``). Un message invalide ou hors état
ne change rien à l'état et produit un événement ``error`` — la machine ne lève
jamais.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

try:
    import grimoire_acp_core as _rust_core
except ImportError:  # pragma: no cover - exercé par le job CI Rust
    _rust_core = None

__all__ = ["Machine", "PyMachine", "new_machine", "replay", "rust_backend_available"]

STOP_REASONS = ("end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled")
OPTION_KINDS = ("allow_once", "allow_always", "reject_once", "reject_always")

Event = dict[str, Any]


def rust_backend_available() -> bool:
    """Le module compilé ``grimoire_acp_core`` est-il importable ?"""
    return _rust_core is not None


class Machine(Protocol):
    """Contrat commun aux deux backends."""

    @property
    def state(self) -> str: ...
    def on_outgoing(self, msg: Any) -> list[Event]: ...
    def on_incoming(self, msg: Any) -> list[Event]: ...
    def close(self) -> list[Event]: ...
    def snapshot(self) -> dict[str, Any]: ...


def _err(code: str, message: str) -> Event:
    return {"type": "error", "code": code, "message": message}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _id_ok(v: Any) -> bool:
    return isinstance(v, str) or _is_int(v)


def _key(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _str_param(params: Any, name: str) -> str | None:
    v = params.get(name) if isinstance(params, dict) else None
    return v if isinstance(v, str) else None


def _at(obj: Any, *path: str) -> Any:
    for part in path:
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


class PyMachine:
    """Backend Python (celui qui est livré)."""

    def __init__(self) -> None:
        self._state = "new"
        self._session_id: str | None = None
        self._opening_session: str | None = None
        self._load_session = False
        self._outstanding: list[tuple[str, str]] = []  # (clé d'id, méthode)
        # (clé, id, options | None) ; None : requête d'agent non annoncée (fs/*, terminal/*).
        self._pending: list[tuple[str, Any, list[tuple[str, str]] | None]] = []
        self._resolved: list[dict[str, Any]] = []

    @property
    def state(self) -> str:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "session_id": self._session_id,
            "load_session": self._load_session,
            "pending_permissions": [rid for _, rid, opts in self._pending if opts is not None],
            "resolved_permissions": list(self._resolved),
            "outstanding": [method for _, method in self._outstanding],
        }

    @staticmethod
    def _base_check(msg: Any) -> Event | None:
        if not isinstance(msg, dict):
            return _err("invalid_message", "le message n'est pas un objet JSON")
        if msg.get("jsonrpc") != "2.0":
            return _err("invalid_message", 'jsonrpc doit valoir "2.0"')
        if "id" in msg and not _id_ok(msg["id"]):
            return _err("invalid_message", "id doit etre une chaine ou un entier")
        if "method" in msg:
            if not isinstance(msg["method"], str):
                return _err("invalid_message", "method doit etre une chaine")
        elif "id" not in msg or not ("result" in msg or "error" in msg):
            return _err("invalid_message", "ni methode ni reponse")
        return None

    # ── client -> agent ──────────────────────────────────────────────────

    def on_outgoing(self, msg: Any) -> list[Event]:
        if (bad := self._base_check(msg)) is not None:
            return [bad]
        if "method" in msg:
            return self._out_request(msg["method"], msg)
        return self._out_response(msg)

    def _track(self, msg: dict[str, Any], method: str) -> None:
        if "id" in msg:
            self._outstanding.append((_key(msg["id"]), method))

    def _out_request(self, method: str, msg: dict[str, Any]) -> list[Event]:
        params = msg.get("params")
        has_id = "id" in msg
        state = self._state
        if state == "closed":
            return [_err("closed", "la session est fermee")]
        if method != "session/cancel" and not has_id:
            return [_err("invalid_message", "cette methode est une requete : id manquant")]
        if method == "initialize":
            if state != "new":
                return [_err("already_initialized", "initialize a deja ete envoye")]
            if not _is_int(_at(params, "protocolVersion")):
                return [_err("invalid_message", "initialize : protocolVersion entier requis")]
            self._state = "initializing"
            self._track(msg, method)
            return []
        if method in ("session/new", "session/load"):
            if state in ("new", "initializing"):
                return [_err("not_initialized", "initialize n'est pas termine")]
            if state not in ("ready", "idle"):
                return [_err("busy", "une operation est deja en cours")]
            if _str_param(params, "cwd") is None:
                return [_err("invalid_message", "cwd requis")]
            if method == "session/load":
                if not self._load_session:
                    return [_err("load_unsupported", "l'agent n'annonce pas loadSession")]
                sid = _str_param(params, "sessionId")
                if sid is None:
                    return [_err("invalid_message", "sessionId requis")]
                self._opening_session = sid
            self._state = "opening"
            self._track(msg, method)
            return []
        if method == "session/prompt":
            if state in ("prompting", "cancelling", "opening"):
                return [_err("busy", "un tour est deja en cours")]
            if state != "idle":
                return [_err("no_session", "aucune session ouverte")]
            if _str_param(params, "sessionId") != self._session_id:
                return [_err("session_mismatch", "sessionId different de la session ouverte")]
            if not isinstance(_at(params, "prompt"), list):
                return [_err("invalid_message", "prompt (liste de blocs) requis")]
            self._state = "prompting"
            self._track(msg, method)
            return []
        if method == "session/cancel":
            if has_id:
                return [_err("invalid_message", "session/cancel est une notification : pas d'id")]
            if state != "prompting":
                return [_err("not_prompting", "aucun tour a annuler")]
            if _str_param(params, "sessionId") != self._session_id:
                return [_err("session_mismatch", "sessionId different de la session ouverte")]
            self._state = "cancelling"
            return [{"type": "cancel_requested", "session_id": self._session_id}]
        return [_err("unknown_method", "methode client inconnue")]

    def _out_response(self, msg: dict[str, Any]) -> list[Event]:
        rid = msg["id"]
        key = _key(rid)
        pos = next((i for i, p in enumerate(self._pending) if p[0] == key), None)
        if pos is None:
            return [_err("unknown_request", "aucune requete de l'agent avec cet id")]
        options = self._pending[pos][2]
        if options is None:
            del self._pending[pos]
            return [{"type": "unsupported_replied", "request_id": rid}]
        outcome = _at(msg, "result", "outcome", "outcome")
        option_id: str | None
        if outcome == "cancelled":
            label, option_id = "cancelled", None
        elif outcome == "selected":
            option_id = _at(msg, "result", "outcome", "optionId")
            if not isinstance(option_id, str):
                return [_err("invalid_permission_response", "optionId requis")]
            kind = next((k for oid, k in options if oid == option_id), None)
            if kind is None:
                return [_err("invalid_permission_response", "optionId inconnu de la demande")]
            label = "granted" if kind.startswith("allow") else "denied"
        else:
            return [_err("invalid_permission_response", "outcome selected ou cancelled requis")]
        del self._pending[pos]
        self._resolved.append({"request_id": rid, "outcome": label, "option_id": option_id})
        return [{"type": "permission_resolved", "request_id": rid, "outcome": label, "option_id": option_id}]

    # ── agent -> client ──────────────────────────────────────────────────

    def on_incoming(self, msg: Any) -> list[Event]:
        if (bad := self._base_check(msg)) is not None:
            return [bad]
        if "method" in msg:
            return self._in_request(msg["method"], msg)
        return self._in_response(msg)

    def _known_session(self, sid: str) -> bool:
        return sid in (self._session_id, self._opening_session)

    def _in_request(self, method: str, msg: dict[str, Any]) -> list[Event]:
        params = msg.get("params")
        has_id = "id" in msg
        if method == "session/update" and not has_id:
            sid = _str_param(params, "sessionId")
            if sid is None:
                return [_err("invalid_message", "session/update : sessionId requis")]
            kind = _at(params, "update", "sessionUpdate")
            if not isinstance(kind, str):
                return [_err("invalid_message", "session/update : update.sessionUpdate requis")]
            if not self._known_session(sid):
                return [_err("unknown_session", "session/update pour une session inconnue")]
            text = None
            if _at(params, "update", "content", "type") == "text":
                t = _at(params, "update", "content", "text")
                text = t if isinstance(t, str) else None
            return [{"type": "update", "session_id": sid, "kind": kind, "text": text}]
        if method == "session/request_permission" and has_id:
            return self._in_permission(msg["id"], params)
        if has_id:
            self._pending.append((_key(msg["id"]), msg["id"], None))
            return [{"type": "unsupported_request", "method": method, "request_id": msg["id"]}]
        return [{"type": "ignored_notification", "method": method}]

    def _in_permission(self, rid: Any, params: Any) -> list[Event]:
        sid = _str_param(params, "sessionId")
        if sid is None:
            return [_err("invalid_message", "session/request_permission : sessionId requis")]
        tool_call_id = _at(params, "toolCall", "toolCallId")
        if not isinstance(tool_call_id, str):
            return [_err("invalid_message", "session/request_permission : toolCall.toolCallId requis")]
        raw = _at(params, "options")
        if not isinstance(raw, list) or not raw:
            return [_err("invalid_message", "session/request_permission : options non vide requis")]
        options: list[tuple[str, str]] = []
        shown: list[dict[str, str]] = []
        for o in raw:
            oid, name, kind = (_str_param(o, k) for k in ("optionId", "name", "kind"))
            if oid is None or name is None or kind is None:
                return [_err("invalid_message", "option : optionId, name et kind requis")]
            if kind not in OPTION_KINDS:
                return [_err("invalid_message", "option : kind inconnu")]
            options.append((oid, kind))
            shown.append({"option_id": oid, "name": name, "kind": kind})
        if not self._known_session(sid):
            return [_err("unknown_session", "permission pour une session inconnue")]
        key = _key(rid)
        if any(p[0] == key for p in self._pending):
            return [_err("duplicate_request", "id de requete deja en attente")]
        self._pending.append((key, rid, options))
        return [
            {
                "type": "permission_requested",
                "request_id": rid,
                "session_id": sid,
                "tool_call_id": tool_call_id,
                "options": shown,
            }
        ]

    def _in_response(self, msg: dict[str, Any]) -> list[Event]:
        key = _key(msg["id"])
        pos = next((i for i, o in enumerate(self._outstanding) if o[0] == key), None)
        if pos is None:
            return [_err("unexpected_response", "reponse sans requete en attente")]
        method = self._outstanding.pop(pos)[1]
        resting = "idle" if self._session_id is not None else "ready"
        if "error" in msg:
            error = msg["error"]
            self._state = "closed" if method == "initialize" else resting
            if method == "session/load":
                self._opening_session = None
            message = error.get("message") if isinstance(error, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            return [
                {
                    "type": "error",
                    "code": "agent_error",
                    "message": message if isinstance(message, str) else "",
                    "method": method,
                    "rpc_code": code if _is_int(code) else None,
                }
            ]
        result = msg.get("result")
        if method == "initialize":
            version = _at(result, "protocolVersion")
            if not _is_int(version):
                self._state = "closed"
                return [_err("invalid_message", "initialize : protocolVersion absent de la reponse")]
            self._load_session = _at(result, "agentCapabilities", "loadSession") is True
            auth = _at(result, "authMethods")
            self._state = "ready"
            return [
                {
                    "type": "initialized",
                    "protocol_version": version,
                    "load_session": self._load_session,
                    "auth_methods": len(auth) if isinstance(auth, list) else 0,
                }
            ]
        if method == "session/new":
            sid = _at(result, "sessionId")
            if not isinstance(sid, str):
                self._state = resting
                return [_err("invalid_message", "session/new : sessionId absent de la reponse")]
            self._session_id = sid
            self._state = "idle"
            return [{"type": "session_opened", "session_id": sid, "loaded": False}]
        if method == "session/load":
            self._session_id, self._opening_session = self._opening_session, None
            self._state = "idle"
            return [{"type": "session_opened", "session_id": self._session_id, "loaded": True}]
        self._state = "idle"
        reason = _at(result, "stopReason")
        if isinstance(reason, str) and reason in STOP_REASONS:
            return [{"type": "turn_ended", "session_id": self._session_id, "stop_reason": reason}]
        return [_err("invalid_message", "session/prompt : stopReason inconnu ou absent")]

    def close(self) -> list[Event]:
        self._state = "closed"
        self._outstanding.clear()
        return [{"type": "closed"}]


class _RustMachine:
    """Adaptateur du module compilé au même contrat que :class:`PyMachine`."""

    def __init__(self) -> None:
        assert _rust_core is not None
        self._m = _rust_core.Machine()

    @property
    def state(self) -> str:
        return str(json.loads(self._m.snapshot())["state"])

    def on_outgoing(self, msg: Any) -> list[Event]:
        events: list[Event] = json.loads(self._m.on_outgoing(json.dumps(msg)))
        return events

    def on_incoming(self, msg: Any) -> list[Event]:
        events: list[Event] = json.loads(self._m.on_incoming(json.dumps(msg)))
        return events

    def close(self) -> list[Event]:
        events: list[Event] = json.loads(self._m.close())
        return events

    def snapshot(self) -> dict[str, Any]:
        snap: dict[str, Any] = json.loads(self._m.snapshot())
        return snap


def new_machine(backend: str | None = None) -> Machine:
    """Machine neuve. ``backend`` : ``python`` (défaut), ``rust`` ou ``auto``.

    Sans argument, ``GRIMOIRE_ACP_BACKEND`` décide ; absent, c'est Python — le
    module compilé n'est jamais requis. ``rust`` sans module installé lève.
    """
    choice = (backend or os.environ.get("GRIMOIRE_ACP_BACKEND") or "python").lower()
    if choice == "auto":
        choice = "rust" if rust_backend_available() else "python"
    if choice == "python":
        return PyMachine()
    if choice == "rust":
        if not rust_backend_available():
            raise RuntimeError("grimoire_acp_core est introuvable : maturin develop dans rust/grimoire-acp-core")
        return _RustMachine()
    raise ValueError(f"backend ACP inconnu : {choice!r} (python, rust ou auto)")


def replay(transcript: list[dict[str, Any]], backend: str | None = None) -> dict[str, Any]:
    """Rejoue ``[{"dir": "out"|"in"|"close", "msg": {...}}]`` ; même forme que ``replay`` côté Rust."""
    machine = new_machine(backend)
    steps: list[dict[str, Any]] = []
    for i, entry in enumerate(transcript):
        direction = entry.get("dir")
        if direction == "out":
            events = machine.on_outgoing(entry.get("msg"))
        elif direction == "in":
            events = machine.on_incoming(entry.get("msg"))
        elif direction == "close":
            events = machine.close()
        else:
            raise ValueError(f"entree {i} : dir doit valoir out, in ou close")
        steps.append({"events": events, "state": machine.state})
    return {"steps": steps, "final": machine.snapshot()}
