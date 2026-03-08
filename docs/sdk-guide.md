# Guide SDK Python — Grimoire Kit v3

> Utiliser Grimoire Kit comme bibliothèque Python dans vos scripts et outils.

## Installation

```bash
pip install grimoire-kit
```

## Configuration

```python
from pathlib import Path
from grimoire.core.config import GrimoireConfig

# Charger depuis un fichier YAML
config = GrimoireConfig.from_yaml(Path("project-context.yaml"))

# Accéder aux sections typées
print(config.project.name)       # str
print(config.project.stack)      # tuple[str, ...]
print(config.user.skill_level)   # "beginner" | "intermediate" | "expert"
print(config.memory.backend)     # "auto" | "local" | ...
print(config.agents.archetype)   # "minimal" | "web-app" | ...
```

## Projet

```python
from grimoire.core.project import GrimoireProject

project = GrimoireProject(config)
status = project.status()

# Agents installés
for agent in status.agents:
    print(f"{agent.id}: {agent.name} ({agent.archetype})")

# Contexte du projet
ctx = project.context()
print(ctx.project_name)
print(ctx.stack)
```

## Résolution de chemins

```python
from grimoire.core.resolver import PathResolver

resolver = PathResolver(project_root=Path("."))
print(resolver.bmad_dir)         # _grimoire/
print(resolver.config_dir)       # _grimoire/_config/
print(resolver.memory_dir)       # _grimoire/_memory/
print(resolver.agents_dir)       # _grimoire/_config/agents/
```

## Détection de stack

```python
from grimoire.core.scanner import StackScanner

scanner = StackScanner(Path("."))
result = scanner.scan()

for detection in result.detections:
    print(f"{detection.technology}: {detection.confidence:.0%}")
    print(f"  Fichiers: {detection.evidence}")
```

## Validation

```python
from grimoire.core.validator import validate_config

errors = validate_config(config)
if errors:
    for err in errors:
        print(f"[{err.severity}] {err.field}: {err.message}")
else:
    print("Configuration valide")
```

## Outils intégrés

Tous les outils sont disponibles comme classes Python :

```python
from grimoire.tools import (
    AgentForge,
    ContextGuard,
    ContextRouter,
    HarmonyCheck,
    MemoryLint,
    PreflightCheck,
    Stigmergy,
)

# Harmony Check — vérifie la cohérence du projet
hc = HarmonyCheck(project_root=Path("."))
report = hc.run()
print(f"Score: {report.score}/100")
for issue in report.issues:
    print(f"  [{issue.severity}] {issue.message}")

# Preflight Check — vérification pré-déploiement
pf = PreflightCheck(project_root=Path("."))
result = pf.run()
for check in result.checks:
    print(f"  {'✔' if check.passed else '✘'} {check.name}")

# Memory Lint — vérifie l'intégrité de la mémoire
ml = MemoryLint(project_root=Path("."))
report = ml.run()
for issue in report.issues:
    print(f"  {issue.severity}: {issue.message}")

# Context Router — routage vers l'agent optimal
cr = ContextRouter(project_root=Path("."))
result = cr.route("Comment déployer sur Kubernetes ?")
print(f"Agent recommandé: {result.agent_id}")

# Context Guard — vérification du budget contexte
cg = ContextGuard(max_tokens=8000)
result = cg.check(Path("_grimoire/_config/agents/architect.md"))
print(f"Tokens: {result.token_count} / {cg.max_tokens}")

# Stigmergy — signaux inter-agents
st = Stigmergy(project_root=Path("."))
st.deposit("insight", {"content": "Pattern détecté"})
signals = st.collect("insight")

# Agent Forge — génération de squelettes d'agents
af = AgentForge()
skeleton = af.generate(
    agent_id="my-agent",
    name="Mon Agent",
    title="Expert Custom",
)
```

## Merge Engine

```python
from grimoire.core.merge import MergeEngine

engine = MergeEngine(source=Path("template"), target=Path("my-project"))

# Analyser sans modifier
plan = engine.analyze()
print(f"Fichiers à créer: {len(plan.files_to_create)}")
print(f"Conflits: {len(plan.conflicts)}")
for conflict in plan.conflicts:
    print(f"  {conflict.path} → {conflict.resolution}")

# Exécuter le merge
result = engine.execute(plan)
print(f"Créés: {len(result.files_created)}")
print(f"Log: {result.log_path}")

# Dry-run (aucune modification)
result = engine.execute(plan, dry_run=True)

# Force (écrase les conflits)
result = engine.execute(plan, force=True)

# Rollback
MergeEngine.undo(result.log_path)
```

## Registre local

```python
from grimoire.registry.local import LocalRegistry

registry = LocalRegistry(Path("archetypes"))

# Lister
for item in registry.list_all():
    print(f"{item.id}: {item.description}")

# Chercher
results = registry.search("kubernetes")
```

## Mémoire

```python
from grimoire.memory.manager import MemoryManager

mm = MemoryManager(config)

# Stocker
mm.store("Décision: utiliser FastAPI pour l'API", user_id="guilhem")

# Rechercher
results = mm.search("choix framework", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.text}")
```

## Exceptions

Toutes les exceptions héritent de `GrimoireError` :

```python
from grimoire.core.exceptions import (
    GrimoireError,          # Base
    GrimoireConfigError,    # Configuration invalide
    GrimoireProjectError,   # Structure projet invalide
    GrimoireAgentError,     # Erreur agent
    GrimoireToolError,      # Erreur outil
    GrimoireMergeError,     # Erreur merge
    GrimoireRegistryError,  # Erreur registre
    GrimoireMemoryError,    # Erreur mémoire
    GrimoireValidationError,# Erreur validation
)
```

## Voir aussi

- [Référence YAML](grimoire-yaml-reference.md)
- [Intégration MCP](mcp-integration.md)
- [Getting Started](getting-started.md)
