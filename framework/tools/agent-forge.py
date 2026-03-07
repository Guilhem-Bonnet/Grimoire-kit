#!/usr/bin/env python3
"""
BMAD Agent Forge — BM-52
=========================
Génère un scaffold d'agent BMAD rempli intelligemment depuis :
  - une description textuelle de besoin
  - des requêtes inter-agents non résolues (shared-context.md)
  - des patterns de failure sans agent propriétaire (BMAD_TRACE.md)

Le résultat est un .proposed.md à réviser, PAS un agent activé directement.
Chaîne : agent-forge → review humain → bmad-init.sh forge --install → Sentinel audit

Usage:
    python3 agent-forge.py --from "je veux un agent pour les migrations DB"
    python3 agent-forge.py --from-gap --shared-context path/to/shared-context.md
    python3 agent-forge.py --from-trace --trace _bmad-output/BMAD_TRACE.md
    python3 agent-forge.py --list-proposals
    python3 agent-forge.py --install agent-db-migrator  (appelé par bmad-init.sh)
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.agent_forge")

# ── Taxonomie domaine → profil agent ─────────────────────────────────────────
# Chaque domaine mappe vers : icône, outil CLI principal, tools_list, pattern de prompts

DOMAIN_TAXONOMY: dict[str, dict] = {
    "database": {
        "icon": "🗄️", "tag_prefix": "db",
        "tools": ["psql", "mysql", "sqlite3", "pg_dump", "flyway", "liquibase", "alembic"],
        "keywords": ["database", "db", "sql", "migration", "schema", "postgres", "mysql", "mongodb", "redis", "requête", "query", "orm"],
        "role": "Database & Migration Specialist",
        "domain_word": "base de données",
        "prompt_patterns": ["migration", "schema-audit", "query-optimize", "backup-restore"],
        "cc_check": "db connection check",
    },
    "security": {
        "icon": "🛡️", "tag_prefix": "sec",
        "tools": ["trivy", "grype", "snyk", "semgrep", "bandit", "gitleaks", "trufflehog"],
        "keywords": ["security", "sécurité", "vulnérabilité", "audit", "secrets", "cve", "hardening", "pentest", "owasp", "rbac", "auth", "authentification", "autorisation"],
        "role": "Security & Hardening Specialist",
        "domain_word": "sécurité",
        "prompt_patterns": ["vulnerability-scan", "secrets-audit", "access-review", "hardening-check"],
        "cc_check": "security scan",
    },
    "frontend": {
        "icon": "🎨", "tag_prefix": "ui",
        "tools": ["node", "pnpm", "npm", "vite", "storybook", "playwright", "cypress"],
        "keywords": ["frontend", "ui", "ux", "react", "vue", "next", "angular", "css", "html", "component", "composant", "accessibilité", "accessibility", "responsive"],
        "role": "Frontend & UI Specialist",
        "domain_word": "interface",
        "prompt_patterns": ["component-review", "accessibility-audit", "perf-bundle", "visual-test"],
        "cc_check": "npx tsc --noEmit && npm test",
    },
    "api": {
        "icon": "🔌", "tag_prefix": "api",
        "tools": ["curl", "httpie", "postman", "swagger", "openapi-generator", "grpc"],
        "keywords": ["api", "rest", "graphql", "grpc", "endpoint", "route", "swagger", "openapi", "webhook", "contrat", "contract", "interface"],
        "role": "API Design & Integration Specialist",
        "domain_word": "API",
        "prompt_patterns": ["contract-check", "endpoint-audit", "breaking-change", "load-test"],
        "cc_check": "API health check",
    },
    "testing": {
        "icon": "🧪", "tag_prefix": "qa",
        "tools": ["pytest", "jest", "vitest", "playwright", "k6", "go test", "rspec"],
        "keywords": ["test", "qa", "qualité", "quality", "coverage", "couverture", "e2e", "unitaire", "integration", "bdd", "tdd", "regression"],
        "role": "QA & Testing Specialist",
        "domain_word": "qualité et tests",
        "prompt_patterns": ["coverage-report", "flaky-test-hunt", "e2e-suite", "perf-test"],
        "cc_check": "test suite pass rate",
    },
    "data": {
        "icon": "📊", "tag_prefix": "data",
        "tools": ["dbt", "airflow", "spark", "pandas", "polars", "dask", "great-expectations"],
        "keywords": ["data", "pipeline", "etl", "elt", "analytics", "ml", "machine learning", "dbt", "airflow", "spark", "feature", "dataset", "modèle"],
        "role": "Data Pipeline & Analytics Specialist",
        "domain_word": "données",
        "prompt_patterns": ["pipeline-audit", "data-quality", "schema-drift", "backfill"],
        "cc_check": "dbt test && dbt compile",
    },
    "devops": {
        "icon": "⚙️", "tag_prefix": "ops",
        "tools": ["github-actions", "gitlab-ci", "jenkins", "taskfile", "make", "earthly"],
        "keywords": ["ci", "cd", "pipeline", "cicd", "deploy", "déploiement", "release", "workflow", "automation", "build", "artifact", "registry"],
        "role": "CI/CD & Automation Specialist",
        "domain_word": "CI/CD",
        "prompt_patterns": ["pipeline-review", "deploy-gate", "release-notes", "artifact-audit"],
        "cc_check": "CI pipeline pass",
    },
    "monitoring": {
        "icon": "📡", "tag_prefix": "obs",
        "tools": ["prometheus", "grafana", "alertmanager", "loki", "jaeger", "opentelemetry"],
        "keywords": ["monitoring", "observabilité", "observability", "métriques", "metrics", "logs", "traces", "alertes", "alerts", "slo", "sla", "grafana", "prometheus"],
        "role": "Observability & Monitoring Specialist",
        "domain_word": "observabilité",
        "prompt_patterns": ["alert-review", "dashboard-audit", "slo-check", "log-analysis"],
        "cc_check": "Prometheus/Grafana health check",
    },
    "networking": {
        "icon": "🌐", "tag_prefix": "net",
        "tools": ["nmap", "tcpdump", "iptables", "wireguard", "nginx", "traefik", "envoy"],
        "keywords": ["réseau", "network", "dns", "proxy", "load balancer", "firewall", "vpn", "tls", "certificat", "certificate", "nginx", "traefik", "ingress"],
        "role": "Network & Infrastructure Specialist",
        "domain_word": "réseau",
        "prompt_patterns": ["connectivity-check", "cert-audit", "firewall-review", "traffic-analysis"],
        "cc_check": "network connectivity",
    },
    "storage": {
        "icon": "💾", "tag_prefix": "storage",
        "tools": ["restic", "rclone", "s3cmd", "aws-cli", "minio"],
        "keywords": ["stockage", "storage", "backup", "s3", "blob", "filesystem", "volume", "persistent", "objectstorage", "minio", "restic"],
        "role": "Storage & Backup Specialist",
        "domain_word": "stockage",
        "prompt_patterns": ["backup-verify", "storage-audit", "retention-check", "restore-test"],
        "cc_check": "backup integrity check",
    },
    "documentation": {
        "icon": "📝", "tag_prefix": "doc",
        "tools": ["mkdocs", "docusaurus", "pandoc", "sphinx", "vale"],
        "keywords": ["documentation", "doc", "readme", "wiki", "guide", "tutoriel", "tutorial", "rédaction", "writing", "api-doc"],
        "role": "Documentation & Knowledge Specialist",
        "domain_word": "documentation",
        "prompt_patterns": ["doc-audit", "coverage-check", "stale-detection", "api-doc-gen"],
        "cc_check": "documentation build",
    },
    "performance": {
        "icon": "⚡", "tag_prefix": "perf",
        "tools": ["k6", "wrk", "ab", "pprof", "py-spy", "clinic", "flamegraph"],
        "keywords": ["performance", "perf", "optimisation", "optimization", "latence", "latency", "throughput", "profiling", "benchmark", "slow", "lent"],
        "role": "Performance & Profiling Specialist",
        "domain_word": "performance",
        "prompt_patterns": ["load-test", "profile-analysis", "bottleneck-hunt", "cache-review"],
        "cc_check": "performance baseline",
    },
}

# Domaine par défaut si aucune correspondance
DEFAULT_DOMAIN = {
    "icon": "🤖", "tag_prefix": "agent",
    "tools": [],
    "keywords": [],
    "role": "Custom Domain Specialist",
    "domain_word": "domaine",
    "prompt_patterns": ["analyze", "execute", "report", "validate"],
    "cc_check": "domain-specific check",
}


# ── Structures ────────────────────────────────────────────────────────────────

@dataclass
class AgentProposal:
    """Proposal d'un nouvel agent à créer."""
    source: str          # "description" | "gap" | "trace"
    need_description: str
    domain_key: str
    agent_name: str
    agent_tag: str
    agent_icon: str
    agent_role: str
    domain: str
    domain_word: str
    tools: list[str]
    prompt_patterns: list[str]
    cc_check: str
    project_name: str = "{{project_name}}"
    existing_overlap: list[str] = field(default_factory=list)
    inter_agent_source: str | None = None   # agent qui a fait la requête
    trace_failure_pattern: str | None = None


@dataclass
class GapRequest:
    """Requête inter-agent dans shared-context.md."""
    source_agent: str
    target_description: str
    full_line: str


# ── Détection de domaine ──────────────────────────────────────────────────────

def detect_domain(text: str) -> tuple[str, dict]:
    """
    Détecte le domaine depuis un texte libre.
    Retourne (domain_key, domain_profile).
    Utilise un score de correspondance par keyword.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}

    for domain_key, profile in DOMAIN_TAXONOMY.items():
        score = 0
        for kw in profile["keywords"]:
            if kw in text_lower:
                score += 2 if len(kw) > 5 else 1
        scores[domain_key] = score

    best_domain = max(scores, key=lambda k: scores[k]) if scores else "custom"
    best_score = scores.get(best_domain, 0)

    if best_score == 0:
        return "custom", DEFAULT_DOMAIN.copy()

    return best_domain, DOMAIN_TAXONOMY[best_domain].copy()


def extract_agent_name(text: str, domain_key: str, domain_profile: dict) -> tuple[str, str]:
    """
    Extruit un nom + tag d'agent depuis la description.
    Returns (agent_name, agent_tag).

    Stratégie :
      1. Chercher les mots-clés du domaine présents dans le texte → tag direct
      2. Sinon, extraire le sujet via des patterns grammaticaux FR/EN
      3. Nettoyer les articles/prépositions/contractions françaises
      4. Limiter à 2-3 mots significatifs pour un tag concis
    """
    text_lower = text.lower()

    # ── Contractions françaises : l'audit → audit, d'agents → agents, s'occuper → occuper
    text_clean = re.sub(r"\b[ldsnmjc]['']", " ", text_lower)
    text_clean = re.sub(r"\s+", " ", text_clean).strip()

    # ── Stop words FR + EN (articles, prépositions, pronoms)
    STOP_WORDS = {  # noqa: N806
        "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
        "veux", "voudrais", "faut", "besoin", "avoir", "être", "faire",
        "un", "une", "des", "les", "le", "la", "du", "de", "au", "aux",
        "ce", "cette", "ces", "mon", "mes", "son", "ses", "nos", "vos",
        "qui", "que", "quoi", "dont", "où",
        "pour", "par", "avec", "dans", "sur", "sous", "entre", "vers", "chez",
        "en", "et", "ou", "mais", "donc", "car", "ni",
        "va", "est", "a", "sont", "ont", "fait",
        "agent", "assistant", "outil", "tool",
        "i", "want", "an", "the", "to", "for", "that", "which", "with",
    }

    # ── 1) Chercher les keywords du domaine présents dans le texte
    domain_keywords_found = []
    for kw in domain_profile.get("keywords", []):
        if kw in text_clean and len(kw) > 2 and kw not in STOP_WORDS:
            domain_keywords_found.append(kw)

    # ── 2) Extraire le sujet via patterns grammaticaux
    extracted_subject = ""
    patterns = [
        r"(?:pour|gérer|gestion de?|s occuper de?|handle|manage|dealing with)\s+(.{3,40}?)(?:\s*$|[,.])",
        r"(?:agent|assistant)\s+(.{3,20})(?:\s*$|[,.])",
    ]
    for pat in patterns:
        m = re.search(pat, text_clean)
        if m:
            extracted_subject = m.group(1).strip()
            break

    # ── 3) Si aucun match, prendre les mots significatifs restants
    if not extracted_subject:
        words = [w for w in re.split(r'\s+', text_clean) if len(w) > 2 and w not in STOP_WORDS]
        extracted_subject = " ".join(words[:4])

    # ── 4) Nettoyer le sujet : retirer stop words en tête et queue
    subject_words = [w for w in re.split(r'\s+', extracted_subject) if len(w) > 1 and w not in STOP_WORDS]

    # ── 5) Privilégier les keywords du domaine trouvés pour un tag concis
    if domain_keywords_found:
        # Garder les keywords + mots du sujet qui ne sont pas des keywords
        tag_words = []
        for kw in domain_keywords_found[:2]:
            # Normaliser le keyword pour éviter les doublons (racine commune)
            kw_norm = re.sub(r"[^a-z0-9]", "", kw.lower())
            if not any(kw_norm in re.sub(r"[^a-z0-9]", "", tw.lower()) or
                       re.sub(r"[^a-z0-9]", "", tw.lower()) in kw_norm
                       for tw in tag_words):
                tag_words.append(kw)
        for sw in subject_words:
            sw_norm = re.sub(r"[^a-z0-9]", "", sw.lower())
            if (not any(sw_norm in re.sub(r"[^a-z0-9]", "", tw.lower()) or
                        re.sub(r"[^a-z0-9]", "", tw.lower()) in sw_norm
                        for tw in tag_words)
                    and sw not in STOP_WORDS and len(tag_words) < 3):
                tag_words.append(sw)
        subject_words = tag_words if tag_words else subject_words

    # Dédupliquer par racine (ex: "migration" et "migrations")
    deduped: list[str] = []
    for w in subject_words:
        w_norm = re.sub(r"[^a-z0-9]", "", w.lower())
        if not any(w_norm.rstrip("s") == re.sub(r"[^a-z0-9]", "", d.lower()).rstrip("s")
                   for d in deduped):
            deduped.append(w)
    subject_words = deduped[:3]

    if not subject_words:
        subject_words = [domain_key]

    # ── Translittérer les accents (sécurité → securite) avant de construire le tag
    _ACCENT_MAP = str.maketrans(  # noqa: N806
        "àâäéèêëïîôùûüÿçñ",
        "aaaeeeeiioouuycn",
    )

    def _ascii(word: str) -> str:
        return word.lower().translate(_ACCENT_MAP)

    # ── Construire tag (lowercase-hyphen, max 25 chars, coupe sur un mot entier)
    clean_parts = [re.sub(r"[^a-z0-9]", "", _ascii(w)) for w in subject_words]
    clean_parts = [p for p in clean_parts if p]
    # Assembler en respectant la limite sans couper un mot
    parts_for_tag: list[str] = []
    tag_len = 0
    for p in clean_parts:
        needed = len(p) + (1 if parts_for_tag else 0)  # +1 for hyphen
        if tag_len + needed <= 25:
            parts_for_tag.append(p)
            tag_len += needed
        else:
            break
    clean_tag = "-".join(parts_for_tag) if parts_for_tag else (clean_parts[0][:25] if clean_parts else domain_key)

    prefix = domain_profile.get("tag_prefix", "agent")
    tag = f"{prefix}-{clean_tag}" if clean_tag and clean_tag != prefix else prefix

    # ── Construire nom affiché (PascalCase)
    name = "".join(w.capitalize() for w in clean_parts[:3]) if clean_parts else domain_key.capitalize()

    return name, tag


# ── Scanner des gaps ──────────────────────────────────────────────────────────

def scan_gaps_from_shared_context(shared_context_path: Path) -> list[GapRequest]:
    """
    Scanne shared-context.md pour les requêtes inter-agents non résolues.
    Pattern : - [ ] [source_agent→?] description
    """
    gaps: list[GapRequest] = []
    if not shared_context_path.exists():
        return gaps

    inter_agent_section = False
    # Patterns de requêtes non satisfaites (cible = ?, ou cible inconnue)
    req_pattern = re.compile(
        r"-\s*\[ \]\s*\[([^\]→]+)→([^\]]*)\]\s*(.+)"
    )

    with shared_context_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip()
            if "## Requêtes inter-agents" in line or "## Inter-Agent Requests" in line:
                inter_agent_section = True
                continue
            if inter_agent_section and line.startswith("##"):
                inter_agent_section = False
                continue
            if inter_agent_section:
                m = req_pattern.match(line.strip())
                if m:
                    source = m.group(1).strip()
                    target = m.group(2).strip()
                    description = m.group(3).strip()
                    # C'est un gap si la cible est "?" ou vide ou "unknown"
                    if not target or target in ("?", "unknown", "?"):
                        gaps.append(GapRequest(
                            source_agent=source,
                            target_description=description,
                            full_line=line,
                        ))

    return gaps


def scan_gaps_from_trace(trace_path: Path, known_agents: list[str]) -> list[str]:
    """
    Scanne BMAD_TRACE.md pour des patterns de failure récurrents
    qui ne correspondent à aucun agent connu.
    Retourne une liste de descriptions de besoins détectés.
    """
    if not trace_path.exists():
        return []

    failure_patterns: dict[str, int] = {}
    failure_re = re.compile(r"\[FAILURE\].*?([a-zA-Z][a-zA-Z0-9_-]+)", re.IGNORECASE)

    with trace_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[FAILURE]" in line or "[ÉCHEC]" in line:
                m = failure_re.search(line)
                if m:
                    subject = m.group(1).lower()
                    failure_patterns[subject] = failure_patterns.get(subject, 0) + 1

    # Garder uniquement les failures récurrentes (≥ 3) sans agent existant
    gaps = []
    for subject, count in failure_patterns.items():
        if count >= 3:
            # Vérifier si un agent existant couvre ce domaine
            covered = any(
                subject in agent_id.lower() or agent_id.lower() in subject
                for agent_id in known_agents
            )
            if not covered:
                gaps.append(f"Failures récurrentes sans agent : {subject} ({count}x)")

    return gaps


# ── Scanner des agents existants ──────────────────────────────────────────────

def list_existing_agents(agents_dir: Path) -> list[str]:
    """Liste les IDs des agents existants."""
    agents = []
    if not agents_dir.exists():
        return agents
    for f in agents_dir.glob("*.md"):
        if not f.name.startswith("custom-agent"):
            agents.append(f.stem)
    return agents


def check_overlap(tag: str, domain_key: str, existing_agents: list[str]) -> list[str]:
    """Détecte les chevauchements potentiels avec des agents existants."""
    overlaps = []
    tag_keywords = tag.lower().replace("-", " ").split()
    for existing in existing_agents:
        existing_lower = existing.lower().replace("-", " ")
        # Vérifier chevauchement par tag ou domaine
        if any(kw in existing_lower for kw in tag_keywords if len(kw) > 3):
            overlaps.append(existing)
    return overlaps


# ── Lecture project-context ───────────────────────────────────────────────────

def read_project_context(ctx_path: Path) -> dict:
    """Lit project-context.yaml (parsing minimal sans PyYAML)."""
    ctx: dict = {}
    if not ctx_path.exists():
        return ctx
    try:
        with ctx_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and value:
                    ctx[key] = value
    except OSError as _exc:
        _log.debug("OSError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    return ctx


def read_active_dna(archetypes_dir: Path) -> list[str]:
    """Lit les acceptance_criteria des DNA actifs pour les hériter dans l'agent."""
    ac_items: list[str] = []
    for dna_file in archetypes_dir.glob("**/*.dna.yaml"):
        try:
            with dna_file.open(encoding="utf-8", errors="replace") as f:
                content = f.read()
            # Extraction minimale des descriptions d'AC
            for m in re.finditer(r"description:\s*['\"]([^'\"]+)['\"]", content):
                desc = m.group(1)
                if len(desc) > 10:
                    ac_items.append(desc)
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    return ac_items[:10]  # Max 10 pour ne pas surcharger


# ── Génération du template ────────────────────────────────────────────────────

AGENT_TEMPLATE = '''<!-- ARCHETYPE: {archetype} — Agent généré par agent-forge.py / BM-52
     SOURCE: {source} — {need_description}
     Réviser TOUS les placeholders [TODO] avant installation.
     Installer via : bash bmad-init.sh forge --install {agent_tag}
     Valider via   : Sentinel [AA] Audit Agent
-->
---
name: "{agent_tag}"
description: "{agent_role} — {agent_name}"
---

You must fully embody this agent\'s persona and follow all activation instructions exactly as specified. NEVER break character until given an exit command.

```xml
<agent id="{agent_tag}.agent.yaml" name="{agent_name}" title="{agent_role}" icon="{agent_icon}">
<activation critical="MANDATORY">
      <step n="1">Load persona from this current agent file (already in context)</step>
      <step n="2">⚙️ BASE PROTOCOL — Load and apply {{{{project-root}}}}/_bmad/_config/custom/agent-base.md with:
          AGENT_TAG={agent_tag} | AGENT_NAME={agent_name} | LEARNINGS_FILE={agent_tag} | DOMAIN_WORD={domain_word}
      </step>
      <step n="3">Remember: user\'s name is {{{{user_name}}}}</step>
      <step n="4">Show brief greeting using {{{{user_name}}}}, communicate in {{{{communication_language}}}}, display numbered menu</step>
      <step n="5">STOP and WAIT for user input</step>
      <step n="6">On user input: Number → process menu item[n] | Text → fuzzy match | No match → "Non reconnu"</step>
      <step n="7">When processing a menu item: extract attributes (workflow, exec, action) and follow handler instructions</step>

    <rules>
      <!-- BASE PROTOCOL rules inherited from agent-base.md -->
      <r>RAISONNEMENT : 1) IDENTIFIER le contexte/cible → 2) VÉRIFIER l\'état actuel → 3) EXÉCUTER l\'action → 4) VALIDER le résultat</r>
      <r>OUTILS REQUIS : {tools_list}</r>
      <!-- [TODO] Ajouter les guardrails spécifiques au domaine -->
      <r>INTER-AGENT : si un besoin hors scope est identifié, ajouter une requête dans shared-context.md section "## Requêtes inter-agents" au format "- [ ] [{agent_tag}→cible] description"</r>{overlap_rules}{inter_agent_rule}
    </rules>
</activation>

  <persona>
    <role>{agent_role}</role>
    <identity>{agent_name} est expert en {domain_word} pour le projet {project_name}.
    [TODO] Compléter : expertise spécifique au projet, outils/technologies maîtrisés, périmètre.
    Consulte shared-context.md pour le contexte complet du projet.</identity>
    <communication_style>Direct et factuel. Répond en {{{{communication_language}}}}. Chaque affirmation appuyée par une action concrète.
    [TODO] Affiner le style de communication (direct/analytique/pédagogue, longueur des réponses).</communication_style>
    <principles>
      <!-- [TODO] Remplacer par 3-5 principes SPÉCIFIQUES au domaine {domain_word} -->
      - Vérifier avant d\'agir — lire l\'état actuel avant toute modification
      - Écrire directement dans les fichiers — jamais proposer du code à copier-coller
      - Documenter chaque décision significative dans decisions-log.md
      - Respecter le périmètre — escalader si une requête dépasse {domain_word}
    </principles>
  </persona>

  <menu>
    <item cmd="MH or fuzzy match on menu or help">[MH] Afficher le Menu</item>
    <item cmd="CH or fuzzy match on chat">[CH] Discuter avec {agent_name}</item>
{menu_items}    <item cmd="PM or fuzzy match on party-mode" exec="{{{{project-root}}}}/_bmad/core/workflows/party-mode/workflow.md">[PM] Party Mode</item>
    <item cmd="DA or fuzzy match on exit, leave, goodbye or dismiss agent">[DA] Quitter</item>
  </menu>

  <prompts>
{prompts_section}
  </prompts>
</agent>
```
'''

MENU_ITEM_TEMPLATE = '    <item cmd="{cmd} or fuzzy match on {keyword}" action="#{action_id}">[{cmd}] [TODO] {label}</item>\n'

PROMPT_TEMPLATE = '''    <prompt id="{action_id}">
      {agent_name} entre en mode {label}.

      RAISONNEMENT :
      1. IDENTIFIER : [TODO] qu\'est-ce qu\'on analyse/exécute ?
      2. VÉRIFIER : [TODO] quel est l\'état actuel avant action ?
      3. EXÉCUTER : [TODO] quelle action concrète ?
      4. VALIDER : {cc_check}

      <!-- [TODO] Ajouter des exemples concrets tirés du projet -->
      <!-- <example>...</example> -->

      FORMAT DE SORTIE :
      [TODO] Décrire le format de sortie attendu (rapport, fichier, commande).
    </prompt>

'''


def generate_menu_and_prompts(profile: dict, agent_name: str, cc_check: str) -> tuple[str, str]:
    """Génère les items de menu et les prompts depuis les patterns du profil."""
    patterns = profile.get("prompt_patterns", ["analyze", "execute", "report"])
    menu_items = ""
    prompts = ""

    cmd_letters = ["AA", "BB", "CC", "DD", "EE"]

    for i, pattern in enumerate(patterns[:5]):
        cmd = cmd_letters[i]
        label = pattern.replace("-", " ").title()
        keyword = pattern.split("-")[0]
        action_id = pattern

        menu_items += MENU_ITEM_TEMPLATE.format(
            cmd=cmd, keyword=keyword, action_id=action_id, label=label
        )
        prompts += PROMPT_TEMPLATE.format(
            action_id=action_id,
            agent_name=agent_name,
            label=label,
            cc_check=cc_check,
        )

    return menu_items, prompts


def render_agent(proposal: AgentProposal, archetype: str = "custom") -> str:
    """Rend le template d'agent depuis un AgentProposal."""
    profile = DOMAIN_TAXONOMY.get(proposal.domain_key, DEFAULT_DOMAIN)

    tools_list = ", ".join(profile.get("tools", [])[:6]) or "[TODO] lister les outils requis"
    menu_items, prompts_section = generate_menu_and_prompts(
        profile, proposal.agent_name, proposal.cc_check
    )

    # Règle overlap
    overlap_rules = ""
    if proposal.existing_overlap:
        overlap_str = ", ".join(proposal.existing_overlap)
        overlap_rules = f'\n      <r>⚠️ SCOPE WARNING : chevauchement potentiel avec {overlap_str} — clarifier la frontière de responsabilité</r>'

    # Règle inter-agent si la forge vient d\'un gap
    inter_agent_rule = ""
    if proposal.inter_agent_source:
        inter_agent_rule = f'\n      <r>PROTOCOLE ENTRANT : reçoit les requêtes de {proposal.inter_agent_source} concernant {proposal.domain_word}</r>'

    return AGENT_TEMPLATE.format(
        archetype=archetype,
        source=proposal.source,
        need_description=proposal.need_description[:100],
        agent_tag=proposal.agent_tag,
        agent_name=proposal.agent_name,
        agent_role=proposal.agent_role,
        agent_icon=proposal.agent_icon,
        domain_word=proposal.domain_word,
        domain=proposal.domain_word,
        project_name=proposal.project_name,
        tools_list=tools_list,
        overlap_rules=overlap_rules,
        inter_agent_rule=inter_agent_rule,
        menu_items=menu_items,
        prompts_section=prompts_section,
    )


# ── Création du proposal ──────────────────────────────────────────────────────

def build_proposal_from_description(
    description: str,
    project_context: dict,
    existing_agents: list[str],
) -> AgentProposal:
    """Construit un AgentProposal depuis une description textuelle."""
    domain_key, domain_profile = detect_domain(description)
    agent_name, agent_tag = extract_agent_name(description, domain_key, domain_profile)
    overlap = check_overlap(agent_tag, domain_key, existing_agents)

    return AgentProposal(
        source="description",
        need_description=description,
        domain_key=domain_key,
        agent_name=agent_name,
        agent_tag=agent_tag,
        agent_icon=domain_profile["icon"],
        agent_role=domain_profile["role"],
        domain=domain_profile.get("domain_word", domain_key),
        domain_word=domain_profile.get("domain_word", domain_key),
        tools=domain_profile.get("tools", []),
        prompt_patterns=domain_profile.get("prompt_patterns", []),
        cc_check=domain_profile.get("cc_check", "domain check"),
        project_name=project_context.get("project_name", "{{project_name}}"),
        existing_overlap=overlap,
    )


def build_proposals_from_gaps(
    gaps: list[GapRequest],
    project_context: dict,
    existing_agents: list[str],
) -> list[AgentProposal]:
    """Construit des proposals depuis des gaps inter-agents."""
    proposals = []
    for gap in gaps:
        proposal = build_proposal_from_description(
            gap.target_description, project_context, existing_agents
        )
        proposal.source = "gap"
        proposal.inter_agent_source = gap.source_agent
        proposals.append(proposal)
    return proposals


def build_proposals_from_trace_gaps(
    trace_gaps: list[str],
    project_context: dict,
    existing_agents: list[str],
) -> list[AgentProposal]:
    """Construit des proposals depuis des gaps détectés dans BMAD_TRACE."""
    proposals = []
    for gap_description in trace_gaps:
        proposal = build_proposal_from_description(
            gap_description, project_context, existing_agents
        )
        proposal.source = "trace"
        proposal.trace_failure_pattern = gap_description
        proposals.append(proposal)
    return proposals


# ── Sauvegarde / installation ─────────────────────────────────────────────────

def save_proposal(proposal: AgentProposal, output_dir: Path, archetype: str = "custom") -> Path:
    """Sauvegarde le proposal dans le dossier de sortie."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"agent-{proposal.agent_tag}.proposed.md"
    out_path = output_dir / filename
    content = render_agent(proposal, archetype)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def install_proposal(
    proposal_name: str,
    proposals_dir: Path,
    agents_dir: Path,
    manifest_path: Path | None = None,
) -> None:
    """
    Déplace un .proposed.md vers le répertoire des agents et met à jour le manifest.
    Appelé par bmad-init.sh forge --install.
    """
    # Chercher le fichier proposal
    candidates = list(proposals_dir.glob(f"*{proposal_name}*.proposed.md"))
    if not candidates:
        candidates = list(proposals_dir.glob("*.proposed.md"))
        if not candidates:
            print(f"❌ Aucun proposal trouvé pour : {proposal_name}", file=sys.stderr)
            sys.exit(1)
        # Sélectionner le plus proche
        candidates = [c for c in candidates if proposal_name.lower() in c.stem.lower()]
        if not candidates:
            print(f"❌ Proposal introuvable : {proposal_name}", file=sys.stderr)
            print(f"   Proposals disponibles : {[f.name for f in proposals_dir.glob('*.proposed.md')]}")
            sys.exit(1)

    proposal_file = candidates[0]
    agents_dir.mkdir(parents=True, exist_ok=True)

    # Renommer .proposed.md → .md
    agent_tag = re.sub(r"^agent-", "", proposal_file.stem.replace(".proposed", ""))
    dest = agents_dir / f"{agent_tag}.md"

    if dest.exists():
        print(f"⚠️  {dest.name} existe déjà — sauvegarder en .bak", file=sys.stderr)
        dest.rename(dest.with_suffix(".md.bak"))

    proposal_file.rename(dest)
    print(f"✅ Agent installé : {dest}")
    print(f"   Tag    : {agent_tag}")
    print(f"   Fichier: {dest}")
    print()
    print("   Étapes suivantes :")
    print("   1. Réviser les [TODO] dans le fichier agent")
    print("   2. Ajouter dans _bmad/_config/agent-manifest.csv")
    print("   3. Lancer Sentinel [AA] pour l\'audit qualité")
    print("   4. Tester l\'agent en session Copilot Chat")

    # Mise à jour manifest (si accessible)
    if manifest_path and manifest_path.exists():
        try:
            with manifest_path.open("a", encoding="utf-8") as f:
                f.write(f"\n{agent_tag},custom,{agent_tag}.md,[TODO description]")
            print("   ✅ agent-manifest.csv mis à jour")
        except OSError as e:
            print(f"   ⚠️  Impossible de mettre à jour le manifest : {e}")


def list_proposals(proposals_dir: Path) -> None:
    """Liste les proposals disponibles."""
    proposals = list(proposals_dir.glob("*.proposed.md")) if proposals_dir.exists() else []
    if not proposals:
        print("Aucun proposal en attente.")
        print(f"   Dossier : {proposals_dir}")
        return

    print(f"\nProposals d\'agents en attente ({len(proposals)}) :\n")
    for p in sorted(proposals):
        # Lire les premières lignes pour extraire source + description
        try:
            first_lines = p.read_text(encoding="utf-8", errors="replace")[:300]
            source_m = re.search(r"SOURCE:\s*(\w+)\s*—\s*(.+)", first_lines)
            source = source_m.group(1) if source_m else "?"
            desc = source_m.group(2)[:60] if source_m else ""
        except OSError:
            source, desc = "?", ""
        print(f"  📄 {p.name}")
        print(f"     Source : {source} — {desc}")
        print()
    print("  → Installer : bash bmad-init.sh forge --install <nom-agent>")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BMAD Agent Forge — génère des scaffolds d\'agents depuis des besoins détectés",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 agent-forge.py --from "je veux un agent pour les migrations de base de données"
  python3 agent-forge.py --from-gap --shared-context _bmad/_memory/shared-context.md
  python3 agent-forge.py --from-trace --trace _bmad-output/BMAD_TRACE.md
  python3 agent-forge.py --list
  python3 agent-forge.py --install db-migrator
        """,
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--from", dest="from_desc", metavar="DESCRIPTION",
                            help="Générer depuis une description textuelle")
    mode_group.add_argument("--from-gap", action="store_true",
                            help="Générer depuis les gaps inter-agents dans shared-context.md")
    mode_group.add_argument("--from-trace", action="store_true",
                            help="Générer depuis les failure patterns sans agent dans BMAD_TRACE")
    mode_group.add_argument("--list", action="store_true",
                            help="Lister les proposals en attente")
    mode_group.add_argument("--install", metavar="AGENT_NAME",
                            help="Installer un proposal dans le répertoire des agents")

    parser.add_argument("--shared-context", metavar="PATH",
                        default="_bmad/_memory/shared-context.md")
    parser.add_argument("--trace", metavar="PATH",
                        default="_bmad-output/BMAD_TRACE.md")
    parser.add_argument("--project-context", metavar="PATH",
                        default="project-context.yaml")
    parser.add_argument("--agents-dir", metavar="PATH",
                        default="_bmad/_config/custom/agents")
    parser.add_argument("--out-dir", metavar="PATH",
                        default="_bmad-output/forge-proposals")
    parser.add_argument("--archetype", metavar="ARCHETYPE",
                        default="custom",
                        help="Archétype de référence pour l\'agent (défaut: custom)")
    parser.add_argument("--manifest", metavar="PATH",
                        default="_bmad/_config/agent-manifest.csv")

    args = parser.parse_args()

    proposals_dir = Path(args.out_dir)
    agents_dir = Path(args.agents_dir)
    project_ctx_path = Path(args.project_context)
    manifest_path = Path(args.manifest)

    # ── list ───────────────────────────────────────────────────────────────
    if args.list:
        list_proposals(proposals_dir)
        return

    # ── install ────────────────────────────────────────────────────────────
    if args.install:
        install_proposal(
            args.install,
            proposals_dir,
            agents_dir,
            manifest_path if manifest_path.exists() else None,
        )
        return

    # ── Contexte commun ────────────────────────────────────────────────────
    project_context = read_project_context(project_ctx_path)
    existing_agents = list_existing_agents(agents_dir)

    proposals: list[AgentProposal] = []

    # ── --from description ─────────────────────────────────────────────────
    if args.from_desc:
        proposal = build_proposal_from_description(
            args.from_desc, project_context, existing_agents
        )
        proposals.append(proposal)

    # ── --from-gap ─────────────────────────────────────────────────────────
    elif args.from_gap:
        gaps = scan_gaps_from_shared_context(Path(args.shared_context))
        if not gaps:
            print("ℹ️  Aucune requête inter-agent non résolue trouvée dans shared-context.md")
            print(f"   Cherché dans : {args.shared_context}")
            print("   Format attendu : - [ ] [agent→?] description")
            return
        proposals = build_proposals_from_gaps(gaps, project_context, existing_agents)
        print(f"🔍 {len(gaps)} gap(s) inter-agent trouvé(s)")

    # ── --from-trace ───────────────────────────────────────────────────────
    elif args.from_trace:
        trace_gaps = scan_gaps_from_trace(Path(args.trace), existing_agents)
        if not trace_gaps:
            print("ℹ️  Aucun pattern de failure récurrent sans agent propriétaire détecté")
            return
        proposals = build_proposals_from_trace_gaps(trace_gaps, project_context, existing_agents)
        print(f"🔍 {len(trace_gaps)} gap(s) détecté(s) dans BMAD_TRACE")

    # ── Sauvegarder les proposals ──────────────────────────────────────────
    if not proposals:
        print("Aucun proposal généré.")
        return

    print()
    saved_paths = []
    for proposal in proposals:
        out_path = save_proposal(proposal, proposals_dir, args.archetype)
        saved_paths.append(out_path)

        # Résumé du proposal
        print(f"✅ Proposal généré : {out_path.name}")
        print(f"   Domaine  : {proposal.domain_key} ({proposal.agent_icon})")
        print(f"   Nom      : {proposal.agent_name} [{proposal.agent_tag}]")
        print(f"   Rôle     : {proposal.agent_role}")
        if proposal.existing_overlap:
            print(f"   ⚠️  Overlap : {', '.join(proposal.existing_overlap)}")
        if proposal.inter_agent_source:
            print(f"   Gap from : {proposal.inter_agent_source}")
        print()

    print("─" * 60)
    print(f"  {len(proposals)} proposal(s) dans {proposals_dir}/")
    print()
    print("  Étapes suivantes :")
    print("  1. Réviser les [TODO] dans chaque fichier .proposed.md")
    print("  2. Remplir les prompts avec la logique métier réelle")
    if proposal.existing_overlap:
        print("  3. ⚠️  Résoudre les overlaps détectés avant installation")
    print(f"  {3 if proposal.existing_overlap else '3'}. bash bmad-init.sh forge --install <nom-agent>")
    print("  4. Sentinel [AA] pour l'audit qualité")


if __name__ == "__main__":
    main()
