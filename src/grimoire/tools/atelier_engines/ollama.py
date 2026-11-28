"""Appeler un modèle d'Ollama, sur la machine seulement (épique #1477, lot B2).

L'adresse doit être une boucle locale reconnue : une autre est refusée à la construction. Aucune clé n'existe ici.
La sortie est contrainte par le schéma du geste (champ ``format`` d'Ollama), puis revalidée par le programme.
"""

from __future__ import annotations

from typing import Any

from grimoire.tools.atelier_engines.transport import (
    EngineRefus,
    EngineSortie,
    Transport,
    default_transport,
    is_loopback_url,
    post_json,
)

__all__ = ["OllamaBackend"]


class OllamaBackend:
    """``POST <base>/api/chat`` sans flux, au schéma du geste, température basse."""

    def __init__(self, base_url: str, model: str, *, timeout_s: float, transport: Transport | None = None) -> None:
        base = (base_url or "").strip().rstrip("/")
        if not is_loopback_url(base):
            raise EngineRefus("Ollama n'est utilisé que sur une adresse de la machine")
        if not model or len(model) > 128 or not model.isprintable():
            raise EngineRefus("nom de modèle invalide")
        self.url = f"{base}/api/chat"
        self.model = model
        self.timeout_s = timeout_s
        self.transport = transport or default_transport

    def completer(self, systeme: str, utilisateur: str, schema: dict[str, Any], max_jetons: int) -> tuple[str, bytes]:
        payload = {
            "model": self.model, "stream": False, "format": schema,
            "messages": [{"role": "system", "content": systeme}, {"role": "user", "content": utilisateur}],
            "options": {"temperature": 0.2, "num_predict": max_jetons},
        }
        data, raw = post_json(self.transport, self.url, {}, payload, self.timeout_s)
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise EngineSortie("réponse du modèle malformée : ignorée")
        return content, raw
