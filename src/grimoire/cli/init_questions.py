"""Le wizard en trois questions (issue #888) : pile, qui travaille, domaine.

Décision #630 (2026-10-23) : le wizard ne demande plus un archétype — c'est un
vocabulaire interne du kit, pas celui d'un nouvel utilisateur. Il pose trois
questions, pré-remplies par la détection (:class:`StackScanner`,
:class:`ArchetypeResolver`, marqueurs du registre d'expertises), et déduit :

- l'archétype (ou les archétypes) à installer ;
- le niveau d'accompagnement (``skill_level`` de ``project-context.yaml``) ;
- les besoins du standard agentique et le profil qu'ils impliquent
  (``grimoire up --needs``) ;
- les skills de pile attachés à Atlas (``stack-*``) et les expertises du
  catalogue à recommander — recommandées seulement : la doctrine des
  artefacts (issue #616) veut qu'une expertise ne s'attache jamais d'office,
  et un projet Python nu reçoit ``stack`` + ``stack-python`` seuls.

Tout ici est pur : aucun terminal, aucun disque, aucune écriture. La CLI
(:mod:`grimoire.cli.cmd_init`) et l'assistant du cockpit
(:mod:`grimoire.tools.project_setup`) appellent les mêmes fonctions, donc une
même réponse donne le même archétype des deux côtés. Module séparé de
``cmd_init.py`` parce que celui-ci est au plafond du cliquet de taille (R2).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from grimoire.core.archetype_resolver import ArchetypeResolver, ResolvedArchetype
from grimoire.core.scanner import ScanResult, StackDetection
from grimoire.core.stack_skills import STACK_SKILL_BY_DETECTION

__all__ = [
    "QUESTIONS",
    "Answers",
    "Deduction",
    "Option",
    "Question",
    "answers_from_dict",
    "answers_to_scan",
    "apply_adjustment",
    "deduce",
    "parse_choice",
    "prefill",
    "question",
    "questions_payload",
]


# ── Modèle ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Option:
    """Une réponse possible à une question."""

    id: str
    label: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class Question:
    """Une des trois questions ; ``multiple`` autorise plusieurs réponses."""

    id: str
    prompt: str
    options: tuple[Option, ...]
    multiple: bool = False

    def option_ids(self) -> tuple[str, ...]:
        return tuple(o.id for o in self.options)

    def index_of(self, option_id: str) -> int:
        """Numéro affiché (1-based) d'une option."""
        return self.option_ids().index(option_id) + 1


STACK_QUESTION = Question(
    id="stack",
    prompt="Which technical stack does this project use?",
    multiple=True,
    options=(
        Option("python", "Python"),
        Option("typescript", "JavaScript / TypeScript", "Node, React, Vue included"),
        Option("go", "Go"),
        Option("rust", "Rust"),
        Option("java", "Java / Kotlin"),
        Option("csharp", "C#"),
        Option("ruby", "Ruby"),
        Option("docker", "Docker"),
        Option("terraform", "Terraform"),
        Option("kubernetes", "Kubernetes"),
        Option("ansible", "Ansible"),
    ),
)

WHO_QUESTION = Question(
    id="who",
    prompt="Who works on this project?",
    options=(
        Option("solo", "Just me, I know my stack"),
        Option("learning", "Just me, I am learning"),
        Option("team", "A small team of developers"),
        Option("team-ops", "A team with ops or platform people"),
        Option("agents", "Mostly AI agents that I supervise"),
    ),
)

DOMAIN_QUESTION = Question(
    id="domain",
    prompt="What is the project about?",
    options=(
        Option("none", "A library, a CLI, a service", "no asserted domain"),
        Option("web", "A web application", "frontend and/or API"),
        Option("infra", "Infrastructure, deployment, homelab"),
        Option("platform", "An internal platform", "contracts between teams"),
        Option("creative", "Content, design, brand"),
        Option("fixloop", "Maintenance with a certified fix loop", "TDD proofs, incidents"),
        Option("standard", "The agentic standard itself", "traceability, evidence gates"),
    ),
)

QUESTIONS: tuple[Question, Question, Question] = (STACK_QUESTION, WHO_QUESTION, DOMAIN_QUESTION)


def question(question_id: str) -> Question:
    for q in QUESTIONS:
        if q.id == question_id:
            return q
    msg = f"unknown question: {question_id!r}"
    raise KeyError(msg)


@dataclass(frozen=True, slots=True)
class Answers:
    """Les trois réponses ; ``stacks`` suit l'ordre de :data:`STACK_QUESTION`."""

    stacks: tuple[str, ...] = ()
    who: str = "solo"
    domain: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {"stack": list(self.stacks), "who": self.who, "domain": self.domain}


@dataclass(frozen=True, slots=True)
class Deduction:
    """Ce que les réponses impliquent — affiché dans le résumé, appliqué au scaffold."""

    archetypes: tuple[str, ...]
    skill_level: str
    needs: tuple[str, ...]
    standard_profile: str
    stack_skills: tuple[str, ...]
    expertises: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def archetype(self) -> str:
        return self.archetypes[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "archetype": self.archetype,
            "archetypes": list(self.archetypes),
            "skill_level": self.skill_level,
            "needs": list(self.needs),
            "standard_profile": self.standard_profile,
            "stack_skills": list(self.stack_skills),
            "expertises": list(self.expertises),
            "reasons": list(self.reasons),
        }


# ── Tables de déduction ───────────────────────────────────────────────────────

#: Nom de détection du scanner → réponse à la question « pile ». Les
#: frameworks (react, vue, django, fastapi) confirment un langage, le domaine
#: qu'ils impliquent passe par :class:`ArchetypeResolver` (question 3).
_SCANNER_TO_STACK: dict[str, str] = {
    "python": "python",
    "django": "python",
    "fastapi": "python",
    "javascript": "typescript",
    "typescript": "typescript",
    "react": "typescript",
    "vue": "typescript",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
    "ruby": "ruby",
    "docker": "docker",
    "terraform": "terraform",
    "kubernetes": "kubernetes",
    "ansible": "ansible",
}

#: Réponse « pile » → nom que le scanner (et donc le scaffold) connaît.
_STACK_TO_SCANNER: dict[str, str] = {
    "python": "python",
    "typescript": "typescript",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
    "ruby": "ruby",
    "docker": "docker",
    "terraform": "terraform",
    "kubernetes": "kubernetes",
    "ansible": "ansible",
}

_INFRA_STACKS = frozenset({"terraform", "kubernetes", "ansible"})

_DOMAIN_TO_ARCHETYPE: dict[str, str] = {
    "none": "stack",
    "web": "web-app",
    "infra": "infra-ops",
    "platform": "platform-engineering",
    "creative": "creative-studio",
    "fixloop": "fix-loop",
    "standard": "agentic-standard",
}
_ARCHETYPE_TO_DOMAIN: dict[str, str] = {v: k for k, v in _DOMAIN_TO_ARCHETYPE.items()}

_WHO_TO_SKILL_LEVEL: dict[str, str] = {
    "solo": "expert",
    "learning": "beginner",
    "team": "intermediate",
    "team-ops": "intermediate",
    "agents": "intermediate",
}

#: Réponse « qui travaille » → besoins du standard (``needs-catalog.yaml``).
#: Le profil découle des ``recommended_profile`` de ces besoins (plancher le
#: plus haut), par :func:`grimoire.core.agentic_standard.resolve_install_plan`.
_WHO_TO_NEEDS: dict[str, tuple[str, ...]] = {
    "solo": ("solo-prototyping",),
    "learning": ("solo-prototyping",),
    "team": (),
    "team-ops": ("hooks-skills-governance",),
    "agents": ("multi-agent-orchestration",),
}

_DEFAULT_STANDARD_PROFILE = "starter"


# ── Pré-remplissage depuis la détection ──────────────────────────────────────


def prefill(scan: ScanResult | None, resolved: ResolvedArchetype | None = None) -> Answers:
    """Réponses par défaut déduites de la détection seule — ce que ``-y`` applique.

    La pile vient des détections du scanner ; le domaine, de l'archétype que
    :class:`ArchetypeResolver` a résolu — sauf si c'est une supposition
    (``is_best_guess``, décision 2026-09-18) : une supposition n'est jamais
    pré-remplie comme une détection, le domaine reste « aucun ». « Qui
    travaille » n'a pas de signal sur disque : « moi seul ».
    """
    detected = [d.name for d in scan.stacks] if scan and scan.stacks else []
    stacks = tuple(
        option.id
        for option in STACK_QUESTION.options
        if any(_SCANNER_TO_STACK.get(name) == option.id for name in detected)
    )
    if resolved is None and scan is not None:
        resolved = ArchetypeResolver().resolve(scan)
    domain = "none"
    if resolved is not None and not resolved.is_best_guess:
        candidates = resolved.archetypes or (resolved.archetype,)
        for archetype in candidates:
            if archetype in _ARCHETYPE_TO_DOMAIN and archetype != "stack":
                domain = _ARCHETYPE_TO_DOMAIN[archetype]
                break
    return Answers(stacks=stacks, who="solo", domain=domain)


# ── Lecture d'une réponse ─────────────────────────────────────────────────────


def parse_choice(q: Question, raw: str, default: Answers | None = None) -> tuple[str, ...]:
    """Lit une saisie (« 1,3 », « python go », « 2 », « none », « ») en identifiants.

    Vide → la valeur par défaut (ou rien). Une saisie sans aucun identifiant
    reconnu lève :class:`ValueError` avec la liste des choix valides, pour que
    l'appelant redemande plutôt que d'installer autre chose que ce qui a été
    tapé.
    """
    text = raw.strip().lower()
    if not text:
        if default is None:
            return ()
        if q.id == "stack":
            return default.stacks
        return (default.who,) if q.id == "who" else (default.domain,)
    if q.multiple and text in {"0", "none", "aucun", "aucune", "-"}:
        return ()
    ids = q.option_ids()
    chosen: list[str] = []
    unknown: list[str] = []
    for part in text.replace(",", " ").replace(";", " ").split():
        if part.isdigit() and 1 <= int(part) <= len(ids):
            value = ids[int(part) - 1]
        elif part in ids:
            value = part
        else:
            unknown.append(part)
            continue
        if value not in chosen:
            chosen.append(value)
    if unknown or not chosen:
        valid = ", ".join(f"{i}={oid}" for i, oid in enumerate(ids, 1))
        msg = f"unknown answer for « {q.id} »: {', '.join(unknown) or raw!r} — valid: {valid}"
        raise ValueError(msg)
    if not q.multiple and len(chosen) > 1:
        msg = f"« {q.id} » takes one answer, got {', '.join(chosen)}"
        raise ValueError(msg)
    # Ordre stable (celui de la question), pas celui de la frappe.
    return tuple(oid for oid in ids if oid in chosen)


def answers_from_dict(raw: dict[str, Any], default: Answers | None = None) -> Answers:
    """Construit des :class:`Answers` depuis une charge utile (cockpit, tests).

    Une clé absente ou vide garde la valeur de ``default`` ; ``stack`` accepte
    une liste ou une chaîne « a,b ». Une valeur inconnue lève
    :class:`ValueError` (même garde que :func:`parse_choice`).
    """
    base = default or Answers()
    stack_raw = raw.get("stack")
    if isinstance(stack_raw, (list, tuple)):
        stack_raw = ",".join(str(s) for s in stack_raw)
    stacks = parse_choice(STACK_QUESTION, str(stack_raw or ""), base) if stack_raw is not None else base.stacks
    who = parse_choice(WHO_QUESTION, str(raw.get("who") or ""), base)[0]
    domain = parse_choice(DOMAIN_QUESTION, str(raw.get("domain") or ""), base)[0]
    return Answers(stacks=stacks, who=who, domain=domain)


def apply_adjustment(answers: Answers, line: str) -> Answers:
    """L'ajustement en une ligne du résumé : ``domain=web who=team stack=python,go``.

    Chaque paire remplace la réponse nommée ; une clé inconnue ou une valeur
    invalide lève :class:`ValueError`. Ligne vide → réponses inchangées.
    """
    text = line.strip()
    if not text:
        return answers
    updated = answers
    for token in text.replace(";", " ").split():
        if "=" not in token:
            msg = f"expected key=value, got {token!r} (keys: stack, who, domain)"
            raise ValueError(msg)
        key, value = token.split("=", 1)
        key = key.strip().lower()
        if key in {"stack", "stacks", "pile"}:
            updated = replace(updated, stacks=parse_choice(STACK_QUESTION, value, None))
        elif key in {"who", "qui"}:
            updated = replace(updated, who=parse_choice(WHO_QUESTION, value, None)[0])
        elif key in {"domain", "domaine"}:
            updated = replace(updated, domain=parse_choice(DOMAIN_QUESTION, value, None)[0])
        else:
            msg = f"unknown key {key!r} (keys: stack, who, domain)"
            raise ValueError(msg)
    return updated


# ── Déduction ─────────────────────────────────────────────────────────────────


def _standard_profile_for(needs: tuple[str, ...]) -> str:
    if not needs:
        return _DEFAULT_STANDARD_PROFILE
    try:
        from grimoire.core.agentic_standard import resolve_install_plan

        return str(resolve_install_plan(needs=needs).profile or _DEFAULT_STANDARD_PROFILE)
    except (OSError, ValueError, KeyError):  # catalogue absent ou illisible : jamais fatal ici
        return _DEFAULT_STANDARD_PROFILE


def _recommended_expertises(stacks: tuple[str, ...], stack_skills: tuple[str, ...]) -> tuple[str, ...]:
    """Entrées du registre dont le marqueur ``stack_scanner`` est une pile déclarée,
    hors celles déjà couvertes par un skill ``stack-*`` attaché (sinon Python nu
    recevrait « python » en plus de ``stack-python``)."""
    try:
        from grimoire.core.expertises import load_catalog

        catalog = load_catalog()
    except Exception:
        return ()
    attached = set(stack_skills)
    out: list[str] = []
    for entry in catalog:
        if not entry.detect_stack_scanner:
            continue
        if _SCANNER_TO_STACK.get(entry.detect_stack_scanner) not in stacks:
            continue
        if entry.slug in attached:
            continue
        out.append(entry.id)
    return tuple(out)


def deduce(answers: Answers, *, extra_expertises: tuple[str, ...] = ()) -> Deduction:
    """Archétype, niveau, besoins, profil, skills et expertises impliqués par les réponses.

    ``extra_expertises`` : identifiants déjà recommandés par la détection sur
    disque (famille cloud, :func:`grimoire.core.expertises.detect_expertises`)
    que la fonction pure ne peut pas voir ; ajoutés tels quels, dédupliqués.
    """
    reasons: list[str] = []
    primary = _DOMAIN_TO_ARCHETYPE.get(answers.domain, "stack")
    archetypes = [primary]
    domain_label = next((o.label for o in DOMAIN_QUESTION.options if o.id == answers.domain), answers.domain)
    reasons.append(f"domain « {domain_label} » → {primary}")

    if answers.who == "team-ops" and primary != "infra-ops" and _INFRA_STACKS & set(answers.stacks):
        archetypes.append("infra-ops")
        reasons.append("ops people + infra stack declared → infra-ops added")

    skill_level = _WHO_TO_SKILL_LEVEL.get(answers.who, "intermediate")
    needs = _WHO_TO_NEEDS.get(answers.who, ())
    profile = _standard_profile_for(needs)
    who_label = next((o.label for o in WHO_QUESTION.options if o.id == answers.who), answers.who)
    needs_text = ", ".join(needs) if needs else "no need"
    reasons.append(f"« {who_label} » → {skill_level}, standard profile {profile} ({needs_text})")

    stack_skills = tuple(
        STACK_SKILL_BY_DETECTION[_STACK_TO_SCANNER[s]]
        for s in answers.stacks
        if _STACK_TO_SCANNER.get(s) in STACK_SKILL_BY_DETECTION
    )
    expertises = list(_recommended_expertises(answers.stacks, stack_skills))
    for extra in extra_expertises:
        if extra not in expertises:
            expertises.append(extra)
    if stack_skills:
        reasons.append("stack skills attached to Atlas: " + ", ".join(stack_skills))
    if expertises:
        reasons.append("expertises to attach on request: " + ", ".join(expertises))

    return Deduction(
        archetypes=tuple(archetypes),
        skill_level=skill_level,
        needs=needs,
        standard_profile=profile,
        stack_skills=stack_skills,
        expertises=tuple(expertises),
        reasons=tuple(reasons),
    )


def answers_to_scan(answers: Answers, base: ScanResult | None, root: Any) -> ScanResult:
    """Le :class:`ScanResult` que le scaffold doit croire : les piles que
    l'utilisateur a confirmées, avec l'évidence du scanner quand elle existe.

    Le scaffold et le résolveur lisent la détection, pas les réponses : plutôt
    que de leur ajouter un paramètre chacun, la réponse à la question « pile »
    devient la détection. Une pile détectée mais décochée disparaît (le
    scaffold n'attache pas son skill) ; une pile cochée sans marqueur sur
    disque est ajoutée avec l'évidence « declared in wizard ».
    """
    kept: list[StackDetection] = []
    present: set[str] = set()
    if base is not None:
        for det in base.stacks:
            if _SCANNER_TO_STACK.get(det.name) in answers.stacks:
                kept.append(det)
                present.add(_SCANNER_TO_STACK[det.name])
    for stack in answers.stacks:
        if stack in present:
            continue
        name = _STACK_TO_SCANNER.get(stack)
        if name:
            kept.append(StackDetection(name=name, confidence=1.0, evidence=("declared in wizard",)))
    project_type = base.project_type if base is not None else "generic"
    return ScanResult(stacks=tuple(kept), project_type=project_type, root=base.root if base is not None else root)


# ── Charge utile partagée avec le cockpit ─────────────────────────────────────


def questions_payload(scan: ScanResult | None, raw_answers: dict[str, Any] | None = None) -> dict[str, Any]:
    """Questions, réponses par défaut, réponses effectives et déduction — en JSON.

    Ce que ``GET /api/setup/questions`` rend au cockpit : les mêmes fonctions
    que la CLI, pour que l'archétype déduit soit identique des deux côtés.
    """
    defaults = prefill(scan)
    answers = answers_from_dict(raw_answers or {}, defaults)
    return {
        "questions": [
            {
                "id": q.id,
                "prompt": q.prompt,
                "multiple": q.multiple,
                "options": [{"id": o.id, "label": o.label, "hint": o.hint} for o in q.options],
            }
            for q in QUESTIONS
        ],
        "defaults": defaults.to_dict(),
        "answers": answers.to_dict(),
        "deduction": deduce(answers).to_dict(),
    }
