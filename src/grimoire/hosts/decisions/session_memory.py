"""Mémoire de session bornée : ce que l'utilisateur a dit, ce qu'un contenu
externe a dit — jamais confondus (issue #645, phase 5 lot 5.2).

``PreToolUse`` ne voit qu'une commande candidate ; il ne peut pas savoir si
elle vient d'une consigne de l'utilisateur ou d'une page web que l'agent
vient de lire. jev-guard (MIT) et jev-engineering (MIT, voir l'issue) closent
cet écart avec trois règles sans modèle : une petite mémoire de session sur
disque, alimentée par ``UserPromptSubmit`` (les mots de l'utilisateur) et
``PostToolUse`` (les sorties d'outil qui viennent d'ailleurs), et jamais par
le raisonnement de l'assistant — un agent qui pourrait écrire dans les
preuves de son propre gate pourrait le convaincre.

Fichier d'état : ``_grimoire-output/.runs/memory-<session>.json``, à côté de
``session-<id>.json`` (:mod:`grimoire.policies.session_state`, issue #429/#422)
dont ce module copie la forme (écriture atomique, best-effort dans les deux
sens, jamais d'exception qui remonte à l'appelant).

Pourquoi les motifs d'instruction sont recopiés, pas importés
---------------------------------------------------------------
:func:`grimoire.memory.validation.instruction_like` détecte exactement ce
qu'il faut (texte qui se présente comme un tour système ou une réécriture de
consigne), mais ``import grimoire.memory.validation`` exécute d'abord
``grimoire/memory/__init__.py`` — qui importe ``MemoryManager``, les
backends, le sidecar. Mesuré : ~43 ms rien que pour cet import, à comparer au
budget de ``PreToolUse`` tout entier (≤ 60 ms, voir
``tests/security/test_injections.py::test_pre_tool_use_budget``). Ce module
tourne dans un process ``grimoire-hook`` neuf à chaque appel (aucun cache
entre invocations), donc cet import serait payé sur *chaque* commande. La
table ``_INSTRUCTION_PATTERNS`` ci-dessous est une copie verbatim des motifs
de :mod:`grimoire.memory.validation` — ``test_injection_patterns_mirror_memory_validation``
(``tests/security/test_injections.py``) échoue si les deux dérivent.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.policies.schemas import ActionKind

if TYPE_CHECKING:
    from grimoire.hosts.decisions._shared import HookInput
    from grimoire.hosts.decisions.tool_facts import ToolFacts

__all__ = [
    "MAX_UNTRUSTED_CHARS",
    "MAX_UNTRUSTED_ENTRIES",
    "MAX_USER_MESSAGES",
    "MAX_USER_MESSAGE_CHARS",
    "MIN_OUTPUT_CHARS",
    "SessionMemory",
    "UntrustedMatch",
    "find_untrusted_match",
    "instruction_label",
    "load_session_memory",
    "memory_path",
    "record_tool_output",
    "record_user_message",
    "save_session_memory",
]

#: Copie verbatim de ``grimoire.memory.validation._INSTRUCTION_PATTERNS`` —
#: voir le docstring du module pour pourquoi ce n'est pas un import.
_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chat-template-marker", re.compile(r"<?\|(?:im_start|im_end|system|endoftext)\|>?", re.IGNORECASE)),
    ("inst-marker", re.compile(r"\[/?INST\]|<<SYS>>")),
    ("role-header", re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:system|assistant|instruction)s?\s*:", re.UNICODE)),
    ("override-en", re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
        r"(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(?:instructions?|prompts?|rules?)\b"
    )),
    ("override-fr", re.compile(
        r"(?i)\b(?:ignore[rz]?|oublie[rz]?|passe outre)\b[^.\n]{0,40}\b"
        r"(?:instructions?|consignes?|règles?)\b"
    )),
    ("role-rewrite-en", re.compile(r"(?i)\byou are now\b\s+(?:an?|the)\b")),
    ("role-rewrite-fr", re.compile(r"(?i)\b(?:tu es|vous êtes)\s+(?:désormais|maintenant)\b")),
)

#: Verbes dont la présence, suivie d'un argument, ressemble à une ligne de
#: commande shell. Un heuristique volontairement large : un faux positif ici
#: ne fait que mémoriser un extrait de plus (borné à
#: :data:`MAX_UNTRUSTED_ENTRIES`), il ne déclenche jamais de refus — seule
#: une correspondance textuelle exacte avec une commande *réellement*
#: proposée compte ensuite (:func:`find_untrusted_match`).
_SHELL_COMMAND_VERBS: frozenset[str] = frozenset({
    "rm", "curl", "wget", "chmod", "chown", "kill", "killall", "sudo", "bash", "sh", "zsh",
    "python", "python3", "pip", "pip3", "npm", "npx", "yarn", "docker", "kubectl", "ssh",
    "scp", "dd", "mkfs", "eval", "xargs", "node", "systemctl", "crontab", "base64", "nc",
    "git", "psql", "mysql", "mongo", "gh", "terraform", "make", "shutdown", "reboot",
    "iptables", "openssl", "certutil", "powershell",
})
_SHELL_COMMAND_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in sorted(_SHELL_COMMAND_VERBS, key=len, reverse=True)) + r")\b[ \t]+\S"
)

#: Outils dont le nom signale un appel à un sous-agent (``Task`` sous Claude
#: Code ; d'autres hôtes nomment cela différemment, d'où la correspondance
#: par sous-chaîne comme partout ailleurs dans :mod:`.tool_facts`).
_SUBAGENT_MARKERS: tuple[str, ...] = ("task", "subagent", "dispatch")

#: Marqueurs de lecture — copie de ``tool_facts._READ_MARKERS``. Dupliqué
#: plutôt qu'importé : c'est un nom privé d'un autre module, et le dupliquer
#: coûte trois mots quand l'importer coderait une dépendance sur le détail
#: d'implémentation d'un autre fichier.
_READ_MARKERS: tuple[str, ...] = ("read", "cat_file", "view", "open_file", "openfile")

_MEMORY_DIR = Path("_grimoire-output") / ".runs"
_SCHEMA_VERSION = 1
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

#: Bornes de la mémoire — voir le docstring du module et l'issue #645.
MAX_USER_MESSAGES = 6
MAX_USER_MESSAGE_CHARS = 700
MAX_UNTRUSTED_ENTRIES = 10
#: Taille d'un extrait mémorisé. Pas le texte entier : la mémoire doit rester
#: bornée même face à une page web de plusieurs mégaoctets. Une commande
#: plantée loin au-delà de cette limite échappe à la détection — un choix
#: délibéré (borné et déterministe plutôt qu'illimité), documenté dans l'issue.
MAX_UNTRUSTED_CHARS = 4000
#: Seuil de l'issue #645 : une sortie d'outil de moins de 200 caractères n'est
#: jamais mémorisée, quelle que soit sa provenance.
MIN_OUTPUT_CHARS = 200
#: Une correspondance plus courte que ça n'est pas une commande, c'est du
#: bruit — un ``needle`` d'un ou deux caractères matcherait presque tout.
_MIN_NEEDLE_LEN = 4


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_session_id(session_id: str) -> str:
    session_id = session_id.strip() or "unknown"
    if _SAFE_ID_RE.match(session_id):
        return session_id
    return "h-" + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def memory_path(project_root: Path, session_id: str) -> Path:
    return project_root / _MEMORY_DIR / f"memory-{_safe_session_id(session_id)}.json"


def instruction_label(text: str) -> str:
    """L'étiquette du premier motif de consigne rencontré, ou une chaîne vide.

    Mirroir léger de :func:`grimoire.memory.validation.instruction_like` (voir
    le docstring du module) — sans la normalisation Unicode complète de
    l'original : la casse est déjà couverte par ``re.IGNORECASE`` sur chaque
    motif, ce qui suffit aux variantes de casse et d'espacement que la suite
    d'injections exerce.
    """
    for label, pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def _looks_like_shell_command(text: str) -> bool:
    return bool(_SHELL_COMMAND_RE.search(text))


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _response_text(tool_response: dict[str, Any]) -> str:
    """Le texte qu'un hôte a mis dans ``tool_response``, toutes formes confondues.

    Une chaîne nue (``Bash``/``Read`` sous la plupart des hôtes), ou une liste
    de blocs ``{"type": "text", "text": ...}`` (la forme d'un retour de
    sous-agent sous Claude Code). Le premier champ trouvé gagne ; rien de
    reconnu rend une chaîne vide, jamais une exception.
    """
    for key in ("content", "output", "result", "text", "stdout"):
        value = tool_response.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [
                item["text"]
                for item in value
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if parts:
                return "\n".join(parts)
    return ""


def _is_outside_repo(target: str, project_root: Path) -> bool:
    try:
        candidate = Path(target)
        resolved = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
        root_resolved = project_root.resolve()
    except (OSError, ValueError):
        return False
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        return True
    return False


def _classify_source(hook: HookInput, facts: ToolFacts) -> tuple[str, str] | None:
    """La provenance d'un appel d'outil, si elle est externe — sinon ``None``.

    Trois provenances comptent (issue #645) : le web, un fichier lu hors du
    dépôt, un retour de sous-agent. Tout le reste (un fichier du dépôt, une
    commande Bash, une écriture) n'est jamais du contenu externe et ne
    déclenche aucun enregistrement.
    """
    name = (hook.tool_name or "").lower()
    if facts.kind is ActionKind.NETWORK:
        url = _first_str(hook.tool_input, "url", "uri", "query")
        return "web", f"page web ({url})" if url else "page web"
    if any(marker in name for marker in _SUBAGENT_MARKERS):
        who = hook.agent_name or _first_str(hook.tool_input, "subagent_type", "agent", "description") or "sous-agent"
        return "subagent", f"retour de sous-agent ({who})"
    if any(marker in name for marker in _READ_MARKERS) and facts.targets:
        target = facts.targets[0]
        if _is_outside_repo(target, hook.project_root):
            return "external-file", f"fichier hors dépôt ({target})"
    return None


@dataclass
class SessionMemory:
    """Ce qu'une session a dit, et ce qu'elle a lu qui pourrait mentir."""

    session_id: str
    updated_at: str = ""
    user_messages: list[str] = field(default_factory=list)
    untrusted: list[dict[str, str]] = field(default_factory=list)
    schema_version: int = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "updated_at": self.updated_at,
            "user_messages": list(self.user_messages),
            "untrusted": [dict(entry) for entry in self.untrusted],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, session_id: str) -> SessionMemory:
        raw_messages = data.get("user_messages", [])
        messages = [str(m) for m in raw_messages if isinstance(m, str)] if isinstance(raw_messages, list) else []
        raw_untrusted = data.get("untrusted", [])
        untrusted = (
            [
                {
                    "text": str(entry.get("text", "")),
                    "source": str(entry.get("source", "")),
                    "label": str(entry.get("label", "")),
                    "at": str(entry.get("at", "")),
                }
                for entry in raw_untrusted
                if isinstance(entry, dict)
            ]
            if isinstance(raw_untrusted, list)
            else []
        )
        return cls(
            session_id=session_id,
            updated_at=str(data.get("updated_at", "")),
            user_messages=messages[-MAX_USER_MESSAGES:],
            untrusted=untrusted[-MAX_UNTRUSTED_ENTRIES:],
        )

    @classmethod
    def new(cls, session_id: str) -> SessionMemory:
        return cls(session_id=session_id)


def load_session_memory(project_root: Path, session_id: str) -> SessionMemory:
    """La mémoire de *session_id*, ou une mémoire neuve — jamais d'exception.

    Même contrat que :func:`grimoire.policies.session_state.load_session_state` :
    un fichier absent, illisible, corrompu ou d'une autre version rend une
    session neuve plutôt qu'une erreur qu'un hook pourrait faire échouer sur.
    """
    path = memory_path(project_root, session_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return SessionMemory.new(session_id)
    try:
        data = json.loads(raw)
    except ValueError:
        return SessionMemory.new(session_id)
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        return SessionMemory.new(session_id)
    return SessionMemory.from_dict(data, session_id=session_id)


def save_session_memory(project_root: Path, memory: SessionMemory) -> None:
    """Écriture atomique (fichier temporaire + ``os.replace``), best-effort.

    Une écriture qui échoue ne remonte jamais : voir le docstring du module
    pour pourquoi ça ne peut, par construction, jamais transformer un refus
    en autorisation — cette fonction n'alimente que la mémoire consultée par
    :func:`find_untrusted_match`, qui ne fait jamais que degrader un ``allow``
    en ``ask``, jamais l'inverse.
    """
    memory.updated_at = _now_iso()
    path = memory_path(project_root, memory.session_id)
    payload = json.dumps(memory.to_dict(), ensure_ascii=False)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def record_user_message(hook: HookInput) -> None:
    """``UserPromptSubmit`` : mémorise le message de l'utilisateur, rien d'autre.

    Jamais le raisonnement de l'assistant, jamais un résumé produit par un
    outil — seul ``hook.prompt`` (le tour humain tel que l'hôte l'a envoyé)
    entre ici. Tronqué à :data:`MAX_USER_MESSAGE_CHARS`, borné à
    :data:`MAX_USER_MESSAGES` messages les plus récents. Best-effort : voir
    :func:`save_session_memory`.
    """
    try:
        if not hook.session_id or not hook.prompt.strip():
            return
        memory = load_session_memory(hook.project_root, hook.session_id)
        memory.user_messages.append(hook.prompt.strip()[:MAX_USER_MESSAGE_CHARS])
        memory.user_messages = memory.user_messages[-MAX_USER_MESSAGES:]
        save_session_memory(hook.project_root, memory)
    except Exception:
        return


def record_tool_output(hook: HookInput, facts: ToolFacts) -> None:
    """``PostToolUse`` : mémorise un extrait de sortie d'outil externe suspecte.

    Trois conditions, toutes nécessaires (issue #645) :

    1. la provenance est externe — web, fichier hors dépôt, retour de
       sous-agent (:func:`_classify_source`) ;
    2. la sortie fait au moins :data:`MIN_OUTPUT_CHARS` caractères ;
    3. elle contient un motif de consigne (:func:`instruction_label`) ou
       ressemble à une ligne de commande (:func:`_looks_like_shell_command`).

    Best-effort dans son ensemble — voir :func:`save_session_memory`.
    """
    try:
        if not hook.session_id:
            return
        classified = _classify_source(hook, facts)
        if classified is None:
            return
        source_kind, provenance = classified
        text = _response_text(hook.tool_response)
        if len(text) < MIN_OUTPUT_CHARS:
            return
        label = instruction_label(text) or ("shell-command" if _looks_like_shell_command(text) else "")
        if not label:
            return
        memory = load_session_memory(hook.project_root, hook.session_id)
        memory.untrusted.append(
            {
                "text": text[:MAX_UNTRUSTED_CHARS],
                "source": provenance,
                "label": f"{source_kind}:{label}",
                "at": _now_iso(),
            }
        )
        memory.untrusted = memory.untrusted[-MAX_UNTRUSTED_ENTRIES:]
        save_session_memory(hook.project_root, memory)
    except Exception:
        return


@dataclass(frozen=True, slots=True)
class UntrustedMatch:
    """Ce que :func:`find_untrusted_match` a trouvé — jamais la commande elle-même."""

    source: str
    excerpt: str


def _excerpt(text: str, needle: str, *, width: int = 60) -> str:
    idx = text.find(needle)
    if idx < 0:
        return text[:120]
    start = max(0, idx - width)
    end = min(len(text), idx + len(needle) + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def find_untrusted_match(project_root: Path, session_id: str, needle: str) -> UntrustedMatch | None:
    """*needle* (la surface d'une commande proposée) apparaît-elle dans un
    contenu marqué non fiable, et **jamais** dans un message de l'utilisateur ?

    C'est la seule question que :mod:`.tool_policy` pose à ce module. Un
    ``needle`` trop court (:data:`_MIN_NEEDLE_LEN`) ou un ``session_id`` vide
    rendent toujours ``None`` : rien à mémoriser ne veut pas dire rien à
    craindre, ça veut dire rien à juger sur ce seul signal.
    """
    needle = needle.strip()
    if len(needle) < _MIN_NEEDLE_LEN or not session_id:
        return None
    memory = load_session_memory(project_root, session_id)
    for message in memory.user_messages:
        if needle in message:
            return None
    for entry in reversed(memory.untrusted):
        text = entry.get("text", "")
        if needle in text:
            return UntrustedMatch(source=entry.get("source") or "contenu externe", excerpt=_excerpt(text, needle))
    return None
