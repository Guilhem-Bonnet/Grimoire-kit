"""La politique de routage déclarative (K9, issue #774, épopée #705).

Un fichier, ``_grimoire/standard/routing-policy.yaml``, dit **quel palier** une
tâche mérite — jamais quel modèle : les modèles d'un palier viennent du registre
des fournisseurs (``providers.registry``). Sans fichier, le défaut livré par le
kit (:data:`DEFAULT_POLICY`) s'applique, en mode ``shadow``.

Trois principes, tous testés :

- **Toutes les règles qui correspondent s'appliquent**, et le palier retenu est le
  *maximum* de leurs ``min_tier`` : le résultat ne dépend pas de l'ordre des règles
  et ne peut que monter. ``route: session`` est absorbant.
- **Fail-closed.** Une politique malformée, sans version ou de version inconnue,
  n'est jamais « corrigée » : :func:`load_policy` rend l'erreur et aucune route
  autonome n'est produite (``on_invalid: refuse``).
- **Aucune entrée de budget.** Une règle ne peut parler ni de quota, ni de budget, ni
  de tokens restants : ``when`` n'accepte que les clés de :data:`WHEN_KEYS`, toute
  autre clé invalide la politique. Le budget et les quotas relèvent de l'admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from grimoire.core.standard_generation import STANDARD_DIR
from grimoire.providers.registry import SUPPORTED_CURRENCIES, SUPPORTED_MODEL_TIERS

if TYPE_CHECKING:
    from pathlib import Path

    from grimoire.missions.router import RoutingTask

__all__ = [
    "DEFAULT_POLICY",
    "MODES",
    "POLICY_FILE",
    "SESSION",
    "WHEN_KEYS",
    "PolicyError",
    "PolicyLoad",
    "RoutingPolicy",
    "Rule",
    "load_policy",
    "parse_policy",
    "rule_matches",
]

#: Chemin relatif de la politique, projet par projet.
POLICY_FILE = STANDARD_DIR / "routing-policy.yaml"

#: « Pas de route autonome : la session qui tient la tâche décide. »
SESSION = "session"

#: ``shadow`` : la décision est tracée (``task.routed``), le dispatch suit l'ancien
#: chemin. ``active`` : la décision gouverne le palier de départ et le modèle.
MODES: tuple[str, ...] = ("shadow", "active")

#: Les seules conditions qu'une règle peut poser — jamais de quota ni de budget.
WHEN_KEYS: tuple[str, ...] = ("class", "type", "risk_any", "exclusive_files_gt", "needs_local")

_CLASSES = ("V0", "V1", "V2")
_RULE_KEYS = frozenset({"id", "when", "route", "min_tier", "providers"})
_TOP_KEYS = frozenset({"version", "mode", "on_invalid", "max_attempts", "rules", "default"})


class PolicyError(ValueError):
    """La politique de routage est invalide : aucune route autonome n'en sort."""


@dataclass(frozen=True, slots=True)
class Rule:
    """Une règle : des conditions (``when``) et un effet (session, palier minimal, monnaie)."""

    id: str
    when: dict[str, Any] = field(default_factory=dict)
    route: str | None = None
    min_tier: str | None = None
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "when": dict(self.when)}
        if self.route is not None:
            data["route"] = self.route
        if self.min_tier is not None:
            data["min_tier"] = self.min_tier
        if self.currency is not None:
            data["providers"] = {"currency": self.currency}
        return data


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    version: int = 1
    mode: str = "shadow"
    on_invalid: str = "refuse"
    max_attempts: int = 3
    rules: tuple[Rule, ...] = ()
    default: str = SESSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": self.mode,
            "on_invalid": self.on_invalid,
            "max_attempts": self.max_attempts,
            "rules": [rule.to_dict() for rule in self.rules],
            "default": self.default,
        }


@dataclass(frozen=True, slots=True)
class PolicyLoad:
    """Le résultat d'une lecture : la politique, ou l'erreur qui la rend inutilisable."""

    policy: RoutingPolicy | None
    source: str  # "default" | "file"
    error: str | None = None


#: Le défaut livré par le kit. Même contenu que la section 3.2 du plan.
DEFAULT_POLICY = RoutingPolicy(
    rules=(
        Rule("r-v2", {"class": "V2"}, route=SESSION),
        Rule("r-risk", {"risk_any": ["security", "migration", "concurrency", "ledger"]}, min_tier="strong"),
        Rule("r-size", {"exclusive_files_gt": 8}, min_tier="mid"),
        Rule("r-local", {"needs_local": True}, currency="local"),
        Rule("r-v1", {"class": "V1"}, min_tier="mid"),
        Rule("r-v0", {"class": "V0"}, min_tier="cheap"),
    ),
)


def _fail(message: str) -> PolicyError:
    return PolicyError(f"routing-policy.yaml : {message}")


def _parse_when(rule_id: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail(f"règle {rule_id!r} : `when` doit être une table")
    unknown = sorted(str(key) for key in raw if key not in WHEN_KEYS)
    if unknown:
        raise _fail(f"règle {rule_id!r} : condition inconnue {unknown} (permises : {list(WHEN_KEYS)})")
    when: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "class":
            if value not in _CLASSES:
                raise _fail(f"règle {rule_id!r} : class doit être l'une de {list(_CLASSES)}")
        elif key == "type":
            if not isinstance(value, str) or not value:
                raise _fail(f"règle {rule_id!r} : type doit être une chaîne")
        elif key == "risk_any":
            if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
                raise _fail(f"règle {rule_id!r} : risk_any doit être une liste de chaînes non vide")
            value = [str(v) for v in value]
        elif key == "exclusive_files_gt":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _fail(f"règle {rule_id!r} : exclusive_files_gt doit être un entier >= 0")
        elif not isinstance(value, bool):  # needs_local
            raise _fail(f"règle {rule_id!r} : needs_local doit être un booléen")
        when[str(key)] = value
    return when


def _parse_rule(raw: Any, seen: set[str]) -> Rule:
    if not isinstance(raw, dict):
        raise _fail("chaque règle doit être une table")
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise _fail("chaque règle a un `id` (chaîne non vide)")
    if rule_id in seen:
        raise _fail(f"id de règle en double : {rule_id!r}")
    seen.add(rule_id)
    unknown = sorted(str(key) for key in raw if key not in _RULE_KEYS)
    if unknown:
        raise _fail(f"règle {rule_id!r} : clé inconnue {unknown}")
    when = _parse_when(rule_id, raw.get("when", {}))
    route, min_tier, providers = raw.get("route"), raw.get("min_tier"), raw.get("providers")
    if route is None and min_tier is None and providers is None:
        raise _fail(f"règle {rule_id!r} : aucun effet (route, min_tier ou providers)")
    if route is not None and (route != SESSION or min_tier is not None or providers is not None):
        raise _fail(f"règle {rule_id!r} : `route` n'accepte que `{SESSION}`, et seul")
    if min_tier is not None and min_tier not in SUPPORTED_MODEL_TIERS:
        raise _fail(f"règle {rule_id!r} : min_tier doit être l'un de {list(SUPPORTED_MODEL_TIERS)}")
    currency: str | None = None
    if providers is not None:
        if not isinstance(providers, dict) or set(providers) != {"currency"} or providers["currency"] not in SUPPORTED_CURRENCIES:
            raise _fail(f"règle {rule_id!r} : providers = {{currency: {list(SUPPORTED_CURRENCIES)}}}")
        currency = str(providers["currency"])
    return Rule(rule_id, when, route=route, min_tier=min_tier, currency=currency)


def parse_policy(data: Any) -> RoutingPolicy:
    """Une politique validée, ou :class:`PolicyError`. Jamais de valeur « corrigée » en silence."""
    if not isinstance(data, dict):
        raise _fail("la racine doit être une table")
    unknown = sorted(str(key) for key in data if key not in _TOP_KEYS)
    if unknown:
        raise _fail(f"clé inconnue {unknown}")
    version = data.get("version")
    if isinstance(version, bool) or version != 1:
        raise _fail(f"version absente ou inconnue ({version!r}) : attendu 1")
    mode = data.get("mode", "shadow")
    if mode not in MODES:
        raise _fail(f"mode {mode!r} : attendu l'un de {list(MODES)}")
    if data.get("on_invalid", "refuse") != "refuse":
        raise _fail("on_invalid : seule la valeur `refuse` existe")
    max_attempts = data.get("max_attempts", 3)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        raise _fail("max_attempts doit être un entier >= 1")
    if data.get("default", SESSION) != SESSION:
        raise _fail(f"default : seule la valeur `{SESSION}` existe")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise _fail("rules doit être une liste")
    seen: set[str] = set()
    rules = tuple(_parse_rule(raw, seen) for raw in raw_rules)
    return RoutingPolicy(version=1, mode=str(mode), max_attempts=max_attempts, rules=rules)


def load_policy(root: Path) -> PolicyLoad:
    """La politique du projet : le fichier s'il existe, le défaut livré sinon.

    Un fichier présent mais illisible ou invalide n'est **jamais** remplacé par le
    défaut : ``policy`` vaut ``None`` et ``error`` dit pourquoi (``on_invalid: refuse``).
    """
    path = root / POLICY_FILE
    if not path.is_file():
        return PolicyLoad(DEFAULT_POLICY, "default")
    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        return PolicyLoad(parse_policy(data), "file")
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        return PolicyLoad(None, "file", f"{POLICY_FILE} illisible : {type(exc).__name__}: {exc}")
    except PolicyError as exc:
        return PolicyLoad(None, "file", str(exc))


def rule_matches(rule: Rule, task: RoutingTask) -> bool:
    """Toutes les conditions de ``when`` tiennent (une table vide correspond à tout)."""
    when = rule.when
    if "class" in when and when["class"] != task.verifiability:
        return False
    if "type" in when and when["type"] != task.task_type:
        return False
    if "risk_any" in when and not set(when["risk_any"]) & set(task.risk_tags):
        return False
    if "exclusive_files_gt" in when and task.exclusive_files <= when["exclusive_files_gt"]:
        return False
    return not ("needs_local" in when and when["needs_local"] != task.needs_local)
