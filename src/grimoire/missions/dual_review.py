"""La double relecture indépendante d'une partie des chemins réservés, au choix du projet.

Quand ``orchestrator.roles.merger.protected_gate`` vaut ``dual_review``, une PR qui touche un chemin de la liste
« double relecture » (:data:`DUAL_REVIEW_PATHS`, plus les ``protected_paths`` du projet) n'escalade plus à l'humain :
le merger demande une **seconde** relecture, puis ne fusionne que si deux READY indépendants, scellés au journal
d'autorité, couvrent la même tête, et si la CI est verte. Les chemins de :data:`HUMAN_ONLY_PATHS` restent à l'humain
quel que soit le réglage.

**Ce qui fait foi : le journal d'autorité, jamais le ledger d'événements** (un worker peut écrire le ledger).

- *Le choix du second relecteur* (:func:`choose_second_reviewer`) se fait dans le code du tick, au lancement : le modèle
  du premier relecteur vient de son sceau, celui du worker de son lancement scellé, les candidats du registre de
  fournisseurs — ceux que l'hôte du relecteur sait lancer (:data:`HOST_PROVIDERS`), jamais un modèle d'un fournisseur
  étranger. Le modèle choisi diffère de celui du premier relecteur et sa famille de celle du worker ; une famille
  différente de celle du premier relecteur est préférée. Aucun candidat : rien n'est lancé, l'humain tranche.
- *La demande de seconde relecture* est une entrée du journal d'autorité écrite par le merger
  (:data:`~grimoire.missions.review_seal.DUAL_REQUEST_ENTRY`) ; le relecteur la lit là (:func:`pending_second_review`),
  et seulement en ``dual_review``. Elle ne porte aucun modèle.
- *La décision de fusion et d'acceptation* (:func:`judge_dual_seals`) compare les deux sceaux : sessions distinctes,
  lancées par le tick, inscrites comme relecteurs et distinctes de toute session de worker ; modèles distincts, celui du
  second d'une autre famille que le worker ; tous deux READY, couvrant la même tête et le même arbre (directement ou
  par un réalignement noté par le merger pour chacun), postérieurs au commit relu ; aucun NOT-READY entre les deux.

Famille : le nom du modèle porte l'alias (``opus``, ``sonnet``, ``haiku``) ; un modèle inconnu est sa propre famille.
Deux noms de la même famille ne sont tenus pour deux modèles que si aucun n'est un alias nu (``opus`` peut désigner
``claude-opus-4-7``).

**Limites.**

- *Un sceau prouve qu'une session lancée par le tick existe, pas qu'elle a rendu le verdict.* Le READY vient du reçu,
  un fichier du worktree, rattaché à son relecteur par l'identifiant de session qu'il porte ; worker, relecteurs et
  tick tournent sous le même utilisateur du système, et cet identifiant se lit (liste des sessions de l'hôte, reçu une
  fois écrit). Un worker hostile qui laisse un processus écrire un reçu READY au nom de chaque relecteur lancé obtient
  deux sceaux valides : la double relecture lui demande de le faire deux fois, elle ne l'en empêche pas. C'est la
  limite du verrou 7 (:mod:`.reviewer`, :mod:`.launcher`), et en ``dual_review`` elle atteint des chemins réservés
  qu'en ``human`` elle n'atteint pas. Seule une isolation par utilisateur du système la fermerait ; le journal
  d'autorité lui-même vit sous ce même utilisateur (:mod:`.review_seal`).
- Les deux relecteurs tournent dans le worktree du worker, avec les réglages d'hôte qui s'y trouvent.
- L'indépendance est celle de deux sessions et de deux modèles, pas de deux personnes.
- Le premier relecteur est celui de main : son modèle et sa consigne dépendent encore du lancement et du worktree
  notés au ledger. La seconde relecture, elle, n'en tire rien (modèle, consigne, dépôt).
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from grimoire.providers.registry import read_registry

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "BASIS_OTHER_FAMILY",
    "BASIS_SAME_FAMILY",
    "DUAL_MISSING",
    "DUAL_OK",
    "DUAL_REFUSED",
    "DUAL_REVIEW_PATHS",
    "DUAL_STALE",
    "HOST_PROVIDERS",
    "HUMAN_ONLY_PATHS",
    "MAX_SECOND_LAUNCHES",
    "PROTECTED_GATES",
    "PROTECTED_GATE_DUAL_REVIEW",
    "PROTECTED_GATE_HUMAN",
    "SECOND_LAUNCH_GRACE",
    "DualReviewChoice",
    "DualVerdict",
    "choose_second_reviewer",
    "judge_dual_seals",
    "matched_paths",
    "model_family",
    "models_distinct",
    "pending_second_review",
    "second_review_stalled",
    "second_reviews_exhausted",
    "worker_models",
]

PROTECTED_GATE_HUMAN = "human"
PROTECTED_GATE_DUAL_REVIEW = "dual_review"
PROTECTED_GATES: tuple[str, ...] = (PROTECTED_GATE_HUMAN, PROTECTED_GATE_DUAL_REVIEW)

#: Les chemins qui restent à l'humain quel que soit ``protected_gate``. « CI verte » est l'autre condition de fusion :
#: tout fichier capable de rendre la CI verte à tort, ou de changer ce que la relecture juge, y figure. La politique du
#: projet peut étendre cette liste (``merger.human_only_paths``), jamais la réduire.
HUMAN_ONLY_PATHS: tuple[str, ...] = (
    # la politique et la configuration d'hôte
    "_grimoire/standard/*", ".claude/*", ".mcp.json", ".vscode/*",
    # ce qui définit la CI et les tests
    ".github/*", "*conftest.py", "pyproject.toml", "setup.py", "setup.cfg", ".pre-commit-config.yaml",
    ".githooks/*", ".husky/*", "framework/hooks/*", "codeowners", "*/codeowners", ".gitmodules", ".gitattributes",
    # ce qui se compile ou s'exécute à l'import : aucune relecture ne le juge
    "*.pyc", "*.pyo", "*.pyd", "*.so", "*.dll", "*.dylib", "*.exe", "*.whl", "*.egg", "*.pth",
    "*sitecustomize*", "*usercustomize*",
    # le mécanisme lui-même et le cœur de l'autorité
    "src/grimoire/missions/merger.py", "src/grimoire/missions/dual_review.py", "src/grimoire/missions/review_seal.py",
    "src/grimoire/missions/reviewer.py", "src/grimoire/missions/mandate.py", "src/grimoire/missions/autopilot.py",
    "src/grimoire/missions/breaker.py", "src/grimoire/missions/launcher.py",
    "src/grimoire/missions/orchestrator_policy.py", "src/grimoire/hosts/decisions/*",
    "src/grimoire/flows/attestation.py",
    # … et ce qui le remplacerait sans le toucher : un paquet du même nom l'emporte sur le module à l'import, et
    # ``missions/__init__.py`` s'exécute avant chacun d'eux
    "src/grimoire/missions/__init__.py",
    "src/grimoire/missions/merger/*", "src/grimoire/missions/dual_review/*", "src/grimoire/missions/review_seal/*",
    "src/grimoire/missions/reviewer/*", "src/grimoire/missions/mandate/*", "src/grimoire/missions/autopilot/*",
    "src/grimoire/missions/breaker/*", "src/grimoire/missions/launcher/*",
    "src/grimoire/missions/orchestrator_policy/*",
)

#: Les chemins réservés qu'une double relecture peut juger : le reste de
#: :data:`~grimoire.missions.merger.DEFAULT_PROTECTED_PATHS`. Un fichier que :data:`HUMAN_ONLY_PATHS` désigne aussi
#: reste à l'humain. Les ``protected_paths`` du projet s'y ajoutent.
DUAL_REVIEW_PATHS: tuple[str, ...] = ("src/grimoire/missions/*", "src/grimoire/core/*")

#: Les fournisseurs du registre que l'hôte du relecteur (``claude --bg --model``, sans route) sait lancer : les mêmes
#: que le lanceur tient pour natifs. Un fournisseur atteint par une route (``anthropic_base_url``) est étranger.
HOST_PROVIDERS: frozenset[str] = frozenset({"anthropic", "claude", "claude-code"})

#: Les familles que le nom d'un modèle peut porter, dans l'ordre où on les reconnaît.
_FAMILY_ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")
#: Le palier d'un modèle du registre : une relecture de chemin réservé prend le plus fort des candidats.
_TIER_ORDER = {"strong": 0, "mid": 1, "cheap": 2}
#: Secondes relectures lancées pour un même premier READY (la première, puis ses relances) avant de rendre la PR à
#: l'humain : jamais une boucle de lancements, même si un READY ne se scelle pas.
MAX_SECOND_LAUNCHES = 3
#: Ce que le merger attend d'une seconde relecture demandée (ou relancée) avant de rendre la PR à l'humain : le délai
#: au-delà duquel un relecteur sans reçu est lui-même arrêté.
SECOND_LAUNCH_GRACE = timedelta(hours=2)
#: Un nom de modèle, jamais une option (le même motif que la politique de l'orchestrateur).
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]/@-]*")
_VARIANT = re.compile(r"\[[^\]]*\]$")

#: Le registre offrait une famille différente de celle du premier relecteur : elle a été préférée.
BASIS_OTHER_FAMILY = "other_family"
#: Le registre n'offrait pas d'autre famille : un autre modèle de la famille du premier relecteur a été pris.
BASIS_SAME_FAMILY = "same_family"

#: Les deux READY scellés tiennent sur la tête : la fusion et l'acceptation peuvent suivre.
DUAL_OK = "ok"
#: Un seul READY scellé couvre la tête : la seconde relecture manque.
DUAL_MISSING = "missing"
#: Les sceaux ne couvrent pas (ou plus) cette tête : nouvelle relecture.
DUAL_STALE = "stale"
#: Les sceaux ne prouvent pas deux relectures indépendantes, ou ne se lisent pas : l'humain tranche.
DUAL_REFUSED = "refused"

_READY = "READY"


def model_family(model: str) -> str:
    """La famille d'un modèle, lue dans son nom ; un nom sans alias connu est sa propre famille."""
    lowered = model.lower()
    return next((alias for alias in _FAMILY_ALIASES if alias in lowered), lowered)


def _plain(model: str) -> str:
    """Le nom sans sa variante de contexte (``claude-opus-4-7[1m]`` est ``claude-opus-4-7``)."""
    return _VARIANT.sub("", model.strip().lower())


def _bare_alias(model: str) -> bool:
    """Un alias nu (``opus``, ``sonnet[1m]``) : il désigne un modèle que son nom ne dit pas."""
    return _plain(model) in _FAMILY_ALIASES


def models_distinct(first: str, second: str) -> bool:
    """Deux noms désignent-ils sûrement deux modèles ? Deux familles : oui. Une seule : seulement deux noms complets
    dont aucun ne prolonge l'autre — un alias nu, une variante (``[1m]``) ou un identifiant daté
    (``claude-opus-4-7-20260101``) peuvent désigner le même modèle."""
    one, two = _plain(first), _plain(second)
    if not one or not two:
        return False
    if model_family(one) != model_family(two):
        return True
    if _bare_alias(one) or _bare_alias(two):
        return False
    return not (one.startswith(two) or two.startswith(one))


def matched_paths(files: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Les fichiers qu'un des motifs désigne, comparés en ``casefold()``."""
    folded = tuple(p.casefold() for p in patterns)
    return sorted(f for f in files if any(fnmatch.fnmatchcase(f.casefold(), pat) for pat in folded))


@dataclass(frozen=True, slots=True)
class DualReviewChoice:
    """Le modèle de la seconde relecture, sa famille, et la règle qui l'a fait choisir."""

    model: str
    family: str
    basis: str
    first_family: str
    worker_families: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "family": self.family, "family_basis": self.basis,
            "first_family": self.first_family, "worker_families": list(self.worker_families),
        }


def _host_models(root: Path) -> tuple[tuple[str, ...], str]:
    """``(modèles, "")`` : les modèles des fournisseurs activés que l'hôte du relecteur sait lancer, les plus forts
    d'abord ; ``((), raison)`` si le registre ne se lit pas. Échec fermé."""
    try:
        providers = read_registry(root)
    except Exception as exc:  # registre illisible : aucun candidat, l'humain tranche
        return (), f"le registre de fournisseurs est illisible ({type(exc).__name__}: {str(exc)[:160]})"
    found: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for provider in providers:
        if not provider.enabled or provider.id.casefold() not in HOST_PROVIDERS or provider.anthropic_base_url:
            continue
        for model in provider.models:
            key = model.id.casefold()
            if key in seen or not _MODEL_ID.fullmatch(model.id):
                continue
            seen.add(key)
            found.append((_TIER_ORDER.get(model.tier, len(_TIER_ORDER)), len(found), model.id))
    return tuple(model_id for _order, _rank, model_id in sorted(found)), ""


def choose_second_reviewer(
    root: Path, *, first_model: str, worker_models: Sequence[str],
) -> tuple[DualReviewChoice | None, str]:
    """``(choix, "")`` ou ``(None, raison)`` : le modèle de la seconde relecture, choisi dans le registre.

    *first_model* : le modèle du premier relecteur, lu dans son sceau ; *worker_models* : ceux des lancements du
    worker, lus au journal d'autorité (:func:`worker_models`). Contraintes dures : un modèle que l'hôte du relecteur
    sait lancer, sûrement distinct de celui du premier relecteur (:func:`models_distinct`), d'une famille différente
    de chacune de celles du worker. Parmi les admissibles, une famille différente de celle du premier relecteur est
    préférée, puis le palier le plus fort. Sans candidat, rien n'est choisi : l'appelant escalade à l'humain.
    """
    if not first_model.strip():
        return None, "le sceau du premier relecteur ne porte pas son modèle : l'indépendance n'est pas prouvable"
    if not worker_models or any(not m.strip() for m in worker_models):
        return None, ("le modèle du worker n'est pas scellé au journal d'autorité (tâche faite à la main, lancée avant "
                      "le réglage dual_review, ou worker sans modèle nommé) : l'indépendance n'est pas prouvable")
    first_family = model_family(first_model)
    worker_families = tuple(sorted({model_family(m) for m in worker_models}))
    models, unreadable = _host_models(root)
    if unreadable:
        return None, unreadable
    if not models:
        return None, ("le registre de fournisseurs n'offre aucun modèle que l'hôte du relecteur sait lancer "
                      f"(fournisseurs activés parmi {', '.join(sorted(HOST_PROVIDERS))})")
    eligible = [m for m in models if models_distinct(first_model, m) and model_family(m) not in worker_families]
    if not eligible:
        return None, (f"le registre n'offre aucun modèle distinct de celui du premier relecteur ({first_model}) et "
                      f"d'une famille différente de celle du worker ({', '.join(worker_families)})")
    other = [m for m in eligible if model_family(m) != first_family]
    chosen = (other or eligible)[0]
    basis = BASIS_OTHER_FAMILY if other else BASIS_SAME_FAMILY
    return DualReviewChoice(chosen, model_family(chosen), basis, first_family, worker_families), ""


# ── Lecture du journal d'autorité ──────────────────────────────────────────────


def _digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _seq(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def worker_models(project_root: Path, task_id: str) -> tuple[str, ...]:
    """Les modèles des lancements du worker de *task_id*, scellés au journal d'autorité.

    ``()`` si le lancement d'origine ne l'est pas : une session neuve repartie après une reprise ratée (``relaunch``)
    porte un modèle venu du ledger, qui ne suffit pas à dire qui a écrit la branche.
    """
    from grimoire.missions.review_seal import WORKER_ENTRY, task_entries

    return _worker_models([e for e in task_entries(project_root, task_id) if e.get("type") == WORKER_ENTRY])


def _worker_models(launches: list[dict[str, Any]]) -> tuple[str, ...]:
    if not any(not e.get("relaunch") for e in launches):
        return ()
    return tuple(str(e.get("model") or "") for e in launches)


def _is_second(seal: dict[str, Any]) -> bool:
    return "second_of" in seal


def _voided(mine: list[dict[str, Any]], first_seq: int) -> bool:
    from grimoire.missions.review_seal import DUAL_VOID_ENTRY

    return any(e.get("type") == DUAL_VOID_ENTRY and e.get("first_seq") == first_seq for e in mine)


def _second_launches(mine: list[dict[str, Any]], first_seq: int) -> int:
    from grimoire.missions.review_seal import LAUNCH_ENTRY

    return sum(1 for e in mine if e.get("type") == LAUNCH_ENTRY and e.get("second_of") == first_seq)


def second_reviews_exhausted(project_root: Path, task_id: str, first_seq: int) -> bool:
    """Le premier READY scellé à *first_seq* a eu toutes ses secondes relectures (:data:`MAX_SECOND_LAUNCHES`) sans
    qu'aucune n'aboutisse : plus aucune n'est lancée, l'humain tranche."""
    from grimoire.missions.review_seal import task_entries

    return _second_launches(task_entries(project_root, task_id), first_seq) >= MAX_SECOND_LAUNCHES


def second_review_stalled(project_root: Path, task_id: str, first_seq: int, now: datetime) -> bool:
    """La seconde relecture du premier READY scellé à *first_seq* n'avance plus : ni la demande du merger ni le dernier
    lancement d'une seconde relecture n'ont moins de :data:`SECOND_LAUNCH_GRACE`. Le merger rend alors la PR à
    l'humain plutôt que d'attendre sans fin (branche locale qui n'est plus la tête relue, relecteur empêché…).
    Une date illisible vaut un blocage."""
    from grimoire.missions.review_seal import DUAL_REQUEST_ENTRY, LAUNCH_ENTRY, task_entries

    steps = [
        e for e in task_entries(project_root, task_id)
        if (e.get("type") == DUAL_REQUEST_ENTRY and e.get("first_seq") == first_seq)
        or (e.get("type") == LAUNCH_ENTRY and e.get("second_of") == first_seq)
    ]
    if not steps:
        return False
    stamps = [_sealed_at(e) for e in steps]
    if any(s is None for s in stamps):
        return True
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return moment.timestamp() - max(s for s in stamps if s is not None) > SECOND_LAUNCH_GRACE.total_seconds()


def pending_second_review(project_root: Path, task_id: str, head: str) -> dict[str, Any] | None:
    """Le sceau du premier READY dont le merger a demandé la seconde relecture, encore due sur *head* ; sinon ``None``.

    Lu au journal d'autorité seulement : le dernier sceau de la tâche est un premier READY qui couvre *head*, le merger
    en a demandé la seconde relecture après lui, et aucun NOT-READY ne l'a annulée. Lève si le journal ne se lit pas
    (l'appelant ne lance alors rien).
    """
    from grimoire.missions.review_seal import DUAL_REQUEST_ENTRY, SEAL_ENTRY, covered_by, task_entries, tree_of

    if not head:
        return None
    mine = task_entries(project_root, task_id)
    seals = [(i, e) for i, e in enumerate(mine) if e.get("type") == SEAL_ENTRY]
    if not seals:
        return None
    index, first = seals[-1]
    seq = _seq(first.get("seq"))
    if seq is None or _is_second(first) or first.get("verdict") != _READY:
        return None
    asked = any(e.get("type") == DUAL_REQUEST_ENTRY and e.get("first_seq") == seq for e in mine[index + 1:])
    if not asked or _voided(mine, seq) or _second_launches(mine, seq) >= MAX_SECOND_LAUNCHES:
        return None
    if (head, tree_of(project_root, head)) not in covered_by(mine, index, first):
        return None
    return dict(first)


@dataclass(frozen=True, slots=True)
class DualVerdict:
    """Ce que les sceaux du journal d'autorité disent de la double relecture d'une tête."""

    kind: str  # DUAL_OK, DUAL_MISSING, DUAL_STALE ou DUAL_REFUSED
    reason: str = ""
    first: dict[str, Any] | None = None
    second: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.kind == DUAL_OK

    def summary(self) -> dict[str, Any]:
        """Ce que le ledger garde d'une paire tenue : relectures, modèles et familles, tous lus dans les sceaux."""
        first, second = self.first or {}, self.second or {}
        first_model, second_model = str(first.get("model") or ""), str(second.get("model") or "")
        same = model_family(first_model) == model_family(second_model)
        return {
            "review": str(first.get("review") or ""), "second_review": str(second.get("review") or ""),
            "first_seal": first.get("seq"), "second_seal": second.get("seq"),
            "first_model": first_model, "second_model": second_model,
            "first_family": model_family(first_model), "second_family": model_family(second_model),
            "family_basis": BASIS_SAME_FAMILY if same else BASIS_OTHER_FAMILY,
        }


def _commit_time(project_root: Path, commit: str) -> float | None:
    """La date de commit (committer) de *commit*, en secondes ; ``None`` s'il n'est pas dans le dépôt."""
    from grimoire.core.safe_git import git_env

    if not commit:
        return None
    try:
        done = subprocess.run(  # arguments fixes, sans shell
            ["git", "-C", str(project_root), "log", "-1", "--format=%ct", f"{commit}^{{commit}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, check=False, env=git_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    stamp = done.stdout.strip()
    return float(stamp) if done.returncode == 0 and stamp.isdigit() else None


def _sealed_at(seal: dict[str, Any]) -> float | None:
    try:
        moment = datetime.fromisoformat(str(seal.get("ts") or ""))
    except ValueError:
        return None
    return (moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)).timestamp()


def judge_dual_seals(project_root: Path, task_id: str, head: str) -> DualVerdict:
    """La double relecture de *head* tient-elle ? Lu au journal d'autorité et dans git, jamais au ledger d'événements.

    Échec fermé : un journal illisible, un sceau incomplet ou toute erreur rendent :data:`DUAL_REFUSED`.
    """
    try:
        return _judge(project_root, task_id, head)
    except Exception as exc:  # une garde qui ne lit pas ses sceaux refuse
        return DualVerdict(DUAL_REFUSED, f"sceaux de la double relecture illisibles ({type(exc).__name__}: {str(exc)[:160]})")


def _judge(project_root: Path, task_id: str, head: str) -> DualVerdict:
    from grimoire.missions.mandate import MandateStore
    from grimoire.missions.review_seal import (
        DUAL_REQUEST_ENTRY,
        LAUNCH_ENTRY,
        SEAL_ENTRY,
        WORKER_ENTRY,
        covered_by,
        task_entries,
        tree_of,
    )

    mine = task_entries(project_root, task_id)
    tree = tree_of(project_root, head)
    if not head or not tree:
        return DualVerdict(DUAL_REFUSED, f"tête {head[:12] or 'absente'} inconnue du dépôt : rien n'est comparable")
    target = (head, tree)
    seals = [(i, e) for i, e in enumerate(mine) if e.get("type") == SEAL_ENTRY]
    if not seals:
        return DualVerdict(DUAL_STALE, "aucun READY scellé au journal d'autorité pour cette tâche")
    i2, second = seals[-1]
    if target not in covered_by(mine, i2, second):
        return DualVerdict(DUAL_STALE, f"le dernier READY scellé ne couvre pas la tête {head[:12]} (autre tête, ou "
                                       "réalignement non prolongé pour lui)")
    if not _is_second(second):
        only = _seq(second.get("seq"))
        if second.get("verdict") != _READY or only is None:
            # Un sceau d'avant la double relecture (sans verdict ni modèle) ne fonde aucune paire : pas d'attente sans fin.
            return DualVerdict(DUAL_REFUSED, "le READY scellé ne porte pas son verdict : il ne peut pas fonder une "
                                             "double relecture", first=dict(second))
        if _voided(mine, only):  # plus aucune seconde relecture ne sera lancée pour lui : pas d'attente sans fin non plus
            return DualVerdict(DUAL_REFUSED, "ce READY a été annulé (NOT-READY ou READY périmé depuis) : il ne fonde "
                                             "plus de double relecture", first=dict(second))
        return DualVerdict(DUAL_MISSING, f"un seul READY scellé couvre la tête {head[:12]} : la seconde relecture manque",
                           first=dict(second))
    first_seq, second_seq = _seq(second.get("second_of")), _seq(second.get("seq"))
    found = [(i, e) for i, e in seals if first_seq is not None and e.get("seq") == first_seq]
    if not found or second_seq is None:
        return DualVerdict(DUAL_REFUSED, "le premier sceau de la paire est introuvable au journal d'autorité")
    i1, first = found[0]
    if i1 >= i2 or _is_second(first):
        return DualVerdict(DUAL_REFUSED, "le premier sceau de la paire n'est pas un premier READY antérieur au second")
    if target not in covered_by(mine, i1, first):
        return DualVerdict(DUAL_STALE, f"le premier READY scellé ne couvre pas la tête {head[:12]} (autre tête, ou "
                                       "réalignement non prolongé pour lui)")
    pair = {"first": dict(first), "second": dict(second)}
    if first.get("verdict") != _READY or second.get("verdict") != _READY:
        return DualVerdict(DUAL_REFUSED, "un des deux sceaux ne porte pas le verdict READY", **pair)
    if _voided(mine, first_seq if first_seq is not None else -1):
        return DualVerdict(DUAL_REFUSED, "ce premier READY a été annulé (NOT-READY ou READY périmé depuis) : la paire "
                                         "ne vaut plus", **pair)
    # Les sessions : distinctes, lancées par le tick, inscrites comme relecteurs, jamais celles d'un worker.
    one, two = str(first.get("reviewer_session") or "").strip(), str(second.get("reviewer_session") or "").strip()
    if not one or not two:
        return DualVerdict(DUAL_REFUSED, "un des deux sceaux ne porte pas la session de son relecteur", **pair)
    if one == two:
        return DualVerdict(DUAL_REFUSED, "les deux READY viennent de la même session : la seconde relecture doit être "
                                         "indépendante", **pair)
    launches = [(i, e) for i, e in enumerate(mine) if e.get("type") == LAUNCH_ENTRY]
    first_launch = next(((i, e) for i, e in reversed(launches) if e.get("session_sha256") == _digest(one) and i < i1), None)
    second_launch = next(((i, e) for i, e in reversed(launches) if e.get("session_sha256") == _digest(two) and i < i2), None)
    if first_launch is None or second_launch is None:
        return DualVerdict(DUAL_REFUSED, "un des deux relecteurs n'a pas de lancement scellé par le tick", **pair)
    asked = any(e.get("type") == DUAL_REQUEST_ENTRY and e.get("first_seq") == first_seq
                for e in mine[i1 + 1:second_launch[0]])
    if (not asked or second_launch[0] <= i1 or second_launch[1].get("second_of") != first_seq
            or "second_of" in first_launch[1]):
        return DualVerdict(DUAL_REFUSED, "le second relecteur n'a pas été lancé par le tick comme seconde relecture de "
                                         "ce premier READY, à la demande du merger", **pair)
    registered = MandateStore(project_root).sessions(task_id=task_id)
    reviewers = {str(e.get("session_id") or "") for e in registered if e.get("role") == "reviewer"}
    workers = {str(e.get("session_id") or "") for e in registered if e.get("role") == "worker"}
    worker_launches = [e for e in mine if e.get("type") == WORKER_ENTRY]
    worker_digests = {str(e.get("session_sha256") or "") for e in worker_launches}
    launched_models = _worker_models(worker_launches)
    if not launched_models or "" in worker_digests:
        return DualVerdict(DUAL_REFUSED, "le lancement du worker n'est pas scellé au journal d'autorité : rien ne prouve "
                                         "que les relecteurs ne sont pas le worker", **pair)
    if {one, two} & workers or {_digest(one), _digest(two)} & worker_digests:
        return DualVerdict(DUAL_REFUSED, "un des deux READY vient d'une session de worker", **pair)
    if one not in reviewers or two not in reviewers:
        return DualVerdict(DUAL_REFUSED, "un des deux relecteurs n'est pas inscrit au journal d'autorité comme relecteur "
                                         "de cette tâche", **pair)
    # Les modèles : ceux que le tick a lancés, distincts, le second d'une autre famille que le worker.
    first_model, second_model = str(first.get("model") or ""), str(second.get("model") or "")
    if first_model != str(first_launch[1].get("model") or "") or second_model != str(second_launch[1].get("model") or ""):
        return DualVerdict(DUAL_REFUSED, "le modèle d'un sceau n'est pas celui que le tick a lancé", **pair)
    if not models_distinct(first_model, second_model):
        return DualVerdict(DUAL_REFUSED, f"les deux relectures n'ont pas deux modèles sûrement distincts "
                                         f"({first_model or 'inconnu'}, {second_model or 'inconnu'})", **pair)
    if any(not m.strip() for m in launched_models):
        return DualVerdict(DUAL_REFUSED, "le modèle du worker n'est pas scellé : l'indépendance n'est pas prouvable", **pair)
    if model_family(second_model) in {model_family(m) for m in launched_models}:
        return DualVerdict(DUAL_REFUSED, f"la seconde relecture ({second_model}) est de la famille du worker", **pair)
    # Les deux READY sont postérieurs au commit relu.
    commits = [_commit_time(project_root, str(s.get("head") or "")) for s in (first, second)]
    sealed = [_sealed_at(first), _sealed_at(second)]
    if any(c is None for c in commits) or any(s is None for s in sealed):
        return DualVerdict(DUAL_REFUSED, "date d'un sceau ou du commit relu illisible", **pair)
    latest = max(c for c in commits if c is not None)
    if any(s is not None and s < latest for s in sealed):
        return DualVerdict(DUAL_STALE, "un des deux READY est antérieur au dernier commit relu", **pair)
    return DualVerdict(DUAL_OK, "", **pair)
