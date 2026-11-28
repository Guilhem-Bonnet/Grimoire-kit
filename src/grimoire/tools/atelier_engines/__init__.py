"""Les moteurs à modèle de la conversation de cadrage de l'Atelier (épique #1477, lot B2).

``transport`` (transport minimal et erreurs), ``schema`` (sorties validées), ``gestes`` (invites et lecture stricte),
``ollama`` / ``openai_compat`` / ``anthropic`` (adaptateurs), ``moteur`` (le protocole ``Moteur`` porté par des appels),
``fabrique`` (qui parle à ce tour, d'après ``choose_for``). Voir ``docs/reference/atelier-conversation.md``.
"""
