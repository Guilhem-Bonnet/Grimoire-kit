"""Validation par du code d'un fichier de verdicts (``grimoire steward verdicts``), schéma ``grimoire-steward-verdicts/v1``.

Un agent lit la carte et juge chaque élément ; ce module ne juge pas, il **vérifie la preuve** et
qu'elle porte bien **sur l'élément jugé** :

- ``commit`` : le SHA désigne un commit (jamais une référence homonyme) qui est sur la cible ; avec
  ``message_contient``, le message contient ce texte ;
- ``fichier`` : un fichier (un blob, pas un dossier) et son motif sont sur la cible — ou, avec
  ``absent``, n'y sont plus **après y avoir été** (une faute de frappe n'est jamais « absente ») ;
- ``issue`` / ``pr`` : l'état lu par ``gh`` — raison de fermeture d'une issue (``not_planned`` ou
  ``duplicate`` ne valent pas une livraison), fusion d'une PR dans la cible ;
- ``inactivite`` : la dernière activité et la vivacité des sessions **recalculées** ;
- ``doublon_de`` : l'autre élément existe, est ouvert, n'est pas lui-même jugé doublon, abandonné ou
  obsolète, et ne forme pas de cycle ;
- ``note`` : du texte libre, jamais suffisant seul.

Un faux « livré » fait perdre du travail : dans le doute, le verdict est rejeté, avec sa raison.
Rien n'est écrit au ledger, au dépôt ni sur GitHub.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from grimoire.missions.lease import parse_instant
from grimoire.missions.liveness import TERMINE
from grimoire.missions.schemas import TaskState
from grimoire.steward import VALIDATION_SCHEMA, VERDICTS_SCHEMA
from grimoire.steward.project_map import LedgerView, MapContext, id_pattern, issue_refs, owners_of_branch, read_ledger
from grimoire.steward.sources import (
    NON_MESURE,
    Sources,
    branch_ref,
    commit_on_target,
    file_on_target,
    gh_json,
    hours_since,
    last_commit_at,
    list_worktrees,
    path_in_history,
    resolve_commit,
    resolve_sha,
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
_PR_FIELDS = "number,state,title,body,updatedAt,headRefName,headRefOid,baseRefName,mergedAt,mergeCommit"
#: Verdicts qui retirent un élément du travail : jamais sur une branche dont le worktree a du travail non commité.
_REMOVING = frozenset({"livree", "obsolete", "abandonnee"})
#: Une cible de doublon jugée ainsi n'est plus une cible.
_NOT_A_TARGET = frozenset({"doublon", "abandonnee", "obsolete"})


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


def _names(ref: str, text: str, branch: str = "") -> bool:
    """*text* (ou le nom de *branch*) nomme-t-il l'élément *ref* ?"""
    kind, _, ident = ref.partition(":")
    if kind == "tache":
        pattern = id_pattern(ident)
        return bool(pattern.search(text) or (branch and pattern.search(branch)))
    if kind in ("issue", "pr") and ident.isdigit():
        number = int(ident)
        return number in issue_refs(text) or bool(branch and re.search(rf"(?<!\d){number}(?!\d)", branch))
    return False


@dataclass
class _Checker:
    src: Sources
    ctx: MapContext
    #: Verdict annoncé par élément, et cible de doublon — tout le fichier, pour les cycles.
    planned: dict[str, str] = field(default_factory=dict)
    duplicates: dict[str, str] = field(default_factory=dict)
    _gh: dict[tuple[str, ...], tuple[Any, str]] = field(default_factory=dict)
    _worktrees: dict[str, bool | None] | None = None

    @property
    def view(self) -> LedgerView | None:
        return self.ctx.view

    # ── lectures mises en cache ──

    def gh(self, *args: str) -> tuple[Any, str]:
        if args not in self._gh:
            self._gh[args] = gh_json(self.src, *args)
        return self._gh[args]

    def pr(self, number: int) -> tuple[dict[str, Any] | None, str]:
        data, reason = self.gh("pr", "view", str(number), "--json", _PR_FIELDS)
        return (data, "") if isinstance(data, dict) else (None, reason or f"PR #{number} illisible")

    def issue(self, number: int) -> tuple[dict[str, Any] | None, str]:
        data, reason = self.gh("issue", "view", str(number), "--json", "number,state,stateReason,closedAt,updatedAt,title")
        return (data, "") if isinstance(data, dict) else (None, reason or f"issue #{number} illisible")

    def dirty_branch(self, name: str) -> bool | None:
        """Le worktree qui porte la branche locale *name* a-t-il du travail non commité ? ``None`` : aucun / non mesuré."""
        if self._worktrees is None:
            self._worktrees = {}
            for entry in list_worktrees(self.src) or []:
                if entry.branch:
                    previous = self._worktrees.get(entry.branch)
                    self._worktrees[entry.branch] = True if previous or entry.dirty else entry.dirty
        local = name.split("/", 1)[1] if self.src.remote and name.startswith(f"{self.src.remote}/") else name
        return self._worktrees.get(local)

    def merged_on_target(self, data: Mapping[str, Any]) -> tuple[bool, str]:
        """Une PR fusionnée **dans la cible** : base = branche cible et commit de fusion sur la cible."""
        if str(data.get("state") or "") != "MERGED" or not data.get("mergedAt"):
            return False, "PR non fusionnée"
        if str(data.get("baseRefName") or self.src.target_branch) != self.src.target_branch:
            return False, f"PR fusionnée dans {data.get('baseRefName')}, pas dans {self.src.target_branch}"
        merge = data.get("mergeCommit") if isinstance(data.get("mergeCommit"), dict) else {}
        if not self.ctx.target_sha:
            return False, f"cible {self.src.target} introuvable"
        ok, _full, why = commit_on_target(self.src, self.ctx.target_sha, str((merge or {}).get("oid") or ""))
        return ok, "" if ok else f"commit de fusion non vérifié sur la cible : {why}"

    # ── éléments ──

    def element(self, ref: str) -> tuple[bool, str]:
        """``(existe, raison)`` — une existence qu'on ne peut pas mesurer est un refus (échec fermé)."""
        match = _REF.fullmatch(ref)
        if not match or match["kind"] not in KINDS:
            return False, f"élément « {ref} » mal formé (attendu <{'|'.join(KINDS)}>:<identifiant>)"
        kind, ident = match["kind"], match["id"]
        if kind == "tache":
            if self.view is None:
                return False, "ledger non mesuré (absent ou illisible)"
            return (ident in self.view.tasks, f"tâche {ident} absente du ledger")
        if kind == "branche":
            found, reason = branch_ref(self.src, ident)
            return found is not None, reason
        if kind == "worktree":
            entries = list_worktrees(self.src)
            if entries is None:
                return False, "git worktree list a échoué"
            return (any(Path(e.path) == Path(ident) for e in entries), f"worktree {ident} introuvable")
        if not ident.isdigit():
            return False, f"numéro « {ident} » invalide"
        data, reason = self.pr(int(ident)) if kind == "pr" else self.issue(int(ident))
        return (data is not None, reason)

    def closed(self, ref: str) -> str:
        """Pourquoi l'élément *ref* n'est plus ouvert (``""`` s'il l'est ou si on ne sait pas le dire fermé)."""
        kind, _, ident = ref.partition(":")
        if kind == "tache" and self.view is not None and ident in self.view.tasks:
            status = self.view.tasks[ident].status
            return f"tâche {status.value}" if status in (TaskState.CLOSED, TaskState.CANCELLED, TaskState.FAILED) else ""
        if kind in ("issue", "pr") and ident.isdigit():
            data, _ = self.pr(int(ident)) if kind == "pr" else self.issue(int(ident))
            state = str((data or {}).get("state") or "")
            return f"{kind} #{ident} {state.lower()}" if state in ("CLOSED", "MERGED") else ""
        return ""

    # ── preuves ──

    def check(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        kind = proof.get("type")
        handler = getattr(self, f"_proof_{kind}", None) if kind in PROOF_TYPES else None
        if handler is None:
            return False, f"type de preuve inconnu « {kind} » (attendu : {', '.join(PROOF_TYPES)})", {}
        result: tuple[bool, str, dict[str, Any]] = handler(ref, proof)
        return result

    def _need_target(self) -> str:
        return "" if self.ctx.target_sha else f"cible {self.src.target} introuvable : preuve non vérifiable"

    def _proof_commit(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        if missing := self._need_target():
            return False, missing, {}
        ok, full, reason = commit_on_target(self.src, self.ctx.target_sha, str(proof.get("sha") or ""))
        observed: dict[str, Any] = {"sha": full, "nomme": False} if full else {}
        expected = str(proof.get("message_contient") or "")
        if ok and expected:
            message = self.src.git("log", "-1", "--format=%B", full).stdout
            if expected not in message:
                return False, f"le message de {full[:12]} ne contient pas « {expected} »", observed
            observed["nomme"] = _names(ref, expected)
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
        present, detail = file_on_target(self.src, self.ctx.target_sha, path, pattern)
        if present is None:
            return False, f"{detail} ({NON_MESURE})", {}
        observed: dict[str, Any] = {"present": present}
        if present is absent:
            return False, f"attendu {'absent' if absent else 'présent'} : {detail}", observed
        if absent:
            # Une absence ne prouve un retrait que si le chemin (et le motif) a existé sur la cible.
            existed = path_in_history(self.src, self.ctx.target_sha, path, pattern)
            observed["a_existe"] = existed
            if existed is not True:
                what = f"le motif dans {path}" if pattern else path
                return False, f"{what} n'a jamais existé dans l'historique de la cible (faute de frappe ?)", observed
            return True, f"{detail}, après y avoir existé", observed
        return True, detail, observed

    def _proof_issue(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        number, expected = proof.get("numero"), str(proof.get("etat") or "fermee")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0 or expected not in ("fermee", "ouverte"):
            return False, "preuve issue : « numero » entier et « etat » fermee|ouverte attendus", {}
        data, reason = self.issue(number)
        if data is None:
            return False, f"{reason} ({NON_MESURE})", {}
        actual = _ISSUE_STATES.get(str(data.get("state") or ""), str(data.get("state") or ""))
        observed: dict[str, Any] = {"etat": actual, "raison_fermeture": data.get("stateReason") or "", "fermee_le": data.get("closedAt")}
        # Un numéro de PR : « fermée » ne vaut livraison que fusionnée dans la cible.
        as_pr, _ = self.pr(number)
        observed["est_pr"] = as_pr is not None
        if as_pr is not None:
            observed["livree"] = self.merged_on_target(as_pr)[0]
        else:
            observed["livree"] = actual == "fermee" and str(data.get("stateReason") or "").upper() == "COMPLETED"
        kind, _, ident = ref.partition(":")
        linked = self.view is not None and kind == "tache" and ident in self.view.tasks and number in issue_refs(
            self.view.tasks[ident].title, self.view.tasks[ident].description
        )
        observed["nomme"] = (kind in ("issue", "pr") and ident == str(number)) or linked
        if actual != expected:
            return False, f"#{number} est {actual}, attendu {expected}", observed
        reason_txt = f" ({str(data.get('stateReason')).lower()})" if data.get("stateReason") else ""
        return True, f"#{number} est {actual}{reason_txt}", observed

    def _proof_pr(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        number, expected = proof.get("numero"), str(proof.get("etat") or "fusionnee")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0 or expected not in _PR_STATES.values():
            return False, "preuve pr : « numero » entier et « etat » fusionnee|fermee|ouverte attendus", {}
        data, reason = self.pr(number)
        if data is None:
            return False, f"{reason} ({NON_MESURE})", {}
        actual = _PR_STATES.get(str(data.get("state") or ""), str(data.get("state") or ""))
        merge = data.get("mergeCommit") if isinstance(data.get("mergeCommit"), dict) else {}
        text = f"{data.get('title') or ''}\n{data.get('body') or ''}"
        observed: dict[str, Any] = {
            "etat": actual,
            "fusionnee_le": data.get("mergedAt"),
            "commit_de_fusion": (merge or {}).get("oid"),
            "base": data.get("baseRefName"),
            "nomme": ref == f"pr:{number}" or _names(ref, text, str(data.get("headRefName") or "")),
        }
        if actual != expected:
            return False, f"PR #{number} est {actual}, attendu {expected}", observed
        if actual == "fusionnee":
            ok, why = self.merged_on_target(data)
            observed["livree"] = ok
            if not ok:
                return False, f"PR #{number} fusionnée, mais pas vérifiée dans la cible : {why}", observed
        return True, f"PR #{number} est {actual}", observed

    def _proof_inactivite(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        threshold = proof.get("heures")
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
            return False, "preuve inactivite : « heures » (nombre > 0) attendu", {}
        if self.view is None:
            return False, "vivacité non mesurée : ledger illisible ou absent", {}
        kind, _, ident = ref.partition(":")
        head_sha, branch = "", ""
        if kind == "tache":
            last, owners = parse_instant(self.view.last_activity.get(ident)), [ident]
            branch = f"task/{ident}"
        elif kind == "pr" and ident.isdigit():
            data, reason = self.pr(int(ident))
            if data is None:
                return False, f"{reason} ({NON_MESURE})", {}
            last = parse_instant(data.get("updatedAt"))
            branch = str(data.get("headRefName") or "")
            head_sha = resolve_sha(self.src, str(data.get("headRefOid") or "")) or ""
            owners = owners_of_branch(self.view, branch, str(data.get("title") or ""), int(ident))
        elif kind == "branche":
            full, reason = branch_ref(self.src, ident)
            if full is None:
                return False, reason, {}
            head_sha, branch = full, ident
            last, owners = last_commit_at(self.src, full), owners_of_branch(self.view, ident)
        else:
            return False, f"inactivité non mesurable pour un élément « {kind} »", {}
        hours = hours_since(last, self.src.now)
        states = {o: (self.view.sessions.get(o) or {}).get("vivacite", {}).get("state") for o in owners}
        observed: dict[str, Any] = {"derniere_activite": last.isoformat() if last else None, "heures": hours, "sessions": states}
        if hours is None:
            return False, f"dernière activité non mesurée ({NON_MESURE})", observed
        if hours < threshold:
            return False, f"activité il y a {hours} h, moins que les {threshold} h annoncées", observed
        measured = {o: s for o, s in states.items() if s}
        if owners and not measured:
            return False, "tâche liée sans session : vivacité non mesurée, l'abandon n'est pas établi", observed
        if any(s != TERMINE for s in measured.values()):
            alive = ", ".join(f"{o} {s}" for o, s in measured.items() if s != TERMINE)
            return False, f"session non terminée ({alive}) : « inconnu » n'est pas « morte »", observed
        if not owners:
            # Sans propriétaire : seulement si aucun travail non commité et un dernier commit au-delà du seuil.
            if self.dirty_branch(branch):
                return False, "worktree de la branche avec du travail non commité", observed
            commit_hours = hours_since(last_commit_at(self.src, head_sha), self.src.now) if head_sha else None
            observed["heures_dernier_commit"] = commit_hours
            if commit_hours is None or commit_hours < threshold:
                return False, f"sans propriétaire, dernier commit à {commit_hours} h : sous le seuil ou non mesuré", observed
            return True, f"aucune activité depuis {hours} h, aucun propriétaire, dernier commit à {commit_hours} h", observed
        return True, f"aucune activité depuis {hours} h, session(s) terminée(s)", observed

    def _proof_doublon_de(self, ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        other = str(proof.get("element") or "")
        if not other or other == ref:
            return False, "« doublon_de » doit nommer un autre élément", {}
        exists, reason = self.element(other)
        if not exists:
            return False, reason, {"element": other}
        if self.planned.get(other) in _NOT_A_TARGET:
            return False, f"{other} est lui-même jugé {self.planned[other]} dans ce fichier", {"element": other}
        seen, cursor = {ref}, other
        while cursor in self.duplicates:
            cursor = self.duplicates[cursor]
            if cursor in seen:
                return False, f"cycle de doublons ({ref} → {other} → … → {cursor})", {"element": other}
            seen.add(cursor)
        if why := self.closed(other):
            return False, f"{other} n'est plus ouvert ({why}) : ce n'est pas un original", {"element": other}
        return True, f"{other} existe et reste ouvert", {"element": other}

    def _proof_note(self, _ref: str, proof: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        text = proof.get("texte")
        if not isinstance(text, str) or not text.strip():
            return False, "preuve note : « texte » non vide attendu", {}
        return True, "note libre (non vérifiable, jamais suffisante seule)", {}

    # ── conflits et fusion ──

    def head_of(self, ref: str) -> tuple[str | None, str, str]:
        """``(sha local, branche, raison)`` de la tête d'une branche ou d'une PR."""
        kind, _, ident = ref.partition(":")
        if kind == "branche":
            full, reason = branch_ref(self.src, ident)
            return (resolve_commit(self.src, full) if full else None), ident, reason
        if kind == "pr" and ident.isdigit():
            data, reason = self.pr(int(ident))
            head = str((data or {}).get("headRefOid") or "")
            sha = resolve_sha(self.src, head) if head else None
            return sha, str((data or {}).get("headRefName") or ""), "" if sha else "tête de la PR absente du dépôt local (git fetch)"
        return None, "", f"aucune tête pour un élément « {kind} »"

    def conflict(self, ref: str) -> tuple[str, list[str], str]:
        sha, _branch, why = self.head_of(ref)
        if not sha:
            return NON_MESURE, [], why
        if not self.ctx.target_sha:
            return NON_MESURE, [], self._need_target()
        probe = self.ctx.probe(sha)
        return probe.conflict_kind, list(probe.files), probe.reason


_ENOUGH = {
    "obsolete": "commit, fichier, pr ou issue",
    "doublon": "doublon_de",
    "abandonnee": "inactivite",
}


def _sufficiency(checker: _Checker, ref: str, verdict: str, checked: Sequence[Mapping[str, Any]]) -> list[str]:
    """Ce qui manque pour que les preuves (toutes vérifiées) établissent *verdict* sur *ref*."""
    kinds = [str(p.get("type")) for p in checked]
    kind = ref.partition(":")[0]
    if verdict == "livree":
        if kind == "branche":
            sha, _branch, why = checker.head_of(ref)
            if not sha:
                return [f"fusion non mesurée : {why}"]
            fusion, _conflict, _counts = checker.ctx.fusion(sha)
            if fusion["etat"] != "fusionnee":
                return [f"« livree » exige une branche fusionnée dans la cible ; fusion recalculée : {fusion['etat']} {fusion.get('par', '')}".rstrip()]
            return []
        if kind == "worktree":
            return ["« livree » ne vaut pas pour un worktree"]
        naming = [
            p
            for p in checked
            if (p.get("mesure") or {}).get("nomme")
            and (p.get("type") == "commit" or (p.get("mesure") or {}).get("livree") is True)
        ]
        if not naming:
            return [
                "« livree » exige une preuve qui nomme l'élément : commit dont « message_contient » le cite, "
                "PR fusionnée dans la cible qui le nomme (titre, corps ou branche), ou l'issue liée fermée comme terminée"
            ]
        return []
    if verdict == "obsolete" and not any(k in ("commit", "fichier", "pr", "issue") for k in kinds):
        return [f"« obsolete » exige une preuve vérifiable de type {_ENOUGH['obsolete']} — une note seule ne suffit jamais"]
    if verdict in ("doublon", "abandonnee") and _ENOUGH[verdict] not in kinds:
        return [f"« {verdict} » exige une preuve vérifiable de type {_ENOUGH[verdict]} — une note seule ne suffit jamais"]
    if verdict == "abandonnee" and kind not in ("pr", "branche", "tache"):
        return ["« abandonnee » ne vaut que pour une PR, une branche ou une tâche"]
    return []


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
    if exists and not reasons and verdict in VERDICTS:
        reasons += _sufficiency(checker, ref, verdict, checked)
    kind = ref.partition(":")[0]
    if exists and verdict in _REMOVING and kind in ("branche", "pr", "tache"):
        branch = ref.partition(":")[2] if kind == "branche" else checker.head_of(ref)[1] if kind == "pr" else f"task/{ref.partition(':')[2]}"
        if branch and checker.dirty_branch(branch):
            reasons.append(f"le worktree de {branch} a du travail non commité : jamais « {verdict} »")
    if verdict in ("conflit_simple", "conflit_semantique") and exists and not reasons:
        if kind not in ("pr", "branche"):
            reasons.append(f"« {verdict} » ne vaut que pour une PR ou une branche")
        else:
            state, files, why = checker.conflict(ref)
            out["conflit"] = {"etat": state, "fichiers": files}
            want = "changelog_seul" if verdict == "conflit_simple" else "autre"
            if state != want:
                reasons.append(f"conflit recalculé : {state}{f' ({why})' if why else ''}, attendu {want}")
    if reasons:
        out["raisons"] = reasons
    return not reasons, out


def validate(src: Sources, data: Mapping[str, Any], *, source: str = "", view: LedgerView | None = None) -> dict[str, Any]:
    """Revérifie chaque verdict de *data* ; rend ``grimoire-steward-validation/v1`` (établis et rejetés)."""
    if view is None:
        view, _reason = read_ledger(src)
    ctx = MapContext(src=src, view=view)
    ctx.prepare()
    checker = _Checker(src, ctx)
    raws = [r for r in data.get("verdicts") or [] if isinstance(r, dict)]
    for raw in raws:
        ref = str(raw.get("element") or "")
        checker.planned.setdefault(ref, str(raw.get("verdict") or ""))
        if raw.get("verdict") == "doublon":
            for proof in raw.get("preuves") or []:
                if isinstance(proof, dict) and proof.get("type") == "doublon_de" and proof.get("element"):
                    checker.duplicates.setdefault(ref, str(proof["element"]))
    established: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    with scratch_objects(src) as env:
        ctx.env = env if ctx.target_tree else None
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
        "cible": {"ref": src.target, "sha": ctx.target_sha or NON_MESURE},
        "troncatures": ctx.truncations,
        "etablis": established,
        "rejetes": rejected,
        "resume": {"etablis": len(established), "rejetes": len(rejected), "par_verdict": dict(sorted(by_verdict.items()))},
    }
