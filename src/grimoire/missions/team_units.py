"""Unités de travail partagées entre humains par git (W7-02, première tranche).

Le Mission Ledger reste local à un clone (``_grimoire-runtime-output/ledger``) : il
sert un opérateur et ses agents. Ce module porte ce qui se partage entre humains,
et rien d'autre : des **unités** (un titre, des critères d'acceptation, un
titulaire, un bail grossier, une livraison et sa revue), versionnées dans le dépôt
sous ``_grimoire/team/units/<unité>/`` et transportées par ``git pull``/``push``.

Format : un fichier par événement, jamais réécrit, nommé par l'empreinte de son
contenu (``<id>.json``). Deux clones qui écrivent en même temps écrivent deux
fichiers différents : une fusion git n'a jamais de conflit textuel. Chaque
événement cite les têtes qu'il a vues (``prev``) ; l'ordre se lit dans ce graphe,
jamais dans les horodatages (horloges des postes non comparables).

Concurrence : deux événements écrits sur la même tête dans deux clones donnent,
après fusion, deux têtes. L'unité est alors ``contested`` et **personne ne
gagne** : toute écriture est refusée, sauf ``resolve --keep <événement>``, qui
cite toutes les têtes et désigne la branche retenue (fail-closed, sans perte :
l'historique de la branche écartée reste lisible). Un fichier illisible, une
empreinte fausse ou une transition impossible rendent l'unité ``invalid`` ; un
parent absent (clone partiel) la rend ``incomplete`` ; dans les deux cas rien
ne s'écrit. Inconnu n'est jamais OK.

Identité : déclarée, pas prouvée — ``git config user.email`` du clone, ou
``--as``. L'événement dit d'où elle vient (``identity``).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.core.fsio import atomic_write, locked
from grimoire.missions.lease import parse_instant

__all__ = [
    "DEFAULT_TTL_HOURS",
    "UNITS_DIR",
    "UnitEvent",
    "UnitState",
    "list_units",
    "load_unit",
    "record",
    "resolve_identity",
]

SCHEMA = "grimoire-team-unit-event/v1"
UNITS_DIR = Path("_grimoire") / "team" / "units"
#: Verrou local, dans le dossier git commun du clone (jamais versionné, partagé par ses worktrees) ;
#: hors dépôt git, sous la sortie d'exécution : deux commandes du même clone ne s'intercalent pas.
LOCK_NAME = "grimoire-team-units"
LOCK_FALLBACK = Path("_grimoire-runtime-output") / "team" / LOCK_NAME
DEFAULT_TTL_HOURS = 48.0
KINDS = ("publish", "claim", "renew", "release", "adopt", "deliver", "review", "resolve")
#: Minuscules seules : sous Windows et macOS, ``U1`` et ``u1`` seraient le même dossier.
_UNIT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_EVENT_ID = re.compile(r"[0-9a-f]{32}")


def _canonical(body: dict[str, Any]) -> bytes:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class UnitEvent:
    """Un événement d'unité ; ``id`` est l'empreinte du reste (relue, jamais crue)."""

    unit: str
    kind: str
    actor: str
    at: str
    prev: tuple[str, ...]
    data: dict[str, Any]
    identity: str = "git-config"
    nonce: str = ""
    id: str = ""

    def body(self) -> dict[str, Any]:
        return {
            "$schema": SCHEMA, "unit": self.unit, "kind": self.kind, "actor": self.actor, "at": self.at,
            "prev": list(self.prev), "data": self.data, "identity": self.identity, "nonce": self.nonce,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.body())).hexdigest()[:32]

    def to_text(self) -> str:
        return json.dumps({**self.body(), "id": self.id}, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class UnitState:
    """L'état replié d'une unité. ``branches`` : l'état de chaque tête quand l'unité est ``contested``."""

    unit: str
    status: str
    title: str = ""
    criteria: tuple[str, ...] = ()
    publisher: str = ""
    holder: str = ""
    expires_at: str = ""
    adopted_from: str = ""
    deliverer: str = ""
    evidence: tuple[dict[str, str], ...] = ()
    verdict: str = ""
    head: str = ""
    heads: tuple[str, ...] = ()
    events: int = 0
    branches: tuple[UnitState, ...] = ()
    faults: tuple[str, ...] = ()
    last: dict[str, str] = field(default_factory=dict)

    def expired(self, now: datetime) -> bool:
        """Bail passé à *now* ; seulement pour un titulaire (``claimed``)."""
        moment = parse_instant(self.expires_at)
        return self.status == "claimed" and moment is not None and now > moment

    def to_dict(self, now: datetime) -> dict[str, Any]:
        out: dict[str, Any] = {
            "unit": self.unit, "status": self.status, "title": self.title, "criteria": list(self.criteria),
            "publisher": self.publisher, "holder": self.holder, "expires_at": self.expires_at,
            "expired": self.expired(now), "adopted_from": self.adopted_from, "deliverer": self.deliverer,
            "evidence": [dict(e) for e in self.evidence], "verdict": self.verdict, "head": self.head,
            "heads": list(self.heads), "events": self.events, "faults": list(self.faults),
        }
        if self.branches:
            out["branches"] = [b.to_dict(now) for b in self.branches]
        return out


def units_root(project_root: Path) -> Path:
    return project_root / UNITS_DIR


def check_unit_id(unit: str) -> str:
    if not _UNIT_ID.fullmatch(unit):
        raise GrimoireMissionError(
            f"identifiant d'unité invalide : {unit!r} (minuscules, chiffres et tirets, 63 caractères au plus)"
        )
    return unit


# ── Lecture ──────────────────────────────────────────────────────────────────


def _parse_event(path: Path, unit: str) -> tuple[UnitEvent | None, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path.name} : illisible ({exc.__class__.__name__})"
    if not isinstance(raw, dict) or raw.get("$schema") != SCHEMA:
        return None, f"{path.name} : schéma inconnu"
    prev, data = raw.get("prev"), raw.get("data")
    fields = [raw.get(k) for k in ("unit", "kind", "actor", "at", "identity", "nonce", "id")]
    if not all(isinstance(v, str) for v in fields) or not isinstance(prev, list) or not isinstance(data, dict):
        return None, f"{path.name} : champ manquant ou mal typé"
    if not all(isinstance(p, str) and _EVENT_ID.fullmatch(p) for p in prev):
        return None, f"{path.name} : prev mal formé"
    event = UnitEvent(
        unit=raw["unit"], kind=raw["kind"], actor=raw["actor"], at=raw["at"], prev=tuple(prev), data=data,
        identity=raw["identity"], nonce=raw["nonce"], id=raw["id"],
    )
    if event.unit != unit:
        return None, f"{path.name} : appartient à l'unité {event.unit!r}"
    if event.kind not in KINDS or not event.actor:
        return None, f"{path.name} : genre {event.kind!r} ou acteur vide"
    if event.id != event.digest() or path.name != f"{event.id}.json":
        return None, f"{path.name} : empreinte fausse (fichier modifié à la main ?)"
    return event, ""


def _read_events(unit_dir: Path, unit: str) -> tuple[dict[str, UnitEvent], list[str]]:
    events: dict[str, UnitEvent] = {}
    faults: list[str] = []
    for path in sorted(unit_dir.glob("*.json")):
        if path.name.startswith("."):
            continue  # temporaire d'une écriture atomique interrompue
        event, fault = _parse_event(path, unit)
        if event is None:
            faults.append(fault)
        else:
            events[event.id] = event
    return events, faults


def _strs(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
        return tuple(value)
    return None


def _apply(state: UnitState, event: UnitEvent) -> UnitState:
    """Une transition ; lève ``ValueError`` (motif) si elle est impossible depuis *state*."""
    kind, actor, data = event.kind, event.actor, event.data
    expires = data.get("expires_at")
    needs_expiry = kind in ("claim", "renew", "adopt") or (kind == "review" and data.get("verdict") == "reject")
    if needs_expiry and parse_instant(expires) is None:
        raise ValueError(f"{kind} sans expires_at lisible")
    if kind == "claim" and state.status == "open":
        return replace(state, status="claimed", holder=actor, expires_at=str(expires), adopted_from="")
    if kind == "renew" and state.status == "claimed" and actor == state.holder:
        return replace(state, expires_at=str(expires))
    if kind == "release" and state.status == "claimed" and actor == state.holder:
        return replace(state, status="open", holder="", expires_at="")
    if kind == "adopt" and state.status == "claimed" and actor != state.holder:
        if data.get("from") != state.holder or data.get("from_expires_at") != state.expires_at:
            raise ValueError("adopt ne cite pas le bail qu'il reprend")
        return replace(state, holder=actor, expires_at=str(expires), adopted_from=state.holder)
    if kind == "deliver" and state.status == "claimed" and actor == state.holder:
        evidence = data.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(e, dict) for e in evidence):
            raise ValueError("deliver sans preuve")
        return replace(state, status="delivered", deliverer=actor, evidence=tuple(evidence), verdict="")
    if kind == "review" and state.status == "delivered" and actor != state.deliverer:
        if data.get("verdict") == "accept":
            return replace(state, status="done", verdict="accept")
        if data.get("verdict") == "reject":
            return replace(state, status="claimed", holder=state.deliverer, expires_at=str(expires), verdict="reject")
    raise ValueError(f"{kind} par {actor} impossible depuis {state.status}")


def _fold(unit: str, head: str, events: dict[str, UnitEvent]) -> UnitState:
    """L'état au bout de *head*, en remontant la branche retenue jusqu'à la publication."""
    path: list[UnitEvent] = []
    cursor: str | None = head
    while cursor is not None:
        event = events[cursor]
        path.append(event)
        if event.kind == "publish":
            if event.prev:
                return UnitState(unit, "invalid", faults=(f"{event.id} : publish avec parent",))
            cursor = None
        elif event.kind == "resolve":
            keep = event.data.get("keep")
            if len(event.prev) < 2 or keep not in event.prev:
                return UnitState(unit, "invalid", faults=(f"{event.id} : resolve sans branche retenue valide",))
            cursor = str(keep)
        elif len(event.prev) != 1:
            return UnitState(unit, "invalid", faults=(f"{event.id} : {event.kind} doit avoir un seul parent",))
        else:
            cursor = event.prev[0]
    root = path.pop()
    title, criteria = root.data.get("title"), _strs(root.data.get("criteria"))
    if not isinstance(title, str) or not title or not criteria:
        return UnitState(unit, "invalid", faults=(f"{root.id} : publish sans titre ou sans critère",))
    state = UnitState(unit, "open", title=title, criteria=criteria, publisher=root.actor)
    for event in reversed(path):
        if event.kind == "resolve":
            continue
        try:
            state = _apply(state, event)
        except ValueError as exc:
            return UnitState(unit, "invalid", title=title, faults=(f"{event.id} : {exc}",))
    last = events[head]
    return replace(state, head=head, last={"kind": last.kind, "actor": last.actor, "at": last.at})


def load_unit(project_root: Path, unit: str) -> UnitState | None:
    """L'état de *unit* replié depuis ses fichiers ; ``None`` si l'unité n'existe pas."""
    unit_dir = units_root(project_root) / check_unit_id(unit)
    if not unit_dir.is_dir():
        return None
    events, faults = _read_events(unit_dir, unit)
    if faults:
        return UnitState(unit, "invalid", events=len(events), faults=tuple(faults))
    if not events:
        return None
    missing = sorted({p for e in events.values() for p in e.prev if p not in events})
    if missing:
        return UnitState(unit, "incomplete", events=len(events), faults=tuple(f"parent absent : {m}" for m in missing))
    cited = {p for e in events.values() for p in e.prev}
    heads = tuple(sorted(eid for eid in events if eid not in cited))
    if not heads:
        return UnitState(unit, "invalid", events=len(events), faults=("aucune tête (cycle)",))
    states = [_fold(unit, h, events) for h in heads]
    if len(states) == 1:
        return replace(states[0], heads=heads, events=len(events))
    invalid = tuple(f for s in states for f in s.faults)
    title = next((s.title for s in states if s.title), "")
    if invalid:
        return UnitState(unit, "invalid", title=title, heads=heads, events=len(events), faults=invalid)
    return UnitState(unit, "contested", title=title, heads=heads, events=len(events), branches=tuple(states))


def list_units(project_root: Path) -> list[UnitState]:
    root = units_root(project_root)
    if not root.is_dir():
        return []
    out: list[UnitState] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if not _UNIT_ID.fullmatch(child.name):
            out.append(UnitState(child.name, "invalid", faults=("nom de dossier hors charte",)))
            continue
        state = load_unit(project_root, child.name)
        if state is not None:
            out.append(state)
    return out


# ── Écriture ─────────────────────────────────────────────────────────────────


def run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args], capture_output=True, text=True, encoding="utf-8", check=False,
    )


def _lock_target(project_root: Path) -> Path:
    done = run_git(project_root, "rev-parse", "--git-common-dir")
    common = done.stdout.strip() if done.returncode == 0 else ""
    if not common:
        return project_root / LOCK_FALLBACK
    base = Path(common)
    return (base if base.is_absolute() else project_root / base) / LOCK_NAME


def resolve_identity(project_root: Path, declared: str = "") -> tuple[str, str]:
    """``(acteur, source)`` : *declared* (``option``) sinon ``git config user.email`` ; refuse l'inconnu."""
    if declared.strip():
        return declared.strip(), "option"
    done = run_git(project_root, "config", "user.email")
    email = done.stdout.strip() if done.returncode == 0 else ""
    if not email:
        raise GrimoireMissionError(
            "identité inconnue : aucun `git config user.email` dans ce clone — le poser, ou passer `--as <qui>`"
        )
    return email, "git-config"


def resolve_evidence(project_root: Path, refs: Iterable[str]) -> list[dict[str, str]]:
    """Chaque preuve résolue contre git : un commit (SHA complet) ou un fichier du dépôt (empreinte)."""
    out: list[dict[str, str]] = []
    for ref in refs:
        done = run_git(project_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if done.returncode == 0 and done.stdout.strip():
            out.append({"ref": ref, "commit": done.stdout.strip()})
            continue
        candidate = (project_root / ref).resolve()
        inside = candidate.is_relative_to(project_root.resolve())
        if inside and candidate.is_file():
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            out.append({"ref": Path(ref).as_posix(), "sha256": digest})
            continue
        raise GrimoireMissionError(f"preuve introuvable : {ref!r} n'est ni un commit ni un fichier du dépôt")
    if not out:
        raise GrimoireMissionError("livrer exige au moins une preuve (`--evidence <commit|fichier>`)")
    return out


def _expiry(now: datetime, hours: float) -> str:
    if not hours > 0:
        raise GrimoireMissionError("la durée du bail doit être positive (`--hours`)")
    return (now + timedelta(hours=hours)).isoformat()


def _refuse(state: UnitState, verb: str) -> None:
    if state.status == "contested":
        tips = ", ".join(f"{b.head[:12]} ({b.status} {b.holder or b.last.get('actor', '')})" for b in state.branches)
        raise GrimoireMissionError(
            f"{verb} refusé : l'unité {state.unit} est contestée ({len(state.heads)} têtes après fusion : {tips}). "
            f"Personne ne gagne d'office : `grimoire team unit resolve {state.unit} --keep <tête>`"
        )
    if state.status in ("invalid", "incomplete"):
        raise GrimoireMissionError(
            f"{verb} refusé : l'unité {state.unit} est {state.status} — " + "; ".join(state.faults)
        )


def _check(state: UnitState, kind: str, actor: str, now: datetime) -> None:
    """Le refus nommé d'un geste, avant d'écrire (la même règle que le repli, plus le temps)."""
    who = state.holder or "personne"
    if kind == "claim" and state.status != "open":
        hint = " — bail expiré : `adopt`" if state.expired(now) and state.holder != actor else ""
        raise GrimoireMissionError(
            f"claim refusé : {state.unit} est {state.status} (titulaire {who}, jusqu'à {state.expires_at or '-'}){hint}"
        )
    if kind in ("renew", "release", "deliver") and (state.status != "claimed" or state.holder != actor):
        raise GrimoireMissionError(f"{kind} refusé : {state.unit} est {state.status}, titulaire {who}, pas {actor}")
    if kind == "adopt":
        if state.status != "claimed" or state.holder == actor:
            raise GrimoireMissionError(
                f"adopt refusé : {state.unit} est {state.status}, titulaire {who} (son titulaire renouvelle : `renew`)"
            )
        if not state.expired(now):
            raise GrimoireMissionError(
                f"adopt refusé : le bail de {state.holder} sur {state.unit} court jusqu'à {state.expires_at}"
            )
    if kind == "review" and (state.status != "delivered" or state.deliverer == actor):
        raise GrimoireMissionError(
            f"review refusée : {state.unit} est {state.status}"
            + (" — on ne revoit pas sa propre livraison" if state.deliverer == actor else "")
        )


def record(
    project_root: Path,
    unit: str,
    kind: str,
    actor: str,
    *,
    identity: str = "git-config",
    data: dict[str, Any] | None = None,
    hours: float = DEFAULT_TTL_HOURS,
    now: datetime | None = None,
) -> tuple[UnitEvent, Path]:
    """Écrit l'événement *kind* sur *unit* après relecture sous verrou ; rend l'événement et son fichier."""
    check_unit_id(unit)
    if kind not in KINDS:
        raise GrimoireMissionError(f"geste inconnu : {kind}")
    moment = now or datetime.now(UTC)
    payload = dict(data or {})
    if kind == "review" and payload.get("verdict") not in ("accept", "reject"):
        raise GrimoireMissionError("review : verdict `accept` ou `reject` attendu")
    with locked(_lock_target(project_root)):
        state = load_unit(project_root, unit)
        if kind == "publish":
            if state is not None:
                raise GrimoireMissionError(f"publish refusé : l'unité {unit} existe déjà ({state.status})")
            prev: tuple[str, ...] = ()
        elif state is None:
            raise GrimoireMissionError(f"unité inconnue : {unit} (après `git pull` ?)")
        elif kind == "resolve":
            if state.status != "contested":
                raise GrimoireMissionError(f"resolve refusé : {unit} n'est pas contestée ({state.status})")
            wanted = str(payload.get("keep", ""))
            keep = [h for h in state.heads if len(wanted) >= 6 and h.startswith(wanted)]
            if len(keep) != 1:
                raise GrimoireMissionError(f"resolve : --keep doit désigner une seule tête parmi {', '.join(state.heads)}")
            payload["keep"] = keep[0]
            prev = state.heads
        else:
            _refuse(state, kind)
            _check(state, kind, actor, moment)
            prev = (state.head,)
            if kind in ("claim", "renew", "adopt") or (kind == "review" and payload.get("verdict") == "reject"):
                payload["expires_at"] = _expiry(moment, hours)
            if kind == "adopt":
                payload.update({"from": state.holder, "from_expires_at": state.expires_at})
        event = UnitEvent(unit, kind, actor, moment.isoformat(), prev, payload, identity, uuid.uuid4().hex)
        event = replace(event, id=event.digest())
        path = units_root(project_root) / unit / f"{event.id}.json"
        atomic_write(path, event.to_text())
        after = load_unit(project_root, unit)
        if after is None or after.status == "invalid":
            path.unlink(missing_ok=True)
            faults = "; ".join(after.faults) if after is not None else "unité illisible"
            raise GrimoireMissionError(f"{kind} refusé à la relecture : {faults}")
    return event, path
