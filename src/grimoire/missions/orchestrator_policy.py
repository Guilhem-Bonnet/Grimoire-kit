"""Les réglages des rôles de l'orchestrateur, lus dans ``orchestration-policy.yaml`` (#876, lot 2).

Un bloc ``orchestrator:`` du fichier ``_grimoire/standard/orchestration-policy.yaml`` — à côté des ``roles:`` et
du ``wip:`` du standard, que ce module ne lit pas — règle le rôle ``worker`` (le lanceur), le disjoncteur et le
janitor :

.. code-block:: yaml

    orchestrator:
      roles:
        worker:
          enabled: false          # défaut : rien n'est lancé, le comportement actuel est inchangé
          model: ""               # alias ou nom complet ; vide : la route du routeur, sinon le palier
          tier: ""                # cheap | mid | strong (repli quand ni model ni route ne donnent de modèle)
          wip: 1                  # sessions de worker en même temps
          sessions_per_day: 5     # plafond de lancements par jour (UTC)
          permission_mode: ""     # transmis à claude --permission-mode ; vide : réglages de l'hôte
      breaker:
        threshold: 3              # échecs consécutifs avant l'ouverture du disjoncteur
      janitor:
        close_issue: false        # fermer l'issue liée (``Closes #N`` dans la description) une fois fusionnée

Échec fermé : un fichier illisible, une clé inconnue ou une valeur hors type n'est jamais « corrigé » — la lecture
rend l'erreur et le lanceur ne lance rien. Un fichier absent, ou sans bloc ``orchestrator``, vaut le défaut
(rôle désactivé).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.standard_generation import STANDARD_DIR

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_BREAKER_THRESHOLD",
    "ORCHESTRATION_FILE",
    "OrchestratorPolicy",
    "PolicyRead",
    "WorkerRole",
    "load_orchestrator_policy",
]

ORCHESTRATION_FILE = STANDARD_DIR / "orchestration-policy.yaml"
DEFAULT_BREAKER_THRESHOLD = 3
TIERS = ("cheap", "mid", "strong")
_WORKER_KEYS = frozenset({"enabled", "model", "tier", "wip", "sessions_per_day", "permission_mode"})


@dataclass(frozen=True, slots=True)
class WorkerRole:
    """Le rôle ``worker`` : désactivé par défaut, WIP 1 et 5 sessions par jour (valeurs de départ du pilote)."""

    enabled: bool = False
    model: str = ""
    tier: str = ""
    wip: int = 1
    sessions_per_day: int = 5
    permission_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "model": self.model, "tier": self.tier, "wip": self.wip,
            "sessions_per_day": self.sessions_per_day, "permission_mode": self.permission_mode,
        }


@dataclass(frozen=True, slots=True)
class OrchestratorPolicy:
    worker: WorkerRole = field(default_factory=WorkerRole)
    breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD
    close_issue: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"worker": self.worker.to_dict(), "breaker_threshold": self.breaker_threshold, "close_issue": self.close_issue}


@dataclass(frozen=True, slots=True)
class PolicyRead:
    """La lecture : la politique (défaut si le fichier est muet) ou l'erreur qui la rend inutilisable."""

    policy: OrchestratorPolicy | None
    source: str  # "default" | "file"
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.policy is not None


class _InvalidPolicyError(ValueError):
    pass


def _table(raw: Any, where: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _InvalidPolicyError(f"{where} doit être une table, pas {raw!r}")
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        raise _InvalidPolicyError(f"{where} : clé inconnue {', '.join(unknown)} (admises : {', '.join(sorted(allowed))})")
    return raw


def _positive(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _InvalidPolicyError(f"{where} doit être un entier strictement positif, pas {value!r}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise _InvalidPolicyError(f"{where} doit être du texte, pas {value!r}")
    return value.strip()


def _parse_worker(raw: Any) -> WorkerRole:
    data = _table(raw, "orchestrator.roles.worker", _WORKER_KEYS)
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _InvalidPolicyError(f"orchestrator.roles.worker.enabled doit être un booléen, pas {enabled!r}")
    tier = _text(data.get("tier", ""), "orchestrator.roles.worker.tier")
    if tier and tier not in TIERS:
        raise _InvalidPolicyError(f"orchestrator.roles.worker.tier : {tier!r} inconnu (admis : {', '.join(TIERS)})")
    return WorkerRole(
        enabled=enabled,
        model=_text(data.get("model", ""), "orchestrator.roles.worker.model"),
        tier=tier,
        wip=_positive(data.get("wip", 1), "orchestrator.roles.worker.wip"),
        sessions_per_day=_positive(data.get("sessions_per_day", 5), "orchestrator.roles.worker.sessions_per_day"),
        permission_mode=_text(data.get("permission_mode", ""), "orchestrator.roles.worker.permission_mode"),
    )


def _parse(block: Any) -> OrchestratorPolicy:
    top = _table(block, "orchestrator", frozenset({"roles", "breaker", "janitor"}))
    roles = _table(top.get("roles", {}), "orchestrator.roles", frozenset({"worker"}))
    breaker = _table(top.get("breaker", {}), "orchestrator.breaker", frozenset({"threshold"}))
    janitor = _table(top.get("janitor", {}), "orchestrator.janitor", frozenset({"close_issue"}))
    close_issue = janitor.get("close_issue", False)
    if not isinstance(close_issue, bool):
        raise _InvalidPolicyError(f"orchestrator.janitor.close_issue doit être un booléen, pas {close_issue!r}")
    return OrchestratorPolicy(
        worker=_parse_worker(roles["worker"]) if "worker" in roles else WorkerRole(),
        breaker_threshold=_positive(breaker.get("threshold", DEFAULT_BREAKER_THRESHOLD), "orchestrator.breaker.threshold"),
        close_issue=close_issue,
    )


def load_orchestrator_policy(root: Path) -> PolicyRead:
    """Les réglages de l'orchestrateur du projet *root* ; échec fermé (voir le module)."""
    path = root / ORCHESTRATION_FILE
    if not path.is_file():
        return PolicyRead(OrchestratorPolicy(), "default")
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        return PolicyRead(None, "file", f"{ORCHESTRATION_FILE} illisible : {type(exc).__name__}: {exc}")
    if not isinstance(data, dict):
        return PolicyRead(None, "file", f"{ORCHESTRATION_FILE} doit être une table")
    if "orchestrator" not in data:
        return PolicyRead(OrchestratorPolicy(), "default")
    try:
        return PolicyRead(_parse(data["orchestrator"]), "file")
    except _InvalidPolicyError as exc:
        return PolicyRead(None, "file", f"{ORCHESTRATION_FILE} : {exc}")
