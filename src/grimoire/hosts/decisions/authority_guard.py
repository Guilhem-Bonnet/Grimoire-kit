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
- ``control-zone`` (#886 d) — écrire sous ``~/.grimoire/control/`` : la preuve de contrat d'hôte, le journal
  d'autorité du mandat de l'orchestrateur et la clé de contrôle du cockpit. Refusé quel que soit le profil, par
  les mêmes voies d'écriture que le journal (Write, Edit, NotebookEdit, redirection, ``cp``/``mv``/``tee``/
  ``sed -i``, ``python -c``). Le kit y écrit par son propre code, hors outil d'agent ; la limite résiduelle (un
  script arbitraire) est écrite à :data:`CONTROL_ZONE` ;
- ``ledger-snapshot`` (K13) — écrire ``ledger.snapshot.json``, l'instantané du rejeu du
  ledger : refusé quel que soit le profil. Ce n'est pas une preuve d'autorité, c'est un
  cache que le ledger restaure *sans rejouer* le préfixe qu'il couvre ; son hash ne lie que
  le journal, pas l'état qu'il porte. Écrit à la main, il imposerait un état que le journal
  ne dit pas, sans trace d'audit. La garde ne couvre que les voies d'écriture reconnues
  (même limite que les autres vecteurs, :data:`LIMIT_NOTE`) ; supprimer l'instantané se
  fait hors agent.

Analyse de texte, avec une seule E/S : la résolution des chemins vers la zone de contrôle
(``realpath``, pour qu'un lien symbolique ne la cache pas). Le profil est lu par l'appelant
(:mod:`.tool_policy`), qui décide aussi du sort d'une exception ici (refus en
governed, fail-closed).
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from itertools import pairwise
from pathlib import Path

from grimoire.hosts.decisions.human_turns import TURNS_FILENAME
from grimoire.hosts.decisions.tool_facts import (
    ToolFacts,
    _strip_heredoc_bodies,
    command_surface,
    extract_c_bodies,
)
from grimoire.policies.schemas import ActionKind, MutationClass

__all__ = [
    "AUTHORITY_SUFFIX",
    "CONTROL_ZONE",
    "CONTROL_ZONE_FILES",
    "CONTROL_ZONE_MARKER",
    "LIMIT_NOTE",
    "SNAPSHOT_FILENAME",
    "ForgeryVector",
    "forgery_vector",
    "names_zone_secret",
]

#: Suffixe des journaux de challenges et de décisions de checkpoint
#: (:mod:`grimoire.flows.attestation`) — recopié ici plutôt qu'importé pour
#: que ``PreToolUse`` n'importe jamais :mod:`grimoire.flows`.
AUTHORITY_SUFFIX = ".authority.jsonl"
#: Nom de l'instantané du rejeu du ledger (:data:`grimoire.missions.ledger_snapshot.SNAPSHOT_FILENAME`),
#: recopié ici pour la même raison ; un test fige l'égalité.
SNAPSHOT_FILENAME = "ledger.snapshot.json"
#: Zone de contrôle de l'utilisateur (#886 d, #876 lot 1) : ``~/.grimoire/control/``. Elle porte la preuve de
#: contrat d'hôte (``host-contracts.json``), le journal d'autorité du mandat (``mandates.authority.jsonl``) et la
#: clé de contrôle du cockpit (``cockpit-key``). Le kit y écrit par son propre code Python, hors outil d'agent ;
#: un outil d'agent n'y écrit jamais (code ``control-zone``). Les noms de fichier sont refusés aussi hors du
#: chemin complet (``cd ~/.grimoire && echo x > control/host-contracts.json``).
#:
#: **Limite résiduelle, écrite** : la garde lit des chemins et des commandes, pas du code. Un agent qui lance un
#: script Python arbitraire (ou ``bash script.sh`` dont le corps écrit là) contourne cette garde ; ``LIMIT_NOTE``
#: l'affiche à chaque refus. Le mandat n'en dépend pas seul : le journal est chaîné par hash et un mandat se
#: révoque sur écriture refusée dans la zone (``MandateStore.revoke_on_protected_write``).
#: Le second membre attrape le chemin que du code assemble en morceaux cités
#: (``Path.home().joinpath('.grimoire', 'control', ...)``, ``'.grimoire' / 'control'``).
CONTROL_ZONE = re.compile(r"""\.grimoire(?:/+|['"][\s,+/()'"]*)control(?![A-Za-z0-9_.-])""")
CONTROL_ZONE_FILES = ("host-contracts.json", "mandates.authority.jsonl", "cockpit-key")
#: Ce que :func:`_names_protected` rend pour la zone de contrôle.
CONTROL_ZONE_MARKER = "~/.grimoire/control"
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
    "record_chain_ack_request",
)
#: Le module qui écrit le journal des contournements (W2-06), nommé seul : un
#: ``from grimoire.hosts.decisions import gate_override`` ne contient ni
#: ``hosts.decisions.gate_override`` ni ``record_override_request``, mais
#: ``gate_override._append`` suffit à écrire une paire forgée. Le mot entier,
#: sans identifiant collé devant (``test_gate_override.py`` n'est pas le module).
_GATE_OVERRIDE_WORD = re.compile(r"(?<![A-Za-z0-9_])(?:gate_override|chain_ack)(?![A-Za-z0-9])")
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
    """Le vecteur d'une écriture qui vise *marker* : l'instantané du ledger (K13) et la zone de contrôle ont leur code."""
    if marker == SNAPSHOT_FILENAME:
        code = "ledger-snapshot"
    elif marker == CONTROL_ZONE_MARKER:
        code = "control-zone"
    else:
        code = "journal"
    return ForgeryVector(code, what, governed_only=False)


def _names_protected(text: str) -> str:
    normalized = _norm(text)
    # La zone de contrôle d'abord : le journal du mandat porte aussi le suffixe d'un journal d'autorité, mais
    # c'est son emplacement qui déclenche la révocation des mandats.
    if CONTROL_ZONE.search(normalized) or any(name in normalized for name in CONTROL_ZONE_FILES):
        return CONTROL_ZONE_MARKER
    for marker in _PROTECTED:
        if marker in normalized:
            return marker
    return ""


# ── Zone de contrôle par résolution de chemin ────────────────────────────────
#
# L'expression :data:`CONTROL_ZONE` lit du texte : ``.grimoire/x/../control``, ``${HOME}/.grimoire/./control``,
# ``cd ~/.grimoire && echo > control/y``, ``.grimoire/'control'``, ``.grimoire/contr*`` ou un lien symbolique posé
# ailleurs la contournent. Ici on résout : ``~``/``$HOME``/``${HOME}`` développés, ``..`` et ``//`` repliés, chemins
# relatifs joints au répertoire courant et à chaque ``cd``/``-C``/``--directory`` de la commande, globs comparés
# composant par composant, ``realpath`` pour les liens existants. Hors de portée, dit dans :data:`LIMIT_NOTE` : une
# variable que le shell assemble (``$X``), un code exécuté (``python -c``) qui construit le chemin.

_GLOB_CHARS = re.compile(r"[*?\[]")
_REDIRECT_PREFIX = re.compile(r"^(?:[0-9]*>>?|[0-9]*<|&>>?|>&)+")
_HOME_PREFIX = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/|$)")
_DIR_FLAGS = frozenset({"-C", "--directory", "--target-directory", "-t"})
_DIR_PROGRAMS = frozenset({"cd", "pushd"})
#: Programmes qui déplacent, copient, lient ou suppriment : nommer ``~/.grimoire`` lui-même suffit à contourner
#: la zone (déplacer le dossier, l'éditer ailleurs, le remettre ; ou le lier sous un autre nom).
_RELOCATORS = frozenset({"mv", "cp", "rsync", "ln", "rm", "install", "rename", "chmod", "chown", "rmdir"})


def _control_zones() -> tuple[list[str], str, str]:
    """``(zones, .grimoire, home)`` : la zone, normalisée et résolue ; ``~/.grimoire`` ; le home."""
    home = Path.home()
    base = home / ".grimoire" / "control"
    zones = {os.path.normpath(str(base))}
    with contextlib.suppress(OSError):
        zones.add(os.path.realpath(base))
    return sorted(zones), os.path.normpath(str(home / ".grimoire")), os.path.normpath(str(home))


def _cwd() -> str:
    try:
        return str(Path.cwd())
    except OSError:  # répertoire courant supprimé : on retombe sur le home, où la zone se trouve
        return str(Path.home())


def _expand(word: str, home: str) -> str:
    return _HOME_PREFIX.sub(lambda _m: home, word, count=1)


def _inside(path: str, zone: str) -> bool:
    # ``normcase`` : sous Windows, la casse ne distingue pas deux chemins et ``/`` vaut ``\`` (no-op sous POSIX).
    path, zone = os.path.normcase(path), os.path.normcase(zone)
    return path == zone or path.startswith(zone.rstrip(os.sep) + os.sep)


def _glob_reaches(pattern: str, zone: str) -> bool:
    """Vrai si le motif *pattern* (avec jokers) peut désigner la zone ou ce qu'elle contient.

    Les deux sont découpés sur le séparateur du système une fois normalisés : sous Windows, ``~`` développé puis
    ``normpath`` donnent des antislashs — découper sur ``/`` ne reconnaissait plus aucun motif (``cat ~/.grimoire/control/*``
    passait sous Git Bash).
    """
    parts, wanted = Path(os.path.normcase(pattern)).parts, Path(os.path.normcase(zone)).parts
    return len(parts) >= len(wanted) and all(fnmatchcase(w, p) for w, p in zip(wanted, parts, strict=False))


def _word_paths(word: str) -> list[str]:
    """Les lectures d'un mot comme chemin : tel quel, sans redirection (``>f``), après ``clé=`` (``of=f``)."""
    out = {word}
    stripped = _REDIRECT_PREFIX.sub("", word)
    out.add(stripped)
    for candidate in (word, stripped):
        if "=" in candidate:
            out.add(candidate.split("=", 1)[1])
        if "," in candidate and "=" not in candidate:
            out.update(candidate.split(","))
    return [c for c in out if c]


def _resolutions(word: str, dirs: list[str], home: str) -> list[tuple[str, str]]:
    """``(normalisé, résolu)`` de chaque lecture de *word* comme chemin, relative à chaque répertoire possible."""
    found: list[tuple[str, str]] = []
    for candidate in _word_paths(word):
        expanded = _expand(candidate, home)
        joined = [expanded] if Path(expanded).is_absolute() else [str(Path(d) / expanded) for d in dirs]
        for path in joined:
            normal = os.path.normpath(path)
            real = normal
            if not _GLOB_CHARS.search(normal):
                with contextlib.suppress(OSError, ValueError):
                    real = os.path.realpath(path)
            found.append((normal, real))
    return found


def _without_neutral(words: list[str]) -> list[str]:
    """Les mots d'un segment sans assignations d'environnement ni préfixes neutres (``sudo``, ``env``, ``nohup``…)."""
    kept = [w for w in words if not _ENV_ASSIGNMENT.match(w)]
    while kept and kept[0].rsplit("/", 1)[-1] in _NEUTRAL:
        kept = kept[1:]
    return kept


def _names_control_zone_path(target: str) -> bool:
    """Un chemin d'outil (Write, Edit, NotebookEdit) qui se résout dans la zone de contrôle."""
    zones, _grimoire, home = _control_zones()
    return any(_reaches(normal, real, z) for normal, real in _resolutions(target, [_cwd()], home) for z in zones)


def _command_paths(command: str, home: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """``(programme, [(normalisé, résolu)…])`` pour chaque segment de *command*.

    Les répertoires où la commande peut se placer (``cd``, ``pushd``, ``-C``, ``--directory``) sont collectés
    d'abord, puis chaque mot relatif est joint à chacun : une commande qui change de dossier n'est pas lue
    comme si elle ne l'avait pas fait.
    """
    bodies = [_strip_heredoc_bodies(command), *extract_c_bodies(command)]
    segments = [_without_neutral(seg) for text in bodies for seg in _command_words(text)]
    dirs = [_cwd()]
    for launched in segments:
        if launched and launched[0].rsplit("/", 1)[-1] in _DIR_PROGRAMS:
            args = [w for w in launched[1:] if not w.startswith("-")]
            for target in args[:1] or ["~"]:
                dirs.extend(real for _normal, real in _resolutions(target, list(dirs), home))
        for prev, word in pairwise(launched):
            if prev in _DIR_FLAGS:
                dirs.extend(real for _normal, real in _resolutions(word, list(dirs), home))
        for word in launched:
            if word.startswith(("--directory=", "--target-directory=")):
                dirs.extend(real for _normal, real in _resolutions(word.split("=", 1)[1], list(dirs), home))
    dirs = list(dict.fromkeys(dirs))
    return [
        (launched[0].rsplit("/", 1)[-1], [r for word in launched[1:] for r in _resolutions(word, dirs, home)])
        for launched in segments
        if launched
    ]


def _reaches(normal: str, real: str, target: str) -> bool:
    """Vrai si le chemin (ou le motif) désigne *target*, ou ce qu'il contient."""
    if _inside(normal, target) or _inside(real, target):
        return True
    return bool(_GLOB_CHARS.search(normal) and _glob_reaches(normal, target))


def _names_control_zone_command(command: str) -> bool:
    """Une commande qui nomme la zone de contrôle, ou ``~/.grimoire`` pour le déplacer, par un chemin résolu."""
    zones, grimoire, home = _control_zones()
    for program, resolved in _command_paths(command, home):
        for normal, real in resolved:
            if any(_reaches(normal, real, z) for z in zones):
                return True
            if program in _RELOCATORS and grimoire in (normal, real):
                return True
            if program == "ln" and (normal in (grimoire, home) or real in (grimoire, home)):
                return True
    return False


#: Ce que la zone de contrôle porte et qu'un agent ne lit pas : la clé du cockpit (avec le jeton de session que
#: le cockpit injecte dans ses pages, elle accorde un mandat) et le journal du mandat (les identifiants de session
#: que le principal ``orchestrator`` doit prouver). La preuve de contrat d'hôte, elle, reste lisible.
_ZONE_SECRET_FILES = ("cockpit-key", "mandates.authority.jsonl")


def names_zone_secret(command: str, targets: tuple[str, ...] = ()) -> str:
    """Le fichier secret de la zone de contrôle qu'un appel lit — jokers, ``cd``, liens et ``..`` résolus ; ``""`` sinon."""
    zones, _grimoire, home = _control_zones()
    secrets = [(name, str(Path(z) / name)) for z in zones for name in _ZONE_SECRET_FILES]
    found: list[tuple[str, str]] = [r for t in targets for r in _resolutions(t, [_cwd()], home)]
    if command:
        found.extend(r for _program, resolved in _command_paths(command, home) for r in resolved)
    for normal, real in found:
        for name, path in secrets:
            if _reaches(normal, real, path):
                return f"{CONTROL_ZONE_MARKER}/{name}"
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
            marker = _names_protected(target) or (CONTROL_ZONE_MARKER if _names_control_zone_path(target) else "")
            if marker:
                return _write_vector(marker, f"écriture de {target}")
    command = facts.command
    if not command:
        return None
    if facts.mutation is not MutationClass.READ_ONLY:
        marker = _names_protected(command) or (CONTROL_ZONE_MARKER if _names_control_zone_command(command) else "")
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
