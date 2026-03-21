#!/usr/bin/env python3
"""
message-bus.py — Couche d'abstraction Message Bus Grimoire (BM-43 Story 4.1).
============================================================

Couche de communication inter-agents avec backends pluggables :
  - InProcessBus  : queue mémoire (tests, mode simulated)
  - RedisBus      : Redis Streams (sequential/parallel)
  - NATSBus       : NATS JetStream (parallel haute perf)

Patterns supportés :
  - request-reply   : agent A envoie, agent B répond
  - pub-sub         : agent publie, N agents abonnés reçoivent
  - broadcast       : message à tous les agents

Modes :
  send      — Envoie un message à un agent
  receive   — Reçoit le prochain message pour un agent
  status    — Affiche l'état du bus et des queues
  clear     — Vide toutes les queues

Usage :
  python3 message-bus.py --backend in-process send --from dev --to architect \\
    --type task-request --payload '{"task": "review auth"}'
  python3 message-bus.py --backend in-process status

Stdlib only — Redis et NATS sont optionnels.

Références :
  - Google A2A Protocol: https://google.github.io/A2A/
  - Redis Streams: https://redis.io/docs/latest/develop/data-types/streams/
  - NATS JetStream: https://docs.nats.io/nats-concepts/jetstream
  - AutoGen GroupChat: https://microsoft.github.io/autogen/docs/tutorial/conversation-patterns/
  - CrewAI Agent Communication: https://docs.crewai.com/concepts/agents
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.message_bus")

# ── Version ──────────────────────────────────────────────────────────────────

MESSAGE_BUS_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

VALID_MSG_TYPES = frozenset({
    "task-request",
    "task-response",
    "observation",
    "question",
    "answer",
    "broadcast",
    "heartbeat",
    "status-update",
})

VALID_PATTERNS = frozenset({"request-reply", "pub-sub", "broadcast"})

DEFAULT_TIMEOUT = 30.0
MAX_QUEUE_SIZE = 1000
BROADCAST_RECIPIENT = "*"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class AgentMessage:
    """Message standardisé pour la communication inter-agents."""

    sender: str
    recipient: str
    msg_type: str
    payload: dict = field(default_factory=dict)
    correlation_id: str = ""
    timestamp: str = ""
    trace_id: str | None = None
    message_id: str = ""
    pattern: str = "request-reply"

    def __post_init__(self):
        if not self.message_id:
            self.message_id = f"msg-{uuid.uuid4().hex[:12]}"
        if not self.correlation_id:
            self.correlation_id = f"corr-{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AgentMessage:
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        msg = cls(**filtered)
        return msg


@dataclass
class BusStats:
    """Statistiques du bus."""

    backend: str = "in-process"
    total_sent: int = 0
    total_received: int = 0
    total_dropped: int = 0
    queues: dict[str, int] = field(default_factory=dict)
    subscriptions: dict[str, list[str]] = field(default_factory=dict)
    uptime_seconds: float = 0.0


@dataclass
class DeliveryResult:
    """Résultat d'un envoi de message."""

    success: bool
    message_id: str = ""
    error: str = ""
    recipients_count: int = 0


# ── Abstract Base ────────────────────────────────────────────────────────────


class MessageBus(ABC):
    """
    Interface abstraite pour les backends de message bus.

    Implémente les patterns request-reply, pub-sub et broadcast
    pour la communication inter-agents Grimoire.
    """

    @abstractmethod
    def send(self, message: AgentMessage) -> DeliveryResult:
        """Envoie un message. Synchrone pour simplifier les tests."""
        ...

    @abstractmethod
    def receive(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> AgentMessage | None:
        """Reçoit le prochain message pour un agent. Bloque jusqu'au timeout."""
        ...

    @abstractmethod
    def subscribe(self, agent_id: str, pattern: str) -> bool:
        """Abonne un agent à un pattern (ex: 'task-request', 'broadcast')."""
        ...

    @abstractmethod
    def unsubscribe(self, agent_id: str, pattern: str) -> bool:
        """Désabonne un agent d'un pattern."""
        ...

    @abstractmethod
    def get_stats(self) -> BusStats:
        """Retourne les statistiques du bus."""
        ...

    @abstractmethod
    def clear(self) -> int:
        """Vide toutes les queues. Retourne le nombre de messages supprimés."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Ferme proprement le bus."""
        ...


# ── InProcessBus ─────────────────────────────────────────────────────────────


class InProcessBus(MessageBus):
    """
    Backend mémoire — pour tests et mode simulated.

    Utilise des collections.deque par agent_id.
    Thread-safe via threading.Lock.
    """

    def __init__(self, max_queue_size: int = MAX_QUEUE_SIZE):
        self._queues: dict[str, collections.deque[AgentMessage]] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._max_queue_size = max_queue_size
        self._stats_sent = 0
        self._stats_received = 0
        self._stats_dropped = 0
        self._start_time = time.monotonic()

    def _ensure_queue(self, agent_id: str) -> collections.deque[AgentMessage]:
        if agent_id not in self._queues:
            self._queues[agent_id] = collections.deque(maxlen=self._max_queue_size)
        return self._queues[agent_id]

    def send(self, message: AgentMessage) -> DeliveryResult:
        with self._lock:
            recipients = []

            if message.recipient == BROADCAST_RECIPIENT:
                # Broadcast: envoie à tous les agents connus
                for agent_id in list(self._queues.keys()):
                    if agent_id != message.sender:
                        q = self._ensure_queue(agent_id)
                        if len(q) < self._max_queue_size:
                            q.append(message)
                            recipients.append(agent_id)
                        else:
                            self._stats_dropped += 1

                # Also deliver to subscribers of "broadcast"
                for agent_id, subs in self._subscriptions.items():
                    if "broadcast" in subs and agent_id != message.sender and agent_id not in recipients:
                        q = self._ensure_queue(agent_id)
                        if len(q) < self._max_queue_size:
                            q.append(message)
                            recipients.append(agent_id)
                        else:
                            self._stats_dropped += 1

            elif message.pattern == "pub-sub":
                # Pub-sub: envoie aux abonnés du msg_type
                for agent_id, subs in self._subscriptions.items():
                    if message.msg_type in subs and agent_id != message.sender:
                        q = self._ensure_queue(agent_id)
                        if len(q) < self._max_queue_size:
                            q.append(message)
                            recipients.append(agent_id)
                        else:
                            self._stats_dropped += 1

            else:
                # Request-reply: envoie directement au destinataire
                q = self._ensure_queue(message.recipient)
                if len(q) < self._max_queue_size:
                    q.append(message)
                    recipients.append(message.recipient)
                else:
                    self._stats_dropped += 1

            self._stats_sent += 1
            self._event.set()

        return DeliveryResult(
            success=len(recipients) > 0,
            message_id=message.message_id,
            recipients_count=len(recipients),
        )

    def receive(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> AgentMessage | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                q = self._ensure_queue(agent_id)
                if q:
                    self._stats_received += 1
                    return q.popleft()
            # Wait a bit before retrying
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self._event.wait(timeout=min(0.05, remaining))
                self._event.clear()
            else:
                break
        return None

    def subscribe(self, agent_id: str, pattern: str) -> bool:
        with self._lock:
            if agent_id not in self._subscriptions:
                self._subscriptions[agent_id] = set()
            self._subscriptions[agent_id].add(pattern)
            self._ensure_queue(agent_id)
        return True

    def unsubscribe(self, agent_id: str, pattern: str) -> bool:
        with self._lock:
            if agent_id in self._subscriptions:
                self._subscriptions[agent_id].discard(pattern)
                return True
        return False

    def get_stats(self) -> BusStats:
        with self._lock:
            return BusStats(
                backend="in-process",
                total_sent=self._stats_sent,
                total_received=self._stats_received,
                total_dropped=self._stats_dropped,
                queues={k: len(v) for k, v in self._queues.items()},
                subscriptions={k: sorted(v) for k, v in self._subscriptions.items()},
                uptime_seconds=round(time.monotonic() - self._start_time, 2),
            )

    def clear(self) -> int:
        with self._lock:
            total = sum(len(q) for q in self._queues.values())
            for q in self._queues.values():
                q.clear()
            return total

    def close(self) -> None:
        self.clear()


# ── RedisBus (stub) ─────────────────────────────────────────────────────────


class RedisBus(MessageBus):
    """
    Backend Redis Streams — pour mode sequential/parallel.

    Nécessite: pip install redis
    Connection: redis://localhost:6379 par défaut
    """

    def __init__(self, url: str = "redis://localhost:6379", prefix: str = "grimoire"):
        self._url = url
        self._prefix = prefix
        self._redis = None
        self._connected = False
        try:
            import redis
            self._redis = redis.from_url(url, decode_responses=True)
            self._redis.ping()
            self._connected = True
        except Exception:
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _stream_key(self, agent_id: str) -> str:
        return f"{self._prefix}:agent:{agent_id}"

    def send(self, message: AgentMessage) -> DeliveryResult:
        if not self._connected or not self._redis:
            return DeliveryResult(success=False, message_id=message.message_id, error="Redis not connected")

        try:
            data = json.dumps(message.to_dict(), ensure_ascii=False)
            if message.recipient == BROADCAST_RECIPIENT:
                # Scan all agent streams and send to each
                keys = self._redis.keys(f"{self._prefix}:agent:*")
                count = 0
                for key in keys:
                    agent_id = key.split(":")[-1]
                    if agent_id != message.sender:
                        self._redis.xadd(key, {"data": data})
                        count += 1
                return DeliveryResult(success=count > 0, message_id=message.message_id, recipients_count=count)
            else:
                key = self._stream_key(message.recipient)
                self._redis.xadd(key, {"data": data})
                return DeliveryResult(success=True, message_id=message.message_id, recipients_count=1)
        except Exception as e:
            return DeliveryResult(success=False, message_id=message.message_id, error=str(e))

    def receive(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> AgentMessage | None:
        if not self._connected or not self._redis:
            return None
        try:
            key = self._stream_key(agent_id)
            result = self._redis.xread({key: "0-0"}, count=1, block=int(timeout * 1000))
            if result:
                _stream_name, entries = result[0]
                if entries:
                    entry_id, fields = entries[0]
                    self._redis.xdel(key, entry_id)
                    data = json.loads(fields["data"])
                    return AgentMessage.from_dict(data)
        except Exception as _exc:
            _log.debug("Exception suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
        return None

    def subscribe(self, agent_id: str, pattern: str) -> bool:
        if not self._connected or not self._redis:
            return False
        try:
            key = f"{self._prefix}:subs:{agent_id}"
            self._redis.sadd(key, pattern)
            return True
        except Exception:
            return False

    def unsubscribe(self, agent_id: str, pattern: str) -> bool:
        if not self._connected or not self._redis:
            return False
        try:
            key = f"{self._prefix}:subs:{agent_id}"
            self._redis.srem(key, pattern)
            return True
        except Exception:
            return False

    def get_stats(self) -> BusStats:
        if not self._connected or not self._redis:
            return BusStats(backend="redis (disconnected)")
        try:
            keys = self._redis.keys(f"{self._prefix}:agent:*")
            queues = {}
            for key in keys:
                agent_id = key.split(":")[-1]
                queues[agent_id] = self._redis.xlen(key)
            return BusStats(backend="redis", queues=queues)
        except Exception:
            return BusStats(backend="redis (error)")

    def clear(self) -> int:
        if not self._connected or not self._redis:
            return 0
        try:
            keys = self._redis.keys(f"{self._prefix}:*")
            if keys:
                return self._redis.delete(*keys)
            return 0
        except Exception:
            return 0

    def close(self) -> None:
        if self._redis:
            try:
                self._redis.close()
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues


# ── NATSBus (stub) ──────────────────────────────────────────────────────────


class NATSBus(MessageBus):
    """
    Backend NATS JetStream — pour mode parallel haute perf.

    Nécessite: pip install nats-py
    Statut : STUB — implémentation async nécessaire
    """

    def __init__(self, url: str = "nats://localhost:4222", prefix: str = "grimoire"):
        self._url = url
        self._prefix = prefix
        self._available = False
        try:
            import nats
            self._available = True
        except ImportError as _exc:
            _log.debug("ImportError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    @property
    def available(self) -> bool:
        return self._available

    def send(self, message: AgentMessage) -> DeliveryResult:
        return DeliveryResult(
            success=False,
            message_id=message.message_id,
            error="NATS backend not yet implemented — requires async runtime",
        )

    def receive(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> AgentMessage | None:
        return None

    def subscribe(self, agent_id: str, pattern: str) -> bool:
        return False

    def unsubscribe(self, agent_id: str, pattern: str) -> bool:
        return False

    def get_stats(self) -> BusStats:
        return BusStats(backend=f"nats ({'available' if self._available else 'unavailable'})")

    def clear(self) -> int:
        return 0

    def close(self) -> None:
        pass


# ── Bus Factory ──────────────────────────────────────────────────────────────

BACKENDS = {
    "in-process": InProcessBus,
    "redis": RedisBus,
    "nats": NATSBus,
}


def create_bus(backend: str = "in-process", **kwargs) -> MessageBus:
    """
    Factory pour créer un bus selon le backend demandé.

    Args:
        backend: "in-process" | "redis" | "nats"
        **kwargs: arguments spécifiques au backend

    Returns:
        Instance de MessageBus
    """
    if backend not in BACKENDS:
        raise ValueError(f"Backend inconnu: {backend}. Disponibles: {sorted(BACKENDS)}")
    return BACKENDS[backend](**kwargs)


def load_bus_config(project_root: Path) -> dict:
    """Charge la config bus depuis project-context.yaml ou grimoire.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [project_root / "project-context.yaml", project_root / "grimoire.yaml"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("messaging", {})
    return {}


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_message_bus_send(
    sender: str,
    recipient: str,
    msg_type: str,
    payload: str = "{}",
    backend: str = "in-process",
) -> dict:
    """
    MCP tool `grimoire_message_bus_send` — envoie un message inter-agent.
    """
    try:
        payload_dict = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON payload"}

    bus = create_bus(backend)
    msg = AgentMessage(
        sender=sender,
        recipient=recipient,
        msg_type=msg_type,
        payload=payload_dict,
    )
    result = bus.send(msg)
    return asdict(result)


def mcp_message_bus_status(backend: str = "in-process") -> dict:
    """
    MCP tool `grimoire_message_bus_status` — statut du bus.
    """
    bus = create_bus(backend)
    stats = bus.get_stats()
    return asdict(stats)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_stats(stats: BusStats) -> None:
    print(f"\n  Message Bus — {stats.backend}")
    print(f"  {'─' * 50}")
    print(f"  Total envoyés  : {stats.total_sent}")
    print(f"  Total reçus    : {stats.total_received}")
    print(f"  Total perdus   : {stats.total_dropped}")
    if stats.uptime_seconds:
        print(f"  Uptime         : {stats.uptime_seconds:.1f}s")
    print()

    if stats.queues:
        print("  Queues :")
        for agent_id, count in sorted(stats.queues.items()):
            print(f"    {agent_id:20s} │ {count} messages")
    else:
        print("  (aucune queue active)")

    if stats.subscriptions:
        print("\n  Subscriptions :")
        for agent_id, patterns in sorted(stats.subscriptions.items()):
            print(f"    {agent_id:20s} │ {', '.join(patterns)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Message Bus — Communication inter-agents Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--backend", choices=sorted(BACKENDS.keys()), default="in-process",
                        help="Backend de transport (défaut: in-process)")
    parser.add_argument("--version", action="version",
                        version=f"message-bus {MESSAGE_BUS_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # send
    send_p = sub.add_parser("send", help="Envoyer un message")
    send_p.add_argument("--from", dest="sender", required=True, help="Agent émetteur")
    send_p.add_argument("--to", dest="recipient", required=True, help="Agent destinataire (ou '*' pour broadcast)")
    send_p.add_argument("--type", dest="msg_type", default="task-request",
                        choices=sorted(VALID_MSG_TYPES), help="Type de message")
    send_p.add_argument("--payload", default="{}", help="Payload JSON")
    send_p.add_argument("--pattern", default="request-reply",
                        choices=sorted(VALID_PATTERNS), help="Pattern de communication")
    send_p.add_argument("--json", action="store_true", help="Output JSON")

    # status
    sub.add_parser("status", help="Afficher l'état du bus")

    # clear
    sub.add_parser("clear", help="Vider toutes les queues")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    bus = create_bus(args.backend)

    if args.command == "send":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print(f"  ❌ Payload JSON invalide: {args.payload}", file=sys.stderr)
            sys.exit(1)

        msg = AgentMessage(
            sender=args.sender,
            recipient=args.recipient,
            msg_type=args.msg_type,
            payload=payload,
            pattern=args.pattern,
        )
        result = bus.send(msg)

        if getattr(args, "json", False):
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            icon = "✅" if result.success else "❌"
            print(f"\n  {icon} Message {result.message_id}")
            print(f"    {args.sender} → {args.recipient} [{args.msg_type}]")
            print(f"    Pattern : {args.pattern}")
            print(f"    Destinataires : {result.recipients_count}")
            if result.error:
                print(f"    Erreur : {result.error}")
            print()

    elif args.command == "status":
        stats = bus.get_stats()
        _print_stats(stats)

    elif args.command == "clear":
        count = bus.clear()
        print(f"\n  🗑️  {count} messages supprimés\n")

    bus.close()


if __name__ == "__main__":
    main()
