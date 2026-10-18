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
  appelle la même ``run_hook``), leur module, la fonction d'écriture ou le
  paquet des décisions (``grimoire.hosts.decisions`` : ``decide_task_context``
  enregistre le tour humain puis la demande de contournement), nus
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
  refusé ;
- ``ledger-snapshot`` (K13) — écrire ``ledger.snapshot.json``, l'instantané du rejeu du
  ledger : refusé quel que soit le profil. Ce n'est pas une preuve d'autorité, c'est un
  cache que le ledger restaure *sans rejouer* le préfixe qu'il couvre ; son hash ne lie que
  le journal, pas l'état qu'il porte. Écrit à la main, il imposerait un état que le journal
  ne dit pas, sans trace d'audit. La garde ne couvre que les voies d'écriture reconnues
  (même limite que les autres vecteurs, :data:`LIMIT_NOTE`) ; supprimer l'instantané se
  fait hors agent.

Pure analyse de texte, sans E/S : le profil est lu par l'appelant
(:mod:`.tool_policy`), qui décide aussi du sort d'une exception ici (refus en
governed, fail-closed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise

from grimoire.hosts.decisions.human_turns import TURNS_FILENAME
from grimoire.hosts.decisions.tool_facts import (
    ToolFacts,
    _strip_heredoc_bodies,
    command_surface,
    extract_c_bodies,
)
from grimoire.policies.schemas import ActionKind, MutationClass

__all__ = ["AUTHORITY_SUFFIX", "LIMIT_NOTE", "SNAPSHOT_FILENAME", "ForgeryVector", "forgery_vector"]

#: Suffixe des journaux de challenges et de décisions de checkpoint
#: (:mod:`grimoire.flows.attestation`) — recopié ici plutôt qu'importé pour
#: que ``PreToolUse`` n'importe jamais :mod:`grimoire.flows`.
AUTHORITY_SUFFIX = ".authority.jsonl"
#: Nom de l'instantané du rejeu du ledger (:data:`grimoire.missions.ledger_snapshot.SNAPSHOT_FILENAME`),
#: recopié ici pour la même raison ; un test fige l'égalité.
SNAPSHOT_FILENAME = "ledger.snapshot.json"
_PROTECTED = (TURNS_FILENAME, AUTHORITY_SUFFIX, SNAPSHOT_FILENAME)

#: La limite que tout refus de cette garde affiche (principe 7 du plan).
LIMIT_NOTE = (
    "Limite : un agent qui exécute du code arbitraire peut contourner une garde de chemin "
    "par une voie non prévue ; l'attestation rend la falsification plus coûteuse et visible, "
    "pas impossible."
)

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
    "record_override_request",
)
#: Le module qui écrit le journal des contournements (W2-06), nommé seul : un
#: ``from grimoire.hosts.decisions import gate_override`` ne contient ni
#: ``hosts.decisions.gate_override`` ni ``record_override_request``, mais
#: ``gate_override._append`` suffit à écrire une paire forgée. Le mot entier,
#: sans identifiant collé devant (``test_gate_override.py`` n'est pas le module).
_GATE_OVERRIDE_WORD = re.compile(r"(?<![A-Za-z0-9_])gate_override(?![A-Za-z0-9])")
#: Le paquet des décisions de hook, nommé par n'importe quelle importation
#: (``grimoire.hosts.decisions``, ``from grimoire.hosts import decisions``,
#: ``importlib.import_module(...)``). ``decide_task_context`` appelle
#: ``record_human_turn`` puis ``record_override_request`` : l'atteindre depuis un
#: outil, c'est forger le tour humain et la phrase sans passer par le hook. On
#: refuse donc le paquet entier plutôt que d'énumérer ses points d'entrée ; les
#: noms d'entrée (``decide_task_context``, ``run_decision``, ``DECISIONS``) restent
#: listés pour le cas où ils sont atteints sans nommer le paquet.
_DECISIONS_PACKAGE = re.compile(r"hosts\s*\.\s*decisions|hosts\s+import\s[^;\n]*(?<![A-Za-z0-9_])decisions(?![A-Za-z0-9_])")
_DECISION_ENTRY_NAMES = re.compile(r"(?<![A-Za-z0-9_])(?:decide_task_context|run_decision|DECISIONS)(?![A-Za-z0-9_])")
_DECISIONS_MODULE_PREFIX = "grimoire.hosts.decisions"
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


def _names_hook_module(text: str) -> bool:
    """Vrai si *text* nomme un point d'entrée du hook ou le module du journal des contournements."""
    return (
        any(m in text for m in _HOOK_MODULE_MARKERS)
        or bool(_GATE_OVERRIDE_WORD.search(text))
        or bool(_DECISIONS_PACKAGE.search(text))
        or bool(_DECISION_ENTRY_NAMES.search(text))
    )


def _write_vector(marker: str, what: str) -> ForgeryVector:
    """Le vecteur d'une écriture qui vise *marker* : l'instantané du ledger a son propre code (K13)."""
    code = "ledger-snapshot" if marker == SNAPSHOT_FILENAME else "journal"
    return ForgeryVector(code, what, governed_only=False)


def _names_protected(text: str) -> str:
    normalized = _norm(text)
    for marker in _PROTECTED:
        if marker in normalized:
            return marker
    return ""


_OPERATORS = ("&&", "||", "$(", ";", "|", "\n", "`", "(", ")")
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
#: Modules dont ``python -m`` est l'entrée du hook : les nommer, c'est le rejouer.
#: Tout module du paquet ``grimoire.hosts.decisions`` en fait partie (voir
#: :func:`_is_hook_module`).
_HOOK_MODULES = (
    "grimoire.hosts.runtime",
    "hosts.decisions.human_turns",
    "grimoire.cli.cmd_host",
)


def _is_hook_module(module: str) -> bool:
    """Vrai si ``python -m <module>`` lance l'entrée du hook ou une décision."""
    return module in _HOOK_MODULES or module == _DECISIONS_MODULE_PREFIX or module.startswith(_DECISIONS_MODULE_PREFIX + ".")


def _command_words(text: str) -> list[list[str]]:
    """Les segments de *text* et les mots de chacun, guillemets et antislash retirés.

    Ce que le shell lancera, pas ce qu'il affichera : ``"grimoire-hook"``,
    ``'claude' -c`` ou ``gri\\moire-hook`` lancent le même programme que leur forme
    nue. :func:`command_surface` blanchit tout mot cité, ce qui est juste pour de
    la prose (un message de commit) mais faisait disparaître le programme visé.
    Ici les opérateurs ne séparent les segments qu'hors guillemets, un mot cité
    reste un seul mot (``-m "claude -c"`` n'est pas ``claude -c``), et une
    citation jamais fermée est lue jusqu'au bout plutôt que rejetée (fail-closed).
    """
    segments: list[list[str]] = []
    words: list[str] = []
    current: list[str] = []
    in_word = False
    quote = ""
    i = 0

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(current))
            current.clear()
            in_word = False

    def end_segment() -> None:
        nonlocal words
        end_word()
        if words:
            segments.append(words)
        words = []

    while i < len(text):
        ch = text[i]
        if quote == "'":
            if ch == "'":
                quote = ""
            else:
                current.append(ch)
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch == "\\" and i + 1 < len(text):
                current.append(ch + text[i + 1])
                i += 1
            else:
                current.append(ch)
        elif ch == "\\" and i + 1 < len(text):
            if text[i + 1] != "\n":  # antislash-saut de ligne : continuation de ligne
                current.append(ch + text[i + 1])
                in_word = True
            i += 1
        elif ch in "'\"":
            quote = ch
            in_word = True
        elif ch.isspace() and ch != "\n":
            end_word()
        else:
            operator = next((op for op in _OPERATORS if text.startswith(op, i)), "")
            if operator:
                end_segment()
                i += len(operator) - 1
            else:
                current.append(ch)
                in_word = True
        i += 1
    end_segment()
    return segments


def _word_variants(words: list[str]) -> tuple[list[str], list[str]]:
    """Les mots lus comme échappements shell (``\\x`` -> ``x``) puis comme chemin (``\\`` -> ``/``)."""
    return [_ESCAPE.sub(r"\1", w) for w in words], [_norm(w) for w in words]


def _launched(words: list[str]) -> list[tuple[str, list[str]]]:
    """Les programmes visés par un segment — nom de base et arguments qui suivent.

    *words* est la sortie de :func:`_command_words` (guillemets et antislash déjà
    retirés). Sans lanceur, le seul programme est le nom de tête, après
    assignations d'environnement. Un lanceur (``uvx``, ``uv run``, ``pipx run``,
    ``python -m``, ``sudo -u x``, ``timeout 5``…) masquait ce nom de tête : tout
    ``grimoire-hook``, ``grimoire``, ``claude`` ou module ``grimoire*`` placé
    après lui est un programme visé, à n'importe quelle position.
    """
    words = [w for w in words if not _ENV_ASSIGNMENT.match(w)]
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
        if words[idx - 1] == "-m":
            if _is_hook_module(words[idx]):
                found.append(("grimoire-hook", []))
            if base.split(".", 1)[0] == "grimoire":
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
                return _write_vector(marker, f"écriture de {target}")
    command = facts.command
    if not command:
        return None
    if facts.mutation is not MutationClass.READ_ONLY:
        marker = _names_protected(command)
        if marker:
            return _write_vector(marker, f"commande qui écrit un journal d'autorité ou l'instantané du ledger ({marker})")
    executed = [command_surface(command), *extract_c_bodies(command)]
    if facts.mutation is not MutationClass.READ_ONLY and any(_names_hook_module(text) for text in executed):
        return ForgeryVector("hook-replay", "rejeu du hook UserPromptSubmit", governed_only=True)
    # Les programmes visés se lisent sur le texte non blanchi : un nom cité reste le nom lancé.
    for text in (_strip_heredoc_bodies(command), *extract_c_bodies(command)):
        for segment in _command_words(text):
            for words in _word_variants(segment):
                for name, args in _launched(words):
                    if name == "grimoire-hook" or (
                        name == "grimoire" and _is_host_hook(args) and _replays_prompt_event(command)
                    ):
                        return ForgeryVector("hook-replay", "rejeu du hook UserPromptSubmit", governed_only=True)
                    if name == "claude" and any(_is_resume_flag(arg) for arg in args):
                        return ForgeryVector(
                            "session-resume", "reprise de session claude (--resume/-c/--session-id)", governed_only=True
                        )
    return None
