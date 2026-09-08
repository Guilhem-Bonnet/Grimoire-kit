"""``grimoire providers audit`` — sonder sans dépenser (issue #330, sous-issue de #307).

Le registre déclare les fournisseurs ; rien ne vérifie qu'ils répondent. Un
fournisseur activé dont le CLI est absent fait perdre une tentative à chaque
cascade (``grimoire task dispatch``). Ce module répond à « qui est vraiment
là, maintenant ? » avec trois sondes bornées, jamais une quatrième :

1. présence du premier mot de ``invocation`` sur le ``PATH``
   (``shutil.which``) ;
2. ``--version``, seulement pour un exécutable dont on connaît la forme de
   sortie (``_VERSION_PROBE_KNOWN``) — un binaire inconnu n'est pas sondé à
   l'aveugle ;
3. pour un fournisseur local (invocation ``ollama ...`` ou
   ``provider_type: local``), la liste des modèles via ``GET /api/tags``.

Aucune sonde n'assemble ni n'exécute l'``invocation`` complète du registre :
un audit qui consomme du quota ment sur le quota. L'audit n'écrit jamais le
registre — ``enabled`` reste une décision de gouvernance ; seul l'état
runtime (``_grimoire-output/providers-state.json``, via ``providers.state``)
change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from grimoire.providers.registry import ProviderSpec, read_registry
from grimoire.providers.state import ProviderRuntimeState, load_state, save_state

#: Exécutables dont ``--version`` est un sondage sûr : rapide, sans réseau ni
#: effet de bord connu. Un exécutable hors de cette liste est jugé présent
#: (le PATH le dit) sans qu'on tente de le faire parler davantage — on ne
#: devine pas le contrat d'un CLI qu'on ne connaît pas.
_VERSION_PROBE_KNOWN: frozenset[str] = frozenset({"claude", "gemini", "copilot", "codex", "ollama"})

_VERSION_TIMEOUT_S = 10.0

_OLLAMA_DEFAULT_URL = "http://localhost:11434"
_OLLAMA_TAGS_TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Ce qu'une sonde a établi pour un fournisseur, sans jamais l'appeler."""

    provider_id: str
    available: bool
    models_seen: tuple[str, ...]
    probe_note: str


def _first_word(invocation: str) -> str:
    parts = invocation.split()
    return parts[0] if parts else ""


def _probe_version(resolved_path: str) -> str | None:
    """Première ligne de ``<resolved_path> --version``, ou ``None`` en échec.

    Timeout court (10 s) : un binaire qui ne répond pas à ``--version`` en
    10 secondes ne répondra pas mieux à un vrai prompt, et l'audit ne doit
    pas bloquer une cascade en attendant.
    """
    try:
        completed = subprocess.run(
            [resolved_path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (completed.stdout or completed.stderr or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _ollama_base_url() -> str:
    """``OLLAMA_HOST`` (issue #330) ou le défaut local, toujours en URL complète.

    La variable réelle d'Ollama prend souvent la forme ``hôte:port`` sans
    schéma (``127.0.0.1:11434``) — on complète en ``http://`` plutôt que de
    rejeter une valeur que l'outil natif accepte.
    """
    value = os.environ.get("OLLAMA_HOST", "").strip()
    if not value:
        return _OLLAMA_DEFAULT_URL
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


def _probe_ollama_models() -> tuple[tuple[str, ...], str | None]:
    """Modèles connus d'Ollama sans prompt : ``GET /api/tags``, timeout 3 s."""
    url = f"{_ollama_base_url()}/api/tags"
    try:
        request = urllib.request.Request(url, method="GET")  # noqa: S310 — URL locale, schéma vérifié
        with urllib.request.urlopen(request, timeout=_OLLAMA_TAGS_TIMEOUT_S) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return (), f"Ollama injoignable sur {url}"
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return (), None
    names = tuple(str(entry["name"]) for entry in models if isinstance(entry, dict) and entry.get("name"))
    return names, None


def probe_provider(provider: ProviderSpec) -> ProbeResult:
    """Sonde *provider* : présence PATH, ``--version`` connu, modèles Ollama.

    Jamais l'invocation complète du registre, jamais de prompt — seulement ce
    que ``shutil.which``, un ``--version`` borné et ``GET /api/tags`` peuvent
    dire sans dépenser de quota. Un fournisseur sans ``invocation`` déclarée
    est jugé indisponible : rien à sonder n'est aussi peu exploitable que
    l'exécutable introuvable.
    """
    executable = _first_word(provider.invocation or "")
    if not executable:
        return ProbeResult(provider.id, False, (), "aucune invocation déclarée dans le registre")

    resolved = shutil.which(executable)
    if resolved is None:
        return ProbeResult(provider.id, False, (), f"exécutable {executable!r} absent du PATH")

    notes = [f"{executable!r} trouvé ({resolved})"]

    name = PurePosixPath(executable).name
    if name in _VERSION_PROBE_KNOWN:
        version = _probe_version(resolved)
        if version:
            notes.append(version)

    models_seen: tuple[str, ...] = ()
    if name == "ollama" or provider.provider_type == "local":
        models_seen, ollama_note = _probe_ollama_models()
        if ollama_note:
            notes.append(ollama_note)

    return ProbeResult(provider.id, True, models_seen, " · ".join(notes))


def audit_providers(root: Path, *, now: datetime | None = None) -> tuple[ProbeResult, ...]:
    """Sonde chaque fournisseur *activé* et journalise le résultat dans l'état.

    Ne lit ``enabled`` que pour choisir qui sonder — jamais pour l'écrire :
    l'audit n'active ni ne désactive un fournisseur, seul l'état runtime
    (``probed_at``, ``available``, ``models_seen``, ``probe_note``) change.
    Le refroidissement (``record_failure``/``record_success``) est préservé
    tel quel : l'audit et la cascade sont deux sources d'information
    distinctes sur le même fournisseur, ni ne s'écrasent.
    """
    effective_now = now if now is not None else datetime.now(UTC)
    providers = tuple(spec for spec in read_registry(root) if spec.enabled)
    results = tuple(probe_provider(spec) for spec in providers)

    state = load_state(root)
    for result in results:
        previous = state.get(result.provider_id, ProviderRuntimeState())
        state[result.provider_id] = ProviderRuntimeState(
            cooldown_until=previous.cooldown_until,
            last_failure=previous.last_failure,
            failure_count=previous.failure_count,
            available=result.available,
            probed_at=effective_now.isoformat(),
            models_seen=result.models_seen,
            probe_note=result.probe_note,
        )
    save_state(root, state)
    return results
