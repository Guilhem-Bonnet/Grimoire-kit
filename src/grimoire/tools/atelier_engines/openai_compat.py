"""Appeler un service au format OpenAI (``/v1/chat/completions``) : passerelle admise ou serveur de la machine (lot B2).

L'adresse passe par :func:`grimoire.providers.capability_probe.admitted_gateway_url` (https partout, http vers la
machine seulement, sans identifiants, requête ni fragment). La clé, si elle existe, part en en-tête vers cette seule
adresse : jamais dans une erreur, jamais dans un journal.
"""

from __future__ import annotations

from typing import Any

from grimoire.providers.capability_probe import admitted_gateway_url
from grimoire.tools.atelier_engines.transport import EngineRefus, EngineSortie, Transport, default_transport, post_json

__all__ = ["OpenAICompatBackend"]


class OpenAICompatBackend:
    """``POST <passerelle>/v1/chat/completions`` en mode JSON, température basse."""

    def __init__(self, base_url: str, model: str, *, key: str = "", timeout_s: float, transport: Transport | None = None) -> None:
        base = admitted_gateway_url(base_url)
        if not base:
            raise EngineRefus("adresse de la passerelle non admise")
        if not model or len(model) > 128 or not model.isprintable():
            raise EngineRefus("nom de modèle invalide")
        if key and not (key.isascii() and key.isprintable() and " " not in key):
            raise EngineRefus("clé de forme impropre à un en-tête")
        self.url = f"{base}/v1/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        self.transport = transport or default_transport
        self._key = key  # jamais exposée : ni repr, ni message d'erreur

    def __repr__(self) -> str:
        return f"OpenAICompatBackend(model={self.model!r})"

    def completer(self, systeme: str, utilisateur: str, schema: dict[str, Any], max_jetons: int) -> tuple[str, bytes]:
        payload = {
            "model": self.model, "temperature": 0.2, "max_tokens": max_jetons,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}],
        }
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        data, raw = post_json(self.transport, self.url, headers, payload, self.timeout_s)
        choices = data.get("choices") if isinstance(data, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise EngineSortie("réponse du modèle malformée : ignorée")
        return content, raw
