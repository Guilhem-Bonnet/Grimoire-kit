from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


GUARDRAIL_POLICY_VERSION = "0.6.1"

PLANNING_ARTIFACTS_RELATIVE = "_grimoire-runtime-output/planning-artifacts"
LIVRABLE_FINAL_PREFIX = "LIVRABLE-FINAL-"
TECHNICAL_DOC_PREFIX = "DOC-TECHNIQUE-"
USAGE_GUIDE_PREFIX = "GUIDE-utilisation-"
RECENT_EDIT_PATH_LIMIT = 48

HOOK_TERMS = (
    "hook",
    "hooks",
    "copilot",
    "custom agent",
    "customization",
    "instruction",
    "skill",
    "subagent",
)
TASK_FLOW_TERMS = ("task", "tasks", "flow", "workflow", "todo", "to do", "checklist")
OBSERVABILITY_TERMS = ("debug", "trace", "log", "logs")
GITHUB_FLOW_TERMS = (
    "pull request",
    "direct push",
    "push direct",
    "codeowners",
    "merge queue",
    "conventional commit",
    "commit convention",
    "protected branch",
    "branche protegee",
)
AGENTIC_PLUGIN_TERMS = (
    "plugin agentique",
    "plugins agentiques",
    "agentic plugin",
    "agentic plugins",
)
SAFETY_TERMS = (
    "sans casser",
    "without breaking",
    "safe",
    "safety",
    "risque",
    "risk",
    "secure",
    "securise",
    "securite",
)
EXPLICIT_BRAINSTORM_TERMS = (
    "brainstorm",
    "brain storm",
    "brainstormer",
    "brainstorming",
    "explore",
    "explorer",
    "options",
    "approches",
    "alternatives",
    "idees",
    "idee",
)
AMBIGUITY_TERMS = (
    "je comprends pas",
    "je ne comprends pas",
    "i do not understand",
    "i don't understand",
    "unclear",
    "pas clair",
    "flou",
    "ambiguous",
    "ambigu",
    "clarifier",
)
AUTONOMOUS_EXECUTION_TERMS = (
    "de bout en bout",
    "bout en bout",
    "end-to-end",
    "fais tout",
    "fait tout",
    "continue",
    "continue sans t'arreter",
    "continue sans t arreter",
    "sans t'arreter",
    "sans t arreter",
    "jusqu'au bout",
    "ensuite fait",
    "do it all",
    "met le tout en place",
    "mets le tout en place",
    "mettre le tout en place",
    "met tout en place",
    "mets tout en place",
    "vas-y",
    "vas y",
)
EXECUTION_TERMS = (
    "implement",
    "implemente",
    "modifier",
    "modifie",
    "edit",
    "update",
    "met a jour",
    "fix",
    "corrige",
    "create",
    "cree",
    "add",
    "ajoute",
)
SPECIFICITY_TERMS = (
    "/",
    ".py",
    ".sh",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    "fichier",
    "file",
    "hook",
    "test",
    "workflow",
    "agent",
)
CHALLENGE_TERMS = (
    "challenge",
    "steelman",
    "critique",
    "devil's advocate",
    "avocat du diable",
    "angle mort",
    "remet en question",
    "tu es sur",
    "est ce vraiment",
)
DEBATE_TERMS = (
    "debat",
    "debate",
    "party mode",
    "party-mode",
    "contradiction",
    "plusieurs points de vue",
    "croiser les avis",
    "cross-validation",
    "cvtl",
)
DESIGN_AUTHORITY_TERMS = (
    "direction artistique",
    "style guide",
    "style-guide",
    "palette",
    "theme",
    "design system",
    "design-system",
    "sprite",
    "room kit",
    "room-kit",
    "visuel",
    "visual",
    "fx",
)
TOKEN_CONTEXT_TERMS = (
    "token",
    "tokens",
    "context optimization",
    "context budget",
    "contexte",
    "compaction",
    "compact",
    "summar",
    "distill",
    "distillat",
    "prune",
)
CHALLENGE_SKIP_TEST_TERMS = (
    "sans test",
    "sans tests",
    "pas de test",
    "pas de tests",
    "no test",
    "no tests",
    "skip tests",
    "skip test",
)
CHALLENGE_SKIP_REVIEW_TERMS = (
    "sans review",
    "pas de review",
    "skip review",
    "sans pr",
    "pas de pr",
    "sans ci",
    "pas de ci",
)
CHALLENGE_BYPASS_TERMS = (
    "bypass les hooks",
    "bypass hook",
    "contourne les hooks",
    "contourner les hooks",
    "desactive les hooks",
    "desactive le hook",
    "disable hooks",
    "disable hook",
    "ignore le linter",
    "ignore le lint",
    "disable validation",
)
CHALLENGE_DIRTY_SHORTCUT_TERMS = (
    "vite fait",
    "quick and dirty",
    "hack",
    "bourrin",
    "juste faire marcher",
    "just make it work",
)
CHALLENGE_CRITICAL_DELIVERY_TERMS = (
    "en prod direct",
    "directement en prod",
    "push direct sur main",
    "push direct sur master",
    "sans filet",
)
CHALLENGE_SECRET_TERMS = ("hardcode", "hardcoder", "en dur")
CHALLENGE_SECRET_OBJECT_TERMS = ("secret", "token", "password", "mot de passe", "api key", "cle api", "key")
VISUAL_DELIVERY_TERMS = (
    "generer",
    "produire",
    "creer",
    "build",
    "implement",
    "maquett",
    "wireframe",
    "prototype",
    "livrable",
)
REFERENCE_INTENT_TERMS = (
    "api",
    "sdk",
    "library",
    "librairie",
    "framework",
    "package",
    "dependency",
    "dependencies",
    "config",
    "configuration",
    "setup",
    "install",
    "migration",
    "version",
    "cli",
)
EXTERNAL_RESEARCH_TERMS = (
    "web",
    "internet",
    "recherche web",
    "web search",
    "search web",
    "source fiable",
    "sources fiables",
    "reliable source",
    "reliable sources",
    "documentation officielle",
    "doc officielle",
    "docs officielles",
    "official documentation",
    "latest docs",
    "a jour",
    "à jour",
)
ORCHESTRATOR_CONTROL_TERMS = (
    "orchestrateur",
    "orchestrator",
    "smart orchestrator gateway",
    "grimoire master",
    "master orchestrator",
    "point d'entree unique",
    "point d'entrée unique",
)
PROJECT_PROTECTOR_TERMS = (
    "protecteur du projet",
    "protege le projet",
    "protège le projet",
    "non viable",
    "pas viable",
    "viable pour le projet",
    "plus-value",
    "plus value",
    "angle mort",
    "angles morts",
    "me challenger",
    "challenge ma demande",
    "challenger ma demande",
    "auto-saboter",
    "autosaboter",
    "accepter tout et n'importe quoi",
    "accepter tout et nimporte quoi",
)
DISPATCH_PROMPT_TERMS = (
    "prompt engineering",
    "prompt complet",
    "prompts complets",
    "prompt de dispatch",
    "dispatch prompt",
    "forme des prompts",
    "forme du prompt",
    "template pour la forme des prompts",
)
INTERACTIVE_CLARIFICATION_TERMS = (
    "question input",
    "chat de question",
    "ouvrir un chat de question",
    "entree input",
    "entrees input",
    "entrée input",
    "entrées input",
    "input avec moi",
    "pas relance la conversation",
    "pas relancer la conversation",
    "sans relancer la conversation",
    "relancer la conversation",
)
INTERNAL_REFERENCE_INTENT_TERMS = (
    "memoire",
    "mémoire",
    "memory",
    "memories",
    "repo memory",
    "repo knowledge",
    "knowledge",
    "reference",
    "references",
    "référence",
    "références",
    "source",
    "sources",
    "contexte",
    "context",
    "scan",
    "scanner",
    "connaissance",
    "corpus",
    "recherche",
    "search",
    "cherche",
    "grounding",
    "source de verite",
    "source de vérité",
)
INTERNAL_REFERENCE_WORKSPACE_PREFIXES = (
    "docs/",
    "grimoire-kit/docs/",
    "_grimoire-runtime/_memory/",
    "_grimoire-runtime/core/",
)
INTERNAL_REFERENCE_WORKSPACE_FILES = (
    "README.md",
    "grimoire-kit/README.md",
    ".github/copilot-instructions.md",
)
CONTEXT7_LIBRARY_TERMS = (
    "vscode",
    "copilot",
    "react",
    "next.js",
    "nextjs",
    "prisma",
    "express",
    "tailwind",
    "django",
    "spring",
    "spring boot",
    "fastapi",
    "pytest",
    "ruff",
    "docker",
    "playwright",
    "mermaid",
    "typer",
    "mkdocs",
)
TEST_BREAKER_TERMS = (
    "test",
    "tests",
    "pytest",
    "coverage",
    "integration",
    "e2e",
    "unit",
)
CLARIFICATION_STOP_WORDS = frozenset(
    {
        "prioriser",
        "priorise",
        "priorite",
        "peux",
        "peut",
        "traiter",
        "abord",
        "lequel",
        "la",
        "le",
        "les",
        "des",
        "de",
        "du",
        "ou",
        "et",
        "on",
        "une",
        "un",
        "plan",
        "plane",
        "boucle",
        "control",
    }
)
LOW_SIGNAL_REPLY_TERMS = frozenset({"oui", "ok", "okay", "dac", "daccord", "d'accord", "ca marche"})
GRADE_PRIORITY = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
OBLIGATION_PRIORITY = {"critical": 3, "high": 2, "medium": 1, "low": 0}
CLOSURE_RISK_OBLIGATION_WEIGHTS = {
    "taskflow-recovery": 60,
    "conflict-resolution": 50,
    "challenge-review": 35,
    "visual-validation-gate": 30,
    "clarification-batch": 25,
    "external-reference-proof": 25,
    "internal-reference-proof": 20,
    "breaker-post-tests": 20,
    "compact-context": 10,
}
CLOSURE_RISK_LEVEL_LABELS = {
    "critical": "critique",
    "high": "high",
    "medium": "medium",
    "low": "low",
}
STANDARD_DA_FALLBACK = (
    "Lisibilite avant spectacle",
    "Palette semantique nommee",
    "Tokens stables entre UI, FX et etats",
    "Motion semantique avant decor",
)

PLAN_ONLY_PATTERN = re.compile(
    r"\b(plan uniquement|plan only|sans coder|pas de code|n['’]impl[eé]mente pas|n['’][eé]cris pas|ne modifie pas|brainstorm uniquement|audit uniquement|review uniquement)\b"
)
TASK_LINE_PATTERN = re.compile(r"^\s*(?:[-*]|\d+[.)]|\[[ xX]\])\s+(?P<task>.+\S)\s*$")
INLINE_ENUM_PATTERN = re.compile(r"(?:^|[\s;])(?:\d+[.)])\s+([^;\n]+)")
TASK_TAIL_PATTERN = re.compile(
    r"(?:ensuite\s+fai[st]|puis\s+fai[st]|todo|to do|tasks?|checklist)\s*:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

WRITE_TOOL_MARKERS = (
    "create",
    "replace",
    "edit",
    "insert",
    "rename",
    "delete",
    "move",
    "str_replace",
)
EDIT_TOOL_MARKERS = ("create", "replace", "edit", "insert", "rename", "move")
TERMINAL_TOOL_MARKERS = (
    "run_in_terminal",
    "runinterminal",
    "run_task",
    "runtask",
    "create_and_run_task",
    "createandruntask",
)
FALLBACK_WRITE_MARKERS = (
    "create_file",
    "replace_string_in_file",
    "multi_replace_string_in_file",
    "editfiles",
    "createfile",
    "editnotebook",
    "insert",
    "rename",
    "delete",
)
FALLBACK_EDIT_MARKERS = (
    "create_file",
    "replace_string_in_file",
    "multi_replace_string_in_file",
    "editfiles",
    "createfile",
    "editnotebook",
    "rename",
    "insert",
)
PROTECTED_SURFACES = (
    (".github/hooks/", "les hooks Copilot/VS Code"),
    (".github/agents/grimoire-master.agent.md", "l'agent maitre"),
    (".github/instructions/", "les instructions agentiques"),
    ("_grimoire-runtime/_config/", "la configuration runtime"),
    ("_grimoire-runtime/core/agents/", "les agents coeur runtime"),
    (".vscode/settings.json", "les reglages workspace"),
    (".vscode/tasks.json", "l'orchestration des tasks"),
)
DENY_PATTERNS = (
    (r"\bgit\s+reset\s+--hard\b", "Commande destructive `git reset --hard` interdite."),
    (r"\bgit\s+checkout\s+--\b", "Commande destructive `git checkout --` interdite."),
    (r"\bgit\s+clean\s+-[^\n]*f", "Commande destructive `git clean -f` interdite."),
    (r"\brm\s+-rf\s+/($|\s)", "Suppression de la racine interdite."),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "Fork bomb interdite."),
    (r"\bmkfs(?:\.[a-z0-9_+-]+)?\b", "Formatage de volume interdit."),
    (r"\bdd\s+if=.*\sof=/dev/", "Ecriture directe sur device bloc interdite."),
)
ALLOWED_PATH_KEYS = frozenset(
    {
        "filepath",
        "path",
        "file",
        "files",
        "target",
        "newpath",
        "oldpath",
        "new_path",
        "old_path",
    }
)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
MODULE_TEXT_KEYS = frozenset({
    "output",
    "result",
    "response",
    "summary",
    "message",
    "content",
    "text",
    "analysis",
    "finaloutput",
    "assistantresponse",
})
MODULE_TASK_KEYS = frozenset({"task", "goal", "objective", "instruction", "query", "prompt"})
PROMPT_THIN_WRAPPER_ACTION_PATTERN = re.compile(r"\b(load|follow|execute|present|wait|store)\b", re.IGNORECASE)
PROMPT_THIN_WRAPPER_REFERENCE_PATTERN = re.compile(
    r"(?:(?:\{project-root\}/)?_grimoire-runtime/|\.github/(?:agents|skills|instructions)/)",
    re.IGNORECASE,
)
PROMPT_THIN_WRAPPER_SIGNATURES = (
    "load the full agent file",
    "follow all activation instructions",
    "present the numbered menu",
    "wait for user input",
    "load and follow the workflow",
    "load and execute the task",
    "store all fields as session variables",
)


_TOOL_MODULE_CACHE: dict[str, Any] = {}


@dataclass(frozen=True, slots=True)
class PromptSignals:
    prompt_preview: str
    tags: tuple[str, ...]
    notes: tuple[str, ...]
    plan_only: bool
    safety_focus: bool
    task_items: tuple[str, ...]
    brainstorm_recommended: bool
    autonomous_execution: bool

    def to_state(self, prompt: str, timestamp: str) -> dict[str, Any]:
        return {
            "timestamp": timestamp,
            "promptPreview": self.prompt_preview,
            "promptLength": len(prompt),
            "tags": list(self.tags),
            "constraints": {
                "planOnly": self.plan_only,
                "safetyFocus": self.safety_focus,
            },
            "signals": {
                "taskListDetected": bool(self.task_items),
                "taskItems": list(self.task_items),
                "brainstormRecommended": self.brainstorm_recommended,
                "autonomousExecutionPreferred": self.autonomous_execution,
            },
            "notes": list(self.notes),
        }

    def additional_context(self, max_length: int = 900) -> str:
        return " ".join(self.notes)[:max_length]


def load_payload(raw: str) -> dict[str, Any] | None:
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def dedupe_preserve_order(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)


def compact_text(text: str, limit: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if limit > 0:
        return cleaned[:limit]
    return cleaned


def normalize_match_text(text: str) -> str:
    lowered = text.lower().replace("’", "'")
    lowered = re.sub(r"[^a-z0-9+#./ -]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def clarification_tokens(text: str) -> tuple[str, ...]:
    tokens = re.findall(r"[a-z0-9][a-z0-9+#./-]*", normalize_match_text(text))
    return tuple(
        cleaned
        for token in tokens
        for cleaned in [token.strip("./-+")]
        if len(cleaned) > 2 and cleaned not in CLARIFICATION_STOP_WORDS and not cleaned.isdigit()
    )


def select_default_clarification_option(options: list[str]) -> tuple[int, str]:
    if not options:
        return -1, ""
    return 0, str(options[0])


def match_clarification_option(prompt: str, options: list[str]) -> dict[str, Any]:
    prompt_normalized = normalize_match_text(prompt)
    if not prompt_normalized or not options:
        return {}

    index_match = re.search(r"\b([1-9])\b", prompt_normalized)
    if index_match:
        selected_index = int(index_match.group(1)) - 1
        if 0 <= selected_index < len(options):
            return {
                "selectedIndex": selected_index,
                "selectedOption": str(options[selected_index]),
                "matchType": "index",
            }

    prompt_tokens = set(clarification_tokens(prompt_normalized))
    best_match: dict[str, Any] = {}
    best_score = 0
    for index, option in enumerate(options):
        option_text = str(option)
        option_normalized = normalize_match_text(option_text)
        if option_normalized and option_normalized in prompt_normalized:
            return {
                "selectedIndex": index,
                "selectedOption": option_text,
                "matchType": "substring",
            }

        overlap = prompt_tokens & set(clarification_tokens(option_text))
        if len(overlap) > best_score:
            best_score = len(overlap)
            best_match = {
                "selectedIndex": index,
                "selectedOption": option_text,
                "matchType": "token-overlap",
            }

    return best_match if best_score > 0 else {}


def is_go_ahead_prompt(prompt: str) -> bool:
    normalized = normalize_match_text(prompt)
    if not normalized:
        return False
    return bool(re.search(r"\bgo\b", normalized)) or any(
        term in normalized
        for term in (
            "vas y",
            "vas-y",
            "continue",
            "fonce",
            "lance",
            "on y va",
            "lets go",
        )
    )


def is_low_signal_reply(prompt: str) -> bool:
    normalized = normalize_match_text(prompt)
    if not normalized:
        return True
    if normalized in LOW_SIGNAL_REPLY_TERMS:
        return True
    return len(clarification_tokens(normalized)) == 0 and len(normalized.split()) <= 3


_VAGUE_VERBS: frozenset[str] = frozenset(
    {
        "améliore", "améliorer", "fais", "faire", "aide", "aider",
        "gère", "gérer", "arrange", "arranger", "regarde", "regarder",
        "vérifie", "vérifier", "change", "changer", "modifie", "modifier",
        "travaille", "travailler", "clean", "nettoie", "nettoyer",
        "fix", "help", "do", "make", "handle", "check", "update", "improve",
    }
)

_TECHNICAL_MARKER = re.compile(
    r"`[^`]+`"               # backtick identifier
    r"|\b\w+\.\w{1,5}\b"    # file.ext or dotted.path
    r"|(?:src|tests?|\.github|docs?)/\S+"  # path fragments
    r"|\bdef \b|\bclass \b|\bimport \b"    # code keywords
    r"|[a-z][a-z0-9]*_[a-z0-9_]+"         # snake_case identifier
    r"|#\d+|\bpr\b|\badr\b"                # issue/PR references
)

_AMBIGUOUS_REF_PATTERN = re.compile(
    r"\b(?:ça|ce\s+truc|ce\s+fichier|ce\s+code|cela|celui-ci|celle-ci"
    r"|l'autre|le\s+même|this|that|the\s+thing)\b"
    r"|(?<!\w)ici(?!\w)"
    r"|(?<!\w)là(?!\w)",
    re.IGNORECASE,
)

_TRADEOFF_MARKERS: frozenset[str] = frozenset(
    {"migr", "refactor", "supprim", "remov", "delet", "perfo", "optimis", "sécuri", "secur"}
)

_CONSTRAINT_MARKERS: frozenset[str] = frozenset(
    {
        "sans casser", "sans modifier", "rétrocompat", "backward compat",
        "ne pas", "don't", "without breaking", "preserve", "keep",
        "doit rester", "must not", "must keep",
    }
)

_OUTPUT_HINT = re.compile(
    r"retourne|returns|output|résultat attendu|expected|format json"
    r"|format yaml|\.md\b|\.csv\b|\.json\b|tableau|table|liste"
)


def compute_prompt_clarity(
    prompt: str,
    session_context: dict[str, Any] | None = None,
    user_skill_level: str = "",
) -> dict[str, Any]:
    """Score prompt completeness for the Prompt Clarity Gate (PCG).

    Returns a dict with: score (0-10), level (CLEAR|BORDERLINE|VAGUE),
    gaps (list[str]), bypass_available (bool).
    """
    ctx = session_context or {}
    prompt_lower = prompt.lower().strip()
    words = prompt_lower.split()
    word_count = len(words)

    score = 10
    gaps: list[str] = []

    # --- Word count deductions ---
    if word_count <= 6:
        score -= 4
        gaps.append("prompt_too_short")
    elif word_count <= 12:
        score -= 2
    elif word_count <= 18:
        score -= 1

    # --- Vague verb without technical target in the 80 chars that follow ---
    first_verb = next((w for w in words[:5] if w in _VAGUE_VERBS), None)
    if first_verb:
        verb_pos = prompt_lower.find(first_verb)
        post_verb = prompt[verb_pos + len(first_verb) : verb_pos + len(first_verb) + 80]
        if not _TECHNICAL_MARKER.search(post_verb):
            score -= 2
            gaps.append("vague_verb")

    # --- Ambiguous pronoun reference without resolvable session context ---
    recent_files: list[str] = ctx.get("recent_files", [])
    has_ambiguous_ref = bool(_AMBIGUOUS_REF_PATTERN.search(prompt))
    if has_ambiguous_ref and not recent_files:
        score -= 2
        gaps.append("unresolved_reference")

    # --- Missing scope for code tasks ---
    is_code_task = bool(_TECHNICAL_MARKER.search(prompt)) or any(
        kw in prompt_lower
        for kw in ("module", "fonction", "function", "class", "fichier", "file",
                   "code", "script", "service", "api", "endpoint", "test", "bug", "error")
    )
    if is_code_task and not _TECHNICAL_MARKER.search(prompt):
        score -= 1
        gaps.append("scope_missing")

    # --- Missing constraint for risky operations ---
    has_tradeoff = any(marker in prompt_lower for marker in _TRADEOFF_MARKERS)
    has_constraint = any(marker in prompt_lower for marker in _CONSTRAINT_MARKERS)
    if has_tradeoff and not has_constraint:
        score -= 1
        gaps.append("no_constraint")

    # --- Bonuses ---
    if _OUTPUT_HINT.search(prompt_lower):
        score += 1
    if _TECHNICAL_MARKER.search(prompt):
        score += 1

    score = max(0, min(10, score))

    if score >= 8:
        level = "CLEAR"
    elif score >= 5:
        level = "BORDERLINE"
    else:
        level = "VAGUE"

    # Expert bypass: VAGUE → BORDERLINE (non-blocking) for expert users
    bypass_available = user_skill_level == "expert" and level == "VAGUE"
    if bypass_available:
        level = "BORDERLINE"

    return {
        "score": score,
        "level": level,
        "gaps": gaps,
        "bypassAvailable": bypass_available,
    }


def format_clarification_state_note(clarification_state: dict[str, Any]) -> str:
    status = str(clarification_state.get("status") or "")
    question = compact_text(str(clarification_state.get("question") or ""), 220)
    selected_option = compact_text(str(clarification_state.get("selectedOption") or ""), 180)
    if status == "open":
        instruction = compact_text(
            str(
                clarification_state.get("instruction")
                or "Poser exactement une question batch via vscode/askQuestions quand disponible avant toute edition ou routage final."
            ),
            220,
        )
        return f"Clarification interactive requise maintenant: {instruction} Question suggeree: {question}".strip()
    if status == "needs-relance":
        return (
            "Clarification encore insuffisante: lancer une seule relance avant routage final. "
            f"Question suggeree: {question}"
        ).strip()
    if status == "resolved":
        return f"Clarification resolue via reponse utilisateur: priorite retenue = {selected_option}.".strip()
    if status == "auto-resolved":
        return f"Clarification debloquee automatiquement: priorite retenue = {selected_option}.".strip()
    return ""


def advance_clarification_state(
    prompt: str,
    clarification: dict[str, Any],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    current_state = clarification if isinstance(clarification, dict) else {}
    earlier_state = previous_state if isinstance(previous_state, dict) else {}
    previous_status = str(earlier_state.get("status") or "")
    active_previous = previous_status in {"open", "needs-relance"}

    prompt_preview = compact_text(prompt, 220)
    prompt_lower = prompt.lower()
    base_state = earlier_state if active_previous else current_state
    question = compact_text(str(base_state.get("question") or current_state.get("question") or ""), 220)
    options = [
        str(item)
        for item in list(base_state.get("options", []) or current_state.get("options", []) or [])[:3]
        if str(item).strip()
    ]
    relance_count = int(earlier_state.get("relanceCount", 0) or 0) if active_previous else 0

    if active_previous:
        matched_option = match_clarification_option(prompt, options)
        if matched_option:
            return {
                "status": "resolved",
                "question": question,
                "options": options,
                "answer": prompt_preview,
                "selectedOption": str(matched_option.get("selectedOption") or ""),
                "selectedIndex": int(matched_option.get("selectedIndex", -1) or -1),
                "relanceCount": relance_count,
                "resolutionSource": "user-answer",
                "instruction": "Clarification fermee: ne pas reposer la question batch.",
            }

        if is_go_ahead_prompt(prompt) or relance_count >= 1:
            selected_index, selected_option = select_default_clarification_option(options)
            return {
                "status": "auto-resolved",
                "question": question,
                "options": options,
                "answer": prompt_preview,
                "selectedOption": selected_option,
                "selectedIndex": selected_index,
                "relanceCount": relance_count,
                "resolutionSource": "go-ahead" if is_go_ahead_prompt(prompt) else "fallback-default",
                "instruction": "Clarification debloquee: appliquer la priorite par defaut et poursuivre.",
            }

        if is_low_signal_reply(prompt) or not prompt_lower.strip():
            return {
                "status": "needs-relance",
                "question": question,
                "options": options,
                "answer": prompt_preview,
                "selectedOption": "",
                "selectedIndex": -1,
                "relanceCount": relance_count + 1,
                "resolutionSource": "insufficient-answer",
                "instruction": "Faire une seule relance de clarification, puis debloquer si la reponse reste insuffisante.",
            }

        return {
            "status": "needs-relance",
            "question": question,
            "options": options,
            "answer": prompt_preview,
            "selectedOption": "",
            "selectedIndex": -1,
            "relanceCount": relance_count + 1,
            "resolutionSource": "insufficient-answer",
            "instruction": "Faire une seule relance de clarification, puis debloquer si la reponse reste insuffisante.",
        }

    if current_state.get("recommended"):
        if is_go_ahead_prompt(prompt):
            selected_index, selected_option = select_default_clarification_option(options)
            return {
                "status": "auto-resolved",
                "question": question,
                "options": options,
                "answer": prompt_preview,
                "selectedOption": selected_option,
                "selectedIndex": selected_index,
                "relanceCount": 0,
                "resolutionSource": "go-ahead",
                "instruction": "Clarification debloquee: appliquer la priorite par defaut et poursuivre.",
            }

        return {
            "status": "open",
            "question": question,
            "options": options,
            "answer": "",
            "selectedOption": "",
            "selectedIndex": -1,
            "relanceCount": 0,
            "resolutionSource": "pending",
            "instruction": compact_text(
                str(current_state.get("instruction") or "Poser exactement une question batch avant toute edition ou routage final."),
                220,
            ),
        }

    if previous_status in {"resolved", "auto-resolved"}:
        return earlier_state

    return {}


def guardrail_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def tool_dir() -> Path:
    return Path(__file__).resolve().parent


def ensure_sdk_paths(project_root: Path) -> None:
    candidates = (
        project_root / "grimoire-kit" / "src",
        project_root / "src",
        guardrail_repo_root() / "grimoire-kit" / "src",
        guardrail_repo_root() / "src",
    )
    for candidate in candidates:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def load_tool_module(filename: str, cache_key: str) -> Any | None:
    cached = _TOOL_MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    module_path = tool_dir() / filename
    if not module_path.exists():
        return None

    try:
        spec = importlib.util.spec_from_file_location(f"_guardrail_{cache_key}", module_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        _TOOL_MODULE_CACHE[cache_key] = module
        spec.loader.exec_module(module)
        return module
    except Exception:
        _TOOL_MODULE_CACHE.pop(cache_key, None)
        return None


def load_guardrail_rules(project_root: Path) -> dict[str, Any]:
    module = load_tool_module("guardrail-policy-rules.py", "guardrail_policy_rules")
    loader = getattr(module, "load_guardrail_rules", None) if module is not None else None
    if not callable(loader):
        return {}

    try:
        rules = loader(project_root)
    except Exception:
        return {}
    return rules if isinstance(rules, dict) else {}


def challenge_terms(project_root: Path, key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    module = load_tool_module("guardrail-policy-rules.py", "guardrail_policy_rules")
    extractor = getattr(module, "challenge_terms", None) if module is not None else None
    if not callable(extractor):
        return fallback

    values = extractor(load_guardrail_rules(project_root), key)
    if not isinstance(values, tuple):
        return fallback
    return tuple(str(value) for value in values if str(value)) or fallback


def follow_through_task_map(project_root: Path) -> dict[str, str]:
    module = load_tool_module("guardrail-policy-rules.py", "guardrail_policy_rules")
    extractor = getattr(module, "follow_through_task_map", None) if module is not None else None
    if not callable(extractor):
        return dict(FOLLOW_THROUGH_TASK_MAP)

    mapping = extractor(load_guardrail_rules(project_root))
    if not isinstance(mapping, dict):
        return dict(FOLLOW_THROUGH_TASK_MAP)
    return {str(key): str(value) for key, value in mapping.items() if str(key) and str(value)} or dict(FOLLOW_THROUGH_TASK_MAP)


def follow_through_task_specs(project_root: Path) -> dict[str, dict[str, Any]]:
    module = load_tool_module("guardrail-policy-rules.py", "guardrail_policy_rules")
    extractor = getattr(module, "follow_through_task_specs", None) if module is not None else None
    if not callable(extractor):
        return dict(FOLLOW_THROUGH_TASK_SPECS)

    specs = extractor(load_guardrail_rules(project_root))
    if not isinstance(specs, dict):
        return dict(FOLLOW_THROUGH_TASK_SPECS)
    normalized_specs = {str(label): dict(spec) for label, spec in specs.items() if str(label) and isinstance(spec, dict)}
    return normalized_specs or dict(FOLLOW_THROUGH_TASK_SPECS)


def follow_through_module() -> Any | None:
    return load_tool_module("guardrail-policy-follow-through.py", "guardrail_policy_follow_through")


def load_core_symbol(project_root: Path, module_name: str, symbol_name: str) -> Any | None:
    ensure_sdk_paths(project_root)
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None
    return getattr(module, symbol_name, None)


def should_check_tests(agent_name: str, task: str) -> bool:
    agent_lower = agent_name.lower()
    task_lower = task.lower()
    if any(name in agent_lower for name in ("dev", "qa", "tea", "quick-flow")):
        return True
    return any(term in task_lower for term in ("test", "tests", "pytest", "coverage", "verification"))


def derive_task_type(prompt: str, suggested_agent: str, tags: tuple[str, ...]) -> str:
    prompt_lower = prompt.lower()
    tag_set = set(tags)

    if "hooks" in tag_set or any(term in prompt_lower for term in ("hook", "guardrail", "task-flow")):
        return "hooks-guardrails"
    if suggested_agent in {"qa", "tea"} or any(term in prompt_lower for term in ("test", "tests", "coverage")):
        return "testing"
    if suggested_agent == "architect" or any(term in prompt_lower for term in ("architecture", "adr", "design")):
        return "architecture-review"
    if suggested_agent in {"pm", "analyst"} or any(term in prompt_lower for term in ("prd", "roadmap", "requirements")):
        return "product-discovery"
    if suggested_agent in {"tech-writer", "ux-designer"} or any(term in prompt_lower for term in ("doc", "documentation", "markdown", "ux", "ui")):
        return "documentation"
    if suggested_agent in {"dev", "quick-flow-solo-dev"}:
        return "code-implementation"
    return "general-execution"


def should_rag_inject(prompt: str, signals: PromptSignals, suggested_agent: str) -> bool:
    prompt_lower = prompt.lower()
    reference_intent = (
        any(term in prompt_lower for term in INTERNAL_REFERENCE_INTENT_TERMS)
        or any(term in prompt_lower for term in EXTERNAL_RESEARCH_TERMS)
        or wants_orchestrator_control(prompt_lower)
        or wants_project_protector(prompt_lower)
        or wants_dispatch_prompt_contract(prompt_lower)
    )
    if len(prompt) > 180 and not reference_intent:
        return False
    if signals.task_items and not reference_intent:
        return False
    if any(term in prompt_lower for term in SPECIFICITY_TERMS) and not reference_intent:
        return False
    return reference_intent or signals.brainstorm_recommended or suggested_agent in {"analyst", "architect", "pm"}


def wants_challenge(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in CHALLENGE_TERMS)


def wants_debate(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in DEBATE_TERMS)


def wants_orchestrator_control(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in ORCHESTRATOR_CONTROL_TERMS)


def wants_project_protector(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in PROJECT_PROTECTOR_TERMS)


def wants_dispatch_prompt_contract(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in DISPATCH_PROMPT_TERMS)


def wants_interactive_clarification(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in INTERACTIVE_CLARIFICATION_TERMS)


def is_design_related(prompt_lower: str, suggested_agent: str, task_type: str) -> bool:
    if suggested_agent in {"ux-designer", "art-director"}:
        return True
    if task_type == "documentation" and any(term in prompt_lower for term in DESIGN_AUTHORITY_TERMS):
        return True
    return any(term in prompt_lower for term in DESIGN_AUTHORITY_TERMS)


def build_clarification_plan(
    prompt: str,
    prompt_lower: str,
    signals: PromptSignals,
    task_type: str,
) -> dict[str, Any]:
    question_count = prompt.count("?")
    repeated_questions = (
        len(re.findall(r"est[ -]?ce qu", prompt_lower))
        + prompt_lower.count("can we")
        + prompt_lower.count("should we")
    )
    orchestrator_design = (
        wants_orchestrator_control(prompt_lower)
        or wants_project_protector(prompt_lower)
        or wants_dispatch_prompt_contract(prompt_lower)
    )
    interactive_clarification = wants_interactive_clarification(prompt_lower)
    recommended = (
        (
            signals.brainstorm_recommended
            or question_count >= 3
            or repeated_questions >= 2
            or orchestrator_design
            or interactive_clarification
        )
        and not signals.task_items
        and not signals.plan_only
    )
    if not recommended:
        return {}

    input_fields: list[dict[str, Any]] = []
    if orchestrator_design or interactive_clarification:
        options = [
            "Prioriser l'intake protecteur (objectif, plus-value, angles morts)",
            "Prioriser la clarification interactive continue",
            "Prioriser le contrat de dispatch prompt vers les subagents",
        ]
        reason = "la demande melange posture du master, clarification continue et prompt engineering de dispatch"
        question = (
            "Je peux verrouiller d'abord l'intake protecteur, la clarification interactive, "
            "ou le contrat de dispatch prompt. Lequel veux-tu cadrer en premier ?"
        )
        input_fields = [
            {
                "id": "priority",
                "label": "Priorite",
                "type": "single-select",
                "options": options,
            },
            {
                "id": "success-signal",
                "label": "Succes attendu",
                "question": "Quel comportement observable doit etre vrai une fois le Master renforce ?",
            },
            {
                "id": "anti-goal",
                "label": "Anti-goal",
                "question": "Qu'est-ce que le Master ne doit surtout plus faire ?",
            },
        ]
    elif task_type == "hooks-guardrails":
        options = [
            "Prioriser le control plane hooks",
            "Prioriser la boucle tests/review/breaker",
            "Prioriser memoire, contexte et tokens",
        ]
        reason = "plusieurs axes d'amelioration sont melanges dans une meme demande"
        question = (
            "Je peux prioriser le control plane hooks, la boucle tests/breaker, "
            "ou memoire/tokens. Lequel veux-tu traiter d'abord ?"
        )
    elif task_type == "testing":
        options = [
            "Prioriser les tests et la couverture",
            "Prioriser le breaker post-tests",
            "Prioriser la qualite CI et les gates",
        ]
        reason = "la demande combine plusieurs couches de verification"
        question = (
            "Je peux prioriser les tests/couverture, le breaker post-tests, "
            "ou les gates CI. Lequel veux-tu traiter d'abord ?"
        )
    elif any(term in prompt_lower for term in DESIGN_AUTHORITY_TERMS):
        options = [
            "Prioriser la DA existante du projet",
            "Generer une baseline standard de DA",
            "Traiter seulement les garde-fous de coherence",
        ]
        reason = "le scope visuel melange style, gouvernance et implementation"
        question = (
            "Je peux prioriser la DA projet, une baseline standard, "
            "ou seulement les garde-fous de coherence. Lequel veux-tu traiter d'abord ?"
        )
    else:
        options = [
            "Prioriser le cadrage du besoin",
            "Prioriser l'implementation immediate",
            "Prioriser la verification et les preuves",
        ]
        reason = "la demande reste suffisamment large pour meriter une seule question batch"
        question = (
            "Je peux prioriser le cadrage, l'implementation immediate, "
            "ou la verification/preuves. Lequel veux-tu traiter d'abord ?"
        )

    return {
        "recommended": True,
        "mode": "batched-options",
        "reason": reason,
        "options": options,
        "question": question,
        "askBeforeRouting": True,
        "waitForAnswer": True,
        "maxQuestions": 1,
        "instruction": "Poser exactement une question batch via vscode/askQuestions quand l'outil est disponible; sinon en chat libre avant toute edition ou routage final.",
        "allowFreeform": True,
        "toolPreference": "vscode/askQuestions",
        "inputFields": input_fields,
    }


def detect_visual_validation_need(
    prompt_lower: str,
    signals: PromptSignals,
    task_type: str,
) -> dict[str, Any]:
    if not any(term in prompt_lower for term in DESIGN_AUTHORITY_TERMS):
        return {}

    requires_delivery = bool(signals.task_items) or signals.autonomous_execution or any(
        term in prompt_lower for term in VISUAL_DELIVERY_TERMS
    )
    if not requires_delivery:
        return {}

    focus = "visual-testing" if task_type == "testing" else "visual-delivery"
    return {
        "recommended": True,
        "required": True,
        "focus": focus,
        "acceptanceAuthority": "user + ux-designer + art-director + qa",
        "criteria": [
            "score UX explicite (0-5)",
            "score visuel explicite (0-5)",
            "score direction artistique explicite (0-5)",
            "clarte du premier ecran",
            "coherence style/palette/type/motion",
            "accessibilite (contraste, focus, reduced-motion)",
            "performance percue et animations bornees",
            "preuves visuelles traceables dans proof-pack",
        ],
        "evidence": {
            "proofPack": "proof-pack.md",
            "uxVisualDaReview": "_grimoire-runtime-output/implementation-artifacts/visual-evidence/ux-visual-da-review.md",
            "requiredSurfaces": [
                "runtime-views-report.html",
                "http://127.0.0.1:4174/",
            ],
            "screenshots": True,
        },
        "retention": {
            "path": "_grimoire-runtime-output/implementation-artifacts/visual-evidence",
            "manifest": "_grimoire-runtime-output/implementation-artifacts/visual-evidence/retention-manifest.json",
            "defaultTtlDays": 14,
            "policy": "conserver uniquement les captures liees au ticket/objectif courant",
        },
        "reason": "la demande implique une livraison visuelle qui doit etre acceptee sur criteres explicites",
    }


def visual_validation_signature(focus: str, criteria: list[str]) -> str:
    normalized_criteria = [normalize_match_text(item) for item in criteria if item.strip()]
    return make_signature("visual-validation", focus, *sorted(normalized_criteria))


def advance_visual_validation_state(
    visual_validation: dict[str, Any],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(visual_validation, dict) or not visual_validation.get("required"):
        return {}

    criteria = [compact_text(str(item), 120) for item in list(visual_validation.get("criteria", []) or []) if str(item).strip()]
    focus = compact_text(str(visual_validation.get("focus") or "visual-delivery"), 80)
    signature = visual_validation_signature(focus, criteria)
    previous = previous_state if isinstance(previous_state, dict) else {}
    same_scope = bool(previous) and str(previous.get("signature") or "") == signature
    previous_proofs = [item for item in list(previous.get("proofs", []) or []) if isinstance(item, dict)] if same_scope else []
    previous_status = str(previous.get("status") or "") if same_scope else ""
    previous_satisfied = bool(previous.get("satisfied")) if same_scope else False

    return {
        "required": True,
        "focus": focus,
        "acceptanceAuthority": compact_text(str(visual_validation.get("acceptanceAuthority") or ""), 120),
        "criteria": criteria,
        "retention": dict(visual_validation.get("retention") or {}),
        "status": previous_status if previous_satisfied and previous_status else "pending",
        "satisfied": previous_satisfied,
        "proofs": previous_proofs,
        "signature": signature,
        "lastUpdated": timestamp_now(),
    }


def detect_external_reference_need(prompt_lower: str, suggested_agent: str, task_type: str) -> dict[str, Any]:
    libraries = [term for term in CONTEXT7_LIBRARY_TERMS if term in prompt_lower]
    reference_intent = any(term in prompt_lower for term in REFERENCE_INTENT_TERMS)
    research_intent = any(term in prompt_lower for term in EXTERNAL_RESEARCH_TERMS)

    if not libraries and not reference_intent and not research_intent:
        return {}

    if not libraries and not research_intent and suggested_agent not in {"architect", "dev", "qa", "tech-writer"} and task_type not in {
        "hooks-guardrails",
        "architecture-review",
        "testing",
        "documentation",
        "code-implementation",
    }:
        return {}

    return {
        "recommended": True,
        "source": "context7-first",
        "fallback": "web",
        "libraries": list(dedupe_preserve_order(libraries[:4])),
        "reason": (
            "question de bibliotheque/API/configuration susceptible de dependre d'une doc a jour"
            if not research_intent
            else "la demande attend explicitement des sources externes plus fiables et une recherche web si necessaire"
        ),
    }


def detect_internal_reference_need(prompt_lower: str, suggested_agent: str, task_type: str) -> dict[str, Any]:
    orchestrator_design = (
        wants_orchestrator_control(prompt_lower)
        or wants_project_protector(prompt_lower)
        or wants_dispatch_prompt_contract(prompt_lower)
    )
    strong_reference_intent = any(
        term in prompt_lower
        for term in (
            "memoire",
            "mémoire",
            "memory",
            "memories",
            "repo memory",
            "repo knowledge",
            "reference",
            "references",
            "référence",
            "références",
        )
    )
    reference_intent = strong_reference_intent or orchestrator_design or any(
        term in prompt_lower for term in INTERNAL_REFERENCE_INTENT_TERMS
    )
    if not reference_intent:
        return {}

    if not strong_reference_intent and not orchestrator_design and task_type not in {
        "hooks-guardrails",
        "architecture-review",
        "documentation",
        "product-discovery",
    }:
        return {}

    if suggested_agent not in {"architect", "analyst", "pm", "tech-writer", "dev", "qa"} and task_type not in {
        "hooks-guardrails",
        "architecture-review",
        "documentation",
        "product-discovery",
        "general-execution",
    }:
        return {}

    return {
        "recommended": True,
        "surfaces": [
            "/memories/repo/",
            "docs/references/",
            "docs/governance/",
            "_grimoire-runtime/core/agents/grimoire-master.md",
            "_grimoire-runtime/_memory/shared-context.md",
        ],
        "preferredTools": [
            "memory view /memories/repo/",
            "read_file sur docs/references/ ou docs/governance/",
            "read_file sur le runtime canonique du master",
        ],
        "reason": (
            "le redesign du master doit etre ancre dans le corpus projet, la memoire repo et les invariants du runtime"
            if orchestrator_design
            else "la demande implique de fonder la reponse sur les references internes et la memoire repo plutot que sur l'intuition seule"
        ),
    }


def build_dispatch_contract(prompt_lower: str, task_type: str) -> dict[str, Any]:
    requested = (
        wants_orchestrator_control(prompt_lower)
        or wants_project_protector(prompt_lower)
        or wants_dispatch_prompt_contract(prompt_lower)
    )
    if not requested:
        return {}

    return {
        "required": True,
        "taskType": task_type,
        "reason": "les handoffs doivent devenir explicites, challengeables et prouvables avant toute delegation",
        "sections": [
            "Mission et resultat attendu",
            "Objectif utilisateur et plus-value visee",
            "Contexte projet et invariants non-negociables",
            "Contraintes, non-objectifs et surfaces interdites",
            "Risques, angles morts et points a challenger",
            "Preuves attendues, validations et condition d'arret",
            "Livrable attendu et format de retour",
            "Escalade si une hypothese critique manque",
        ],
        "qualityBar": [
            "Ne jamais dispatcher le message brut",
            "Distinguer faits prouves, hypotheses et recommandations du master",
            "Remonter toute incoherence projet avant execution",
        ],
    }


def normalize_external_reference_libraries(values: list[str]) -> list[str]:
    libraries: list[str] = []
    for value in values:
        text = compact_text(str(value or ""), 80)
        if text:
            libraries.append(text)
    return list(dedupe_preserve_order(libraries))[:4]


def external_reference_signature(libraries: list[str]) -> str:
    return make_signature("external-reference", *sorted(normalize_match_text(item) for item in libraries if item.strip()))


def normalize_internal_reference_surfaces(values: list[str]) -> list[str]:
    surfaces: list[str] = []
    for value in values:
        text = compact_text(str(value or ""), 120)
        if text:
            surfaces.append(text)
    return list(dedupe_preserve_order(surfaces))[:4]


def internal_reference_signature(surfaces: list[str]) -> str:
    return make_signature("internal-reference", *sorted(normalize_match_text(item) for item in surfaces if item.strip()))


def advance_external_reference_state(
    external_references: dict[str, Any],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(external_references, dict) or not external_references.get("recommended"):
        return {}

    libraries = normalize_external_reference_libraries(list(external_references.get("libraries", []) or []))
    signature = external_reference_signature(libraries)
    previous = previous_state if isinstance(previous_state, dict) else {}
    same_scope = bool(previous) and str(previous.get("signature") or "") == signature
    previous_proofs = [item for item in list(previous.get("proofs", []) or []) if isinstance(item, dict)] if same_scope else []
    previous_status = str(previous.get("status") or "") if same_scope else ""
    previous_satisfied = bool(previous.get("satisfied")) if same_scope else False

    state = {
        "required": True,
        "status": previous_status if previous_satisfied and previous_status else "pending",
        "satisfied": previous_satisfied,
        "preferredSource": str(external_references.get("source") or "context7-first"),
        "fallback": str(external_references.get("fallback") or "web"),
        "libraries": libraries,
        "libraryId": str(previous.get("libraryId") or "") if same_scope else "",
        "reason": compact_text(str(external_references.get("reason") or ""), 220),
        "proofs": previous_proofs,
        "signature": signature,
        "lastUpdated": timestamp_now(),
    }
    return state


def advance_internal_reference_state(
    internal_references: dict[str, Any],
    previous_state: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(internal_references, dict) or not internal_references.get("recommended"):
        return {}

    surfaces = normalize_internal_reference_surfaces(list(internal_references.get("surfaces", []) or []))
    preferred_tools = normalize_internal_reference_surfaces(list(internal_references.get("preferredTools", []) or []))
    signature = internal_reference_signature(surfaces)
    previous = previous_state if isinstance(previous_state, dict) else {}
    same_scope = bool(previous) and str(previous.get("signature") or "") == signature
    previous_proofs = [item for item in list(previous.get("proofs", []) or []) if isinstance(item, dict)] if same_scope else []
    previous_status = str(previous.get("status") or "") if same_scope else ""
    previous_satisfied = bool(previous.get("satisfied")) if same_scope else False

    state = {
        "required": True,
        "status": previous_status if previous_satisfied and previous_status else "pending",
        "satisfied": previous_satisfied,
        "surfaces": surfaces,
        "preferredTools": preferred_tools,
        "reason": compact_text(str(internal_references.get("reason") or ""), 220),
        "proofs": previous_proofs,
        "signature": signature,
        "detail": compact_text(str(previous.get("detail") or ""), 160) if same_scope else "",
        "lastUpdated": timestamp_now(),
    }
    return state


def tool_response_text(payload: dict[str, Any]) -> str:
    response = payload.get("tool_response")
    if response is None:
        response = payload.get("toolResponse")
    if response is None:
        response = payload.get("tool_output")
    if response is None:
        response = payload.get("toolOutput")

    if isinstance(response, str):
        return compact_text(response, 1200)
    if response is None:
        return ""
    return compact_text(json.dumps(response, ensure_ascii=True), 1200)


def parse_tool_response_payload(payload: dict[str, Any]) -> Any:
    response = payload.get("tool_response")
    if response is None:
        response = payload.get("toolResponse")
    if response is None:
        response = payload.get("tool_output")
    if response is None:
        response = payload.get("toolOutput")

    if isinstance(response, (dict, list)):
        return response
    if not isinstance(response, str):
        return None
    stripped = response.strip()
    if not stripped.startswith(("{", "[")):
        return None
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(stripped)
    return None


def extract_context7_library_id(tool_input: dict[str, Any], response_payload: Any, response_text: str) -> str:
    library_id = compact_text(str(tool_input.get("libraryId") or ""), 160)
    if library_id:
        return library_id

    if isinstance(response_payload, dict):
        candidate = compact_text(str(response_payload.get("libraryId") or ""), 160)
        if candidate:
            return candidate
        results = response_payload.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict):
                    continue
                candidate = compact_text(str(item.get("libraryId") or item.get("id") or ""), 160)
                if candidate.startswith("/"):
                    return candidate
    if isinstance(response_payload, list):
        for item in response_payload:
            if not isinstance(item, dict):
                continue
            candidate = compact_text(str(item.get("libraryId") or item.get("id") or ""), 160)
            if candidate.startswith("/"):
                return candidate

    match = re.search(r"/(?:[A-Za-z0-9_.-]+)/(?:[A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_.-]+)?", response_text)
    return match.group(0) if match else ""


def build_external_reference_proof(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    response_text = tool_response_text(payload)
    response_payload = parse_tool_response_payload(payload)

    if "context7" in tool_name and "resolve-library-id" in tool_name:
        library_name = compact_text(str(tool_input.get("libraryName") or ""), 80)
        library_id = extract_context7_library_id(tool_input, response_payload, response_text)
        return {
            "source": "context7",
            "stage": "resolve-library-id",
            "status": "context7-resolved",
            "satisfied": False,
            "libraryName": library_name,
            "libraryId": library_id,
            "query": compact_text(str(tool_input.get("query") or ""), 160),
            "responsePreview": response_text,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    if "context7" in tool_name and "query-docs" in tool_name:
        return {
            "source": "context7",
            "stage": "query-docs",
            "status": "context7-proved",
            "satisfied": True,
            "libraryName": compact_text(str(tool_input.get("libraryName") or ""), 80),
            "libraryId": extract_context7_library_id(tool_input, response_payload, response_text),
            "query": compact_text(str(tool_input.get("query") or ""), 160),
            "responsePreview": response_text,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    if tool_name == "fetch_webpage":
        urls = [str(item) for item in list(tool_input.get("urls", []) or []) if str(item).strip()][:3]
        return {
            "source": "web",
            "stage": "fetch-webpage",
            "status": "web-fallback",
            "satisfied": True,
            "libraryName": "",
            "libraryId": "",
            "query": compact_text(str(tool_input.get("query") or ""), 160),
            "responsePreview": response_text,
            "urls": urls,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    return {}


def is_internal_reference_workspace_path(file_path: str, project_root: Path) -> tuple[bool, str]:
    raw_path = compact_text(file_path, 400)
    if not raw_path:
        return False, ""

    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate

    try:
        relative = relpath(project_root, candidate.resolve())
    except Exception:
        relative = raw_path.replace("\\", "/")

    if relative in INTERNAL_REFERENCE_WORKSPACE_FILES:
        return True, relative
    if any(relative == prefix.rstrip("/") or relative.startswith(prefix) for prefix in INTERNAL_REFERENCE_WORKSPACE_PREFIXES):
        return True, relative
    return False, relative


def classify_memory_reference_path(path: str) -> tuple[str, str]:
    normalized = compact_text(path, 160)
    if not normalized:
        return "", ""

    if normalized == "/memories" or normalized == "/memories/":
        return "memory-scope-proved", "/memories/"
    if normalized == "/memories/repo" or normalized == "/memories/repo/":
        return "repo-memory-proved", "/memories/repo/"
    if normalized.startswith("/memories/repo/"):
        return "repo-memory-proved", normalized
    if normalized.startswith("/memories/"):
        return "memory-scope-proved", normalized
    return "", normalized


def build_internal_reference_proof(payload: dict[str, Any], project_root: Path) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    normalized_tool_name = re.sub(r"[^a-z0-9]+", "", tool_name)
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    response_text = tool_response_text(payload)

    if tool_name == "memory" or normalized_tool_name.endswith("memory"):
        command = compact_text(str(tool_input.get("command") or "").lower(), 40)
        path = compact_text(str(tool_input.get("path") or ""), 160)
        status, detail = classify_memory_reference_path(path)
        if command == "view" and status:
            return {
                "source": "repo-memory" if status == "repo-memory-proved" else "memory-scope",
                "stage": "memory-view",
                "status": status,
                "satisfied": True,
                "detail": detail,
                "query": "",
                "responsePreview": response_text,
                "timestamp": timestamp_now(),
                "tool": tool_name,
            }

    if "repoknowledgesearch" in normalized_tool_name:
        return {
            "source": "repo-knowledge",
            "stage": "repo-knowledge-search",
            "status": "repo-knowledge-proved",
            "satisfied": True,
            "detail": compact_text(str(tool_input.get("query") or "repo-knowledge-search"), 160),
            "query": compact_text(str(tool_input.get("query") or ""), 160),
            "responsePreview": response_text,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    if tool_name == "read_file":
        file_path = compact_text(str(tool_input.get("filePath") or ""), 400)
        is_reference, relative = is_internal_reference_workspace_path(file_path, project_root)
        if is_reference:
            return {
                "source": "workspace-reference",
                "stage": "read-file",
                "status": "workspace-reference-proved",
                "satisfied": True,
                "detail": relative,
                "query": "",
                "responsePreview": response_text,
                "timestamp": timestamp_now(),
                "tool": tool_name,
            }

    return {}


def build_visual_validation_proof(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    response_text = tool_response_text(payload)
    serialized_input = json.dumps(tool_input, ensure_ascii=True).lower()

    if "screenshot" in tool_name or tool_name.endswith("browser_snapshot") or tool_name == "view_image":
        return {
            "source": "visual-proof",
            "stage": "capture",
            "status": "visual-proof-captured",
            "satisfied": True,
            "detail": compact_text(tool_name, 120),
            "query": "",
            "responsePreview": response_text,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    if "proof-pack.md" in serialized_input or "visual-brief.md" in serialized_input or "assets-manifest.csv" in serialized_input:
        return {
            "source": "visual-proof",
            "stage": "artifact",
            "status": "visual-artifacts-captured",
            "satisfied": True,
            "detail": "proof-pack/visual-brief/assets-manifest",
            "query": "",
            "responsePreview": response_text,
            "timestamp": timestamp_now(),
            "tool": tool_name,
        }

    return {}


def apply_visual_validation_proof(
    visual_validation_state: dict[str, Any],
    proof: dict[str, Any],
) -> dict[str, Any]:
    if not proof:
        return visual_validation_state if isinstance(visual_validation_state, dict) else {}

    state = dict(visual_validation_state) if isinstance(visual_validation_state, dict) else {}
    proofs = [item for item in list(state.get("proofs", []) or []) if isinstance(item, dict)]
    stage = str(proof.get("stage") or "")
    detail = str(proof.get("detail") or "")
    signature = make_signature("visual-validation", stage, detail, str(proof.get("source") or ""))
    proof_entry = dict(proof)
    proof_entry["signature"] = signature

    replaced = False
    for index, existing in enumerate(proofs):
        if str(existing.get("signature") or "") == signature:
            proofs[index] = proof_entry
            replaced = True
            break
    if not replaced:
        proofs.append(proof_entry)

    state["proofs"] = proofs
    state["status"] = str(proof.get("status") or state.get("status") or "pending")
    state["satisfied"] = bool(proof.get("satisfied"))
    state["lastUpdated"] = timestamp_now()
    return state


def format_visual_validation_state_note(visual_validation_state: dict[str, Any]) -> str:
    if not isinstance(visual_validation_state, dict) or not visual_validation_state.get("required"):
        return ""

    status = str(visual_validation_state.get("status") or "")
    criteria = ", ".join(str(item) for item in list(visual_validation_state.get("criteria", []) or [])[:3])
    authority = compact_text(str(visual_validation_state.get("acceptanceAuthority") or "user"), 120)
    retention = visual_validation_state.get("retention") if isinstance(visual_validation_state.get("retention"), dict) else {}
    ttl = int(retention.get("defaultTtlDays", 14) or 14)

    if status in {"visual-proof-captured", "visual-artifacts-captured"} and bool(visual_validation_state.get("satisfied")):
        return f"Validation visuelle prouvee. Autorite d'acceptation: {authority}."

    return (
        "Gate visuel actif: ne pas cloturer sans validation sur criteres "
        f"({criteria}) et preuves traceables (captures + proof-pack). Retention captures: TTL {ttl} jours."
    )


def apply_external_reference_proof(
    external_reference_state: dict[str, Any],
    proof: dict[str, Any],
) -> dict[str, Any]:
    if not proof:
        return external_reference_state if isinstance(external_reference_state, dict) else {}

    state = dict(external_reference_state) if isinstance(external_reference_state, dict) else {}
    proofs = [item for item in list(state.get("proofs", []) or []) if isinstance(item, dict)]
    stage = str(proof.get("stage") or "")
    library_id = str(proof.get("libraryId") or "")
    query = str(proof.get("query") or "")
    proof_signature = make_signature(stage, library_id, query, str(proof.get("source") or ""))
    proof_entry = dict(proof)
    proof_entry["signature"] = proof_signature

    replaced = False
    for index, existing in enumerate(proofs):
        if str(existing.get("signature") or "") == proof_signature:
            proofs[index] = proof_entry
            replaced = True
            break
    if not replaced:
        proofs.append(proof_entry)

    if not state.get("libraries"):
        inferred = []
        library_name = compact_text(str(proof.get("libraryName") or ""), 80)
        if library_name:
            inferred.append(library_name)
        state["libraries"] = normalize_external_reference_libraries(inferred)
        state["signature"] = external_reference_signature(list(state.get("libraries", []) or []))

    state["proofs"] = proofs
    state["status"] = str(proof.get("status") or state.get("status") or "pending")
    state["satisfied"] = bool(proof.get("satisfied"))
    if library_id:
        state["libraryId"] = library_id
    state["lastUpdated"] = timestamp_now()
    return state


def apply_internal_reference_proof(
    internal_reference_state: dict[str, Any],
    proof: dict[str, Any],
) -> dict[str, Any]:
    if not proof:
        return internal_reference_state if isinstance(internal_reference_state, dict) else {}

    state = dict(internal_reference_state) if isinstance(internal_reference_state, dict) else {}
    proofs = [item for item in list(state.get("proofs", []) or []) if isinstance(item, dict)]
    stage = str(proof.get("stage") or "")
    detail = str(proof.get("detail") or "")
    query = str(proof.get("query") or "")
    proof_signature = make_signature(stage, detail, query, str(proof.get("source") or ""))
    proof_entry = dict(proof)
    proof_entry["signature"] = proof_signature

    replaced = False
    for index, existing in enumerate(proofs):
        if str(existing.get("signature") or "") == proof_signature:
            proofs[index] = proof_entry
            replaced = True
            break
    if not replaced:
        proofs.append(proof_entry)

    state["proofs"] = proofs
    state["status"] = str(proof.get("status") or state.get("status") or "pending")
    state["satisfied"] = bool(proof.get("satisfied"))
    state["detail"] = compact_text(detail, 160)
    state["lastUpdated"] = timestamp_now()
    return state


def format_external_reference_state_note(external_reference_state: dict[str, Any]) -> str:
    if not isinstance(external_reference_state, dict):
        return ""

    status = str(external_reference_state.get("status") or "")
    libraries = ", ".join(str(item) for item in list(external_reference_state.get("libraries", []) or [])[:3])
    suffix = f" ({libraries})" if libraries else ""
    library_id = compact_text(str(external_reference_state.get("libraryId") or ""), 120)

    if status == "pending":
        return f"Preuve Context7 requise{suffix}: capter une consultation documentaire avant synthese finale.".strip()
    if status == "context7-resolved":
        resolution = f" via {library_id}" if library_id else ""
        return f"Resolution Context7 capturee{suffix}{resolution}: il manque encore la preuve documentaire finale.".strip()
    if status == "context7-proved":
        resolution = f" via {library_id}" if library_id else ""
        return f"Preuve Context7 capturee{suffix}{resolution}.".strip()
    if status == "web-fallback":
        return f"Preuve web capturee{suffix} en fallback.".strip()
    return ""


def format_internal_reference_state_note(internal_reference_state: dict[str, Any]) -> str:
    if not isinstance(internal_reference_state, dict):
        return ""

    status = str(internal_reference_state.get("status") or "")
    detail = compact_text(str(internal_reference_state.get("detail") or ""), 120)
    surfaces = ", ".join(str(item) for item in list(internal_reference_state.get("surfaces", []) or [])[:3])
    suffix = f" ({surfaces})" if surfaces else ""

    if status == "pending":
        return (
            f"Consultation des references internes requise{suffix}: verifier /memories/, /memories/repo/, la connaissance repo ou les docs canoniques avant synthese finale."
        ).strip()
    if status == "repo-memory-proved":
        return f"Preuve references internes capturee via {detail or '/memories/repo/'}.".strip()
    if status == "memory-scope-proved":
        return f"Preuve references internes capturee via memoire partagee ({detail or '/memories/'}).".strip()
    if status == "repo-knowledge-proved":
        return f"Preuve references internes capturee via repo knowledge ({detail or 'query'}).".strip()
    if status == "workspace-reference-proved":
        return f"Preuve references internes capturee via {detail or 'workspace docs'}.".strip()
    return ""


def resolve_design_authority(project_root: Path, prompt_lower: str, suggested_agent: str, task_type: str) -> dict[str, Any]:
    if not is_design_related(prompt_lower, suggested_agent, task_type):
        return {}

    style_guide = project_root / "grimoire-game-assets" / "STYLE_GUIDE.md"
    readme = project_root / "grimoire-game-assets" / "README.md"
    instruction = project_root / ".github" / "instructions" / "grimoire-2d-assets.instructions.md"
    if style_guide.exists():
        sources: list[str] = []
        for candidate in (style_guide, readme, instruction):
            if candidate.exists():
                sources.append(str(candidate.relative_to(project_root)))
        return {
            "found": True,
            "scope": "assets-2d",
            "sources": sources,
            "fallback": False,
        }

    return {
        "found": False,
        "scope": "generic-visual",
        "sources": [],
        "fallback": True,
        "fallbackPrinciples": list(STANDARD_DA_FALLBACK),
    }


def project_context_root(project_root: Path) -> Path:
    candidate = project_root / "grimoire-kit"
    return candidate if candidate.exists() else project_root


def assess_token_budget(project_root: Path, prompt_lower: str = "", force: bool = False) -> dict[str, Any]:
    module = load_tool_module("token-budget.py", "token_budget")
    enforcer_cls = getattr(module, "TokenBudgetEnforcer", None) if module is not None else None
    warning_threshold = float(getattr(module, "WARNING_THRESHOLD", 0.60) or 0.60) if module is not None else 0.60
    if enforcer_cls is None:
        return {}

    try:
        status = enforcer_cls(project_context_root(project_root), agent="grimoire-master").check()
    except Exception:
        return {}

    usage_pct = float(getattr(status, "usage_pct", 0.0) or 0.0)
    if not force and usage_pct < 0.45 and not any(term in prompt_lower for term in TOKEN_CONTEXT_TERMS):
        return {}

    recommendations = [
        compact_text(str(item), 160) for item in list(getattr(status, "recommendations", []) or [])[:2]
    ]
    return {
        "level": str(getattr(status, "level", "ok") or "ok"),
        "usagePct": round(usage_pct, 4),
        "usedTokens": int(getattr(status, "used_tokens", 0) or 0),
        "windowTokens": int(getattr(status, "window_tokens", 0) or 0),
        "recommendations": recommendations,
        "enforcementRecommended": usage_pct >= warning_threshold,
    }


def format_token_budget_note(token_budget: dict[str, Any]) -> str:
    level = str(token_budget.get("level") or "ok")
    usage_pct = round(float(token_budget.get("usagePct", 0.0) or 0.0) * 100)
    recommendations = list(token_budget.get("recommendations", []) or [])
    tail = f" {recommendations[0]}" if recommendations else ""
    return f"Budget token {level} ({usage_pct}%).{tail}".strip()


def detect_proposal_challenge(prompt_lower: str) -> dict[str, Any]:
    reasons: list[str] = []
    severity = ""
    rules_project_root = guardrail_repo_root()
    skip_test_terms = challenge_terms(rules_project_root, "skipTests", CHALLENGE_SKIP_TEST_TERMS)
    skip_review_terms = challenge_terms(rules_project_root, "skipReview", CHALLENGE_SKIP_REVIEW_TERMS)
    bypass_terms = challenge_terms(rules_project_root, "bypass", CHALLENGE_BYPASS_TERMS)
    dirty_shortcut_terms = challenge_terms(rules_project_root, "dirtyShortcut", CHALLENGE_DIRTY_SHORTCUT_TERMS)
    critical_delivery_terms = challenge_terms(rules_project_root, "criticalDelivery", CHALLENGE_CRITICAL_DELIVERY_TERMS)

    if any(term in prompt_lower for term in skip_test_terms):
        reasons.append("la demande court-circuite les tests")
        severity = severity or "high"

    if any(term in prompt_lower for term in skip_review_terms):
        reasons.append("la demande court-circuite la review ou la CI")
        severity = severity or "high"

    if any(term in prompt_lower for term in bypass_terms):
        reasons.append("la demande contourne des garde-fous du systeme")
        severity = severity or "high"

    if any(term in prompt_lower for term in dirty_shortcut_terms):
        reasons.append("la demande privilegie un raccourci fragile plutot qu'une solution reversible")
        severity = severity or "medium"

    if any(term in prompt_lower for term in critical_delivery_terms):
        reasons.append("la demande pousse un changement sensible sans filet de securite")
        severity = "critical"

    if any(term in prompt_lower for term in CHALLENGE_SECRET_TERMS) and any(
        term in prompt_lower for term in CHALLENGE_SECRET_OBJECT_TERMS
    ):
        reasons.append("la demande suggere de hardcoder un secret ou un credential")
        severity = "critical"

    if not reasons:
        return {}

    if not severity:
        severity = "medium"

    summary = "; ".join(dict.fromkeys(reasons))
    return {
        "recommended": True,
        "severity": severity,
        "summary": compact_text(summary, 220),
        "instruction": "Avant execution, challenger l'idee, expliciter les risques et proposer une alternative reversible.",
        "reasons": list(dict.fromkeys(reasons))[:4],
    }


def grade_rank(value: str) -> int:
    return GRADE_PRIORITY.get(value.upper(), -1)


def detect_subagent_conflict(previous_state: dict[str, Any], current_state: dict[str, Any]) -> dict[str, Any]:
    if not previous_state:
        return {}

    previous_task = compact_text(str(previous_state.get("task") or ""), 180)
    current_task = compact_text(str(current_state.get("task") or ""), 180)
    if not previous_task or not current_task or previous_task != current_task:
        return {}

    previous_agent = str(previous_state.get("agent") or "")
    current_agent = str(current_state.get("agent") or "")
    if not previous_agent or previous_agent == current_agent:
        return {}

    previous_grade = str(previous_state.get("grade") or "")
    current_grade = str(current_state.get("grade") or "")
    previous_rank = grade_rank(previous_grade)
    current_rank = grade_rank(current_grade)
    previous_trust = previous_state.get("trust") if isinstance(previous_state.get("trust"), dict) else {}
    current_trust = current_state.get("trust") if isinstance(current_state.get("trust"), dict) else {}
    previous_trust_level = str(previous_trust.get("level") or "") if isinstance(previous_trust, dict) else ""
    current_trust_level = str(current_trust.get("level") or "") if isinstance(current_trust, dict) else ""

    contradictory = False
    if previous_rank >= 0 and current_rank >= 0 and abs(previous_rank - current_rank) >= 2:
        contradictory = True
    if {previous_trust_level, current_trust_level} == {"trusted", "untrusted"}:
        contradictory = True
    if not contradictory:
        return {}

    return {
        "previousAgent": previous_agent,
        "previousGrade": previous_grade,
        "currentAgent": current_agent,
        "currentGrade": current_grade,
        "recommendedAction": "challenge-mode" if "untrusted" in {previous_trust_level, current_trust_level} else "party-mode",
    }


def recommend_hygiene_actions(
    task_state: dict[str, Any],
    prompt_state: dict[str, Any],
    subagent_state: dict[str, Any],
) -> tuple[str, ...]:
    actions: list[str] = []
    task_name = str(task_state.get("task") or "")
    task_status = str(task_state.get("status") or "").lower()
    task_lower = task_name.lower()
    task_type = extract_task_type_from_prompt_state(prompt_state)

    if task_name and task_status and task_status not in {"success", "completed", "ok"}:
        actions.append(f"resoudre l'anomalie sur {task_name}")
        return dedupe_preserve_order(actions)

    flags = subagent_state.get("flags") if isinstance(subagent_state.get("flags"), list) else []
    if any(flag in flags for flag in ("trust-red", "quality-red", "conflict-red")):
        actions.append("Challenge Mode")

    if any(term in task_lower for term in TEST_BREAKER_TERMS) or task_type == "testing":
        actions.extend([
            "review adversariale ciblee",
            "edge-case hunt",
            "retests cibles",
            "quick-check",
            "preflight",
        ])
    elif task_type in {"code-implementation", "hooks-guardrails"} or any(
        tag in (prompt_state.get("tags") or []) for tag in ("hooks", "task-flow")
    ):
        actions.extend(["quick-check", "memory-lint", "preflight"])

    return dedupe_preserve_order(actions)


FOLLOW_THROUGH_TASK_MAP = {
    "quick-check": "grimoire: quickcheck",
    "memory-lint": "grimoire: memory-lint",
    "preflight": "grimoire: preflight",
}

FOLLOW_THROUGH_TASK_SPECS = {
    "grimoire: quickcheck": {
        "flow": "quality",
        "command": ["bash", "framework/tools/quick-check.sh"],
        "timeoutSeconds": 240,
    },
    "grimoire: memory-lint": {
        "flow": "memory",
        "command": [".venv/bin/python", "framework/tools/memory-lint.py", "--project-root", "."],
        "timeoutSeconds": 240,
    },
    "grimoire: preflight": {
        "flow": "quality",
        "command": [".venv/bin/python", "framework/tools/preflight-check.py", "--project-root", "."],
        "timeoutSeconds": 240,
    },
}


def derive_logical_next_tasks(
    prompt_state: dict[str, Any],
    task_state: dict[str, Any],
    subagent_state: dict[str, Any],
    session_state: dict[str, Any],
    closure_risk: dict[str, Any],
) -> list[dict[str, Any]]:
    task_name = compact_text(str(task_state.get("task") or ""), 120)
    task_status = str(task_state.get("status") or "").lower()
    if not task_name or task_status not in {"success", "completed", "ok"}:
        return []

    if blocking_open_obligations(list(session_state.get("openObligations", []) or [])):
        return []

    if str(closure_risk.get("decision") or "") in {"block", "defer"}:
        return []

    hygiene_actions = recommend_hygiene_actions(task_state, prompt_state, subagent_state)
    task_map = follow_through_task_map(guardrail_repo_root())
    executable_tasks = [
        task_map[action]
        for action in hygiene_actions
        if action in task_map
    ]
    executable_tasks = list(dedupe_preserve_order(executable_tasks))[:4]
    if not executable_tasks:
        return []

    return [
        {
            "id": make_signature(task_name, task_label),
            "task": task_label,
            "source": "logical-follow-through",
        }
        for task_label in executable_tasks
    ]


def sync_logical_follow_through_ticket(
    project_root: Path,
    objective: str,
    logical_next_tasks: list[dict[str, Any]],
    execution_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticket_id = "logical-follow-through"
    tickets_path = deferred_tickets_file(project_root)
    payload = load_deferred_tickets(tickets_path)
    tickets = list(payload.get("tickets", []) or [])
    execution_report = execution_report if isinstance(execution_report, dict) else {}

    if logical_next_tasks:
        ticket = {
            "id": ticket_id,
            "status": "open",
            "priority": "medium",
            "flow": "quality",
            "source": "stop-closure",
            "task": compact_text(objective, 160),
            "summary": compact_text(
                "Suite logique immediate detectee: ajouter les taches de follow-through a la checklist active puis les executer.",
                220,
            ),
            "recommendedTasks": [str(item.get("task") or "") for item in logical_next_tasks if str(item.get("task") or "")],
            "executedTasks": list(execution_report.get("executedTasks", []) or []),
            "failedTask": str(execution_report.get("failedTask") or ""),
            "updatedAt": timestamp_now(),
        }
        upsert_deferred_ticket(tickets_path, ticket)
        return ticket

    if str(execution_report.get("status") or "") in {"completed", "already-satisfied"}:
        ticket = {
            "id": ticket_id,
            "status": "closed",
            "priority": "medium",
            "flow": "quality",
            "source": "stop-closure",
            "task": compact_text(objective, 160),
            "summary": compact_text(
                "Suite logique immediate executee et cloturee automatiquement.",
                220,
            ),
            "recommendedTasks": [],
            "executedTasks": list(execution_report.get("executedTasks", []) or []),
            "failedTask": "",
            "executionStatus": str(execution_report.get("status") or "completed"),
            "updatedAt": timestamp_now(),
        }
        upsert_deferred_ticket(tickets_path, ticket)
        return ticket

    updated = False
    for index, existing in enumerate(tickets):
        if str(existing.get("id") or "") != ticket_id:
            continue
        updated_ticket = dict(existing)
        updated_ticket["status"] = "closed"
        updated_ticket["recommendedTasks"] = []
        if execution_report:
            updated_ticket["executedTasks"] = list(execution_report.get("executedTasks", []) or [])
            updated_ticket["failedTask"] = str(execution_report.get("failedTask") or "")
            updated_ticket["executionStatus"] = str(execution_report.get("status") or "")
        updated_ticket["updatedAt"] = timestamp_now()
        tickets[index] = updated_ticket
        updated = True
        break

    if updated:
        payload["updatedAt"] = timestamp_now()
        payload["tickets"] = tickets
        save_deferred_tickets(tickets_path, payload)
    return {}


def make_signature(*parts: str) -> str:
    normalized = "||".join(compact_text(part, 180).lower() for part in parts if part.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]


def logical_follow_through_report_file(project_root: Path) -> Path:
    module = follow_through_module()
    resolver = getattr(module, "logical_follow_through_report_file", None) if module is not None else None
    if callable(resolver):
        try:
            return resolver(project_root)
        except Exception:
            pass
    return project_root / "_grimoire-runtime-output" / "task-flow" / "logical-follow-through.json"


def logical_follow_through_signature(objective: str, task_labels: list[str]) -> str:
    module = follow_through_module()
    resolver = getattr(module, "logical_follow_through_signature", None) if module is not None else None
    if callable(resolver):
        try:
            return str(resolver(objective, task_labels))
        except Exception:
            pass
    return make_signature(objective, *task_labels)


def resolve_task_flow_script(project_root: Path) -> Path:
    preferred = project_root / ".github" / "hooks" / "scripts" / "grimoire-task-flow.sh"
    if preferred.exists():
        return preferred
    return guardrail_repo_root() / ".github" / "hooks" / "scripts" / "grimoire-task-flow.sh"


def resolve_follow_through_python(kit_root: Path) -> str:
    preferred = kit_root / ".venv" / "bin" / "python"
    if preferred.exists():
        return ".venv/bin/python"
    return sys.executable


def execute_logical_follow_through_tasks(
    project_root: Path,
    objective: str,
    logical_next_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    module = follow_through_module()
    executor = getattr(module, "execute_logical_follow_through_tasks", None) if module is not None else None
    task_specs = follow_through_task_specs(project_root)

    if callable(executor):
        try:
            result = executor(
                project_root=project_root,
                objective=objective,
                logical_next_tasks=logical_next_tasks,
                task_specs=task_specs,
                report_loader=load_json_mapping,
                report_saver=save_json_mapping,
                compact_text=compact_text,
                timestamp_now=timestamp_now,
                kit_root_resolver=project_context_root,
                task_flow_script_resolver=resolve_task_flow_script,
                python_resolver=resolve_follow_through_python,
                subprocess_run=subprocess.run,
                timeout_error_cls=subprocess.TimeoutExpired,
            )
        except Exception:
            result = {}
        if isinstance(result, dict):
            return result

    task_labels = [str(item.get("task") or "") for item in logical_next_tasks if str(item.get("task") or "")]
    if not task_labels:
        return {}

    signature = logical_follow_through_signature(objective, task_labels)
    report_path = logical_follow_through_report_file(project_root)
    existing_report = load_json_mapping(report_path)
    if str(existing_report.get("signature") or "") == signature and str(existing_report.get("status") or "") == "completed":
        return {
            "signature": signature,
            "status": "already-satisfied",
            "executedTasks": list(existing_report.get("executedTasks", []) or task_labels),
            "failedTask": "",
            "results": list(existing_report.get("results", []) or []),
            "updatedAt": timestamp_now(),
        }

    report = {
        "signature": signature,
        "status": "unavailable",
        "executedTasks": [],
        "failedTask": "",
        "results": [],
        "updatedAt": timestamp_now(),
        "reason": "follow-through-module-missing",
    }
    save_json_mapping(report_path, report)
    return report


def load_json_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def save_json_mapping(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def timestamp_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hook_runtime_root_from_hook_path(path: Path) -> Path:
    if path.parent.name in {"pre-compact", "session-start", "stop", "subagent-stop"}:
        return path.parent.parent
    return path.parent


def session_state_file_from_hook_path(path: Path) -> Path:
    return hook_runtime_root_from_hook_path(path) / "session-state.json"


def obligation_rank(level: str) -> int:
    return OBLIGATION_PRIORITY.get(level.lower(), -1)


def blocking_open_obligations(obligations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in obligations
        if isinstance(item, dict) and str(item.get("level") or "").lower() in {"critical", "high"}
    ]


def make_obligation(obligation_id: str, level: str, summary: str, source: str) -> dict[str, Any]:
    return {
        "id": obligation_id,
        "level": level,
        "status": "open",
        "source": source,
        "summary": compact_text(summary, 220),
    }


def make_evidence(entry_type: str, status: str, summary: str, source: str) -> dict[str, Any]:
    return {
        "type": entry_type,
        "status": status,
        "source": source,
        "summary": compact_text(summary, 220),
    }


def build_session_obligations(
    prompt_state: dict[str, Any],
    task_state: dict[str, Any],
    subagent_state: dict[str, Any],
) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []

    clarification = prompt_state.get("clarification") if isinstance(prompt_state.get("clarification"), dict) else {}
    clarification_state = (
        prompt_state.get("clarificationState") if isinstance(prompt_state.get("clarificationState"), dict) else {}
    )
    clarification_status = str(clarification_state.get("status") or "")
    if clarification.get("recommended") and clarification_status not in {"resolved", "auto-resolved"}:
        question = compact_text(
            str(clarification_state.get("question") or clarification.get("question") or ""),
            220,
        )
        options = list(clarification_state.get("options", []) or clarification.get("options", []) or [])[:3]
        preview = " / ".join(str(item) for item in options)
        if clarification_status == "needs-relance":
            answer = compact_text(str(clarification_state.get("answer") or ""), 120)
            summary = question or f"Poser une unique question batch avant de diverger davantage. Options: {preview}"
            if answer:
                summary = f"{summary} Derniere reponse insuffisante: {answer}"
        else:
            summary = question or f"Poser une unique question batch avant de diverger davantage. Options: {preview}"
        obligations.append(
            make_obligation(
                "clarification-batch",
                "high",
                summary,
                "prompt",
            )
        )

    external_references = (
        prompt_state.get("externalReferences") if isinstance(prompt_state.get("externalReferences"), dict) else {}
    )
    external_reference_state = (
        prompt_state.get("externalReferenceState")
        if isinstance(prompt_state.get("externalReferenceState"), dict)
        else {}
    )
    internal_references = (
        prompt_state.get("internalReferences") if isinstance(prompt_state.get("internalReferences"), dict) else {}
    )
    internal_reference_state = (
        prompt_state.get("internalReferenceState")
        if isinstance(prompt_state.get("internalReferenceState"), dict)
        else {}
    )
    external_reference_status = str(external_reference_state.get("status") or "")
    if external_references.get("recommended") and not bool(external_reference_state.get("satisfied")):
        libraries = ", ".join(str(item) for item in list(external_reference_state.get("libraries", []) or external_references.get("libraries", []) or [])[:3])
        suffix = f" ({libraries})" if libraries else ""
        if external_reference_status == "context7-resolved":
            summary = f"Capturer la preuve Context7 finale{suffix} apres la resolution de librairie."
        else:
            summary = f"Capturer une preuve Context7 ou fallback web{suffix} avant synthese finale."
        obligations.append(make_obligation("external-reference-proof", "high", summary, "prompt"))
    if internal_references.get("recommended") and not bool(internal_reference_state.get("satisfied")):
        surfaces = ", ".join(
            str(item) for item in list(internal_reference_state.get("surfaces", []) or internal_references.get("surfaces", []) or [])[:3]
        )
        suffix = f" ({surfaces})" if surfaces else ""
        summary = f"Capturer une preuve de consultation des references internes{suffix} avant synthese finale."
        obligations.append(make_obligation("internal-reference-proof", "high", summary, "prompt"))

    visual_validation = (
        prompt_state.get("visualValidation") if isinstance(prompt_state.get("visualValidation"), dict) else {}
    )
    visual_validation_state = (
        prompt_state.get("visualValidationState")
        if isinstance(prompt_state.get("visualValidationState"), dict)
        else {}
    )
    if visual_validation.get("required") and not bool(visual_validation_state.get("satisfied")):
        criteria = ", ".join(
            str(item)
            for item in list(visual_validation_state.get("criteria", []) or visual_validation.get("criteria", []) or [])[:3]
        )
        retention = visual_validation.get("retention") if isinstance(visual_validation.get("retention"), dict) else {}
        ttl = int(retention.get("defaultTtlDays", 14) or 14)
        summary = (
            "Valider le visuel avant cloture sur criteres explicites"
            + (f" ({criteria})" if criteria else "")
            + f" et ranger les captures avec retention TTL {ttl} jours."
        )
        obligations.append(make_obligation("visual-validation-gate", "high", summary, "prompt"))

    token_budget = prompt_state.get("tokenBudget") if isinstance(prompt_state.get("tokenBudget"), dict) else {}
    token_level = str(token_budget.get("level") or "")
    if token_level in {"warning", "critical", "emergency"}:
        obligations.append(
            make_obligation(
                "compact-context",
                "high" if token_level in {"critical", "emergency"} else "medium",
                f"Compacter le contexte avant synthese finale (budget token {token_level}).",
                "prompt",
            )
        )

    task_name = str(task_state.get("task") or "")
    task_status = str(task_state.get("status") or "").lower()
    if task_name and task_status and task_status not in {"success", "completed", "ok"}:
        obligations.append(
            make_obligation(
                "taskflow-recovery",
                "critical",
                f"Corriger ou relancer le task-flow avant cloture: {task_name} ({task_status}).",
                "task-flow",
            )
        )

    follow_up = subagent_state.get("followUp") if isinstance(subagent_state.get("followUp"), dict) else {}
    if follow_up.get("breakerRecommended"):
        obligations.append(
            make_obligation(
                "breaker-post-tests",
                "high",
                "Executer le breaker post-tests: review adversariale, edge-case hunt, retests, quick-check puis preflight.",
                "subagent-stop",
            )
        )

    conflict = subagent_state.get("conflict") if isinstance(subagent_state.get("conflict"), dict) else {}
    if conflict:
        obligations.append(
            make_obligation(
                "conflict-resolution",
                "critical",
                "Arbitrer le conflit inter-subagents avant synthese finale.",
                "subagent-stop",
            )
        )

    if follow_up.get("challengeRecommended"):
        obligations.append(
            make_obligation(
                "challenge-review",
                "high",
                "Declencher Challenge Mode ou CVTL ciblee avant cloture.",
                "subagent-stop",
            )
        )

    return sorted(obligations, key=lambda item: (-obligation_rank(str(item.get("level") or "")), str(item.get("id") or "")))


def build_session_evidence(prompt_state: dict[str, Any], task_state: dict[str, Any], subagent_state: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []

    clarification_state = (
        prompt_state.get("clarificationState") if isinstance(prompt_state.get("clarificationState"), dict) else {}
    )
    clarification_status = str(clarification_state.get("status") or "")
    selected_option = compact_text(str(clarification_state.get("selectedOption") or ""), 160)
    if clarification_status in {"resolved", "auto-resolved"} and selected_option:
        source = str(clarification_state.get("resolutionSource") or clarification_status)
        evidence.append(
            make_evidence(
                "clarification",
                clarification_status,
                f"Clarification resolue: {selected_option} ({source})",
                "prompt",
            )
        )

    external_reference_state = (
        prompt_state.get("externalReferenceState")
        if isinstance(prompt_state.get("externalReferenceState"), dict)
        else {}
    )
    internal_reference_state = (
        prompt_state.get("internalReferenceState")
        if isinstance(prompt_state.get("internalReferenceState"), dict)
        else {}
    )
    external_reference_status = str(external_reference_state.get("status") or "")
    external_reference_libraries = ", ".join(
        str(item) for item in list(external_reference_state.get("libraries", []) or [])[:3]
    )
    external_reference_library_id = compact_text(str(external_reference_state.get("libraryId") or ""), 120)
    if external_reference_status and list(external_reference_state.get("proofs", []) or []):
        detail = external_reference_library_id or external_reference_libraries or "preuve externe"
        evidence.append(
            make_evidence(
                "external-reference",
                external_reference_status,
                f"Preuve externe: {detail} ({external_reference_status})",
                "post-tool-use",
            )
        )
    internal_reference_status = str(internal_reference_state.get("status") or "")
    internal_reference_detail = compact_text(str(internal_reference_state.get("detail") or ""), 120)
    if internal_reference_status and list(internal_reference_state.get("proofs", []) or []):
        detail = internal_reference_detail or "references internes"
        evidence.append(
            make_evidence(
                "internal-reference",
                internal_reference_status,
                f"Preuve references internes: {detail} ({internal_reference_status})",
                "post-tool-use",
            )
        )

    visual_validation_state = (
        prompt_state.get("visualValidationState")
        if isinstance(prompt_state.get("visualValidationState"), dict)
        else {}
    )
    visual_validation_status = str(visual_validation_state.get("status") or "")
    if visual_validation_status and list(visual_validation_state.get("proofs", []) or []):
        authority = compact_text(str(visual_validation_state.get("acceptanceAuthority") or "user"), 120)
        evidence.append(
            make_evidence(
                "visual-validation",
                visual_validation_status,
                f"Preuve visuelle capturee ({visual_validation_status}) - validation par {authority}",
                "post-tool-use",
            )
        )

    task_name = str(task_state.get("task") or "")
    task_status = str(task_state.get("status") or "")
    if task_name:
        evidence.append(
            make_evidence(
                "task-flow",
                task_status or "unknown",
                f"Dernier task-flow observe: {task_name} [{task_status or 'unknown'}]",
                "task-flow",
            )
        )

    agent = str(subagent_state.get("agent") or "")
    grade = str(subagent_state.get("grade") or "")
    trust = subagent_state.get("trust") if isinstance(subagent_state.get("trust"), dict) else {}
    trust_level = str(trust.get("level") or "") if isinstance(trust, dict) else ""
    if agent and (grade or trust_level):
        summary = f"Derniere review subagent: {agent}"
        if grade:
            summary += f" grade {grade}"
        if trust_level:
            summary += f" / trust {trust_level}"
        evidence.append(make_evidence("subagent-review", "observed", summary, "subagent-stop"))

    return evidence


def summarize_open_obligations(obligations: list[dict[str, Any]], limit: int = 3) -> str:
    if not obligations:
        return ""
    preview = "; ".join(str(item.get("summary") or "") for item in obligations[:limit])
    return f"Obligations ouvertes: {preview}"


def summarize_evidence(evidence: list[dict[str, Any]], limit: int = 2) -> str:
    if not evidence:
        return ""
    preview = "; ".join(str(item.get("summary") or "") for item in evidence[:limit])
    return f"Preuves recentes: {preview}"


def persist_session_state(
    session_state_file: Path,
    prompt_state: dict[str, Any],
    task_state: dict[str, Any],
    subagent_state: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    open_obligations = build_session_obligations(prompt_state, task_state, subagent_state)
    evidence = build_session_evidence(prompt_state, task_state, subagent_state)
    clarification_state = (
        prompt_state.get("clarificationState") if isinstance(prompt_state.get("clarificationState"), dict) else {}
    )
    external_reference_state = (
        prompt_state.get("externalReferenceState")
        if isinstance(prompt_state.get("externalReferenceState"), dict)
        else {}
    )
    internal_reference_state = (
        prompt_state.get("internalReferenceState")
        if isinstance(prompt_state.get("internalReferenceState"), dict)
        else {}
    )
    state = {
        "timestamp": timestamp_now(),
        "source": source,
        "objective": compact_text(str(prompt_state.get("promptPreview") or ""), 180),
        "clarificationState": clarification_state,
        "externalReferenceState": external_reference_state,
        "internalReferenceState": internal_reference_state,
        "openObligations": open_obligations,
        "openObligationsCount": len(open_obligations),
        "criticalOpenCount": sum(1 for item in open_obligations if str(item.get("level") or "") == "critical"),
        "evidence": evidence,
        "summary": summarize_open_obligations(open_obligations),
        "evidenceSummary": summarize_evidence(evidence),
    }
    save_json_mapping(session_state_file, state)
    return state


def deferred_tickets_file(project_root: Path) -> Path:
    return project_root / "_grimoire-runtime-output" / "task-flow" / "deferred-tickets.json"


def load_deferred_tickets(path: Path) -> dict[str, Any]:
    payload = load_json_mapping(path)
    tickets = payload.get("tickets") if isinstance(payload, dict) else []
    if not isinstance(tickets, list):
        tickets = []
    return {
        "updatedAt": str(payload.get("updatedAt") or "") if isinstance(payload, dict) else "",
        "tickets": [item for item in tickets if isinstance(item, dict)],
    }


def save_deferred_tickets(path: Path, payload: dict[str, Any]) -> None:
    normalized = {
        "updatedAt": str(payload.get("updatedAt") or timestamp_now()),
        "tickets": [item for item in list(payload.get("tickets", []) or []) if isinstance(item, dict)],
    }
    save_json_mapping(path, normalized)


def upsert_deferred_ticket(path: Path, ticket: dict[str, Any]) -> None:
    payload = load_deferred_tickets(path)
    tickets = list(payload.get("tickets", []) or [])
    ticket_id = str(ticket.get("id") or "")
    if not ticket_id:
        return

    replaced = False
    for index, existing in enumerate(tickets):
        if str(existing.get("id") or "") == ticket_id:
            tickets[index] = ticket
            replaced = True
            break
    if not replaced:
        tickets.append(ticket)

    payload["updatedAt"] = timestamp_now()
    payload["tickets"] = sorted(tickets, key=lambda item: (-obligation_rank(str(item.get("priority") or "")), str(item.get("id") or "")))
    save_deferred_tickets(path, payload)


def sync_deferred_tickets(project_root: Path, subagent_state: dict[str, Any]) -> list[dict[str, Any]]:
    follow_up = subagent_state.get("followUp") if isinstance(subagent_state.get("followUp"), dict) else {}
    task_text = compact_text(str(subagent_state.get("task") or ""), 180)
    conflict = subagent_state.get("conflict") if isinstance(subagent_state.get("conflict"), dict) else {}
    tickets: list[dict[str, Any]] = []

    if follow_up.get("breakerRecommended"):
        tickets.append(
            {
                "id": "breaker-post-tests",
                "status": "open",
                "priority": "high",
                "flow": "quality",
                "source": "subagent-stop",
                "task": task_text,
                "summary": "Executer le breaker post-tests complet apres une suite verte.",
                "recommendedTasks": ["grimoire: quickcheck", "grimoire: preflight"],
                "updatedAt": timestamp_now(),
            }
        )

    if follow_up.get("challengeRecommended"):
        tickets.append(
            {
                "id": "challenge-review",
                "status": "open",
                "priority": "critical" if conflict else "high",
                "flow": "review",
                "source": "subagent-stop",
                "task": task_text,
                "summary": "Arbitrer via challenge mode ou CVTL avant synthese finale.",
                "recommendedTasks": [],
                "updatedAt": timestamp_now(),
            }
        )

    tickets_file = deferred_tickets_file(project_root)
    for ticket in tickets:
        upsert_deferred_ticket(tickets_file, ticket)
    return tickets


def summarize_deferred_tickets(project_root: Path, limit: int = 2) -> str:
    payload = load_deferred_tickets(deferred_tickets_file(project_root))
    tickets = [item for item in list(payload.get("tickets", []) or []) if str(item.get("status") or "") == "open"]
    if not tickets:
        return ""
    preview = "; ".join(compact_text(str(item.get("summary") or ""), 160) for item in tickets[:limit])
    return f"Tickets differes ouverts: {preview}"


def add_closure_risk_driver(
    drivers: list[dict[str, Any]],
    driver_id: str,
    weight: int,
    summary: str,
) -> None:
    if weight <= 0:
        return

    normalized_id = compact_text(driver_id, 80) or f"driver-{len(drivers) + 1}"
    normalized_summary = compact_text(summary, 180)
    if not normalized_summary:
        return

    for index, existing in enumerate(drivers):
        if str(existing.get("id") or "") == normalized_id:
            if int(existing.get("weight", 0) or 0) < weight:
                drivers[index] = {"id": normalized_id, "weight": weight, "summary": normalized_summary}
            return

    drivers.append({"id": normalized_id, "weight": weight, "summary": normalized_summary})


def assess_closure_risk(
    prompt_state: dict[str, Any],
    task_state: dict[str, Any],
    subagent_state: dict[str, Any],
    session_state: dict[str, Any],
    stop_hook_active: bool,
) -> dict[str, Any]:
    drivers: list[dict[str, Any]] = []
    open_obligations = [item for item in list(session_state.get("openObligations", []) or []) if isinstance(item, dict)]
    blocking_obligations = blocking_open_obligations(open_obligations)
    seen_obligation_ids: set[str] = set()
    highest_obligation_level = ""

    for obligation in open_obligations:
        obligation_id = compact_text(str(obligation.get("id") or ""), 80)
        level = str(obligation.get("level") or "").lower()
        if obligation_rank(level) > obligation_rank(highest_obligation_level):
            highest_obligation_level = level
        weight = CLOSURE_RISK_OBLIGATION_WEIGHTS.get(obligation_id, {"critical": 35, "high": 20, "medium": 10, "low": 5}.get(level, 5))
        summary = str(obligation.get("summary") or obligation_id or "obligation ouverte")
        add_closure_risk_driver(drivers, obligation_id or f"obligation-{len(drivers) + 1}", weight, summary)
        if obligation_id:
            seen_obligation_ids.add(obligation_id)

    task_name = compact_text(str(task_state.get("task") or ""), 120)
    task_status = str(task_state.get("status") or "").lower()
    if task_name and task_status and task_status not in {"success", "completed", "ok"} and "taskflow-recovery" not in seen_obligation_ids:
        add_closure_risk_driver(
            drivers,
            "taskflow-recovery-fallback",
            60,
            f"Task-flow encore en anomalie: {task_name} ({task_status})",
        )

    conflict = subagent_state.get("conflict") if isinstance(subagent_state.get("conflict"), dict) else {}
    if conflict and "conflict-resolution" not in seen_obligation_ids:
        add_closure_risk_driver(
            drivers,
            "conflict-resolution-fallback",
            50,
            "Conflit inter-subagents non arbitre avant cloture.",
        )

    flags = [str(item) for item in list(subagent_state.get("flags", []) or []) if str(item).strip()]
    if "quality-red" in flags and "challenge-review" not in seen_obligation_ids:
        add_closure_risk_driver(drivers, "quality-red", 20, "Evaluation subagent en zone rouge.")
    if "trust-red" in flags and "challenge-review" not in seen_obligation_ids:
        add_closure_risk_driver(drivers, "trust-red", 20, "Trust scorer en zone rouge.")

    token_budget = prompt_state.get("tokenBudget") if isinstance(prompt_state.get("tokenBudget"), dict) else {}
    token_level = str(token_budget.get("level") or "")
    if token_level in {"critical", "emergency"} and "compact-context" not in seen_obligation_ids:
        add_closure_risk_driver(drivers, "token-budget", 15, f"Budget token {token_level} avant cloture.")

    if len(open_obligations) >= 3:
        add_closure_risk_driver(drivers, "obligation-stack", 10, "Empilement de plusieurs obligations ouvertes a la cloture.")

    score = min(100, sum(int(item.get("weight", 0) or 0) for item in drivers))
    if score >= 80:
        level = "critical"
    elif score >= 45:
        level = "high"
    elif score >= 20:
        level = "medium"
    else:
        level = "low"

    if obligation_rank(highest_obligation_level) > obligation_rank(level):
        level = highest_obligation_level

    missing_reference_proofs: list[str] = []
    if "external-reference-proof" in seen_obligation_ids:
        missing_reference_proofs.append("preuve externe obligatoire absente")
    if "internal-reference-proof" in seen_obligation_ids:
        missing_reference_proofs.append("preuve interne obligatoire absente")

    if missing_reference_proofs or blocking_obligations or level in {"critical", "high"}:
        decision = "block"
    elif level == "medium":
        decision = "note"
    else:
        decision = "allow"

    top_drivers = sorted(drivers, key=lambda item: (-int(item.get("weight", 0) or 0), str(item.get("id") or "")))[:3]
    driver_preview = "; ".join(str(item.get("summary") or "") for item in top_drivers)
    summary_level = CLOSURE_RISK_LEVEL_LABELS.get(level, level)
    summary = f"Risque de cloture {summary_level} ({score}/100)"
    if driver_preview:
        summary += f": {driver_preview}"

    reason = ""
    if missing_reference_proofs:
        reason = (
            summary
            + ". Cloture interdite tant que les preuves obligatoires manquent: "
            + "; ".join(missing_reference_proofs)
            + "."
        )
    elif decision == "block":
        reason = summary

    return {
        "score": score,
        "level": level,
        "decision": decision,
        "drivers": top_drivers,
        "summary": summary,
        "reason": reason,
    }


def sync_closure_risk_ticket(
    project_root: Path,
    closure_risk: dict[str, Any],
    objective: str,
    hygiene_actions: tuple[str, ...],
) -> dict[str, Any]:
    ticket_id = "closure-risk-review"
    tickets_path = deferred_tickets_file(project_root)
    payload = load_deferred_tickets(tickets_path)
    tickets = list(payload.get("tickets", []) or [])

    if str(closure_risk.get("decision") or "") in {"block", "defer"}:
        ticket = {
            "id": ticket_id,
            "status": "open",
            "priority": "critical" if str(closure_risk.get("level") or "") == "critical" else "high",
            "flow": "review",
            "source": "stop-closure",
            "task": compact_text(objective, 160),
            "summary": compact_text(str(closure_risk.get("summary") or "Risque de cloture eleve"), 220),
            "recommendedTasks": list(hygiene_actions[:4]),
            "updatedAt": timestamp_now(),
        }
        upsert_deferred_ticket(tickets_path, ticket)
        return ticket

    updated = False
    for index, existing in enumerate(tickets):
        if str(existing.get("id") or "") != ticket_id:
            continue
        updated_ticket = dict(existing)
        updated_ticket["status"] = "closed"
        updated_ticket["updatedAt"] = timestamp_now()
        tickets[index] = updated_ticket
        updated = True
        break

    if updated:
        payload["updatedAt"] = timestamp_now()
        payload["tickets"] = tickets
        save_deferred_tickets(tickets_path, payload)
    return {}


def normalize_task_candidate(text: str) -> str:
    cleaned = TASK_LINE_PATTERN.sub(r"\g<task>", text).strip()
    cleaned = cleaned.strip(" \t\r\n-:;,.[]()")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) < 4:
        return ""
    if cleaned.lower() in {"todo", "tasks", "checklist", "et", "puis"}:
        return ""
    return cleaned[:160]


def extract_task_candidates(prompt: str) -> tuple[str, ...]:
    candidates: list[str] = []

    for line in prompt.splitlines():
        if not TASK_LINE_PATTERN.match(line):
            continue
        candidate = normalize_task_candidate(line)
        if candidate:
            candidates.append(candidate)

    if len(candidates) < 2:
        for match in INLINE_ENUM_PATTERN.findall(prompt):
            candidate = normalize_task_candidate(match)
            if candidate:
                candidates.append(candidate)

    if len(candidates) < 2:
        marker = TASK_TAIL_PATTERN.search(prompt)
        if marker:
            for segment in re.split(r"[\n;]+", marker.group(1)):
                candidate = normalize_task_candidate(segment)
                if candidate:
                    candidates.append(candidate)

    return dedupe_preserve_order(candidates[:7])


def detect_brainstorm_recommended(prompt: str, prompt_lower: str, task_items: tuple[str, ...]) -> bool:
    if any(term in prompt_lower for term in EXPLICIT_BRAINSTORM_TERMS):
        return True
    if any(term in prompt_lower for term in AMBIGUITY_TERMS):
        return True
    if task_items:
        return False
    if "?" not in prompt:
        return False

    broad_request = any(term in prompt_lower for term in ("amelior", "improve", "strategie", "strategy", "approach"))
    has_specificity = any(term in prompt_lower for term in SPECIFICITY_TERMS)
    has_execution = any(term in prompt_lower for term in EXECUTION_TERMS)
    return broad_request and not has_specificity and not has_execution


def detect_autonomous_execution(prompt_lower: str) -> bool:
    return any(term in prompt_lower for term in AUTONOMOUS_EXECUTION_TERMS)


def analyze_prompt(prompt: str) -> PromptSignals:
    prompt_preview = re.sub(r"\s+", " ", prompt).strip()[:240]
    prompt_lower = prompt.lower()

    tags: list[str] = []
    notes: list[str] = []

    if any(term in prompt_lower for term in HOOK_TERMS):
        tags.append("hooks")
        notes.append(
            "References hooks: doc officielle VS Code hooks, DeepWiki vscode-copilot-chat 5.4/5.5/5.6, hooks workspace .github/hooks/*.json, scripts .github/hooks/scripts/*.sh."
        )

    if any(term in prompt_lower for term in TASK_FLOW_TERMS):
        tags.append("task-flow")
        notes.append(
            "Rappel: les hooks natifs ne couvrent pas tasks.json; utiliser .github/hooks/scripts/grimoire-task-flow.sh pour tracer et orchestrer les tasks."
        )

    if any(term in prompt_lower for term in OBSERVABILITY_TERMS):
        tags.append("observability")
        notes.append(
            "Observabilite dispo: _grimoire-runtime-output/GRIMOIRE_TRACE.jsonl pour les subagents et _grimoire-runtime-output/task-flow/latest.json pour les tasks."
        )

    if any(term in prompt_lower for term in GITHUB_FLOW_TERMS):
        tags.append("github-flow")
        notes.append(
            "Flow GitHub detecte: branche de travail hors main, PR obligatoire, titres et commits en Conventional Commits, CODEOWNERS et checks CI avant merge."
        )

    if any(term in prompt_lower for term in AGENTIC_PLUGIN_TERMS):
        tags.append("agentic-plugin")
        notes.append(
            "Definition utile: un plugin agentique est une surface d'extension exposee a l'agent (MCP server, bridge, hook, skill, tool ou adapter) avec contrat, permissions, traces et owner."
        )

    if wants_challenge(prompt_lower):
        tags.append("challenge")
        notes.append("Mode challenge detecte: steelman puis critique structuree avant toute validation finale.")

    if wants_debate(prompt_lower):
        tags.append("debate")
        notes.append("Mode debat detecte: si les avis divergent, preferer un mini-debat ou Party Mode cible.")

    if wants_orchestrator_control(prompt_lower):
        tags.append("orchestrator-control")
        notes.append(
            "Scope orchestrateur detecte: traiter le Master comme plan de controle du projet, pas comme simple routeur de prompts."
        )

    if wants_project_protector(prompt_lower):
        tags.append("project-protector")
        notes.append(
            "Protecteur projet demande: verifier viabilite, plus-value, cout de couplage, angles morts et preuve attendue avant d'accepter une piste."
        )

    if wants_dispatch_prompt_contract(prompt_lower):
        tags.append("dispatch-prompt")
        notes.append(
            "Prompt engineering de dispatch requis: chaque handoff doit expliciter mission, valeur, contexte projet, contraintes, risques, preuves et livrable."
        )

    if wants_interactive_clarification(prompt_lower):
        tags.append("interactive-clarification")
        notes.append(
            "Clarification continue demandee: privilegier une seule collecte outillee via vscode/askQuestions plutot qu'une relance conversationnelle libre."
        )

    if any(term in prompt_lower for term in TOKEN_CONTEXT_TERMS):
        tags.append("token-budget")
        notes.append("Budget token explicitement demande: verifier compaction, distillation et priorites de contexte.")

    if any(term in prompt_lower for term in DESIGN_AUTHORITY_TERMS):
        tags.append("design-authority")
        notes.append("Scope visuel detecte: verifier d'abord la DA projet avant toute proposition de fallback standard.")

    plan_only = bool(PLAN_ONLY_PATTERN.search(prompt_lower))
    if plan_only:
        tags.append("plan-only")
        notes.append(
            "Contrainte active: analyse/plan uniquement; aucune ecriture tant qu'une demande d'implementation explicite n'est pas donnee."
        )

    safety_focus = any(term in prompt_lower for term in SAFETY_TERMS)
    if safety_focus:
        tags.append("safety")
        notes.append(
            "Mode prudent: privilegier changements additifs, validations ciblees et gates progressifs audit -> ask -> block."
        )

    task_items = extract_task_candidates(prompt)
    if task_items:
        tags.append("task-list")
        preview = "; ".join(task_items[:3])
        notes.append(
            f"Prompt signal: checklist candidate detectee ({len(task_items)} items). Reprendre ces items dans la todo list avant execution: {preview}."
        )

    brainstorm_recommended = detect_brainstorm_recommended(prompt, prompt_lower, task_items)
    if brainstorm_recommended:
        tags.append("brainstorm")
        notes.append(
            "Prompt signal: demande exploratoire ou sous-specifiee. Commencer par cadrer hypotheses, options et criteres avant toute edition."
        )

    autonomous_execution = detect_autonomous_execution(prompt_lower)
    if autonomous_execution:
        tags.append("autonomous")
        notes.append(
            "Prompt signal: l'utilisateur attend une execution autonome de bout en bout une fois le cap valide."
        )

    return PromptSignals(
        prompt_preview=prompt_preview,
        tags=dedupe_preserve_order(tags),
        notes=dedupe_preserve_order(notes),
        plan_only=plan_only,
        safety_focus=safety_focus,
        task_items=task_items,
        brainstorm_recommended=brainstorm_recommended,
        autonomous_execution=autonomous_execution,
    )


def enrich_prompt_signals(project_root: Path, prompt: str, signals: PromptSignals) -> tuple[tuple[str, ...], dict[str, Any]]:
    notes: list[str] = []
    state: dict[str, Any] = {}
    prompt_lower = prompt.lower()

    triage_data: dict[str, Any] = {}
    suggested_agent = ""
    concierge_module = load_tool_module("concierge.py", "concierge")
    if concierge_module is not None and hasattr(concierge_module, "triage"):
        try:
            triage_result = concierge_module.triage(prompt)
            suggested_agent = str(getattr(triage_result, "suggested_agent", "") or "")
            triage_data = {
                "suggestedAgent": suggested_agent,
                "classification": str(getattr(triage_result, "classification", "") or ""),
                "confidence": float(getattr(triage_result, "confidence", 0.0) or 0.0),
                "reasoning": compact_text(str(getattr(triage_result, "reasoning", "") or ""), 220),
                "alternatives": list(getattr(triage_result, "alternatives", []) or [])[:3],
            }
            if suggested_agent:
                notes.append(
                    "Routage suggere: "
                    f"{suggested_agent} ({triage_data['classification']}, confiance {int(triage_data['confidence'] * 100)}%). "
                    f"{triage_data['reasoning']}"
                )
        except Exception:
            triage_data = {}

    task_type = derive_task_type(prompt, suggested_agent, signals.tags)
    clarification_plan = build_clarification_plan(prompt, prompt_lower, signals, task_type)
    challenge_requested = wants_challenge(prompt_lower)
    debate_requested = wants_debate(prompt_lower)
    proposal_challenge = detect_proposal_challenge(prompt_lower)
    design_authority = resolve_design_authority(project_root, prompt_lower, suggested_agent, task_type)
    visual_validation = detect_visual_validation_need(prompt_lower, signals, task_type)
    token_budget = assess_token_budget(project_root, prompt_lower)
    external_references = detect_external_reference_need(prompt_lower, suggested_agent, task_type)
    internal_references = detect_internal_reference_need(prompt_lower, suggested_agent, task_type)
    dispatch_contract = build_dispatch_contract(prompt_lower, task_type)

    procedural_patterns: list[str] = []
    procedural_module = load_tool_module("procedural-memory.py", "procedural_memory")
    if procedural_module is not None and hasattr(procedural_module, "lookup_patterns"):
        try:
            lookup_tags = [str(tag) for tag in signals.tags[:4]]
            if suggested_agent:
                lookup_tags.append(suggested_agent)
            matches = procedural_module.lookup_patterns(project_root, task_type, lookup_tags, limit=3)
            if not matches and lookup_tags:
                matches = procedural_module.lookup_patterns(project_root, task_type, limit=3)
            for match in matches[:2]:
                pattern = compact_text(str(match.get("pattern", "") or ""), 180)
                if pattern:
                    procedural_patterns.append(pattern)
            if procedural_patterns:
                notes.append("Pattern procedural utile: " + " | ".join(procedural_patterns[:2]))
        except Exception:
            procedural_patterns = []

    nudge_messages: list[dict[str, Any]] = []
    nudge_module = load_tool_module("nudge-engine.py", "nudge_engine")
    if nudge_module is not None and hasattr(nudge_module, "load_all_memory"):
        try:
            entries = nudge_module.load_all_memory(project_root)
            nudges = []
            if entries:
                if hasattr(nudge_module, "generate_recalls"):
                    nudges = nudge_module.generate_recalls(entries, query=prompt, agent=suggested_agent, max_nudges=2)
                if not nudges and hasattr(nudge_module, "generate_suggestions"):
                    nudges = nudge_module.generate_suggestions(entries, agent=suggested_agent, context=prompt, max_nudges=2)

            for nudge in nudges[:2]:
                message = compact_text(str(getattr(nudge, "message", "") or ""), 180)
                if not message:
                    continue
                nudge_messages.append(
                    {
                        "title": compact_text(str(getattr(nudge, "title", "") or ""), 80),
                        "message": message,
                        "relevance": float(getattr(nudge, "relevance", 0.0) or 0.0),
                    }
                )
            if nudge_messages:
                notes.append("Rappel contextuel: " + " | ".join(item["message"] for item in nudge_messages[:2]))
        except Exception:
            nudge_messages = []

    rag_chunks: list[dict[str, Any]] = []
    if should_rag_inject(prompt, signals, suggested_agent):
        rag_module = load_tool_module("rag-auto-inject.py", "rag_auto_inject")
        if rag_module is not None and hasattr(rag_module, "auto_inject"):
            try:
                rag_result = rag_module.auto_inject(project_root, prompt, max_chunks=2)
                for chunk in list(rag_result.get("chunks", []) or [])[:2]:
                    text = compact_text(str(chunk.get("text", "") or ""), 180)
                    source = compact_text(str(chunk.get("source", "") or ""), 80)
                    score = float(chunk.get("score", 0.0) or 0.0)
                    if text and source:
                        rag_chunks.append({"source": source, "score": round(score, 3), "text": text})
                if rag_chunks:
                    source_summary = ", ".join(f"{item['source']} ({item['score']:.2f})" for item in rag_chunks)
                    notes.append(f"RAG borne active: {source_summary}.")
            except Exception:
                rag_chunks = []

    if clarification_plan:
        question = compact_text(str(clarification_plan.get("question") or ""), 220)
        notes.append(
            "Clarification interactive requise maintenant: "
            f"{clarification_plan.get('instruction', 'poser une question batch')}. "
            f"Question suggeree: {question}"
        )

    if challenge_requested:
        notes.append("Challenge Mode demande: charger le protocole Rodin et critiquer sans complaisance l'artefact en contexte.")

    if proposal_challenge:
        notes.append(
            "Challenge proactif requis: "
            f"{proposal_challenge.get('summary', '')}. "
            f"{proposal_challenge.get('instruction', '')}"
        )

    if debate_requested:
        notes.append("Debat recommande: arbitrer via mini Party Mode si plusieurs lectures serieuses restent ouvertes.")

    if design_authority:
        if design_authority.get("found"):
            sources = ", ".join(str(item) for item in list(design_authority.get("sources", []))[:2])
            notes.append(f"DA projet detectee ({design_authority.get('scope')}): {sources}.")
        elif design_authority.get("fallback"):
            fallback = ", ".join(str(item) for item in list(design_authority.get("fallbackPrinciples", []))[:3])
            notes.append(f"Aucune DA projet explicite detectee pour ce scope. Fallback standard: {fallback}.")

    if visual_validation:
        retention = visual_validation.get("retention") if isinstance(visual_validation.get("retention"), dict) else {}
        ttl = int(retention.get("defaultTtlDays", 14) or 14)
        notes.append(
            "Validation visuelle requise: appliquer des criteres explicites, conserver les preuves (captures + proof-pack), "
            f"et ranger les captures avec retention TTL {ttl} jours."
        )

    if token_budget:
        notes.append(format_token_budget_note(token_budget))

    if external_references:
        libraries = ", ".join(str(item) for item in list(external_references.get("libraries", []))[:3])
        scope = f" ({libraries})" if libraries else ""
        notes.append(
            f"References externes recommandees{scope}: consulter Context7 d'abord pour la doc a jour, "
            "puis fetch_webpage seulement si la couverture est insuffisante."
        )

    if internal_references:
        surfaces = ", ".join(str(item) for item in list(internal_references.get("surfaces", []))[:3])
        notes.append(
            "References internes recommandees: scanner d'abord "
            f"{surfaces} via memory view, repo knowledge search ou lecture des docs canoniques."
        )

    if dispatch_contract:
        notes.append(
            "Contrat de dispatch requis: ne jamais transmettre le message brut; inclure mission, objectif/plus-value, contexte projet, contraintes, risques, preuves attendues et condition d'arret."
        )

    state["routing"] = triage_data
    state["memoryHints"] = {
        "taskType": task_type,
        "proceduralPatterns": procedural_patterns,
        "nudges": nudge_messages,
    }
    state["rag"] = {
        "enabled": bool(rag_chunks),
        "chunks": rag_chunks,
    }
    state["clarification"] = clarification_plan
    state["challengeMode"] = {"requested": challenge_requested, "source": "prompt"} if challenge_requested else {}
    state["proposalChallenge"] = proposal_challenge
    state["debateMode"] = {"recommended": debate_requested, "source": "prompt"} if debate_requested else {}
    state["designAuthority"] = design_authority
    state["visualValidation"] = visual_validation
    state["tokenBudget"] = token_budget
    state["externalReferences"] = external_references
    state["internalReferences"] = internal_references
    state["dispatchContract"] = dispatch_contract
    return dedupe_preserve_order(notes), state


def make_permission_payload(event_name: str, decision: str, reason: str, additional_context: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
            "additionalContext": additional_context,
        }
    }


def make_block_payload(event_name: str, reason: str, additional_context: str) -> dict[str, Any]:
    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": additional_context,
        },
    }


def detect_write_tool(tool_name: str, raw_lower: str) -> bool:
    if any(marker in tool_name for marker in WRITE_TOOL_MARKERS):
        return True
    return any(marker in raw_lower for marker in FALLBACK_WRITE_MARKERS)


def detect_edit_tool(tool_name: str, raw_lower: str) -> bool:
    if tool_name:
        return any(marker in tool_name for marker in EDIT_TOOL_MARKERS)
    return any(marker in raw_lower for marker in FALLBACK_EDIT_MARKERS)


def detect_terminal_tool(tool_name: str, raw_lower: str) -> bool:
    if any(marker in tool_name for marker in TERMINAL_TOOL_MARKERS):
        return True
    return "run_in_terminal" in raw_lower


def load_prompt_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_last_jsonl(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open("rb") as handle:
        handle.seek(max(path.stat().st_size - 4096, 0))
        chunk = handle.read().decode("utf-8", errors="ignore")
    for line in reversed(chunk.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def load_recent_jsonl_entries(path: Path, limit: int = 8) -> list[dict[str, Any]]:
    if limit <= 0 or not path.exists() or path.stat().st_size == 0:
        return []

    try:
        with path.open("rb") as handle:
            handle.seek(max(path.stat().st_size - 24576, 0))
            chunk = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    for line in chunk.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries[-limit:]


def compute_text_support(reference_text: str, source_text: str) -> tuple[float, tuple[str, ...]]:
    reference_tokens = tuple(dict.fromkeys(clarification_tokens(reference_text)))[:12]
    source_tokens = tuple(dict.fromkeys(clarification_tokens(source_text)))
    if not reference_tokens or not source_tokens:
        return 0.0, ()

    reference_set = set(reference_tokens)
    matched = tuple(dict.fromkeys(token for token in source_tokens if token in reference_set))[:6]
    if not matched:
        return 0.0, ()

    score = len(matched) / max(1, min(len(reference_tokens), 12))
    return round(min(1.0, score), 3), matched


def normalize_weighted_percentages(weights: list[float]) -> list[int]:
    if not weights:
        return []

    positive_weights = [max(0.0, float(value or 0.0)) for value in weights]
    total = sum(positive_weights)
    if total <= 0:
        return [0 for _ in positive_weights]

    raw_values = [(value / total) * 100 for value in positive_weights]
    rounded = [int(value) for value in raw_values]
    remainder = max(0, 100 - sum(rounded))
    order = sorted(range(len(raw_values)), key=lambda index: (raw_values[index] - rounded[index], raw_values[index]), reverse=True)
    for index in order[:remainder]:
        rounded[index] += 1
    return rounded


def build_memory_context(
    prompt: str,
    previous_session_state: dict[str, Any],
    internal_reference_state: dict[str, Any],
) -> dict[str, Any]:
    current_state = internal_reference_state if isinstance(internal_reference_state, dict) else {}
    previous_state = (
        previous_session_state.get("internalReferenceState")
        if isinstance(previous_session_state.get("internalReferenceState"), dict)
        else {}
    )
    current_detail = compact_text(str(current_state.get("detail") or ""), 160)
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for source_state in (current_state, previous_state):
        proofs = [item for item in list(source_state.get("proofs", []) or []) if isinstance(item, dict)]
        for proof in proofs:
            proof_source = str(proof.get("source") or "")
            if proof_source not in {"repo-memory", "memory-scope"}:
                continue

            detail = compact_text(str(proof.get("detail") or ""), 160)
            summary = compact_text(str(proof.get("responsePreview") or ""), 200)
            if not detail or not summary or detail in seen_paths:
                continue

            seen_paths.add(detail)
            relevance, matched_terms = compute_text_support(prompt, f"{detail} {summary}")
            if current_detail and detail == current_detail:
                relevance = min(1.0, relevance + 0.2)
            elif str(source_state.get("status") or "") in {"repo-memory-proved", "memory-scope-proved"}:
                relevance = min(1.0, relevance + 0.05)

            candidates.append(
                {
                    "path": detail,
                    "scope": proof_source,
                    "summary": summary,
                    "relevance": round(max(0.05, relevance), 3),
                    "matchedTerms": list(matched_terms),
                    "timestamp": str(proof.get("timestamp") or ""),
                }
            )

    if not candidates:
        return {}

    candidates.sort(key=lambda item: (-float(item.get("relevance", 0.0) or 0.0), str(item.get("path") or "")))
    selected = candidates[:2]
    relevance_pcts = normalize_weighted_percentages([float(item.get("relevance", 0.0) or 0.0) for item in selected])
    for item, pct in zip(selected, relevance_pcts, strict=False):
        item["relevancePct"] = pct

    reference_tokens = tuple(dict.fromkeys(clarification_tokens(prompt)))[:12]
    covered_terms = {term for item in selected for term in list(item.get("matchedTerms", []) or [])}
    coverage_pct = (
        round((len(covered_terms) / max(1, len(reference_tokens))) * 100)
        if reference_tokens
        else max(relevance_pcts or [0])
    )

    return {
        "enabled": True,
        "source": "cached-internal-proofs",
        "coveragePct": int(min(100, coverage_pct)),
        "snippets": selected,
    }


def format_memory_context_note(memory_context: dict[str, Any]) -> str:
    if not isinstance(memory_context, dict) or not memory_context.get("enabled"):
        return ""

    snippets = [item for item in list(memory_context.get("snippets", []) or []) if isinstance(item, dict)]
    if not snippets:
        return ""

    preview = " | ".join(
        f"{compact_text(str(item.get('path') or ''), 100)} ({int(item.get('relevancePct', 0) or 0)}%): {compact_text(str(item.get('summary') or ''), 90)}"
        for item in snippets[:2]
    )
    return f"Memoire auto-injectee depuis references internes deja prouvees: {preview}".strip()


def add_source_candidate(
    candidates: list[dict[str, Any]],
    seen_signatures: dict[str, int],
    *,
    label: str,
    source_type: str,
    text: str,
    hint: float,
    stage: str = "",
    status: str = "",
) -> None:
    normalized_label = compact_text(label, 160)
    normalized_text = compact_text(text, 240)
    if not normalized_label and not normalized_text:
        return

    signature = make_signature(source_type, normalized_label, stage or status)
    existing_index = seen_signatures.get(signature)
    if existing_index is not None:
        existing = candidates[existing_index]
        if len(normalized_text) > len(str(existing.get("text") or "")):
            existing["text"] = normalized_text
        existing["hint"] = max(float(existing.get("hint", 0.0) or 0.0), float(hint or 0.0))
        if stage and not existing.get("stage"):
            existing["stage"] = stage
        if status and not existing.get("status"):
            existing["status"] = status
        return

    seen_signatures[signature] = len(candidates)
    candidates.append(
        {
            "label": normalized_label,
            "type": source_type,
            "text": normalized_text,
            "hint": round(max(0.0, float(hint or 0.0)), 3),
            "stage": compact_text(stage, 40),
            "status": compact_text(status, 40),
        }
    )


def collect_source_candidates(prompt_state: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(prompt_state, dict):
        return []

    candidates: list[dict[str, Any]] = []
    seen_signatures: dict[str, int] = {}
    memory_context = prompt_state.get("memoryContext") if isinstance(prompt_state.get("memoryContext"), dict) else {}
    memory_paths = {
        compact_text(str(item.get("path") or ""), 160)
        for item in list(memory_context.get("snippets", []) or [])
        if isinstance(item, dict)
    }

    for snippet in [item for item in list(memory_context.get("snippets", []) or []) if isinstance(item, dict)]:
        add_source_candidate(
            candidates,
            seen_signatures,
            label=str(snippet.get("path") or ""),
            source_type="memory",
            text=str(snippet.get("summary") or ""),
            hint=float(snippet.get("relevance", 0.0) or 0.0),
            stage="memory-context",
            status="cached",
        )

    internal_reference_state = (
        prompt_state.get("internalReferenceState")
        if isinstance(prompt_state.get("internalReferenceState"), dict)
        else {}
    )
    for proof in [item for item in list(internal_reference_state.get("proofs", []) or []) if isinstance(item, dict)]:
        label = compact_text(str(proof.get("detail") or ""), 160)
        if label in memory_paths:
            continue
        proof_source = str(proof.get("source") or "")
        source_type = "memory" if proof_source in {"repo-memory", "memory-scope"} else "internal-reference"
        add_source_candidate(
            candidates,
            seen_signatures,
            label=label,
            source_type=source_type,
            text=str(proof.get("responsePreview") or proof.get("detail") or ""),
            hint=0.6 if bool(proof.get("satisfied")) else 0.25,
            stage=str(proof.get("stage") or ""),
            status=str(proof.get("status") or ""),
        )

    external_reference_state = (
        prompt_state.get("externalReferenceState")
        if isinstance(prompt_state.get("externalReferenceState"), dict)
        else {}
    )
    for proof in [item for item in list(external_reference_state.get("proofs", []) or []) if isinstance(item, dict)]:
        urls = [str(item) for item in list(proof.get("urls", []) or []) if str(item).strip()]
        label = compact_text(
            str(proof.get("libraryId") or proof.get("libraryName") or (urls[0] if urls else proof.get("query") or proof.get("stage") or "")),
            160,
        )
        add_source_candidate(
            candidates,
            seen_signatures,
            label=label,
            source_type="external-doc",
            text=str(proof.get("responsePreview") or proof.get("query") or label),
            hint=0.75 if bool(proof.get("satisfied")) else 0.35,
            stage=str(proof.get("stage") or ""),
            status=str(proof.get("status") or ""),
        )

    rag_state = prompt_state.get("rag") if isinstance(prompt_state.get("rag"), dict) else {}
    for chunk in [item for item in list(rag_state.get("chunks", []) or []) if isinstance(item, dict)]:
        add_source_candidate(
            candidates,
            seen_signatures,
            label=str(chunk.get("source") or ""),
            source_type="rag",
            text=str(chunk.get("text") or ""),
            hint=float(chunk.get("score", 0.0) or 0.0),
            stage="rag-auto-inject",
            status="injected",
        )

    design_authority = prompt_state.get("designAuthority") if isinstance(prompt_state.get("designAuthority"), dict) else {}
    for source in [str(item) for item in list(design_authority.get("sources", []) or [])[:3] if str(item).strip()]:
        add_source_candidate(
            candidates,
            seen_signatures,
            label=source,
            source_type="design-authority",
            text=source,
            hint=0.2,
            stage="design-authority",
            status="available",
        )

    return candidates


def source_base_weight(source_type: str) -> float:
    return {
        "memory": 1.0,
        "internal-reference": 0.85,
        "external-doc": 1.0,
        "rag": 0.75,
        "design-authority": 0.45,
    }.get(source_type, 0.6)


def build_source_report(
    agent_name: str,
    task_text: str,
    output_text: str,
    prompt_state: dict[str, Any],
) -> dict[str, Any]:
    candidates = collect_source_candidates(prompt_state)
    if not candidates:
        return {}

    reference_text = " ".join(
        part for part in (task_text, output_text, compact_text(str(prompt_state.get("promptPreview") or ""), 240)) if part
    )
    reference_tokens = tuple(dict.fromkeys(clarification_tokens(reference_text)))[:12]
    scored: list[dict[str, Any]] = []
    covered_terms: set[str] = set()

    for candidate in candidates:
        support_score, matched_terms = compute_text_support(reference_text, f"{candidate.get('label', '')} {candidate.get('text', '')}")
        hint = float(candidate.get("hint", 0.0) or 0.0)
        raw_weight = source_base_weight(str(candidate.get("type") or "")) * max(0.08, support_score * 0.8 + hint * 0.2)
        if support_score <= 0.0 and hint < 0.2:
            continue
        if raw_weight < 0.09:
            continue
        covered_terms.update(matched_terms)
        scored.append(
            {
                "label": str(candidate.get("label") or ""),
                "type": str(candidate.get("type") or ""),
                "stage": str(candidate.get("stage") or ""),
                "status": str(candidate.get("status") or ""),
                "matchedTerms": list(matched_terms),
                "supportScore": round(support_score, 3),
                "rawWeight": round(raw_weight, 3),
            }
        )

    if not scored:
        fallback_candidates = sorted(
            candidates,
            key=lambda item: (-float(item.get("hint", 0.0) or 0.0), str(item.get("label") or "")),
        )[:2]
        for candidate in fallback_candidates:
            scored.append(
                {
                    "label": str(candidate.get("label") or ""),
                    "type": str(candidate.get("type") or ""),
                    "stage": str(candidate.get("stage") or ""),
                    "status": str(candidate.get("status") or ""),
                    "matchedTerms": [],
                    "supportScore": 0.0,
                    "rawWeight": round(source_base_weight(str(candidate.get("type") or "")) * max(0.1, float(candidate.get("hint", 0.0) or 0.0)), 3),
                }
            )

    scored.sort(key=lambda item: (-float(item.get("rawWeight", 0.0) or 0.0), str(item.get("label") or "")))
    scored = scored[:4]
    support_pcts = normalize_weighted_percentages([float(item.get("rawWeight", 0.0) or 0.0) for item in scored])
    for item, pct in zip(scored, support_pcts, strict=False):
        item["supportPct"] = pct
        item.pop("rawWeight", None)

    coverage_pct = (
        round((len(covered_terms) / max(1, len(reference_tokens))) * 100)
        if reference_tokens
        else max(support_pcts or [0])
    )
    summary = "; ".join(f"{compact_text(str(item.get('label') or ''), 90)} {int(item.get('supportPct', 0) or 0)}%" for item in scored[:3])

    return {
        "agent": agent_name,
        "coveragePct": int(min(100, coverage_pct)),
        "sourceCount": len(scored),
        "sources": scored,
        "summary": f"Observabilite sources {agent_name}: appui documentaire {int(min(100, coverage_pct))}% | {summary}".strip(),
    }


def format_source_report_note(source_report: dict[str, Any]) -> str:
    if not isinstance(source_report, dict):
        return ""
    return compact_text(str(source_report.get("summary") or ""), 320)


def source_observability_dir(project_root: Path) -> Path:
    return project_root / "_grimoire-runtime-output" / "hook-runtime" / "source-observability"


def record_source_observability(project_root: Path, subagent_state: dict[str, Any]) -> None:
    source_report = subagent_state.get("sourceReport") if isinstance(subagent_state.get("sourceReport"), dict) else {}
    if not source_report:
        return

    trust_payload = subagent_state.get("trust") if isinstance(subagent_state.get("trust"), dict) else {}
    payload = {
        "timestamp": str(subagent_state.get("timestamp") or timestamp_now()),
        "agent": str(subagent_state.get("agent") or ""),
        "task": compact_text(str(subagent_state.get("task") or ""), 220),
        "grade": str(subagent_state.get("grade") or ""),
        "trustLevel": str(trust_payload.get("level") or ""),
        "sourceReport": source_report,
    }
    observability_dir = source_observability_dir(project_root)
    save_json_mapping(observability_dir / "latest.json", payload)
    append_jsonl(observability_dir / "events.jsonl", payload)


def load_recent_source_reports(project_root: Path, limit: int = 4) -> list[dict[str, Any]]:
    entries = load_recent_jsonl_entries(source_observability_dir(project_root) / "events.jsonl", max(4, limit * 4))
    reports: list[dict[str, Any]] = []
    seen_agents: set[str] = set()
    for entry in reversed(entries):
        source_report = entry.get("sourceReport") if isinstance(entry.get("sourceReport"), dict) else {}
        agent_name = str(entry.get("agent") or source_report.get("agent") or "")
        if not agent_name or agent_name in seen_agents or not source_report:
            continue
        seen_agents.add(agent_name)
        reports.append(source_report)
        if len(reports) >= limit:
            break
    reports.reverse()
    return reports


def format_source_observability_summary(source_reports: list[dict[str, Any]]) -> str:
    if not source_reports:
        return ""

    segments: list[str] = []
    for report in source_reports[:4]:
        agent_name = compact_text(str(report.get("agent") or "subagent"), 40)
        coverage_pct = int(report.get("coveragePct", 0) or 0)
        sources = [item for item in list(report.get("sources", []) or []) if isinstance(item, dict)]
        sources_preview = ", ".join(
            f"{compact_text(str(item.get('label') or ''), 80)} {int(item.get('supportPct', 0) or 0)}%"
            for item in sources[:3]
        )
        if sources_preview:
            segments.append(f"{agent_name} [{coverage_pct}%] {sources_preview}")

    if not segments:
        return ""
    return "Rapport sources par agent: " + " | ".join(segments)


def parse_prompt_state_signals(prompt_state: dict[str, Any]) -> tuple[tuple[str, ...], bool, bool]:
    if not isinstance(prompt_state, dict):
        return (), False, False

    signals = prompt_state.get("signals", {})
    if not isinstance(signals, dict):
        return (), False, False

    raw_task_items = signals.get("taskItems", [])
    task_items = tuple(
        str(item).strip() for item in raw_task_items if isinstance(item, str) and str(item).strip()
    )[:5]
    brainstorm_recommended = bool(signals.get("brainstormRecommended"))
    autonomous_execution = bool(signals.get("autonomousExecutionPreferred"))
    return task_items, brainstorm_recommended, autonomous_execution


def extract_task_type_from_prompt_state(prompt_state: dict[str, Any]) -> str:
    if not isinstance(prompt_state, dict):
        return ""
    memory_hints = prompt_state.get("memoryHints", {})
    if isinstance(memory_hints, dict):
        task_type = str(memory_hints.get("taskType") or "").strip()
        if task_type:
            return task_type
    routing = prompt_state.get("routing", {})
    if not isinstance(routing, dict):
        return ""
    suggested_agent = str(routing.get("suggestedAgent") or "").strip()
    prompt_preview = str(prompt_state.get("promptPreview") or "")
    tags = tuple(str(tag) for tag in prompt_state.get("tags", []) if isinstance(tag, str))
    return derive_task_type(prompt_preview, suggested_agent, tags)


def looks_like_text_payload(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 12:
        return False
    if stripped.startswith(("/", "./", "../")) and "\n" not in stripped:
        return False
    return not (re.fullmatch(r"[A-Za-z0-9_./:-]+", stripped) and " " not in stripped)


def collect_payload_texts(value: Any, preferred_keys: frozenset[str], fallback_patterns: tuple[str, ...]) -> list[tuple[int, str]]:
    collected: list[tuple[int, str]] = []

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child_value in node.items():
                walk(child_value, str(child_key))
            return
        if isinstance(node, list):
            for item in node:
                walk(item, key)
            return
        if not isinstance(node, str):
            return

        text = node.strip()
        if not looks_like_text_payload(text):
            return

        key_lower = key.lower().replace("_", "")
        if key_lower in preferred_keys:
            priority = 0
        elif any(pattern in key_lower for pattern in fallback_patterns):
            priority = 1
        else:
            return
        collected.append((priority, compact_text(text, 12000)))

    walk(value)
    collected.sort(key=lambda item: (item[0], -len(item[1])))
    return collected


def extract_payload_text(payload: dict[str, Any], preferred_keys: frozenset[str], fallback_patterns: tuple[str, ...]) -> str:
    matches = collect_payload_texts(payload, preferred_keys, fallback_patterns)
    return matches[0][1] if matches else ""


def detect_payload_failure(payload: dict[str, Any]) -> bool:
    raw_status = str(payload.get("status") or payload.get("outcome") or "").lower()
    if raw_status in {"failed", "failure", "error", "cancelled", "timeout"}:
        return True
    if payload.get("success") is False or payload.get("ok") is False:
        return True
    error_value = payload.get("error")
    return isinstance(error_value, str) and bool(error_value.strip())


def update_repetition_counter(counter_file: Path, bucket: str, signature: str) -> dict[str, Any]:
    payload = load_json_mapping(counter_file)
    buckets = payload.setdefault("buckets", {})
    assert isinstance(buckets, dict)
    state = buckets.setdefault(bucket, {})
    assert isinstance(state, dict)
    entry = state.setdefault(signature, {"count": 0, "learningLogged": False, "failureLogged": False})
    assert isinstance(entry, dict)
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["lastSeen"] = timestamp_now()
    save_json_mapping(counter_file, payload)
    return entry


def maybe_record_learning(project_root: Path, counter_file: Path, agent_name: str, task_type: str, task: str, *, min_count: int = 2) -> str:
    learnings_cls = load_core_symbol(project_root, "grimoire.tools.learnings", "Learnings")
    if learnings_cls is None:
        return ""

    signature = make_signature(agent_name, task_type, task)
    counter = update_repetition_counter(counter_file, "success", signature)
    if int(counter.get("count", 0)) < min_count or bool(counter.get("learningLogged")):
        return ""

    learning = learnings_cls(project_root)
    learning_key = f"hook-{task_type}-{agent_name}-{signature}"
    insight = compact_text(
        f"Pattern confirme via SubagentStop pour {task_type}: {task or 'sortie attribuable et validee localement'}",
        180,
    )
    learning.log(
        learning_key,
        insight,
        confidence=85,
        source="observed",
        skill=agent_name,
        tags=("hook", "subagent-stop", task_type, agent_name),
    )

    procedural_module = load_tool_module("procedural-memory.py", "procedural_memory")
    if procedural_module is not None and hasattr(procedural_module, "record_pattern"):
        with contextlib.suppress(Exception):
            procedural_module.record_pattern(
                project_root,
                task_type,
                compact_text(task or insight, 180),
                [agent_name, "hook", "subagent-stop"],
                source="hook-subagent-stop",
            )

    counter_payload = load_json_mapping(counter_file)
    success_bucket = counter_payload.setdefault("buckets", {}).setdefault("success", {})
    assert isinstance(success_bucket, dict)
    success_entry = success_bucket.setdefault(signature, counter)
    assert isinstance(success_entry, dict)
    success_entry["learningLogged"] = True
    success_entry["learningKey"] = learning_key
    save_json_mapping(counter_file, counter_payload)
    return learning_key


def maybe_record_failure(project_root: Path, counter_file: Path, agent_name: str, task_type: str, task: str) -> str:
    museum_module = load_tool_module("failure-museum.py", "failure_museum")
    if museum_module is None:
        return ""

    signature = make_signature(agent_name, task_type, task)
    counter = update_repetition_counter(counter_file, "failure", signature)
    if int(counter.get("count", 0)) < 2 or bool(counter.get("failureLogged")):
        return ""

    load_failures = getattr(museum_module, "load_failures", None)
    next_failure_id = getattr(museum_module, "next_failure_id", None)
    save_failure = getattr(museum_module, "save_failure", None)
    sync_markdown = getattr(museum_module, "sync_markdown", None)
    failure_cls = getattr(museum_module, "Failure", None)
    if not all((load_failures, next_failure_id, save_failure, sync_markdown, failure_cls)):
        return ""

    existing = load_failures(project_root)
    title = f"SubagentStop {agent_name} {task_type}"
    for entry in existing:
        if getattr(entry, "title", "") == title:
            return getattr(entry, "failure_id", "") or title

    failure_id, sequence = next_failure_id(existing)
    failure = failure_cls(
        failure_id=failure_id,
        sequence=sequence,
        timestamp=timestamp_now(),
        title=title,
        severity="medium",
        agents=[agent_name],
        description=compact_text(f"Echec repete detecte par SubagentStop sur {task_type}: {task}", 220),
        root_cause="Sortie subagent repetitivement insuffisante ou desalignee selon le scoring hook.",
        fix="Renforcer le cadrage, demander des preuves locales et enclencher une cross-validation ciblee.",
        rule_added="Si un SubagentStop sort en D/F ou trust untrusted de facon repetee, ne pas agreger sans CVTL.",
        tags=[task_type, agent_name, "subagent-stop"],
        status="resolved",
    )
    save_failure(project_root, failure)
    sync_markdown(project_root)

    counter_payload = load_json_mapping(counter_file)
    failure_bucket = counter_payload.setdefault("buckets", {}).setdefault("failure", {})
    assert isinstance(failure_bucket, dict)
    failure_entry = failure_bucket.setdefault(signature, counter)
    assert isinstance(failure_entry, dict)
    failure_entry["failureLogged"] = True
    failure_entry["failureId"] = failure_id
    save_json_mapping(counter_file, counter_payload)
    return failure_id


def evaluate_subagent_stop(
    raw: str,
    prompt_state: dict[str, Any],
    project_root: Path,
    latest_file: Path,
    events_file: Path,
    counter_file: Path,
) -> dict[str, Any]:
    payload = load_payload(raw)
    if payload is None:
        return {}

    event_name = str(payload.get("hookEventName") or payload.get("hook_event_name") or "SubagentStop")
    if event_name != "SubagentStop":
        return {}

    previous_state = load_prompt_state(latest_file)

    agent_name = str(payload.get("agent_type") or payload.get("agentName") or payload.get("agent_id") or "subagent")
    output_text = extract_payload_text(payload, MODULE_TEXT_KEYS, ("output", "result", "summary", "message", "content"))
    task_text = extract_payload_text(payload, MODULE_TASK_KEYS, ("task", "goal", "objective", "prompt", "query"))
    if not task_text:
        task_text = compact_text(str(payload.get("task") or prompt_state.get("promptPreview") or ""), 240)

    explicit_failure = detect_payload_failure(payload)
    task_type = extract_task_type_from_prompt_state(prompt_state) or derive_task_type(task_text, agent_name, ())

    state: dict[str, Any] = {
        "event": event_name,
        "agent": agent_name,
        "task": task_text,
        "taskType": task_type,
        "outputPreview": compact_text(output_text, 240),
        "explicitFailure": explicit_failure,
        "timestamp": timestamp_now(),
    }

    if not output_text and not explicit_failure:
        latest_file.parent.mkdir(parents=True, exist_ok=True)
        events_file.parent.mkdir(parents=True, exist_ok=True)
        latest_file.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        append_jsonl(events_file, state)
        return {}

    notes: list[str] = []
    flags: list[str] = []
    passed = not explicit_failure
    grade = "F" if explicit_failure else ""
    score = 0.0

    evaluator_cls = load_core_symbol(project_root, "grimoire.core.evaluator", "Evaluator")
    criteria_cls = load_core_symbol(project_root, "grimoire.core.evaluator", "EvalCriteria")
    if output_text and evaluator_cls is not None and criteria_cls is not None:
        try:
            evaluator = evaluator_cls(project_root)
            criteria = criteria_cls(check_tests=should_check_tests(agent_name, task_text), check_relevance=bool(task_text))
            result = evaluator.evaluate(agent=agent_name, output=output_text, task=task_text, criteria=criteria)
            score = float(result.score)
            grade = str(result.grade)
            passed = bool(result.passed)
            state["evaluation"] = result.to_dict()
            if grade in {"D", "F"}:
                flags.append("quality-red")
            elif grade == "C":
                flags.append("quality-yellow")
        except Exception:
            state["evaluation"] = {"grade": grade or "unknown", "score": score}

    telemetry_cls = load_core_symbol(project_root, "grimoire.core.telemetry", "Telemetry")
    if telemetry_cls is not None:
        try:
            telemetry = telemetry_cls(project_root)
            telemetry.record_skill(
                agent_name,
                outcome="success" if passed else "failure",
                message=compact_text(f"SubagentStop grade={grade or 'n/a'} task={task_text}", 180),
                metadata={"source": "vscode-hook", "taskType": task_type, "grade": grade or "n/a"},
            )
        except Exception:
            pass

    trust_cls = load_core_symbol(project_root, "grimoire.core.trust_scorer", "TrustScorer")
    trust_payload: dict[str, Any] = {}
    if trust_cls is not None:
        try:
            trust_score = trust_cls(project_root).score(agent_name)
            trust_payload = trust_score.to_dict()
            state["trust"] = trust_payload
            trust_level = str(trust_payload.get("level") or "")
            if trust_level == "untrusted":
                flags.append("trust-red")
            elif trust_level == "cautious":
                flags.append("trust-yellow")
        except Exception:
            trust_payload = {}

    conflict = detect_subagent_conflict(previous_state, state)
    if conflict:
        flags.append("conflict-red")
        notes.append(
            "Conflit inter-subagents detecte sur la meme tache: "
            f"{conflict['previousAgent']}:{conflict['previousGrade']} vs {conflict['currentAgent']}:{conflict['currentGrade']}. "
            f"Declencher {conflict['recommendedAction']} avant synthese."
        )
        state["conflict"] = conflict

    if passed and grade in {"A", "B", "C"} and task_text:
        # Grades A/B: log immediately (first occurrence). Grade C: require confirmation (count >= 2).
        min_count = 1 if grade in {"A", "B"} else 2
        learning_key = maybe_record_learning(project_root, counter_file, agent_name, task_type, task_text, min_count=min_count)
        if learning_key:
            notes.append(f"Learning auto enregistre: {learning_key}.")
    elif not passed or grade in {"D", "F"}:
        failure_id = maybe_record_failure(project_root, counter_file, agent_name, task_type, task_text or output_text)
        if failure_id:
            notes.append(f"Failure Museum enrichi: {failure_id}.")

    if grade:
        notes.append(f"Evaluation subagent: {agent_name} grade {grade} ({score:.2f}).")
    if trust_payload:
        notes.append(
            f"Confiance historique: {trust_payload.get('level', 'unknown')} ({float(trust_payload.get('score', 0.0)):.2f})."
        )

    source_report = build_source_report(agent_name, task_text, output_text, prompt_state)
    if source_report:
        state["sourceReport"] = source_report
        notes.append(format_source_report_note(source_report))

    if "quality-red" in flags or "trust-red" in flags:
        notes.append("Ne pas agreger tel quel: exiger des preuves locales ou declencher une CVTL ciblee.")
    elif "quality-yellow" in flags or "trust-yellow" in flags:
        notes.append("Aggregation prudente: conserver l'attribution et verifier les points critiques avant synthese.")

    if task_text and any(term in task_text.lower() for term in TEST_BREAKER_TERMS) and passed:
        notes.append(
            "Breaker post-tests recommande: review adversariale ciblee, edge-case hunt, retests, quick-check puis preflight avant conclusion."
        )

    state["passed"] = passed
    state["grade"] = grade
    state["score"] = round(score, 3)
    state["flags"] = flags
    state["followUp"] = {
        "breakerRecommended": bool(task_text and any(term in task_text.lower() for term in TEST_BREAKER_TERMS) and passed),
        "challengeRecommended": bool(conflict) or "trust-red" in flags or "quality-red" in flags,
    }
    deferred_tickets = sync_deferred_tickets(project_root, state)
    if deferred_tickets:
        notes.append(
            "Tickets differes ouverts: " + "; ".join(str(ticket.get("id") or "") for ticket in deferred_tickets)
        )
    state["notes"] = list(dedupe_preserve_order(notes))

    persist_session_state(
        session_state_file_from_hook_path(latest_file),
        prompt_state,
        {},
        state,
        "subagent-stop",
    )

    latest_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.parent.mkdir(parents=True, exist_ok=True)
    latest_file.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    append_jsonl(events_file, state)
    record_source_observability(project_root, state)

    additional_context = " ".join(state["notes"])[:1100]
    if not additional_context:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStop",
            "additionalContext": additional_context,
        }
    }


def workflow_recap(project_root: Path) -> str:
    analyzer_cls = load_core_symbol(project_root, "grimoire.core.workflow_analyzer", "WorkflowAnalyzer")
    if analyzer_cls is None:
        return ""
    try:
        report = analyzer_cls(project_root).analyze()
    except Exception:
        return ""
    if not report.total_events:
        return ""

    parts = [f"{report.total_events} evenements, {report.unique_skills} skills observes"]
    if report.top_failures:
        name, count = report.top_failures[0]
        parts.append(f"top failure {name} x{count}")
    if report.recommendations:
        parts.append(compact_text(str(report.recommendations[0].message), 160))
    return "Workflow recap: " + " ; ".join(parts)


def weak_signals(prompt_state: dict[str, Any], task_state: dict[str, Any], subagent_state: dict[str, Any]) -> tuple[str, ...]:
    signals: list[str] = []
    task_status = str(task_state.get("status") or "").lower()
    if task_state.get("task") and task_status and task_status not in {"success", "completed", "ok"}:
        signals.append(f"dernier task-flow en anomalie ({task_state.get('task')} => {task_status})")

    grade = str(subagent_state.get("grade") or "")
    trust = subagent_state.get("trust") if isinstance(subagent_state.get("trust"), dict) else {}
    trust_level = str(trust.get("level") or "") if isinstance(trust, dict) else ""
    if grade in {"D", "F"}:
        signals.append(f"derniere evaluation subagent en {grade}")
    if trust_level == "untrusted":
        signals.append("trust scorer en zone rouge")

    tags = prompt_state.get("tags", []) if isinstance(prompt_state, dict) else []
    if isinstance(tags, list) and "task-list" in tags and not subagent_state:
        signals.append("checklist detectee sans preuve de cloture subagent")

    return dedupe_preserve_order(signals)


def build_session_start_context(config: dict[str, str], shared_summary: str) -> str:
    parts: list[str] = []
    if config.get("user_name"):
        parts.append(f"Utilisateur courant: {config['user_name']}.")
    if config.get("communication_language"):
        parts.append(f"Langue de travail: {config['communication_language']}.")
    parts.append("Projet: Grimoire.")
    parts.append(
        "Les hooks natifs couvrent le cycle agent; tasks.json reste orchestre via .github/hooks/scripts/grimoire-task-flow.sh."
    )
    if shared_summary:
        parts.append(f"Shared context actif: {shared_summary[:220]}")
    return " ".join(part.strip() for part in parts if part.strip()).strip()


def read_simple_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = raw_line.split(":", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def first_shared_context_summary(path: Path) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(">"):
            continue
        return re.sub(r"\s+", " ", line)
    return ""


def evaluate_session_start(config_file: Path, shared_context_file: Path, latest_file: Path) -> dict[str, Any]:
    config = read_simple_yaml(config_file)
    shared_summary = first_shared_context_summary(shared_context_file)
    additional_context = build_session_start_context(config, shared_summary)

    latest_payload = {
        "user": config.get("user_name", ""),
        "language": config.get("communication_language", ""),
        "sharedContextSummary": shared_summary[:220],
        "additionalContext": additional_context,
    }
    latest_file.parent.mkdir(parents=True, exist_ok=True)
    latest_file.write_text(json.dumps(latest_payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    if not additional_context:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }


def _fetch_agent_learnings(project_root: Path | None, agent_name: str, task_type: str) -> str:
    """Return a compact summary of the top learnings for this agent/task, or empty string."""
    if project_root is None:
        return ""
    try:
        learnings_cls = load_core_symbol(project_root, "grimoire.tools.learnings", "Learnings")
        if learnings_cls is None:
            return ""
        learnings = learnings_cls(project_root)
        if learnings.count() == 0:
            return ""
        query = f"{agent_name} {task_type}".strip()
        entries = learnings.search(query, limit=2) if query else learnings.top(limit=2)
        if not entries:
            return ""
        snippets = " | ".join(compact_text(str(e.insight), 90) for e in entries)
        return f"Learnings memorises ({len(entries)}): {snippets}."
    except Exception:
        return ""


def build_subagent_context(payload: dict[str, Any], prompt_state: dict[str, Any], project_root: Path | None = None) -> str:
    """Build a compact subagent context capsule.

    Static rules (language, escalation, routing, agent conventions) live in
    grimoire-master.md which is already loaded in every sub-agent's context.
    Only session-specific signals are injected here to avoid redundant tokens.
    """
    agent_name = str(payload.get("agent_type") or payload.get("agentName") or payload.get("agent_id") or "subagent")
    if not isinstance(prompt_state, dict):
        prompt_state = {}

    prompt_preview = str(prompt_state.get("promptPreview") or "")
    constraints = prompt_state.get("constraints") or {}
    tags = prompt_state.get("tags") or []
    signals = prompt_state.get("signals") or {}
    clarification = prompt_state.get("clarification") or {}
    clarification_state = prompt_state.get("clarificationState") or {}
    challenge_mode = prompt_state.get("challengeMode") or {}
    debate_mode = prompt_state.get("debateMode") or {}
    design_authority = prompt_state.get("designAuthority") or {}
    external_references = prompt_state.get("externalReferences") or {}
    external_reference_state = prompt_state.get("externalReferenceState") or {}
    proposal_challenge = prompt_state.get("proposalChallenge") or {}
    dispatch_contract = prompt_state.get("dispatchContract") or {}
    token_budget = prompt_state.get("tokenBudget") or {}

    parts: list[str] = []

    # Session objective -- primary orientation signal for the sub-agent
    if prompt_preview:
        parts.append(f"Obj: {compact_text(prompt_preview, 90)}")

    # Unresolved clarification that needs a follow-through relance
    if str(clarification_state.get("status") or "") == "resolved":
        selected = compact_text(str(clarification_state.get("selectedOption") or ""), 90)
        parts.append(f"Clarification resolue: {selected}.")
    elif clarification.get("recommended") or str(clarification_state.get("status") or "") == "needs-relance":
        q = compact_text(str(clarification_state.get("question") or ""), 70)
        if not q:
            q = compact_text(str(clarification.get("question") or ""), 90)
        parts.append(f"Clarification non resolue: {q}.")

    task_items = [compact_text(str(item), 80) for item in (signals.get("taskItems") or []) if str(item).strip()]
    if task_items:
        parts.append(
            "Checklist prioritaire detectee: "
            f"{'; '.join(task_items[:3])}. Maintenir living checklist ou todo list."
        )

    # Active session mode overrides (not in agent definition)
    if constraints.get("planOnly"):
        parts.append("Plan-only.")
    if constraints.get("safetyFocus"):
        parts.append("Mode prudent.")
    if signals.get("brainstormRecommended") or "brainstorm" in tags:
        parts.append("Signal brainstorm: explorer options avant verrouillage si ambigu.")
    if signals.get("autonomousExecutionPreferred") or "autonomous" in tags:
        parts.append("Execution autonome attendue: continuer les suites L1/L2 jusqu'a preuve ou blocage.")
    if challenge_mode.get("requested"):
        parts.append("Mode challenge actif: critiquer les angles morts avant execution risquee.")
    if debate_mode.get("recommended"):
        parts.append("Mode debat recommande: comparer alternatives utiles avant choix durable.")
    if design_authority.get("found"):
        scope = compact_text(str(design_authority.get("scope") or "global"), 50)
        sources = ", ".join(str(source) for source in design_authority.get("sources", [])[:2])
        parts.append(f"DA projet a appliquer: {scope} {compact_text(sources, 120)}.")
    if external_reference_state.get("status") == "context7-proved":
        library_id = compact_text(str(external_reference_state.get("libraryId") or ""), 80)
        parts.append(f"Preuve externe capturee: Context7 {library_id}.")
    elif external_references.get("recommended"):
        libs = ", ".join(str(lib) for lib in external_references.get("libraries", [])[:3])
        parts.append(f"Context7 requis avant web pour references externes: {compact_text(libs, 100)}.")
    if proposal_challenge.get("recommended"):
        challenge = str(proposal_challenge.get("instruction") or proposal_challenge.get("summary") or "")
        parts.append(f"Challenge utilisateur requis: {compact_text(challenge, 180)}.")
    if "project-protector" in tags:
        parts.append("Protecteur projet: verifier objectif, plus-value, reversibilite et preuve.")
    if dispatch_contract.get("required"):
        sections = ", ".join(str(section) for section in dispatch_contract.get("sections", [])[:3])
        parts.append(f"Contrat de dispatch requis: {compact_text(sections, 150)}.")
    if "interactive-clarification" in tags:
        parts.append("Clarification interactive souhaitee: batcher les questions et eviter les relances isolees.")

    # Token pressure -- agent must keep output compact
    if str(token_budget.get("level") or "") in {"warning", "critical", "emergency"}:
        pct = round(float(token_budget.get("usagePct", 0.0) or 0.0) * 100)
        parts.append(f"Budget token {token_budget.get('level')} {pct}%.")

    # Agent-specific learnings from session memory (session-relevant, not static)
    task_type = extract_task_type_from_prompt_state(prompt_state) or derive_task_type(prompt_preview, agent_name, ())
    learning_snippet = _fetch_agent_learnings(project_root, agent_name, task_type)
    if learning_snippet:
        parts.append(compact_text(learning_snippet, 60))

    return " ".join(parts)[:1200]



def evaluate_subagent_context(raw: str, prompt_state: dict[str, Any], project_root: Path | None = None) -> dict[str, Any]:
    payload = load_payload(raw)
    if payload is None:
        return {}

    additional_context = build_subagent_context(payload, prompt_state, project_root=project_root)
    if not additional_context:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": additional_context,
        }
    }


def evaluate_precompact(
    raw: str,
    prompt_state: dict[str, Any],
    task_state: dict[str, Any],
    trace_state: dict[str, Any],
    subagent_state: dict[str, Any],
    project_root: Path,
    latest_file: Path,
    events_file: Path,
) -> dict[str, Any]:
    payload = load_payload(raw)
    if payload is None:
        return {}

    prompt_preview = prompt_state.get("promptPreview", "") if isinstance(prompt_state, dict) else ""
    constraints = prompt_state.get("constraints", {}) if isinstance(prompt_state, dict) else {}
    tags = prompt_state.get("tags", []) if isinstance(prompt_state, dict) else []
    prompt_signals = prompt_state.get("signals", {}) if isinstance(prompt_state, dict) else {}
    clarification = prompt_state.get("clarification", {}) if isinstance(prompt_state, dict) else {}
    clarification_state = prompt_state.get("clarificationState", {}) if isinstance(prompt_state, dict) else {}
    challenge_mode = prompt_state.get("challengeMode", {}) if isinstance(prompt_state, dict) else {}
    debate_mode = prompt_state.get("debateMode", {}) if isinstance(prompt_state, dict) else {}
    design_authority = prompt_state.get("designAuthority", {}) if isinstance(prompt_state, dict) else {}
    token_budget = prompt_state.get("tokenBudget", {}) if isinstance(prompt_state, dict) else {}
    external_references = prompt_state.get("externalReferences", {}) if isinstance(prompt_state, dict) else {}
    external_reference_state = prompt_state.get("externalReferenceState", {}) if isinstance(prompt_state, dict) else {}
    task_items, brainstorm_recommended, autonomous_execution = parse_prompt_state_signals(prompt_state)
    trigger = str(payload.get("summary_source") or payload.get("trigger") or "auto")
    transcript_path = str(payload.get("transcript_path") or payload.get("transcriptPath") or "")

    parts: list[str] = []
    if prompt_preview:
        parts.append(f"Objectif recent: {prompt_preview}")
    if task_items:
        parts.append("Checklist detectee: " + "; ".join(task_items[:3]))
    if brainstorm_recommended:
        parts.append("Brainstorm recommande avant edition")
    if autonomous_execution:
        parts.append("Execution autonome preferee")
    clarification_status = str(clarification_state.get("status") or "")
    if clarification_status in {"resolved", "auto-resolved"}:
        parts.append("Clarification resolue")
    elif clarification_status == "needs-relance":
        parts.append("Clarification a relancer une fois")
    elif isinstance(clarification, dict) and clarification.get("recommended"):
        parts.append("Clarification batch recommandee")
    if isinstance(challenge_mode, dict) and challenge_mode.get("requested"):
        parts.append("Challenge Mode actif")
    if isinstance(debate_mode, dict) and debate_mode.get("recommended"):
        parts.append("Debat ou Party Mode recommande si les avis divergent")
    if constraints.get("planOnly"):
        parts.append("Contrainte active: plan uniquement")
    if constraints.get("safetyFocus"):
        parts.append("Mode prudent actif")
    if tags:
        parts.append("Tags actifs: " + ", ".join(str(tag) for tag in tags[:5]))
    if isinstance(design_authority, dict) and design_authority.get("found"):
        parts.append("DA projet activee")
    elif isinstance(design_authority, dict) and design_authority.get("fallback"):
        parts.append("Fallback DA standard active")
    if isinstance(external_references, dict) and external_references.get("recommended"):
        libraries = ", ".join(str(item) for item in list(external_references.get("libraries", []))[:3])
        suffix = f" ({libraries})" if libraries else ""
        parts.append(f"Reference Context7 recommandee{suffix}")
    if bool(external_reference_state.get("satisfied")):
        parts.append("Preuve externe capturee")
    elif isinstance(external_references, dict) and external_references.get("recommended"):
        parts.append("Preuve externe requise")
    if not token_budget:
        token_budget = assess_token_budget(project_root, force=True)
    if token_budget:
        parts.append(format_token_budget_note(token_budget))
    if task_state.get("task"):
        parts.append(f"Dernier flow task: {task_state.get('task')} [{task_state.get('status', 'unknown')}]")
    if trace_state.get("event") and trace_state.get("agent"):
        parts.append(f"Dernier evenement subagent: {trace_state.get('event')} -> {trace_state.get('agent')}")
    if subagent_state.get("agent") and subagent_state.get("grade"):
        trust_level = ""
        trust_payload = subagent_state.get("trust")
        if isinstance(trust_payload, dict):
            trust_level = str(trust_payload.get("level") or "")
        summary = f"Derniere evaluation subagent: {subagent_state.get('agent')} {subagent_state.get('grade')}"
        if trust_level:
            summary += f" / trust {trust_level}"
        parts.append(summary)

    workflow_summary = workflow_recap(project_root)
    if workflow_summary:
        parts.append(workflow_summary)

    weak_signal_items = weak_signals(prompt_state, task_state, subagent_state)
    if weak_signal_items:
        parts.append("Signaux faibles: " + "; ".join(weak_signal_items[:3]))

    prompt_state_for_session = dict(prompt_state) if isinstance(prompt_state, dict) else {}
    if token_budget and not prompt_state_for_session.get("tokenBudget"):
        prompt_state_for_session["tokenBudget"] = token_budget
    session_state = persist_session_state(
        session_state_file_from_hook_path(latest_file),
        prompt_state_for_session,
        task_state,
        subagent_state,
        "pre-compact",
    )
    if session_state.get("summary"):
        parts.append(str(session_state["summary"]))
    if session_state.get("evidenceSummary"):
        parts.append(str(session_state["evidenceSummary"]))

    capsule = "Pre-compact Grimoire: " + " | ".join(parts) if parts else ""
    state = {
        "trigger": trigger,
        "transcriptPath": transcript_path,
        "capsule": capsule,
        "promptPreview": prompt_preview,
        "signals": prompt_signals if isinstance(prompt_signals, dict) else {},
        "clarification": clarification if isinstance(clarification, dict) else {},
        "challengeMode": challenge_mode if isinstance(challenge_mode, dict) else {},
        "debateMode": debate_mode if isinstance(debate_mode, dict) else {},
        "designAuthority": design_authority if isinstance(design_authority, dict) else {},
        "externalReferences": external_references if isinstance(external_references, dict) else {},
        "externalReferenceState": external_reference_state if isinstance(external_reference_state, dict) else {},
        "tokenBudget": token_budget,
        "task": task_state.get("task", ""),
        "taskStatus": task_state.get("status", ""),
        "lastSubagentEvent": trace_state,
        "lastSubagentReview": subagent_state,
        "workflowRecap": workflow_summary,
        "weakSignals": list(weak_signal_items),
        "sessionState": session_state,
    }

    latest_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.parent.mkdir(parents=True, exist_ok=True)
    latest_file.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    with events_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(state, ensure_ascii=True) + "\n")

    if not capsule:
        return {}

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": capsule[:1200],
        }
    }


def evaluate_control_surface(raw: str, prompt_state: dict[str, Any]) -> dict[str, Any]:
    payload = load_payload(raw)
    if payload is None:
        return {}

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    raw_lower = raw.lower()

    is_write_tool = detect_write_tool(tool_name, raw_lower)
    is_terminal_tool = detect_terminal_tool(tool_name, raw_lower)
    constraints = prompt_state.get("constraints", {}) if isinstance(prompt_state, dict) else {}
    plan_only = bool(constraints.get("planOnly"))

    if plan_only and is_write_tool:
        return make_permission_payload(
            "PreToolUse",
            "deny",
            "Le dernier prompt demande explicitement une phase analyse/plan sans modification de fichiers.",
            "Contrainte active: plan uniquement. Attendre une demande explicite d'implementation avant toute ecriture.",
        )

    if is_terminal_tool:
        for pattern, reason in DENY_PATTERNS:
            if re.search(pattern, raw_lower):
                return make_permission_payload(
                    "PreToolUse",
                    "deny",
                    reason,
                    "Le repo interdit les operations destructives non confirmees sur le shell. Utiliser une alternative sure et reversible.",
                )

        if re.search(r"\brm\s+-rf\b", raw_lower) or re.search(r"\bsudo\b", raw_lower):
            return make_permission_payload(
                "PreToolUse",
                "ask",
                "Commande shell potentiellement destructive ou privilegiee detectee. Confirmer ?",
                "Verifier que la commande est strictement necessaire, cible un chemin borne et reste reversible.",
            )

    if is_write_tool:
        for marker, label in PROTECTED_SURFACES:
            if marker.lower() in raw_lower:
                return make_permission_payload(
                    "PreToolUse",
                    "ask",
                    f"Modification d'une surface de controle agentique ({label}). Confirmer ?",
                    "Surface de controle protegee: garder le changement additif, verifier la syntaxe locale et eviter d'elargir le scope de l'automatisation.",
                )

    return {}


def evaluate_memory_guard(raw: str) -> dict[str, Any]:
    if "_grimoire-runtime/_memory/" not in raw:
        return {}

    payload = load_payload(raw)
    if payload is None:
        return {}

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if not detect_write_tool(tool_name, raw.lower()):
        return {}

    return make_permission_payload(
        "PreToolUse",
        "ask",
        "Modification d'une surface memoire Grimoire (_grimoire-runtime/_memory/). Confirmer ?",
        "Surface memoire protegee: garder la modification minimale et justifier explicitement le besoin d'ecriture.",
    )


def maybe_add_candidate_path(candidate_paths: set[Path], project_root: Path, value: str, key: str | None = None) -> None:
    text = value.strip()
    if not text or text.startswith("file://"):
        return
    key_lower = (key or "").lower()
    if key_lower not in ALLOWED_PATH_KEYS and not text.endswith((".py", ".sh", ".json", ".md", ".yaml", ".yml")):
        return
    path = Path(text)
    if not path.is_absolute():
        path = project_root / path
    try:
        resolved = path.resolve()
    except OSError:
        return
    try:
        resolved.relative_to(project_root)
    except ValueError:
        return
    if resolved.exists() and resolved.is_file():
        candidate_paths.add(resolved)


def walk_tool_input_for_paths(candidate_paths: set[Path], project_root: Path, value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            walk_tool_input_for_paths(candidate_paths, project_root, child_value, child_key)
        return
    if isinstance(value, list):
        for item in value:
            walk_tool_input_for_paths(candidate_paths, project_root, item, key)
        return
    if isinstance(value, str):
        maybe_add_candidate_path(candidate_paths, project_root, value, key)


def relpath(project_root: Path, path: Path) -> str:
    return str(path.relative_to(project_root))


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, cwd=cwd, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return 1, f"commande timeout apres 30s: {' '.join(command[:3])}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip()


def validate_agent_entrypoint(relative: str, candidate: Path, parsed: dict[str, Any]) -> str | None:
    if not relative.startswith(".github/agents/") or not relative.endswith(".agent.md"):
        return None

    user_invocable = parsed.get("user-invocable")
    if candidate.name == "grimoire-master.agent.md":
        if user_invocable is not True:
            return f"{relative}: grimoire-master doit conserver user-invocable: true"
        return None

    if user_invocable is not False:
        return (
            f"{relative}: bypass interdit, seul .github/agents/grimoire-master.agent.md "
            "peut etre user-invocable"
        )

    return None


def split_frontmatter_body(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---", 4)
    if end == -1:
        return content[4:], ""
    return content[4:end], content[end + 4 :].lstrip("\n")


def prompt_basename(relative: str) -> str:
    return Path(relative).name[: -len(".prompt.md")]


def collect_agent_basenames(project_root: Path) -> set[str]:
    agents_dir = project_root / ".github" / "agents"
    if not agents_dir.exists():
        return set()
    return {path.name[: -len(".agent.md")] for path in agents_dir.glob("*.agent.md")}


def collect_skill_basenames(project_root: Path) -> set[str]:
    skills_dir = project_root / ".github" / "skills"
    if not skills_dir.exists():
        return set()
    return {path.parent.name for path in skills_dir.glob("**/SKILL.md")}


def extract_markdown_content_lines(body: str) -> list[str]:
    lines: list[str] = []
    in_fence = False

    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        lines.append(stripped)

    return lines


def is_thin_wrapper_prompt(body: str) -> bool:
    if not PROMPT_THIN_WRAPPER_REFERENCE_PATTERN.search(body):
        return False

    lines = extract_markdown_content_lines(body)
    if not lines or len(lines) > 9:
        return False
    if any(line.startswith("#") for line in lines):
        return False
    if body.count("{{") >= 2:
        return False

    numbered_lines = sum(1 for line in lines if re.match(r"^\d+\.\s", line))
    action_lines = sum(1 for line in lines if PROMPT_THIN_WRAPPER_ACTION_PATTERN.search(line))
    signature_hits = sum(1 for signature in PROMPT_THIN_WRAPPER_SIGNATURES if signature in body.lower())
    mostly_numbered = numbered_lines >= max(1, len(lines) - 1)
    mostly_actionable = action_lines >= max(1, len(lines) - 1)

    return mostly_numbered and (signature_hits >= 1 or mostly_actionable)


def validate_prompt_entrypoint(relative: str, candidate: Path, project_root: Path, content: str) -> str | None:
    if not relative.startswith(".github/prompts/") or not relative.endswith(".prompt.md"):
        return None

    basename = prompt_basename(relative)
    if basename in collect_agent_basenames(project_root):
        return (
            f"{relative}: collision de basename avec .github/agents/{basename}.agent.md; "
            "utiliser l'agent existant ou renommer le prompt"
        )

    if basename in collect_skill_basenames(project_root):
        return (
            f"{relative}: collision de basename avec .github/skills/{basename}/SKILL.md; "
            "utiliser la skill existante ou renommer le prompt"
        )

    _, body = split_frontmatter_body(content)
    if is_thin_wrapper_prompt(body):
        return (
            f"{relative}: thin wrapper prompt interdit; migrer vers une skill, une instruction, un hook "
            "ou l'artefact cible deja existant"
        )

    return None


def validate_candidate_paths(candidate_paths: set[Path], project_root: Path, python_executable: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for candidate in sorted(candidate_paths):
        relative = relpath(project_root, candidate)

        if candidate.suffix == ".py" and python_executable:
            exit_code, output = run_command([python_executable, "-m", "ruff", "check", str(candidate), "--no-fix", "-q"], project_root)
            if exit_code != 0 and "No module named ruff" not in output:
                short_output = " ".join(output.splitlines()[:4])
                errors.append(f"{relative}: ruff check a echoue ({short_output})")

        if relative.startswith(".github/hooks/") and candidate.suffix == ".json":
            try:
                json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{relative}: JSON invalide ({exc.msg} a la ligne {exc.lineno})")

        if candidate.suffix == ".sh":
            exit_code, output = run_command(["bash", "-n", str(candidate)], project_root)
            if exit_code != 0:
                short_output = " ".join(output.splitlines()[:4])
                errors.append(f"{relative}: bash -n a echoue ({short_output})")

        if relative.endswith((".agent.md", ".prompt.md", ".instructions.md", "/SKILL.md")):
            content = candidate.read_text(encoding="utf-8")
            if not content.startswith("---\n"):
                errors.append(f"{relative}: frontmatter YAML manquant")
            else:
                end = content.find("\n---", 4)
                if end == -1:
                    errors.append(f"{relative}: frontmatter YAML non ferme")
                elif yaml is not None:
                    frontmatter = content[4:end]
                    try:
                        parsed = yaml.safe_load(frontmatter) or {}
                    except Exception as exc:
                        errors.append(f"{relative}: frontmatter YAML invalide ({exc})")
                    else:
                        if not isinstance(parsed, dict):
                            errors.append(f"{relative}: frontmatter doit etre un mapping YAML")
                        elif not parsed.get("description"):
                            errors.append(f"{relative}: champ description manquant dans le frontmatter")
                        else:
                            prompt_error = validate_prompt_entrypoint(relative, candidate, project_root, content)
                            if prompt_error:
                                errors.append(prompt_error)
                            entrypoint_error = validate_agent_entrypoint(relative, candidate, parsed)
                            if entrypoint_error:
                                errors.append(entrypoint_error)

        is_document_markdown = candidate.suffix.lower() == ".md" and not relative.endswith(
            (".agent.md", ".prompt.md", ".instructions.md", "/SKILL.md")
        )

        if candidate.suffix.lower() == ".md":
            ref_validator_cls = load_core_symbol(project_root, "grimoire.core.ref_validator", "RefValidator")
            if ref_validator_cls is not None:
                try:
                    issues = ref_validator_cls(project_root).validate_file(candidate, check_stale=False)
                    broken = [issue for issue in issues if getattr(issue, "issue_type", "") == "broken"]
                    if broken:
                        preview = "; ".join(
                            f"L{getattr(issue, 'line', '?')}->{getattr(issue, 'ref', '?')}" for issue in broken[:3]
                        )
                        errors.append(f"{relative}: references Markdown cassees ({preview})")
                except Exception:
                    pass

            quality_module = load_tool_module("quality-score.py", "quality_score")
            if is_document_markdown and quality_module is not None and hasattr(quality_module, "score_artifact"):
                try:
                    report = quality_module.score_artifact(candidate)
                    score = int(report.get("score", 100) or 100)
                    if score < 75:
                        detail = "; ".join(str(item) for item in list(report.get("details", []) or [])[:2])
                        warnings.append(f"{relative}: score qualite {score}/100 ({detail or 'artefact a consolider'})")
                except Exception:
                    pass

            semantic_module = load_tool_module("semantic-chain.py", "semantic_chain")
            if is_document_markdown and semantic_module is not None and all(
                hasattr(semantic_module, attr)
                for attr in ("extract_concepts_from_file", "detect_drift")
            ):
                try:
                    content = candidate.read_text(encoding="utf-8")
                    neighbor_paths: list[Path] = []
                    for href in MARKDOWN_LINK_PATTERN.findall(content):
                        clean = href.split("#", 1)[0].strip()
                        if not clean or clean.startswith(("http://", "https://", "mailto:", "#")):
                            continue
                        neighbor = (candidate.parent / clean).resolve()
                        try:
                            neighbor.relative_to(project_root)
                        except ValueError:
                            continue
                        if neighbor.exists() and neighbor.is_file() and neighbor.suffix.lower() == ".md" and neighbor != candidate:
                            neighbor_paths.append(neighbor)
                    for neighbor in list(dict.fromkeys(neighbor_paths))[:2]:
                        source_concepts = semantic_module.extract_concepts_from_file(neighbor)
                        target_concepts = semantic_module.extract_concepts_from_file(candidate)
                        if not source_concepts or not target_concepts:
                            continue
                        drift = semantic_module.detect_drift(
                            source_concepts,
                            target_concepts,
                            source_label=relpath(project_root, neighbor),
                            target_label=relative,
                        )
                        if float(getattr(drift, "drift_score", 0.0) or 0.0) >= 0.5:
                            warnings.append(
                                f"{relative}: drift semantique eleve vs {relpath(project_root, neighbor)} "
                                f"({getattr(drift, 'level', 'warning')}, {float(getattr(drift, 'drift_score', 0.0)):.2f})"
                            )
                            break
                except Exception:
                    pass

    return errors, warnings


def extract_post_edit_candidate_paths(raw: str, project_root: Path) -> tuple[dict[str, Any] | None, set[Path]]:
    payload = load_payload(raw)
    if payload is None:
        return None, set()

    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if not detect_edit_tool(tool_name, raw.lower()):
        return payload, set()

    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    candidate_paths: set[Path] = set()
    walk_tool_input_for_paths(candidate_paths, project_root, tool_input)

    return payload, candidate_paths


def planning_artifacts_dir(project_root: Path) -> Path:
    return project_root / "_grimoire-runtime-output" / "planning-artifacts"


def collect_livrable_slugs(project_root: Path) -> tuple[str, ...]:
    root = planning_artifacts_dir(project_root)
    if not root.is_dir():
        return ()

    slugs: list[str] = []
    for candidate in sorted(root.glob(f"{LIVRABLE_FINAL_PREFIX}*.md")):
        slug = candidate.name[len(LIVRABLE_FINAL_PREFIX) : -3].strip()
        if slug:
            slugs.append(slug)
    return tuple(slugs)


def normalize_relative_workspace_paths(project_root: Path, paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw_path in paths:
        text = str(raw_path or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        with contextlib.suppress(OSError, ValueError):
            resolved = candidate.resolve()
            resolved.relative_to(project_root)
            if not resolved.exists() or not resolved.is_file():
                continue
            relative = relpath(project_root, resolved)
            if relative in seen:
                continue
            seen.add(relative)
            normalized.append(relative)

    return normalized[-RECENT_EDIT_PATH_LIMIT:]


def documentation_companion_paths(project_root: Path, slug: str) -> dict[str, Path]:
    root = planning_artifacts_dir(project_root)
    return {
        "technical": root / f"{TECHNICAL_DOC_PREFIX}{slug}.md",
        "usage": root / f"{USAGE_GUIDE_PREFIX}{slug}.md",
    }


def detect_documentation_slug(relative: str, available_slugs: tuple[str, ...]) -> str | None:
    if not relative.startswith(f"{PLANNING_ARTIFACTS_RELATIVE}/"):
        return None

    basename = Path(relative).name
    matches = [slug for slug in available_slugs if slug in basename]
    if not matches:
        return None
    return sorted(matches, key=len, reverse=True)[0]


def evaluate_documentation_companion_coverage(project_root: Path, relative_paths: list[str]) -> dict[str, Any]:
    available_slugs = collect_livrable_slugs(project_root)
    if not available_slugs:
        return {}

    package_reports: list[dict[str, Any]] = []
    issue_parts: list[str] = []
    ok_slugs: list[str] = []

    for slug in available_slugs:
        companion_paths = documentation_companion_paths(project_root, slug)
        touched_relatives = [
            relative for relative in relative_paths if detect_documentation_slug(relative, available_slugs) == slug
        ]
        if not touched_relatives:
            continue

        touched_paths = [(project_root / relative).resolve() for relative in touched_relatives]
        missing_docs: list[str] = []
        stale_docs: list[str] = []
        latest_source_relative = ""

        technical_path = companion_paths["technical"]
        usage_path = companion_paths["usage"]

        if not technical_path.is_file():
            missing_docs.append("documentation technique")
        if not usage_path.is_file():
            missing_docs.append("guide d'utilisation")

        non_companion_touched = [
            path
            for path in touched_paths
            if path.name not in {technical_path.name, usage_path.name}
        ]
        if non_companion_touched and not missing_docs:
            latest_source = max(non_companion_touched, key=lambda item: item.stat().st_mtime)
            latest_source_relative = relpath(project_root, latest_source)
            latest_source_mtime = latest_source.stat().st_mtime
            if technical_path.stat().st_mtime < latest_source_mtime:
                stale_docs.append("documentation technique")
            if usage_path.stat().st_mtime < latest_source_mtime:
                stale_docs.append("guide d'utilisation")

        package_reports.append(
            {
                "slug": slug,
                "touchedPaths": touched_relatives[:10],
                "requiredDocs": {
                    "technical": relpath(project_root, technical_path),
                    "usage": relpath(project_root, usage_path),
                },
                "missingDocs": missing_docs,
                "staleDocs": stale_docs,
                "latestSource": latest_source_relative,
            }
        )

        if missing_docs:
            issue_parts.append(f"{slug}: manque {', '.join(missing_docs)}")
            continue
        if stale_docs:
            against = f" vs {latest_source_relative}" if latest_source_relative else ""
            issue_parts.append(f"{slug}: resynchroniser {', '.join(stale_docs)}{against}")
            continue
        ok_slugs.append(slug)

    if not package_reports:
        return {}

    decision = "block" if issue_parts else "allow"
    if issue_parts:
        summary = "Compagnons documentaires: " + " | ".join(issue_parts[:4])
        reason = (
            "Documentation compagnon obligatoire manquante ou non resynchronisee. "
            + "; ".join(issue_parts[:4])
            + ". Mettre a jour "
            + TECHNICAL_DOC_PREFIX
            + "<slug>.md et "
            + USAGE_GUIDE_PREFIX
            + "<slug>.md avant de conclure."
        )
    else:
        summary = "Compagnons documentaires a jour: " + ", ".join(ok_slugs[:4])
        reason = ""

    return {
        "decision": decision,
        "summary": summary,
        "reason": reason,
        "packages": package_reports,
        "recentEditedPaths": relative_paths[-RECENT_EDIT_PATH_LIMIT:],
        "checkedAt": timestamp_now(),
    }


def format_documentation_coverage_note(coverage: dict[str, Any]) -> str:
    return compact_text(str(coverage.get("summary") or ""), 700)


def update_documentation_coverage_state(
    prompt_state_file: Path | None,
    project_root: Path,
    candidate_paths: set[Path],
) -> tuple[dict[str, Any], str]:
    if prompt_state_file is None or not candidate_paths:
        return {}, ""

    prompt_state = load_prompt_state(prompt_state_file)
    existing_paths = [str(item) for item in list(prompt_state.get("recentEditedPaths", []) or []) if str(item).strip()]
    new_paths = [relpath(project_root, path) for path in sorted(candidate_paths)]
    merged_paths = normalize_relative_workspace_paths(project_root, existing_paths + new_paths)
    coverage = evaluate_documentation_companion_coverage(project_root, merged_paths)

    prompt_state_updated = dict(prompt_state)
    prompt_state_updated["recentEditedPaths"] = merged_paths
    if coverage:
        prompt_state_updated["documentationCoverageState"] = coverage
    else:
        prompt_state_updated.pop("documentationCoverageState", None)
    save_json_mapping(prompt_state_file, prompt_state_updated)
    persist_session_state(
        session_state_file_from_hook_path(prompt_state_file),
        prompt_state_updated,
        {},
        {},
        "post-tool-use",
    )
    return coverage, format_documentation_coverage_note(coverage)


def resolve_documentation_coverage_state(project_root: Path, prompt_state: dict[str, Any]) -> dict[str, Any]:
    existing_paths = [str(item) for item in list(prompt_state.get("recentEditedPaths", []) or []) if str(item).strip()]
    merged_paths = normalize_relative_workspace_paths(project_root, existing_paths)
    if not merged_paths:
        return {}
    return evaluate_documentation_companion_coverage(project_root, merged_paths)


def evaluate_post_edit(raw: str, project_root: Path, python_executable: str) -> dict[str, Any]:
    payload, candidate_paths = extract_post_edit_candidate_paths(raw, project_root)
    if payload is None:
        return {}

    if not candidate_paths:
        return {}

    errors, warnings = validate_candidate_paths(candidate_paths, project_root, python_executable)
    if not errors and not warnings:
        return {}

    details = errors[:6] if errors else warnings[:6]
    additional_context = "Validation Grimoire locale: " + " | ".join(details)
    if errors:
        return make_block_payload("PostToolUse", "Validation locale echouee apres edition.", additional_context)

    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    }


def capture_external_reference_proof(
    raw: str,
    prompt_state_file: Path | None,
) -> tuple[dict[str, Any], str]:
    if prompt_state_file is None:
        return {}, ""

    payload = load_payload(raw)
    if payload is None:
        return {}, ""

    prompt_state = load_prompt_state(prompt_state_file)
    external_references = (
        prompt_state.get("externalReferences") if isinstance(prompt_state.get("externalReferences"), dict) else {}
    )
    external_reference_state = (
        prompt_state.get("externalReferenceState")
        if isinstance(prompt_state.get("externalReferenceState"), dict)
        else {}
    )
    proof = build_external_reference_proof(payload)
    if not proof:
        return {}, ""

    if not external_reference_state:
        external_reference_state = advance_external_reference_state(external_references, {})

    updated_state = apply_external_reference_proof(external_reference_state, proof)
    if not updated_state:
        return {}, ""

    prompt_state_updated = dict(prompt_state)
    prompt_state_updated["externalReferenceState"] = updated_state
    save_json_mapping(prompt_state_file, prompt_state_updated)
    persist_session_state(
        session_state_file_from_hook_path(prompt_state_file),
        prompt_state_updated,
        {},
        {},
        "post-tool-use",
    )
    return updated_state, format_external_reference_state_note(updated_state)


def capture_internal_reference_proof(
    raw: str,
    prompt_state_file: Path | None,
    project_root: Path,
) -> tuple[dict[str, Any], str]:
    if prompt_state_file is None:
        return {}, ""

    payload = load_payload(raw)
    if payload is None:
        return {}, ""

    prompt_state = load_prompt_state(prompt_state_file)
    internal_references = (
        prompt_state.get("internalReferences") if isinstance(prompt_state.get("internalReferences"), dict) else {}
    )
    internal_reference_state = (
        prompt_state.get("internalReferenceState")
        if isinstance(prompt_state.get("internalReferenceState"), dict)
        else {}
    )
    proof = build_internal_reference_proof(payload, project_root)
    if not proof:
        return {}, ""

    if not internal_reference_state:
        internal_reference_state = advance_internal_reference_state(internal_references, {})

    updated_state = apply_internal_reference_proof(internal_reference_state, proof)
    if not updated_state:
        return {}, ""

    prompt_state_updated = dict(prompt_state)
    prompt_state_updated["internalReferenceState"] = updated_state
    save_json_mapping(prompt_state_file, prompt_state_updated)
    persist_session_state(
        session_state_file_from_hook_path(prompt_state_file),
        prompt_state_updated,
        {},
        {},
        "post-tool-use",
    )
    return updated_state, format_internal_reference_state_note(updated_state)


def capture_visual_validation_proof(
    raw: str,
    prompt_state_file: Path | None,
) -> tuple[dict[str, Any], str]:
    if prompt_state_file is None:
        return {}, ""

    payload = load_payload(raw)
    if payload is None:
        return {}, ""

    prompt_state = load_prompt_state(prompt_state_file)
    visual_validation = (
        prompt_state.get("visualValidation") if isinstance(prompt_state.get("visualValidation"), dict) else {}
    )
    if not visual_validation.get("required"):
        return {}, ""

    visual_validation_state = (
        prompt_state.get("visualValidationState")
        if isinstance(prompt_state.get("visualValidationState"), dict)
        else {}
    )
    if not visual_validation_state:
        visual_validation_state = advance_visual_validation_state(visual_validation, {})

    proof = build_visual_validation_proof(payload)
    if not proof:
        return {}, ""

    updated_state = apply_visual_validation_proof(visual_validation_state, proof)
    if not updated_state:
        return {}, ""

    prompt_state_updated = dict(prompt_state)
    prompt_state_updated["visualValidationState"] = updated_state
    save_json_mapping(prompt_state_file, prompt_state_updated)
    persist_session_state(
        session_state_file_from_hook_path(prompt_state_file),
        prompt_state_updated,
        {},
        {},
        "post-tool-use",
    )
    return updated_state, format_visual_validation_state_note(updated_state)


def merge_post_tool_outputs(base_output: dict[str, Any], extra_context: str) -> dict[str, Any]:
    if not extra_context:
        return base_output

    merged = dict(base_output) if isinstance(base_output, dict) else {}
    hook_output = merged.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        merged["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": extra_context,
        }
        return merged

    existing_context = compact_text(str(hook_output.get("additionalContext") or ""), 800)
    hook_output["additionalContext"] = (
        f"{extra_context} | {existing_context}".strip(" |") if existing_context else extra_context
    )[:1100]
    merged["hookSpecificOutput"] = hook_output
    return merged


def command_prompt_signals(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    payload = load_payload(raw)
    if payload is None:
        print("{}")
        return 0

    prompt = str(payload.get("prompt") or "")
    if not prompt.strip():
        print("{}")
        return 0

    timestamp = str(payload.get("timestamp") or "")
    signals = analyze_prompt(prompt)
    state = signals.to_state(prompt, timestamp)
    extra_notes, extra_state = enrich_prompt_signals(args.project_root.resolve(), prompt, signals)
    previous_session_state = load_json_mapping(session_state_file_from_hook_path(args.latest_file))
    previous_clarification_state = (
        previous_session_state.get("clarificationState")
        if isinstance(previous_session_state.get("clarificationState"), dict)
        else {}
    )
    previous_external_reference_state = (
        previous_session_state.get("externalReferenceState")
        if isinstance(previous_session_state.get("externalReferenceState"), dict)
        else {}
    )
    previous_internal_reference_state = (
        previous_session_state.get("internalReferenceState")
        if isinstance(previous_session_state.get("internalReferenceState"), dict)
        else {}
    )
    previous_visual_validation_state = (
        previous_session_state.get("visualValidationState")
        if isinstance(previous_session_state.get("visualValidationState"), dict)
        else {}
    )
    clarification_state = advance_clarification_state(
        prompt,
        extra_state.get("clarification") if isinstance(extra_state.get("clarification"), dict) else {},
        previous_clarification_state,
    )
    if clarification_state:
        extra_state["clarificationState"] = clarification_state
    external_reference_state = advance_external_reference_state(
        extra_state.get("externalReferences") if isinstance(extra_state.get("externalReferences"), dict) else {},
        previous_external_reference_state,
    )
    if external_reference_state:
        extra_state["externalReferenceState"] = external_reference_state
    internal_reference_state = advance_internal_reference_state(
        extra_state.get("internalReferences") if isinstance(extra_state.get("internalReferences"), dict) else {},
        previous_internal_reference_state,
    )
    if internal_reference_state:
        extra_state["internalReferenceState"] = internal_reference_state
    visual_validation_state = advance_visual_validation_state(
        extra_state.get("visualValidation") if isinstance(extra_state.get("visualValidation"), dict) else {},
        previous_visual_validation_state,
    )
    if visual_validation_state:
        extra_state["visualValidationState"] = visual_validation_state

    memory_context = build_memory_context(prompt, previous_session_state, internal_reference_state)
    if memory_context:
        extra_state["memoryContext"] = memory_context

    # PCG — Prompt Clarity Gate
    session_ctx: dict[str, Any] = {
        "recent_files": [
            str(f)
            for f in (previous_session_state.get("recentFiles") or [])
            if f
        ]
    }
    user_skill_level = str(
        payload.get("userSkillLevel")
        or payload.get("user_skill_level")
        or ""
    )
    clarity = compute_prompt_clarity(prompt, session_ctx, user_skill_level)
    if clarity["level"] != "CLEAR":
        extra_state["promptClarity"] = clarity

    combined_notes = list(state.get("notes", [])) + list(extra_notes)
    combined_notes = [note for note in combined_notes if not note.startswith("Clarification interactive requise maintenant:")]
    clarification_note = format_clarification_state_note(clarification_state)
    external_reference_note = format_external_reference_state_note(external_reference_state)
    internal_reference_note = format_internal_reference_state_note(internal_reference_state)
    visual_validation_note = format_visual_validation_state_note(visual_validation_state)
    memory_context_note = format_memory_context_note(memory_context)
    priority_notes = [
        note
        for note in (
            clarification_note,
            external_reference_note,
            internal_reference_note,
            visual_validation_note,
            memory_context_note,
        )
        if note
    ]
    combined_notes = dedupe_preserve_order(priority_notes + combined_notes)
    state["notes"] = list(combined_notes)
    state.update(extra_state)

    args.latest_file.parent.mkdir(parents=True, exist_ok=True)
    args.events_file.parent.mkdir(parents=True, exist_ok=True)
    args.latest_file.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    with args.events_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(state, ensure_ascii=True) + "\n")

    persist_session_state(session_state_file_from_hook_path(args.latest_file), state, {}, {}, "prompt-signals")

    additional_context = " ".join(state["notes"])[: args.max_context_length]
    if not additional_context:
        print("{}")
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_session_start(args: argparse.Namespace) -> int:
    output = evaluate_session_start(args.config_file, args.shared_context_file, args.latest_file)
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_subagent_context(args: argparse.Namespace) -> int:
    prompt_state = load_prompt_state(args.prompt_state_file)
    project_root = Path(args.project_root).resolve() if getattr(args, "project_root", None) else None
    output = evaluate_subagent_context(sys.stdin.read(), prompt_state, project_root=project_root)
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_pre_compact(args: argparse.Namespace) -> int:
    prompt_state = load_prompt_state(args.prompt_state_file)
    task_state = load_prompt_state(args.task_latest_file)
    trace_state = load_last_jsonl(args.trace_file)
    subagent_state = load_prompt_state(args.subagent_latest_file)
    output = evaluate_precompact(
        sys.stdin.read(),
        prompt_state,
        task_state,
        trace_state,
        subagent_state,
        args.project_root.resolve(),
        args.latest_file,
        args.events_file,
    )
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_control_surface(args: argparse.Namespace) -> int:
    prompt_state = load_prompt_state(args.prompt_state_file)
    output = evaluate_control_surface(sys.stdin.read(), prompt_state)
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_memory_guard(args: argparse.Namespace) -> int:
    del args
    output = evaluate_memory_guard(sys.stdin.read())
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_post_edit(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    prompt_state_file = args.prompt_state_file if isinstance(args.prompt_state_file, Path) else None
    _, candidate_paths = extract_post_edit_candidate_paths(raw, args.project_root.resolve())
    _, internal_proof_note = capture_internal_reference_proof(raw, prompt_state_file, args.project_root.resolve())
    _, external_proof_note = capture_external_reference_proof(raw, prompt_state_file)
    _, visual_proof_note = capture_visual_validation_proof(raw, prompt_state_file)
    _, documentation_coverage_note = update_documentation_coverage_state(
        prompt_state_file,
        args.project_root.resolve(),
        candidate_paths,
    )
    output = evaluate_post_edit(raw, args.project_root.resolve(), args.python_executable)
    proof_note = " | ".join(
        note
        for note in (
            internal_proof_note,
            external_proof_note,
            visual_proof_note,
            documentation_coverage_note,
        )
        if note
    )
    output = merge_post_tool_outputs(output, proof_note)
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_subagent_stop(args: argparse.Namespace) -> int:
    prompt_state = load_prompt_state(args.prompt_state_file)
    output = evaluate_subagent_stop(
        sys.stdin.read(),
        prompt_state,
        args.project_root.resolve(),
        args.latest_file,
        args.events_file,
        args.counter_file,
    )
    print(json.dumps(output, ensure_ascii=True))
    return 0


def command_stop_closure(args: argparse.Namespace) -> int:
    payload = load_payload(sys.stdin.read()) or {}
    prompt_state = load_prompt_state(args.prompt_state_file)
    task_state = load_prompt_state(args.task_latest_file)
    subagent_state = load_prompt_state(args.subagent_latest_file)
    stop_hook_active = bool(payload.get("stop_hook_active") or payload.get("stopHookActive") or False)
    token_budget = prompt_state.get("tokenBudget") if isinstance(prompt_state, dict) else {}
    if not isinstance(token_budget, dict) or not token_budget:
        token_budget = assess_token_budget(args.project_root.resolve(), force=True)

    prompt_state_for_session = dict(prompt_state) if isinstance(prompt_state, dict) else {}
    if token_budget and not prompt_state_for_session.get("tokenBudget"):
        prompt_state_for_session["tokenBudget"] = token_budget
    documentation_coverage = resolve_documentation_coverage_state(args.project_root.resolve(), prompt_state_for_session)
    if documentation_coverage:
        prompt_state_for_session["documentationCoverageState"] = documentation_coverage
    session_state = persist_session_state(
        session_state_file_from_hook_path(args.latest_file),
        prompt_state_for_session,
        task_state,
        subagent_state,
        "stop-closure",
    )

    summary_parts: list[str] = []
    prompt_preview = str(prompt_state.get("promptPreview") or "")
    if prompt_preview:
        summary_parts.append(f"objectif={compact_text(prompt_preview, 120)}")
    if task_state.get("task"):
        summary_parts.append(f"task={task_state.get('task')}:{task_state.get('status', 'unknown')}")
    if subagent_state.get("agent") and subagent_state.get("grade"):
        summary_parts.append(f"subagent={subagent_state.get('agent')}:{subagent_state.get('grade')}")

    additional_parts: list[str] = []
    hygiene_actions = recommend_hygiene_actions(task_state, prompt_state, subagent_state)
    closure_risk = assess_closure_risk(
        prompt_state_for_session,
        task_state,
        subagent_state,
        session_state,
        stop_hook_active,
    )
    logical_next_tasks = derive_logical_next_tasks(
        prompt_state_for_session,
        task_state,
        subagent_state,
        session_state,
        closure_risk,
    )
    logical_follow_through = {}
    if logical_next_tasks and not stop_hook_active:
        logical_follow_through = execute_logical_follow_through_tasks(
            args.project_root.resolve(),
            prompt_preview,
            logical_next_tasks,
        )
        if str(logical_follow_through.get("status") or "") in {"completed", "already-satisfied"}:
            logical_next_tasks = []
    closure_risk_ticket = sync_closure_risk_ticket(
        args.project_root.resolve(),
        closure_risk,
        prompt_preview,
        hygiene_actions,
    )
    logical_follow_through_ticket = sync_logical_follow_through_ticket(
        args.project_root.resolve(),
        prompt_preview,
        logical_next_tasks,
        logical_follow_through,
    )
    source_reports = load_recent_source_reports(args.project_root.resolve())
    if hygiene_actions:
        additional_parts.append("Avant cloture: " + "; ".join(hygiene_actions[:5]))
    if token_budget:
        additional_parts.append(format_token_budget_note(token_budget))
    conflict = subagent_state.get("conflict") if isinstance(subagent_state.get("conflict"), dict) else {}
    if conflict:
        additional_parts.append(
            f"Conflit a resoudre: {conflict.get('previousAgent')}:{conflict.get('previousGrade')} vs {conflict.get('currentAgent')}:{conflict.get('currentGrade')}"
        )
    if closure_risk.get("summary"):
        additional_parts.append(str(closure_risk["summary"]))
    if documentation_coverage.get("summary"):
        additional_parts.append(str(documentation_coverage["summary"]))
    source_observability_summary = format_source_observability_summary(source_reports)
    if source_observability_summary:
        additional_parts.append(source_observability_summary)
        additional_parts.append(
            "Dans la reponse finale visible, ajouter un bloc Observabilite listant pour chaque agent les sources utilisees et leur % d'appui documentaire."
        )
    if session_state.get("summary"):
        additional_parts.append(str(session_state["summary"]))
    if session_state.get("evidenceSummary"):
        additional_parts.append(str(session_state["evidenceSummary"]))
    if closure_risk_ticket:
        additional_parts.append(f"Ticket differe ouvert: {closure_risk_ticket.get('id')}")
    if logical_next_tasks:
        additional_parts.append(
            "Suite logique candidate: " + "; ".join(str(item.get("task") or "") for item in logical_next_tasks[:4])
        )
    if logical_follow_through:
        follow_through_status = str(logical_follow_through.get("status") or "")
        executed_preview = "; ".join(str(item) for item in list(logical_follow_through.get("executedTasks", []) or [])[:4])
        if follow_through_status in {"completed", "already-satisfied"}:
            additional_parts.append(f"Suite logique executee: {executed_preview}")
        else:
            failed_task = str(logical_follow_through.get("failedTask") or "")
            additional_parts.append(
                "Suite logique auto-executee en echec"
                + (f" sur {failed_task}" if failed_task else "")
                + "."
            )
    if logical_follow_through_ticket:
        additional_parts.append(f"Ticket differe ouvert: {logical_follow_through_ticket.get('id')}")
    deferred_tickets_summary = summarize_deferred_tickets(args.project_root.resolve())
    if deferred_tickets_summary:
        additional_parts.append(deferred_tickets_summary)
    if source_observability_summary:
        additional_parts.append(
            "Observabilite detaillee: _grimoire-runtime-output/hook-runtime/source-observability/events.jsonl"
        )

    state = {
        "timestamp": timestamp_now(),
        "stopHookActive": stop_hook_active,
        "summary": " | ".join(summary_parts),
        "additionalContext": " | ".join(additional_parts)[:1100],
        "openObligationsCount": int(session_state.get("openObligationsCount", 0) or 0),
        "criticalOpenCount": int(session_state.get("criticalOpenCount", 0) or 0),
        "openObligations": list(session_state.get("openObligations", []) or []),
        "evidence": list(session_state.get("evidence", []) or []),
        "closureRisk": closure_risk,
        "documentationCoverage": documentation_coverage,
        "logicalNextTasks": logical_next_tasks,
        "logicalFollowThrough": logical_follow_through,
        "sourceReports": source_reports,
        "deferredTicketsSummary": deferred_tickets_summary,
    }
    args.latest_file.parent.mkdir(parents=True, exist_ok=True)
    args.latest_file.write_text(json.dumps(state, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    telemetry_cls = load_core_symbol(args.project_root.resolve(), "grimoire.core.telemetry", "Telemetry")
    if telemetry_cls is not None:
        try:
            telemetry = telemetry_cls(args.project_root.resolve())
            telemetry.record_session(
                outcome="completed",
                message=compact_text(state["summary"], 180),
                metadata={"source": "stop-hook", "stopHookActive": state["stopHookActive"]},
            )
        except Exception:
            pass

    hook_specific_output: dict[str, Any] = {"hookEventName": "Stop"}
    if state["additionalContext"]:
        hook_specific_output["additionalContext"] = state["additionalContext"]
    if str(closure_risk.get("decision") or "") == "block" and str(closure_risk.get("reason") or ""):
        hook_specific_output["decision"] = "block"
        hook_specific_output["reason"] = str(closure_risk["reason"])
    elif (
        str(documentation_coverage.get("decision") or "") == "block"
        and str(documentation_coverage.get("reason") or "")
    ):
        hook_specific_output["decision"] = "block"
        hook_specific_output["reason"] = str(documentation_coverage["reason"])
    elif logical_follow_through and str(logical_follow_through.get("status") or "") not in {"completed", "already-satisfied"}:
        failed_task = str(logical_follow_through.get("failedTask") or "")
        hook_specific_output["decision"] = "block"
        hook_specific_output["reason"] = (
            "La suite logique auto-executee a echoue"
            + (f" sur {failed_task}" if failed_task else "")
            + "; poursuivre la correction avant de conclure."
        )
    elif logical_next_tasks and not stop_hook_active:
        tasks_preview = "; ".join(str(item.get("task") or "") for item in logical_next_tasks[:4])
        hook_specific_output["decision"] = "block"
        hook_specific_output["reason"] = (
            "Suite logique detectee: ajoute ces taches a la checklist/todo active puis execute-les maintenant, "
            f"dans cet ordre: {tasks_preview}."
        )

    if len(hook_specific_output) > 1:
        print(json.dumps({"hookSpecificOutput": hook_specific_output}, ensure_ascii=True))
        return 0

    print("{}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guardrail policy engine for Grimoire hooks")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt_parser = subparsers.add_parser("prompt-signals")
    prompt_parser.add_argument("--project-root", type=Path, default=guardrail_repo_root())
    prompt_parser.add_argument("--latest-file", type=Path, required=True)
    prompt_parser.add_argument("--events-file", type=Path, required=True)
    prompt_parser.add_argument("--max-context-length", type=int, default=900)
    prompt_parser.set_defaults(func=command_prompt_signals)

    session_start_parser = subparsers.add_parser("session-start")
    session_start_parser.add_argument("--config-file", type=Path, required=True)
    session_start_parser.add_argument("--shared-context-file", type=Path, required=True)
    session_start_parser.add_argument("--latest-file", type=Path, required=True)
    session_start_parser.set_defaults(func=command_session_start)

    subagent_parser = subparsers.add_parser("subagent-context")
    subagent_parser.add_argument("--project-root", type=Path, default=guardrail_repo_root())
    subagent_parser.add_argument("--prompt-state-file", type=Path, required=True)
    subagent_parser.set_defaults(func=command_subagent_context)

    precompact_parser = subparsers.add_parser("pre-compact")
    precompact_parser.add_argument("--project-root", type=Path, default=guardrail_repo_root())
    precompact_parser.add_argument("--prompt-state-file", type=Path, required=True)
    precompact_parser.add_argument("--task-latest-file", type=Path, required=True)
    precompact_parser.add_argument("--trace-file", type=Path, required=True)
    precompact_parser.add_argument("--subagent-latest-file", type=Path, required=True)
    precompact_parser.add_argument("--latest-file", type=Path, required=True)
    precompact_parser.add_argument("--events-file", type=Path, required=True)
    precompact_parser.set_defaults(func=command_pre_compact)

    control_parser = subparsers.add_parser("control-surface")
    control_parser.add_argument("--prompt-state-file", type=Path, required=True)
    control_parser.set_defaults(func=command_control_surface)

    memory_parser = subparsers.add_parser("memory-guard")
    memory_parser.set_defaults(func=command_memory_guard)

    post_edit_parser = subparsers.add_parser("post-edit")
    post_edit_parser.add_argument("--project-root", type=Path, required=True)
    post_edit_parser.add_argument("--python-executable", default=sys.executable)
    post_edit_parser.add_argument("--prompt-state-file", type=Path)
    post_edit_parser.set_defaults(func=command_post_edit)

    subagent_stop_parser = subparsers.add_parser("subagent-stop")
    subagent_stop_parser.add_argument("--project-root", type=Path, required=True)
    subagent_stop_parser.add_argument("--prompt-state-file", type=Path, required=True)
    subagent_stop_parser.add_argument("--latest-file", type=Path, required=True)
    subagent_stop_parser.add_argument("--events-file", type=Path, required=True)
    subagent_stop_parser.add_argument("--counter-file", type=Path, required=True)
    subagent_stop_parser.set_defaults(func=command_subagent_stop)

    stop_closure_parser = subparsers.add_parser("stop-closure")
    stop_closure_parser.add_argument("--project-root", type=Path, required=True)
    stop_closure_parser.add_argument("--prompt-state-file", type=Path, required=True)
    stop_closure_parser.add_argument("--task-latest-file", type=Path, required=True)
    stop_closure_parser.add_argument("--subagent-latest-file", type=Path, required=True)
    stop_closure_parser.add_argument("--latest-file", type=Path, required=True)
    stop_closure_parser.set_defaults(func=command_stop_closure)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
