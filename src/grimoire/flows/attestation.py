"""Décision humaine attestée sur un node ``kind: "checkpoint"`` (W2-01).

Avant ce module, :func:`grimoire.flows.engine.checkpoint_decision_faults` ne
lisait que la *valeur* de ``checkpoint_decision`` : n'importe quel JSON
portant ``"approve"`` franchissait un checkpoint, qu'un humain l'ait décidé
ou que l'agent l'ait écrit seul. Chaque décision est désormais classée :

- ``attested`` — la session qui a présenté le checkpoint (son *challenge*)
  est celle qui décide, et le journal des tours humains
  (:mod:`grimoire.hosts.decisions.human_turns`) porte un tour ``user`` de
  cette même session, postérieur au challenge ;
- ``unattested`` — tout le reste, avec le motif nommé : CLI nue sans
  session, autre session (une sous-session ``claude -p`` a son propre
  ``session_id``), ``GRIMOIRE_UNATTENDED=1``, aucun tour depuis le
  challenge, ou aucun hook de prompt branché (``human_authority_unprovable``).

Décision 2 du plan : un checkpoint déclaré destructif
(``config.checkpoint.destructive: true``) refuse ``unattested`` ; un
checkpoint ordinaire l'accepte avec un avertissement nommé, pendant une
release, avant de le refuser à son tour.

Le journal ``<flows>/authority/<run>.authority.jsonl`` porte les challenges
et les décisions (motif d'``amend`` compris, relu par ``flow status``). Un
challenge reste ouvert jusqu'à la décision qui le consomme, et un refus faute
d'attestation ne le consomme pas. Un tour humain ne sert qu'une décision *au
total*, pas seulement par run : toute décision attestée inscrit le tour qui
l'atteste (``session_id``, ``turn_ts``, empreinte) dans un registre commun à
tous les runs du dossier (``@consumed-turns.authority.jsonl``, sous verrou), et
:func:`attest` ignore un tour déjà inscrit. Le nom du registre ne peut pas
être celui d'un run (``@`` n'est pas dans les identifiants admis).
Plafond d'amendements (W2-10) : ``amend`` rouvre le node (une révision est
demandée, le run n'avance pas) et est compté dans ce même journal. Tant que le
node n'a pas reçu N amendements (``config.checkpoint.max_amendments``, 3 par
défaut), ``accept_as_is`` est refusé ; ensuite il fait avancer le run, exige le
risque nommé (``checkpoint_reason``) et inscrit ``accepted_risk`` (nombre
d'amendements, plafond, motif) dans la décision. Limite : le compteur est celui
du journal du run, y compris des ``amend`` non attestés — il borne la
persévérance, il ne prouve pas que N humains ont relu ; l'attestation de
``accept_as_is`` reste, elle, exigée comme celle de ``approve``.
Plan de retrait (W2-11) : un node ``checkpoint`` peut déclarer
``config.checkpoint.plan`` (un fournisseur de la liste projetée de ce que la
décision engage, ``PLAN_PROVIDERS``). La liste est hachée à la présentation et
inscrite dans le challenge ; à la décision elle est recalculée. Un écart (ou
une projection impossible, d'un côté ou de l'autre) refuse ``approve`` et
``accept_as_is`` en ``plan_drifted``, nomme les écarts, consomme le challenge
sans consommer le tour humain, et la présentation suivante en émet un neuf
sur le plan courant : il faut un nouveau tour humain. Limite : l'empreinte
couvre la projection du fournisseur, pas ce qu'un exécuteur ferait réellement
après la décision ; ``reject`` et ``amend`` n'avancent pas, donc n'y sont pas soumis.
Octroi d'autonomie (W5-06, :mod:`.autonomy`) : une décision ``approve`` ou
``accept_as_is`` non attestée, sur un checkpoint que couvre un octroi valide,
est acceptée avec le statut ``granted:<grant_id>`` — jamais ``attested`` :
aucun humain n'a vu ce checkpoint. Un octroi révoqué qui le couvrait est nommé
dans le refus.
Source d'attestation désignée (``--attest-from``, #876) : le journal des tours
n'est écrit que par le hook du projet où tourne la session. Un octroi
(``autonomy grant``, ``autonomy mandate grant``) demandé sur un projet *cible*
depuis la session d'un autre projet ne trouvait donc jamais de tour
(``human_authority_unprovable``). :func:`attest_grant` lit alors les tours dans
le journal du projet *source* que l'appelant désigne, et seulement lui (aucune
découverte par cwd ni par l'environnement) ; les autres règles ne changent pas.
Le tour est cherché hors du registre des tours consommés de la source **et** de
la cible, puis consommé dans celui de la **source** : le registre suit le
journal qu'il protège, si bien qu'un même tour ne sert qu'une fois, qu'il soit
utilisé depuis la source elle-même ou depuis n'importe quelle cible.
Registre de l'utilisateur : un registre lié à un chemin ne voit pas la *copie*
du journal (la garde d'autorité refuse d'écrire le journal par son nom, pas de
copier un dossier entier). Toute consommation, avec ou sans ``--attest-from``,
est donc aussi inscrite dans un registre indépendant du chemin, sous la zone de
contrôle de l'utilisateur (``~/.grimoire/control/``, :func:`user_consumed_path`),
clé ``(session_id, ts, prompt_sha256)`` ; toute attestation le consulte en plus
du registre du projet. :meth:`CheckpointJournal.consume_turn` revérifie la clé
sous le verrou de ce registre et refuse un tour déjà consommé
(:class:`TurnAlreadyConsumedError`) : deux décisions concurrentes ne partagent
plus un tour.
Python seul : aucune de ces décisions ne traverse ``grimoire_flows_core``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.core.fsio import locked
from grimoire.core.standard_state import current_session
from grimoire.hosts.decisions.authority_guard import AUTHORITY_SUFFIX, LIMIT_NOTE
from grimoire.hosts.decisions.human_turns import ORIGIN_USER, TURNS_RELPATH, is_unattended, load_turns, turns_path

__all__ = [
    "ATTESTED",
    "ATTEST_FROM_HINT",
    "CONSUMED_TURNS_FILENAME",
    "DEFAULT_MAX_AMENDMENTS",
    "FLOWS_RELPATH",
    "GRANTED_PREFIX",
    "NO_HUMAN_TURN",
    "PLAN_DRIFTED",
    "PLAN_PROVIDERS",
    "UNATTESTED",
    "USER_CONSUMED_TURNS_FILENAME",
    "Attestation",
    "CheckpointGate",
    "CheckpointJournal",
    "TurnAlreadyConsumedError",
    "attest",
    "attest_grant",
    "destructive_checkpoints",
    "judge_checkpoint",
    "max_amendments",
    "plan_fingerprint",
    "plan_providers",
    "project_plan",
    "refusal_text",
    "turns_source",
    "user_consumed_path",
    "warning_text",
]

#: Nombre d'amendements après lequel ``accept_as_is`` devient possible (W2-10).
DEFAULT_MAX_AMENDMENTS = 3

ATTESTED = "attested"
UNATTESTED = "unattested"
#: Statut d'une décision prise sous octroi d'autonomie (W5-06) : ``granted:<grant_id>``.
GRANTED_PREFIX = "granted:"
#: Ce que l'octroi qui couvre un checkpoint dit de lui (W5-06) : ``(grant_id, texte de révocation)``,
#: texte vide si l'octroi est valide, ``None`` si aucun octroi ne couvre ce checkpoint.
GrantLookup = Callable[[], tuple[str, str] | None]

NO_HUMAN_TURN = "no_human_turn_since_challenge"
PLAN_DRIFTED = "plan_drifted"
#: Les fournisseurs de plan que ``config.checkpoint.plan`` peut nommer (W2-11).
PLAN_PROVIDERS: tuple[str, ...] = ("upgrade-removals",)
_REASONS: dict[str, str] = {
    "unattended": "GRIMOIRE_UNATTENDED=1 : exécution déclarée sans humain",
    "no_challenge": "aucun challenge émis pour ce checkpoint",
    "no_session": "checkpoint présenté hors session d'hôte (CLI nue) : aucune session à lier",
    "other_session": "la décision vient d'une autre session que celle qui a présenté le checkpoint",
    "human_authority_unprovable": "aucun hook de prompt n'a jamais écrit le journal des tours",
    NO_HUMAN_TURN: "aucun tour humain de cette session depuis la présentation du checkpoint",
    PLAN_DRIFTED: "le plan projeté a changé depuis la présentation du checkpoint",
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
#: Registre des tours humains déjà consommés par une décision attestée, commun
#: à tous les runs. Suffixe ``.authority.jsonl`` : la garde d'écriture le couvre.
CONSUMED_TURNS_FILENAME = "@consumed-turns" + AUTHORITY_SUFFIX
#: Registre de l'utilisateur, indépendant du chemin du journal (zone de contrôle, ``~/.grimoire/control/``).
#: Sans ``@`` : son nom ne contient pas celui du registre d'un projet.
USER_CONSUMED_TURNS_FILENAME = "consumed-turns" + AUTHORITY_SUFFIX
#: Identité d'un tour : ``(session_id, ts, prompt_sha256)``.
TurnKey = tuple[str, str, str]


class TurnAlreadyConsumedError(GrimoireRuntimeError):
    """Le tour qu'une attestation a retenu vient d'être consommé par une autre décision (course)."""


def user_consumed_path() -> Path:
    """Le registre des tours consommés de l'utilisateur : ``~/.grimoire/control/``, zone que la garde refuse aux agents."""
    return Path.home() / ".grimoire" / "control" / USER_CONSUMED_TURNS_FILENAME


def _turn_keys(path: Path) -> frozenset[TurnKey]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    out: set[TurnKey] = set()
    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            out.add((str(data.get("session_id", "")), str(data.get("turn_ts", "")), str(data.get("turn_sha256", ""))))
    return frozenset(out)


#: Le dossier des flows d'un projet, relatif à sa racine : il porte le registre des tours consommés.
FLOWS_RELPATH = Path("_grimoire-runtime-output") / "flows"
#: Ce que dit un octroi refusé en ``human_authority_unprovable`` sans ``--attest-from``.
ATTEST_FROM_HINT = (
    "Si ta session tourne dans un autre projet que la cible, son hook de prompt écrit les tours là-bas : "
    "relance avec --attest-from <racine du projet de ta session>."
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True, slots=True)
class Attestation:
    """Le classement d'une décision, et pourquoi."""

    status: str
    reason: str = ""
    challenge_id: str = ""
    session_id: str = ""
    turn_ts: str = ""
    turn_sha256: str = ""
    #: Le projet dont le journal des tours a servi (``--attest-from``) ; vide : le projet cible.
    source: str = ""

    @property
    def attested(self) -> bool:
        return self.status == ATTESTED

    @property
    def granted(self) -> bool:
        return self.status.startswith(GRANTED_PREFIX)

    def explain(self) -> str:
        return _REASONS.get(self.reason, self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "challenge_id": self.challenge_id,
            "session_id": self.session_id,
            "turn_ts": self.turn_ts,
            **({"source": self.source} if self.source else {}),
        }


def refusal_text(attestation: Attestation) -> str:
    """Le défaut d'un checkpoint destructif refusé — ``awaiting_human``, la cause, le remède, la limite."""
    return (
        f"awaiting_human : {attestation.reason} — {attestation.explain()}. "
        "Checkpoint destructif : la décision doit être attestée (un tour humain de la session qui l'a "
        "présenté, postérieur au challenge, puis la même décision renvoyée depuis cette session). "
        f"{LIMIT_NOTE} Le jeton cockpit (W1-10) sera le canal le plus fort."
    )


def warning_text(attestation: Attestation) -> str:
    return (
        f"décision unattested ({attestation.reason} — {attestation.explain()}) : acceptée sur ce checkpoint "
        "non destructif, refusée à partir de la prochaine release."
    )


def destructive_checkpoints(blueprint: Mapping[str, Any]) -> frozenset[str]:
    """Les nodes ``checkpoint`` que le blueprint déclare destructifs (``config.checkpoint.destructive``)."""
    out: set[str] = set()
    for node in blueprint.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "checkpoint":
            continue
        config = (node.get("config") or {}).get("checkpoint") or {}
        if isinstance(config, dict) and config.get("destructive") is True:
            out.add(str(node.get("id")))
    return frozenset(out)


def max_amendments(blueprint: Mapping[str, Any]) -> dict[str, int]:
    """Le plafond d'amendements de chaque node ``checkpoint`` qui en déclare un (``config.checkpoint.max_amendments``)."""
    out: dict[str, int] = {}
    for node in blueprint.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "checkpoint":
            continue
        config = (node.get("config") or {}).get("checkpoint") or {}
        cap = config.get("max_amendments") if isinstance(config, dict) else None
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1:
            out[str(node.get("id"))] = cap
    return out


def plan_providers(blueprint: Mapping[str, Any]) -> dict[str, str]:
    """Le fournisseur de plan de chaque node ``checkpoint`` qui en déclare un (``config.checkpoint.plan``, W2-11)."""
    out: dict[str, str] = {}
    for node in blueprint.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "checkpoint":
            continue
        config = (node.get("config") or {}).get("checkpoint") or {}
        provider = config.get("plan") if isinstance(config, dict) else None
        if isinstance(provider, str) and provider in PLAN_PROVIDERS:
            out[str(node.get("id"))] = provider
    return out


def plan_fingerprint(items: Collection[str]) -> str:
    """L'empreinte d'une liste projetée : insensible à l'ordre et aux doublons, ASCII pur avant hachage."""
    canonical = json.dumps(sorted(set(items)), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _projected_items(provider: str, project_root: Path) -> list[str]:
    if provider == "upgrade-removals":
        from grimoire.tools import project_upgrade

        return list(project_upgrade.projected_removals(project_root))
    raise ValueError(f"fournisseur de plan inconnu : {provider!r}")


def project_plan(provider: str, project_root: Path) -> dict[str, Any]:
    """La liste projetée par *provider* et son empreinte ; une panne est inscrite (``error``, empreinte vide), jamais avalée."""
    try:
        items = sorted(set(_projected_items(provider, project_root)))
    except Exception as exc:  # la panne devient le plan : rien ne peut alors être approuvé
        return {"provider": provider, "sha256": "", "items": [], "error": f"{type(exc).__name__}: {exc}"}
    return {"provider": provider, "sha256": plan_fingerprint(items), "items": items}


def _listing(items: Collection[str], limit: int = 20) -> str:
    shown = sorted(items)
    more = f" … +{len(shown) - limit}" if len(shown) > limit else ""
    return ", ".join(shown[:limit]) + more


def _plan_drift(baseline: object, current: Mapping[str, Any]) -> str:
    """Le motif de ``plan_drifted`` (écarts nommés), ou ``""`` si le plan courant est celui qui a été présenté."""
    error = current.get("error")
    if error:
        return f"projection impossible à la décision ({error})"
    base: Mapping[str, Any] = baseline if isinstance(baseline, Mapping) else {}
    base_sha = str(base.get("sha256") or "")
    if not base_sha:
        return "aucun plan lié à la présentation du checkpoint (projection impossible alors, ou challenge antérieur au plan)"
    if base_sha == current.get("sha256"):
        return ""
    before = {str(i) for i in base.get("items") or []}
    now = {str(i) for i in current.get("items") or []}
    parts = []
    if now - before:
        parts.append(f"ajoutés depuis la présentation : {_listing(now - before)}")
    if before - now:
        parts.append(f"retirés depuis la présentation : {_listing(before - now)}")
    return "; ".join(parts) or "empreinte différente"


class CheckpointJournal:
    """Challenges et décisions de checkpoint d'un run — ajout seul, sous verrou."""

    def __init__(self, flows_root: Path, run_id: str) -> None:
        safe = run_id if _SAFE_ID.match(run_id) else "h-" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        self.path = flows_root / "authority" / f"{safe}{AUTHORITY_SUFFIX}"

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

    def _append(self, entry: dict[str, Any]) -> None:
        with locked(self.path), self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def open_challenge(self, node_id: str, session_id: str | None = None) -> dict[str, Any] | None:
        """Le dernier challenge de *node_id* qu'aucune décision n'a encore consommé.

        Avec *session_id*, le challenge émis par cette session est préféré à un
        challenge plus récent d'une autre session (ou sans session) : une session
        ne peut attester que le sien. À défaut, le dernier challenge du node, que
        ``attest`` refusera avec son motif (``no_session``, ``other_session``).
        Les challenges antérieurs à la dernière décision ``reject`` ou ``amend``
        du node sont périmés, quelle que soit leur session.
        """
        entries = self.entries()
        consumed = {e.get("challenge_id") for e in entries if e.get("type") == "decision"}
        # Un reject ou un amend ferme tout ce que le node avait d'ouvert, quelle
        # que soit la session du challenge : le tour humain qui précède cette
        # décision ne peut plus attester la version qui la suit. Un approve
        # refusé ne consomme que le sien (voir record_decision).
        start = 0
        for i, e in enumerate(entries):
            if e.get("type") == "decision" and e.get("node_id") == node_id and e.get("decision") in ("reject", "amend"):
                start = i + 1
        challenges = [
            e for e in entries[start:] if e.get("type") == "challenge" and e.get("node_id") == node_id
        ]
        if session_id is not None:
            own = [e for e in challenges if e.get("session_id", "") == session_id]
            if own:
                challenges = own
        if not challenges:
            return None
        last = challenges[-1]
        return None if last.get("challenge_id") in consumed else last

    def issue_challenge(
        self,
        node_id: str,
        env: Mapping[str, str] | None = None,
        plan: Mapping[str, Any] | None = None,
        bound: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Émet le challenge de *node_id*, lié à la session courante — ou rend celui qu'elle a encore ouvert.

        Un challenge ouvert sans session, ou par une autre session, ne vaut rien
        pour celle-ci : le rendre figerait le checkpoint (``no_session`` ou
        ``other_session`` à chaque décision, sans que le refus le consomme).

        *plan* (W2-11) : le plan projeté à la présentation (:func:`project_plan`), inscrit tel quel.
        *bound* (W5-05) : des valeurs que le challenge porte (l'empreinte d'une vague). Un
        challenge ouvert qui ne porte pas les mêmes valeurs est périmé : il n'est pas rendu,
        un nouveau est émis, et son horodatage exige un tour humain postérieur.
        """
        session_id, _host = current_session(env)
        existing = self.open_challenge(node_id, session_id)
        if (
            existing is not None
            and existing.get("session_id", "") == session_id
            and all(existing.get(k) == v for k, v in (bound or {}).items())
        ):
            return existing
        entry = {
            **(bound or {}),
            "type": "challenge",
            "challenge_id": f"CHL-{uuid.uuid4().hex[:12]}",
            "node_id": node_id,
            "session_id": session_id,
            "ts": _now(),
            **({"plan": dict(plan)} if plan is not None else {}),
        }
        self._append(entry)
        return entry

    @property
    def consumed_path(self) -> Path:
        return self.path.parent / CONSUMED_TURNS_FILENAME

    def consumed_turns(self) -> frozenset[TurnKey]:
        """Les tours qu'une décision attestée de n'importe quel run de ce dossier a déjà consommés.

        Le registre de l'utilisateur n'y est pas : :func:`attest` le lit lui-même, pour tout appelant.
        """
        return _turn_keys(self.consumed_path)

    def consume_turn(self, node_id: str, attestation: Attestation, *, for_project: str = "") -> None:
        """Inscrit le tour qui atteste *attestation* au registre de ce dossier et à celui de l'utilisateur.

        Sous le verrou du registre de l'utilisateur, le tour est revérifié : déjà consommé (une décision
        concurrente l'a pris entre l'attestation et maintenant), :class:`TurnAlreadyConsumedError`, rien n'est
        écrit. Le registre du dossier est écrit d'abord : s'il ne s'écrit pas, rien n'est consommé ; si celui de
        l'utilisateur échoue ensuite, le tour reste consommé ici (échec fermé, il n'attestera plus rien).
        *for_project* : le projet cible quand le tour est consommé dans le registre de la source (``--attest-from``).
        """
        key = (attestation.session_id, attestation.turn_ts, attestation.turn_sha256)
        user = user_consumed_path()
        user.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with locked(user):
            if key in _turn_keys(user) or key in _turn_keys(self.consumed_path):
                raise TurnAlreadyConsumedError(
                    f"tour humain du {attestation.turn_ts} (session {attestation.session_id}) déjà consommé par une "
                    "autre décision : il n'atteste plus rien, un nouveau tour humain est nécessaire"
                )
            self._write_consumed(node_id, attestation, for_project)
            with user.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "session_id": attestation.session_id,
                            "turn_ts": attestation.turn_ts,
                            "turn_sha256": attestation.turn_sha256,
                            "challenge_id": attestation.challenge_id,
                            "node_id": node_id,
                            "registry": str(self.consumed_path),
                            "ts": _now(),
                        },
                        ensure_ascii=True,
                    )
                    + "\n"
                )

    def _write_consumed(self, node_id: str, attestation: Attestation, for_project: str) -> None:
        entry = {
            "session_id": attestation.session_id,
            "turn_ts": attestation.turn_ts,
            "turn_sha256": attestation.turn_sha256,
            "challenge_id": attestation.challenge_id,
            "node_id": node_id,
            "run": self.path.name,
            **({"for_project": for_project} if for_project else {}),
            "ts": _now(),
        }
        with locked(self.consumed_path), self.consumed_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def amendment_count(self, node_id: str) -> int:
        """Les ``amend`` déjà inscrits pour *node_id* dans ce run."""
        return sum(
            1
            for e in self.entries()
            if e.get("type") == "decision" and e.get("node_id") == node_id and e.get("decision") == "amend"
        )

    def record_decision(
        self,
        node_id: str,
        decision: str,
        reason: str,
        attestation: Attestation,
        accepted_risk: Mapping[str, Any] | None = None,
        bound: Mapping[str, str] | None = None,
    ) -> None:
        if attestation.attested:
            self.consume_turn(node_id, attestation)
        extra = {"accepted_risk": dict(accepted_risk)} if accepted_risk else {}
        self._append(
            {
                **extra,
                **(bound or {}),
                "type": "decision",
                "challenge_id": attestation.challenge_id,
                "node_id": node_id,
                "decision": decision,
                "reason": reason,
                "attestation": attestation.status,
                "attestation_reason": attestation.reason,
                "session_id": attestation.session_id,
                "turn_ts": attestation.turn_ts,
                "ts": _now(),
            }
        )

    def decisions(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **{k: e.get(k, "") for k in ("node_id", "decision", "reason", "attestation", "attestation_reason", "ts")},
                **({"accepted_risk": e["accepted_risk"]} if isinstance(e.get("accepted_risk"), dict) else {}),
            }
            for e in self.entries()
            if e.get("type") == "decision"
        )


def attest(
    project_root: Path,
    challenge: Mapping[str, Any] | None,
    env: Mapping[str, str] | None = None,
    consumed: Collection[TurnKey] = (),
    *,
    turns_root: Path | None = None,
) -> Attestation:
    """Classe la décision soumise maintenant contre *challenge*.

    *consumed* : les tours qu'une décision attestée a déjà utilisés (registre
    commun à tous les runs, :meth:`CheckpointJournal.consumed_turns`) — un tour
    ne sert qu'une décision. Le registre de l'utilisateur (:func:`user_consumed_path`)
    est toujours consulté en plus, quel que soit l'appelant.
    *turns_root* : le projet dont le journal des tours est lu (``--attest-from``, voir
    :func:`attest_grant`) ; ``None`` : *project_root*, comme toujours.
    """
    decider, _host = current_session(env)
    challenge_id = str((challenge or {}).get("challenge_id", ""))
    source = str(turns_root) if turns_root is not None else ""

    def _no(reason: str) -> Attestation:
        return Attestation(UNATTESTED, reason, challenge_id, decider, source=source)

    if is_unattended(env):
        return _no("unattended")
    if challenge is None:
        return _no("no_challenge")
    owner = str(challenge.get("session_id", ""))
    if not owner:
        return _no("no_session")
    if decider != owner:
        return _no("other_session")
    turns = load_turns(project_root if turns_root is None else turns_root)
    if turns is None:
        return _no("human_authority_unprovable")
    since = _parse(str(challenge.get("ts", "")))
    if since is None:
        return _no("no_challenge")
    consumed = frozenset(consumed) | _turn_keys(user_consumed_path())
    for turn in reversed(turns):
        when = _parse(turn.ts)
        if (
            turn.session_id == owner
            and turn.origin == ORIGIN_USER
            and when is not None
            and when > since
            and (turn.session_id, turn.ts, turn.prompt_sha256) not in consumed
        ):
            return Attestation(ATTESTED, "", challenge_id, decider, turn.ts, turn.prompt_sha256, source)
    return _no(NO_HUMAN_TURN)


def turns_source(raw: Path) -> Path:
    """Le projet que ``--attest-from`` désigne, résolu (liens compris) ; refus nommé si son journal ne se lit pas.

    Seul l'appelant désigne la source : ni le dossier courant ni l'environnement ne sont consultés.
    """
    root = Path(raw).resolve()
    if not root.is_dir():
        raise GrimoireRuntimeError(
            f"--attest-from {raw} : dossier introuvable (racine du projet de ta session attendue)"
        )
    try:
        turns_path(root).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GrimoireRuntimeError(
            f"--attest-from {root} : aucun journal des tours humains ({TURNS_RELPATH.as_posix()}) — aucun hook de "
            "prompt n'y a jamais écrit ; désigne la racine du projet où tourne ta session"
        ) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise GrimoireRuntimeError(
            f"--attest-from {root} : journal des tours humains illisible ({type(exc).__name__}) ; rien ne peut en être attesté"
        ) from exc
    return root


def attest_grant(
    project_root: Path,
    registry: CheckpointJournal,
    challenge: Mapping[str, Any] | None,
    env: Mapping[str, str] | None = None,
    source: Path | None = None,
) -> tuple[Attestation, CheckpointJournal]:
    """L'attestation d'un octroi, et le registre où consommer son tour (``autonomy grant``, ``autonomy mandate grant``).

    Sans *source* (ou si elle est la cible elle-même) : :func:`attest` sur le journal de *project_root* contre
    *registry*, exactement comme avant ``--attest-from``.

    Avec *source* (déjà validée par :func:`turns_source`) : les tours sont lus dans le journal de *source* ;
    même session, tour ``user`` postérieur au challenge, comme toujours ; le tour doit être absent du registre
    de la source, de *registry* et de celui de l'utilisateur (:func:`attest`) ; il sera consommé dans celui de
    la **source**, que l'appelant reçoit (et dans celui de l'utilisateur, :meth:`CheckpointJournal.consume_turn`).
    Pourquoi la source : le registre doit suivre le journal qu'il protège. Le tenir dans la cible laisserait
    le même tour servir une fois par cible, et une fois encore dans la source elle-même.
    """
    if source is None or source == project_root.resolve():
        return attest(project_root, challenge, env, registry.consumed_turns()), registry
    run = registry.path.name.removesuffix(AUTHORITY_SUFFIX)
    own = CheckpointJournal(source / FLOWS_RELPATH, run)
    consumed = registry.consumed_turns() | own.consumed_turns()
    return attest(project_root, challenge, env, consumed, turns_root=source), own


#: Les décisions qui font avancer un run : seules elles exigent une attestation.
#: ``amend`` rouvre le node (W2-10) : il n'avance plus, donc n'en exige pas.
_ADVANCING = ("approve", "accept_as_is")


@dataclass(frozen=True, slots=True)
class CheckpointGate:
    """Ce que la décision soumise vaut : classement, refus éventuel, avertissement éventuel."""

    decision: str
    reason: str
    attestation: Attestation
    refusal: str = ""
    warning: str = ""
    #: ``accept_as_is`` accepté (W2-10) : amendements, plafond, motif.
    accepted_risk: Mapping[str, Any] | None = None
    #: ``approve``/``accept_as_is`` refusé car le plan projeté a changé (W2-11) : à journaliser en ``plan_drifted``.
    drifted: bool = False


def judge_checkpoint(
    journal: CheckpointJournal,
    project_root: Path,
    node_id: str,
    output: Mapping[str, Any],
    *,
    destructive: bool,
    cap: int = DEFAULT_MAX_AMENDMENTS,
    env: Mapping[str, str] | None = None,
    plan: Mapping[str, Any] | None = None,
    grant: GrantLookup | None = None,
) -> CheckpointGate | None:
    """Classe la décision de *output* ; ``None`` si elle n'est pas une décision valide (déjà un défaut nommé).

    ``reject`` est classé mais jamais refusé : il ne fait pas avancer le run.
    ``amend`` rouvre le node (``refusal`` = la révision demandée, ``n/cap``) et
    compte vers *cap*. ``accept_as_is`` est refusé tant que *cap* amendements
    ne sont pas inscrits, puis exige le risque nommé ; accepté, il porte
    ``accepted_risk``. ``approve``/``accept_as_is`` non attestés sont refusés
    sur un checkpoint destructif (``refusal``), acceptés avec ``warning`` ailleurs —
    sauf sous un octroi valide (*grant*, W5-06) : acceptés ``granted:<grant_id>``.
    *grant* n'est consulté que pour une décision qui avance sans attestation.

    *plan* (W2-11) : le plan projeté maintenant, pour un node qui en déclare un.
    Une décision qui avance et dont le plan diffère de celui du challenge ouvert
    est refusée en ``plan_drifted`` avant toute attestation (le tour humain
    n'est pas consommé).
    """
    decision = output.get("checkpoint_decision")
    if decision not in ("approve", "reject", "amend", "accept_as_is"):
        return None
    reason = str(output.get("checkpoint_reason") or "")
    challenge = journal.open_challenge(node_id, current_session(env)[0])
    attestation = attest(project_root, challenge, env, journal.consumed_turns())
    done = journal.amendment_count(node_id)
    if decision == "amend":
        refusal = (
            f"amend : révision demandée ({done + 1}/{cap}), le node reste ouvert"
            + ("" if done + 1 < cap else " ; accept_as_is devient possible")
            + (f" — {reason}" if reason else "")
        )
        return CheckpointGate("amend", reason, attestation, refusal=refusal)
    accepted: dict[str, Any] | None = None
    if decision == "accept_as_is":
        if done < cap:
            refusal = f"accept_as_is refusé : {done}/{cap} amendements, il en faut {cap} avant d'accepter en l'état"
            return CheckpointGate("accept_as_is", reason, attestation, refusal=refusal)
        if not reason.strip():
            refusal = "accept_as_is refusé : 'checkpoint_reason' doit nommer le risque accepté"
            return CheckpointGate("accept_as_is", reason, attestation, refusal=refusal)
        accepted = {"amendments": done, "cap": cap, "reason": reason}
    if plan is not None and decision in _ADVANCING:
        drift = _plan_drift((challenge or {}).get("plan"), plan)
        if drift:
            decider, _host = current_session(env)
            unattested = Attestation(UNATTESTED, PLAN_DRIFTED, attestation.challenge_id, decider)
            refusal = (
                f"{PLAN_DRIFTED} : {drift}. Un nouveau challenge est émis sur le plan courant "
                f"(empreinte {str(plan.get('sha256') or '-')[:12]}) : relire la liste, puis décider après un nouveau tour humain."
            )
            return CheckpointGate(str(decision), reason, unattested, refusal=refusal, drifted=True)
    if decision not in _ADVANCING or attestation.attested:
        return CheckpointGate(str(decision), reason, attestation, accepted_risk=accepted)
    found = grant() if grant is not None else None
    if found is not None and not found[1]:
        granted = Attestation(GRANTED_PREFIX + found[0], "", attestation.challenge_id, attestation.session_id)
        return CheckpointGate(str(decision), reason, granted, accepted_risk=accepted)
    note = f" {found[1]}" if found is not None else ""
    if destructive:
        return CheckpointGate(
            str(decision), reason, attestation, refusal=refusal_text(attestation) + note, accepted_risk=accepted
        )
    return CheckpointGate(
        str(decision), reason, attestation, warning=warning_text(attestation) + note, accepted_risk=accepted
    )
