"""Frontière de confiance des écritures mémoire.

``MemoryManager.store`` acceptait n'importe quel texte, de n'importe quelle
taille, de n'importe quel émetteur, et ``redaction_policy: required`` du
``memory-policy.yaml`` n'était qu'une chaîne de YAML : le vérificateur du
standard contrôlait sa *présence*, jamais son *effet*. Une mémoire est
pourtant le seul contenu qui survit à la session qui l'a écrite et qui se
relit ensuite comme du contexte de confiance — OWASP ASI06 (Memory and
Context Poisoning) et LLM09 (Vector and Embedding Weaknesses).

Ce module porte la règle en code, avant tout appel de backend :

1. **Schéma de contenu** — type, taille maximale, champs de provenance
   obligatoires, type de mémoire déclaré parmi les valeurs connues.
2. **Contenu qui ressemble à une consigne** — refusé, nommément. Une mémoire
   est une donnée ; un texte qui se présente comme un tour système est une
   tentative d'injection différée (Beurer-Kellner : une donnée non fiable
   ingérée ne doit plus pouvoir déclencher d'action conséquente).
3. **Redaction** — réellement exécutée quand la politique la déclare
   ``required``. Les motifs sont documentés ci-dessous et couverts par un
   corpus positif *et* négatif dans ``tests/unit/memory/test_write_validation.py``.
4. **Émetteurs** — liste d'autorisation en **mode observation** : un émetteur
   inconnu produit un événement journalisé, pas un refus. L'acteur MCP est
   générique aujourd'hui ; refuser reviendrait à casser la surface qui appelle
   le plus. Le mode ``refuse`` existe et est testé, il n'est pas le défaut.

Les refus sont des :class:`MemoryWriteRefusedError` — une erreur *nommée*, avec un
code stable, ce que l'appelant (CLI, outil MCP) peut rendre tel quel.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.core.exceptions import GrimoireMemoryError

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_METADATA_BYTES",
    "MAX_TEXT_BYTES",
    "MemoryWritePolicy",
    "MemoryWriteRefusedError",
    "ValidatedWrite",
    "redact_secrets",
    "validate_memory_write",
]

#: Taille maximale d'un texte mémorisé, en octets UTF-8. Trente-deux kilo-octets
#: est déjà dix fois la plus grosse mémoire écrite par le kit ; au-delà, ce n'est
#: plus une mémoire, c'est un document — il a un fichier et une source.
MAX_TEXT_BYTES = 32_768

#: Taille maximale des métadonnées sérialisées en JSON, en octets.
MAX_METADATA_BYTES = 16_384

#: Champs de provenance qu'une écriture enrichie doit porter. Ce sont ceux que
#: ``normalize_palace_metadata`` pose systématiquement : les exiger transforme
#: un invariant tacite en garde.
REQUIRED_METADATA_FIELDS: tuple[str, ...] = ("project_name", "source_kind")

#: Types de mémoire acceptés : l'union des collections typées du protocole
#: d'agents (``grimoire.memory.manager.MEMORY_TYPES``) et des types normatifs du
#: standard agentique (``standard_checks.base.REQUIRED_MEMORY_TYPES``). Recopiés
#: ici plutôt qu'importés pour que la validation d'une écriture ne dépende pas
#: du module de scoring du standard.
ALLOWED_MEMORY_TYPES: frozenset[str] = frozenset({
    # protocole d'agents
    "shared-context", "decisions", "agent-learnings", "failures", "stories",
    # standard agentique
    "session", "task", "project", "workspace", "organization", "procedural",
    "semantic", "episodic", "long_term", "external_knowledge_cache",
})

#: Émetteurs connus du kit. ``unspecified`` y figure sciemment : le retirer
#: ferait parler chaque écriture héritée qui ne déclare pas encore sa source,
#: et noierait le signal que ce mode observation existe pour produire.
DEFAULT_ALLOWED_EMITTERS: tuple[str, ...] = (
    "unspecified", "user", "cli", "agent", "hook", "mcp", "migration", "sidecar",
)

# ── Motifs ────────────────────────────────────────────────────────────────────
#
# Chaque motif porte une étiquette : c'est elle qui apparaît dans le texte
# caviardé (``[redacted:aws-access-key-id]``) et dans le journal, de sorte
# qu'une redaction reste auditable sans jamais réécrire le secret.

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{12,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b")),
    ("private-key-block", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    )),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    # Authentification basique dans une URL : seul le couple identifiant/mot de
    # passe est caviardé, le hôte reste lisible.
    ("url-basic-auth", re.compile(r"(?<=://)[^\s/:@]+:[^\s/:@]{6,}(?=@)")),
    # Affectation générique. La valeur doit être compacte (aucune espace), longue
    # (douze caractères au moins) et contenir un chiffre : c'est ce qui distingue
    # `password: Tr0ub4dor3xKm9Qz` de `password policy is documented in ADR-004`.
    ("credential-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?key|auth[_-]?token|"
        r"password|passwd|secret|token)\b\s*[:=]\s*[\"']?(?=[^\s\"']*\d)"
        r"[A-Za-z0-9_\-+/=.~]{12,}[\"']?"
    )),
)

#: Textes qui se présentent comme un tour système ou une réécriture de consigne.
#: Refusés, jamais caviardés : caviarder laisserait la forme et retirerait la
#: preuve.
_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chat-template-marker", re.compile(r"<\|\s*(?:im_start|im_end|system|endoftext)\s*\|>", re.IGNORECASE)),
    ("inst-marker", re.compile(r"\[/?INST\]|<<SYS>>")),
    ("role-header", re.compile(r"(?im)^\s*(?:#{1,6}\s*)?(?:system|assistant|instruction)s?\s*:", re.UNICODE)),
    ("override-en", re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b"
        r"(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}\b(?:instructions?|prompts?|rules?)\b"
    )),
    ("override-fr", re.compile(
        r"(?i)\b(?:ignore[rz]?|oublie[rz]?|passe outre)\b[^.\n]{0,40}\b"
        r"(?:instructions?|consignes?|règles?)\b"
    )),
    ("role-rewrite-en", re.compile(r"(?i)\byou are now\b\s+(?:an?|the)\b")),
    ("role-rewrite-fr", re.compile(r"(?i)\b(?:tu es|vous êtes)\s+(?:désormais|maintenant)\b")),
)


class MemoryWriteRefusedError(GrimoireMemoryError):
    """Une écriture mémoire refusée par la frontière de confiance.

    Porte un ``code`` stable (``memory.text_oversize``…) et un ``remedy`` : un
    refus qui ne dit pas quoi faire est un mur, pas un garde.
    """

    def __init__(self, code: str, message: str, *, remedy: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.remedy = remedy

    def to_dict(self) -> dict[str, Any]:
        """Forme JSON du refus, telle que la rendent la CLI et l'outil MCP."""
        return {
            "error": str(self),
            "refused": True,
            "code": self.code,
            "remedy": self.remedy,
        }


@dataclass(frozen=True, slots=True)
class MemoryWritePolicy:
    """Ce que le projet exige d'une écriture mémoire.

    Les valeurs par défaut sont celles d'un projet qui n'a pas encore de
    ``memory-policy.yaml`` : redaction requise, refus du contenu qui se donne
    pour une consigne, émetteurs en observation. Un défaut permissif ferait de
    ce module un verrou décoratif de plus.
    """

    enabled: bool = True
    max_text_bytes: int = MAX_TEXT_BYTES
    max_metadata_bytes: int = MAX_METADATA_BYTES
    redaction: str = "required"
    emitter_enforcement: str = "observe"
    allowed_emitters: tuple[str, ...] = DEFAULT_ALLOWED_EMITTERS
    refuse_instruction_like_content: bool = True

    @classmethod
    def from_project(cls, project_root: Path | None) -> MemoryWritePolicy:
        """Lire ``_grimoire/standard/memory-policy.yaml`` — lecture tolérante.

        Un fichier absent, illisible ou malformé rend la politique par défaut :
        une écriture mémoire ne doit pas dépendre de la santé d'un YAML de
        gouvernance, et surtout pas s'ouvrir quand il manque.
        """
        if project_root is None:
            return cls()
        path = Path(project_root) / "_grimoire" / "standard" / "memory-policy.yaml"
        raw = _load_write_validation(path)
        if not raw:
            return cls()
        default = cls()
        emitters = raw.get("allowed_emitters")
        return cls(
            enabled=bool(raw.get("enabled", default.enabled)),
            max_text_bytes=_positive_int(raw.get("max_text_bytes"), default.max_text_bytes),
            max_metadata_bytes=_positive_int(raw.get("max_metadata_bytes"), default.max_metadata_bytes),
            redaction=str(raw.get("redaction") or default.redaction),
            emitter_enforcement=str(raw.get("emitter_enforcement") or default.emitter_enforcement),
            allowed_emitters=(
                tuple(str(e) for e in emitters) if isinstance(emitters, list) and emitters
                else default.allowed_emitters
            ),
            refuse_instruction_like_content=bool(
                raw.get("refuse_instruction_like_content", default.refuse_instruction_like_content)
            ),
        )


@dataclass(frozen=True, slots=True)
class ValidatedWrite:
    """Ce qui doit réellement partir vers le backend, après validation."""

    text: str
    metadata: dict[str, Any] | None = None
    redactions: tuple[str, ...] = ()
    emitter: str = "unspecified"
    emitter_recognized: bool = True
    warnings: tuple[str, ...] = field(default=())


def redact_secrets(text: str) -> tuple[str, tuple[str, ...]]:
    """Caviarder les secrets reconnus ; renvoyer le texte et les étiquettes vues.

    Le remplacement est ``[redacted:<étiquette>]`` : la trace dit *quel type*
    de secret a été retiré, jamais sa valeur.
    """
    hits: list[str] = []
    redacted = text
    for label, pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn(f"[redacted:{label}]", redacted)
        if count:
            hits.append(label)
    return redacted, tuple(hits)


def instruction_like(text: str) -> str:
    """L'étiquette du premier motif de consigne rencontré, ou une chaîne vide."""
    for label, pattern in _INSTRUCTION_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def validate_memory_write(
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    emitter: str = "",
    policy: MemoryWritePolicy | None = None,
) -> ValidatedWrite:
    """Valider une écriture mémoire, et renvoyer ce qu'il faut écrire.

    Lève :class:`MemoryWriteRefusedError` — jamais une exception anonyme — dès qu'une
    règle du schéma est violée. Retourne le texte éventuellement caviardé et les
    métadonnées augmentées de la trace de redaction.
    """
    active = policy or MemoryWritePolicy()
    resolved_emitter = (emitter or "").strip() or "unspecified"
    if not active.enabled:
        return ValidatedWrite(text=text, metadata=metadata, emitter=resolved_emitter)

    _check_text_schema(text, active)
    normalized = _check_metadata_schema(metadata, active)

    if active.refuse_instruction_like_content:
        label = instruction_like(text)
        if label:
            raise MemoryWriteRefusedError(
                "memory.instruction_like_content",
                f"Refusé : le texte porte un motif de consigne ({label}). "
                "Une mémoire est une donnée, pas un tour système.",
                remedy="Reformuler en énoncé factuel, ou citer le texte comme extrait attribué à sa source.",
            )

    recognized = resolved_emitter in active.allowed_emitters
    if not recognized:
        if active.emitter_enforcement == "refuse":
            raise MemoryWriteRefusedError(
                "memory.emitter_unrecognized",
                f"Refusé : émetteur {resolved_emitter!r} hors de la liste d'autorisation.",
                remedy="Déclarer l'émetteur dans write_validation.allowed_emitters de memory-policy.yaml.",
            )
        # Mode observation : on trace, on n'arrête pas. C'est la décision 6 du
        # plan d'exécution — l'acteur MCP est encore générique.
        logger.warning(
            "memory.emitter_unrecognized: émetteur %r absent de la liste d'autorisation "
            "(mode observation, écriture acceptée)",
            resolved_emitter,
        )

    final_text = text
    redactions: tuple[str, ...] = ()
    if active.redaction == "required":
        final_text, redactions = redact_secrets(text)
        if redactions:
            logger.warning("memory.redaction_applied: %s", ", ".join(redactions))
            if normalized is None:
                normalized = {}
            normalized = {**normalized, "redactions": list(redactions)}

    return ValidatedWrite(
        text=final_text,
        metadata=normalized,
        redactions=redactions,
        emitter=resolved_emitter,
        emitter_recognized=recognized,
    )


# ── Détail ────────────────────────────────────────────────────────────────────


def _check_text_schema(text: Any, policy: MemoryWritePolicy) -> None:
    if not isinstance(text, str):
        raise MemoryWriteRefusedError(
            "memory.text_type",
            f"Refusé : le texte mémorisé doit être une chaîne, reçu {type(text).__name__}.",
            remedy="Sérialiser la valeur avant de la mémoriser.",
        )
    if not text.strip():
        raise MemoryWriteRefusedError(
            "memory.text_empty",
            "Refusé : une mémoire vide n'est pas une mémoire.",
            remedy="Écrire l'énoncé à retenir.",
        )
    size = len(text.encode("utf-8"))
    if size > policy.max_text_bytes:
        raise MemoryWriteRefusedError(
            "memory.text_oversize",
            f"Refusé : {size} octets dépassent la taille maximale de {policy.max_text_bytes}.",
            remedy="Mémoriser un résumé et référencer le document source par son chemin.",
        )


def _check_metadata_schema(metadata: Any, policy: MemoryWritePolicy) -> dict[str, Any] | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise MemoryWriteRefusedError(
            "memory.metadata_type",
            f"Refusé : les métadonnées doivent être un mapping, reçu {type(metadata).__name__}.",
            remedy="Passer un dictionnaire, ou rien.",
        )
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, default=None)
    except (TypeError, ValueError) as exc:
        raise MemoryWriteRefusedError(
            "memory.metadata_unserializable",
            f"Refusé : métadonnées non sérialisables en JSON ({exc}).",
            remedy="N'y mettre que des scalaires, listes et mappings.",
        ) from exc
    if "null" in encoded and any(
        not isinstance(value, (str, int, float, bool, list, dict, type(None)))
        for value in metadata.values()
    ):
        raise MemoryWriteRefusedError(
            "memory.metadata_unserializable",
            "Refusé : une valeur de métadonnée n'est pas sérialisable en JSON.",
            remedy="N'y mettre que des scalaires, listes et mappings.",
        )
    size = len(encoded.encode("utf-8"))
    if size > policy.max_metadata_bytes:
        raise MemoryWriteRefusedError(
            "memory.metadata_oversize",
            f"Refusé : {size} octets de métadonnées dépassent la limite de {policy.max_metadata_bytes}.",
            remedy="Déplacer le volume dans le texte ou dans un artefact référencé.",
        )
    missing = [key for key in REQUIRED_METADATA_FIELDS if not str(metadata.get(key) or "").strip()]
    if missing:
        raise MemoryWriteRefusedError(
            "memory.metadata_fields_missing",
            f"Refusé : provenance incomplète, champs manquants : {', '.join(missing)}.",
            remedy="Écrire par MemoryManager.store, qui pose la provenance, plutôt qu'en appelant le backend.",
        )
    declared_type = metadata.get("memory_type")
    if declared_type is not None and str(declared_type) not in ALLOWED_MEMORY_TYPES:
        raise MemoryWriteRefusedError(
            "memory.type_unknown",
            f"Refusé : type de mémoire {declared_type!r} hors des types déclarés.",
            remedy=f"Utiliser l'un de : {', '.join(sorted(ALLOWED_MEMORY_TYPES))}.",
        )
    return metadata


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _load_write_validation(path: Path) -> dict[str, Any]:
    """``write_validation`` du fichier, ou un mapping vide — jamais d'exception."""
    if not path.is_file():
        return {}
    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(path)
    except Exception:
        logger.debug("memory-policy.yaml illisible, politique par défaut appliquée", exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("write_validation")
    return block if isinstance(block, dict) else {}
