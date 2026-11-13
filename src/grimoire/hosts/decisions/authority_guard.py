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
  script arbitraire) est écrite à :data:`CONTROL_ZONE`. Depuis #1173, la **révocation** du mandat est réservée
  à une écriture *prouvée* (redirection ``>``/``>>`` vers la zone, ``tee``, ``sed -i``, ``cp``/``mv`` dont la
  destination est la zone, ``python`` qui ouvre en ``w``/``a``) : elle n'ajoute aucune protection quand la
  commande est déjà refusée avant exécution, et transformait un faux positif en arrêt général. Une **lecture**
  de la zone (``cat``, ``tail``, ``head``, ``grep``, ``ls``, ``find``, ``wc``, ``jq``, ``python`` en mode
  ``r``) reste refusée — la clé du cockpit y vit — mais ne révoque plus. Une simple **mention** du chemin,
  dans un heredoc, une chaîne ou un argument de texte et sans redirection vers la zone, n'est ni refusée ni
  révoquée. Une nature incertaine (variable, substitution, ``eval``, ``| bash``) est refusée sans révoquer
  (:func:`_control_zone_effect`). Viser le journal du mandat ou la clé du cockpit par une écriture prouvée
  révoque tous les mandats du poste (``revokes_all``, :func:`touches_mandate_authority`) ;
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

import ast
import contextlib
import os
import re
from dataclasses import dataclass, field
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
    "is_harmless_mention",
    "names_zone_secret",
    "touches_mandate_authority",
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
#: l'affiche à chaque refus. Le mandat n'en dépend pas seul : le journal est chaîné par hash et le mandat du
#: projet de la session se révoque sur écriture caractérisée refusée dans la zone
#: (:func:`grimoire.missions.mandate.revoke_session_mandates`, #982).
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
    """Un vecteur reconnu : son code, ce qu'il viserait, s'il ne compte qu'en governed, et s'il révoque.

    ``revokes`` (#982) : vrai pour tout refus dans la zone de contrôle, sauf une mention prouvée inoffensive
    (:func:`is_harmless_mention`) — refusée, mais le mandat tient. ``revokes_all`` : la cible peut être le journal
    du mandat ou la clé du cockpit, qui forgent un mandat pour n'importe quel projet ; tous les mandats tombent.
    """

    code: str
    what: str
    governed_only: bool
    revokes: bool = False
    revokes_all: bool = False


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


def _write_vector(marker: str, what: str, *, revokes: bool, revokes_all: bool = False) -> ForgeryVector:
    """Le vecteur d'une écriture qui vise *marker* : l'instantané du ledger (K13) et la zone de contrôle ont leur code.

    Seule la zone de contrôle révoque (#982) ; *revokes_all* n'a de sens qu'avec *revokes*.
    """
    if marker == SNAPSHOT_FILENAME:
        code = "ledger-snapshot"
    elif marker == CONTROL_ZONE_MARKER:
        code = "control-zone"
    else:
        code = "journal"
    revoking = revokes and code == "control-zone"
    return ForgeryVector(code, what, governed_only=False, revokes=revoking, revokes_all=revoking and revokes_all)


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
    segments, dirs = _segments_and_dirs([_strip_heredoc_bodies(command), *extract_c_bodies(command)], home)
    return [
        (launched[0].rsplit("/", 1)[-1], [r for word in launched[1:] for r in _resolutions(word, dirs, home)])
        for launched in segments
        if launched
    ]


def _segments_and_dirs(texts: list[str], home: str) -> tuple[list[list[str]], list[str]]:
    """Les segments de *texts* (sans préfixes neutres) et chaque répertoire où la commande peut se placer."""
    segments = [_without_neutral(seg) for text in texts for seg in _command_words(text)]
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
    return segments, list(dict.fromkeys(dirs))


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


# ── Mention inoffensive : la seule qui ne révoque pas (#982) ─────────────────
#
# Le refus lit la commande entière, heredocs et textes cités compris (échec fermé). La révocation, elle, coupe
# toute l'autonomie jusqu'à un tour humain ; une prose (message de commit, corps d'issue) qui cite la zone ne doit
# pas la déclencher. Mais reconnaître une *écriture* par analyse de texte échoue ouvert : variable, ``$(…)``,
# ``| bash``, préfixe ``sudo -u``, écrivain non listé. On inverse donc la charge : on révoque **sauf** si la
# mention est prouvée inoffensive par une liste blanche stricte. Tout ce qu'elle ne sait pas lire révoque.

#: Lecteurs purs : la zone peut être un de leurs opérandes (``cat`` seulement sans redirection en écriture).
_READERS = frozenset({"grep", "egrep", "fgrep", "rg", "cat", "ls", "head", "tail", "wc"})
#: Programmes admis dans une commande qui cite la zone, à condition qu'eux-mêmes ne la nomment pas.
_PLAIN = frozenset({"echo", "printf", "pwd", "true", "false", "test", "sort", "uniq", "diff", "date", "sleep"})
#: Ceux de :data:`_PLAIN` dont un opérande qui cite la zone ne peut qu'être une donnée écrite en sortie : la
#: mention y est admise (#1173). ``sort``, ``uniq`` et ``diff`` lisent leurs opérandes comme des fichiers.
_PLAIN_MENTION = frozenset({"echo", "printf", "pwd", "true", "false", "test", "date", "sleep"})
_GIT_SUBCOMMANDS = frozenset(
    {"add", "commit", "tag", "status", "log", "diff", "show", "push", "fetch", "rev-parse", "branch", "ls-files"}
)
_GH_ACTIONS = frozenset({"create", "comment", "edit", "view", "list"})
#: Options dont la valeur est de la prose : la seule place où ``git`` et ``gh`` peuvent citer la zone.
_GIT_PROSE = ("-m", "--message")
_GH_PROSE = ("-b", "--body", "-t", "--title")
_OUT_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>"})


@dataclass
class _Segment:
    """Une commande simple : ses mots (guillemets retirés), ses redirections, ses heredocs."""

    words: list[str]
    redirects: list[tuple[str, str]]
    heredocs: int = 0
    #: Les corps de heredoc à tag quoté (aucune expansion de shell) ; un tag non quoté y met ``None``.
    bodies: list[str | None] = field(default_factory=list)


class _Unreadable(Exception):  # noqa: N818 — signal interne : la commande sort de ce que la liste blanche sait lire
    """La commande porte une construction que la liste blanche ne lit pas : échec fermé."""


def _parse_strict(command: str) -> list[_Segment]:
    """Les segments de *command*, ou :class:`_Unreadable` dès une construction hors de la liste blanche.

    Refusés : ``$`` et backtick (hors apostrophes), ``(``/``)``/``{``/``}``, ``<(``/``>(``, ``<<<``, ``&`` seul,
    ``#`` en début de mot, antislash hors guillemets (sauf fin de ligne), guillemet non fermé, NUL, heredoc sans
    fin. Les corps de heredoc sont sautés ; leur consommateur est jugé par :func:`_harmless_segment`.
    """
    if "\x00" in command:
        raise _Unreadable("NUL")
    segments: list[_Segment] = []
    seg = _Segment([], [])
    word: list[str] = []
    in_word = quoted = False
    pending: list[tuple[str, bool, _Segment, bool]] = []
    redirect = ""
    i, n = 0, len(command)

    def flush() -> None:
        nonlocal in_word, quoted, redirect
        if not in_word:
            return
        text = "".join(word)
        word.clear()
        if redirect:
            # ``>&2`` duplique un descripteur ; ``>&f`` écrit dans f.
            if not (redirect == ">&" and (text.isdigit() or text == "-")):
                seg.redirects.append((">" if redirect == ">&" else redirect, text))
            redirect = ""
        else:
            seg.words.append(text)
        in_word = quoted = False

    def close() -> None:
        nonlocal seg
        flush()
        if redirect:
            raise _Unreadable("redirection sans cible")
        if seg.words or seg.redirects or seg.heredocs:
            segments.append(seg)
        seg = _Segment([], [])

    def read_redirect(op: str, at: int) -> int:
        nonlocal redirect, in_word
        if in_word and not quoted and "".join(word).isdigit():  # ``2>`` : descripteur collé
            word.clear()
            in_word = False
        flush()
        if redirect:
            raise _Unreadable("redirections enchaînées")
        if command.startswith("(", at):
            raise _Unreadable("substitution de processus")
        redirect = op
        return at

    while i < n:
        ch = command[i]
        if ch == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise _Unreadable("apostrophe non fermée")
            word.append(command[i + 1 : end])
            in_word = quoted = True
            i = end + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and command[j] != '"':
                if command[j] in "$`":
                    raise _Unreadable("expansion entre guillemets")
                if command[j] == "\\" and j + 1 < n:
                    word.append(command[j + 1] if command[j + 1] in '"\\' else command[j : j + 2])
                    j += 2
                    continue
                word.append(command[j])
                j += 1
            if j >= n:
                raise _Unreadable("guillemet non fermé")
            in_word = quoted = True
            i = j + 1
            continue
        if ch in "$`(){}":
            raise _Unreadable(ch)
        if ch == "\\":
            if command.startswith("\\\n", i):
                i += 2
                continue
            raise _Unreadable("antislash")
        if ch == "#" and not in_word:
            raise _Unreadable("commentaire")
        if ch in " \t\r":
            flush()
            i += 1
            continue
        if ch == "\n" or ch == ";":
            close()
            i += 1
            if ch == "\n" and pending:
                i = _skip_heredoc_bodies(command, i, pending)
            continue
        if ch == "|":
            if command.startswith("|&", i):
                raise _Unreadable("|&")
            close()
            i += 2 if command.startswith("||", i) else 1
            continue
        if ch == "&":
            if command.startswith("&&", i):
                close()
                i += 2
                continue
            if command.startswith("&>", i):
                op = "&>>" if command.startswith("&>>", i) else "&>"
                i = read_redirect(op, i + len(op))
                continue
            raise _Unreadable("&")
        if ch == "<":
            if command.startswith("<<<", i) or command.startswith("<(", i):
                raise _Unreadable("<<< ou <(")
            if command.startswith("<<", i):
                flush()
                j = i + 2
                strip_tabs = command.startswith("-", j)
                j += 1 if strip_tabs else 0
                while j < n and command[j] in " \t":
                    j += 1
                tag = re.match(r"""(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1""", command[j:])
                if tag is None:
                    raise _Unreadable("heredoc illisible")
                pending.append((tag.group(2), strip_tabs, seg, bool(tag.group(1))))
                seg.heredocs += 1
                i = j + tag.end()
                continue
            op = "<>" if command.startswith("<>", i) else "<"
            i = read_redirect(op, i + len(op))
            continue
        if ch == ">":
            op = next(o for o in (">>", ">|", ">&", ">") if command.startswith(o, i))
            i = read_redirect(op, i + len(op))
            continue
        word.append(ch)
        in_word = True
        i += 1
    close()
    if pending:
        raise _Unreadable("heredoc sans corps")
    return segments


def _skip_heredoc_bodies(command: str, start: int, pending: list[tuple[str, bool, _Segment, bool]]) -> int:
    """Saute le corps de chaque heredoc en attente ; un heredoc sans ligne de fin est illisible.

    Le corps est gardé sur son segment quand le tag est quoté (rien n'y est développé par le shell).
    """
    i = start
    while pending:
        tag, strip_tabs, owner, quoted = pending.pop(0)
        lines: list[str] = []
        while True:
            if i > len(command):
                raise _Unreadable("heredoc sans fin")
            end = command.find("\n", i)
            line = command[i:] if end < 0 else command[i:end]
            i = len(command) + 1 if end < 0 else end + 1
            if (line.lstrip("\t") if strip_tabs else line) == tag:
                break
            lines.append(line.lstrip("\t") if strip_tabs else line)
        owner.bodies.append("\n".join(lines) if quoted else None)
    return min(i, len(command))


def _attached(word: str, option: str) -> bool:
    """``--message=x`` pour une option longue, ``-mx`` pour une courte : la valeur est collée à l'option."""
    return word.startswith(option + "=") if option.startswith("--") else len(word) > 2 and word.startswith(option)


def _prose_positions(words: list[str], start: int, options: tuple[str, ...]) -> set[int]:
    """Les index des mots qui sont la valeur d'une option de prose (``-m x``, ``--message=x``, ``-mx``)."""
    found: set[int] = set()
    for i in range(start, len(words)):
        word = words[i]
        if word in options and i + 1 < len(words):
            found.add(i + 1)
        elif any(_attached(word, o) for o in options):
            found.add(i)
    return found


def _stdin_option(words: list[str], options: tuple[str, ...]) -> bool:
    """Vrai si une de *options* vaut ``-`` (le heredoc est lu comme fichier de message)."""
    return any(a in options and b == "-" for a, b in pairwise(words)) or any(f"{o}=-" in words for o in options)


# ── Lecture python prouvée (#1125) ───────────────────────────────────────────
#
# ``python3 -I - <<'P' … json.load(open('…/ledger.snapshot.json')) … P`` ne fait que lire. On ne le prouve que
# sur l'arbre syntaxique, en liste blanche : le seul import est ``json``, ``open`` n'a que le mode de lecture,
# les seuls appels sont des fonctions pures ou des méthodes de lecture de données. Tout le reste — autre
# module, ``getattr``, ``exec``, nom ou attribut à double soulignement, ``.write``, ``Path`` — n'est pas prouvé.

_PY_SAFE_FUNCS = frozenset(
    {"print", "len", "sorted", "sum", "min", "max", "set", "list", "dict", "tuple", "str", "int", "float", "bool",
     "enumerate", "range", "any", "all", "isinstance", "zip", "reversed", "abs", "round"}
)
_PY_SAFE_METHODS = frozenset(
    {"get", "items", "keys", "values", "join", "format", "count", "startswith", "endswith", "split", "strip",
     "lower", "upper", "index", "load", "loads", "dumps", "read", "copy"}
)
_PY_READ_MODES = frozenset({"r", "rb", "rt"})


def _py_open_is_read(call: ast.Call) -> bool:
    if not call.args or len(call.args) > 2 or not isinstance(call.args[0], ast.Constant):
        return False
    if len(call.args) == 2 and not (isinstance(call.args[1], ast.Constant) and call.args[1].value in _PY_READ_MODES):
        return False
    for kw in call.keywords:
        if kw.arg == "mode":
            if not (isinstance(kw.value, ast.Constant) and kw.value.value in _PY_READ_MODES):
                return False
        elif kw.arg != "encoding":
            return False
    return True


def _python_reads_only(body: str) -> bool:
    """Vrai seulement si le script python *body* est prouvé sans écriture ni effet de bord (liste blanche d'AST)."""
    try:
        tree = ast.parse(body)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name != "json" or a.asname for a in node.names):
                    return False
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    if fn.id == "open":
                        if not _py_open_is_read(node):
                            return False
                    elif fn.id not in _PY_SAFE_FUNCS:
                        return False
                elif isinstance(fn, ast.Attribute):
                    if fn.attr not in _PY_SAFE_METHODS:
                        return False
                else:
                    return False
                if fn_has_file_kw(node):
                    return False
            elif isinstance(node, ast.Name):
                if node.id.startswith("__") or node.id in {"eval", "exec", "compile", "getattr", "setattr", "globals",
                                                           "locals", "vars", "input", "breakpoint", "os", "sys"}:
                    return False
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("_"):
                    return False
            elif isinstance(node, (ast.ImportFrom, ast.Global, ast.Nonlocal, ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef, ast.Lambda, ast.Await, ast.Yield, ast.YieldFrom, ast.Delete,
                                   ast.With, ast.Try)):
                return False
        return True
    except Exception:
        return False


def fn_has_file_kw(call: ast.Call) -> bool:
    """``print(…, file=f)`` écrit ailleurs que sur la sortie standard."""
    return any(kw.arg == "file" or kw.arg is None for kw in call.keywords)


def _python_stdin_segment(seg: _Segment) -> bool:
    """``python[3] [-I|-B|-u|-S|-E]… -`` avec un seul heredoc à tag quoté dont le corps est prouvé en lecture seule."""
    words = seg.words
    if not words or not _PYTHON.match(words[0]) or seg.redirects or seg.heredocs != 1:
        return False
    flags = words[1:-1]
    if words[-1] != "-" or any(f not in ("-I", "-B", "-u", "-S", "-E", "-s") for f in flags):
        return False
    body = seg.bodies[0] if seg.bodies else None
    return body is not None and _python_reads_only(body)


def _python_c_segment(seg: _Segment) -> bool:
    """``python[3] [-I|-B|-u|-S|-E]… -c <code>`` dont le code est prouvé en lecture seule (#1173).

    Même preuve que :func:`_python_stdin_segment` : le corps est lu sur l'AST, jamais exécuté ici.
    """
    words = seg.words
    if not words or not _PYTHON.match(words[0]) or seg.redirects or seg.heredocs or "-c" not in words:
        return False
    at = words.index("-c")
    if any(f not in ("-I", "-B", "-u", "-S", "-E", "-s") for f in words[1:at]) or at + 1 >= len(words):
        return False
    return _python_reads_only(words[at + 1])


def _harmless_segment(seg: _Segment, reaches: list[bool], *, python_reads: bool = False) -> bool:
    """Le segment cite la zone seulement là où la liste blanche l'admet (voir :func:`is_harmless_mention`)."""
    words = seg.words
    if not words or "/" in words[0] or _ENV_ASSIGNMENT.match(words[0]):
        return False
    program = words[0]
    mentions = {i for i, hit in enumerate(reaches) if hit}
    writes_out = any(op in _OUT_REDIRECTS for op, _target in seg.redirects)
    if program in _READERS:
        if program == "rg" and any(w.startswith("--pre") for w in words):
            return False
        if seg.heredocs:  # ``cat > f <<EOF`` : le seul heredoc qu'un lecteur porte
            return program == "cat" and seg.heredocs == 1 and words in (["cat"], ["cat", "-"]) and writes_out
        return not (program == "cat" and mentions and writes_out)
    if program == "git":
        i = 1
        while i < len(words) and words[i] == "-C":
            if i + 1 in mentions:
                return False
            i += 2
        if i >= len(words) or words[i] not in _GIT_SUBCOMMANDS:
            return False
        sub = words[i]
        prose = _prose_positions(words, i + 1, _GIT_PROSE) if sub in ("commit", "tag") else set()
        if seg.heredocs and not (sub == "commit" and seg.heredocs == 1 and _stdin_option(words, ("-F", "--file"))):
            return False
        return mentions <= prose
    if program == "gh":
        if len(words) < 3 or words[1] not in ("issue", "pr") or words[2] not in _GH_ACTIONS:
            return False
        if seg.heredocs and not (seg.heredocs == 1 and _stdin_option(words, ("-F", "--body-file"))):
            return False
        return mentions <= _prose_positions(words, 3, _GH_PROSE)
    if program in _PLAIN:
        if seg.heredocs:
            return False
        if program in _PLAIN_MENTION:  # #1173 : la zone dans une chaîne de `echo` est une donnée, pas une cible
            return True
        return not mentions
    if python_reads and _PYTHON.match(program):  # #1125 / #1173 : lecture python prouvée (heredoc ou `-c`)
        return _python_stdin_segment(seg) or _python_c_segment(seg)
    return False


def is_harmless_mention(command: str, *, snapshot: bool = False) -> bool:
    """Vrai seulement si *command* cite la zone de contrôle de façon **prouvée** inoffensive (#982).

    La zone (chemin, nom d'un de ses fichiers, ou chemin résolu qui l'atteint) n'apparaît que dans : la valeur
    d'une option de prose (``git commit|tag -m/--message``, ``gh issue|pr create|comment|edit --body/--title``) ;
    le corps d'un heredoc consommé par ``cat > <hors zone>``, ``git commit -F -`` ou ``gh … --body-file -`` ; un
    opérande de lecture pure (``grep``, ``rg``, ``ls``, ``head``, ``tail``, ``wc``, ``cat`` sans redirection en
    écriture) ; une chaîne d'un programme d'affichage (``echo``, ``printf``, …). Chaque segment est un programme
    de la liste blanche, sans préfixe ni assignation ; ``cd`` ne mène pas sous ``~/.grimoire`` ; aucune
    redirection en écriture ne l'atteint. Tout le reste — ``$``, ``$(…)``, backtick, ``eval``, ``| bash``,
    ``<(…)``, ``source``, guillemet non fermé, programme ou préfixe inconnu — rend faux. Depuis #1173,
    :func:`_control_zone_effect` distingue ce qui est refusé sans révoquer (lecture, nature incertaine) de ce
    qui révoque (écriture prouvée) ; cette fonction ne répond que « prouvé sans écriture ». Une exception rend
    faux aussi.

    ``snapshot=True`` (#1125) : même liste blanche, appliquée à l'instantané du ledger — ``reaches`` y compte les
    mots qui nomment un fichier protégé, et un script python lu sur stdin (heredoc quoté) passe s'il est prouvé
    en lecture seule (:func:`_python_reads_only`).
    """
    try:
        segments = _parse_strict(command)
        zones, grimoire, home = _control_zones()
        dirs = [_cwd()]

        def reaches(word: str) -> bool:
            named = _names_protected(word)
            if named == CONTROL_ZONE_MARKER or (snapshot and named):
                return True
            return any(
                any(_reaches(normal, real, z) for z in zones) or _inside(normal, grimoire) or _inside(real, grimoire)
                for normal, real in _resolutions(word, dirs, home)
            )

        for seg in segments:
            if seg.words and seg.words[0] in ("cd", "pushd"):
                targets = seg.words[1:]
                if len(targets) > 1 or seg.redirects or seg.heredocs or (targets and targets[0].startswith("-")):
                    return False
                if reaches(targets[0] if targets else "~"):
                    return False
                dirs.extend(real for _normal, real in _resolutions(targets[0] if targets else "~", list(dirs), home))
                continue
            if any(op in _OUT_REDIRECTS and reaches(target) for op, target in seg.redirects):
                return False
            if not _harmless_segment(seg, [reaches(w) for w in seg.words], python_reads=snapshot):
                return False
        return True
    except Exception:  # _Unreadable compris : ce qu'on ne sait pas lire révoque
        return False


# ── Effet d'une commande sur la zone de contrôle (#1173) ─────────────────────
#
# Une lecture est refusée (la clé du cockpit y vit) mais ne révoque plus ; une simple mention du chemin est
# admise ; seule une écriture *prouvée* révoque. Le classement est un tri par segment : écriture explicite,
# lecture prouvée, mention de la liste blanche, sinon incertain — refusé sans révoquer. Une commande composée
# dont les segments étrangers à la zone sont anodins n'est plus refusée pour un segment qui ne la nomme pas.

_ZONE_WRITE = "write"
_ZONE_READ = "read"
_ZONE_MENTION = "mention"
_ZONE_UNCERTAIN = "uncertain"

#: Lecteurs purs dont un opérande de fichier qui atteint la zone est une lecture (#1173).
_ZONE_READERS = frozenset({"grep", "egrep", "fgrep", "rg", "cat", "ls", "head", "tail", "wc", "find", "jq"})
#: Ce qu'un segment qui ne cite pas la zone ne doit pas être : il consommerait le texte d'un autre segment, où
#: la zone n'était qu'une donnée (`echo '… > zone' | bash`).
_STDIN_INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "source", ".", "xargs", "awk", "perl", "ruby", "node",
     "python", "python2", "python3", "pypy", "pypy3"}
)
#: Méthodes d'écriture d'un chemin en Python (``Path(...).write_text``, ``os.remove``, …).
_PY_WRITE_METHODS = frozenset(
    {"write_text", "write_bytes", "touch", "unlink", "remove", "rmdir", "rmtree", "rename", "replace"}
)


def _python_program(words: list[str]) -> bool:
    return bool(words) and bool(_PYTHON.match(words[0].rsplit("/", 1)[-1]))


def _python_body(seg: _Segment) -> str | None:
    """Le corps python d'un segment : le code de ``-c``, ou le heredoc quoté lu sur stdin ; ``None`` sinon."""
    words = seg.words
    if not _python_program(words):
        return None
    if "-c" in words:
        at = words.index("-c")
        return words[at + 1] if at + 1 < len(words) else ""
    if seg.heredocs == 1 and seg.bodies and words[-1] == "-":
        return seg.bodies[0]
    return None


def _reaches_control_zone(word: str, dirs: list[str], home: str, zones: list[str]) -> bool:
    """Vrai si *word* nomme la zone (chemin, nom d'un de ses fichiers) ou s'y résout."""
    if _names_protected(word) == CONTROL_ZONE_MARKER:
        return True
    return any(
        _reaches(normal, real, zone) for normal, real in _resolutions(word, dirs, home) for zone in zones
    )


def _zone_constant(node: ast.AST | None, dirs: list[str], home: str, zones: list[str]) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _reaches_control_zone(node.value, dirs, home, zones)
    )


def _python_zone_effect(body: str, dirs: list[str], home: str, zones: list[str]) -> str | None:
    """L'effet du script *body* sur la zone : écriture, lecture, mention, incertain ; ``None`` s'il ne la nomme pas."""
    try:
        tree = ast.parse(body)
    except SyntaxError:
        return _ZONE_UNCERTAIN if _names_protected(body) == CONTROL_ZONE_MARKER else None
    writes = reads = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        first = node.args[0] if node.args else None
        receiver = fn.value.args[0] if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Call) and fn.value.args else None
        if name == "open" and _zone_constant(first, dirs, home, zones):
            modes = list(node.args[1:2]) + [kw.value for kw in node.keywords if kw.arg == "mode"]
            if any(isinstance(m, ast.Constant) and m.value not in _PY_READ_MODES for m in modes):
                writes = True
            elif all(isinstance(m, ast.Constant) and m.value in _PY_READ_MODES for m in modes):
                reads = True  # aucun mode : ``r`` par défaut
        elif name in _PY_WRITE_METHODS and (
            _zone_constant(first, dirs, home, zones) or _zone_constant(receiver, dirs, home, zones)
        ):
            writes = True
        elif name in {"open", "read_text", "read_bytes"} and _zone_constant(receiver, dirs, home, zones):
            reads = True
    if writes:
        return _ZONE_WRITE
    if reads:
        return _ZONE_READ
    if _names_protected(body) == CONTROL_ZONE_MARKER:
        # Le chemin n'est qu'une donnée du script : admis si le script est prouvé sans effet de bord.
        return _ZONE_MENTION if _python_reads_only(body) else _ZONE_UNCERTAIN
    return None


def _reader_operands(program: str, words: list[str]) -> list[str]:
    """Les opérandes de *program* qui désignent un fichier lu (le motif de ``grep``/le filtre de ``jq`` exclus)."""
    rest = [w for w in words[1:] if not w.startswith("-")]
    if program in {"grep", "egrep", "fgrep", "rg"}:
        if not any(
            w in ("-e", "--regexp", "-f", "--file") or w.startswith(("--regexp=", "--file=")) for w in words[1:]
        ):
            rest = rest[1:]
    elif program == "jq":
        rest = rest[1:]
    elif program == "find":
        cut = next((i for i, w in enumerate(words[1:]) if w.startswith("-")), len(words) - 1)
        return words[1 : 1 + cut]
    return rest


def _segment_reads_zone(seg: _Segment, dirs: list[str], home: str, zones: list[str]) -> bool:
    words = seg.words
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1]
    return program in _ZONE_READERS and any(
        _reaches_control_zone(w, dirs, home, zones) for w in _reader_operands(program, words)
    )


def _sed_in_place(words: list[str]) -> bool:
    return any(
        w in ("-i", "--in-place") or w.startswith("--in-place=") or (w.startswith("-") and not w.startswith("--") and "i" in w[1:])
        for w in words[1:]
    )


def _segment_writes_zone(
    seg: _Segment, dirs: list[str], home: str, zones: list[str], grimoire: str
) -> bool:
    """Écriture prouvée vers la zone : redirection, ``tee``, ``sed -i``, ``cp``/``mv`` dont la destination y est."""
    if any(op in _OUT_REDIRECTS and _reaches_control_zone(target, dirs, home, zones) for op, target in seg.redirects):
        return True
    words = seg.words
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1]
    operands = [w for w in words[1:] if not w.startswith("-")]
    if program == "tee":
        return any(_reaches_control_zone(w, dirs, home, zones) for w in operands)
    if program == "sed" and _sed_in_place(words):
        return any(_reaches_control_zone(w, dirs, home, zones) for w in operands)
    if program in _RELOCATORS and any(
        grimoire in {normal, real} for w in operands for normal, real in _resolutions(w, dirs, home)
    ):
        return True  # déplacer/supprimer ~/.grimoire emporte la zone de contrôle
    if program == "ln" and any(
        home in {normal, real} or grimoire in {normal, real}
        for w in operands
        for normal, real in _resolutions(w, dirs, home)
    ):
        return True  # un lien vers le home ou ~/.grimoire exposerait la zone sous un autre nom
    if program in ("cp", "mv", "install", "ln"):
        for i, word in enumerate(words):
            if word in ("-t", "--target-directory") and i + 1 < len(words) and _reaches_control_zone(
                words[i + 1], dirs, home, zones
            ):
                return True
            if word.startswith("--target-directory=") and _reaches_control_zone(word.split("=", 1)[1], dirs, home, zones):
                return True
        return bool(operands) and _reaches_control_zone(operands[-1], dirs, home, zones)
    return False


def _consumes_stdin(words: list[str]) -> bool:
    """Vrai si le segment peut exécuter en stdin le texte d'un autre segment (`echo '… > zone' | bash`).

    Un interpréteur lancé avec ``-c`` ou sur un fichier ``.py`` ne consomme pas stdin ; un segment qui ne cite
    pas la zone et ne lit pas stdin ne peut pas la toucher (limite du code arbitraire mise à part).
    """
    if not words:
        return False
    program = words[0].rsplit("/", 1)[-1]
    if program not in _STDIN_INTERPRETERS:
        return False
    return not (
        "-c" in words[1:] or any(w.endswith(".py") for w in words[1:] if not w.startswith("-"))
    )


def _segment_touches_zone(seg: _Segment, dirs: list[str], home: str, zones: list[str]) -> bool:
    if any(_reaches_control_zone(w, dirs, home, zones) for w in seg.words):
        return True
    if any(_reaches_control_zone(target, dirs, home, zones) for _op, target in seg.redirects):
        return True
    return any(isinstance(body, str) and _names_protected(body) == CONTROL_ZONE_MARKER for body in seg.bodies)


def _control_zone_effect(command: str) -> str:
    """Ce que *command* ferait à ``~/.grimoire/control`` : écriture, lecture, mention ou incertain (#1173).

    Seul :data:`_ZONE_WRITE` révoque le mandat. Un segment qui ne nomme pas la zone et n'est pas un interpréteur
    de stdin ne change rien ; tout ce que la liste blanche ne sait pas lire est incertain (échec fermé, sans
    révocation). Une exception rend incertain.
    """
    try:
        segments = _parse_strict(command)
    except Exception:  # _Unreadable compris : ce qu'on ne sait pas lire est incertain, jamais permis
        return _ZONE_UNCERTAIN
    try:
        zones, grimoire, home = _control_zones()
        dirs = [_cwd()]
        reads = False
        for seg in segments:
            words = seg.words
            if words and words[0] in ("cd", "pushd"):
                targets = words[1:]
                if len(targets) > 1 or seg.redirects or seg.heredocs or (targets and targets[0].startswith("-")):
                    return _ZONE_UNCERTAIN
                target = targets[0] if targets else "~"
                if _reaches_control_zone(target, dirs, home, zones):
                    reads = True
                dirs.extend(real for _normal, real in _resolutions(target, list(dirs), home))
                continue
            # ``-C``/``--directory``/``-t`` déplacent les chemins relatifs du segment (``tar -C ~/.grimoire``).
            seg_dirs = list(dirs)
            for prev, word in pairwise(words):
                if prev in _DIR_FLAGS:
                    seg_dirs.extend(real for _normal, real in _resolutions(word, list(seg_dirs), home))
            for word in words:
                if word.startswith(("--directory=", "--target-directory=")):
                    seg_dirs.extend(
                        real for _normal, real in _resolutions(word.split("=", 1)[1], list(seg_dirs), home)
                    )
            if _python_program(words):
                body = _python_body(seg)
                effect = _python_zone_effect(body, seg_dirs, home, zones) if body is not None else None
                if effect == _ZONE_WRITE:
                    return _ZONE_WRITE
                if effect == _ZONE_READ:
                    reads = True
                    continue
                if effect == _ZONE_UNCERTAIN:
                    return _ZONE_UNCERTAIN
                if effect == _ZONE_MENTION:
                    continue
            if _segment_writes_zone(seg, seg_dirs, home, zones, grimoire):
                return _ZONE_WRITE
            if _segment_reads_zone(seg, seg_dirs, home, zones):
                reads = True
                continue
            if not _segment_touches_zone(seg, seg_dirs, home, zones):
                if _consumes_stdin(words):
                    return _ZONE_UNCERTAIN
                continue
            if not _harmless_segment(seg, [_reaches_control_zone(w, seg_dirs, home, zones) for w in words]):
                return _ZONE_UNCERTAIN
        return _ZONE_READ if reads else _ZONE_MENTION
    except Exception:  # analyse de texte : ce qu'on ne sait pas classer est incertain, jamais permis
        return _ZONE_UNCERTAIN


#: Ce qui, nommé par l'appel, peut frapper un mandat de *n'importe quel* projet : le journal du mandat (sa tête,
#: ses marqueurs de révocation) et la clé du cockpit. Voir :func:`touches_mandate_authority`.
_MANDATE_AUTHORITY_NAMES = (AUTHORITY_SUFFIX, "cockpit-key", "/revoked")


def touches_mandate_authority(command: str, targets: tuple[str, ...] = ()) -> bool:
    """Vrai si l'appel vise (ou peut viser) le journal du mandat ou la clé du cockpit (#982, M2).

    Ces deux fichiers suffisent à forger l'octroi d'un mandat pour **un autre** projet : une tentative d'écriture
    y fait tomber la confiance de tous les mandats du poste. Vrai aussi quand la cible n'est pas lisible (``$``,
    backtick) : échec fermé. Une exception rend vrai.
    """
    try:
        texts = [_norm(t) for t in (command, *targets) if t]
        if any(name in text for text in texts for name in _MANDATE_AUTHORITY_NAMES):
            return True
        if any("$" in text or "`" in text for text in texts):
            return True
        return bool(names_zone_secret(command, targets))
    except Exception:
        return True


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
                # Le chemin d'un outil d'écriture est sa cible : écriture caractérisée.
                return _write_vector(
                    marker,
                    f"écriture de {target}",
                    revokes=True,
                    revokes_all=touches_mandate_authority("", (target,)),
                )
    command = facts.command
    if not command:
        return None
    marker = _names_protected(command) or (CONTROL_ZONE_MARKER if _names_control_zone_command(command) else "")
    if marker == CONTROL_ZONE_MARKER:
        # #1173 : seule une écriture *prouvée* révoque le mandat. Une lecture de la zone reste refusée (la clé du
        # cockpit y vit) sans révoquer ; une simple mention (heredoc, chaîne, argument de texte) est admise ; une
        # nature incertaine est refusée sans révoquer.
        effect = _control_zone_effect(command)
        if effect == _ZONE_MENTION:
            return None
        if effect == _ZONE_WRITE:
            return _write_vector(
                marker,
                f"commande qui écrit dans la zone de contrôle ({marker})",
                revokes=True,
                revokes_all=touches_mandate_authority(command),
            )
        verbs = "lit" if effect == _ZONE_READ else "pourrait toucher"
        return _write_vector(
            marker,
            f"commande qui {verbs} la zone de contrôle ({marker}) — refus prudent, le mandat n'est pas révoqué",
            revokes=False,
        )
    if marker and facts.mutation is not MutationClass.READ_ONLY:
        # #1125 : l'instantané seulement lu ou cité dans de la prose, prouvé par la liste blanche, n'est pas refusé.
        if marker == SNAPSHOT_FILENAME and is_harmless_mention(command, snapshot=True):
            marker = ""
        if marker:
            return _write_vector(
                marker,
                f"commande qui écrit (ou peut écrire) un journal d'autorité ou l'instantané du ledger ({marker})",
                revokes=True,
                revokes_all=touches_mandate_authority(command),
            )
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
