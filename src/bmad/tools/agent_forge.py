"""Agent Forge — intelligent BMAD agent scaffold generator.

Generates a proposed agent file from a textual description, gap analysis
in shared-context, or trace failure patterns.  The output is a
``.proposed.md`` file for human review before installation.

Usage::

    from bmad.tools.agent_forge import AgentForge

    forge = AgentForge(Path("."))
    proposal = forge.run(description="I need an agent for DB migrations")
    print(proposal.agent_tag, proposal.domain_key)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bmad.tools._common import BmadTool

# ── Domain Taxonomy ───────────────────────────────────────────────────────────

DOMAIN_TAXONOMY: dict[str, dict[str, Any]] = {
    "database": {
        "icon": "🗄️", "tag_prefix": "db",
        "keywords": ["database", "db", "sql", "migration", "schema", "postgres",
                      "mysql", "mongodb", "redis", "orm"],
        "role": "Database & Migration Specialist",
    },
    "security": {
        "icon": "🛡️", "tag_prefix": "sec",
        "keywords": ["security", "sécurité", "vulnérabilité", "audit", "secrets",
                      "cve", "owasp", "rbac", "auth"],
        "role": "Security & Hardening Specialist",
    },
    "frontend": {
        "icon": "🎨", "tag_prefix": "ui",
        "keywords": ["frontend", "ui", "ux", "react", "vue", "next", "angular",
                      "css", "html", "component", "accessibility"],
        "role": "Frontend & UI Specialist",
    },
    "api": {
        "icon": "🔌", "tag_prefix": "api",
        "keywords": ["api", "rest", "graphql", "grpc", "endpoint", "swagger",
                      "openapi", "webhook"],
        "role": "API Design & Integration Specialist",
    },
    "testing": {
        "icon": "🧪", "tag_prefix": "qa",
        "keywords": ["test", "qa", "quality", "coverage", "e2e", "tdd", "bdd",
                      "regression"],
        "role": "QA & Testing Specialist",
    },
    "devops": {
        "icon": "⚙️", "tag_prefix": "ops",
        "keywords": ["ci", "cd", "pipeline", "deploy", "release", "automation",
                      "build"],
        "role": "CI/CD & Automation Specialist",
    },
    "monitoring": {
        "icon": "📡", "tag_prefix": "obs",
        "keywords": ["monitoring", "observability", "metrics", "logs", "traces",
                      "alerts", "grafana", "prometheus"],
        "role": "Observability & Monitoring Specialist",
    },
    "data": {
        "icon": "📊", "tag_prefix": "data",
        "keywords": ["data", "pipeline", "etl", "analytics", "ml", "dbt",
                      "spark", "dataset"],
        "role": "Data Pipeline & Analytics Specialist",
    },
    "documentation": {
        "icon": "📝", "tag_prefix": "doc",
        "keywords": ["documentation", "doc", "readme", "wiki", "guide",
                      "tutorial", "api-doc"],
        "role": "Documentation & Knowledge Specialist",
    },
    "performance": {
        "icon": "⚡", "tag_prefix": "perf",
        "keywords": ["performance", "optimization", "latency", "throughput",
                      "profiling", "benchmark"],
        "role": "Performance & Profiling Specialist",
    },
}

DEFAULT_DOMAIN: dict[str, Any] = {
    "icon": "🤖", "tag_prefix": "agent",
    "keywords": [],
    "role": "Custom Domain Specialist",
}


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class AgentProposal:
    """A proposed new agent."""

    source: str  # "description", "gap", "trace"
    description: str
    domain_key: str
    agent_name: str
    agent_tag: str
    agent_icon: str
    agent_role: str
    overlap: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "description": self.description,
            "domain_key": self.domain_key,
            "agent_name": self.agent_name,
            "agent_tag": self.agent_tag,
            "agent_icon": self.agent_icon,
            "agent_role": self.agent_role,
            "overlap": self.overlap,
        }


# ── Domain Detection ─────────────────────────────────────────────────────────

def detect_domain(text: str) -> tuple[str, dict[str, Any]]:
    """Detect the domain from a text description."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for key, profile in DOMAIN_TAXONOMY.items():
        score = sum(2 if len(kw) > 5 else 1
                    for kw in profile["keywords"] if kw in lower)
        scores[key] = score

    best = max(scores, key=lambda k: scores[k]) if scores else "custom"
    if scores.get(best, 0) == 0:
        return "custom", DEFAULT_DOMAIN.copy()
    return best, DOMAIN_TAXONOMY[best].copy()


def extract_agent_name(text: str, domain_key: str,
                       profile: dict[str, Any]) -> tuple[str, str]:
    """Extract (name, tag) from a textual description."""
    lower = text.lower()

    # Remove French contractions
    clean = re.sub(r"\b[ldsnmjc]['']", " ", lower)
    clean = re.sub(r"\s+", " ", clean).strip()

    stop = {"je", "tu", "il", "elle", "veux", "voudrais", "faut", "besoin",
            "un", "une", "des", "les", "le", "la", "du", "de", "pour", "par",
            "avec", "dans", "sur", "en", "et", "ou", "mais", "agent",
            "i", "want", "an", "the", "to", "for", "that", "which", "with"}

    # Try grammatical patterns
    subject = ""
    for pat in [r"(?:pour|gérer|manage|handle)\s+(.{3,30}?)(?:\s*$|[,.])",
                r"(?:agent|assistant)\s+(.{3,20})(?:\s*$|[,.])"]:
        m = re.search(pat, clean)
        if m:
            subject = m.group(1).strip()
            break
    if not subject:
        words = [w for w in clean.split() if len(w) > 2 and w not in stop]
        subject = " ".join(words[:3])

    parts = [w for w in subject.split() if len(w) > 1 and w not in stop]
    if not parts:
        parts = [domain_key]

    # ASCII transliteration
    accent_map = str.maketrans("àâäéèêëïîôùûüÿçñ", "aaaeeeeiioouuycn")

    def _ascii(w: str) -> str:
        return re.sub(r"[^a-z0-9]", "", w.lower().translate(accent_map))

    clean_parts = [_ascii(w) for w in parts if _ascii(w)][:3]
    prefix = profile.get("tag_prefix", "agent")
    tag = f"{prefix}-{'-'.join(clean_parts)}" if clean_parts else prefix
    name = "".join(w.capitalize() for w in clean_parts) if clean_parts else domain_key.capitalize()
    return name, tag


# ── Overlap Detection ────────────────────────────────────────────────────────

def find_existing_agents(project_root: Path) -> list[str]:
    """List IDs of existing agents."""
    agents: list[str] = []
    for d in [project_root / "_bmad/_config/agents",
              project_root / "_bmad/_config/custom",
              project_root / "_bmad/core/agents",
              project_root / "_bmad/bmm/agents"]:
        if d.exists():
            agents.extend(f.stem for f in d.glob("*.md")
                         if "template" not in f.name and "README" not in f.name)
    return agents


def check_overlap(tag: str, existing: list[str]) -> list[str]:
    """Detect potential overlaps with existing agents."""
    keywords = tag.lower().replace("-", " ").split()
    return [a for a in existing
            if any(kw in a.lower() for kw in keywords if len(kw) > 3)]


# ── Tool ──────────────────────────────────────────────────────────────────────

class AgentForge(BmadTool):
    """Agent scaffold generator."""

    def run(self, **kwargs: Any) -> AgentProposal:
        description = kwargs.get("description", "")
        domain_key, profile = detect_domain(description)
        name, tag = extract_agent_name(description, domain_key, profile)
        existing = find_existing_agents(self._project_root)
        overlap = check_overlap(tag, existing)

        return AgentProposal(
            source="description",
            description=description,
            domain_key=domain_key,
            agent_name=name,
            agent_tag=tag,
            agent_icon=profile.get("icon", "🤖"),
            agent_role=profile.get("role", "Custom Specialist"),
            overlap=overlap,
        )
