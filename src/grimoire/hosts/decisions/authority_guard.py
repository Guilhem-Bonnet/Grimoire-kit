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
- ``hook-replay`` — lancer soi-même ``grimoire-hook`` (ou son module, ou la
  fonction d'écriture) avec un faux ``UserPromptSubmit`` : refusé en profil
  governed et production ;
- ``session-resume`` — rouvrir sa propre session par ``claude --resume`` ou
  ``claude -c`` : le tour de cette sous-session porterait le même
  ``session_id`` que la session parente. Le moyen de reconnaître une telle
  sous-session n'étant pas vérifié, l'invocation est refusée en governed et
  production.

Pure analyse de texte, sans E/S : le profil est lu par l'appelant
(:mod:`.tool_policy`), qui décide aussi du sort d'une exception ici (refus en
governed, fail-closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
_HOOK_MODULE_MARKERS = ("grimoire.hosts.runtime", "hosts.decisions.human_turns", "record_human_turn")
_RESUME_LONG = ("--resume", "--continue")


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


def _leading(segment: str) -> tuple[str, list[str]]:
    """Le nom de base de la commande d'un segment et ses arguments, après assignations et préfixes neutres."""
    words = segment.split()
    idx = 0
    while idx < len(words) and (_ENV_ASSIGNMENT.match(words[idx]) or words[idx] in _NEUTRAL):
        idx += 1
    if idx >= len(words):
        return "", []
    return words[idx].rsplit("/", 1)[-1], words[idx + 1 :]


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
            name, args = _leading(segment)
            if name == "grimoire-hook":
                return ForgeryVector("hook-replay", "rejeu du hook UserPromptSubmit", governed_only=True)
            if name == "claude" and any(_is_resume_flag(arg) for arg in args):
                return ForgeryVector(
                    "session-resume", "reprise de session claude (--resume/-c)", governed_only=True
                )
    return None
