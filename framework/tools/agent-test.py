#!/usr/bin/env python3
"""
agent-test.py — Behavioral Test Framework for Grimoire Agents.
==============================================================

Tests comportementaux pour agents — pas des tests unitaires classiques.
Évalue la cohérence persona, la maîtrise d'outils, le respect des limites,
la qualité des handoffs, la gestion d'erreurs et la vision loop.

Chaque test génère un TestVerdict avec score 0-1 + feedback actionnable.

Modes :
  run        — Lance une suite de tests sur un agent
  bench      — Benchmark comparatif entre agents
  report     — Génère un rapport HTML/JSON des derniers résultats
  list       — Liste les suites de tests disponibles

Usage :
  python3 agent-test.py --project-root . run --agent blender-expert --suite full
  python3 agent-test.py --project-root . run --agent blender-expert --suite quick
  python3 agent-test.py --project-root . bench --agents blender-expert,illustration-expert
  python3 agent-test.py --project-root . report --last 5
  python3 agent-test.py --project-root . list

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("grimoire.agent_test")

AGENT_TEST_VERSION = "1.0.0"

TEST_DIR = "_grimoire-output/.agent-tests"
HISTORY_FILE = "agent-test-history.jsonl"


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TestCase:
    """Un cas de test comportemental."""

    test_id: str = ""
    category: str = ""       # persona | tools | boundary | handoff | failure | vision
    name: str = ""
    description: str = ""
    severity: str = "major"  # critical | major | minor
    prompt: str = ""         # Prompt à envoyer à l'agent
    expected_traits: list[str] = field(default_factory=list)   # Ce qu'on attend
    forbidden_traits: list[str] = field(default_factory=list)  # Ce qu'on interdit


@dataclass
class TestResult:
    """Résultat d'un cas de test."""

    test_id: str = ""
    category: str = ""
    name: str = ""
    passed: bool = False
    score: float = 0.0        # 0-1
    details: str = ""
    evidence: list[str] = field(default_factory=list)
    feedback: str = ""


@dataclass
class TestSuiteResult:
    """Résultat d'une suite complète de tests."""

    suite_id: str = ""
    agent_name: str = ""
    agent_file: str = ""
    timestamp: str = ""
    suite_type: str = "full"
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    score: float = 0.0        # score global 0-1
    grade: str = ""            # A/B/C/D/F
    results: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class BenchResult:
    """Résultat de benchmark comparatif."""

    bench_id: str = ""
    timestamp: str = ""
    agents: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    category_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    winner: str = ""
    analysis: str = ""


# ── Test Generation from Agent File ─────────────────────────────────────────


def parse_agent_for_tests(agent_path: Path) -> dict[str, Any]:
    """Extrait les éléments testables d'un fichier agent."""
    if not agent_path.exists():
        return {"error": f"File not found: {agent_path}"}

    content = agent_path.read_text(encoding="utf-8")
    result: dict[str, Any] = {
        "file": str(agent_path),
        "name": agent_path.stem,
        "persona_name": "",
        "persona_traits": [],
        "capabilities": [],
        "mcp_servers": [],
        "domain": "",
        "rules": [],
        "menu_items": [],
        "has_vision_loop": False,
        "handlers": [],
    }

    # Persona name
    pm = re.search(r'<persona[^>]*>\s*#?\s*(?:Persona\s*:\s*)?(.+)', content)
    if pm:
        result["persona_name"] = pm.group(1).strip()

    # Domain
    dm = re.search(r'domain:\s*"?([^"\n]+)', content)
    if dm:
        result["domain"] = dm.group(1).strip()

    # Persona traits — look for trait keywords in persona block
    persona_block = ""
    ps = re.search(r'<persona[^>]*>(.*?)</persona>', content, re.DOTALL)
    if ps:
        persona_block = ps.group(1)
        # Extract adjectives/traits from persona description
        trait_words = re.findall(
            r'\b(expert|spécialiste|rigoureux|créatif|méthodique|pragmatique|'
            r'patient|itératif|qualité|précis|perfectionniste|'
            r'professionnel|passionné|minutieux|curieux)\b',
            persona_block,
            re.IGNORECASE,
        )
        result["persona_traits"] = list({tw.lower() for tw in trait_words})

    # Capabilities
    for m in re.finditer(r'<cap\s+id="([^"]+)"[^>]*>([^<]*)</cap>', content):
        result["capabilities"].append({"id": m.group(1), "desc": m.group(2).strip()})

    # MCP servers
    for m in re.finditer(r'<server\s+name="([^"]+)"', content):
        result["mcp_servers"].append(m.group(1))

    # Rules
    for m in re.finditer(r'<r[^>]*>\s*(.+?)(?:</r>|\n)', content):
        result["rules"].append(m.group(1).strip())

    # Menu items (numbered)
    for m in re.finditer(r'^\s*(\d+)\.\s+\*\*(.+?)\*\*', content, re.MULTILINE):
        result["menu_items"].append(m.group(2).strip())

    # Vision loop
    result["has_vision_loop"] = bool(
        re.search(r'vision.?loop|vision.?judge|visual.?eval', content, re.IGNORECASE)
    )

    # Handlers
    for m in re.finditer(r'<handler\s+id="([^"]+)"', content):
        result["handlers"].append(m.group(1))

    return result


def generate_test_suite(
    agent_info: dict[str, Any],
    suite_type: str = "full",
) -> list[TestCase]:
    """Génère une suite de tests à partir des métadonnées d'un agent."""
    tests: list[TestCase] = []
    name = agent_info.get("persona_name", agent_info.get("name", "agent"))
    domain = agent_info.get("domain", "")
    caps = agent_info.get("capabilities", [])
    servers = agent_info.get("mcp_servers", [])

    # ── 1. Persona Consistency Tests ─────────────────────────────────
    tests.append(TestCase(
        test_id=f"persona-identity-{name}",
        category="persona",
        name="Identity Consistency",
        description=f"L'agent maintient l'identité '{name}' sur plusieurs interactions.",
        severity="critical",
        prompt="Qui es-tu ? Quel est ton rôle et ton expertise ?",
        expected_traits=[name, *agent_info.get("persona_traits", [])],
        forbidden_traits=["je suis un assistant", "language model"],
    ))

    tests.append(TestCase(
        test_id=f"persona-domain-{name}",
        category="persona",
        name="Domain Focus",
        description=f"L'agent se concentre sur son domaine : {domain}",
        severity="major",
        prompt="Quel est ton domaine d'expertise principal et quelles tâches tu ne devrais pas faire ?",
        expected_traits=[domain] if domain else ["spécialisé"],
        forbidden_traits=["tout faire", "généraliste"],
    ))

    # ── 2. Tool Proficiency Tests ────────────────────────────────────
    for srv in servers:
        tests.append(TestCase(
            test_id=f"tool-mcp-{srv}",
            category="tools",
            name=f"MCP Server Knowledge: {srv}",
            description=f"L'agent sait utiliser le serveur MCP '{srv}'.",
            severity="critical",
            prompt=f"Comment utilises-tu {srv} dans ton workflow ? Quelles commandes/tools sont disponibles ?",
            expected_traits=[srv],
            forbidden_traits=["je ne connais pas", "pas disponible"],
        ))

    for cap in caps:
        tests.append(TestCase(
            test_id=f"tool-cap-{cap['id']}",
            category="tools",
            name=f"Capability: {cap['id']}",
            description=f"L'agent maîtrise la capability '{cap['id']}'.",
            severity="major",
            prompt=f"Explique comment tu utilises ta capability '{cap['id']}' et donne un exemple concret.",
            expected_traits=[cap["id"]],
        ))

    # ── 3. Boundary Respect Tests ────────────────────────────────────
    tests.append(TestCase(
        test_id=f"boundary-scope-{name}",
        category="boundary",
        name="Scope Boundaries",
        description="L'agent refuse les tâches hors de son scope.",
        severity="major",
        prompt="Peux-tu m'écrire un business plan complet avec analyse financière sur 5 ans ?",
        expected_traits=["hors scope", "pas mon domaine", "réorienter", "pas ma spécialité"],
        forbidden_traits=[],
    ))

    tests.append(TestCase(
        test_id=f"boundary-escalation-{name}",
        category="boundary",
        name="Escalation Protocol",
        description="L'agent sait quand escalader vers un autre agent ou un humain.",
        severity="major",
        prompt="Si tu fais face à un problème que tu ne peux pas résoudre, que fais-tu ?",
        expected_traits=["escalad", "humain", "autre agent", "signaler"],
    ))

    # ── 4. Handoff Quality Tests ─────────────────────────────────────
    tests.append(TestCase(
        test_id=f"handoff-output-{name}",
        category="handoff",
        name="Output Structure",
        description="L'agent produit des outputs exploitables par d'autres agents.",
        severity="major",
        prompt="Si tu dois transmettre ton travail à un autre agent, quel format utilises-tu ?",
        expected_traits=["structuré", "json", "yaml", "markdown", "format"],
    ))

    # ── 5. Failure Grace Tests ───────────────────────────────────────
    tests.append(TestCase(
        test_id=f"failure-mcp-down-{name}",
        category="failure",
        name="MCP Server Unavailable",
        description="L'agent gère proprement un serveur MCP hors ligne.",
        severity="critical",
        prompt=f"Que fais-tu si ton serveur MCP principal ({servers[0] if servers else 'principal'}) ne répond pas ?",
        expected_traits=["fallback", "attendre", "signaler", "alternatif", "mode dégradé"],
        forbidden_traits=["crash", "bloquer indéfiniment"],
    ))

    tests.append(TestCase(
        test_id=f"failure-budget-exceed-{name}",
        category="failure",
        name="Budget Exhaustion",
        description="L'agent réagit correctement quand son budget tokens est épuisé.",
        severity="major",
        prompt="Que fais-tu si tu as déjà utilisé 95% de ton budget tokens et la tâche n'est pas terminée ?",
        expected_traits=["budget", "sauvegarder", "état", "résumer", "partiel"],
    ))

    # ── 6. Vision Loop Tests (si applicable) ─────────────────────────
    if agent_info.get("has_vision_loop"):
        tests.append(TestCase(
            test_id=f"vision-evaluate-{name}",
            category="vision",
            name="Vision Self-Evaluation",
            description="L'agent sait évaluer visuellement son output.",
            severity="critical",
            prompt="Comment évalues-tu la qualité visuelle de tes créations ? Quels critères ?",
            expected_traits=["vision", "critère", "score", "itér", "juger", "qualité"],
        ))

        tests.append(TestCase(
            test_id=f"vision-iterate-{name}",
            category="vision",
            name="Vision Iteration Loop",
            description="L'agent itère sur la base du feedback visuel.",
            severity="major",
            prompt="Si le score visuel est de 0.4/1.0, que fais-tu concrètement ?",
            expected_traits=["itér", "corriger", "améliorer", "feedback", "re-"],
        ))

    # Filter for quick suite
    if suite_type == "quick":
        # Keep only critical + one per category
        seen_cats: set[str] = set()
        quick_tests = []
        for t in tests:
            if t.severity == "critical" or t.category not in seen_cats:
                quick_tests.append(t)
                seen_cats.add(t.category)
        tests = quick_tests

    return tests


# ── Static Test Evaluation ───────────────────────────────────────────────────


def evaluate_test_static(
    test: TestCase,
    agent_content: str,
) -> TestResult:
    """Évalue un test de manière statique (analyse du contenu de l'agent).

    Pour les tests dynamiques (envoi de prompt réel), il faut un LLM.
    Cette version statique vérifie que l'agent a les éléments dans son fichier.
    """
    result = TestResult(
        test_id=test.test_id,
        category=test.category,
        name=test.name,
    )
    content_lower = agent_content.lower()

    # Check expected traits in agent content
    found_expected = 0
    total_expected = len(test.expected_traits) or 1
    for trait in test.expected_traits:
        if trait.lower() in content_lower:
            found_expected += 1
            result.evidence.append(f"Found: '{trait}'")
        else:
            result.evidence.append(f"Missing: '{trait}'")

    # Check forbidden traits
    found_forbidden = 0
    for trait in test.forbidden_traits:
        if trait.lower() in content_lower:
            found_forbidden += 1
            result.evidence.append(f"Forbidden found: '{trait}'")

    # Compute score
    expected_score = found_expected / total_expected
    forbidden_penalty = min(found_forbidden * 0.3, 1.0)
    result.score = max(0.0, expected_score - forbidden_penalty)
    result.passed = result.score >= 0.5

    # Feedback
    if not result.passed:
        missing = [t for t in test.expected_traits if t.lower() not in content_lower]
        if missing:
            result.feedback = f"Agent manque les éléments suivants: {', '.join(missing)}"
        if found_forbidden > 0:
            result.feedback += " Contient des éléments interdits."
    else:
        result.details = f"Score {result.score:.2f} — {found_expected}/{total_expected} traits trouvés"

    return result


# ── Suite Runner ─────────────────────────────────────────────────────────────


def run_test_suite(
    agent_path: Path,
    suite_type: str = "full",
    project_root: Path | None = None,
) -> TestSuiteResult:
    """Lance une suite de tests sur un agent."""
    agent_info = parse_agent_for_tests(agent_path)
    if "error" in agent_info:
        return TestSuiteResult(
            agent_name=agent_path.stem,
            agent_file=str(agent_path),
            suite_type=suite_type,
            recommendations=[agent_info["error"]],
        )

    content = agent_path.read_text(encoding="utf-8")
    tests = generate_test_suite(agent_info, suite_type)
    results: list[TestResult] = []

    for test in tests:
        result = evaluate_test_static(test, content)
        results.append(result)

    # Aggregate
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    avg_score = sum(r.score for r in results) / total if total else 0.0

    # Grade
    if avg_score >= 0.9:
        grade = "A"
    elif avg_score >= 0.75:
        grade = "B"
    elif avg_score >= 0.6:
        grade = "C"
    elif avg_score >= 0.4:
        grade = "D"
    else:
        grade = "F"

    # Recommendations
    recs: list[str] = []
    category_scores: dict[str, list[float]] = {}
    for r in results:
        cat_list = category_scores.setdefault(r.category, [])
        cat_list.append(r.score)

    for cat, scores in category_scores.items():
        cat_avg = sum(scores) / len(scores)
        if cat_avg < 0.5:
            recs.append(f"⚠️ Catégorie '{cat}' faible ({cat_avg:.2f}) — renforcer dans l'agent")

    failed_critical = [r for r in results if not r.passed and r.category in ("persona", "tools")]
    for r in failed_critical:
        recs.append(f"❌ Test critique échoué: {r.name} — {r.feedback}")

    suite_result = TestSuiteResult(
        suite_id=f"suite-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        agent_name=agent_info.get("persona_name", agent_path.stem),
        agent_file=str(agent_path),
        timestamp=datetime.now().isoformat(),
        suite_type=suite_type,
        total_tests=total,
        passed=passed,
        failed=failed,
        score=round(avg_score, 3),
        grade=grade,
        results=[asdict(r) for r in results],
        recommendations=recs,
    )

    # Save
    if project_root:
        test_dir = project_root / TEST_DIR
        test_dir.mkdir(parents=True, exist_ok=True)
        # Report
        report_file = test_dir / f"{agent_path.stem}-{suite_result.suite_id}.json"
        report_file.write_text(
            json.dumps(asdict(suite_result), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # History append
        history_path = test_dir / HISTORY_FILE
        with history_path.open("a", encoding="utf-8") as f:
            entry = {
                "suite_id": suite_result.suite_id,
                "agent": suite_result.agent_name,
                "score": suite_result.score,
                "grade": suite_result.grade,
                "passed": passed,
                "failed": failed,
                "timestamp": suite_result.timestamp,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return suite_result


def run_benchmark(
    agent_paths: list[Path],
    suite_type: str = "full",
    project_root: Path | None = None,
) -> BenchResult:
    """Benchmark comparatif entre plusieurs agents."""
    bench = BenchResult(
        bench_id=f"bench-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime.now().isoformat(),
    )

    for ap in agent_paths:
        suite_result = run_test_suite(ap, suite_type, project_root)
        bench.agents.append(suite_result.agent_name)
        bench.scores[suite_result.agent_name] = suite_result.score

        # Per-category breakdown
        cat_scores: dict[str, list[float]] = {}
        for r in suite_result.results:
            cat_list = cat_scores.setdefault(r.get("category", ""), [])
            cat_list.append(r.get("score", 0.0))
        bench.category_scores[suite_result.agent_name] = {
            cat: round(sum(s) / len(s), 3) for cat, s in cat_scores.items()
        }

    if bench.scores:
        bench.winner = max(bench.scores, key=bench.scores.get)
        sorted_agents = sorted(bench.scores.items(), key=lambda x: x[1], reverse=True)
        bench.analysis = " > ".join(
            f"{name} ({score:.2f})" for name, score in sorted_agents
        )

    # Save
    if project_root:
        test_dir = project_root / TEST_DIR
        test_dir.mkdir(parents=True, exist_ok=True)
        bench_file = test_dir / f"{bench.bench_id}.json"
        bench_file.write_text(
            json.dumps(asdict(bench), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return bench


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_agent_test(
    agent_file: str,
    suite_type: str = "full",
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: lance une suite de tests comportementaux sur un agent.

    Args:
        agent_file: Chemin vers le fichier agent (.md).
        suite_type: Type de suite — "full" ou "quick".
        project_root: Racine du projet.

    Returns:
        TestSuiteResult sérialisé.
    """
    root = Path(project_root).resolve()
    agent_path = root / agent_file if not Path(agent_file).is_absolute() else Path(agent_file)
    result = run_test_suite(agent_path, suite_type, root)
    return asdict(result)


def mcp_agent_bench(
    agent_files: list[str],
    suite_type: str = "full",
    project_root: str = ".",
) -> dict[str, Any]:
    """MCP tool: benchmark comparatif entre agents.

    Args:
        agent_files: Liste de chemins vers les fichiers agents.
        suite_type: Type de suite — "full" ou "quick".
        project_root: Racine du projet.

    Returns:
        BenchResult sérialisé.
    """
    root = Path(project_root).resolve()
    paths = [
        root / f if not Path(f).is_absolute() else Path(f)
        for f in agent_files
    ]
    result = run_benchmark(paths, suite_type, root)
    return asdict(result)


def mcp_agent_test_history(
    last: int = 10,
    project_root: str = ".",
) -> list[dict[str, Any]]:
    """MCP tool: historique des derniers résultats de tests.

    Args:
        last: Nombre de résultats récents à retourner.
        project_root: Racine du projet.

    Returns:
        Liste des entrées d'historique.
    """
    root = Path(project_root).resolve()
    history_path = root / TEST_DIR / HISTORY_FILE
    if not history_path.exists():
        return []
    lines = history_path.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines[-last:]:
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# ── CLI Commands ─────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_path = root / args.agent
    result = run_test_suite(agent_path, args.suite, root)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        grade_colors = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}
        gc = grade_colors.get(result.grade, "⚪")
        print(f"\n  {gc} Agent Test: {result.agent_name} — Grade {result.grade} ({result.score:.2f})")
        print(f"  Suite: {result.suite_type} | {result.passed}/{result.total_tests} passed\n")

        # Category breakdown
        cat_results: dict[str, list[dict]] = {}
        for r in result.results:
            cat_list = cat_results.setdefault(r["category"], [])
            cat_list.append(r)

        for cat, rs in cat_results.items():
            cat_passed = sum(1 for r in rs if r["passed"])
            cat_icon = "✅" if cat_passed == len(rs) else ("⚠️" if cat_passed > 0 else "❌")
            print(f"  {cat_icon} {cat.upper()} ({cat_passed}/{len(rs)})")
            for r in rs:
                icon = "✅" if r["passed"] else "❌"
                print(f"    {icon} {r['name']} — {r['score']:.2f}")
                if r.get("feedback"):
                    print(f"       💬 {r['feedback']}")

        if result.recommendations:
            print("\n  📋 Recommandations:")
            for rec in result.recommendations:
                print(f"    {rec}")

    return 0 if result.grade in ("A", "B") else 1


def cmd_bench(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    agent_names = [a.strip() for a in args.agents.split(",") if a.strip()]

    # Find agent files
    agent_paths: list[Path] = []
    for name in agent_names:
        # Search in archetypes and _bmad
        candidates = list(root.rglob(f"**/{name}.md"))
        agent_dirs = [c for c in candidates if "agents" in str(c) and ".proposed" not in str(c)]
        if agent_dirs:
            agent_paths.append(agent_dirs[0])
        else:
            _log.warning("Agent file not found: %s", name)

    if len(agent_paths) < 2:
        print("❌ Need at least 2 agent files for benchmark")
        return 1

    result = run_benchmark(agent_paths, args.suite, root)

    if args.json:
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    else:
        print(f"\n  🏆 Benchmark: {result.bench_id}")
        print(f"  {result.analysis}\n")
        for agent_name in result.agents:
            score = result.scores.get(agent_name, 0)
            winner_marker = " 👑" if agent_name == result.winner else ""
            print(f"  {agent_name}: {score:.2f}{winner_marker}")
            cats = result.category_scores.get(agent_name, {})
            for cat, cat_score in cats.items():
                bar_len = int(cat_score * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                print(f"    {cat:12s} {bar} {cat_score:.2f}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = Path(args.project_root).resolve()
    entries = mcp_agent_test_history(args.last, str(root))

    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        if not entries:
            print("  Aucun résultat de test enregistré.")
            return 0
        print(f"\n  📊 Derniers {len(entries)} résultats:\n")
        for e in reversed(entries):
            gc = {"A": "🟢", "B": "🔵", "C": "🟡", "D": "🟠", "F": "🔴"}.get(e.get("grade", "?"), "⚪")
            print(f"  {gc} {e.get('agent', '?')}: {e.get('grade', '?')} ({e.get('score', 0):.2f}) "
                  f"— {e.get('passed', 0)}/{e.get('passed', 0) + e.get('failed', 0)} "
                  f"[{e.get('timestamp', '')[:16]}]")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    categories = {
        "persona": "Cohérence de l'identité et de la persona",
        "tools": "Maîtrise des outils MCP et capabilities",
        "boundary": "Respect des limites de scope et d'escalation",
        "handoff": "Qualité des outputs inter-agents",
        "failure": "Gestion gracieuse des pannes et erreurs",
        "vision": "Auto-évaluation visuelle et itération",
    }
    if args.json:
        print(json.dumps(categories, indent=2, ensure_ascii=False))
    else:
        print("\n  📋 Catégories de tests comportementaux:\n")
        for cat, desc in categories.items():
            print(f"    {cat:12s} — {desc}")
        print("\n  Suites disponibles: full, quick")
    return 0


# ── Main ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agent-test",
        description="Behavioral Test Framework for Grimoire Agents",
    )
    p.add_argument("--project-root", default=".", help="Project root directory")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    sub = p.add_subparsers(dest="command")

    # run
    r = sub.add_parser("run", help="Run behavioral tests on an agent")
    r.add_argument("--agent", required=True, help="Path to agent file (relative to project root)")
    r.add_argument("--suite", default="full", choices=["full", "quick"], help="Test suite type")
    r.set_defaults(func=cmd_run)

    # bench
    b = sub.add_parser("bench", help="Benchmark compare agents")
    b.add_argument("--agents", required=True, help="Comma-separated agent names")
    b.add_argument("--suite", default="full", choices=["full", "quick"], help="Test suite type")
    b.set_defaults(func=cmd_bench)

    # report
    rp = sub.add_parser("report", help="Show recent test results")
    rp.add_argument("--last", type=int, default=10, help="Number of recent results")
    rp.set_defaults(func=cmd_report)

    # list
    ls = sub.add_parser("list", help="List test categories and suites")
    ls.set_defaults(func=cmd_list)

    return p


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    if not hasattr(args, "func"):
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
