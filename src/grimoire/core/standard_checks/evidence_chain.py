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
from typing import Any

from grimoire.core.fsio import atomic_write, locked
from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check

__all__ = [
    "GENESIS",
    "HEAD_SUFFIX",
    "ChainReport",
    "analyze_chain",
    "append_chained",
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
    binaire : pas de ``\\r\\n`` fabriqué par la plate-forme). Un échec d'écriture
    de l'ancre après l'ajout est toléré : l'ancre retarde, la vérification
    l'accepte et le prochain ajout recompte.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path, timeout=_LOCK_TIMEOUT_S):
        last, clean_end = _tail(path)
        prev = _digest(last) if last is not None else GENESIS
        line = json.dumps({**event, "prev": prev}, ensure_ascii=False).encode("utf-8")
        state = _read_head(path)
        if state is not None and last is not None and state.head == _digest(last) and state.count > 0:
            count = state.count
        elif last is None:
            count = 0
        else:
            count = _chained_count(path)
        dropped = state.dropped if state is not None else 0
        with open(path, "ab") as handle:
            handle.write(b"" if clean_end else b"\n")
            handle.write(line + b"\n")
        try:
            _write_head(path, _Head(count + 1, _digest(line), dropped), durable=False)
        except OSError:  # silent-ok — l'ancre retarde, la vérification l'accepte et le prochain ajout recompte (docstring)
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
    for number, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip():
            continue
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
            if parsed is not None and broken is None:
                broken = (prev_line, number, "n'est pas chaînée alors qu'elle suit une ligne chaînée")
        else:
            legacy += 1
        prev_raw, prev_line = line, number

    chained = len(chained_hashes)
    truncated = 0
    if broken is None and head is not None:
        if head.count > chained:
            truncated = head.count - chained
            broken = (chained_lines[-1] if chained_lines else 0, 0, "")
        elif head.count > 0 and chained_hashes[head.count - 1] != head.head:
            where = chained_lines[head.count - 1]
            broken = (where, where, "ne correspond plus à l'ancre du journal")

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
            "broken", chained, legacy, tuple(unreadable), after, line_no or None, truncated, head is not None, dropped, message
        )
    if raw == b"" and head is None:
        return ChainReport(status="absent", dropped_writes=dropped)
    if chained == 0 and (head is None or head.count == 0):
        return ChainReport("legacy", 0, legacy, tuple(unreadable), anchored=head is not None, dropped_writes=dropped)
    return ChainReport("intact", chained, legacy, tuple(unreadable), anchored=head is not None, dropped_writes=dropped)


def verify_evidence_chain(root: Path, task_id: str, result: StandardVerificationResult) -> None:
    """Ajoute à *result* les constats du chaînage du journal de *task_id* (lecture seule).

    Une chaîne rompue est une erreur (``standard verify`` échoue) ; une écriture
    perdue, une ligne illisible ou une chaîne sans ancre sont des avertissements.
    """
    from grimoire.core.standard_checks.evidence_journal import evidence_log_relpath

    relpath = evidence_log_relpath(task_id)
    report = analyze_chain(root / relpath)
    process = process_dropped_writes(root / relpath)
    if report.status == "broken":
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
