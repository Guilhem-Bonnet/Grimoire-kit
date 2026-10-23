"""Chaînage du journal d'actions observées et décompte des écritures perdues (W4-03).

Le journal ``evidence-log.jsonl`` (voir :mod:`.evidence_journal`) est la preuve
que ``gate check`` et ``standard verify`` projettent dans ``evidence-pack.md``.
Avant ce module, il se lisait ligne à ligne sans aucun lien entre les lignes :
une ligne réécrite ou une dernière ligne supprimée ne se voyait pas, et une
``OSError`` à l'écriture était avalée sans trace : le journal paraissait
simplement plus court.

Le chaînage tient en trois pièces.

1. **``prev``** : chaque nouvelle ligne porte le SHA-256 de la ligne qui la
   précède dans le fichier (octets exacts, sans le saut de ligne), ou
   ``"genesis"`` pour la première. Modifier, retirer ou insérer une ligne casse
   le ``prev`` de la suivante : la rupture est localisée entre deux lignes.
2. **Un fichier d'ancre** ``evidence-log.head.json``, à côté du journal, qui
   tient le nombre de lignes chaînées, le hachage de la dernière, et
   ``dropped_writes``. La dernière ligne n'a pas de suivante pour la trahir :
   c'est l'ancre qui détecte sa suppression ou sa modification.
3. **``dropped_writes``**, dans l'ancre et non dans le journal : un événement
   perdu ne peut pas s'inscrire dans le fichier qui vient de refuser l'écriture.

Les lignes écrites avant ce module n'ont pas de ``prev`` : elles sont lues comme
``legacy`` (non vérifiables, jamais une rupture). Une ligne sans ``prev`` qui
suit une ligne chaînée, elle, est une rupture.

**Limite assumée.** Sans ancre externe (un dépôt, un serveur, une signature),
la chaîne détecte la corruption et les modifications maladroites ; elle ne rend
pas le journal infalsifiable : qui réécrit le journal *et* son ancre, en
recalculant les hachages, produit un journal cohérent. Si le dossier est
inscriptible nulle part, le compte des écritures perdues ne vit que dans le
processus (:func:`process_dropped_writes`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.fsio import atomic_write, locked

if TYPE_CHECKING:  # base importe ruamel : hors du chemin chaud des hooks (issue #419)
    from grimoire.core.standard_checks.base import StandardVerificationResult

__all__ = [
    "GENESIS",
    "HEAD_SUFFIX",
    "ChainReport",
    "InsertedLine",
    "analyze_chain",
    "append_chained",
    "digest_of",
    "dropped_writes",
    "head_path",
    "process_dropped_writes",
    "record_dropped_write",
    "verify_evidence_chain",
]

#: ``prev`` de la première ligne chaînée d'un journal sans ligne antérieure.
GENESIS = "genesis"

#: Suffixe du fichier d'ancre : ``evidence-log.jsonl`` -> ``evidence-log.head.json``.
HEAD_SUFFIX = ".head.json"

#: Attente maximale du verrou d'écriture : le hook ``PostToolUse`` a un budget de 30 ms,
#: un verrou tenu plus longtemps vaut une écriture perdue (comptée), pas un hook bloqué.
_LOCK_TIMEOUT_S = 2.0

#: Écritures perdues dans CE processus, quand l'ancre elle-même n'est pas inscriptible.
_process_drops: dict[Path, int] = {}


def process_dropped_writes(path: Path) -> int:
    """Écritures du journal *path* perdues par ce processus (ancre incluse ou non)."""
    return _process_drops.get(path, 0)


def head_path(log_path: Path) -> Path:
    """Chemin du fichier d'ancre du journal *log_path*."""
    return log_path.with_name(log_path.stem + HEAD_SUFFIX)


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest_of(raw: bytes) -> str:
    """Empreinte d'une ligne du journal (octets exacts, sans saut de ligne) : celle que ``prev`` porte."""
    return _digest(raw)


@dataclass(frozen=True, slots=True)
class _Head:
    count: int
    head: str
    dropped: int


def _read_head(log_path: Path) -> _Head | None:
    """L'ancre, ``None`` si absente, illisible ou mal formée (jamais levée)."""
    try:
        data = json.loads(head_path(log_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # silent-ok — ancre absente ou illisible : dite « non ancrée » par la vérification
        return None
    if not isinstance(data, dict):
        return None
    count, head, dropped = data.get("count"), data.get("head"), data.get("dropped_writes", 0)
    if not (isinstance(count, int) and not isinstance(count, bool) and count >= 0):
        return None
    if not isinstance(head, str):
        return None
    if not (isinstance(dropped, int) and not isinstance(dropped, bool) and dropped >= 0):
        return None
    return _Head(count, head, dropped)


def _write_head(log_path: Path, state: _Head, *, durable: bool) -> None:
    """Écrit l'ancre. ``durable`` : atomique et synchronisée (``fsio.atomic_write``), pour le décompte des pertes.

    Le chemin courant (``durable=False``) réécrit sur place, sans ``fsync`` : le hook
    ``PostToolUse`` a 30 ms, et deux ``fsync`` par outil les consomment. Une ancre tronquée
    par un crash se lit « absente » (avertissement ``evidence.chain_unanchored``) et le prochain
    ajout la reconstruit depuis le journal.
    """
    payload = json.dumps({"version": 1, "count": state.count, "head": state.head, "dropped_writes": state.dropped}, sort_keys=True) + "\n"
    if durable:
        atomic_write(head_path(log_path), payload)
        return
    with open(head_path(log_path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def _tail(path: Path) -> tuple[bytes | None, bool]:
    """(dernière ligne non vide, le fichier se termine-t-il par un saut de ligne ou est-il vide).

    Lecture par la fin, par blocs doublants : le coût ne dépend pas de la taille du journal.
    """
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, 2)
            if size == 0:
                return None, True
            handle.seek(size - 1)
            clean_end = handle.read(1) == b"\n"
            chunk = 4096
            while True:
                start = max(0, size - chunk)
                handle.seek(start)
                data = handle.read(size - start).rstrip(b"\n")
                cut = data.rfind(b"\n")
                if cut != -1 or start == 0:
                    last = data[cut + 1 :]
                    break
                chunk *= 2
    except FileNotFoundError:  # silent-ok — journal absent : rien à chaîner, la première ligne sera genesis
        return None, True
    if last.strip():
        return last, clean_end
    # Ligne d'espaces en fin de fichier : rare, on retombe sur le parcours complet.
    lines = [line for line in path.read_bytes().split(b"\n") if line.strip()]
    return (lines[-1] if lines else None), clean_end


def _chained_count(path: Path) -> int:
    """Nombre de lignes chaînées du journal (parcours complet : seulement quand l'ancre ne suit plus)."""
    return analyze_chain(path).chained


def append_chained(path: Path, event: dict[str, Any]) -> None:
    """Ajoute *event* au journal avec son ``prev``, puis met l'ancre à jour. Lève en cas d'échec.

    Sous le verrou de :mod:`grimoire.core.fsio` : deux hooks parallèles ne
    forkent pas la chaîne. L'octet exact écrit est celui qu'on hache (mode
    binaire : pas de ``\\r\\n`` fabriqué par la plate-forme).

    **Ordre : l'ancre d'abord, la ligne ensuite.** Une ancre qui retarde sur le
    fichier (ligne écrite, ancre non) est indiscernable d'une dernière ligne
    modifiée : la tolérer cachait une falsification. L'ancre annonce donc la ligne
    avant qu'elle n'existe ; si l'ancre ne s'écrit pas, la ligne n'est pas
    écrite (``OSError`` levée, écriture comptée par l'appelant) ; si la ligne ne
    s'écrit pas, l'ancre précédente est restaurée.

    **Une divergence ne se répare pas en ajoutant.** Si l'ancre ne désigne plus
    la dernière ligne du fichier (dernière ligne retirée ou modifiée, journal
    supprimé), la ligne est ajoutée mais l'ancre reste figée : la divergence
    reste visible à chaque vérification, quel que soit le nombre d'ajouts
    ultérieurs. Ce n'est pas le hook ``PostToolUse``, qui ajoute après chaque
    outil, qui absorbe la preuve de la falsification.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path, timeout=_LOCK_TIMEOUT_S):
        last, clean_end = _tail(path)
        prev = _digest(last) if last is not None else GENESIS
        line = json.dumps({**event, "prev": prev}, ensure_ascii=False).encode("utf-8")
        state = _read_head(path)
        dropped = state.dropped if state is not None else 0
        anchored = state is not None and state.count > 0
        count = 0
        if anchored and state is not None:
            if last is not None and state.head == _digest(last):
                count = state.count
            else:
                # La dernière ligne n'est pas celle de l'ancre : soit une écriture coupée par un crash (ligne illisible
                # en queue, la chaîne tient en amont), soit une divergence. Le parcours complet tranche, rarement.
                report = analyze_chain(path)
                if report.status != "intact" or report.chained != state.count:
                    _append_bytes(path, line, clean_end)  # divergence : l'ancre reste figée
                    return
                count = report.chained
        elif last is not None:
            count = _chained_count(path)
        _write_head(path, _Head(count + 1, _digest(line), dropped), durable=False)
        try:
            _append_bytes(path, line, clean_end)
        except BaseException:
            _restore_head(path, state)
            raise


def _append_bytes(path: Path, line: bytes, clean_end: bool) -> None:
    with open(path, "ab") as handle:
        handle.write(b"" if clean_end else b"\n")
        handle.write(line + b"\n")


def _restore_head(path: Path, state: _Head | None) -> None:
    """Remet l'ancre d'avant l'ajout quand la ligne n'a pas pu s'écrire (au mieux : ne lève jamais)."""
    try:
        if state is None:
            head_path(path).unlink(missing_ok=True)
        else:
            _write_head(path, state, durable=False)
    except OSError:  # silent-ok — l'ancre reste en avance d'une ligne : analyze_chain le dit (« manquent en fin de journal »)
        return


def record_dropped_write(path: Path) -> None:
    """Compte une écriture perdue : dans l'ancre quand elle est inscriptible, dans le processus toujours. Ne lève jamais."""
    _process_drops[path] = _process_drops.get(path, 0) + 1
    try:
        with locked(path, timeout=_LOCK_TIMEOUT_S):
            state = _read_head(path) or _Head(0, "", 0)
            _write_head(path, _Head(state.count, state.head, state.dropped + 1), durable=True)
    except (OSError, ValueError, TypeError):  # silent-ok — dossier inscriptible nulle part : le compte reste en mémoire (docstring du module)
        return


def dropped_writes(path: Path) -> int:
    """Écritures perdues enregistrées dans l'ancre de *path* (0 si absente)."""
    state = _read_head(path)
    return state.dropped if state is not None else 0


@dataclass(frozen=True, slots=True)
class ChainReport:
    """Résultat de l'analyse d'un journal chaîné.

    ``status`` : ``absent`` (ni journal ni ancre), ``legacy`` (lignes sans
    chaînage seulement), ``intact`` ou ``broken``. ``break_after_line`` et
    ``break_line`` sont les numéros physiques (1-based) des deux lignes entre
    lesquelles la chaîne ne tient plus : l'une des deux a été modifiée, ou une
    ligne a été supprimée ou insérée entre elles. ``truncated`` est le nombre
    de lignes chaînées que l'ancre connaît et que le fichier n'a plus.

    ``kind`` nomme le cas d'une rupture : ``unchained_inserted`` (des lignes sans
    ``prev`` intercalées entre des lignes chaînées, et la chaîne continue autour :
    chaque ligne qui suit prolonge celle qui la précède, ce qu'écrit un hook d'une
    version antérieure du kit) ; ``truncated`` (lignes chaînées manquantes en fin de
    journal) ; ``chain_broken`` (ligne chaînée modifiée, supprimée ou insérée, ancre
    qui ne suit plus, journal illisible : toute ambiguïté). Seul le premier cas
    peut être expliqué par un humain (:mod:`grimoire.hosts.decisions.chain_ack`).
    ``inserted`` liste ces lignes avec l'empreinte de leurs voisines ;
    ``anchor_frozen`` dit que l'ancre est restée là où la première ligne non
    chaînée l'a figée (``append_chained`` ne la fait pas avancer après une rupture).
    """

    status: str
    chained: int = 0
    legacy: int = 0
    unreadable_lines: tuple[int, ...] = ()
    break_after_line: int | None = None
    break_line: int | None = None
    truncated: int = 0
    anchored: bool = False
    dropped_writes: int = 0
    message: str = ""
    kind: str = ""
    inserted: tuple[InsertedLine, ...] = ()
    anchor_frozen: bool = False


@dataclass(frozen=True, slots=True)
class InsertedLine:
    """Une ligne sans ``prev`` intercalée entre des lignes chaînées, et l'empreinte de ses voisines.

    ``prev_sha256`` / ``next_sha256`` : empreintes des lignes physiques voisines (``""`` si
    aucune ligne ne suit). ``chained_before`` : lignes chaînées qui la précèdent.
    """

    line: int
    prev_line: int
    next_line: int
    sha256: str
    prev_sha256: str
    next_sha256: str
    chained_before: int


def analyze_chain(path: Path) -> ChainReport:
    """Parcourt le journal *path* et son ancre. Ne lève jamais, n'écrit rien."""
    head = _read_head(path)
    dropped = head.dropped if head is not None else 0
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = b""
    except OSError as exc:
        return ChainReport(status="broken", message=f"journal illisible ({exc.__class__.__name__})", dropped_writes=dropped)

    legacy = 0
    unreadable: list[int] = []
    chained_hashes: list[str] = []
    chained_lines: list[int] = []
    prev_raw: bytes | None = None
    prev_line = 0
    broken: tuple[int, int, str] | None = None
    inserted: list[dict[str, Any]] = []
    for number, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip():
            continue
        if inserted and not inserted[-1]["next_line"]:
            inserted[-1]["next_line"], inserted[-1]["next_sha256"] = number, _digest(line)
        try:
            parsed = json.loads(line)
        except ValueError:  # silent-ok — ligne illisible : comptée, sa voisine dira si la chaîne tient
            parsed = None
            unreadable.append(number)
        link = parsed.get("prev") if isinstance(parsed, dict) else None
        if isinstance(link, str):
            expected = _digest(prev_raw) if prev_raw is not None else GENESIS
            if link != expected and broken is None:
                broken = (prev_line, number, "ne prolonge pas la ligne précédente")
            chained_hashes.append(_digest(line))
            chained_lines.append(number)
        elif chained_hashes:
            if parsed is not None:
                inserted.append(
                    {
                        "line": number,
                        "prev_line": prev_line,
                        "next_line": 0,
                        "sha256": _digest(line),
                        "prev_sha256": _digest(prev_raw) if prev_raw is not None else "",
                        "next_sha256": "",
                        "chained_before": len(chained_hashes),
                    }
                )
        else:
            legacy += 1
        prev_raw, prev_line = line, number

    chained = len(chained_hashes)
    truncated = 0
    frozen = False
    if broken is None and inserted and not inserted[-1]["next_line"]:
        # Rien ne prolonge la dernière ligne non chaînée : indiscernable d'une ligne ajoutée à la main en queue.
        broken = (inserted[-1]["prev_line"], inserted[-1]["line"], "n'est pas chaînée et aucune ligne ne la prolonge")
    if broken is None and inserted and head is not None:
        before = inserted[0]["chained_before"]
        frozen = head.count == before and chained_hashes[before - 1] == head.head
    if broken is None and head is not None and not frozen:
        if head.count > chained:
            truncated = head.count - chained
            broken = (chained_lines[-1] if chained_lines else 0, 0, "")
        elif head.count > 0 and chained_hashes[head.count - 1] != head.head:
            where = chained_lines[head.count - 1]
            broken = (where, where, "ne correspond plus à l'ancre du journal")
        elif 0 < head.count < chained:
            # L'ancre est écrite avant la ligne (append_chained) : une ancre en retard ne vient plus d'un crash.
            where = chained_lines[chained - 1]
            broken = (where, where, f"n'est pas annoncée par l'ancre (elle connaît {head.count} ligne(s) chaînée(s), le fichier {chained})")

    if broken is None and inserted:
        lines = tuple(InsertedLine(**item) for item in inserted)
        numbers = ", ".join(str(item.line) for item in lines)
        message = (
            f"ligne(s) {numbers} sans `prev` intercalée(s) entre des lignes chaînées, la chaîne continue autour "
            "(chaque ligne suivante prolonge la précédente) : écriture d'un écrivain sans chaînage, par exemple une "
            "version antérieure du kit ; ce n'est ni une ligne chaînée modifiée ni une suppression."
        )
        return ChainReport(
            "broken", chained, legacy, tuple(unreadable), lines[0].prev_line, lines[0].line, 0, head is not None,
            dropped, message, "unchained_inserted", lines, frozen,
        )
    if broken is not None:
        after, line_no, why = broken
        if truncated:
            message = (
                f"{truncated} ligne(s) chaînée(s) manquent en fin de journal "
                f"(l'ancre en connaît {chained + truncated}, le fichier {chained}) : suppression ou troncature."
            )
        elif after == line_no:
            message = f"ligne {line_no} modifiée : elle {why}."
        else:
            message = (
                f"la ligne {line_no} {why} : la ligne {after} a été modifiée, "
                f"ou une ligne a été supprimée ou insérée entre les lignes {after} et {line_no}."
            )
        return ChainReport(
            "broken", chained, legacy, tuple(unreadable), after, line_no or None, truncated, head is not None, dropped, message,
            "truncated" if truncated else "chain_broken",
        )
    if raw == b"" and head is None:
        return ChainReport(status="absent", dropped_writes=dropped)
    if chained == 0 and (head is None or head.count == 0):
        return ChainReport("legacy", 0, legacy, tuple(unreadable), anchored=head is not None, dropped_writes=dropped)
    return ChainReport("intact", chained, legacy, tuple(unreadable), anchored=head is not None, dropped_writes=dropped)


def _verify_inserted_lines(root: Path, task_id: str, report: ChainReport, relpath: Path, result: StandardVerificationResult) -> None:
    """Lignes non chaînées intercalées : expliquées par un humain (avertissement) ou non (erreur). Jamais masquées."""
    from grimoire.core.standard_checks.base import _add_check
    from grimoire.hosts.decisions.chain_ack import covering_ack, explain_phrase, read_acks

    acks = read_acks(root, task_id)
    open_lines = []
    for item in report.inserted:
        ack = covering_ack(acks, task_id, item)
        if ack is None:
            open_lines.append(item.line)
            continue
        raw_author = ack.get("author")
        author: dict[str, Any] = raw_author if isinstance(raw_author, dict) else {}
        who = ", ".join(f"{key} {author[key]}" for key in ("user", "host", "session_id") if author.get(key)) or "auteur inconnu"
        _add_check(
            result,
            "evidence.chain_break_explained",
            "warning",
            f"Rupture du journal de preuve expliquée par un humain, ligne {item.line} : ligne sans `prev` intercalée entre "
            f"des lignes chaînées (la chaîne continue autour). Raison : « {ack.get('reason', '')} » — {who}, le "
            f"{ack.get('ts', '?')} ({ack.get('ack_id', '?')}). La ligne reste dans le journal : explication, pas effacement.",
            path=relpath,
        )
    if open_lines:
        first = open_lines[0]
        _add_check(
            result,
            "evidence.chain_broken",
            "error",
            f"Journal de preuve rompu : ligne(s) {', '.join(map(str, open_lines))} sans `prev` intercalée(s) entre des lignes "
            "chaînées, la chaîne continue autour (écriture d'un écrivain sans chaînage, par exemple une version antérieure du "
            "kit). Ne rechaîne ni ne réécris le journal. Si l'origine est connue, l'humain l'explique en tapant dans son "
            f"prompt `{explain_phrase(task_id, first)}` (`grimoire evidence chain explain --task-id {task_id} --line {first}`) ; "
            "tant que la rupture n'est pas expliquée, le journal n'est pas une preuve d'inventaire.",
            path=relpath,
        )
    if report.anchor_frozen:
        _add_check(
            result,
            "evidence.chain_anchor_frozen",
            "warning",
            "L'ancre du journal (evidence-log.head.json) est restée figée à la première ligne non chaînée : les lignes "
            "suivantes ne sont pas ancrées, la suppression de la dernière ligne ne serait pas détectée.",
            path=relpath,
        )


def verify_evidence_chain(root: Path, task_id: str, result: StandardVerificationResult) -> None:
    """Ajoute à *result* les constats du chaînage du journal de *task_id* (lecture seule).

    Une chaîne rompue est une erreur (``standard verify`` échoue) ; une écriture
    perdue, une ligne illisible ou une chaîne sans ancre sont des avertissements.
    """
    from grimoire.core.standard_checks.base import _add_check
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath

    relpath = evidence_log_relpath(task_id)
    report = analyze_chain(root / relpath)
    process = process_dropped_writes(root / relpath)
    if report.status == "broken" and report.kind == "unchained_inserted":
        _verify_inserted_lines(root, task_id, report, relpath, result)
    elif report.status == "broken":
        _add_check(
            result,
            "evidence.chain_broken",
            "error",
            f"Journal de preuve rompu : {report.message} Rejoue l'action concernée puis relance la vérification ; "
            "le journal n'est plus une preuve d'inventaire tant que la rupture n'est pas expliquée.",
            path=relpath,
        )
    dropped = max(report.dropped_writes, process)
    if dropped:
        _add_check(
            result,
            "evidence.writes_dropped",
            "warning",
            f"{dropped} écriture(s) du journal de preuve perdue(s) (erreur d'écriture) : "
            "l'inventaire observé est incomplet, ce n'est pas la preuve d'une absence d'action.",
            path=relpath,
        )
    if report.status == "intact" and report.unreadable_lines:
        _add_check(
            result,
            "evidence.chain_unreadable_lines",
            "warning",
            f"Ligne(s) {', '.join(map(str, report.unreadable_lines))} du journal de preuve illisible(s) "
            "(écriture coupée ?) : chaîne intacte autour, événements perdus.",
            path=relpath,
        )
    if report.status == "intact" and report.chained and not report.anchored:
        _add_check(
            result,
            "evidence.chain_unanchored",
            "warning",
            "Journal de preuve chaîné sans ancre (evidence-log.head.json absent ou illisible) : "
            "une suppression de la dernière ligne ne serait pas détectée.",
            path=relpath,
        )
