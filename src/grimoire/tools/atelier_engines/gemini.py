"""Appeler l'API Gemini de Google, voie native ``generateContent`` (épique #1477).

Voie retenue : l'API NATIVE, pas le point d'accès compatible OpenAI. Sa sortie JSON contrainte par un schéma est documentée
(``generationConfig.responseMimeType`` + ``responseJsonSchema``), alors que la page de compatibilité OpenAI ne nomme ni le mode
JSON ni ``max_tokens`` et dit que les paramètres inconnus sont « silencieusement ignorés ». La clé part dans l'en-tête
``x-goog-api-key``, JAMAIS dans l'adresse (les exemples de Google la mettent en ``?key=`` : on ne le fait pas, une URL finit
dans des journaux).

Sources : https://ai.google.dev/gemini-api/docs/structured-output et https://ai.google.dev/api/generate-content (qui redirigent
vers aistudio.google.com/docs/structured-output et aistudio.google.com/api-reference/generate-content).

L'adresse est CONSTRUITE ici (hôte officiel fixe, modèle validé) : aucune adresse lue du registre ne peut détourner la clé.
La clé ne quitte pas ce module : ni repr, ni message d'erreur.
"""

from __future__ import annotations

import string
from typing import Any

from grimoire.tools.atelier_engines.transport import (
    EngineRefus,
    EngineSortie,
    Transport,
    default_transport,
    post_json,
)

__all__ = ["GEMINI_HOTE", "GeminiBackend"]

GEMINI_HOTE = "https://generativelanguage.googleapis.com/v1beta/models/"
_ID_CHARS = frozenset(string.ascii_letters + string.digits + "._-")


class GeminiBackend:
    """``POST /v1beta/models/<modèle>:generateContent``, sortie JSON contrainte, température basse."""

    def __init__(self, model: str, key: str, *, timeout_s: float, transport: Transport | None = None) -> None:
        if not key or not (key.isascii() and key.isprintable() and " " not in key):
            raise EngineRefus("aucune clé utilisable pour ce fournisseur")
        if not model or len(model) > 64 or not set(model) <= _ID_CHARS or not model[0].isalnum():
            raise EngineRefus("nom de modèle invalide")
        self.model = model
        self.url = f"{GEMINI_HOTE}{model}:generateContent"
        self.timeout_s = timeout_s
        self.transport: Transport = transport or default_transport
        self._key = key

    def __repr__(self) -> str:
        return f"GeminiBackend(model={self.model!r})"

    def completer(self, systeme: str, utilisateur: str, schema: dict[str, Any], max_jetons: int) -> tuple[str, bytes]:
        payload = {
            "systemInstruction": {"parts": [{"text": systeme}]},
            "contents": [{"role": "user", "parts": [{"text": utilisateur}]}],
            "generationConfig": {
                "temperature": 0.2, "maxOutputTokens": max_jetons,
                "responseMimeType": "application/json", "responseJsonSchema": schema,
            },
        }
        data, raw = post_json(self.transport, self.url, {"x-goog-api-key": self._key}, payload, self.timeout_s)
        candidats = data.get("candidates") if isinstance(data, dict) else None
        premier = candidats[0] if isinstance(candidats, list) and candidats else None
        contenu = premier.get("content") if isinstance(premier, dict) else None
        parts = contenu.get("parts") if isinstance(contenu, dict) else None
        if not isinstance(premier, dict) or premier.get("finishReason", "STOP") != "STOP" or not isinstance(parts, list):
            raise EngineSortie("réponse du modèle malformée ou interrompue : ignorée")
        textes = [p["text"] for p in parts if isinstance(p, dict) and not p.get("thought") and isinstance(p.get("text"), str)]
        texte = "".join(textes)
        if not texte.strip():
            raise EngineSortie("réponse du modèle malformée : ignorée")
        return texte, raw
