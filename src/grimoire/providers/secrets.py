"""Magasin léger de jetons API : ``~/.env-store/`` + un index (issue #310, suite).

Les clés ne vivent jamais dans ``llm-provider-registry.yaml`` lui-même — sa
``data_policy.forbidden_data_classes`` interdit déjà ``secrets``/``credentials``
dans ce qui *transite par* un fournisseur ; ce module applique la même règle
à ce qui *décrit* un fournisseur. Une valeur se pose en saisie masquée
(``grimoire providers set-key``, ``getpass``), jamais en argument de ligne de
commande ni en log — c'est le seul chemin d'écriture exposé par la CLI.

Format du magasin :
  ``~/.env-store/index.json``     — ``{"entries": [{"provider", "env_var", "file", "added_at"}]}``
  ``~/.env-store/<provider>.env`` — lignes ``VAR=valeur``, une par variable, ``chmod 600``

``env_for_provider`` est lu par ``missions.dispatch._run_provider_call`` pour
fusionner ces variables dans l'environnement du sous-processus avant
d'invoquer le fournisseur — un script d'invocation (``deepseek-cli.py`` et
analogues) n'a besoin de rien connaître du magasin, il reçoit déjà la
variable dans son ``os.environ``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def store_dir() -> Path:
    """``~/.env-store`` — jamais dans le dépôt du projet, jamais versionné.

    Sous Windows, ``chmod`` ne restreint rien : seuls les droits du profil utilisateur protègent ce dossier.
    """
    return Path.home() / ".env-store"


def _index_file() -> Path:
    return store_dir() / "index.json"


#: Identifiant de fournisseur admis pour nommer un fichier du magasin ou dériver un nom de
#: variable : ASCII minuscule strict. Un id hors de cette forme pourrait sortir du magasin
#: (``../``) ou, après ``upper()``, tomber sur la variable d'un autre fournisseur.
_PROVIDER_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}", re.ASCII)


def is_valid_provider_id(provider_id: str) -> bool:
    """Vrai si *provider_id* est un identifiant ASCII strict (``[a-z0-9][a-z0-9-]{0,63}``)."""
    return isinstance(provider_id, str) and _PROVIDER_ID_RE.fullmatch(provider_id) is not None


def provider_key_var(provider_id: str) -> str:
    """``<ID>_API_KEY`` dérivé d'un id valide ; vide pour un id invalide (rien n'est dérivé)."""
    if not is_valid_provider_id(provider_id):
        return ""
    return f"{provider_id.upper().replace('-', '_')}_API_KEY"


def _provider_file(provider_id: str) -> Path:
    """Fichier du magasin pour *provider_id* ; ``ValueError`` si l'id n'est pas admis."""
    if not is_valid_provider_id(provider_id):
        msg = f"identifiant de fournisseur invalide {provider_id!r} (attendu : [a-z0-9][a-z0-9-]{{0,63}})."
        raise ValueError(msg)
    return store_dir() / f"{provider_id}.env"


@dataclass(frozen=True, slots=True)
class SecretEntry:
    """Une ligne de l'index — jamais la valeur, seulement sa provenance."""

    provider: str
    env_var: str
    file: str
    added_at: str


def _ensure_store() -> None:
    store_dir().mkdir(parents=True, exist_ok=True)
    store_dir().chmod(0o700)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    body = "\n".join(f"{k}={v}" for k, v in values.items())
    path.write_text(f"{body}\n" if values else "", encoding="utf-8")
    path.chmod(0o600)


def _load_index() -> dict[str, list[dict[str, str]]]:
    path = _index_file()
    if not path.is_file():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": []}
    return data if isinstance(data, dict) else {"entries": []}


def _save_index(data: dict[str, list[dict[str, str]]]) -> None:
    path = _index_file()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    path.chmod(0o600)


def set_secret(provider_id: str, env_var: str, value: str) -> Path:
    """Pose *value* pour *env_var* du fournisseur *provider_id* ; rend le fichier écrit.

    Lève ``ValueError`` sur une valeur vide — un magasin qui accepterait une
    clé vide masquerait l'absence réelle de configuration — ou sur un id invalide.
    """
    if not value.strip():
        msg = "valeur vide refusée."
        raise ValueError(msg)
    path = _provider_file(provider_id)
    _ensure_store()
    values = _read_env_file(path)
    values[env_var] = value.strip()
    _write_env_file(path, values)

    index = _load_index()
    entries = [e for e in index["entries"] if not (e["provider"] == provider_id and e["env_var"] == env_var)]
    entries.append(
        {"provider": provider_id, "env_var": env_var, "file": path.name, "added_at": datetime.now(UTC).isoformat()}
    )
    index["entries"] = entries
    _save_index(index)
    return path


def list_secrets() -> tuple[SecretEntry, ...]:
    """Entrées déclarées (fournisseur, variable, fichier, date) — jamais les valeurs."""
    index = _load_index()
    return tuple(
        SecretEntry(provider=e["provider"], env_var=e["env_var"], file=e["file"], added_at=e.get("added_at", ""))
        for e in index.get("entries", [])
        if isinstance(e, dict) and e.get("provider") and e.get("env_var")
    )


def remove_secret(provider_id: str, env_var: str | None = None) -> None:
    """Retire une variable, ou tout le fichier de *provider_id* si *env_var* est ``None``.

    Lève ``ValueError`` sur un id invalide.
    """
    path = _provider_file(provider_id)
    values = _read_env_file(path)
    if env_var is None:
        path.unlink(missing_ok=True)
    elif env_var in values:
        del values[env_var]
        _write_env_file(path, values)
    index = _load_index()
    index["entries"] = [
        e
        for e in index["entries"]
        if not (e["provider"] == provider_id and (env_var is None or e["env_var"] == env_var))
    ]
    _save_index(index)


def env_for_provider(provider_id: str) -> dict[str, str]:
    """Variables déjà posées pour *provider_id* — ``{}`` si rien n'est enregistré ou si l'id est invalide."""
    if not is_valid_provider_id(provider_id):
        return {}
    return _read_env_file(_provider_file(provider_id))
