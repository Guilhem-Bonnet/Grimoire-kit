"""Tests for the host surface layer: collection, emission, decisions, wire format.

The promise under test is narrow and checkable: one description of the project,
the same governance decision on every host, and a refusal that actually refuses
where the host can refuse.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core import layout
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.core.claude_activation import activation_context_text
from grimoire.hosts.capabilities import gaps_for, profile_for, resolve_host
from grimoire.hosts.collect import build_surface, collect_agents, default_permissions, infer_tools, parse_frontmatter
from grimoire.hosts.decisions import (
    DECISIONS,
    Decision,
    HookInput,
    Outcome,
    classify_tool,
    decide_activation,
    decide_context_capsule,
    decide_evidence_gate,
    decide_subagent_gate,
    decide_tool_policy,
    entry_persona_context,
)
from grimoire.hosts.emitters import apply_plan, emitter_for, supported_hosts
from grimoire.hosts.emitters.claude_code import _matcher
from grimoire.hosts.runtime import normalize_input, parse_event, render, run_hook
from grimoire.hosts.secrets import SECRET_RULES, secret_read_globs
from grimoire.hosts.surface import Enforcement, HookEvent, HookSpec, ToolVerb

AGENT_DIR = Path("_grimoire/_config/custom/agents")


def _write_agent(
    root: Path,
    name: str,
    body: str,
    *,
    tools: str = "",
    reasoning: str = "medium",
    cost: str = "medium",
    max_turns: int | None = None,
    context: tuple[str, ...] = (),
) -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    header = [
        "---",
        f'name: "{name}"',
        f'description: "{name} — rôle de test"',
    ]
    if tools:
        header.append(f"tools: [{tools}]")
    header += [
        "model_affinity:",
        f"  reasoning: {reasoning}",
        "  context_window: medium",
        f"  cost: {cost}",
    ]
    if max_turns is not None:
        header.append(f"max_turns: {max_turns}")
    if context:
        rendered = ", ".join(f"'{c}'" for c in context)
        header.append(f"context: [{rendered}]")
    header += [
        "---",
        "",
    ]
    (root / AGENT_DIR / f"{name}.md").write_text("\n".join(header) + body + "\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_agent(tmp_path, "concierge", "Tu tries et tu routes. Tu exécutes des diagnostics.")
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.", reasoning="low")
    return tmp_path


@pytest.fixture
def governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


# ── Collection ───────────────────────────────────────────────────────────────


def test_frontmatter_parsing_survives_a_leading_comment() -> None:
    text = "<!-- ARCHETYPE: meta -->\n---\nname: x\n---\ncorps"
    meta, body = parse_frontmatter(text)
    assert meta["name"] == "x"
    assert body.strip() == "corps"


def test_declared_tools_win_over_inference(project: Path) -> None:
    _write_agent(project, "auditor", "Tu rédiges et tu exécutes.", tools="'read'")
    by_name = {a.name: a for a in collect_agents(project)}
    assert by_name["auditor"].tools == (ToolVerb.READ,)
    assert by_name["auditor"].tools_origin == "declared"
    assert by_name["scribe"].tools_origin == "inferred"


def test_inference_grants_write_only_on_an_explicit_signal() -> None:
    assert ToolVerb.EDIT not in infer_tools("Tu observes et tu rapportes.", "")
    assert ToolVerb.EDIT in infer_tools("Tu rédiges la documentation.", "")
    assert ToolVerb.EXECUTE in infer_tools("Tu lances les tests.", "")


def test_an_unrendered_placeholder_name_is_not_a_collected_agent(project: Path) -> None:
    """Un gabarit non rendu (``name: "{{agent_tag}}"``) n'est pas un agent installé.

    ``layout.agent_identity`` — la lecture que le diagnostic emploie depuis
    #349 — n'y reconnaît aucun tag, donc aucune identité : ce fichier n'existe
    pas encore en tant qu'agent, seulement en tant que gabarit à compléter.
    ``collect_agents`` doit s'accorder sur ce point plutôt que retomber sur le
    nom de fichier (issue #381).
    """
    (project / AGENT_DIR / "custom-agent.md").write_text(
        '---\nname: "{{agent_tag}}"\ndescription: "{{agent_role}}"\n---\nCorps.\n',
        encoding="utf-8",
    )
    names = {a.name for a in collect_agents(project)}
    assert "custom-agent" not in names
    assert not [n for n in names if "{{" in n]


def test_installed_agents_et_collect_agents_nomment_pareil(project: Path) -> None:
    """Garde #381 : les deux lectures de « quel agent existe » s'accordent.

    ``layout.installed_agents()`` (diagnostic, outil d'ajout) et
    ``collect_agents()`` (construction de surface, ``host sync``, cockpit)
    répondent à la même question sur les mêmes fichiers — gabarit non rendu
    compris. Un même fichier nommé différemment selon le lecteur est
    exactement le défaut de #381.
    """
    (project / AGENT_DIR / "custom-agent.md").write_text(
        '---\nname: "{{agent_tag}}"\ndescription: "{{agent_role}}"\n---\nCorps.\n',
        encoding="utf-8",
    )
    installed_tags = set(layout.installed_agents(project))
    collected_names = {a.name for a in collect_agents(project)}
    assert installed_tags == collected_names


def test_an_override_wins_over_the_kit_tier(tmp_path: Path) -> None:
    """La persona du projet doit gagner sur celle que le kit livre.

    Régression croisée entre la frontière kit/overrides et cette couche : tant
    que l'émetteur balayait les répertoires en direct, il projetait la
    définition du tier kit même quand le projet en avait posé une dans
    ``_grimoire/overrides/agents/``. Le wrapper généré pointait alors le
    fichier que la prochaine mise à jour réécrit, et la customisation
    disparaissait sans un mot.
    """
    for tier, body in ((layout.KIT_DIR, "Version livrée."), (layout.OVERRIDES_DIR, "Version du projet.")):
        directory = tmp_path / tier / layout.AGENTS_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "concierge.md").write_text(
            '---\nname: "concierge"\ndescription: "tri"\n---\n' + body + "\n", encoding="utf-8"
        )

    (concierge,) = [a for a in collect_agents(tmp_path) if a.name == "concierge"]
    assert concierge.definition_ref == f"{layout.OVERRIDES_DIR}/{layout.AGENTS_SUBDIR}/concierge.md"

    emitter = emitter_for(HostId.GITHUB_COPILOT)
    apply_plan(emitter.plan(build_surface(tmp_path), tmp_path), tmp_path)
    wrapper = (tmp_path / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    assert concierge.definition_ref in wrapper
    assert f"{layout.KIT_DIR}/{layout.AGENTS_SUBDIR}/concierge.md" not in wrapper


def test_evidence_skill_appears_only_once_enrolled(project: Path) -> None:
    assert "grimoire-evidence" not in {s.slug for s in build_surface(project).skills}
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    assert "grimoire-evidence" in {s.slug for s in build_surface(project).skills}


def test_blocking_gate_hook_requires_enrolment(project: Path) -> None:
    assert not [h for h in build_surface(project).hooks if h.event is HookEvent.STOP]
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    stop = [h for h in build_surface(project).hooks if h.event is HookEvent.STOP]
    assert stop and stop[0].enforcement is Enforcement.BLOCKING


# ── Emission ─────────────────────────────────────────────────────────────────


def test_claude_surface_is_native_everywhere(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert (governed / ".claude/agents/concierge.md").is_file()
    assert (governed / ".claude/skills/grimoire-evidence/SKILL.md").is_file()
    assert (governed / ".claude/commands/grimoire-gate.md").is_file()
    settings = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]
    assert settings["permissions"]["deny"]


def test_a_hook_declared_on_subagent_start_is_wired_by_host_sync(governed: Path) -> None:
    """A3/B8 — l'événement existe désormais dans le vocabulaire host-neutre :
    un hook qui le cible atterrit dans ``.claude/settings.json`` sous
    ``SubagentStart``, au lieu de disparaître en silence (c'était le bug :
    `HookEvent` et l'émetteur ne connaissaient pas cet événement, donc rien ne
    pouvait le câbler, quoi que déclare un registre côté hôte)."""
    surface = build_surface(governed)
    extra = HookSpec(
        event=HookEvent.SUBAGENT_START,
        decision="grimoire.subagent-context",
        enforcement=Enforcement.ADVISORY,
        rationale="test",
    )
    surface = replace(surface, hooks=(*surface.hooks, extra))
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(surface, governed), governed)
    settings = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "SubagentStart" in settings["hooks"]
    # Événement sans appel d'outil : pas de matcher à porter.
    assert "matcher" not in settings["hooks"]["SubagentStart"][0]


def test_a_hook_declared_on_post_tool_use_failure_is_wired_by_host_sync(governed: Path) -> None:
    surface = build_surface(governed)
    extra = HookSpec(
        event=HookEvent.POST_TOOL_USE_FAILURE,
        decision="grimoire.tool-failure",
        enforcement=Enforcement.ADVISORY,
        matcher=("execute",),
        rationale="test",
    )
    surface = replace(surface, hooks=(*surface.hooks, extra))
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(surface, governed), governed)
    settings = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "PostToolUseFailure" in settings["hooks"]
    assert settings["hooks"]["PostToolUseFailure"][0]["matcher"] == "Bash"


def test_agent_tool_boundary_reaches_the_host_file(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    scribe = (governed / ".claude/agents/scribe.md").read_text(encoding="utf-8")
    assert "tools: 'Read, Glob, Grep, Edit, Write'" in scribe
    assert "model: 'haiku'" in scribe  # low reasoning demand


def test_a_declared_context_replaces_the_default_load_not_adds_to_it(governed: Path) -> None:
    """#379 — un agent qui déclare ``context:`` ne charge plus par défaut le
    contexte partagé (``_grimoire/_memory/shared-context.md``) : il charge le
    sien, et lui seul. Un agent qui ne déclare rien (``scribe``) reçoit le
    texte d'avant #379 à l'identique — la déclaration rétrécit, elle n'amute
    jamais par défaut."""
    (governed / "_grimoire/_memory").mkdir(parents=True, exist_ok=True)
    (governed / "_grimoire/_memory/notes-securite.md").write_text("Notes.", encoding="utf-8")
    _write_agent(
        governed,
        "sentinelle",
        "Tu surveilles la sécurité du dépôt.",
        context=("_grimoire/_memory/notes-securite.md",),
    )
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)

    sentinelle = (governed / ".claude/agents/sentinelle.md").read_text(encoding="utf-8")
    assert "_grimoire/_memory/notes-securite.md" in sentinelle
    assert "shared-context.md" not in sentinelle

    scribe = (governed / ".claude/agents/scribe.md").read_text(encoding="utf-8")
    assert "Lis `_grimoire/_memory/shared-context.md` s'il existe" in scribe


_SYNTHETIC_SHARED_CONTEXT = (
    "# Contexte partagé du projet\n\n"
    + "\n".join(
        f"- Décision {i} : une ligne de contexte partagé que tout agent charge par défaut, "
        "qu'il en ait besoin ou non, et qui pèse à chaque activation."
        for i in range(1, 25)
    )
    + "\n"
)


def _synthetic_agent(slug: str, role: str) -> str:
    """Un agent de test à la taille d'un agent réel, sans dépendre des fichiers
    livrés par le kit — ceux-ci changent de forme dans d'autres lots (#375), et
    un test qui les lisait depuis ``origin/main`` n'avait pas cette référence
    dans la CI. Seule la nature du rôle change entre les trois cas."""
    body = "\n".join(
        f"{i}. {role} — étape de raisonnement numéro {i}, décrite avec le niveau de détail "
        "d'une persona livrée, pour que la mesure porte sur une taille réaliste."
        for i in range(1, 21)
    )
    return (
        "---\n"
        f'name: "{slug}"\n'
        f'description: "{slug} — {role}"\n'
        'tools: "read, search"\n'
        f'use_when: "Quand la tâche relève de : {role}."\n'
        'dont_use_when: "Quand une lecture directe suffit."\n'
        f'tool_boundary: "Lecture transverse — {role}."\n'
        "---\n"
        f"# {slug}\n\n{body}\n"
    )


def _inject_context_declaration(raw: str, context_path: str) -> str:
    """Insère ``context: [...]`` dans le frontmatter de *raw*, juste avant le
    ``---`` de fermeture — sans toucher au reste du fichier source."""
    lines = raw.splitlines()
    dashes = [i for i, line in enumerate(lines) if line.strip() == "---"]
    closing = dashes[1]  # premier `---` = ouverture, second = fermeture
    lines.insert(closing, f"context: ['{context_path}']")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "slug,role",
    [
        ("nav-agent", "navigation dans le projet"),
        ("memoire-agent", "qualité de la mémoire"),
        ("securite-agent", "audit de sécurité"),
    ],
)
def test_declaring_context_measurably_shrinks_what_activation_loads(
    tmp_path: Path, slug: str, role: str
) -> None:
    """#379 — mesure avant/après sur trois agents synthétiques de nature
    différente, à la taille d'un agent réel, sans dépendre des fichiers livrés.

    « Avant » = ce que cet agent charge sans déclaration : le fichier d'agent
    émis, plus le contexte partagé du projet (``_grimoire/_memory/shared-
    context.md``, taille réelle prise sur le gabarit du kit) que l'instruction
    par défaut lui fait lire, existe-t-il ou non le concerne — c'est aussi ce
    qu'il chargeait avant #379, le témoin. « Après » = le même agent, augmenté
    d'une déclaration ``context:`` minimale, plus le seul fichier qu'elle
    nomme : le contexte partagé disparaît de ce qu'il charge, remplacé par ce
    qu'il a réellement demandé.
    """
    from grimoire.tools._common import estimate_tokens

    raw = _synthetic_agent(slug, role)
    shared_context_body = _SYNTHETIC_SHARED_CONTEXT

    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None

    before_root = tmp_path / "avant"
    (before_root / "_grimoire/_memory").mkdir(parents=True, exist_ok=True)
    (before_root / "_grimoire/_memory/shared-context.md").write_text(shared_context_body, encoding="utf-8")
    (before_root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (before_root / AGENT_DIR / f"{slug}.md").write_text(raw, encoding="utf-8")
    apply_plan(emitter.plan(build_surface(before_root), before_root), before_root)
    before_text = (before_root / ".claude/agents" / f"{slug}.md").read_text(encoding="utf-8")
    tokens_before = estimate_tokens(before_text) + estimate_tokens(shared_context_body)

    after_root = tmp_path / "apres"
    (after_root / "_grimoire/_memory").mkdir(parents=True, exist_ok=True)
    context_rel = f"_grimoire/_memory/{slug}-context.md"
    own_context_body = f"Contexte propre à {slug} : ce qu'il déclare, rien de plus."
    (after_root / context_rel).write_text(own_context_body, encoding="utf-8")
    (after_root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (after_root / AGENT_DIR / f"{slug}.md").write_text(_inject_context_declaration(raw, context_rel), encoding="utf-8")
    apply_plan(emitter.plan(build_surface(after_root), after_root), after_root)
    after_text = (after_root / ".claude/agents" / f"{slug}.md").read_text(encoding="utf-8")
    tokens_after = estimate_tokens(after_text) + estimate_tokens(own_context_body)

    print(f"\n[{slug}] activation : {tokens_before} tokens avant -> {tokens_after} tokens après (#379)")

    assert "shared-context.md" in before_text, "le témoin sans déclaration garde le contexte partagé par défaut"
    assert "shared-context.md" not in after_text, "le contexte déclaré remplace le partagé, il ne s'y ajoute pas"
    assert context_rel in after_text
    assert tokens_after < tokens_before


def test_sub_agents_carry_effort_max_turns_and_background(governed: Path) -> None:
    """B10 — un sous-agent généré porte des bornes explicites : ``effort``,
    ``maxTurns``, et ``background`` pour tout ce qui n'est pas le point
    d'entrée. Sans elles, un sous-agent invisible tourne sans plafond."""
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    scribe = (governed / ".claude/agents/scribe.md").read_text(encoding="utf-8")
    concierge = (governed / ".claude/agents/concierge.md").read_text(encoding="utf-8")

    assert "effort: 'low'" in scribe  # reasoning: low -> effort low
    assert "maxTurns: 30" in scribe  # défaut faute d'override
    assert "background: true" in scribe  # dispatché en invisible, pas le point d'entrée

    assert "effort: 'low'" in concierge  # reasoning: medium (défaut du fixture) -> effort low
    assert "background:" not in concierge, "le point d'entrée tourne en avant-plan, attendu par l'humain"


def test_high_reasoning_gets_high_effort(project: Path) -> None:
    _write_agent(project, "gros-cerveau", "Tu raisonnes beaucoup.", reasoning="high")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    gros_cerveau = (project / ".claude/agents/gros-cerveau.md").read_text(encoding="utf-8")
    assert "effort: 'high'" in gros_cerveau


def test_max_turns_override_from_the_agent_file_wins(project: Path) -> None:
    _write_agent(project, "verbeux", "Tu écris beaucoup, il te faut plus de tours.", max_turns=80)
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    verbeux = (project / ".claude/agents/verbeux.md").read_text(encoding="utf-8")
    assert "maxTurns: 80" in verbeux


def test_claude_model_affinity_crosses_reasoning_and_cost(project: Path) -> None:
    """`reasoning` et `cost` se croisent : le premier signal fort gagne.

    - `cost: low` + `reasoning: medium` doit dégrader vers haiku : un agent
      qu'on veut bon marché ne doit pas hériter du modèle de session par
      défaut.
    - `reasoning: high` gagne toujours, même avec `cost: low` (cas réel du
      concierge : gros raisonnement de triage, invoqué à chaque tour) — le
      raisonnement fort ne doit jamais être rétrogradé pour l'économie.
    - Sans signal fort dans un sens ou l'autre (medium/medium), `inherit`
      reste le défaut honnête.
    """
    # `tools` diffère explicitement entre les deux : sans ça, les deux fiches
    # inférent la même frontière (read, search) et deviennent indiscernables
    # au sens de la garde de distinction (#372) — un faux positif ici, pas
    # une vraie régression, mais la garde n'a aucun moyen de le savoir.
    _write_agent(project, "petit-malin", "Tu triages à bas coût.", tools="'read'", reasoning="medium", cost="low")
    _write_agent(
        project,
        "gros-cerveau",
        "Tu raisonnes beaucoup, pour pas cher.",
        tools="'read', 'edit'",
        reasoning="high",
        cost="low",
    )
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    petit_malin = (project / ".claude/agents/petit-malin.md").read_text(encoding="utf-8")
    gros_cerveau = (project / ".claude/agents/gros-cerveau.md").read_text(encoding="utf-8")
    concierge = (project / ".claude/agents/concierge.md").read_text(encoding="utf-8")
    assert "model: 'haiku'" in petit_malin  # cost low prime sur reasoning medium
    assert "model: 'opus'" in gros_cerveau  # reasoning high prime sur cost low
    assert "model: 'inherit'" in concierge  # medium/medium : pas de signal fort


# ── Politique de dispatch (#329) ─────────────────────────────────────────────


def test_claude_entry_agent_carries_the_dispatch_policy(project: Path) -> None:
    """Le plan d'émission Claude Code porte la politique V0/V1/V2 + incertitudes.

    Seule la persona d'entrée route vers d'autres personas ; c'est donc son
    fichier — pas celui de chaque sous-agent — qui doit dire quel modèle
    correspond à quelle classe de vérifiabilité.
    """
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    entry = (project / ".claude/agents/concierge.md").read_text(encoding="utf-8")
    sub = (project / ".claude/agents/scribe.md").read_text(encoding="utf-8")

    assert "Politique de dispatch" in entry
    assert "V0" in entry and "haiku" in entry
    assert "V1" in entry and "sonnet" in entry
    assert "V2" in entry and "le modèle de la session" in entry
    assert "grimoire-uncertainties" in entry
    # Seule la persona d'entrée dispatche ; les autres n'en ont pas besoin.
    assert "Politique de dispatch" not in sub


def test_copilot_readme_documents_the_policy_without_inventing_a_model(governed: Path) -> None:
    """Le README Copilot porte la même règle V0/V1/V2, jamais un nom de modèle.

    Cet hôte n'offre aucune sélection automatique équivalente à `inherit`
    (dégradation « model affinity » déjà déclarée) ; documenter la politique
    sans y coller `haiku`/`sonnet`/`opus` est la même discipline que
    l'émetteur applique déjà aux fichiers d'agent.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    readme = (governed / ".github/hooks/README.md").read_text(encoding="utf-8")

    assert "Politique de dispatch" in readme
    assert "V0" in readme and "V1" in readme and "V2" in readme
    assert "grimoire-uncertainties" in readme
    for invented_model in ("haiku", "sonnet", "opus", "gpt-", "gemini"):
        assert invented_model not in readme.lower()


def _write_providers_registry(root: Path, *, project: str = "demo") -> None:
    registry = f"""\
$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "{project}"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat", "code"]
    default_models: ["claude-sonnet-4.6"]
    currency: "quota"
    invocation: "claude -p {{prompt}} --model {{model}}"
    models:
      - id: "claude-haiku-4.5"
        tier: "cheap"
      - id: "claude-sonnet-4.6"
        tier: "mid"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "anthropic"
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
"""
    registry_path = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(registry, encoding="utf-8")


def test_session_start_reports_providers_when_the_registry_exists(project: Path) -> None:
    _write_providers_registry(project)
    context = _session_start(project)
    assert "Fournisseurs : 1 disponibles, 0 refroidis, prochain cheap=anthropic" in context


def test_session_start_says_nothing_about_providers_without_a_registry(project: Path) -> None:
    context = _session_start(project)
    assert "Fournisseurs :" not in context


def test_copilot_surface_declares_its_permission_gap(governed: Path) -> None:
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    apply_plan(plan, governed)
    assert (governed / ".github/agents/concierge.agent.md").is_file()
    assert (governed / ".github/prompts/grimoire-gate.prompt.md").is_file()
    assert (governed / ".github/hooks/grimoire-stop.json").is_file()
    assert "permissions" in {d.surface for d in plan.degradations}


def test_copilot_agent_files_carry_the_wrapper_contract(governed: Path) -> None:
    """Contracts inherited from the scaffolder's wrappers, now owned here.

    `.github/agents/` had two writers: the scaffolder emitted a coarse wrapper
    and this emitter replaced it with one carrying the resolved tool boundary
    and the real definition path. The scaffolder no longer writes them, so the
    guarantees its tests pinned are pinned here instead.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)

    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    sub = (governed / ".github/agents/scribe.agent.md").read_text(encoding="utf-8")

    assert entry.startswith("---\n") and "description:" in entry
    # The entry point stays user-invocable; every other persona is routed.
    assert "user-invocable: false" not in entry
    assert "user-invocable: false" in sub
    # The wrapper must point at the file that actually holds the persona.
    assert "_grimoire/_config/custom/agents/concierge.md" in entry
    # ...and carry the boundary the surface resolved, not a fixed guess.
    assert "tools: ['read', 'search', 'edit']" in sub


def test_copilot_declares_the_model_affinity_gap_instead_of_guessing(governed: Path) -> None:
    """Copilot ne documente aucune valeur `model` de sélection automatique.

    Contrairement à Claude Code (`inherit`), le contrat VS Code pour
    `.github/agents/*.agent.md` n'offre qu'un nom de modèle explicite ou une
    liste de repli — rien qui corresponde à l'affinité `reasoning`/`cost` du
    projet. Inventer un nom de modèle serait une hallucination silencieuse ;
    la bonne réponse est de ne rien émettre, quelle que soit l'affinité de
    l'agent, et de le dire dans une dégradation explicite du plan.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    apply_plan(plan, governed)
    assert "model affinity" in {d.surface for d in plan.degradations}
    concierge = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    assert "model:" not in concierge


def test_a_hand_written_copilot_wrapper_is_preserved(governed: Path) -> None:
    target = governed / ".github/agents/concierge.agent.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Mon agent à moi\n", encoding="utf-8")
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".github/agents/concierge.agent.md" in result.skipped
    assert target.read_text(encoding="utf-8") == "# Mon agent à moi\n"


def test_prose_only_host_states_that_governance_is_not_enforced(governed: Path) -> None:
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    apply_plan(plan, governed, force=True)
    catalog = (governed / "AGENTS.md").read_text(encoding="utf-8")
    assert "concierge" in catalog
    assert "ne sont pas opposables" in catalog
    assert {"subagents", "skills", "commands", "hooks"} <= {d.surface for d in plan.degradations}


@pytest.mark.parametrize("host_id", list(supported_hosts()))
def test_emission_is_idempotent(governed: Path, host_id: HostId) -> None:
    emitter = emitter_for(host_id)
    assert emitter is not None
    surface = build_surface(governed)
    apply_plan(emitter.plan(surface, governed), governed, force=True)
    again = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert not again.written, f"{host_id.value} réécrit : {again.written}"


def test_a_hand_written_file_is_never_silently_replaced(governed: Path) -> None:
    target = governed / ".claude/agents/concierge.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("écrit à la main\n", encoding="utf-8")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    result = apply_plan(emitter.plan(build_surface(governed), governed), governed)
    assert ".claude/agents/concierge.md" in result.skipped
    assert target.read_text(encoding="utf-8") == "écrit à la main\n"
    forced = apply_plan(emitter.plan(build_surface(governed), governed), governed, force=True)
    assert ".claude/agents/concierge.md" in forced.written


def test_settings_merge_preserves_foreign_configuration(governed: Path) -> None:
    settings = governed / ".claude/settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "grimoire standard activation-context"}]},
                        {"hooks": [{"type": "command", "command": "echo maison"}]},
                    ]
                },
                "permissions": {"deny": ["Read(./privé)"]},
            }
        ),
        encoding="utf-8",
    )
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    data = json.loads(settings.read_text(encoding="utf-8"))

    assert data["model"] == "opus"
    commands = [h["command"] for entry in data["hooks"]["SessionStart"] for h in entry["hooks"]]
    assert "echo maison" in commands
    # The legacy activation hook is superseded, not stacked on top of.
    assert "grimoire standard activation-context" not in commands
    assert commands.count("grimoire-hook --host claude --event SessionStart") == 1
    # The superseded invocation is migrated, not stacked beside the new one.
    assert not [c for c in commands if c.startswith("grimoire host hook")]
    assert "Read(./privé)" in data["permissions"]["deny"]


def test_repeated_sync_never_stacks_hook_entries(governed: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    for _ in range(3):
        apply_plan(emitter.plan(build_surface(governed), governed), governed)
    data = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["Stop"]) == 1


# ── Decisions ────────────────────────────────────────────────────────────────


def test_tool_classification_reads_both_host_vocabularies() -> None:
    assert classify_tool("Bash", {"command": "rm -rf build"}).destructive_reason
    assert classify_tool("run_in_terminal", {"command": "rm -rf build"}).destructive_reason
    assert classify_tool("replace_string_in_file", {"filePath": "a.py"}).family == "write"
    assert classify_tool("Read", {"file_path": "app/.env"}).secret_target


def test_a_host_with_a_permission_table_does_not_pay_a_process_per_read() -> None:
    """`Read` in the matcher means a hook process on every file read (~307 ms).

    Credential paths are already refused by the declarative deny rules on a host
    that has them, so intercepting reads there buys nothing and taxes every
    session. Shell access to the same files stays covered: `Bash` is in the
    matcher for the execute family.
    """
    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.tool-policy",
        matcher=("execute", "write", "secret"),
    )
    trimmed = _matcher(hook, covered=frozenset({"secret"}))
    assert "Read" not in trimmed
    assert "Bash" in trimmed and "Edit" in trimmed


def test_a_host_without_a_permission_table_keeps_intercepting_reads() -> None:
    hook = HookSpec(
        event=HookEvent.PRE_TOOL_USE,
        decision="grimoire.tool-policy",
        matcher=("execute", "write", "secret"),
    )
    assert "Read" in _matcher(hook)


def test_every_credential_family_is_declared_in_both_forms() -> None:
    """The regex and the deny glob drifted apart once; this is what caught it.

    Three families (`.npmrc`, `credentials.*`, `service-account*.json`) had a
    detection pattern and no declarative counterpart, so they were unprotected
    on the side that costs nothing.
    """
    for rule in SECRET_RULES:
        assert rule.pattern, f"{rule.name} sans motif de détection"
        assert rule.globs, f"{rule.name} sans glob déclaratif"

    deny = default_permissions("governed").deny
    for glob in secret_read_globs():
        assert f"read:{glob}" in deny, f"{glob} absent des règles deny"


@pytest.mark.parametrize(
    "probe",
    [
        ".env",
        "app/.env.local",
        "app/.npmrc",
        "x/.pypirc",
        "~/.ssh/id_ecdsa",
        "secrets/token",
        "certs/a.p12",
        "k/credentials.json",
        "sa/service-account-prod.json",
    ],
)
def test_each_credential_family_is_actually_detected(probe: str) -> None:
    assert classify_tool("Read", {"file_path": probe}).secret_target, probe


def test_secret_detection_survives_a_windows_separator() -> None:
    """A Windows host hands over `app\\.env`; a `/`-only anchor lets it through."""
    assert classify_tool("Read", {"file_path": r"app\.env"}).secret_target
    assert classify_tool("Read", {"file_path": r"C:\proj\secrets\token.txt"}).secret_target
    assert not classify_tool("Read", {"file_path": r"docs\environment.md"}).secret_target


def test_destructive_and_secret_actions_are_refused(governed: Path) -> None:
    deny = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Bash", tool_input={"command": "rm -rf src"}
        )
    )
    assert deny.outcome is Outcome.DENY
    secret = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Read", tool_input={"file_path": ".env"}
        )
    )
    assert secret.outcome is Outcome.DENY


def test_read_only_calls_are_not_slowed_down(governed: Path) -> None:
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE, project_root=governed, tool_name="Read", tool_input={"file_path": "README.md"}
        )
    )
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail == {}


def _set_task_in_progress(root: Path) -> None:
    board = root / "_grimoire/standard/task-board.yaml"
    board.write_text(
        board.read_text(encoding="utf-8").replace('status: "proposed"', 'status: "in_progress"'), encoding="utf-8"
    )


def test_red_gates_block_a_governed_closure(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.BLOCK
    assert "non terminée" in decision.reason


def test_a_block_never_repeats_itself(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, stop_active=True))
    assert decision.outcome is Outcome.ALLOW


def test_an_unenrolled_project_is_never_blocked(project: Path) -> None:
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=project))
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["skipped"] == "project_not_enrolled"


def test_a_green_gate_on_a_proposed_task_says_it_protects_nothing(governed: Path) -> None:
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.ALLOW
    assert "ne protège rien" in decision.context


def test_a_broken_governed_project_blocks_once_and_says_why(governed: Path) -> None:
    """Un gate qu'on ne sait pas évaluer n'est pas un gate vert.

    Avant : un task-board illisible rendait ALLOW avec un contexte que l'hôte
    n'affiche pas sur Stop — la clôture passait, en silence, précisément dans
    le profil qui promet de la refuser.
    """
    (governed / "_grimoire/standard/task-board.yaml").write_text("[oups", encoding="utf-8")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.BLOCK
    assert "non évaluables" in decision.reason
    assert "task-board.yaml" in decision.reason
    # ...et ne rend jamais la session inquittable : le second Stop passe.
    again = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, stop_active=True))
    assert again.outcome is Outcome.ALLOW


def test_a_broken_unguarded_project_is_told_not_blocked(project: Path) -> None:
    setup_standard_profile(project, profile_id="orchestrated", task_id="bootstrap")
    (project / "_grimoire/standard/task-board.yaml").write_text("[oups", encoding="utf-8")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=project))
    assert decision.outcome is Outcome.ALLOW
    assert "non évaluables" in decision.context


def test_a_subagent_gate_that_cannot_be_evaluated_says_so(governed: Path) -> None:
    (governed / "_grimoire/standard/task-board.yaml").write_text("[oups", encoding="utf-8")
    decision = decide_subagent_gate(HookInput(event=HookEvent.SUBAGENT_STOP, project_root=governed))
    assert decision.outcome is Outcome.ALLOW
    assert "non évaluables" in decision.context


def test_a_capsule_survives_gates_that_cannot_be_evaluated(governed: Path) -> None:
    """La compaction est le moment où l'on perd tout ; la capsule dit ce qu'elle sait."""
    (governed / "_grimoire/standard/task-board.yaml").write_text("[oups", encoding="utf-8")
    decision = decide_context_capsule(HookInput(event=HookEvent.PRE_COMPACT, project_root=governed))
    assert "non évaluables" in decision.context
    assert "governed" in decision.context


# ── Wire format ──────────────────────────────────────────────────────────────


def test_event_names_are_accepted_in_every_spelling() -> None:
    assert parse_event("PreToolUse") is HookEvent.PRE_TOOL_USE
    assert parse_event("pre_tool_use") is HookEvent.PRE_TOOL_USE
    assert parse_event("pre-tool-use") is HookEvent.PRE_TOOL_USE
    assert parse_event("inconnu") is None


def test_payload_keys_are_read_in_both_casings(tmp_path: Path) -> None:
    snake = normalize_input({"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(tmp_path)})
    camel = normalize_input({"hookEventName": "PreToolUse", "toolName": "Bash", "cwd": str(tmp_path)})
    assert snake.tool_name == camel.tool_name == "Bash"
    assert snake.event is camel.event is HookEvent.PRE_TOOL_USE


def test_the_same_refusal_reaches_both_blocking_hosts(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    payload = {"hook_event_name": "Stop", "cwd": str(governed)}
    claude, claude_decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    copilot, copilot_decision, _ = run_hook(payload, host_id=HostId.GITHUB_COPILOT)
    assert claude["decision"] == copilot["decision"] == "block"
    assert claude["reason"] == copilot["reason"]
    assert claude_decision.reason == copilot_decision.reason


def test_a_host_without_blocking_hooks_gets_context_not_a_refusal(governed: Path) -> None:
    _set_task_in_progress(governed)
    (governed / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    rendered, decision, hook = run_hook({"hook_event_name": "Stop", "cwd": str(governed)}, host_id=HostId.CODEX)
    assert "decision" not in rendered
    assert decision.outcome is Outcome.BLOCK  # the rule is the same...
    assert render(decision, hook, HostId.CODEX) == rendered  # ...only the wiring differs


def test_pre_tool_use_renders_a_permission_decision(governed: Path) -> None:
    rendered, _, _ = run_hook(
        {
            "hook_event_name": "PreToolUse",
            "cwd": str(governed),
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        },
        host_id=HostId.CLAUDE_CODE_CLI,
    )
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_compaction_capsule_outlives_the_context(governed: Path) -> None:
    rendered, _, _ = run_hook({"hook_event_name": "PreCompact", "cwd": str(governed)}, host_id=HostId.CLAUDE_CODE_CLI)
    capsule = governed / "_grimoire-output/context/bootstrap/compaction-capsule.md"
    assert capsule.is_file()
    assert "bootstrap" in capsule.read_text(encoding="utf-8")
    assert "systemMessage" in rendered


def test_a_capsule_that_could_not_be_written_is_not_claimed_written(governed: Path) -> None:
    """Le message système disait « capsule écrite » même quand le disque avait refusé."""
    dest = governed / "_grimoire-output/context/bootstrap/compaction-capsule.md"
    dest.mkdir(parents=True)  # un dossier à la place du fichier : l'écriture échoue
    rendered, decision, _ = run_hook(
        {"hook_event_name": "PreCompact", "cwd": str(governed)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    assert "capsule" not in decision.detail
    assert decision.detail["capsule_error"]
    assert "non écrite" in rendered["systemMessage"]
    assert not rendered["systemMessage"].startswith("[Grimoire] capsule de gouvernance écrite")


def _crash(_hook: HookInput) -> Decision:
    raise RuntimeError("moteur de politique cassé")


def test_a_crashing_policy_asks_instead_of_allowing(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une garde qui plante n'est pas une garde qui autorise.

    Avant : l'exception devenait ``permissionDecision: allow`` — l'hôte
    n'affiche pas ``additionalContext`` sur PreToolUse, donc l'auto-approbation
    ne laissait aucune trace visible.
    """
    monkeypatch.setitem(DECISIONS, "grimoire.tool-policy", _crash)
    payload = {
        "hook_event_name": "PreToolUse",
        "cwd": str(governed),
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf build"},
    }
    claude, decision, _ = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert decision.outcome is Outcome.ASK
    assert claude["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "moteur de politique cassé" in claude["hookSpecificOutput"]["permissionDecisionReason"]
    copilot, _, _ = run_hook(payload, host_id=HostId.CODEX)
    assert "moteur de politique cassé" in copilot["hookSpecificOutput"]["additionalContext"]


def test_a_crashing_non_tool_decision_keeps_the_session_and_says_so(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(DECISIONS, "grimoire.task-context", _crash)
    rendered, decision, _ = run_hook(
        {"hook_event_name": "UserPromptSubmit", "cwd": str(governed)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    assert decision.outcome is Outcome.ALLOW
    assert "moteur de politique cassé" in rendered["hookSpecificOutput"]["additionalContext"]


def test_a_non_blocking_stop_verdict_is_still_visible(project: Path) -> None:
    """Sur Stop, l'hôte ne lit pas ``additionalContext`` : un verdict non
    bloquant qui n'y vivait qu'en contexte était un verdict que personne ne
    voyait."""
    setup_standard_profile(project, profile_id="orchestrated", task_id="bootstrap")
    _set_task_in_progress(project)
    (project / "_grimoire-output/context/bootstrap/context-bundle.yaml").unlink(missing_ok=True)
    rendered, decision, _ = run_hook({"hook_event_name": "Stop", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI)
    assert decision.outcome is Outcome.ALLOW
    assert "rouges" in rendered["systemMessage"]


# ── Capabilities ─────────────────────────────────────────────────────────────


def test_host_aliases_resolve() -> None:
    assert resolve_host("claude") is HostId.CLAUDE_CODE_CLI
    assert resolve_host("host-github-copilot") is HostId.GITHUB_COPILOT
    assert resolve_host("nawak") is None


def test_every_gap_names_its_fallback() -> None:
    for host_id in supported_hosts():
        for gap in gaps_for(profile_for(host_id)):
            assert gap.fallback, f"{host_id.value}/{gap.surface} dégrade sans repli déclaré"


def _copilot_hook_plan(project: Path) -> tuple[object, object]:
    """Le plan Copilot et son premier fichier de hook."""
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    plan = emitter.plan(build_surface(project), project)
    hooks = [f for f in plan.files if f.relpath.as_posix().startswith(".github/hooks/grimoire-")]
    assert hooks, "le plan Copilot doit émettre au moins un fichier de hook"
    return plan, hooks[0]


_HAND_WRITTEN_HOOK = (
    '{"hooks": {"SessionStart": [{"type": "command", "command": "MAISON-NE-PAS-ECRASER.sh", "timeout": 10}]}}\n'
)


def test_copilot_sync_preserves_a_hand_written_hook(project: Path) -> None:
    """Un hook écrit à la main survit au sync, comme un agent écrit à la main.

    L'émetteur Copilot posait ``managed=False`` sur ses fichiers de hook, ce
    qui désactivait entièrement le contrôle de préservation : le drapeau
    confondait « ne peut pas porter de marqueur de gestion » — un JSON n'a pas
    de commentaires — avec « peut être écrasé sans prévenir ». Un projet ayant
    sa propre chaîne de gouvernance la perdait au premier sync, sans message
    et sans sauvegarde.
    """
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HAND_WRITTEN_HOOK, encoding="utf-8")

    result = apply_plan(plan, project)

    assert target.read_text(encoding="utf-8") == _HAND_WRITTEN_HOOK
    assert hook.relpath.as_posix() in result.skipped


def test_copilot_sync_still_updates_its_own_hook(project: Path) -> None:
    """Garde-fou : le kit doit continuer à mettre à jour ce qu'il a écrit."""
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    stale = (
        json.dumps(
            {"hooks": {"SessionStart": [{"type": "command", "command": "grimoire-hook --host copilot", "timeout": 5}]}},
            indent=2,
        )
        + "\n"
    )
    target.write_text(stale, encoding="utf-8")

    result = apply_plan(plan, project)

    assert target.read_text(encoding="utf-8") == hook.content
    assert hook.relpath.as_posix() in result.written


def test_copilot_sync_force_overwrites_a_hand_written_hook(project: Path) -> None:
    """``--force`` reste la porte de sortie, comme pour les agents."""
    plan, hook = _copilot_hook_plan(project)
    target = project / hook.relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_HAND_WRITTEN_HOOK, encoding="utf-8")

    apply_plan(plan, project, force=True)

    assert target.read_text(encoding="utf-8") == hook.content


# ── Persona d'entrée ─────────────────────────────────────────────────────────


def _session_start(root: Path) -> str:
    return decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=root)).context


def test_the_entry_persona_reaches_the_session_start_context(project: Path) -> None:
    """Le contrat entier : la désignation `entry_point` sort du hook."""
    context = _session_start(project)
    assert "concierge" in context, "la persona d'entrée n'atteint pas la session"
    assert "_grimoire/_config/custom/agents/concierge.md" in context, "sans chemin, rien à lire"
    assert "scribe" not in context, "seule la persona d'entrée est injectée"


def test_the_validated_directive_survives_the_persona(project: Path) -> None:
    """La persona s'ajoute au standard, elle ne le remplace pas.

    Le mécanisme d'activation a été mesuré 40/40 contre 0/40. L'écraser pour
    faire de la place à une persona échangerait un effet prouvé contre un
    effet supposé.
    """
    context = _session_start(project)
    assert "[Grimoire Standard — activation]" in context
    assert context.index("[Grimoire — persona d'entrée]") < context.index("[Grimoire Standard — activation]")


def test_a_project_without_an_entry_persona_keeps_the_bare_directive(tmp_path: Path) -> None:
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    assert entry_persona_context(tmp_path) == ("", "")
    assert _session_start(tmp_path) == activation_context_text(tmp_path, task_id="bootstrap")


def test_the_hook_names_the_persona_it_injected(project: Path) -> None:
    """Sans trace dans `detail`, une injection muette est indistinguable d'une absence."""
    rendered, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    assert decision.detail["entry_agent"] == "concierge"
    assert "concierge" in rendered["hookSpecificOutput"]["additionalContext"]


# ── Rappel de tâche au claim (#141) ─────────────────────────────────────────


def test_le_rappel_de_tache_n_est_jamais_injecte_sans_claim(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contrôle négatif : une tâche active mais jamais réclamée ne rappelle rien.

    ``GRIMOIRE_TASK_ID`` force la tâche active sans passer par un claim — c'est
    exactement le cas que la boucle ne doit pas déclencher.
    """
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, "Tâche jamais réclamée", acceptance=("x",))
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    monkeypatch.setenv("GRIMOIRE_TASK_ID", task.id)

    context = _session_start(project)

    assert "rappel de tâche" not in context.lower()
    assert decide_activation(
        HookInput(event=HookEvent.SESSION_START, project_root=project)
    ).detail["recall_injected"] is False


def test_le_rappel_de_tache_arrive_au_claim_entre_la_persona_et_la_directive(project: Path) -> None:
    """Le critère de l'issue #141, vu depuis le hook : une jumelle qui a échoué
    remonte au claim, dans l'ordre prescrit — persona, rappel, directive."""
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(project)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    passee = service.ledger.create_task(mission.id, "Configurer le webhook amont", acceptance=("x",))
    service.ledger.transition_task(passee.id, TaskState.READY, actor_id="a")
    service.ledger.transition_task(passee.id, TaskState.CLAIMED, actor_id="a")
    service.ledger.transition_task(passee.id, TaskState.RUNNING, actor_id="a")
    service.ledger.transition_task(
        passee.id, TaskState.BLOCKED, actor_id="a", reason="webhook amont : certificat expiré"
    )
    service.ledger.transition_task(passee.id, TaskState.READY, actor_id="a")

    jumelle = service.ledger.create_task(
        mission.id, "Configurer le webhook amont (reprise)", acceptance=("x",)
    )
    service.ledger.transition_task(jumelle.id, TaskState.READY, actor_id="b")
    service.ledger.claim_task(jumelle.id, "b", "host-b")

    context = _session_start(project)

    assert "certificat expiré" in context
    assert (
        context.index("[Grimoire — persona d'entrée]")
        < context.index("[Grimoire — rappel de tâche]")
        < context.index("[Grimoire Standard — activation]")
    )


def _configure_entry(root: Path, entry: str | None) -> None:
    """Écrit un project-context.yaml minimal ; ``None`` omet la clé."""
    lines = ['project:', '  name: "hosts-test"', '  type: "library"', 'agents:', '  archetype: "minimal"']
    if entry is not None:
        lines.append(f'  entry: "{entry}"')
    (root / "project-context.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_entry_persona_defaults_to_concierge_without_a_config(project: Path) -> None:
    surface = build_surface(project)
    assert surface.entry_agent() is not None and surface.entry_agent().name == "concierge"


def test_the_project_designates_its_entry_persona(project: Path) -> None:
    """Un projet qui a son propre point d'entrée ne doit pas en recevoir un second."""
    _configure_entry(project, "scribe")
    names = {a.name: a.entry_point for a in collect_agents(project)}
    assert names == {"concierge": False, "scribe": True}
    context = _session_start(project)
    assert "**scribe**" in context and "**concierge**" not in context


def test_an_empty_entry_means_no_entry_persona(project: Path) -> None:
    """Vide n'est pas absent : c'est la déclaration « je porte déjà mon point d'entrée »."""
    _configure_entry(project, "")
    assert build_surface(project).entry_agent() is None
    assert entry_persona_context(project) == ("", "")
    assert _session_start(project) == activation_context_text(project, task_id="bootstrap")


def test_an_entry_that_names_no_agent_is_reported_not_invented(project: Path) -> None:
    _configure_entry(project, "fantome")
    assert build_surface(project).entry_agent() is None
    assert entry_persona_context(project) == ("", "")


def test_choosing_an_entry_persona_is_recorded_in_the_trace_ledger(project: Path) -> None:
    """Le fait que #365 demande : quel agent, quand — écrit là où le choix se fait."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger

    _session_start(project)
    counts = TraceLedger(project / TRACES_DIR).agent_dispatch_counts()
    assert set(counts) == {"concierge"}
    assert counts["concierge"]["count"] == 1
    assert counts["concierge"]["last_seen"]


def test_no_entry_persona_writes_nothing_to_the_trace_ledger(tmp_path: Path) -> None:
    """Symétrique du fait précédent : rien à observer, rien d'écrit."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger

    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    assert entry_persona_context(tmp_path) == ("", "")
    _session_start(tmp_path)
    assert TraceLedger(tmp_path / TRACES_DIR).agent_dispatch_counts() == {}


def test_an_unwritable_trace_ledger_never_breaks_session_start(project: Path) -> None:
    """Best-effort : un journal illisible n'est pas au prix de l'activation elle-même."""
    from grimoire.core.standard_generation import TRACES_DIR

    traces_dir = project / TRACES_DIR
    traces_dir.parent.mkdir(parents=True, exist_ok=True)
    # Un fichier régulier à l'emplacement du dossier attendu fait échouer le
    # `mkdir` du TraceLedger — exactement le type de panne qu'un journal
    # absent ou illisible peut produire en pratique.
    traces_dir.write_text("pas un dossier", encoding="utf-8")
    context = _session_start(project)
    assert "**concierge**" in context


def test_no_host_can_open_a_session_inside_an_agent(project: Path) -> None:
    """Le manque est déclaré, pas commenté — et chaque hôte nomme son substitut."""
    del project
    for host_id in supported_hosts():
        profile = profile_for(host_id)
        assert not profile.agent_autostart, f"{host_id.value} prétend démarrer dans un agent"
        gap = next((g for g in gaps_for(profile) if g.surface == "agent_autostart"), None)
        assert gap is not None, f"{host_id.value} tait le manque au lieu de le dégrader"
        if profile.supports_event(HookEvent.SESSION_START):
            assert "session_start" in gap.fallback
        else:
            assert profile.instructions_entrypoint in gap.fallback


def _write_agent_in(root: Path, tier_dir: str, name: str) -> None:
    """Un agent au faisceau volontairement banal, dans la couche demandée."""
    d = root / tier_dir
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                f'description: "{name} — rôle de test"',
                'tools: "read, edit"',
                "---",
                "Tu lis et tu édites.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_deux_agents_du_kit_au_meme_faisceau_donnent_une_note_pas_une_erreur(tmp_path: Path) -> None:
    """Dette du kit (Grimoire-kit#375) : visible, jamais bloquante pour le projet."""
    from grimoire.hosts.collect import build_surface

    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-a")
    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-b")

    surface = build_surface(tmp_path)

    assert surface.notes, "la collision doit rester visible"
    assert "kit-a == kit-b" in surface.notes[0]
    assert "#375" in surface.notes[0]


def test_un_agent_override_au_meme_faisceau_qu_un_autre_est_refuse(tmp_path: Path) -> None:
    """Un agent créé dans le projet doit se distinguer : c'est le socle du système émergent."""
    from grimoire.core.exceptions import GrimoireAgentError
    from grimoire.hosts.collect import build_surface

    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-a")
    _write_agent_in(tmp_path, "_grimoire/overrides/agents", "mon-agent")

    with pytest.raises(GrimoireAgentError, match="faisceau identique"):
        build_surface(tmp_path)


def test_deux_agents_distincts_ne_laissent_aucune_note(tmp_path: Path) -> None:
    from grimoire.hosts.collect import build_surface

    _write_agent_in(tmp_path, "_grimoire/kit/agents", "kit-a")
    d = tmp_path / "_grimoire/kit/agents"
    (d / "kit-c.md").write_text(
        '---\nname: "kit-c"\ndescription: "autre"\ntools: "read"\n---\nTu lis seulement.\n',
        encoding="utf-8",
    )

    assert build_surface(tmp_path).notes == ()
