"""The déclencheur — propose an artifact on repeated non-choice, never create it alone.

Issue #395, third path of #355 made concrete: reading the aggregated misses
(:meth:`grimoire.traces.ledger.TraceLedger.agent_miss_counts`, issue #394),
this module writes a *proposal* — never the artifact itself — the moment a
specialty's non-choice count crosses a configurable threshold (default 2,
never 1). A proposal is a plain, project-owned YAML file under
``_grimoire-output/proposals/<slug>.yaml`` with a ``pending`` / ``accepted`` /
``rejected`` status; accepting one writes a real agent (or attaches a real
skill) through the exact same path issue #367 fixed for
``grimoire_add_agent`` (:mod:`grimoire.tools.agent_creation`). Nothing here
ever calls an LLM: the name, role, and employment clause are mechanical
templates filled from the category/specialty/fallback-agent labels the miss
traces already carry — never from the content of a request, which
:func:`grimoire.hosts.decisions.record_agent_miss` never stores in the first
place.

Artifact-type decision (``docs/artifact-doctrine.md``): a skill's sequence
must be writable in advance *and* attachable to an existing agent; a miss
with no attachable surface has, by definition, nothing to attach anything
to, so it can only become an agent. Mechanically, that reads as: an
attachable agent was found → propose a **skill** attached to it; none was
found → propose an **agent**. This is the one place in the kit that decides
skill vs. agent without a human in the loop, and it does so from observable
facts, not from judgment.

"Attachable" excludes one thing unconditionally (issue #402): the project's
*entry persona* — the one ``grimoire.hosts.decisions.entry_persona_context``
hands the session-start turn to, named by
``grimoire.hosts.collect.entry_agent_name`` (``concierge`` by default). It
routes requests to specialists; it does not do specialist work itself, so
the doctrine's "agent that does the work" never resolves to it. Because the
concierge is nearly always the fallback a miss records — it is the one
running the triage that produces the miss in the first place — treating its
name the same as any other observed fallback would turn almost every
proposal into "skill attached to concierge", exactly backwards from the
doctrine. So: a fallback that is *not* the entry persona is used directly
(``carrier_reason`` "repli observé"). A fallback that is empty or *is* the
entry persona is treated as no fallback at all, and a second, independent
search runs before giving up on a skill: among the project's declared
agents (minus the entry persona), one whose ``use_when`` names the miss's
category, or — for categories that read as execution work — whose tool
faisceau includes ``execute``, is a plausible carrier
(:func:`_category_carrier`). Exactly one match attaches a skill there
(``carrier_reason`` "porteur par catégorie : <name>"); zero or several
matches are as unusable as no fallback, and fall through to proposing a
brand-new agent (``carrier_reason`` "persona d'entrée exclue, aucun
porteur : agent"). ``fallback_agent`` itself is never rewritten by this
search — it stays the raw fact the ledger observed; ``carrier_reason`` is
the separate field that explains what the déclencheur did with it.

Rejection is sticky but not permanent: refusing a proposal snapshots the
miss count at the moment of refusal (``rejected_at_count``); the same
specialty is only proposed again once its count has at least doubled since —
"a third miss right after a refusal" must not resurrect it, per the issue's
own stop criterion.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireRuntimeError

__all__ = [
    "Proposal",
    "accept_proposal",
    "count_pending",
    "list_proposals",
    "reject_proposal",
    "rust_backend_available",
    "sync_proposals",
]

try:
    import grimoire_traces_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_traces_core`` module is importable.

    Purely informational (used by tests and diagnostics) — every call site
    below decides its own backend fresh via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend this module's Rust-optional functions should use.

    Own copy of the precedent set by ``grimoire.traces.ledger`` (issue
    #354): each module backed by a Rust crate reads its own env var
    independently. Reads ``GRIMOIRE_TRACES_BACKEND`` fresh every call so
    tests can flip it with ``monkeypatch.setenv`` — the same variable as
    ``grimoire.traces.ledger``, since both modules are the same crate.
    """
    override = os.environ.get("GRIMOIRE_TRACES_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireRuntimeError(
                "GRIMOIRE_TRACES_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_traces_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-traces-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireRuntimeError(f"GRIMOIRE_TRACES_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None

#: Never 1 — the issue is explicit: repetition, not a single non-choice, is
#: what earns a proposal.
DEFAULT_THRESHOLD = 2
_MIN_THRESHOLD = 2

_STATUSES = frozenset({"pending", "accepted", "rejected"})

#: Substrings that mechanically suggest a specialty needs to *run* something,
#: not just read and search — a small, documented heuristic, never a model
#: call. Keeps freshly proposed specialists from all sharing the exact same
#: tool boundary as soon as more than one exists (the fingerprint guard in
#: ``grimoire.hosts.surface`` would otherwise refuse the second one on sight).
_EXECUTION_HINTS = (
    "infra", "ops", "terraform", "ansible", "deploy", "ci", "cd", "pipeline",
    "docker", "kubernetes", "k8s", "build", "test", "script", "release",
)


@dataclass(frozen=True, slots=True)
class Proposal:
    """One artifact proposal, exactly as stored on disk."""

    slug: str
    specialty: str
    artifact_type: str  # "agent" | "skill"
    status: str  # "pending" | "accepted" | "rejected"
    count: int
    category: str = ""
    fallback_agent: str = ""
    carrier_reason: str = ""
    """Why ``target_agent`` (or the absence of one) was chosen — one of
    "repli observé", "porteur par catégorie : <name>", or "persona
    d'entrée exclue, aucun porteur : agent" (see :func:`_resolve_carrier`).
    ``fallback_agent`` above stays the raw observed fact; this field is the
    déclencheur's account of what it did with it."""
    first_seen: str = ""
    last_seen: str = ""
    created_at: str = ""
    agent_role: str = ""
    use_when: str = ""
    dont_use_when: str = ""
    tool_boundary: str = ""
    tools: str = ""
    target_agent: str = ""
    accepted_at: str = ""
    accepted_path: str = ""
    rejected_at: str = ""
    rejected_at_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "specialty": self.specialty,
            "artifact_type": self.artifact_type,
            "status": self.status,
            "count": self.count,
            "category": self.category,
            "fallback_agent": self.fallback_agent,
            "carrier_reason": self.carrier_reason,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "created_at": self.created_at,
            "agent_role": self.agent_role,
            "use_when": self.use_when,
            "dont_use_when": self.dont_use_when,
            "tool_boundary": self.tool_boundary,
            "tools": self.tools,
            "target_agent": self.target_agent,
            "accepted_at": self.accepted_at,
            "accepted_path": self.accepted_path,
            "rejected_at": self.rejected_at,
            "rejected_at_count": self.rejected_at_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Proposal:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


# ── Naming — mechanical, never an LLM call ──────────────────────────────────


def _slugify(text: str) -> str:
    """Lowercase, ASCII-ish, hyphen-separated — safe as a filename and an id.

    Délègue à ``grimoire_traces_core.slugify`` (issue #354) quand le backend
    Rust est actif — voir :func:`_use_rust_backend`.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return str(_rust_core.slugify(text))
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "specialite"


def _agent_slug(specialty: str) -> str:
    return f"{_slugify(specialty)}-specialist"


def _skill_slug(specialty: str) -> str:
    return _slugify(specialty)


def _guess_tools(category: str, specialty: str) -> str:
    """Délègue à ``grimoire_traces_core.guess_tools`` (issue #354) quand le
    backend Rust est actif — voir :func:`_use_rust_backend`."""
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return str(_rust_core.guess_tools(category, specialty))
    haystack = f"{category} {specialty}".lower()
    if any(hint in haystack for hint in _EXECUTION_HINTS):
        return "read, search, execute"
    return "read, search"


def _employment_clause(specialty: str, category: str, carrier: str) -> tuple[str, str]:
    """Mechanical ``(use_when, dont_use_when)`` from the labels a miss carries.

    *carrier* is the agent the proposal actually attaches to (or empty for a
    fresh agent proposal) — not the raw ``fallback_agent`` fact, which may
    name the entry persona even when the proposal attaches elsewhere or
    nowhere (see :func:`_resolve_carrier`). Naming the entry persona here
    would misdescribe the boundary this specialist is meant to relieve.

    Délègue à ``grimoire_traces_core.employment_clause`` (issue #354) quand
    le backend Rust est actif — voir :func:`_use_rust_backend`.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        use_when, dont_use_when = _rust_core.employment_clause(specialty, category, carrier)
        return str(use_when), str(dont_use_when)
    if category:
        use_when = (
            f"Une demande classée « {category} » cherche une compétence "
            f"« {specialty} » qu'aucun agent déclaré ne couvre."
        )
    else:
        use_when = f"Une demande cherche une compétence « {specialty} » qu'aucun agent déclaré ne couvre."
    if carrier:
        dont_use_when = (
            f"Toute demande déjà couverte par {carrier} ou un autre agent déclaré — "
            f"ce spécialiste n'existe que pour « {specialty} »."
        )
    else:
        dont_use_when = (
            f"Toute demande déjà couverte par un agent déclaré — ce spécialiste n'existe "
            f"que pour « {specialty} »."
        )
    return use_when, dont_use_when


def _entry_persona_name(project_root: Path) -> str:
    """The name ``project-context.yaml`` designates as the entry persona.

    This is the configured *role*, not proof that a matching agent file
    exists on disk — the same source ``entry_persona_context`` in
    :mod:`grimoire.hosts.decisions` reads to decide who receives the
    session-start hand-off (``concierge`` when unset). Best-effort: a config
    that fails to parse yields ``""``, which simply matches nothing below
    rather than raising into the déclencheur.
    """
    try:
        from grimoire.hosts.collect import entry_agent_name

        return entry_agent_name(project_root)
    except Exception:
        return ""


def _category_carrier(project_root: Path, category: str, entry_name: str) -> str:
    """The one declared agent whose surface plausibly covers *category*.

    Best-effort over ``collect_agents`` — a project whose agents fail to
    collect (unknown skill, missing context…) yields no carrier here, never
    an exception: this function only ever *suggests* an attachment point,
    it never blocks the déclencheur itself. The entry persona is excluded
    unconditionally, even if its own ``use_when`` happens to mention the
    category — it routes, it does not carry (issue #402).

    A match is either textual (the category's own word appears in the
    agent's declared ``use_when``, as a whole word — a substring test would
    let a category like ``ci`` match ``spécifique`` or ``ops`` match
    ``développe``) or structural (the category reads as execution work —
    the same ``_EXECUTION_HINTS`` heuristic used to guess a *new* agent's
    tools — and the candidate already carries the ``execute`` tool). Two
    matches are as unusable as zero: attaching a skill to an ambiguous
    carrier is worse than proposing a fresh agent a human can place by
    hand, so anything but exactly one candidate returns ``""``.

    Toute l'E/S (``collect_agents``, lecture de fichier, frontmatter) reste
    ici — seul le jugement pur (mot entier, heuristique d'exécution,
    exclusion de la persona, unicité du candidat) délègue à
    ``grimoire_traces_core.category_carrier`` (issue #354) quand le backend
    Rust est actif, voir :func:`_use_rust_backend`.
    """
    if not category:
        return ""
    try:
        from grimoire.hosts.collect import collect_agents, effective_agent_frontmatter
        from grimoire.hosts.surface import ToolVerb

        agents = collect_agents(project_root)
    except Exception:
        return ""

    raw_candidates: list[tuple[str, str, bool]] = []
    for agent in agents:
        # `effective_agent_frontmatter`, pas une relecture directe de
        # `agent.definition_ref` : depuis l'issue #427, un override partiel
        # (`extends: kit`) ne redéfinit `use_when` que s'il le veut, et
        # `definition_ref` pointe alors vers le fichier kit — une relecture
        # brute y trouverait le `use_when` du kit, jamais celui, différent,
        # que l'override a choisi.
        use_when = str(effective_agent_frontmatter(project_root, agent).get("use_when") or "")
        raw_candidates.append((agent.name, use_when, ToolVerb.EXECUTE in agent.tools))

    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        result = _rust_core.category_carrier(category, entry_name, raw_candidates)
        return str(result) if result else ""

    category_lower = category.lower()
    category_word = re.compile(rf"\b{re.escape(category_lower)}\b")
    is_execution_category = any(hint in category_lower for hint in _EXECUTION_HINTS)
    candidates: list[str] = []
    for name, use_when, has_execute in raw_candidates:
        if entry_name and name == entry_name:
            continue
        matches_use_when = bool(category_word.search(use_when.lower()))
        matches_execute = is_execution_category and has_execute
        if matches_use_when or matches_execute:
            candidates.append(name)

    return candidates[0] if len(candidates) == 1 else ""


def _resolve_carrier(project_root: Path, *, category: str, fallback_agent: str) -> tuple[str, str]:
    """Who a proposal should attach to, and why (issue #402).

    A fallback agent that is not the project's entry persona is a directly
    observed carrier. An empty fallback, or one that names the entry
    persona, is "no carrier yet" — the doctrine's routing agent never
    attaches a skill — so a category-based search over the project's other
    declared agents (:func:`_category_carrier`) gets a second, independent
    chance before the déclencheur gives up and proposes a brand-new agent.

    L'E/S (résolution de la persona d'entrée, recherche par catégorie) reste
    ici ; seul l'arbitrage final délègue à
    ``grimoire_traces_core.resolve_carrier`` (issue #354) quand le backend
    Rust est actif, voir :func:`_use_rust_backend`.
    """
    entry_name = _entry_persona_name(project_root)
    # Court-circuit conserve : la recherche par catégorie (I/O —
    # `collect_agents` + lecture de chaque fichier d'agent) ne s'exécute que
    # si le repli observé ne suffit pas a lui seul, sous les deux backends.
    if fallback_agent and fallback_agent != entry_name:
        if _use_rust_backend():
            assert _rust_core is not None  # guarded by _use_rust_backend
            carrier, reason = _rust_core.resolve_carrier(fallback_agent, entry_name, None)
            return str(carrier), str(reason)
        return fallback_agent, "repli observé"
    category_candidate = _category_carrier(project_root, category, entry_name) or None
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        carrier, reason = _rust_core.resolve_carrier(fallback_agent, entry_name, category_candidate)
        return str(carrier), str(reason)
    if category_candidate:
        return category_candidate, f"porteur par catégorie : {category_candidate}"
    return "", "persona d'entrée exclue, aucun porteur : agent"


def _build_proposal(
    *,
    specialty: str,
    count: int,
    category: str,
    fallback_agent: str,
    carrier: str,
    carrier_reason: str,
    first_seen: str,
    last_seen: str,
) -> Proposal:
    """A fresh, pending proposal from what the ledger — and, when the raw
    fallback offers no usable carrier, a category-based search — observed.

    *carrier* and *carrier_reason* come from :func:`_resolve_carrier` and
    decide the artifact type; *fallback_agent* is stored unchanged as the
    raw fact the ledger recorded, even when it differs from *carrier* (the
    entry-persona case).

    Le gabarit mécanique complet (nom, rôle, ``use_when``/``dont_use_when``,
    outils par défaut) délègue en un seul appel à
    ``grimoire_traces_core.build_proposal_fields`` (issue #354) quand le
    backend Rust est actif — voir :func:`_use_rust_backend`. ``count``,
    ``category``, ``fallback_agent``, ``carrier_reason``,
    ``first_seen``/``last_seen``/``created_at`` sont des faits observés ou
    des horodatages, jamais du gabarit : ils restent assemblés ici, sous les
    deux backends.
    """
    now = datetime.now(UTC).isoformat()
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        (
            slug,
            artifact_type,
            agent_role,
            use_when,
            dont_use_when,
            tool_boundary,
            tools,
            target_agent,
        ) = _rust_core.build_proposal_fields(specialty, category, carrier)
        return Proposal(
            slug=str(slug),
            specialty=specialty,
            artifact_type=str(artifact_type),
            status="pending",
            count=count,
            category=category,
            fallback_agent=fallback_agent,
            carrier_reason=carrier_reason,
            first_seen=first_seen or last_seen,
            last_seen=last_seen,
            created_at=now,
            agent_role=str(agent_role),
            use_when=str(use_when),
            dont_use_when=str(dont_use_when),
            tool_boundary=str(tool_boundary),
            tools=str(tools),
            target_agent=str(target_agent),
        )
    use_when, dont_use_when = _employment_clause(specialty, category, carrier)
    if carrier:
        # Attachable to an existing surface → the doctrine's skill branch.
        return Proposal(
            slug=_skill_slug(specialty),
            specialty=specialty,
            artifact_type="skill",
            status="pending",
            count=count,
            category=category,
            fallback_agent=fallback_agent,
            carrier_reason=carrier_reason,
            first_seen=first_seen or last_seen,
            last_seen=last_seen,
            created_at=now,
            agent_role=f"Spécialiste {specialty} pour les demandes {category or 'sans catégorie'}",
            use_when=use_when,
            dont_use_when=dont_use_when,
            tool_boundary="",
            tools="",
            target_agent=carrier,
        )
    return Proposal(
        slug=_agent_slug(specialty),
        specialty=specialty,
        artifact_type="agent",
        status="pending",
        count=count,
        category=category,
        fallback_agent=fallback_agent,
        carrier_reason=carrier_reason,
        first_seen=first_seen or last_seen,
        last_seen=last_seen,
        created_at=now,
        agent_role=f"Spécialiste {specialty} pour les demandes {category or 'sans catégorie'}",
        use_when=use_when,
        dont_use_when=dont_use_when,
        tool_boundary=(
            f"Lecture et recherche circonscrites au périmètre « {specialty} », distinct du "
            "périmètre générique de l'agent de repli."
        ),
        tools=_guess_tools(category, specialty),
    )


# ── Storage ──────────────────────────────────────────────────────────────────


def _proposals_dir(project_root: Path) -> Path:
    from grimoire.core.standard_generation import PROPOSALS_DIR

    return project_root.resolve() / PROPOSALS_DIR


def _proposal_path(project_root: Path, slug: str) -> Path:
    return _proposals_dir(project_root) / f"{slug}.yaml"


def _load_proposal(path: Path) -> Proposal | None:
    if not path.is_file():
        return None
    try:
        from grimoire.tools._common import load_yaml

        data = load_yaml(path)
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") not in _STATUSES:
        return None
    try:
        return Proposal.from_dict(data)
    except Exception:
        return None


def _save_proposal(path: Path, proposal: Proposal) -> None:
    from ruamel.yaml import YAML

    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(proposal.to_dict(), fh)


def _configured_threshold(project_root: Path) -> int:
    """``proposals.threshold`` from ``project-context.yaml`` — never below 2.

    Le clamp délègue à ``grimoire_traces_core.clamp_threshold`` (issue #354)
    quand le backend Rust est actif — voir :func:`_use_rust_backend`.
    """
    config_path = project_root.resolve() / "project-context.yaml"
    if not config_path.is_file():
        return DEFAULT_THRESHOLD
    try:
        from grimoire.tools._common import load_yaml

        data = load_yaml(config_path)
        raw = data.get("proposals", {}).get("threshold") if isinstance(data, dict) else None
        value = int(raw) if raw is not None else DEFAULT_THRESHOLD
    except Exception:
        return DEFAULT_THRESHOLD
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return int(_rust_core.clamp_threshold(value))
    return max(_MIN_THRESHOLD, value)


def _sync_decision(
    threshold_raw: int,
    observed_count: int,
    existing: Proposal | None,
) -> tuple[str, int, int | None]:
    """The central branch decision of :func:`sync_proposals` (issue #395),
    isolated from all file I/O so it can be handed to Rust as one call.

    Returns ``(action, effective_threshold, reopen_at)`` — ``action`` is one
    of ``skip``/``create``/``keep_accepted``/``refresh_pending``/
    ``keep_rejected``/``reopen``. Délègue à
    ``grimoire_traces_core.sync_decision`` quand le backend Rust est actif —
    voir :func:`_use_rust_backend`. Le chemin Python ci-dessous est la
    référence : il clampe le seuil lui-même plutôt que de faire confiance à
    l'appelant, exactement comme le fait le cœur Rust.
    """
    existing_status = existing.status if existing is not None else None
    existing_count = existing.count if existing is not None else 0
    existing_rejected_at_count = existing.rejected_at_count if existing is not None else None
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        action, threshold, reopen_at = _rust_core.sync_decision(
            threshold_raw, observed_count, existing_status, existing_count, existing_rejected_at_count
        )
        return str(action), int(threshold), (int(reopen_at) if reopen_at is not None else None)

    threshold = max(_MIN_THRESHOLD, threshold_raw)
    if existing_status is None:
        return ("skip" if observed_count < threshold else "create"), threshold, None
    if existing_status == "accepted":
        return "keep_accepted", threshold, None
    if existing_status == "rejected":
        # `existing.rejected_at_count or effective_threshold` cote reference
        # Python historique : `0` est aussi falsy que `None`.
        base = existing_rejected_at_count or threshold
        reopen_at = 2 * max(base, 1)
        return ("keep_rejected" if observed_count < reopen_at else "reopen"), threshold, reopen_at
    return "refresh_pending", threshold, None


# ── The déclencheur ──────────────────────────────────────────────────────────


def sync_proposals(project_root: Path, *, threshold: int | None = None) -> list[Proposal]:
    """Read the ledger's aggregated misses, refresh every proposal file.

    Best-effort on the ledger read alone (issue #394's own contract: an
    absent or unreadable trace log is zero observations, never an error) —
    everything downstream of that read is pure local file I/O and is allowed
    to raise, because a broken proposals directory is a real defect, not an
    absent signal.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import UNNAMED_SPECIALTY, TraceLedger

    root = project_root.resolve()
    raw_threshold = threshold if threshold is not None else _configured_threshold(root)
    effective_threshold = (
        int(_rust_core.clamp_threshold(raw_threshold))
        if _use_rust_backend() and _rust_core is not None
        else max(_MIN_THRESHOLD, raw_threshold)
    )

    try:
        misses = TraceLedger(root / TRACES_DIR).agent_miss_counts()
    except Exception:
        misses = {}

    proposals_dir = _proposals_dir(root)
    results: list[Proposal] = []

    for specialty, stats in misses.items():
        if specialty == UNNAMED_SPECIALTY:
            # A non-choice with nothing to name cannot become a slug — it
            # stays a signal for `registry dispatches`, never a proposal.
            continue
        count = int(stats.get("count", 0) or 0)
        if count < effective_threshold:
            continue
        category = str(stats.get("category") or "")
        fallback_agent = str(stats.get("fallback_agent") or "")
        last_seen = str(stats.get("last_seen") or "")

        # The artifact type depends on whether a carrier resolves (issue
        # #402), and that resolution can drift between syncs — most notably,
        # accepting *this very* proposal writes a new agent file whose
        # mechanical `use_when` names its own category, which would then
        # look like a category carrier to a naive re-guess. So an existing
        # proposal is found by checking both shapes the specialty could be
        # stored under, never by re-deriving the type first and hoping it
        # still matches what is on disk; the type is only (re)computed below
        # when there is genuinely nothing there yet, or a rejection is old
        # enough to reopen.
        agent_path = proposals_dir / f"{_agent_slug(specialty)}.yaml"
        skill_path = proposals_dir / f"{_skill_slug(specialty)}.yaml"
        path, existing = agent_path, _load_proposal(agent_path)
        if existing is None:
            existing = _load_proposal(skill_path)
            if existing is not None:
                path = skill_path

        # `effective_threshold` is already clamped ; `_sync_decision`
        # re-clampe (idempotent) — jamais un second seuil différent. Seul
        # l'arbitrage accepted/rejected/pending (et le calcul de
        # ``reopen_at``) est délégué ici : l'ordre des opérations et l'E/S
        # autour restent inchangés, `existing is None` reste géré
        # directement (son action est nécessairement "create" : le
        # `count < effective_threshold` ci-dessus l'a déjà exclu sinon).
        action, _, reopen_at = _sync_decision(effective_threshold, count, existing)

        if existing is None:
            carrier, carrier_reason = _resolve_carrier(root, category=category, fallback_agent=fallback_agent)
            path = skill_path if carrier else agent_path
            proposal = _build_proposal(
                specialty=specialty, count=count, category=category,
                fallback_agent=fallback_agent, carrier=carrier, carrier_reason=carrier_reason,
                first_seen=last_seen, last_seen=last_seen,
            )
            _save_proposal(path, proposal)
            results.append(proposal)
            continue

        if action == "keep_accepted":
            results.append(existing)
            continue

        if action == "keep_rejected":
            refreshed = replace(existing, count=count, last_seen=last_seen)
            _save_proposal(path, refreshed)
            results.append(refreshed)
            continue

        if action == "reopen":
            assert reopen_at is not None  # "reopen" toujours accompagné de son seuil
            carrier, carrier_reason = _resolve_carrier(root, category=category, fallback_agent=fallback_agent)
            new_path = skill_path if carrier else agent_path
            reopened = _build_proposal(
                specialty=specialty, count=count, category=category,
                fallback_agent=fallback_agent, carrier=carrier, carrier_reason=carrier_reason,
                first_seen=existing.first_seen, last_seen=last_seen,
            )
            if new_path != path:
                # The re-evaluated type no longer matches the shape the
                # rejected proposal was stored under — move it rather than
                # leaving a stale duplicate at the old slug.
                path.unlink(missing_ok=True)
            _save_proposal(new_path, reopened)
            results.append(reopened)
            continue

        # Still pending (action == "refresh_pending"): refresh the observed
        # facts, keep identity/status.
        refreshed = replace(
            existing,
            count=count,
            last_seen=last_seen,
            category=category or existing.category,
            fallback_agent=fallback_agent or existing.fallback_agent,
        )
        _save_proposal(path, refreshed)
        results.append(refreshed)

    # Proposals the current ledger no longer surfaces (rotated, pruned) stay
    # visible if a decision was already recorded for them — a proposal
    # accepted or rejected yesterday does not vanish because today's ledger
    # is thinner.
    seen = {p.slug for p in results}
    if proposals_dir.is_dir():
        for path in sorted(proposals_dir.glob("*.yaml")):
            if path.stem in seen:
                continue
            existing = _load_proposal(path)
            if existing is not None:
                results.append(existing)
                seen.add(path.stem)

    return sorted(results, key=lambda p: p.slug)


def list_proposals(project_root: Path, *, sync: bool = True, threshold: int | None = None) -> list[Proposal]:
    """Every proposal this project has, optionally refreshed from the ledger first.

    ``sync=False`` is a pure disk read (no ``TraceLedger`` involved at all) —
    what the SessionStart line uses, so a session start never depends on the
    trace ledger being writable.
    """
    if sync:
        return sync_proposals(project_root, threshold=threshold)
    proposals_dir = _proposals_dir(project_root)
    if not proposals_dir.is_dir():
        return []
    found = (_load_proposal(path) for path in sorted(proposals_dir.glob("*.yaml")))
    return sorted((p for p in found if p is not None), key=lambda p: p.slug)


def count_pending(project_root: Path) -> int:
    """Pending proposals, read straight off disk — best-effort, no ledger call."""
    try:
        return sum(1 for p in list_proposals(project_root, sync=False) if p.status == "pending")
    except Exception:
        return 0


def accept_proposal(project_root: Path, slug: str) -> dict[str, Any]:
    """Validate and write the real artifact — the only door creation goes through.

    Never marks a proposal accepted unless the artifact was actually written
    (and, for an agent, actually resolves without colliding with another
    agent's tool/context/skill faisceau — the distinction guard from #372).
    A failed write, or a write that breaks that guard, is rolled back and
    reported honestly: this command never says it created what it did not.

    Syncs first: a proposal whose threshold was only just crossed may not
    have a file on disk yet if nobody called ``list`` in between — accepting
    it must work the moment `grimoire proposals list` would already show it,
    not only after a separate read.
    """
    root = project_root.resolve()
    sync_proposals(root)
    path = _proposal_path(root, slug)
    proposal = _load_proposal(path)
    if proposal is None:
        return {"ok": False, "error": f"proposition introuvable : {slug}"}
    if proposal.status == "accepted":
        return {"ok": False, "error": "proposition déjà acceptée", "path": proposal.accepted_path}

    try:
        result = _accept_skill(root, proposal) if proposal.artifact_type == "skill" else _accept_agent(root, proposal)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    accepted = replace(
        proposal,
        status="accepted",
        accepted_at=datetime.now(UTC).isoformat(),
        accepted_path=str(result.get("path", "")),
    )
    _save_proposal(path, accepted)
    return {"ok": True, **result}


def _accept_agent(project_root: Path, proposal: Proposal) -> dict[str, Any]:
    from grimoire.core.exceptions import GrimoireAgentError
    from grimoire.hosts import collect
    from grimoire.tools.agent_creation import AgentCreationError, create_agent_file

    try:
        result = create_agent_file(
            project_root,
            proposal.slug,
            agent_role=proposal.agent_role,
            use_when=proposal.use_when,
            dont_use_when=proposal.dont_use_when,
            tool_boundary=proposal.tool_boundary,
            tools=proposal.tools,
        )
    except AgentCreationError as exc:
        raise RuntimeError(exc.message) from exc

    dest = Path(result["path"])
    try:
        skills = collect.collect_skills(project_root)
        collect.collect_agents(project_root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"agent créé puis annulé : {exc} (faisceau identique à un agent existant)"
        ) from exc
    return result


def _accept_skill(project_root: Path, proposal: Proposal) -> dict[str, Any]:
    from grimoire.core import layout

    if not proposal.target_agent:
        raise RuntimeError("aucun agent de repli à qui attacher ce skill")

    skills_dir = layout.overrides_dir(project_root) / layout.SKILLS_SUBDIR
    dest = skills_dir / f"{proposal.slug}.md"
    if dest.is_file():
        raise RuntimeError(f"un skill '{proposal.slug}' existe déjà à {dest}")

    body = (
        f"---\n"
        f'name: "{proposal.slug}"\n'
        f'description: "{proposal.use_when}"\n'
        f"---\n\n"
        f"# {proposal.agent_role}\n\n"
        f"Proposé après {proposal.count} non-choix observés sur la spécialité "
        f"« {proposal.specialty} » (catégorie « {proposal.category or 'non classée'} »).\n\n"
        f"**Employer quand** : {proposal.use_when}\n\n"
        f"**Ne pas employer quand** : {proposal.dont_use_when}\n\n"
        "<!-- Ce corps est un gabarit mécanique — complétez la séquence réelle "
        "avant de vous y fier. -->\n"
    )
    skills_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")

    from grimoire.tools.workspace_routes import _agent_skill_action

    try:
        _agent_skill_action(project_root, proposal.target_agent, {"skill": proposal.slug, "action": "assign"})
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"skill créé puis annulé : impossible de l'attacher à {proposal.target_agent} ({exc})") from exc

    return {"status": "created", "artifact_type": "skill", "path": str(dest), "attached_to": proposal.target_agent}


def reject_proposal(project_root: Path, slug: str) -> dict[str, Any]:
    """Mark a proposal refused, snapshotting the count it was refused at.

    Syncs first, same reason as :func:`accept_proposal`: a proposal must be
    refusable the moment it would show up in ``list``, not only after a
    separate read materialized its file.
    """
    root = project_root.resolve()
    sync_proposals(root)
    path = _proposal_path(root, slug)
    proposal = _load_proposal(path)
    if proposal is None:
        return {"ok": False, "error": f"proposition introuvable : {slug}"}
    if proposal.status == "accepted":
        return {"ok": False, "error": "proposition déjà acceptée — le refus n'a plus d'effet"}

    rejected = replace(
        proposal,
        status="rejected",
        rejected_at=datetime.now(UTC).isoformat(),
        rejected_at_count=proposal.count,
    )
    _save_proposal(path, rejected)
    return {"ok": True, "status": "rejected", "slug": slug}
