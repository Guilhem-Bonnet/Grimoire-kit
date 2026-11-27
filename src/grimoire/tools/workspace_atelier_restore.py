"""Atelier de spec : comparer deux versions d'un chantier, et restaurer une phase (épique #1197).

Prolonge :mod:`grimoire.tools.workspace_atelier_history` (historique en lecture seule) et lui emprunte TOUTES ses aides
git durcies (``_git``, ``_blob``, ``_resolve``, ``_phases``…) : aucune commande git n'est lancée d'ici autrement que par
elles, et aucune ne peut écrire (``show``, ``ls-tree``, ``log``, ``rev-parse`` seulement). Rien n'est jamais commité par le kit.

Routes (préfixe ``/api/workspace/atelier/chantiers/<slug>/``, branchées par :mod:`grimoire.tools.workspace_atelier`) :

- ``GET compare?from=<id>&to=<id>`` : :func:`compare`, le diff par phase entre deux versions du chantier. ``<id>`` est un
  identifiant de la liste des versions, ou ``working`` (le fichier sur disque). Mêmes validations et mêmes bornes que
  ``history/<id>`` : l'identifiant est retrouvé dans la liste des commits du chantier, seul le condensat complet va à git.
  Quand ``to`` est ``working`` et ``from`` un commit, chaque phase qui diffère dit si elle se restaure (``restorable``,
  ``refusal``, ``file_fp``) et liste ses blocs restaurables un à un (``blocks``, par la route d'édition d'un bloc).
- ``POST restore`` ``{"phase", "expected", "version"}`` ou ``{"phase", "expected", "undo"}`` : :func:`apply`, remplace le
  fichier d'UNE phase par celui de la version choisie (``version``), ou par le contenu d'avant une restauration (``undo``,
  jeton rendu par la restauration, gardé en mémoire du processus). Le contenu ne vient JAMAIS du client : il vient de
  ``git show`` (aide durcie) ou de la mémoire du serveur.

Écriture (la seule de ce module) : drapeau de l'Atelier, chantier et chemin retrouvés dans la liste réelle (aucun lien
symbolique, confinement), verrou du fichier, contrôle de conflit par empreinte du fichier lu (``expected``, 409), refus
si le document est au statut accepté (ou si la version à restaurer l'est : une restauration ne prononce jamais une
acceptation), refus si une exigence validée par la MOA serait modifiée ou retirée, écriture atomique, fils de
commentaires resynchronisés. L'écriture est une modification non commitée.
"""

from __future__ import annotations

import difflib
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from grimoire.core.cadrage import PHASES
from grimoire.tools import atelier_blocs, atelier_conflict
from grimoire.tools import workspace_atelier_history as h

__all__ = ["apply", "compare"]

#: Au plus ce nombre de blocs par côté pour proposer « Restaurer ce bloc » (au-delà : la phase seule se restaure).
MAX_BLOCKS = 400
MAX_PAIRS = 200
#: Restaurations qu'on peut encore annuler (en mémoire du processus) et leur durée de vie.
UNDO_KEEP = 16
UNDO_TTL = 3600.0
_UNDO: OrderedDict[str, tuple[str, str, str, bytes, float]] = OrderedDict()
_UNDO_LOCK = threading.Lock()


# ── Comparer ─────────────────────────────────────────────────────────────────────────────────────────────


def _label(root: Path, sha: str) -> dict[str, Any]:
    if sha == h.WORKING:
        return {"id": h.WORKING, "short": "", "kind": "working", "date": None, "author": "", "subject": "Version actuelle"}
    return {"id": sha, "short": sha[:7], "kind": "commit", **h._meta(root, sha)}


def _text_at(root: Path, ident: str, phase: h._Phase) -> tuple[str | None, bool]:
    if ident == h.WORKING:
        return h._disk(phase.path)
    return h._blob(root, ident, phase.rel)


def _pairs(phase_id: str, old: str, new: str) -> list[dict[str, Any]]:
    """Les blocs de la version actuelle (``new``) qui diffèrent d'UN bloc de même rang et de même type de la version
    ancienne (``old``) : ceux que la route d'édition d'un bloc peut réécrire, avec les empreintes que cette route exige."""
    from grimoire.tools import workspace_atelier as wa

    older, newer = atelier_blocs.split(phase_id, old), atelier_blocs.split(phase_id, new)
    if len(older) > MAX_BLOCKS or len(newer) > MAX_BLOCKS:
        return []
    marks = atelier_conflict.neighbour_fields(newer)
    matcher = difflib.SequenceMatcher(None, [b.text for b in older], [b.text for b in newer], autojunk=False)
    out: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or i2 - i1 != j2 - j1:
            continue  # un bloc ajouté ou retiré se rétablit par la phase, pas bloc par bloc
        for before, k in zip(older[i1:i2], range(j1, j2), strict=True):
            now = newer[k]
            if not (now.editable and before.editable and before.kind == now.kind):
                continue
            if not before.text.strip() or len(before.text) > wa.MAX_BLOCK_CHARS or len(now.text) > wa.MAX_BLOCK_CHARS:
                continue
            try:
                wa._refuse_structure(before.text.replace("\r\n", "\n"))
            except ValueError:
                continue
            out.append({"id": now.id, "kind": now.kind, "line": new.count("\n", 0, now.start) + 1, "old_text": before.text,
                        "text": now.text, **marks[k]})
            if len(out) >= MAX_PAIRS:
                return out
    return out


def _restore_info(root: Path, slug: str, phase: h._Phase, sha: str, old: str | None, new: str | None, cut: bool) -> dict[str, Any]:
    """Ce que la restauration de cette phase vers la version ``sha`` dirait aujourd'hui (lecture seule)."""
    info: dict[str, Any] = {"restorable": False, "restore_note": "", "refusal": "", "file_fp": "", "blocks": []}
    if cut:
        info["restore_note"] = "fichier trop volumineux : restauration impossible d'ici."
        return info
    if old is None or new is None:
        info["restore_note"] = "fichier binaire : restauration impossible d'ici."
        return info
    listed = h._git(root, ["ls-tree", "--name-only", sha, "--", phase.rel])
    if listed.failed:
        raise h._BudgetError
    if listed.code != 0 or not listed.data.strip():
        info["restore_note"] = "cette version n'a pas ce fichier : rien à restaurer."
        return info
    try:
        current = phase.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        info["restore_note"] = "le fichier actuel est absent ou n'est pas de l'UTF-8 : restauration impossible d'ici."
        return info
    info["file_fp"] = atelier_blocs.fingerprint(current)
    info["blocks"] = _pairs(phase.id, old, new)
    reason = _refusal(root, slug, phase, current, old)
    info["refusal"] = reason
    info["restorable"] = not reason
    return info


def _compare(root: Path, slug: str, frm: str, to: str) -> dict[str, Any]:
    phases = h._phases(root, slug)
    reason = h._repo_state(root)
    if reason:
        raise FileNotFoundError(h.REASONS.get(reason, "version introuvable"))
    first, second = h._resolve(root, phases, frm), h._resolve(root, phases, to)
    out: dict[str, Any] = {"from": _label(root, first), "to": _label(root, second), "phases": [], "truncated": False,
                           "restorable": second == h.WORKING and first != h.WORKING}
    budget = h.MAX_ROWS
    for phase in phases:
        try:
            old, old_cut = _text_at(root, first, phase)
            new, new_cut = _text_at(root, second, phase)
            shown = h._phase_diff(phase, old, new, old_cut or new_cut, max(budget, 0))
            if shown is not None and out["restorable"]:
                shown.update(_restore_info(root, slug, phase, first, old, new, old_cut or new_cut))
        except h._BudgetError:
            out.update({"truncated": True, "reason": "delai", "reason_text": "git a pris trop de temps : la comparaison est incomplète."})
            break
        except h._UnreadableError:
            out["phases"].append({"id": phase.id, "title": phase.title, "added": 0, "removed": 0, "rows": [], "truncated": True,
                                  "note": "contenu indisponible dans ce dépôt (objet absent) : diff non calculé."})
            out["truncated"] = True
            continue
        if shown is None:
            continue
        budget -= len(shown["rows"])
        out["truncated"] = out["truncated"] or bool(shown["truncated"]) or (budget <= 0 and bool(shown["rows"]))
        out["phases"].append(shown)
    out["added"] = sum(p["added"] for p in out["phases"])
    out["removed"] = sum(p["removed"] for p in out["phases"])
    return out


def compare(root: Path, slug: str, frm: str, to: str) -> dict[str, Any]:
    """Le diff, phase par phase, entre deux versions du chantier (``working`` : le fichier sur disque). Lecture seule."""
    if not frm or not to:
        raise ValueError("`from` et `to` requis : deux identifiants de version, ou `working`")
    return h._within_budget(_compare, root, slug, frm, to)


# ── Restaurer ────────────────────────────────────────────────────────────────────────────────────────────


def _accepted(text: str) -> bool:
    """Le front matter du fichier porte-t-il un statut accepté/validé (même règle que ``cdc_accepted``) ?"""
    from grimoire.core.cadrage_requirements import _ACCEPTED_STATUSES, _fold

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:  # toute clé `status` compte, pas seulement la première (règle de l'entretien du cadrage)
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        if key.strip() == "status" and _fold(value).strip().strip("'\"") in _ACCEPTED_STATUSES:
            return True
    return False


def _protected_requirements(root: Path, slug: str, phases: list[h._Phase], phase: h._Phase, candidate: str) -> str:
    """Le refus si ``candidate`` modifierait ou retirerait une exigence que la MOA a validée, sinon ``""``.

    Échec fermé, comme le gate des exigences (« corrompre la MOA ne désarme pas le gate ») : une gouvernance mal formée,
    un journal illisible refusent ; une exigence scellée dans le journal d'autorité reste protégée même si la MOA n'est
    plus déclarée ou a changé (l'empreinte scellée compte, pas la MOA d'aujourd'hui). Les fichiers viennent de la liste
    réelle des phases du chantier, jamais d'un chemin recomposé.
    """
    from grimoire.core.cadrage_requirements import REQUIREMENT_FILES, parse_requirements
    from grimoire.missions.mandate import AuthorityJournal, JournalError
    from grimoire.missions.schemas import ORIGIN_HUMAN
    from grimoire.missions.requirement_gate import (
        APPROVE_CHANNEL,
        VALIDATED_ENTRY,
        fingerprint,
        gate_mode,
        normalize_key,
        origin_root,
    )

    filename = Path(phase.rel).name
    if filename not in REQUIREMENT_FILES:
        return ""
    name = None if slug == "principal" else slug
    texts = {Path(p.rel).name: p.path.read_text(encoding="utf-8", errors="replace")
             for p in phases if Path(p.rel).name in REQUIREMENT_FILES and p.path.is_file()}
    before = parse_requirements(texts)
    texts[filename] = candidate
    after = parse_requirements(texts).by_id()
    project = origin_root(root)
    mode = gate_mode(project)
    if mode.problem:
        return f"la gouvernance du projet est illisible ou mal formée ({mode.problem}) : impossible de savoir quelles exigences sont validées, rien n'est restauré"
    if not before.requirements:
        return ""
    try:
        entries = AuthorityJournal().read()
    except JournalError:
        return "le journal d'autorité est illisible : impossible de savoir quelles exigences sont validées, rien n'est restauré"
    sealed = {(e.get("requirement"), e.get("fingerprint")) for e in entries
              if e.get("type") == VALIDATED_ENTRY and e.get("project") == str(project)
              and e.get("origin") == ORIGIN_HUMAN and e.get("channel") == APPROVE_CHANNEL}
    for requirement in before.requirements:
        key = normalize_key(requirement.id, name)
        now = fingerprint(key, requirement)
        if (key, now) not in sealed:
            continue
        kept = after.get(requirement.id)
        if kept is None or fingerprint(key, kept) != now:
            return f"l'exigence {key} est validée par la MOA : la restauration la modifierait. Elle se rouvre par la MOA, pas d'ici"
    return ""


def _empty_version(phase_id: str, text: str) -> str:
    """Le refus si la version ne contient rien (la restaurer viderait la phase), sinon ``""``."""
    if not text.strip() or not atelier_blocs.split(phase_id, text):
        return "la version choisie est vide : la restaurer viderait la phase."
    return ""


def _refusal(root: Path, slug: str, phase: h._Phase, current: str, candidate: str) -> str:
    if _accepted(current):
        return "le document est au statut accepté : il ne se modifie plus d'ici."
    if _accepted(candidate):
        return "cette version porte le statut accepté : une restauration ne prononce jamais une acceptation."
    empty = _empty_version(phase.id, candidate)
    if empty:
        return empty
    return _protected_requirements(root, slug, h._phases(root, slug), phase, candidate)


def _fetch(root: Path, slug: str, version: str, phase_id: str) -> dict[str, Any]:
    """Le contenu d'une phase à une version, tel que git le stocke (octets), par les aides durcies de l'historique."""
    phases = h._phases(root, slug)
    phase = next((p for p in phases if p.id == phase_id), None)
    if phase is None:
        raise FileNotFoundError(f"phase inconnue : {phase_id!r}")
    reason = h._repo_state(root)
    if reason:
        raise FileNotFoundError(h.REASONS.get(reason, "version introuvable"))
    sha = h._resolve(root, phases, version)
    if sha == h.WORKING:
        raise ValueError("`version` doit être un commit : le fichier sur disque est déjà la version actuelle")
    try:
        listed = h._git(root, ["ls-tree", "--name-only", sha, "--", phase.rel])
        if listed.failed:
            raise h._BudgetError
        if listed.code != 0 or not listed.data.strip():
            raise ValueError("cette version n'a pas ce fichier : rien à restaurer")
        done = h._git(root, ["show", f"{sha}:./{phase.rel}"], limit=h.MAX_BLOB)
        if done.failed:
            raise h._BudgetError
    except h._BudgetError as exc:
        raise ValueError("git a pris trop de temps : réessaie dans un instant") from exc
    if done.truncated:
        raise ValueError("fichier trop volumineux : restauration impossible d'ici")
    if done.code != 0:
        raise ValueError("contenu indisponible dans ce dépôt : rien n'est restauré")
    if b"\x00" in done.data:
        raise ValueError("fichier binaire : restauration impossible d'ici")
    try:
        done.data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("la version n'est pas de l'UTF-8 : restauration impossible d'ici") from exc
    return {"data": done.data, "sha": sha}


def _remember(root: Path, slug: str, phase_id: str, previous: bytes) -> str:
    token = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _UNDO_LOCK:
        for key in [k for k, v in _UNDO.items() if now - v[4] > UNDO_TTL]:
            del _UNDO[key]
        _UNDO[token] = (str(root), slug, phase_id, previous, now)
        while len(_UNDO) > UNDO_KEEP:
            _UNDO.popitem(last=False)
    return token


def _recall(root: Path, slug: str, phase_id: str, token: Any) -> bytes:
    if not isinstance(token, str) or not token:
        raise ValueError("`undo` doit être le jeton rendu par la restauration")
    with _UNDO_LOCK:
        held = _UNDO.get(token)
    if held is None or time.monotonic() - held[4] > UNDO_TTL or held[:3] != (str(root), slug, phase_id):
        raise FileNotFoundError("l'annulation n'est plus disponible (session redémarrée ou trop ancienne)")
    return held[3]


def apply(root: Path, slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Remplace le fichier d'UNE phase par celui d'une version (ou par le contenu d'avant une restauration). Voir le module."""
    from grimoire.core.fsio import atomic_write, locked
    from grimoire.tools import workspace_atelier as wa

    slug = wa._slug(slug)
    phase_id, expected = body.get("phase"), body.get("expected")
    if not isinstance(phase_id, str) or not phase_id:
        raise ValueError("`phase` requis")
    if not isinstance(expected, str) or not expected:
        raise ValueError("`expected` requis : l'empreinte du fichier de phase lue avec le chantier")
    version, undo = body.get("version"), body.get("undo")
    if (version is None) == (undo is None):
        raise ValueError("`version` ou `undo` (l'un des deux, pas les deux)")
    core = next((p for p in PHASES if p.id == phase_id), None)
    if core is None:
        raise FileNotFoundError(f"phase inconnue : {phase_id!r}")
    phase = next((p for p in h._phases(root, slug) if p.id == phase_id), None)
    if phase is None or not phase.path.is_file():
        raise FileNotFoundError(f"phase absente : {core.filename}")
    if version is not None:
        if not isinstance(version, str):
            raise ValueError("`version` doit être une chaîne")
        candidate = h._within_budget(_fetch, root, slug, version, phase_id)["data"]
    else:
        candidate = _recall(root, slug, phase_id, undo)
    text = candidate.decode("utf-8")
    with locked(phase.path):
        try:
            current_bytes = phase.path.read_bytes()
            current = current_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{core.filename} n'est pas de l'UTF-8 : modifiez-le hors du cockpit") from exc
        if atelier_blocs.fingerprint(current) != expected:
            raise wa.ConflictError(f"{core.filename} a changé depuis la lecture : rien n'est restauré", reason="fichier-modifie")
        empty = _empty_version(phase.id, text)
        if empty:
            raise wa.ConflictError(empty, reason="version-vide")
        refused = _refusal(root, slug, phase, current, text)
        if refused:
            raise PermissionError(refused)
        if undo is None and len(current_bytes) > h.MAX_BLOB:
            raise ValueError("fichier trop gros pour être restauré avec annulation : modifie-le hors du cockpit")
        if current_bytes == candidate:
            written = False
        else:
            if undo is not None:  # le jeton se consomme : une seule annulation, jamais deux
                with _UNDO_LOCK:
                    if _UNDO.pop(undo, None) is None:
                        raise FileNotFoundError("l'annulation n'est plus disponible (déjà faite, session redémarrée ou trop ancienne)")
            atomic_write(phase.path, candidate)
            written = True
    if not written:
        view = wa._phase_view(root, None if slug == "principal" else slug, core)
        return {"ok": True, "already": True, "unchanged": True, "committed": False, "phase": view, "added": 0, "removed": 0}
    from grimoire.tools import workspace_atelier_comments as comments

    comments.resync(root, slug)  # les fils retrouvent leur bloc (ou deviennent orphelins), hors du verrou du fichier
    token = "" if undo is not None else _remember(root, slug, phase_id, current_bytes)
    stats = h.line_diff(current, text, 0)
    view = wa._phase_view(root, None if slug == "principal" else slug, core)
    return {"ok": True, "unchanged": False, "committed": False, "phase": view, "undo": token, "added": stats["added"],
            "removed": stats["removed"]}
