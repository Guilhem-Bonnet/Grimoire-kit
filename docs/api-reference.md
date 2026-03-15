# Référence API

## `grimoire.core`

### `GrimoireConfig`

::: grimoire.core.config.GrimoireConfig
    options:
      members:
        - from_yaml
        - from_dict
        - find_and_load
        - validate

**Sous-sections** : `project` ([ProjectConfig](#projectconfig)), `user` ([UserConfig](#userconfig)), `memory` ([MemoryConfig](#memoryconfig)), `agents` ([AgentsConfig](#agentsconfig)), `installed_archetypes`, `extra`

---

### Sous-sections de config

#### ProjectConfig

::: grimoire.core.config.ProjectConfig

#### UserConfig

::: grimoire.core.config.UserConfig

#### MemoryConfig

::: grimoire.core.config.MemoryConfig

#### AgentsConfig

::: grimoire.core.config.AgentsConfig

---

### `GrimoireProject`

::: grimoire.core.project.GrimoireProject

---

### `GrimoireError`

Exception de base. Toutes les exceptions Grimoire en héritent.

```python
from grimoire.core.exceptions import GrimoireError

try:
    cfg = GrimoireConfig.from_yaml(path)
except GrimoireError as e:
    print(e.error_code)  # ex: "GR001"
```

| Exception | Code | Usage |
|---|---|---|
| `GrimoireConfigError` | GR001–GR003 | Fichier config manquant, YAML invalide, section requise absente |
| `GrimoireProjectError` | — | Projet non initialisé ou structure invalide |
| `GrimoireAgentError` | GR101–GR103 | Agent introuvable, archétype absent, registre en erreur |
| `GrimoireToolError` | GR401–GR402 | Outil en échec ou introuvable |
| `GrimoireMergeError` | GR501–GR502 | Erreur de fusion, conflit non résolu |
| `GrimoireMemoryError` | GR201–GR202 | Backend mémoire en erreur ou inaccessible |
| `GrimoireTimeoutError` | GR301 | Timeout réseau |
| `GrimoireNetworkError` | GR302–GR303 | Erreur réseau ou MCP |
| `GrimoireValidationError` | GR501 | Validation échouée |

---

### `configure_logging`

::: grimoire.core.log.configure_logging

Variable d'environnement : `GRIMOIRE_LOG_LEVEL` (override le niveau par défaut `WARNING`).

---

### Validation

::: grimoire.core.validator.validate_config

::: grimoire.core.validator.ValidationError

---

### `@deprecated`

::: grimoire.core.deprecation.deprecated

---

### `@with_retry`

::: grimoire.core.retry.with_retry

---

## `grimoire.registry`

### Plugin Discovery

Les packages tiers peuvent enregistrer des extensions via les entry points :

```toml
# pyproject.toml du plugin
[project.entry-points."grimoire.tools"]
my_tool = "my_package:MyTool"

[project.entry-points."grimoire.backends"]
my_backend = "my_package:MyBackend"
```

```python
from grimoire.registry import discover_tools, discover_backends

tools = discover_tools()       # {"my_tool": MyTool, ...}
backends = discover_backends() # {"my_backend": MyBackend, ...}
```

---

## `grimoire.core.error_codes`

Codes stables pour documentation et outillage.

| Catégorie | Plage | Description |
|---|---|---|
| Configuration | GR001–GR003 | Config introuvable, YAML invalide, section manquante |
| Agents / Registry | GR101–GR103 | Agent/archétype introuvable, registre en erreur |
| Memory | GR201–GR202 | Backend mémoire en erreur ou inaccessible |
| Network / MCP | GR301–GR303 | Timeout, erreur réseau, MCP inaccessible |
| Tools | GR401–GR402 | Outil en échec ou introuvable |
| Validation / Merge | GR501–GR502 | Validation échouée, conflit de fusion |

```python
from grimoire.core.error_codes import CODES

for code, ec in CODES.items():
    print(f"{code}: {ec.summary}")
```
