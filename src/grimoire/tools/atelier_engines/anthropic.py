"""Appeler l'API Messages d'Anthropic avec le client existant de l'Atelier (lot B2).

Réutilise de :mod:`grimoire.tools.atelier_model` l'URL fixe de l'API, la version, la lecture de la clé (environnement ou
magasin de clés du kit) et le transport HTTPS sans redirection ni proxy. La clé ne quitte pas ce module : ni repr, ni
message d'erreur. ``on_response`` reçoit les octets bruts d'une réponse 200 pour inscrire la consommation au compteur et au
quota du kit (voir :func:`grimoire.tools.atelier_model.account_response`).
"""

from __future__ import annotations

import contextlib
import json
import string
from collections.abc import Callable
from typing import Any

from grimoire.tools import atelier_model as am
from grimoire.tools.atelier_engines.transport import EngineRefus, EngineSortie, Transport, post_json

__all__ = ["AnthropicBackend"]

_ID_CHARS = frozenset(string.ascii_letters + string.digits + "._-")

_SUFFIXE = (
    "\n\nRéponds UNIQUEMENT par un objet JSON valide qui respecte ce schéma, sans texte autour ni balise de code :\n"
)


class AnthropicBackend:
    """``POST`` sur l'API Messages ; le schéma est dit dans le message système (l'API n'a pas de mode JSON)."""

    def __init__(
        self, model: str, key: str, *, timeout_s: float, transport: Transport | None = None,
        on_response: Callable[[bytes], object] | None = None,
    ) -> None:
        if not key or not (key.isascii() and key.isprintable() and " " not in key):
            raise EngineRefus("aucune clé utilisable pour ce fournisseur")
        if not model or len(model) > 64 or not set(model) <= _ID_CHARS:
            raise EngineRefus("nom de modèle invalide")
        self.model = model
        self.timeout_s = timeout_s
        self.transport: Transport = transport or am.default_transport
        self.on_response = on_response
        self._key = key

    def __repr__(self) -> str:
        return f"AnthropicBackend(model={self.model!r})"

    def completer(self, systeme: str, utilisateur: str, schema: dict[str, Any], max_jetons: int) -> tuple[str, bytes]:
        payload = {
            "model": self.model, "max_tokens": max_jetons, "temperature": 0.2,
            "system": systeme + _SUFFIXE + json.dumps(schema, ensure_ascii=False),
            "messages": [{"role": "user", "content": utilisateur}],
        }
        headers = {"x-api-key": self._key, "anthropic-version": am.API_VERSION}
        data, raw = post_json(self.transport, am.API_URL, headers, payload, self.timeout_s)
        if self.on_response is not None:
            with contextlib.suppress(Exception):  # une inscription échouée ne fait jamais échouer le geste (le compteur est un plus)
                self.on_response(raw)
        blocks = data.get("content") if isinstance(data, dict) else None
        block = blocks[0] if isinstance(blocks, list) and blocks else None
        text = block.get("text") if isinstance(block, dict) and block.get("type") == "text" else None
        if not isinstance(text, str) or not text.strip():
            raise EngineSortie("réponse du modèle malformée : ignorée")
        return text, raw
