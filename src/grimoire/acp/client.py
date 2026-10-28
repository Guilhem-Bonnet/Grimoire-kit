"""Client ACP : lance l'agent de l'hôte en sous-processus et parle JSON-RPC sur stdio.

Chaque hôte reste son propre harness (boucle d'outils, modèle, permissions,
authentification) ; le kit n'en est que le client. Ce module gère le processus —
lignes lues et écrites, threads, arrêt propre — et délègue toute la validation
à :mod:`grimoire.acp.machine`.

Les capacités ``fs/*`` et ``terminal/*`` ne sont pas annoncées : l'agent agit
lui-même dans le dossier du projet. Si un agent les demande quand même, il
reçoit une erreur JSON-RPC ``-32601``.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from grimoire.acp.machine import Event, Machine, new_machine

__all__ = ["AcpClient", "AcpError", "PermissionHandler", "PermissionRequest"]

PROTOCOL_VERSION = 1
_EOF = object()
_METHOD_NOT_FOUND = -32601


class AcpError(RuntimeError):
    """L'échange ACP a échoué (agent absent, mort, erreur JSON-RPC, message refusé)."""


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Une demande ``session/request_permission`` rendue à l'appelant."""

    request_id: str | int
    session_id: str
    tool_call_id: str
    options: tuple[dict[str, str], ...]

    def option_of_kind(self, *kinds: str) -> str | None:
        """Premier ``optionId`` dont le ``kind`` figure dans *kinds* (``allow_once``, ``reject_once``…)."""
        return next((o["option_id"] for o in self.options if o["kind"] in kinds), None)


#: Rend l'``optionId`` choisi, ou ``None`` pour annuler la demande.
PermissionHandler = Callable[[PermissionRequest], str | None]


def _client_version() -> str:
    from grimoire.__version__ import __version__

    return __version__


class AcpClient:
    """Une session ACP sur un sous-processus.

    >>> with AcpClient(("gemini", "--acp"), Path(".")) as acp:   # doctest: +SKIP
    ...     acp.new_session()
    ...     for event in acp.prompt("bonjour", on_permission=ask):
    ...         ...
    """

    def __init__(
        self,
        command: Sequence[str],
        cwd: Path,
        *,
        backend: str | None = None,
        env: Mapping[str, str] | None = None,
        request_timeout: float = 60.0,
    ) -> None:
        if not command:
            raise AcpError("aucune commande ACP pour cet hôte")
        self.command = tuple(command)
        self.cwd = Path(cwd).resolve()
        self._env = dict(env) if env is not None else None
        self._timeout = request_timeout
        self._machine: Machine = new_machine(backend)
        self._lock = threading.Lock()
        self._next_id = 0
        self._proc: subprocess.Popen[str] | None = None
        self._inbox: queue.Queue[Any] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._threads: list[threading.Thread] = []
        self._eof = False
        self.session_id: str | None = None
        self.agent_events: list[Event] = []

    # ── cycle de vie ─────────────────────────────────────────────────────

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def state(self) -> str:
        return self._machine.state

    @property
    def supports_load_session(self) -> bool:
        return bool(self._machine.snapshot()["load_session"])

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr_tail)

    def start(self) -> None:
        """Lance l'agent dans le dossier du projet et négocie ``initialize``."""
        if self._proc is not None:
            raise AcpError("le client est déjà démarré")
        exe = shutil.which(self.command[0])
        if exe is None:
            raise AcpError(f"commande ACP introuvable : {self.command[0]!r} (est-elle installée et dans le PATH ?)")
        try:
            self._proc = subprocess.Popen(
                [exe, *self.command[1:]],
                cwd=self.cwd,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise AcpError(f"impossible de lancer {self.command[0]!r} : {exc}") from exc
        for target, name in ((self._read_stdout, "acp-stdout"), (self._read_stderr, "acp-stderr")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        self._call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": {"name": "grimoire", "title": "Grimoire Kit", "version": _client_version()},
            },
        )

    def close(self) -> None:
        """Arrêt propre : ferme stdin, laisse l'agent finir, puis termine, puis tue."""
        proc, self._proc = self._proc, None
        with self._lock:
            self._machine.close()
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()

    # ── threads de lecture ───────────────────────────────────────────────

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    self._inbox.put(line)  # la machine le refuse comme `invalid_message`
        except (OSError, ValueError):
            pass
        finally:
            self._inbox.put(_EOF)

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for line in self._proc.stderr:
                self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass

    # ── envoi ────────────────────────────────────────────────────────────

    def _write(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AcpError("l'agent n'est pas démarré")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AcpError(self._died(f"écriture impossible : {exc}")) from exc

    def _call(self, method: str, params: dict[str, Any]) -> list[Event]:
        """Requête + attente de sa réponse ; les notifications reçues entre-temps sont gardées."""
        with self._lock:
            self._next_id += 1
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
            events = self._machine.on_outgoing(msg)
            refused = [e for e in events if e["type"] == "error"]
            if refused:
                raise AcpError(f"{refused[0]['code']} : {refused[0]['message']}")
            self._write(msg)
        collected: list[Event] = []
        resting = {"opening", "initializing"}
        while self._machine.state in resting:
            for event in self._pump_one(self._timeout):
                collected.append(event)
                if event["type"] == "error":
                    raise AcpError(self._describe(event))
        return collected

    def _died(self, why: str) -> str:
        tail = self.stderr_tail.strip()
        return f"{why}" + (f"\n--- stderr de l'agent ---\n{tail}" if tail else "")

    @staticmethod
    def _describe(event: Event) -> str:
        if event["code"] == "agent_error":
            return f"l'agent a refusé {event.get('method')} : {event['message']} (code {event.get('rpc_code')})"
        return f"{event['code']} : {event['message']}"

    def _pump_one(self, timeout: float | None) -> list[Event]:
        """Lit un message de l'agent, le passe à la machine, répond aux requêtes non supportées."""
        if self._eof:
            raise AcpError(self._died("l'agent s'est arrêté avant la fin de l'échange"))
        try:
            msg = self._inbox.get(timeout=timeout)
        except queue.Empty:
            raise AcpError(f"l'agent ne répond plus depuis {timeout:g} s") from None
        if msg is _EOF:
            self._eof = True
            raise AcpError(self._died("l'agent s'est arrêté avant la fin de l'échange"))
        with self._lock:
            events = self._machine.on_incoming(msg)
        self.agent_events.extend(events)
        for event in events:
            if event["type"] == "unsupported_request":
                self._reply_not_found(event["request_id"], event["method"])
        return events

    def _reply_not_found(self, request_id: Any, method: str) -> None:
        reply = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": _METHOD_NOT_FOUND, "message": f"{method} : non supporté par ce client"},
        }
        with self._lock:
            self._machine.on_outgoing(reply)
            self._write(reply)

    # ── sessions ─────────────────────────────────────────────────────────

    def new_session(self) -> str:
        events = self._call("session/new", {"cwd": str(self.cwd), "mcpServers": []})
        self.session_id = next(e["session_id"] for e in events if e["type"] == "session_opened")
        return self.session_id

    def load_session(self, session_id: str) -> str:
        """Reprend une session (``session/load``) ; refuse proprement si l'agent n'annonce pas ``loadSession``."""
        if not self.supports_load_session:
            raise AcpError("cet agent n'annonce pas loadSession : la reprise de session est indisponible")
        events = self._call("session/load", {"sessionId": session_id, "cwd": str(self.cwd), "mcpServers": []})
        self.session_id = next(e["session_id"] for e in events if e["type"] == "session_opened")
        return self.session_id

    # ── tour ─────────────────────────────────────────────────────────────

    def prompt(
        self,
        text: str,
        on_permission: PermissionHandler | None = None,
        *,
        idle_timeout: float | None = None,
    ) -> Iterator[Event]:
        """Envoie un prompt et rend les événements du flux jusqu'à la fin du tour.

        Chaque demande de permission est rendue à *on_permission* ; sans gestionnaire,
        ou si le gestionnaire rend ``None``, elle est annulée (jamais accordée par défaut).
        Le dernier événement est ``turn_ended`` ; une erreur de l'agent lève :class:`AcpError`.
        """
        if self.session_id is None:
            raise AcpError("aucune session ouverte : appeler new_session() ou load_session()")
        with self._lock:
            self._next_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._next_id,
                "method": "session/prompt",
                "params": {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
            }
            refused = [e for e in self._machine.on_outgoing(msg) if e["type"] == "error"]
            if refused:
                raise AcpError(f"{refused[0]['code']} : {refused[0]['message']}")
            self._write(msg)
        yield from self._stream_turn(on_permission, idle_timeout)

    def drain(self, idle_timeout: float | None = 10.0) -> list[Event]:
        """Attend la fin du tour en cours (après :meth:`cancel`), permissions annulées d'office."""
        return list(self._stream_turn(None, idle_timeout))

    def _stream_turn(self, on_permission: PermissionHandler | None, idle_timeout: float | None) -> Iterator[Event]:
        while self._machine.state in ("prompting", "cancelling"):
            for event in self._pump_one(idle_timeout):
                if event["type"] == "permission_requested":
                    yield event
                    yield from self._answer_permission(event, on_permission)
                    continue
                if event["type"] == "error":
                    if event["code"] == "agent_error":
                        raise AcpError(self._describe(event))
                    yield event
                    continue
                yield event

    def _answer_permission(self, event: Event, handler: PermissionHandler | None) -> list[Event]:
        request = PermissionRequest(
            request_id=event["request_id"],
            session_id=event["session_id"],
            tool_call_id=event["tool_call_id"],
            options=tuple(event["options"]),
        )
        choice = None
        if handler is not None and self._machine.state == "prompting":
            choice = handler(request)
            if choice is not None and choice not in {o["option_id"] for o in request.options}:
                choice = None
        return self._respond_permission(request.request_id, choice)

    def _respond_permission(self, request_id: Any, option_id: str | None) -> list[Event]:
        outcome = {"outcome": "cancelled"} if option_id is None else {"outcome": "selected", "optionId": option_id}
        reply = {"jsonrpc": "2.0", "id": request_id, "result": {"outcome": outcome}}
        with self._lock:
            events = self._machine.on_outgoing(reply)
            # Déjà réglée (ex. annulée par `cancel()` pendant que l'utilisateur choisissait) : rien à écrire.
            if any(e["type"] == "error" for e in events):
                return []
            self._write(reply)
            return events

    def cancel(self) -> None:
        """Interrompt le tour en cours (utilisable depuis un autre thread) et annule les permissions en attente."""
        with self._lock:
            if self.session_id is None or self._machine.state != "prompting":
                return
            note = {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.session_id}}
            self._machine.on_outgoing(note)
            self._write(note)
            for request_id in list(self._machine.snapshot()["pending_permissions"]):
                reply = {"jsonrpc": "2.0", "id": request_id, "result": {"outcome": {"outcome": "cancelled"}}}
                self._machine.on_outgoing(reply)
                self._write(reply)
