"""La mesure exigée par l'issue #372 : deux chiffres, pas une supposition.

Le fait économique qui justifie tout le lot (#371, #372) : les skills sont
émises au niveau du projet, donc leur description est payée par la session à
*chaque tour*, qu'elles servent ou non. Un skill attaché à un agent ne doit
coûter que sur les tours où cet agent travaille. Ce test construit deux
surfaces émises pour le même projet — dix skills transversales, puis les dix
mêmes skills attachées à trois agents — et compte les tokens que chaque
scénario fait payer à la session par tour, et à un sous-agent quand il
travaille.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.hosts.collect import parse_frontmatter
from grimoire.hosts.emitters.claude_code import ClaudeCodeEmitter
from grimoire.hosts.surface import AgentSpec, ModelAffinity, ProjectSurface, SkillSpec, ToolVerb
from grimoire.tools._common import estimate_tokens

#: Dix skills, un compte de mots calé sur le chiffre mesuré dans #372
#: ("environ soixante tokens par skill").
_SKILL_COUNT = 10
_WORDS_PER_DESCRIPTION = 55


def _skill(index: int) -> SkillSpec:
    description = " ".join(f"motclé{index}-{n}" for n in range(_WORDS_PER_DESCRIPTION))
    return SkillSpec(
        slug=f"skill-{index}",
        name=f"Skill {index}",
        description=description,
        body=f"Corps détaillé de la compétence {index}.\n" * 8,
        tools=(ToolVerb.READ,),
    )


def _agent(name: str, *, skills: tuple[str, ...]) -> AgentSpec:
    return AgentSpec(
        name=name,
        description=f"Agent de test {name}",
        definition_ref=f"_grimoire/_config/custom/agents/{name}.md",
        tools=(ToolVerb.READ, ToolVerb.SEARCH),
        affinity=ModelAffinity(),
        skills=skills,
    )


def _surface(*, attach: bool) -> ProjectSurface:
    skills = tuple(_skill(i) for i in range(_SKILL_COUNT))
    if attach:
        # Dix skills réparties sur trois agents — aucune ne reste transversale.
        agents = (
            _agent("agent-a", skills=tuple(s.slug for s in skills[0:4])),
            _agent("agent-b", skills=tuple(s.slug for s in skills[4:7])),
            _agent("agent-c", skills=tuple(s.slug for s in skills[7:10])),
        )
    else:
        agents = (
            _agent("agent-a", skills=()),
            _agent("agent-b", skills=()),
            _agent("agent-c", skills=()),
        )
    return ProjectSurface(project_name="projet-de-mesure", agents=agents, skills=skills)


def _skill_description_tokens_per_turn(files: dict[Path, str]) -> int:
    """Ce qu'une session paie à *chaque tour*, transversal par construction.

    Seuls les fichiers sous ``.claude/skills/*/SKILL.md`` comptent : c'est la
    liste que l'hôte relit à chaque tour pour décider quelle skill charger.
    Un skill attaché n'y apparaît plus — il vit dans le fichier de son agent,
    lu seulement quand cet agent est dispatché (voir
    ``_attached_skill_section`` dans l'émetteur).
    """
    total = 0
    for path, content in files.items():
        if path.parts[:2] != (".claude", "skills"):
            continue
        meta, _ = parse_frontmatter(content)
        total += estimate_tokens(str(meta.get("description", "")))
    return total


def _agent_file_tokens(files: dict[Path, str], name: str) -> int:
    """Ce qu'un sous-agent paie une fois, quand il est dispatché."""
    return estimate_tokens(files[Path(".claude/agents") / f"{name}.md"])


def test_attaching_ten_skills_to_agents_removes_their_cost_from_every_turn() -> None:
    transversal = _surface(attach=False)
    attached = _surface(attach=True)

    plan_transversal = ClaudeCodeEmitter().plan(transversal, Path())
    plan_attached = ClaudeCodeEmitter().plan(attached, Path())

    files_transversal = {f.relpath: f.content for f in plan_transversal.files}
    files_attached = {f.relpath: f.content for f in plan_attached.files}

    per_turn_transversal = _skill_description_tokens_per_turn(files_transversal)
    per_turn_attached = _skill_description_tokens_per_turn(files_attached)

    # Ce que l'agent-a paie quand il travaille : quasi rien avant (pas de
    # skill à lui), le corps de ses quatre skills attachées après.
    agent_a_before = _agent_file_tokens(files_transversal, "agent-a")
    agent_a_after = _agent_file_tokens(files_attached, "agent-a")

    session_turns = 20  # une session de travail ordinaire, ni triviale ni marathon

    print(
        "\n[mesure #372] "
        f"par tour, transversal={per_turn_transversal} tokens, "
        f"attaché={per_turn_attached} tokens "
        f"({per_turn_transversal - per_turn_attached} tokens économisés par tour) ; "
        f"sur {session_turns} tours : "
        f"{per_turn_transversal * session_turns} vs {per_turn_attached * session_turns} tokens payés "
        "par la session, indépendamment du travail des agents ; "
        f"agent-a en activité : {agent_a_before} -> {agent_a_after} tokens payés une fois, "
        "seulement quand agent-a travaille."
    )

    # Le fait qui gouverne le lot : dix skills transversales pèsent sur
    # chaque tour, dix skills attachées ne pèsent plus dessus.
    assert per_turn_transversal > 0
    assert per_turn_attached == 0
    assert per_turn_transversal * session_turns > per_turn_attached * session_turns

    # Le coût ne disparaît pas : il se déplace sur le fichier du sous-agent
    # concerné, payé une fois quand il tourne — pas gratuit, mais borné.
    assert agent_a_after > agent_a_before
