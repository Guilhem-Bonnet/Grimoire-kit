"""Garde d'autorité humaine : ce qu'un agent ne doit pas pouvoir fabriquer (W2-01).

Une décision de checkpoint est ``attested`` quand le journal des tours
(:mod:`.human_turns`) porte un tour humain de la même session, postérieur au
challenge que le flow a émis (:mod:`grimoire.flows.attestation`). Ces deux
journaux sont donc la preuve, et une preuve que l'agent peut écrire ne prouve
rien. Ce module dit, pour un appel d'outil en attente, s'il est un des
vecteurs connus pour fabriquer cette preuve :

- ``journal`` — écrire un des deux journaux (Write, Edit, redirection shell,
  ``cp``/``mv``/``tee``/``sed -i``, ``python -c``…) : refusé quel que soit le
  profil ;
- ``hook-replay`` — lancer soi-même l'entrée du hook avec un faux
  ``UserPromptSubmit`` : ``grimoire-hook``, ``grimoire host hook`` (la CLI
  appelle la même ``run_hook``), leur module ou la fonction d'écriture, nus
  ou derrière un lanceur (``uvx``, ``uv run``, ``pipx run``, ``python -m
  grimoire``, ``sudo``, ``timeout``…). La CLI n'est refusée que si son
  ``--event`` est lisible et n'est pas ``UserPromptSubmit`` (un événement
  assemblé par le shell est refusé) : refusé en profil governed et production ;
- ``session-resume`` — rouvrir sa propre session par ``claude --resume``,
  ``claude -c`` ou ``claude --session-id`` : le tour de cette sous-session
  porterait le même ``session_id`` que la session parente. Le moyen de
  reconnaître une telle sous-session n'étant pas vérifié, l'invocation est
  refusée en governed et production. ``claude -p`` seul ouvre une session
  neuve, d'un autre ``session_id`` : il n'ouvre aucun checkpoint et n'est pas
  refusé.

Pure analyse de texte, sans E/S : le profil est lu par l'appelant
(:mod:`.tool_policy`), qui décide aussi du sort d'une exception ici (refus en
governed, fail-closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from grimoire.hosts.decisions.human_turns import TURNS_FILENAME
from grimoire.hosts.decisions.tool_facts import ToolFacts, command_surface, extract_c_bodies
from grimoire.policies.schemas import ActionKind, MutationClass

__all__ = ["AUTHORITY_SUFFIX", "LIMIT_NOTE", "ForgeryVector", "forgery_vector"]

#: Suffixe des journaux de challenges et de décisions de checkpoint
#: (:mod:`grimoire.flows.attestation`) — recopié ici plutôt qu'importé pour
#: que ``PreToolUse`` n'importe jamais :mod:`grimoire.flows`.
AUTHORITY_SUFFIX = ".authority.jsonl"
_PROTECTED = (TURNS_FILENAME, AUTHORITY_SUFFIX)

#: La limite que tout refus de cette garde affiche (principe 7 du plan).
LIMIT_NOTE = (
    "Limite : un agent qui exécute du code arbitraire peut contourner une garde de chemin "
    "par une voie non prévue ; l'attestation rend la falsification plus coûteuse et visible, "
    "pas impossible."
)

_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n|\$\(|`|\(")
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NEUTRAL = frozenset({"sudo", "command", "exec", "nohup", "env", "time", "nice", "ionice", "builtin"})
#: Le module d'entrée du hook et la fonction d'écriture du journal : les
#: nommer dans le code exécuté, c'est rejouer un ``UserPromptSubmit``.
_HOOK_MODULE_MARKERS = (
    "grimoire.hosts.runtime",
    "hosts.decisions.human_turns",
    "record_human_turn",
    "cli.cmd_host",
    "run_hook",
)
_RESUME_LONG = ("--resume", "--continue", "--session-id")
#: Programmes qui en lancent un autre : le binaire visé peut suivre à n'importe
#: quelle position (``uvx --from grimoire-kit grimoire-hook``, ``timeout 5 …``).
_LAUNCHERS = frozenset(
    {"uvx", "uv", "pipx", "poetry", "pdm", "hatch", "rye", "npx", "bunx", "pnpm", "xargs", "timeout", "setsid", "stdbuf"}
)
_PYTHON = re.compile(r"^(?:python|pypy)[0-9.]*$")
_EVENT_OPTION = re.compile(r"""--event(?:=|\s+)(?:"([^"]*)"|'([^']*)'|([^\s"'|;&)]+))""")
_USER_PROMPT_KEY = "userpromptsubmit"


@dataclass(frozen=True, slots=True)
class ForgeryVector:
    """Un vecteur reconnu : son code, ce qu'il viserait, et s'il ne compte qu'en governed."""

    code: str
    what: str
    governed_only: bool


def _norm(text: str) -> str:
    return text.replace("\\", "/")


def _names_protected(text: str) -> str:
    normalized = _norm(text)
    for marker in _PROTECTED:
        if marker in normalized:
            return marker
    return ""


def _launched(segment: str) -> list[tuple[str, list[str]]]:
    """Les programmes visés par un segment — nom de base et arguments qui suivent.

    Sans lanceur, le seul programme est le nom de tête, après assignations
    d'environnement. Un lanceur (``uvx``, ``uv run``, ``pipx run``,
    ``python -m``, ``sudo -u x``, ``timeout 5``…) masquait ce nom de tête : tout
    ``grimoire-hook``, ``grimoire``, ``claude`` ou module ``grimoire*`` placé
    après lui est un programme visé, à n'importe quelle position.
    """
    words = [w for w in segment.split() if not _ENV_ASSIGNMENT.match(w)]
    if not words:
        return []
    head = words[0].rsplit("/", 1)[-1]
    if head not in _NEUTRAL and head not in _LAUNCHERS and not _PYTHON.match(head):
        return [(head, words[1:])]
    if head == "command" and len(words) > 1 and words[1] in ("-v", "-V"):
        return []  # `command -v X` cherche X, il ne le lance pas
    found = [(head, words[1:])]
    for idx in range(1, len(words)):
        base = words[idx].rsplit("/", 1)[-1]
        if words[idx - 1] == "-m" and base.split(".", 1)[0] == "grimoire":
            base = "grimoire"
        if base in ("grimoire-hook", "grimoire", "claude"):
            found.append((base, words[idx + 1 :]))
    return found


def _is_host_hook(args: list[str]) -> bool:
    """``host hook`` : le sous-groupe ``host`` suivi aussitôt de sa commande ``hook``."""
    return any(a == "host" and b == "hook" for a, b in pairwise(args))


def _replays_prompt_event(command: str) -> bool:
    """Vrai sauf si chaque ``--event`` de *command* est lisible et n'est pas ``UserPromptSubmit``.

    Lu sur la commande brute : la surface exécutée perd les valeurs citées
    (``--event "UserPromptSubmit"``). Un événement assemblé par le shell
    (``$EVENT``), inconnu ou absent compte comme un rejeu — fail-closed.
    """
    from grimoire.hosts.events import HookEvent
    from grimoire.hosts.runtime import parse_event

    events = [next(g for g in m.groups() if g is not None) for m in _EVENT_OPTION.finditer(command)]
    if not events:
        return True
    for raw in events:
        parsed = parse_event(raw)
        if parsed is None or parsed is HookEvent.USER_PROMPT_SUBMIT:
            return True
    return False


def _is_resume_flag(word: str) -> bool:
    if word.startswith("--"):
        return word.split("=", 1)[0] in _RESUME_LONG
    return word.startswith("-") and len(word) > 1 and any(ch in "cr" for ch in word[1:])


def forgery_vector(facts: ToolFacts) -> ForgeryVector | None:
    """Le vecteur de falsification que porte cet appel, ou ``None``."""
    writes = facts.kind is ActionKind.FILE_WRITE or facts.mutation is not MutationClass.READ_ONLY
    if writes:
        for target in facts.targets:
            marker = _names_protected(target)
            if marker:
                return ForgeryVector("journal", f"écriture de {target}", governed_only=False)
    command = facts.command
    if not command:
        return None
    if facts.mutation is not MutationClass.READ_ONLY:
        marker = _names_protected(command)
        if marker:
            return ForgeryVector("journal", f"commande qui écrit un journal d'autorité ({marker})", governed_only=False)
    executed = [command_surface(command), *extract_c_bodies(command)]
    for text in executed:
        if facts.mutation is not MutationClass.READ_ONLY and any(m in text for m in _HOOK_MODULE_MARKERS):
            return ForgeryVector("hook-replay", "rejeu du hook UserPromptSubmit", governed_only=True)
        for segment in _SEGMENT_SPLIT.split(text):
            for name, args in _launched(segment):
                if name == "grimoire-hook" or (
                    name == "grimoire" and _is_host_hook(args) and _replays_prompt_event(command)
                ):
                    return ForgeryVector("hook-replay", "rejeu du hook UserPromptSubmit", governed_only=True)
                if name == "claude" and any(_is_resume_flag(arg) for arg in args):
                    return ForgeryVector(
                        "session-resume", "reprise de session claude (--resume/-c/--session-id)", governed_only=True
                    )
    return None
