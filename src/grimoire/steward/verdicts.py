"""Validation par du code d'un fichier de verdicts (``grimoire steward verdicts``), schéma ``grimoire-steward-verdicts/v1``.

Un agent lit la carte et juge chaque élément ; ce module ne juge pas, il **vérifie la preuve** :

- ``commit`` : le SHA existe et il est sur la cible (``git merge-base --is-ancestor``) ;
- ``fichier`` : le chemin (et le motif s'il est donné) est sur la cible — ou n'y est pas, avec ``absent`` ;
- ``issue`` / ``pr`` : l'état lu par ``gh`` (fermée, fusionnée — et, pour une PR fusionnée, son commit
  de fusion sur la cible) ;
- ``inactivite`` : la dernière activité et la vivacité des sessions **recalculées**, jamais recopiées ;
- ``doublon_de`` : l'autre élément existe ;
- ``note`` : du texte libre, qui seul ne suffit jamais pour ``livree``, ``obsolete``, ``doublon``
  ou ``abandonnee``.

Un verdict dont une preuve ne se vérifie pas, ou dont les preuves ne suffisent pas, est **rejeté** avec
sa raison et n'apparaît pas comme établi. Rien n'est écrit au ledger, au dépôt ni sur GitHub.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from grimoire.missions.lease import parse_instant
from grimoire.steward import VALIDATION_SCHEMA, VERDICTS_SCHEMA
from grimoire.steward.project_map import LedgerView, owners_of_branch, read_ledger, session_alive
from grimoire.steward.sources import (
    NON_MESURE,
    Sources,
    commit_on_target,
    file_on_target,
    gh_json,
    hours_since,
    last_commit_at,
    list_worktrees,
    merge_probe,
    resolve_commit,
    scratch_objects,
)

__all__ = ["KINDS", "PROOF_TYPES", "VERDICTS", "VerdictsFileError", "load_verdicts", "validate"]

VERDICTS = (
    "a_faire",
    "livree",
    "obsolete",
    "doublon",
    "abandonnee",
    "conflit_simple",
    "conflit_semantique",
    "humain",
)
PROOF_TYPES = ("commit", "fichier", "issue", "pr", "inactivite", "doublon_de", "note")
KINDS = ("tache", "branche", "pr", "issue", "worktree")

_ISSUE_STATES = {"OPEN": "ouverte", "CLOSED": "fermee", "MERGED": "fermee"}
_PR_STATES = {"OPEN": "ouverte", "CLOSED": "fermee", "MERGED": "fusionnee"}
_REF = re.compile(r"(?P<kind>[a-z]+):(?P<id>.+)")


class VerdictsFileError(ValueError):
    """Le fichier de verdicts est illisible ou n'est pas du schéma attendu."""


def load_verdicts(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise VerdictsFileError(f"{path} illisible : {exc}") from exc
    if not isinstance(data, dict):
        raise VerdictsFileError("un objet JSON est attendu")
    if data.get("$schema") != VERDICTS_SCHEMA:
        raise VerdictsFileError(f"« $schema » doit valoir « {VERDICTS_SCHEMA} » (lu : {data.get('$schema')!r})")
    if not isinstance(data.get("verdicts"), list):
        raise VerdictsFileError("« verdicts » doit être une liste")
    return data


@dataclass
class _Checker:
    src: Sources
    target_sha: str = ""
    target_tree: str = ""
    env: Mapping[str, str] | None = None
    _view: tuple[LedgerView | None, str] | None = None
    _gh: dict[tuple[str, ...], tuple[Any, str]] = field(default_factory=dict)

    # ── lectures mises en cache ──

    @property
    def view(self) -> LedgerView | None:
        if self._view is None:
            self._view = read_ledger(self.src)
        return self._view[0]

    def gh(self, *args: str) -> tuple[Any, str]:
        if args not in self._gh:
            self._gh[args] = gh_json(self.src, *args)
        return self._gh[args]

    def pr(self, number: int) -> tuple[dict[str, Any] | None, str]:
        fields = "number,state,title,updatedAt,headRefName,headRefOid,mergedAt,mergeCommit"
        data, reason = self.gh("pr", "view", str(number), "--json", fields)
        return (data, "") if isinstance(data, dict) else (None, reason or f"PR #{number} illisible")

    def issue(self, number: int) -> tuple[dict[str, Any] | None, str]:
        data, reason = self.gh("issue", "view", str(number), "--json", "number,state,closedAt,updatedAt,title")
        return (data, "") if isinstance(data, dict) else (None, reason or f"issue #{number} illisible")

    def branch_sha(self, name: str) -> str | None:
        return resolve_commit(self.src, f"refs/heads/{name}") or resolve_commit(self.src, f"refs/remotes/{name}")

    # ── éléments ──

    def element(self, ref: str) -> tuple[bool, str]:
        """``(existe, raison)`` — une existence qu'on ne peut pas mesurer est un refus (échec fermé)."""
        match = _REF.fullmatch(ref)
        if not match or match["kind"] not in KINDS:
            return False, f"élément « {ref} » mal formé (attendu <{'|'.join(KINDS)}>:<identifiant>)"
        kind, ident = match["kind"], match["id"]
        if kind == "tache":
            if self.view is None:
                return False, f"ledger non mesuré ({self._view[1] if self._view else NON_MESURE})"
            return (ident in self.view.tasks, f"tâche {ident} absente du ledger")
        if kind == "branche":
            return (self.branch_sha(ident) is not None, f"branche {ident} introuvable")
        if kind == "worktree":
            entries = list_worktrees(self.src)
            if entries is None:
                return False, "git worktree list a échoué"
            return (any(Path(e.path) == Path(ident) for e in entries), f"worktree {ident} introuvable")
        if not ident.isdigit():
            return False, f"numéro « {ident} » invalide"
        found, reason = self.pr(int(ident)) if kind == "pr" else self.issue(int(ident))
        return (found is not None, reason)

    # ── preuves ──

    def check(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        kind = proof.get("type")
        handler = getattr(self, f"_proof_{kind}", None) if kind in PROOF_TYPES else None
        if handler is None:
            return False, f"type de preuve inconnu « {kind} » (attendu : {', '.join(PROOF_TYPES)})", {}
        result: tuple[bool, str, dict[str, Any]] = handler(ref, proof)
        return result

    def _need_target(self) -> str:
        return "" if self.target_sha else f"cible {self.src.target} introuvable : preuve non vérifiable"

    def _proof_commit(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        if missing := self._need_target():
            return False, missing, {}
        ok, full, reason = commit_on_target(self.src, self.target_sha, str(proof.get("sha") or ""))
        observed: dict[str, Any] = {"sha": full} if full else {}
        expected = str(proof.get("message_contient") or "")
        if ok and expected:
            message = self.src.git("log", "-1", "--format=%B", full).stdout
            if expected not in message:
                return False, f"le message de {full[:12]} ne contient pas « {expected} »", observed
        return ok, reason or f"commit {full[:12]} sur la cible", observed

    def _proof_fichier(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        if missing := self._need_target():
            return False, missing, {}
        path = str(proof.get("chemin") or "")
        parts = PurePosixPath(path).parts
        if not path or path.startswith(("/", "-")) or "\\" in path or ".." in parts or ":" in path:
            return False, f"chemin « {path} » invalide (relatif, en /, sans ..)", {}
        pattern = str(proof.get("motif") or "")
        absent = proof.get("absent") is True
        present, detail = file_on_target(self.src, self.target_sha, path, pattern)
        if present is None:
            return False, f"{detail} ({NON_MESURE})", {}
        observed = {"present": present}
        return present is not absent, detail if present is not absent else f"attendu {'absent' if absent else 'présent'} : {detail}", observed

    def _proof_issue(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        number, expected = proof.get("numero"), str(proof.get("etat") or "fermee")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0 or expected not in ("fermee", "ouverte"):
            return False, "preuve issue : « numero » entier et « etat » fermee|ouverte attendus", {}
        data, reason = self.issue(number)
        if data is None:
            return False, f"{reason} ({NON_MESURE})", {}
        actual = _ISSUE_STATES.get(str(data.get("state") or ""), str(data.get("state") or ""))
        observed = {"etat": actual, "fermee_le": data.get("closedAt")}
        return actual == expected, f"#{number} est {actual}" + ("" if actual == expected else f", attendu {expected}"), observed

    def _proof_pr(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        number, expected = proof.get("numero"), str(proof.get("etat") or "fusionnee")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0 or expected not in _PR_STATES.values():
            return False, "preuve pr : « numero » entier et « etat » fusionnee|fermee|ouverte attendus", {}
        data, reason = self.pr(number)
        if data is None:
            return False, f"{reason} ({NON_MESURE})", {}
        actual = _PR_STATES.get(str(data.get("state") or ""), str(data.get("state") or ""))
        merge = data.get("mergeCommit") if isinstance(data.get("mergeCommit"), dict) else {}
        observed = {"etat": actual, "fusionnee_le": data.get("mergedAt"), "commit_de_fusion": (merge or {}).get("oid")}
        if actual != expected:
            return False, f"PR #{number} est {actual}, attendu {expected}", observed
        if actual == "fusionnee":
            if missing := self._need_target():
                return False, missing, observed
            ok, _full, why = commit_on_target(self.src, self.target_sha, str((merge or {}).get("oid") or ""))
            if not ok:
                return False, f"PR #{number} fusionnée, mais son commit de fusion n'est pas vérifié sur la cible : {why}", observed
        return True, f"PR #{number} est {actual}", observed

    def _proof_inactivite(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        threshold = proof.get("heures")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
            return False, "preuve inactivite : « heures » (nombre > 0) attendu", {}
        if self.view is None:
            return False, "vivacité non mesurée : ledger illisible ou absent", {}
        kind, _, ident = ref.partition(":")
        if kind == "tache":
            last, owners = parse_instant(self.view.last_activity.get(ident)), [ident]
        elif kind == "pr" and ident.isdigit():
            data, reason = self.pr(int(ident))
            if data is None:
                return False, f"{reason} ({NON_MESURE})", {}
            last = parse_instant(data.get("updatedAt"))
            owners = owners_of_branch(self.view, str(data.get("headRefName") or ""), str(data.get("title") or ""), int(ident))
        elif kind == "branche":
            last, owners = last_commit_at(self.src, self.branch_sha(ident) or ident), owners_of_branch(self.view, ident)
        else:
            return False, f"inactivité non mesurable pour un élément « {kind} »", {}
        hours = hours_since(last, self.src.now)
        alive, sessions = session_alive(self.view, owners)
        observed = {"derniere_activite": last.isoformat() if last else None, "heures": hours, "sessions": sessions}
        if hours is None:
            return False, f"dernière activité non mesurée ({NON_MESURE})", observed
        if alive:
            return False, "une session liée est encore vivante", observed
        if hours < threshold:
            return False, f"activité il y a {hours} h, moins que les {threshold} h annoncées", observed
        return True, f"aucune activité depuis {hours} h, aucune session vivante", observed

    def _proof_doublon_de(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        other = str(proof.get("element") or "")
        if not other or other == ref:
            return False, "« doublon_de » doit nommer un autre élément", {}
        exists, reason = self.element(other)
        return exists, (f"{other} existe" if exists else reason), {"element": other}

    def _proof_note(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        text = proof.get("texte")
        if not isinstance(text, str) or not text.strip():
            return False, "preuve note : « texte » non vide attendu", {}
        return True, "note libre (non vérifiable, jamais suffisante seule)", {}

    # ── conflits ──

    def conflict(self, ref: str) -> tuple[str, list[str], str]:
        """``(genre, fichiers, raison)`` du conflit de l'élément contre la cible, recalculé."""
        kind, _, ident = ref.partition(":")
        sha: str | None = None
        if kind == "branche":
            sha = self.branch_sha(ident)
        elif kind == "pr" and ident.isdigit():
            data, _reason = self.pr(int(ident))
            head = str((data or {}).get("headRefOid") or "")
            sha = resolve_commit(self.src, head) if head else None
        if not sha:
            return NON_MESURE, [], "tête introuvable dans le dépôt local (git fetch ?)"
        if not self.target_sha:
            return NON_MESURE, [], self._need_target()
        probe = merge_probe(self.src, self.target_sha, self.target_tree, sha, self.env)
        return probe.conflict_kind, list(probe.files), probe.reason


_ENOUGH = {
    "livree": "commit, fichier, pr (fusionnee) ou issue (fermee)",
    "obsolete": "commit, fichier, pr ou issue",
    "doublon": "doublon_de",
    "abandonnee": "inactivite",
}


def _sufficient(verdict: str, proofs: Sequence[Mapping[str, Any]]) -> bool:
    kinds = [str(p.get("type")) for p in proofs]
    if verdict == "livree":
        return any(
            k in ("commit", "fichier")
            or (k == "pr" and str(p.get("etat") or "fusionnee") == "fusionnee")
            or (k == "issue" and str(p.get("etat") or "fermee") == "fermee")
            for k, p in zip(kinds, proofs, strict=True)
        )
    if verdict == "obsolete":
        return any(k in ("commit", "fichier", "pr", "issue") for k in kinds)
    if verdict in ("doublon", "abandonnee"):
        return _ENOUGH[verdict] in kinds
    return True


def _judge(checker: _Checker, raw: Any, seen: set[str]) -> tuple[bool, dict[str, Any]]:
    if not isinstance(raw, dict):
        return False, {"element": None, "verdict": None, "raisons": ["entrée qui n'est pas un objet"], "preuves": []}
    ref, verdict = str(raw.get("element") or ""), str(raw.get("verdict") or "")
    out: dict[str, Any] = {"element": ref, "verdict": verdict}
    for key in ("theme", "taille", "note"):
        if isinstance(raw.get(key), str) and raw[key].strip():
            out[key] = raw[key]
    reasons: list[str] = []
    proofs_in = raw.get("preuves", [])
    if not isinstance(proofs_in, list):
        reasons.append("« preuves » doit être une liste")
        proofs_in = []
    if verdict not in VERDICTS:
        reasons.append(f"verdict « {verdict} » inconnu (attendu : {', '.join(VERDICTS)})")
    if ref in seen:
        reasons.append("élément déjà jugé plus haut dans le fichier")
    seen.add(ref)
    exists, why = checker.element(ref)
    if not exists:
        reasons.append(why)
    checked: list[dict[str, Any]] = []
    proofs = [p for p in proofs_in if isinstance(p, dict)]
    if len(proofs) != len(proofs_in):
        reasons.append("une preuve n'est pas un objet")
    if exists:
        for proof in proofs:
            ok, detail, observed = checker.check(ref, proof)
            checked.append({**proof, "verifiee": ok, "constat": detail, **({"mesure": observed} if observed else {})})
            if not ok:
                reasons.append(f"preuve {proof.get('type')} : {detail}")
    out["preuves"] = checked or proofs
    if verdict in _ENOUGH and not _sufficient(verdict, proofs):
        reasons.append(f"« {verdict} » exige une preuve vérifiable de type {_ENOUGH[verdict]} — une note seule ne suffit jamais")
    if verdict == "abandonnee" and not ref.startswith(("pr:", "branche:", "tache:")):
        reasons.append("« abandonnee » ne vaut que pour une PR, une branche ou une tâche")
    if verdict in ("conflit_simple", "conflit_semantique") and exists and not reasons:
        if not ref.startswith(("pr:", "branche:")):
            reasons.append(f"« {verdict} » ne vaut que pour une PR ou une branche")
        else:
            kind, files, why = checker.conflict(ref)
            out["conflit"] = {"etat": kind, "fichiers": files}
            want = "changelog_seul" if verdict == "conflit_simple" else "autre"
            if kind != want:
                reasons.append(f"conflit recalculé : {kind}{f' ({why})' if why else ''}, attendu {want}")
    if reasons:
        out["raisons"] = reasons
    return not reasons, out


def validate(src: Sources, data: Mapping[str, Any], *, source: str = "") -> dict[str, Any]:
    """Revérifie chaque verdict de *data* ; rend ``grimoire-steward-validation/v1`` (établis et rejetés)."""
    checker = _Checker(src)
    target_sha = resolve_commit(src, src.target)
    if target_sha:
        checker.target_sha = target_sha
        checker.target_tree = src.git("rev-parse", f"{target_sha}^{{tree}}").stdout.strip()
    established: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    with scratch_objects(src) as env:
        checker.env = env if checker.target_tree else None
        for raw in data.get("verdicts") or []:
            ok, entry = _judge(checker, raw, seen)
            (established if ok else rejected).append(entry)
    by_verdict: dict[str, int] = {}
    for entry in established:
        by_verdict[entry["verdict"]] = by_verdict.get(entry["verdict"], 0) + 1
    return {
        "$schema": VALIDATION_SCHEMA,
        "genere_le": src.now.isoformat(),
        "fichier": source,
        "cible": {"ref": src.target, "sha": target_sha or NON_MESURE},
        "etablis": established,
        "rejetes": rejected,
        "resume": {"etablis": len(established), "rejetes": len(rejected), "par_verdict": dict(sorted(by_verdict.items()))},
    }
