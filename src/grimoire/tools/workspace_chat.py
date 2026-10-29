"""Le chat de l'orchestrateur du cockpit (ACP2) — une conversation par projet.

Le cockpit n'est pas un harness : il est le **client ACP** (:mod:`grimoire.acp`) de
l'agent de l'hôte — Claude Code d'abord, par l'adaptateur ``claude-agent-acp`` — lancé
dans le dossier du projet. Cet agent *est* l'orchestrateur du projet : son identité vient
de ce que le kit projette déjà par hôte (``CLAUDE.md``, ``AGENTS.md``, persona d'entrée,
serveur MCP du kit), pas de ce module.

Gouvernance (ADR-007) : un message tapé dans le chat est un **tour humain** du cockpit.
Le cockpit n'écrit rien lui-même — ni dans le ledger, ni dans le dépôt ; il relaie. C'est
l'agent qui agit, avec ses permissions, et chaque ``session/request_permission`` est
posée à la personne (Autoriser une fois, Toujours, Refuser) ; sans réponse, rien n'est
accordé. Le seul fichier écrit ici est l'identifiant de la dernière session
(``_grimoire-runtime-output/cockpit/chat-session.json``), pour la reprise.

Transport, côté serveur :

- :func:`chat_stream` — ``GET /api/workspace/chat/stream`` — Server-Sent Events, même
  mécanique que le pouls. Chaque événement porte un ``seq`` ; une page qui se
  reconnecte (``?last=<seq>``) reçoit seulement ce qu'elle n'a pas vu.
- :func:`chat_send`, :func:`chat_permission`, :func:`chat_cancel` — ``POST`` sous les
  gardes d'écriture du cockpit (loopback, jeton de session, Origin).

Cycle de vie : le processus de l'agent vit tant qu'une page écoute ou qu'un tour est en
cours. Quand la dernière page part et que le tour est fini, il s'arrête après un délai
de grâce (``GRIMOIRE_CHAT_GRACE_SECONDS``, 20 s). À la réouverture du cockpit, la
conversation est reprise par ``session/load`` si l'agent annonce ``loadSession``; sinon
une conversation neuve démarre — la continuité passe alors par la mémoire du projet et
le kanban, jamais par le transcript brut.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import shlex
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from grimoire.acp import AcpClient, AcpError, PermissionRequest
from grimoire.hosts import acp_hosts

__all__ = [
    "AGENT_COMMAND_ENV",
    "GRACE_ENV",
    "HOST_ENV",
    "STREAM_HEADERS",
    "chat_cancel",
    "chat_host",
    "chat_hosts",
    "chat_permission",
    "chat_send",
    "chat_state",
    "chat_stop",
    "chat_stream",
    "shutdown_all",
]

#: Hôte de l'orchestrateur (alias de ``grimoire chat --host``) ; Claude Code par défaut.
HOST_ENV = acp_hosts.HOST_ENV
#: Remplace la commande ACP du profil d'hôte (tests, adaptateur installé hors PATH).
AGENT_COMMAND_ENV = "GRIMOIRE_CHAT_AGENT_COMMAND"
GRACE_ENV = "GRIMOIRE_CHAT_GRACE_SECONDS"
DEFAULT_GRACE_SECONDS = 20.0
SESSION_RELPATH = Path("_grimoire-runtime-output") / "cockpit" / "chat-session.json"

STREAM_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Type", "text/event-stream; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Accel-Buffering", "no"),
)

_HEARTBEAT_SECONDS = 1.0
_LOG_MAX = 4000
_TEXT_MAX = 20_000

#: Décisions de la page → natures d'option ACP, par ordre de préférence.
_DECISIONS: dict[str, tuple[str, ...]] = {
    "allow_once": ("allow_once",),
    "allow_always": ("allow_always",),
    "reject": ("reject_once", "reject_always"),
}


def _grace() -> float:
    raw = os.environ.get(GRACE_ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_GRACE_SECONDS
    except ValueError:
        return DEFAULT_GRACE_SECONDS


def chat_mode(root: Path | None = None) -> tuple[str, tuple[str, ...], str]:
    """``(mode, commande, raison)`` du chat : ``acp`` avec la commande de l'hôte choisi, sinon ``kanban``.

    Le repli kanban vaut pour l'hôte choisi sans mode ACP lançable (Copilot dans VS Code, hôte non
    installé) ; la raison dit pourquoi, pour que le fil ne bascule pas en silence.
    """
    override = os.environ.get(AGENT_COMMAND_ENV, "").strip()
    if override:
        # Windows : le découpage POSIX mange les « \ » des chemins.
        return "acp", tuple(shlex.split(override, posix=os.name != "nt")), ""
    from grimoire.hosts.acp_hosts import KANBAN, acp_availability, chosen_alias
    from grimoire.hosts.capabilities import profile_for, resolve_host

    alias = chosen_alias(root) if root is not None else (os.environ.get(HOST_ENV, "").strip() or "claude")
    if alias == KANBAN:
        return "kanban", (), "repli choisi : le chat écrit dans le kanban"
    host_id = resolve_host(alias)
    if host_id is None:
        raise AcpError(f"hôte inconnu pour l'orchestrateur : {alias!r}")
    profile = profile_for(host_id)
    available, reason = acp_availability(profile)
    if not available:
        return "kanban", (), f"{reason} : le chat écrit dans le kanban"
    return "acp", profile.acp_command, ""


def agent_command(root: Path | None = None) -> tuple[str, ...]:
    """La commande ACP de l'orchestrateur : variable d'environnement, sinon profil de l'hôte choisi."""
    mode, command, reason = chat_mode(root)
    if mode != "acp":
        raise AcpError(reason)
    return command


def _clip(value: Any) -> Any:
    """Borne les textes d'un message d'agent : le navigateur n'a pas à avaler un fichier entier."""
    if isinstance(value, str):
        return value if len(value) <= _TEXT_MAX else value[:_TEXT_MAX] + "\n… (tronqué)"
    if isinstance(value, list):
        return [_clip(v) for v in value]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


class Conversation:
    """La conversation d'un projet : un client ACP, un journal d'événements, des permissions en attente."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cond = threading.Condition()
        self._log: list[dict[str, Any]] = []
        self._seq = 0
        self._subscribers = 0
        self._client: AcpClient | None = None
        self._busy = False
        self._starting = False
        self._closing_timer: threading.Timer | None = None
        self._raw_permissions: dict[str, dict[str, Any]] = {}
        self._decisions: dict[str, str | None] = {}
        self._decision_ready: dict[str, threading.Event] = {}
        self.session_id: str | None = None
        self.resumed = False
        self._kanban_seen: set[str] = set()
        self._kanban_polled = float("-inf")
        self._pilot_open: dict[tuple[str, str], dict[str, Any]] = {}
        self._pilot_polled = float("-inf")
        self._turn_text: list[str] = []

    # ── journal ──────────────────────────────────────────────────────────

    def _emit(self, event: dict[str, Any]) -> None:
        with self._cond:
            self._seq += 1
            self._log.append({"seq": self._seq, **event})
            del self._log[:-_LOG_MAX]
            self._cond.notify_all()

    @property
    def running(self) -> bool:
        return self._client is not None

    def state(self) -> dict[str, Any]:
        with self._cond:
            return {
                "running": self.running,
                "busy": self._busy,
                "session_id": self.session_id,
                "resumed": self.resumed,
                "subscribers": self._subscribers,
                "stored_session": self._stored_session(),
                "seq": self._seq,
            }

    # ── session mémorisée ────────────────────────────────────────────────

    def _session_file(self) -> Path:
        return self.root / SESSION_RELPATH

    def _stored_session(self) -> str | None:
        try:
            data = json.loads(self._session_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        sid = data.get("session_id") if isinstance(data, dict) else None
        return sid if isinstance(sid, str) and sid else None

    def _store_session(self, session_id: str) -> None:
        path = self._session_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
        except OSError:
            pass  # la reprise est un confort : sans elle, une conversation neuve démarre

    # ── abonnés ──────────────────────────────────────────────────────────

    def subscribe(self) -> None:
        with self._cond:
            self._subscribers += 1
            if self._closing_timer is not None:
                self._closing_timer.cancel()
                self._closing_timer = None

    def unsubscribe(self) -> None:
        with self._cond:
            self._subscribers = max(0, self._subscribers - 1)
        self._maybe_schedule_close()

    def _maybe_schedule_close(self) -> None:
        with self._cond:
            if self._subscribers or self._busy or self._client is None or self._starting:
                return
            if self._closing_timer is not None:
                self._closing_timer.cancel()
            timer = threading.Timer(_grace(), self._close_if_idle)
            timer.daemon = True
            self._closing_timer = timer
        timer.start()

    def _close_if_idle(self) -> None:
        with self._cond:
            if self._subscribers or self._busy or self._starting:
                return
            self._closing_timer = None
        self.close()

    def close(self) -> None:
        with self._cond:
            client, self._client = self._client, None
            self._log.clear()
            self.session_id = None
            self.resumed = False
            self._cond.notify_all()
        for ready in list(self._decision_ready.values()):
            ready.set()
        if client is not None:
            client.close()

    # ── démarrage ────────────────────────────────────────────────────────

    def _observe(self, msg: Any) -> None:
        """Garde le détail brut des messages de l'agent que la machine ne conserve pas."""
        if not isinstance(msg, dict):
            return
        method, params = msg.get("method"), msg.get("params")
        if not isinstance(params, dict):
            return
        if method == "session/update" and isinstance(params.get("update"), dict):
            update = params["update"]
            content = update.get("content")
            if update.get("sessionUpdate") == "agent_message_chunk" and isinstance(content, dict):
                self._turn_text.append(str(content.get("text") or ""))
            self._emit({"type": "update", "update": _clip(update)})
        elif method == "session/request_permission" and "id" in msg:
            self._raw_permissions[str(msg["id"])] = _clip(params.get("toolCall") or {})

    def ensure_started(self, *, resume: bool = True) -> None:
        """Lance l'agent s'il ne tourne pas ; reprend la session mémorisée si possible."""
        with self._cond:
            if self._client is not None:
                return
            if self._starting:
                while self._starting:
                    self._cond.wait(timeout=1)
                if self._client is None:
                    raise AcpError("l'agent n'a pas démarré")
                return
            self._starting = True
        try:
            self._emit({"type": "status", "state": "starting"})
            client = AcpClient(agent_command(self.root), self.root, observer=self._observe, request_timeout=120)
            client.start()
            stored = self._stored_session() if resume else None
            resumed = False
            if stored and client.supports_load_session:
                try:
                    # Le journal repart de zéro : l'agent rejoue l'historique en `session/update`.
                    self._emit({"type": "status", "state": "resuming"})
                    client.load_session(stored)
                    resumed = True
                except AcpError:
                    resumed = False  # session perdue côté agent : une conversation neuve
            if not resumed:
                client.new_session()
            assert client.session_id is not None
            self._store_session(client.session_id)
            with self._cond:
                self._client = client
                self.session_id = client.session_id
                self.resumed = resumed
        except AcpError as exc:
            self._emit({"type": "error", "message": str(exc)})
            raise
        finally:
            with self._cond:
                self._starting = False
                self._cond.notify_all()
        self._emit({"type": "ready", "session_id": self.session_id, "resumed": self.resumed})

    # ── tour ─────────────────────────────────────────────────────────────

    def send(
        self, text: str, task_id: str | None = None, answer_to: tuple[str, str] | None = None
    ) -> dict[str, Any]:
        if answer_to is not None:
            return self._answer_question(text, *answer_to)
        mode, _command, reason = chat_mode(self.root)
        if mode == "kanban":
            return self._send_kanban(text, task_id, reason)
        with self._cond:
            if self._busy or self._starting:
                raise ValueError("un tour est déjà en cours : attendez sa fin ou arrêtez-le")
            self._busy = True
        try:
            self._emit({"type": "user", "text": text})
        except Exception:
            self._busy = False
            raise
        threading.Thread(target=self._run_turn, args=(text,), name="chat-turn", daemon=True).start()
        return {"ok": True, "accepted": True}

    def _answer_question(self, text: str, task_id: str, question_id: str) -> dict[str, Any]:
        """La réponse tapée à une question du pilote : un tour humain qui tranche, sans passer par l'agent (ACP4)."""
        from grimoire.tools import workspace_chat_pilot

        result = workspace_chat_pilot.answer(self.root, task_id, question_id, text)
        self._emit({"type": "user", "text": text})
        self.pull_questions(interval=0.0)
        return result

    def pull_questions(self, *, interval: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        """Pilote activé, publie dans le fil les questions qu'il laisse à l'humain, et celles qui viennent d'être tranchées."""
        now = clock()
        if now - self._pilot_polled < interval:
            return
        self._pilot_polled = now
        try:
            from grimoire.tools import workspace_chat_pilot

            rows = workspace_chat_pilot.pending_questions(self.root)
        except Exception:  # un ledger illisible ne coupe pas le flux : la ligne suivante réessaie
            return
        current = {(r["task_id"], r["question_id"]): r for r in rows}
        for key, row in current.items():
            if key not in self._pilot_open:
                self._emit({"type": "question", **row})
        for key in self._pilot_open.keys() - current.keys():
            self._emit({"type": "question_resolved", "task_id": key[0], "question_id": key[1]})
        self._pilot_open = current

    def pilot_opening(self) -> list[dict[str, Any]]:
        """Ce qu'une page qui s'ouvre doit voir de la part du pilote : le résumé, puis les questions encore posées."""
        from grimoire.tools import workspace_chat_pilot

        events: list[dict[str, Any]] = []
        try:
            digest = workspace_chat_pilot.summary(self.root)
            if digest is not None:
                events.append({"seq": 0, "type": "pilot_summary", **digest})
            workspace_chat_pilot.mark_visit(self.root)
            events += [{"seq": 0, "type": "question", **r} for r in workspace_chat_pilot.pending_questions(self.root)]
        except Exception:  # le résumé est un confort : le chat s'ouvre sans lui
            return []
        return events

    def _send_kanban(self, text: str, task_id: str | None, reason: str) -> dict[str, Any]:
        """Sans agent à qui parler : la consigne part au kanban, la réponse viendra par les notes de la session."""
        from grimoire.tools import workspace_chat_kanban

        posted = workspace_chat_kanban.post(self.root, text, task_id)
        self._kanban_seen.add(posted["directive_id"])
        self._emit({"type": "user", "text": text})
        self._emit({"type": "kanban_sent", "task_id": posted["task_id"], "reason": reason})
        return {"ok": True, "accepted": True, "mode": "kanban", **posted}

    def pull_replies(self, *, interval: float = 2.0, clock: Callable[[], float] = time.monotonic) -> None:
        """En repli kanban, publie dans le fil ce que les sessions ont déposé depuis la dernière lecture."""
        now = clock()
        if now - self._kanban_polled < interval:
            return
        self._kanban_polled = now
        try:
            if chat_mode(self.root)[0] != "kanban":
                return
            from grimoire.tools import workspace_chat_kanban

            entries = workspace_chat_kanban.thread(self.root)
        except Exception:  # un ledger illisible ne coupe pas le flux : la ligne suivante réessaie
            return
        for entry in entries:
            if entry["id"] in self._kanban_seen:
                continue
            self._kanban_seen.add(entry["id"])
            if entry["role"] == "user":
                self._emit({"type": "user", "text": entry["text"]})
            else:
                self._emit({
                    "type": "reply", "task_id": entry["task_id"], "actor": entry["actor"],
                    "text": _clip(entry["text"]), "at": entry["at"],
                })

    def _run_turn(self, text: str) -> None:
        try:
            self.ensure_started()
            client = self._client
            if client is None:
                return
            self._emit({"type": "turn_started"})
            self._turn_text = []
            for event in client.prompt(text, on_permission=self._ask):
                if event["type"] == "turn_ended":
                    self._propose_gestures()
                    self._emit({"type": "turn_ended", "stop_reason": event["stop_reason"]})
                elif event["type"] == "error":
                    self._emit({"type": "error", "message": event.get("message", "")})
        except AcpError as exc:
            self._emit({"type": "error", "message": str(exc)})
            self._emit({"type": "turn_ended", "stop_reason": "error"})
            if self._client is not None and self._client.state == "closed":
                self.close()
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()
            self._maybe_schedule_close()

    def _propose_gestures(self) -> None:
        """L'agent propose « active le pilote » / « coupe le pilote » : le fil en fait un bouton, rien n'est écrit ici."""
        from grimoire.tools import workspace_chat_pilot

        text, self._turn_text = "".join(self._turn_text), []
        for on in dict.fromkeys(workspace_chat_pilot.proposals(text)):
            self._emit({"type": "pilot_gesture", "on": on})

    def _ask(self, request: PermissionRequest) -> str | None:
        """Pose la demande à la page et attend sa réponse ; sans réponse, rien n'est accordé."""
        key = str(request.request_id)
        ready = threading.Event()
        self._decision_ready[key] = ready
        self._emit(
            {
                "type": "permission",
                "request_id": key,
                "tool_call": self._raw_permissions.pop(key, {"toolCallId": request.tool_call_id}),
                "options": [dict(o) for o in request.options],
            }
        )
        ready.wait()
        self._decision_ready.pop(key, None)
        kinds = _DECISIONS.get(self._decisions.pop(key, "") or "", ())
        option = request.option_of_kind(*kinds) if kinds else None
        self._emit(
            {
                "type": "permission_resolved",
                "request_id": key,
                "outcome": "selected" if option else "cancelled",
                "option_id": option,
            }
        )
        return option

    def decide(self, request_id: str, decision: str) -> dict[str, Any]:
        if decision not in _DECISIONS:
            raise ValueError(f"décision inconnue : {decision!r} (allow_once, allow_always ou reject)")
        ready = self._decision_ready.get(str(request_id))
        if ready is None:
            raise ValueError("aucune demande de permission en attente pour cet identifiant")
        self._decisions[str(request_id)] = decision
        ready.set()
        return {"ok": True}

    def cancel(self) -> dict[str, Any]:
        client = self._client
        if client is None or not self._busy:
            return {"ok": True, "cancelled": False}
        client.cancel()  # `session/cancel` + permissions en attente annulées côté agent
        for key, ready in list(self._decision_ready.items()):
            self._decisions.setdefault(key, "")
            ready.set()
        return {"ok": True, "cancelled": True}

    # ── lecture du flux ──────────────────────────────────────────────────

    def events_after(self, last: int, timeout: float) -> list[dict[str, Any]]:
        self.pull_replies()
        self.pull_questions()
        with self._cond:
            if not any(e["seq"] > last for e in self._log[-1:]):
                self._cond.wait(timeout=timeout)
            return [e for e in self._log if e["seq"] > last]


_conversations: dict[str, Conversation] = {}
_registry_lock = threading.Lock()


def _conversation(root: Path) -> Conversation:
    key = str(Path(root).resolve())
    with _registry_lock:
        conv = _conversations.get(key)
        if conv is None:
            conv = _conversations[key] = Conversation(Path(key))
        return conv


def shutdown_all() -> None:
    """Arrête les agents de tous les projets (arrêt du serveur, fin des tests)."""
    with _registry_lock:
        convs = list(_conversations.values())
        _conversations.clear()
    for conv in convs:
        conv.close()


def _sse(event: dict[str, Any]) -> bytes:
    return f"id: {event['seq']}\nevent: chat\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


def chat_stream(
    root: Path,
    query: dict[str, list[str]],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[bytes, None, None]:
    """Le flux de la conversation de *root* — ne se termine pas ; l'hôte cesse d'itérer au départ de la page."""
    conv = _conversation(root)
    try:
        last = int((query.get("last") or ["0"])[0])
    except ValueError:
        last = 0
    resume = (query.get("resume") or ["0"])[0] == "1"
    conv.subscribe()
    try:
        yield b"retry: 3000\n\n"
        if resume and not conv.running and conv._stored_session() is not None:
            # Réouverture du cockpit : la conversation précédente est reprise (session/load).
            threading.Thread(target=_resume_quietly, args=(conv,), name="chat-resume", daemon=True).start()
        beat = clock()
        yield _sse({"seq": 0, "type": "hello", "state": conv.state()})
        if last == 0:  # page neuve : le résumé du pilote et ses questions encore posées (le fil, lui, se rejoue)
            for opening in conv.pilot_opening():
                yield _sse(opening)
        while True:
            events = conv.events_after(last, 0.5)
            for event in events:
                last = event["seq"]
                yield _sse(event)
            if events:
                beat = clock()
            elif clock() - beat >= _HEARTBEAT_SECONDS:
                beat = clock()
                yield b": keep-alive\n\n"  # c'est cette écriture qui révèle une page partie
    finally:
        conv.unsubscribe()


def _resume_quietly(conv: Conversation) -> None:
    with contextlib.suppress(AcpError):  # déjà publié dans le flux comme événement `error`
        conv.ensure_started(resume=True)


def chat_send(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text requis : le message ne peut pas être vide")
    task_id = body.get("task_id")
    target = body.get("answer_to")
    answer_to = None
    if target is not None:
        if not (isinstance(target, dict) and isinstance(target.get("task_id"), str)
                and isinstance(target.get("question_id"), str)):
            raise ValueError("answer_to : {task_id, question_id} attendus")
        answer_to = (target["task_id"], target["question_id"])
    return _conversation(root).send(text, task_id if isinstance(task_id, str) and task_id else None, answer_to)


def chat_permission(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    request_id, decision = body.get("request_id"), body.get("decision")
    if not isinstance(request_id, (str, int)) or not isinstance(decision, str):
        raise ValueError("request_id et decision requis")
    return _conversation(root).decide(str(request_id), decision)


def chat_cancel(root: Path, _body: dict[str, Any]) -> dict[str, Any]:
    return _conversation(root).cancel()


def chat_stop(root: Path, _body: dict[str, Any]) -> dict[str, Any]:
    """Termine la conversation (processus de l'agent compris) ; la prochaine démarre à neuf."""
    _conversation(root).close()
    return {"ok": True}


def chat_state(root: Path, _query: dict[str, list[str]]) -> dict[str, Any]:
    return _conversation(root).state()


def chat_hosts(root: Path, _query: dict[str, list[str]]) -> dict[str, Any]:
    """Les hôtes que le chat sait parler : disponibles, ou grisés avec leur raison, et celui en vigueur."""
    from grimoire.hosts.acp_hosts import chosen_alias, host_choices

    mode, _command, reason = chat_mode(root)
    return {
        "chosen": chosen_alias(root),
        "mode": mode,
        "reason": reason,
        "hosts": [c.to_dict() for c in host_choices(root)],
    }


def chat_host(root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Règle l'hôte du chat de ce projet (``""`` : revenir à l'hôte détecté) ; l'agent en cours est arrêté."""
    from grimoire.hosts.acp_hosts import set_chosen_alias

    alias = body.get("host")
    if not isinstance(alias, str):
        raise ValueError("host requis (alias d'hôte, « kanban », ou chaîne vide pour l'hôte détecté)")
    set_chosen_alias(root, alias)
    _conversation(root).close()  # la prochaine conversation démarre sur le nouvel hôte
    return chat_hosts(root, {})


atexit.register(shutdown_all)  # les agents ne survivent pas au serveur
