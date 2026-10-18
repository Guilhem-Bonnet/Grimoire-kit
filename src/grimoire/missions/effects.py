"""Effets externes réconciliés : ``effect.intended`` avant, ``effect.observed`` après, la remote entre les deux (K12).

Un push ou l'ouverture d'une PR ne se défait pas, et une coupure peut tomber entre l'intention et
l'effet (scénario F) ou entre l'effet et sa trace (scénario G). Rejouer à l'aveugle pousse deux fois
ou ouvre deux PR ; ne rien rejouer perd l'effet. Ici :

- l'effet a une identité stable — ``(tâche, genre, cible)`` haché — : le même effet redemandé est
  le même, jamais un second ;
- avant d'agir, :func:`perform` écrit ``effect.intended`` ; après, ``effect.observed`` ;
- une intention restée sans constat est d'abord **vérifiée sur la remote** (``git ls-remote``,
  ``gh pr list --head``) : présent, le constat est écrit et l'effet n'est pas refait ; absent, il est
  fait une fois ; invérifiable, rien n'est fait (fail-closed) et la raison est rendue ;
- le tick (:func:`reconcile`) ne fait aucun effet : il constate ceux qui ont abouti et signale les autres.

Un verrou par effet (:func:`grimoire.core.fsio.locked`) empêche deux exécutants simultanés du même
effet de passer tous deux la vérification. Les effets ``gh`` d'une session périmée restent non
fencés (§2.4) : la vérification distante en réduit le risque, elle ne l'annule pas.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import locked

if TYPE_CHECKING:
    from grimoire.missions.service import TaskService

__all__ = [
    "EFFECT_INTENDED_EVENT",
    "EFFECT_OBSERVED_EVENT",
    "Effect",
    "EffectOutcome",
    "default_probe",
    "effect_id",
    "pending_effects",
    "perform",
    "reconcile",
]

EFFECT_INTENDED_EVENT = "effect.intended"
EFFECT_OBSERVED_EVENT = "effect.observed"
#: Les genres que :func:`default_probe` sait vérifier sur la remote.
GIT_PUSH = "git.push"
GH_PR = "gh.pr"
PRESENT, ABSENT, UNKNOWN = "present", "absent", "unknown"
_LOCK_DIRNAME = "effects"
_PROBE_TIMEOUT_S = 30

#: Une sonde rend ``(present|absent|unknown, preuve ou raison)``.
Probe = Callable[["Effect"], tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Effect:
    """Un effet externe voulu pour une tâche ; ``target`` dit où le constater (dépôt, remote, branche, SHA)."""

    effect_id: str
    task_id: str
    kind: str
    target: Mapping[str, str]

    def label(self) -> str:
        where = self.target.get("branch") or self.target.get("ref") or ""
        return f"{self.kind} {where}".strip()


@dataclass(frozen=True, slots=True)
class EffectOutcome:
    """``done`` (fait maintenant), ``already`` (constaté avant), ``reconciled`` (trouvé sur la remote, non refait),
    ``absent`` (le tick ne l'a pas trouvé : à refaire par son job) ou ``unknown`` (invérifiable : rien n'est fait)."""

    effect: Effect
    status: str
    evidence: str = ""
    written: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect_id": self.effect.effect_id, "task_id": self.effect.task_id, "kind": self.effect.kind,
            "target": dict(self.effect.target), "status": self.status, "evidence": self.evidence, "written": self.written,
        }


def effect_id(task_id: str, kind: str, target: Mapping[str, str]) -> str:
    """L'identité stable d'un effet : le même ``(tâche, genre, cible)`` donne le même identifiant."""
    blob = json.dumps([task_id, kind, sorted((str(k), str(v)) for k, v in target.items())], ensure_ascii=True)
    return "eff-" + hashlib.sha256(blob.encode("ascii")).hexdigest()[:20]


def _states(service: TaskService, task_id: str | None = None) -> tuple[dict[str, Effect], dict[str, str]]:
    """``(intentions par id, preuve par id constaté)`` relus du ledger."""
    intended: dict[str, Effect] = {}
    observed: dict[str, str] = {}
    for event in service.ledger.list_events(task_id):
        payload = event.payload
        eid = str(payload.get("effect_id", "") or "")
        if not eid:
            continue
        if event.event_type == EFFECT_INTENDED_EVENT:
            raw = payload.get("target")
            target = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
            intended[eid] = Effect(eid, event.entity_id, str(payload.get("kind", "")), target)
        elif event.event_type == EFFECT_OBSERVED_EVENT:
            observed[eid] = str(payload.get("evidence", "") or "")
    return intended, observed


def pending_effects(service: TaskService, task_id: str | None = None) -> list[Effect]:
    """Les intentions sans constat — l'effet a pu avoir lieu ou non : seule la remote le dit."""
    intended, observed = _states(service, task_id)
    return [e for eid, e in intended.items() if eid not in observed]


def _observe(service: TaskService, effect: Effect, evidence: str, actor: str, *, reconciled: bool) -> None:
    service.ledger.append_event(
        EFFECT_OBSERVED_EVENT, effect.task_id, "task", actor,
        {"effect_id": effect.effect_id, "task_id": effect.task_id, "kind": effect.kind, "evidence": evidence,
         "reconciled": reconciled, "actor_id": actor},
    )


def perform(
    service: TaskService,
    task_id: str,
    kind: str,
    target: Mapping[str, str],
    act: Callable[[], str],
    *,
    actor: str,
    probe: Probe | None = None,
    lease_epoch: int | None = None,
) -> EffectOutcome:
    """Fait l'effet **une fois** : déjà constaté, rien ; intention en suspens, la remote d'abord ; sinon intention, effet, constat.

    *act* fait l'effet et rend sa preuve (SHA poussé, URL de PR). S'il lève, l'intention reste en suspens :
    l'appel suivant vérifiera la remote avant de refaire. Une remote invérifiable rend ``unknown`` sans agir.
    """
    service.require(task_id)
    clean = {str(k): str(v) for k, v in target.items()}
    effect = Effect(effect_id(task_id, kind, clean), task_id, kind, clean)
    check = probe or default_probe
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(locked(service.ledger_root / _LOCK_DIRNAME / effect.effect_id, timeout=60))
        except TimeoutError as exc:
            raise GrimoireMissionError(f"Effet {effect.label()} de {task_id} : un autre exécutant le tient ({exc})") from exc
        intended, observed = _states(service, task_id)
        if effect.effect_id in observed:
            return EffectOutcome(effect, "already", observed[effect.effect_id])
        if effect.effect_id in intended:
            status, evidence = check(effect)
            if status == PRESENT:
                _observe(service, effect, evidence, actor, reconciled=True)
                return EffectOutcome(effect, "reconciled", evidence, written=True)
            if status != ABSENT:
                return EffectOutcome(effect, UNKNOWN, evidence)
        else:
            service.ledger.append_event(
                EFFECT_INTENDED_EVENT, task_id, "task", actor,
                {"effect_id": effect.effect_id, "task_id": task_id, "kind": kind, "target": clean,
                 "lease_epoch": lease_epoch, "actor_id": actor},
            )
        evidence = act()
        _observe(service, effect, evidence, actor, reconciled=False)
        return EffectOutcome(effect, "done", evidence, written=True)


def reconcile(service: TaskService, *, actor: str = "tick", apply: bool = True, probe: Probe | None = None) -> list[EffectOutcome]:
    """La part « effets » du tick : chaque intention en suspens est vérifiée sur la remote, jamais refaite ici.

    Présent : le constat est écrit (``reconciled``). Absent : rendu ``absent`` — son job le refera, une seule
    fois, par :func:`perform`. Invérifiable : ``unknown``, avec la raison.
    """
    check = probe or default_probe
    outcomes: list[EffectOutcome] = []
    for effect in pending_effects(service):
        status, evidence = check(effect)
        if status == PRESENT:
            if apply:
                _observe(service, effect, evidence, actor, reconciled=True)
            outcomes.append(EffectOutcome(effect, "reconciled", evidence, written=apply))
        else:
            outcomes.append(EffectOutcome(effect, ABSENT if status == ABSENT else UNKNOWN, evidence))
    return outcomes


# ── Sondes ──────────────────────────────────────────────────────────────────


def _run(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # arguments fixes, sans shell
            list(argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_PROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def default_probe(effect: Effect) -> tuple[str, str]:
    """Vérifie l'effet sur la remote : ``git ls-remote`` pour un push, ``gh pr list --head`` pour une PR."""
    target = effect.target
    repo = Path(target.get("repo", "") or ".")
    branch = target.get("branch", "")
    if effect.kind == GIT_PUSH:
        sha = target.get("sha", "")
        if not branch or not sha:
            return UNKNOWN, "cible incomplète (branch, sha)"
        done = _run(repo, "git", "ls-remote", target.get("remote", "origin") or "origin", f"refs/heads/{branch}")
        if done is None or done.returncode != 0:
            return UNKNOWN, "git ls-remote a échoué" + (f" : {done.stderr.strip()[:200]}" if done is not None else "")
        heads = [line.split()[0] for line in done.stdout.splitlines() if line.strip()]
        return (PRESENT, sha) if sha in heads else (ABSENT, f"remote à {heads[0][:12]}" if heads else "branche absente")
    if effect.kind == GH_PR:
        if not branch:
            return UNKNOWN, "cible incomplète (branch)"
        done = _run(repo, "gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,url", "--limit", "1")
        if done is None or done.returncode != 0:
            return UNKNOWN, "gh pr list a échoué"
        try:
            found = json.loads(done.stdout or "[]")
        except json.JSONDecodeError:
            return UNKNOWN, "réponse de gh illisible"
        if isinstance(found, list) and found and isinstance(found[0], dict):
            return PRESENT, str(found[0].get("url", "") or found[0].get("number", ""))
        return (ABSENT, "aucune PR pour cette branche") if isinstance(found, list) else (UNKNOWN, "réponse de gh inattendue")
    return UNKNOWN, f"genre d'effet sans sonde : {effect.kind!r}"
