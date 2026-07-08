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
print(resolver.grimoire_dir)         # _grimoire/
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

Tous les outils partagent la même interface : ils se construisent avec un
`project_root` et s'exécutent via `run(**kwargs)`, qui rend un résultat typé.

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

root = Path(".")

# Harmony Check — cohérence architecturale du projet
harmony = HarmonyCheck(root).run()
print(f"Score: {harmony.score}/100 (grade {harmony.grade})")
for d in harmony.dissonances:
    print(f"  {d}")

# Preflight Check — vérification pré-déploiement
preflight = PreflightCheck(root).run()
print(f"Go/No-Go: {preflight.go_nogo}")
for check in preflight.checks:
    print(f"  {check}")

# Memory Lint — intégrité de la mémoire
lint = MemoryLint(root).run()
print(f"{lint.error_count} erreurs, {lint.warning_count} avertissements")
for issue in lint.issues:
    print(f"  {issue}")

# Context Router — plan de chargement pour un agent
plan = ContextRouter(root).run(agent="architect", task="Déployer sur Kubernetes")
print(f"Agent: {plan.agent} · {plan.total_tokens} tokens · {plan.status}")

# Context Guard — budget contexte par agent
guard = ContextGuard(root).run(agent="architect")
print(f"{guard.overbudget_count} agent(s) hors budget")

# Stigmergy (expérimental) — coordination par phéromones
board = Stigmergy(root).run(action="emit", ptype="ALERT", location="src/auth")
print(f"{board.total_emitted} phéromones émises")

# Agent Forge — proposition de squelette d'agent
proposal = AgentForge(root).run(description="un agent qui écrit et lance les tests")
print(f"{proposal.agent_name} — {proposal.agent_role}")
```

> **Stigmergy** fait partie des features **expérimentales** (R&D) : elle
> fonctionne et est testée, mais son API reste hors du contrat de stabilité
> SemVer. Voir [R&D expérimental](rnd.md).

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
