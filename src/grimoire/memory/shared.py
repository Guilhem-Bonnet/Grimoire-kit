"""Mémoire transverse — ce qui reste vrai quand on efface le nom du projet.

Un agent spécialiste devrait accumuler du savoir réutilisable d'un projet à
l'autre. Le faire naïvement corrompt la connaissance de six façons connues :
confusion entre projets, fait périmé servi comme vrai, contamination d'un
projet par un autre, auto-confirmation (l'agent relit son hypothèse comme un
fait établi), compression jusqu'au mythe, et fuite d'information entre projets
cloisonnés.

Ce module applique trois règles qui traitent ces six modes de défaillance.

**La frontière est physique, pas déclarative.** Le savoir transverse vit dans
un store séparé, pas dans une collection partagée filtrée par métadonnée. Un
filtre oublié ne « fuit » pas un peu : il mélange deux projets sans rien
signaler. Une frontière que l'on peut oublier de poser n'est pas une frontière.

**La promotion est explicite et refusée par défaut.** Rien ne monte
automatiquement. Un souvenir ne devient transverse que s'il reste vrai quand on
efface le nom du projet — « l'app X utilise Postgres 16 » est un fait de
projet, dont la vérité dépend d'un HEAD git ; « les migrations Alembic cassent
quand deux heads coexistent » est un motif, dont la vérité dépend d'un contexte
technique reproductible. :func:`check_promotable` refuse le premier.

**La confiance décroît par défaut.** Un motif non revérifié depuis longtemps
est servi comme hypothèse, pas comme fait. Le calcul se fait à la lecture,
sans tâche de fond : une décroissance qui dépend d'un ordonnanceur est une
décroissance qui n'arrive pas.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.taxonomy import slugify

if TYPE_CHECKING:
    from grimoire.core.config import GrimoireConfig
    from grimoire.memory.manager import MemoryManager

SHARED_SCHEMA_VERSION = "grimoire-shared-memory/v1"

#: Paliers de fraîcheur, en jours depuis la dernière confirmation.
#: Au-delà du dernier palier, un souvenir transverse est servi comme hypothèse
#: — jamais supprimé, seulement déclassé : une connaissance périmée reste utile
#: à qui sait qu'elle est périmée.
FRESH_DAYS = 90
STALE_DAYS = 270

FRESHNESS_CURRENT = "current"
FRESHNESS_AGING = "aging"
FRESHNESS_HYPOTHESIS = "hypothesis"

#: Portées de restitution. Elles ne sont jamais fusionnées sans étiquette :
#: un résultat appris ailleurs ne doit pas être présenté avec l'assurance d'un
#: résultat vérifié ici.
SCOPE_PROJECT = "project"
SCOPE_SHARED = "shared"


class SharedMemoryError(RuntimeError):
    """Promotion refusée, ou store transverse indisponible."""


@dataclass(frozen=True, slots=True)
class PromotionVerdict:
    """Décision de promotion, avec son motif — refus comme acceptation."""

    ok: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class LayeredResult:
    """Un souvenir restitué, avec d'où il vient et ce qu'il vaut aujourd'hui."""

    entry: MemoryEntry
    scope: str
    freshness: str
    learned_in: tuple[str, ...] = ()
    confirmed_in: tuple[str, ...] = ()
    days_since_confirmation: int | None = None
    caveat: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.entry.id,
            "text": self.entry.text,
            "score": self.entry.score,
            "scope": self.scope,
            "freshness": self.freshness,
            "learnedIn": list(self.learned_in),
            "confirmedIn": list(self.confirmed_in),
            "daysSinceConfirmation": self.days_since_confirmation,
            "caveat": self.caveat,
        }


# ── Résolution du store transverse ────────────────────────────────────────────


def shared_home() -> Path:
    """Racine du store transverse pour les backends sans serveur.

    Les backends serveur (Weaviate, Qdrant) partagent déjà l'instance : seule
    la collection change. Les backends fichier doivent sortir du projet, sinon
    « transverse » ne veut rien dire — d'où une racine au niveau machine,
    alignée sur la convention ``~/.grimoire`` du cockpit.
    """
    env = os.environ.get("GRIMOIRE_SHARED_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".grimoire" / "shared"


def shared_collection_name(config: GrimoireConfig) -> str:
    """Nom de collection transverse déclaré, ou chaîne vide si désactivé."""
    return str(getattr(config.memory, "shared_collection", "") or "").strip()


def is_enabled(config: GrimoireConfig) -> bool:
    """La mémoire transverse est opt-in : rien ne traverse sans déclaration."""
    return bool(shared_collection_name(config))


def open_shared(config: GrimoireConfig) -> MemoryManager | None:
    """Ouvre le store transverse, ou ``None`` quand il n'est pas déclaré.

    Le manager retourné vise **une autre collection** que celle du projet : le
    cloisonnement est celui du store, pas d'un filtre appliqué après coup.
    """
    if not is_enabled(config):
        return None

    from dataclasses import replace

    from grimoire.memory.manager import MemoryManager

    name = shared_collection_name(config)
    shared_mem = replace(
        config.memory,
        collection_prefix=slugify(name, default="grimoire_shared").replace("-", "_"),
        weaviate_collection=name if config.memory.weaviate_url else "",
        # Le graphe et la mémoire chaude restent au projet : le transverse ne
        # porte que des motifs, pas des tâches ni du contexte de session.
        knowledge_graph="disabled",
        memory_graph="disabled",
        code_graph="disabled",
        task_memory="disabled",
        short_term_backend="sqlite",
        redis_url="",
    )
    shared_config = replace(config, memory=shared_mem)

    root = shared_home()
    root.mkdir(parents=True, exist_ok=True)
    return MemoryManager.from_config(shared_config, project_root=root)


# ── Garde de promotion ────────────────────────────────────────────────────────

#: Marqueurs d'un fait ancré dans un projet : versions figées, chemins, ports,
#: URLs. Leur vérité dépend d'un état, pas d'un contexte reproductible.
_ANCHORED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"https?://\S+", "contient une URL — ancrée dans un déploiement précis"),
    (r"(?<![\w.])(?:/[\w.-]+){2,}", "contient un chemin absolu — ancré dans une arborescence"),
    (r"\blocalhost:\d+\b|\b127\.0\.0\.1:\d+\b", "contient une adresse locale — ancrée dans une machine"),
)


def _project_aliases(project_name: str) -> tuple[str, ...]:
    """Formes sous lesquelles un nom de projet peut apparaître dans un texte."""
    raw = project_name.strip()
    if not raw:
        return ()
    slug = slugify(raw, default="")
    aliases = {raw.lower(), slug, slug.replace("-", "_"), slug.replace("-", " ")}
    return tuple(sorted(a for a in aliases if len(a) >= 3))


def check_promotable(text: str, *, project_name: str, domain: str) -> PromotionVerdict:
    """Ce souvenir reste-t-il vrai quand on efface le nom du projet ?

    Le test ne peut pas être parfait — distinguer un motif d'un fait demande du
    jugement. Il attrape en revanche les fuites structurelles, qui sont les plus
    fréquentes et les plus coûteuses : un texte qui nomme son projet, cite une
    URL, un chemin absolu ou une adresse locale décrit un état particulier, pas
    un motif réutilisable.
    """
    reasons: list[str] = []

    if not domain.strip():
        reasons.append("aucun domaine déclaré — le transverse se range par domaine, pas en vrac")

    body = text.strip()
    if len(body) < 20:
        reasons.append("texte trop court pour porter un motif réutilisable")

    lowered = body.lower()
    for alias in _project_aliases(project_name):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            reasons.append(
                f"nomme le projet ({alias!r}) — un fait de projet ne devient pas vrai ailleurs"
            )
            break

    for pattern, why in _ANCHORED_PATTERNS:
        if re.search(pattern, body):
            reasons.append(why)

    return PromotionVerdict(ok=not reasons, reasons=tuple(reasons))


# ── Promotion ─────────────────────────────────────────────────────────────────


def _today(today: dt.date | None = None) -> dt.date:
    return today or dt.datetime.now(dt.UTC).date()


def promote(
    shared: MemoryManager,
    text: str,
    *,
    domain: str,
    project_name: str,
    tags: tuple[str, ...] = (),
    force: bool = False,
    today: dt.date | None = None,
) -> MemoryEntry:
    """Écrit un motif dans le store transverse, avec sa provenance.

    Refuse par défaut ce que :func:`check_promotable` rejette. ``force`` laisse
    passer un cas que la garde lit mal, mais l'inscrit dans la provenance :
    un contournement doit rester visible à la relecture.
    """
    verdict = check_promotable(text, project_name=project_name, domain=domain)
    if not verdict.ok and not force:
        raise SharedMemoryError(
            "Promotion refusée :\n  - " + "\n  - ".join(verdict.reasons)
        )

    day = _today(today).isoformat()
    metadata: dict[str, Any] = {
        "wing": f"domain-{slugify(domain)}",
        "scope": SCOPE_SHARED,
        "schema_version": SHARED_SCHEMA_VERSION,
        "domain": slugify(domain),
        "learned_in": [slugify(project_name)],
        "confirmed_in": [],
        "contradicted_in": [],
        "last_confirmed_at": day,
        "promoted_at": day,
        "promotion_forced": bool(not verdict.ok and force),
        "promotion_warnings": list(verdict.reasons),
    }
    return shared.store(text, tags=tags, metadata=metadata)


def confirm(
    shared: MemoryManager,
    entry_id: str,
    *,
    project_name: str,
    today: dt.date | None = None,
) -> MemoryEntry | None:
    """Recontact avec la source : remonte la fraîcheur et note où c'est vérifié.

    C'est le seul mécanisme qui restaure la confiance. Sans lui, tout finit en
    hypothèse — ce qui est le comportement voulu : une connaissance que
    personne ne revérifie ne mérite pas d'être servie comme un fait.
    """
    existing = shared.recall(entry_id)
    if existing is None:
        return None

    metadata = dict(existing.metadata or {})
    slug = slugify(project_name)
    confirmed = [p for p in metadata.get("confirmed_in") or [] if p != slug]
    confirmed.append(slug)
    metadata["confirmed_in"] = confirmed
    metadata["last_confirmed_at"] = _today(today).isoformat()

    updated = getattr(shared, "update", None)
    if callable(updated):
        result = updated(entry_id, metadata=metadata)
        if isinstance(result, MemoryEntry):
            return result
    return shared.store(existing.text, tags=existing.tags, metadata=metadata)


# ── Décroissance de confiance, calculée à la lecture ──────────────────────────


def _parse_day(value: object) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value).strip())
    except (ValueError, AttributeError):
        return None


def freshness_of(entry: MemoryEntry, *, today: dt.date | None = None) -> tuple[str, int | None]:
    """Fraîcheur d'un souvenir transverse et son âge depuis confirmation.

    Une entrée contredite ailleurs tombe directement en hypothèse, quel que
    soit son âge : une contradiction constatée pèse plus qu'une date récente.
    """
    metadata = entry.metadata or {}
    if metadata.get("contradicted_in"):
        return FRESHNESS_HYPOTHESIS, None

    last = _parse_day(metadata.get("last_confirmed_at"))
    if last is None:
        return FRESHNESS_HYPOTHESIS, None

    age = (_today(today) - last).days
    if age < 0:
        age = 0
    if age <= FRESH_DAYS:
        return FRESHNESS_CURRENT, age
    if age <= STALE_DAYS:
        return FRESHNESS_AGING, age
    return FRESHNESS_HYPOTHESIS, age


_CAVEATS = {
    FRESHNESS_CURRENT: "",
    FRESHNESS_AGING: "appris ailleurs, non revérifié récemment",
    FRESHNESS_HYPOTHESIS: "hypothèse — apprise ailleurs, à vérifier avant usage",
}


def _as_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


# ── Restitution en deux passes ────────────────────────────────────────────────


@dataclass
class LayeredRecall:
    """Résultats d'une recherche, séparés par portée et jamais fusionnés muets."""

    project: list[LayeredResult] = field(default_factory=list)
    shared: list[LayeredResult] = field(default_factory=list)

    @property
    def all(self) -> list[LayeredResult]:
        """Projet d'abord : la vérité locale prime sur le motif importé."""
        return [*self.project, *self.shared]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SHARED_SCHEMA_VERSION,
            "project": [r.to_dict() for r in self.project],
            "shared": [r.to_dict() for r in self.shared],
        }


def layered_recall(
    project: MemoryManager,
    shared: MemoryManager | None,
    query: str,
    *,
    limit: int = 5,
    today: dt.date | None = None,
) -> LayeredRecall:
    """Cherche dans le projet puis dans le transverse, en étiquetant l'origine.

    Les deux passes restent séparées jusqu'au bout. Fusionner sans étiquette
    ferait présenter un motif appris ailleurs avec la même assurance qu'un fait
    vérifié ici — c'est exactement le mode de contamination que ce module
    existe pour empêcher.
    """
    result = LayeredRecall()

    for entry in project.search(query, limit=limit):
        result.project.append(
            LayeredResult(entry=entry, scope=SCOPE_PROJECT, freshness=FRESHNESS_CURRENT)
        )

    if shared is None:
        return result

    for entry in shared.search(query, limit=limit):
        metadata = entry.metadata or {}
        freshness, age = freshness_of(entry, today=today)
        result.shared.append(
            LayeredResult(
                entry=entry,
                scope=SCOPE_SHARED,
                freshness=freshness,
                learned_in=_as_tuple(metadata.get("learned_in")),
                confirmed_in=_as_tuple(metadata.get("confirmed_in")),
                days_since_confirmation=age,
                caveat=_CAVEATS[freshness],
            )
        )
    return result
