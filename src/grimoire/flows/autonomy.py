"""Octroi d'autonomie à périmètre, coût et durée, révocable (W5-06).

Avant ce module, un checkpoint destructif n'avançait que sur une décision
``attested`` (W2-01) : un tour humain de la session qui l'a présenté. Faire
tourner un profil governed sans humain (CI, banc) n'avait donc qu'une issue,
déclarer ``GRIMOIRE_UNATTENDED=1`` — et sous ce drapeau le checkpoint refuse.
Une variable d'environnement n'est pas une preuve d'autorité (section 4 du
plan) : l'autonomie passe ici par un *octroi* tracé.

Un octroi :

- est lui-même une décision ``attested`` : ``grimoire autonomy grant`` émet un
  challenge lié à la session, un tour humain de cette session postérieur au
  challenge l'atteste, et la même commande relancée l'inscrit (le tour est
  consommé dans le registre commun de :mod:`.attestation`, comme celui d'un
  checkpoint) — il compte donc comme une intervention humaine ;
- porte un périmètre (``run:<id>`` ou ``blueprint:<id>``, éventuellement
  restreint à un node par ``/<node>``), un plafond ``max_cost_usd`` et une
  échéance ``until``, les trois obligatoires ;
- décide, tant qu'il est valide, les checkpoints de son périmètre avec
  ``attestation=granted:<grant_id>``, distinct d'``attested`` : aucun humain
  n'a vu *ce* checkpoint ;
- est révoqué à l'expiration, au dépassement de coût, au premier coût inconnu
  (politique ``stop`` de :mod:`grimoire.costs`, sans alternative) et au premier
  gate rouge : une sortie refusée par son contrat dans un run couvert, ou une
  tentative de dispatch ``red``/``unrunnable`` dans un run couvert. Une
  révocation est définitive : la session repasse en « décision humaine
  requise » (``awaiting_human``) jusqu'à un nouvel octroi ou un tour attesté.

Chaque octroi, chaque challenge et chaque révocation est une ligne du journal
``<flows>/authority/@autonomy-grants.authority.jsonl`` (suffixe protégé par
:mod:`grimoire.hosts.decisions.authority_guard`). Une révocation constatée à la
lecture (échéance passée, coût franchi) est écrite à ce moment-là, une seule
fois.

Le coût mesuré est celui que le kit voit : les événements ``task.dispatched``
du Mission Ledger pour les nodes des runs couverts (sous-flows compris),
postérieurs à l'octroi. Le coût propre de la session hôte n'est mesuré nulle
part dans le kit : il n'entre pas dans ce total, et ``autonomy status`` le
dit (``cost_scope``).

Python seul : rien ne traverse ``grimoire_flows_core``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.fsio import locked
from grimoire.core.standard_state import current_session
from grimoire.costs import POLICY_STOP, Cost, cap_reason
from grimoire.flows.attestation import GRANTED_PREFIX, Attestation, CheckpointJournal, attest
from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX

__all__ = [
    "COST_SCOPE",
    "GRANTED_PREFIX",
    "GRANTS_FILENAME",
    "REVOKE_COST_REACHED",
    "REVOKE_COST_UNKNOWN",
    "REVOKE_EXPIRED",
    "REVOKE_GATE_RED",
    "REVOKE_MANUAL",
    "GrantJournal",
    "GrantRequest",
    "GrantState",
    "Selector",
    "parse_until",
    "refusal_note",
]

GRANTS_FILENAME = "@autonomy-grants" + AUTHORITY_SUFFIX
#: Ce que couvre le coût d'un octroi — la limite écrite dans ``autonomy status``.
COST_SCOPE = "dispatch_only : coût des tentatives task.dispatched des runs couverts ; le coût de la session hôte n'est pas mesuré"

REVOKE_EXPIRED = "expired"
REVOKE_COST_REACHED = "cost_reached"
REVOKE_COST_UNKNOWN = "cost_unknown"
REVOKE_GATE_RED = "gate_red"
REVOKE_MANUAL = "manual"
_REVOKE_TEXT = {
    REVOKE_EXPIRED: "échéance passée",
    REVOKE_COST_REACHED: "plafond de coût atteint",
    REVOKE_COST_UNKNOWN: "coût inconnu sous plafond (politique stop)",
    REVOKE_GATE_RED: "gate rouge dans le périmètre",
    REVOKE_MANUAL: "révoqué à la main",
}

#: Verdicts de dispatch qui sont un gate rouge : les checks ont tourné et
#: échoué, ou n'ont pas pu rendre de verdict. ``rate_limit``/``timeout``/
#: ``error`` sont des pannes d'appel, pas des gates (aucun check n'a tourné).
_RED_VERDICTS = frozenset({"red", "unrunnable"})
_ID = r"[A-Za-z0-9_.-]{1,128}"
_SELECTOR = re.compile(rf"^(run|blueprint):({_ID})(?:/({_ID}))?$")
_RELATIVE = re.compile(r"^\+(\d{1,6})([mhd])$")
_LEDGER_RELPATH = Path("_grimoire-runtime-output") / "ledger"
#: Garde contre un ``parent_run_id`` circulaire dans des métadonnées abîmées.
_MAX_LINEAGE = 64


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(ts: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class Selector:
    """Un élément de périmètre : ``run:<id>`` ou ``blueprint:<id>``, ``/<node>`` en option."""

    kind: str
    ident: str
    node: str = ""

    @classmethod
    def parse(cls, text: str) -> Selector:
        match = _SELECTOR.match(text.strip())
        if match is None:
            raise GrimoireRuntimeError(
                f"périmètre invalide : {text!r} (attendu run:<id> ou blueprint:<id>, '/<node>' en option ; aucun joker)"
            )
        return cls(match.group(1), match.group(2), match.group(3) or "")

    def __str__(self) -> str:
        return f"{self.kind}:{self.ident}" + (f"/{self.node}" if self.node else "")

    def covers_lineage(self, lineage: Sequence[tuple[str, str]]) -> bool:
        """*lineage* : ``(run_id, blueprint_id)`` du run puis de ses parents — un sous-flow est couvert."""
        index = 0 if self.kind == "run" else 1
        return any(link[index] == self.ident for link in lineage)

    def covers_checkpoint(self, lineage: Sequence[tuple[str, str]], node_id: str) -> bool:
        """Le checkpoint *node_id* du run de tête de *lineage* est-il dans le périmètre ?

        Sans ``/<node>``, toute la lignée est couverte (sous-flows compris). Avec
        ``/<node>``, la restriction vise le seul run que le sélecteur désigne : le
        run courant doit être celui de ``run:<id>`` (ou de ``blueprint:<id>``), et
        c'est son node qui doit porter ce nom. Un node homonyme d'un sous-flow
        d'un autre blueprint n'est pas couvert.
        """
        if not self.node:
            return self.covers_lineage(lineage)
        return bool(lineage) and self.covers_lineage(lineage[:1]) and node_id == self.node


def parse_until(raw: str, now: datetime | None = None) -> datetime:
    """``+30m``/``+2h``/``+1d`` depuis *now*, ou un instant ISO 8601 avec fuseau ; toujours dans le futur."""
    moment = now or _now()
    text = raw.strip()
    relative = _RELATIVE.match(text)
    if relative is not None:
        amount, unit = int(relative.group(1)), relative.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        until = moment + delta
    else:
        parsed = _parse(text.replace("Z", "+00:00") if text.endswith("Z") else text)
        if parsed is None:
            raise GrimoireRuntimeError(
                f"--until invalide : {raw!r} (attendu +<n>m, +<n>h, +<n>d ou un instant ISO 8601 avec fuseau)"
            )
        until = parsed
    if until <= moment:
        raise GrimoireRuntimeError(f"--until {raw!r} : l'échéance doit être dans le futur")
    return until


@dataclass(frozen=True, slots=True)
class GrantRequest:
    """Ce que l'humain octroie — validé, et son empreinte (le même octroi relancé retrouve son challenge)."""

    scope: tuple[Selector, ...]
    max_cost_usd: float
    until_raw: str

    @classmethod
    def build(cls, scope: Iterable[str], max_cost_usd: float, until: str) -> GrantRequest:
        selectors = tuple(sorted({Selector.parse(s) for s in scope}, key=str))
        if not selectors:
            raise GrimoireRuntimeError("--scope est obligatoire : un octroi sans périmètre n'est pas un octroi")
        if isinstance(max_cost_usd, bool) or not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise GrimoireRuntimeError(f"--max-cost-usd doit être un montant fini > 0 (reçu {max_cost_usd!r})")
        parse_until(until)  # validé dès la demande, recalculé à l'inscription
        return cls(selectors, float(max_cost_usd), until.strip())

    @property
    def sha256(self) -> str:
        canon = json.dumps(
            {"scope": [str(s) for s in self.scope], "max_cost_usd": self.max_cost_usd, "until": self.until_raw},
            sort_keys=True,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GrantState:
    """Un octroi relu : son entrée, son état, son coût mesuré et, s'il est révoqué, pourquoi."""

    grant: Mapping[str, Any]
    revoked: str = ""
    revoked_detail: str = ""
    spent: Cost = field(default_factory=Cost.none)

    @property
    def grant_id(self) -> str:
        return str(self.grant.get("grant_id", ""))

    @property
    def active(self) -> bool:
        return not self.revoked

    @property
    def scope(self) -> tuple[Selector, ...]:
        out: list[Selector] = []
        for raw in self.grant.get("scope") or []:
            try:
                out.append(Selector.parse(str(raw)))
            except GrimoireRuntimeError:
                continue
        return tuple(out)

    def explain(self) -> str:
        text = _REVOKE_TEXT.get(self.revoked, self.revoked)
        return text + (f" — {self.revoked_detail}" if self.revoked_detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "state": "active" if self.active else "revoked",
            "revoked": self.revoked,
            "revoked_detail": self.revoked_detail,
            "scope": [str(s) for s in self.scope],
            "max_cost_usd": self.grant.get("max_cost_usd"),
            "until": self.grant.get("until"),
            "granted_at": self.grant.get("ts"),
            "attestation": self.grant.get("attestation"),
            "session_id": self.grant.get("session_id"),
            "spent": self.spent.to_dict(),
            "cost_scope": COST_SCOPE,
        }


class _Runs:
    """Les métadonnées de run du dossier de flows, lues une fois : lignée et nodes de chaque run."""

    def __init__(self, flows_root: Path) -> None:
        self._metas: dict[str, dict[str, Any]] = {}
        for path in sorted(flows_root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("run_id"), str):
                self._metas[data["run_id"]] = data

    def lineage(self, run_id: str) -> list[tuple[str, str]]:
        chain: list[tuple[str, str]] = []
        current: str | None = run_id
        while current and len(chain) < _MAX_LINEAGE and all(current != r for r, _ in chain):
            meta = self._metas.get(current, {})
            chain.append((current, str(meta.get("blueprint_id", ""))))
            parent = meta.get("parent_run_id")
            current = parent if isinstance(parent, str) else None
        return chain

    def covered(self, scope: Sequence[Selector]) -> dict[str, tuple[str, ...]]:
        """Les runs que *scope* couvre (au niveau du run, restriction de node ignorée) → leurs nodes."""
        out: dict[str, tuple[str, ...]] = {}
        for run_id, meta in self._metas.items():
            lineage = self.lineage(run_id)
            if any(s.covers_lineage(lineage) for s in scope):
                out[run_id] = tuple(str(n) for n in meta.get("order") or [])
        return out


def _measure(project_root: Path, runs: Mapping[str, Sequence[str]], since: datetime) -> tuple[Cost, str]:
    """Coût des tentatives de dispatch des *runs* depuis *since*, et le premier gate rouge (``""`` sinon).

    Un ledger présent mais illisible rend un coût inconnu : fail-closed, il révoque.
    """
    events_path = project_root / _LEDGER_RELPATH / "events.jsonl"
    if not runs or not events_path.is_file():
        return Cost.none(), ""
    tasks = {f"FLOW-{run}-{node}": (run, node) for run, nodes in runs.items() for node in nodes}
    try:
        from grimoire.missions.ledger import MissionLedger

        events = MissionLedger(project_root / _LEDGER_RELPATH).list_events()
    except Exception as exc:
        return Cost.unpriced(), f"ledger illisible ({type(exc).__name__})"
    total = Cost.none()
    red = ""
    for event in events:
        if event.event_type != "task.dispatched" or event.entity_id not in tasks:
            continue
        when = _parse(event.created_at)
        if when is not None and when <= since:
            continue
        # Un horodatage illisible n'exclut rien : la tentative compte (fail-closed).
        total = total + Cost.coerce(event.payload.get("cost_usd"))
        verdict = str(event.payload.get("verdict", ""))
        if not red and verdict in _RED_VERDICTS:
            run, node = tasks[event.entity_id]
            red = f"dispatch {verdict} sur {run}/{node}"
    return total, red


class GrantJournal:
    """Challenges, octrois et révocations — ajout seul, sous verrou, un journal par dossier de flows."""

    def __init__(self, flows_root: Path, project_root: Path) -> None:
        self._flows_root = flows_root
        self._project_root = project_root
        self.path = flows_root / "authority" / GRANTS_FILENAME
        #: Porte le registre commun des tours consommés (:mod:`.attestation`).
        self._turns = CheckpointJournal(flows_root, "autonomy-grants")

    def entries(self) -> list[dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        out: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def _append(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with locked(self.path), self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")
        return entry

    # ── Octroi ───────────────────────────────────────────────────────────────

    def _open_challenge(self, request_sha: str, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
        """Le dernier challenge de cette demande **émis par la session courante**, s'il est encore ouvert.

        Un challenge sans session, ou d'une autre session, ne peut jamais être
        attesté par celle-ci (``no_session``/``other_session``, sans être consommé) :
        le réutiliser bloquerait la demande pour toujours. On en émet un nouveau.
        """
        entries = self.entries()
        used = {e.get("challenge_id") for e in entries if e.get("type") == "grant"}
        session_id, _host = current_session(env)
        for entry in reversed(entries):
            if (
                entry.get("type") == "grant_challenge"
                and entry.get("request_sha256") == request_sha
                and entry.get("session_id", "") == session_id
            ):
                return None if entry.get("challenge_id") in used else entry
        return None

    def request(
        self, request: GrantRequest, env: Mapping[str, str] | None = None, now: datetime | None = None
    ) -> tuple[dict[str, Any] | None, Attestation]:
        """Inscrit l'octroi si la demande est attestée ; sinon émet (ou garde ouvert) son challenge.

        Rend ``(octroi, attestation)`` — ``octroi`` à ``None`` tant que la
        demande n'est pas attestée. La même demande relancée retrouve son
        challenge ; un refus ne le consomme pas.
        """
        challenge = self._open_challenge(request.sha256, env)
        if challenge is None:
            session_id, _host = current_session(env)
            challenge = self._append(
                {
                    "type": "grant_challenge",
                    "challenge_id": f"GCH-{uuid.uuid4().hex[:12]}",
                    "request_sha256": request.sha256,
                    "scope": [str(s) for s in request.scope],
                    "max_cost_usd": request.max_cost_usd,
                    "until": request.until_raw,
                    "session_id": session_id,
                    "ts": (now or _now()).isoformat(),
                }
            )
        attestation = attest(self._project_root, challenge, env, self._turns.consumed_turns())
        if not attestation.attested:
            return None, attestation
        moment = now or _now()
        grant_id = f"GRT-{uuid.uuid4().hex[:12]}"
        self._turns.consume_turn(grant_id, attestation)
        grant = self._append(
            {
                "type": "grant",
                "grant_id": grant_id,
                "challenge_id": attestation.challenge_id,
                "request_sha256": request.sha256,
                "scope": [str(s) for s in request.scope],
                "max_cost_usd": request.max_cost_usd,
                "until": parse_until(request.until_raw, moment).isoformat(),
                "cost_policy": POLICY_STOP,
                "attestation": attestation.status,
                "session_id": attestation.session_id,
                "turn_ts": attestation.turn_ts,
                "ts": moment.isoformat(),
            }
        )
        return grant, attestation

    # ── Révocation et état ───────────────────────────────────────────────────

    def revoke(self, grant_id: str, reason: str, detail: str = "", now: datetime | None = None) -> dict[str, Any]:
        return self._append(
            {
                "type": "revoke",
                "grant_id": grant_id,
                "reason": reason,
                "detail": detail,
                "ts": (now or _now()).isoformat(),
            }
        )

    def states(self, now: datetime | None = None) -> list[GrantState]:
        """Chaque octroi, réévalué maintenant ; une révocation constatée ici est écrite (une fois)."""
        entries = self.entries()
        grants = [e for e in entries if e.get("type") == "grant" and e.get("grant_id")]
        if not grants:
            return []
        revoked = {str(e.get("grant_id")): e for e in entries if e.get("type") == "revoke"}
        moment = now or _now()
        runs = _Runs(self._flows_root)
        out: list[GrantState] = []
        for grant in grants:
            gid = str(grant["grant_id"])
            state = self._evaluate(grant, revoked.get(gid), runs, moment)
            if state.revoked and gid not in revoked:
                self.revoke(gid, state.revoked, state.revoked_detail, moment)
            out.append(state)
        return out

    def _evaluate(
        self, grant: Mapping[str, Any], revocation: Mapping[str, Any] | None, runs: _Runs, now: datetime
    ) -> GrantState:
        since = _parse(grant.get("ts"))
        scope = GrantState(grant).scope
        spent, red = (
            _measure(self._project_root, runs.covered(scope), since) if since is not None else (Cost.unpriced(), "")
        )
        if revocation is not None:
            return GrantState(grant, str(revocation.get("reason", "")), str(revocation.get("detail", "")), spent)
        until = _parse(grant.get("until"))
        cap = grant.get("max_cost_usd")
        if since is None or until is None or not scope or not isinstance(cap, int | float) or isinstance(cap, bool):
            return GrantState(grant, REVOKE_EXPIRED, "octroi illisible (ts, until, scope ou plafond)", spent)
        if now >= until:
            return GrantState(grant, REVOKE_EXPIRED, f"échéance {until.isoformat()}", spent)
        if red:
            return GrantState(grant, REVOKE_GATE_RED, red, spent)
        reason = cap_reason(spent, float(cap), POLICY_STOP)
        if reason is not None:
            return GrantState(grant, reason, f"{spent.render()} pour un plafond de {float(cap):.4f} USD", spent)
        return GrantState(grant, spent=spent)

    def for_checkpoint(self, run_id: str, node_id: str, now: datetime | None = None) -> GrantState | None:
        """L'octroi actif le plus récent qui couvre ce checkpoint, sinon le dernier révoqué qui le couvrait."""
        states = self.states(now)
        if not states:
            return None
        lineage = _Runs(self._flows_root).lineage(run_id)
        covering = [s for s in states if any(sel.covers_checkpoint(lineage, node_id) for sel in s.scope)]
        active = [s for s in covering if s.active]
        if active:
            return active[-1]
        return covering[-1] if covering else None

    def revoke_on_red_gate(self, run_id: str, node_id: str, faults: Sequence[str]) -> list[str]:
        """Révoque chaque octroi actif qui couvre le run de *run_id* — rend leurs identifiants."""
        if not self.path.is_file():
            return []
        lineage = _Runs(self._flows_root).lineage(run_id)
        detail = f"sortie refusée sur {run_id}/{node_id} : " + "; ".join(faults)[:400]
        hit: list[str] = []
        for state in self.states():
            if state.active and any(sel.covers_lineage(lineage) for sel in state.scope):
                self.revoke(state.grant_id, REVOKE_GATE_RED, detail)
                hit.append(state.grant_id)
        return hit

    def events(self) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e.get("type") in ("grant_challenge", "grant", "revoke")]


def refusal_note(state: GrantState) -> str:
    """Ce qu'un refus de checkpoint ajoute quand un octroi couvrait ce checkpoint mais ne vaut plus."""
    return (
        f" Octroi {state.grant_id} révoqué ({state.revoked} : {state.explain()}) : décision humaine requise "
        "— un tour attesté, ou un nouvel octroi (`grimoire autonomy grant`)."
    )
