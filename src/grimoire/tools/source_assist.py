"""Suggestions de contenu par un petit modèle local (Ollama) — issue #280, voie 2.

La voie 1 (``workspace_language.py``) est l'IntelliSense déterministe de
l'espace Source : tokens, diagnostics, complétions, sans aucune notion d'IA.
Ce module y ajoute une seconde voie, strictement derrière la première et
jamais à sa place : un petit modèle local propose du *texte*, mais ne fait
jamais autorité sur les identifiants, chemins ou diagnostics — ce module
renvoie la proposition brute du modèle plus la liste de ce qu'elle cite sans
que ce soit connu du paquet de langage (agents installés, workflows,
patterns, chemins). L'interface (``spaces/source-editor.js``) affiche cette
liste plutôt que de faire confiance au texte tel quel, et n'écrit jamais rien
sans un geste explicite (« Insérer »).

Refus, dans l'ordre où ils sont vérifiés :

1. opt-in absent (``project-context.yaml: source.assist.model`` vide) — la
   fonctionnalité n'existe pas tant que rien ne l'a déclarée, quelle que soit
   la présence d'Ollama sur le poste.
2. chemin hors des étages de Source, ou intention inconnue — :class:`ValueError`/
   ``WorkspacePathError``, traduits en 400/403 par :mod:`workspace_routes`.
3. Ollama indisponible, ou le modèle configuré absent de ``ollama list`` (même
   sonde que ``grimoire providers audit``, :mod:`grimoire.providers.audit`) —
   refus discret, jamais une exception : ``{"available": False, "reason": …}``.
4. délai dépassé (10 s) ou erreur réseau pendant l'appel — même forme de refus
   discret.

Aucun appel à un fournisseur distant n'est possible depuis ce module : la
seule URL jamais contactée est celle d'Ollama en local
(:func:`grimoire.providers.audit.ollama_base_url`, par défaut
``http://127.0.0.1:11434``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from grimoire.core import integrity
from grimoire.core.config import GrimoireConfig
from grimoire.core.exceptions import GrimoireConfigError
from grimoire.providers.audit import ollama_base_url, probe_ollama_models
from grimoire.tools import workspace_api, workspace_language

__all__ = ["ASSIST_TIMEOUT_S", "INTENTS", "assist_enabled_model", "assist_status", "assist_view"]

#: Les trois intentions que l'éditeur peut demander (spec issue #280, voie 2).
INTENTS = ("complete-clause", "draft-body", "explain-diagnostic")

#: Délai borné de l'appel à Ollama — un modèle qui ne répond pas en 10 s ne
#: doit pas bloquer l'éditeur davantage qu'un dépôt distant injoignable.
ASSIST_TIMEOUT_S = 10.0

#: Lignes de contexte gardées autour du curseur pour ``draft-body`` — assez
#: pour que le modèle comprenne le paragraphe en cours, jamais tout le
#: fichier (le prompt reste borné, et le fichier courant peut dépasser ce
#: qu'un petit modèle local traite correctement).
_CONTEXT_LINES = 12


def assist_enabled_model(project_root: Path) -> str:
    """Le modèle configuré par ``source.assist.model``, ou ``""`` si absent.

    Lit ``project-context.yaml`` avec la même tolérance que
    :func:`workspace_api.agents_view` : un projet sans config lisible n'a pas
    l'assistance active, ce n'est pas une erreur de transport.
    """
    try:
        cfg = GrimoireConfig.from_yaml(project_root / "project-context.yaml")
    except GrimoireConfigError:
        return ""
    return cfg.source.assist.model


def _readiness(project_root: Path) -> tuple[str, str, dict[str, Any] | None]:
    """``(model, base_url, refusal)`` — porte commune à la lecture et à l'écriture.

    ``refusal`` est ``None`` quand un appel à Ollama peut être tenté ; sinon
    c'est déjà la charge utile complète à rendre (``enabled``, ``model``,
    ``available: False``, ``reason``). Partagée par :func:`assist_status`
    (lecture, jamais de coût — l'interface l'appelle pour savoir si le bouton
    « Suggérer » doit même apparaître) et :func:`assist_view` (écriture, qui
    appelle réellement le modèle une fois cette porte franchie) : la même
    vérification d'opt-in et de présence Ollama, jamais deux.
    """
    root = project_root.resolve()
    model = assist_enabled_model(root)
    if not model:
        return "", "", {
            "enabled": False,
            "model": "",
            "available": False,
            "reason": "assistance désactivée : définissez `source.assist.model` dans project-context.yaml",
        }

    base_url = ollama_base_url()
    models_seen, probe_note = probe_ollama_models(base_url)
    if not models_seen and probe_note:
        return model, base_url, {"enabled": True, "model": model, "available": False, "reason": probe_note}
    if models_seen and model not in models_seen:
        return model, base_url, {
            "enabled": True,
            "model": model,
            "available": False,
            "reason": f"modèle {model!r} absent de `ollama list` — présents : {', '.join(models_seen)}",
        }
    return model, base_url, None


def assist_status(project_root: Path) -> dict[str, Any]:
    """Point d'entrée de ``GET /api/workspace/assist`` — jamais de coût.

    Ne fait ni opt-in ni sonde autre que celle déjà nécessaire pour savoir si
    l'interface doit montrer le bouton « Suggérer » : pas d'appel à
    ``/api/generate``. C'est la garde de la spec (« sinon l'interface ne
    montre rien et ne tente rien ») — l'éditeur appelle cette lecture au
    montage, jamais l'écriture, tant que rien n'a été cliqué.
    """
    model, _base_url, refusal = _readiness(project_root)
    if refusal is not None:
        return refusal
    return {"enabled": True, "model": model, "available": True, "reason": None}


def _language_facts(project_root: Path) -> dict[str, tuple[str, ...]]:
    """Les identifiants connus du paquet de langage — la même vérité que
    ``workspace_language`` sert déjà à l'IntelliSense déterministe.

    Injectés dans le prompt pour orienter le modèle, et relus après coup pour
    marquer ce qu'il aurait inventé quand même (:func:`_flag_unknown`).
    """
    from grimoire.hosts import collect
    from grimoire.workflows.registry import load_workflows

    root = project_root.resolve()
    skills = collect.collect_skills(root)
    workflows = load_workflows(root)
    patterns = workspace_language.pattern_catalogue_ids()
    return {
        "agents": tuple(sorted(integrity.installed_agent_tags(root))),
        "skills": tuple(sorted(s.slug for s in skills)),
        "workflows": tuple(sorted(w.slug for w in workflows)),
        "patterns": tuple(sorted(patterns)) if patterns is not None else (),
    }


def _context_window(text: str, line: int) -> str:
    lines = text.split("\n")
    start = max(0, line - _CONTEXT_LINES)
    end = min(len(lines), line + _CONTEXT_LINES + 1)
    return "\n".join(lines[start:end])


def _build_prompt(
    intent: str,
    rel_path: str,
    text: str,
    line: int,
    facts: dict[str, tuple[str, ...]],
    diagnostic: dict[str, Any] | None,
) -> str:
    """Construit le prompt envoyé au modèle local.

    N'y entrent que le fichier courant (une fenêtre de contexte autour du
    curseur, pas le projet entier) et les identifiants du paquet de langage —
    jamais le contenu d'un autre fichier, jamais une donnée hors de ce que
    l'éditeur affiche déjà (spec issue #280, voie 2, §« ce qui est refusé »).
    """
    known = (
        f"Agents installés : {', '.join(facts['agents']) or '(aucun)'}\n"
        f"Skills disponibles : {', '.join(facts['skills']) or '(aucun)'}\n"
        f"Workflows connus : {', '.join(facts['workflows']) or '(aucun)'}\n"
        f"Patterns du catalogue : {', '.join(facts['patterns']) or '(aucun)'}"
    )
    rules = (
        "Ne cite un agent, un workflow, un pattern ou un chemin `_grimoire/…` "
        "que s'il figure explicitement ci-dessus ou dans le fichier ; sinon, "
        "n'invente pas d'identifiant. Réponds en français, en texte brut, "
        "sans balise Markdown de code autour de la réponse."
    )
    window = _context_window(text, line)

    if intent == "complete-clause":
        task = (
            "Complète la clause d'emploi (`use_when`/`dont_use_when`) d'un agent "
            "Grimoire, en une phrase courte, à partir de son contexte ci-dessous."
        )
    elif intent == "explain-diagnostic":
        diag = diagnostic or {}
        task = (
            "Explique en une ou deux phrases, pour un développeur, ce diagnostic "
            f"de l'espace Source : famille « {diag.get('family', '?')} », "
            f"message « {diag.get('message', '?')} »."
        )
    else:
        task = (
            "Rédige un court paragraphe de contenu (persona, étape de workflow, "
            "description) qui prolonge naturellement le texte ci-dessous, dans "
            "le style du reste du fichier."
        )

    return (
        f"{task}\n\nFichier : {rel_path}\n\n{known}\n\n{rules}\n\n"
        f"Extrait du fichier autour du curseur :\n---\n{window}\n---\n"
    )


def _call_ollama(base_url: str, model: str, prompt: str) -> str | None:
    """Appelle ``POST /api/generate`` sans streaming, borné à
    :data:`ASSIST_TIMEOUT_S`. Rend ``None`` sur toute panne — jamais une
    exception qui remonterait en 500 (spec : « une erreur ou un dépassement =
    message discret, jamais une exception »)."""
    url = f"{base_url}/api/generate"
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — URL locale (Ollama), jamais distante
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=ASSIST_TIMEOUT_S) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError):
        return None
    text = data.get("response") if isinstance(data, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else None


def _flag_unknown(project_root: Path, suggestion: str, facts: dict[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    """Identifiants cités dans *suggestion* mais absents du paquet de langage.

    Réutilise le même tokenizer que l'IntelliSense déterministe
    (:func:`workspace_language.tokenize`) sur le texte de la suggestion —
    jamais une seconde grammaire pour reconnaître un agent, un workflow, un
    pattern ou un chemin. Un token repéré mais absent des faits connus est
    rendu tel quel : c'est à l'interface de le marquer « inconnu », ce module
    ne réécrit jamais le texte du modèle.
    """
    root = project_root.resolve()
    tokens = workspace_language.tokenize("assist-suggestion.md", suggestion, frozenset())
    agents = frozenset(facts["agents"])
    workflows = frozenset(facts["workflows"])
    patterns = frozenset(facts["patterns"])
    out: list[dict[str, Any]] = []
    for tok in tokens:
        unknown = (
            (tok.kind == "agent" and tok.strict and tok.text not in agents)
            or (tok.kind == "workflow" and tok.text not in workflows)
            or (tok.kind == "pattern" and bool(patterns) and tok.text not in patterns)
            or (tok.kind == "path" and integrity.target_is_dead(root, tok.text))
        )
        if unknown:
            out.append({"text": tok.text, "kind": tok.kind, "line": tok.line, "start": tok.start, "end": tok.end})
    return out


def assist_view(project_root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Point d'entrée de ``POST /api/workspace/assist``.

    Rend toujours un 200 avec ``available`` — ``False`` porte le refus
    (opt-in absent, Ollama indisponible, modèle absent, délai dépassé) sous
    forme de message, jamais une exception 5xx : seuls un chemin refusé ou
    une intention/un corps invalides restent des erreurs de requête (400/403,
    traduites par :mod:`workspace_routes`).
    """
    root = project_root.resolve()
    target = workspace_api.safe_relpath(root, body.get("path"))
    tier = workspace_api.tier_of(root, target)
    if tier is None:
        raise workspace_api.WorkspacePathError("ce chemin n'appartient à aucun étage de la vue Source")
    rel = target.relative_to(root).as_posix()

    intent = str(body.get("intent", "")).strip()
    if intent not in INTENTS:
        raise ValueError(f"intention inconnue : {intent!r} — parmi {', '.join(INTENTS)}")

    text = body.get("text")
    if not isinstance(text, str):
        raise ValueError("`text` requis")
    if len(text.encode("utf-8")) > workspace_api.FILE_TEXT_LIMIT:
        raise ValueError("contenu trop volumineux")

    raw_position = body.get("position")
    position: dict[str, Any] = raw_position if isinstance(raw_position, dict) else {}
    try:
        line = int(position.get("line", 0))
    except (TypeError, ValueError):
        line = 0

    model, base_url, refusal = _readiness(root)
    if refusal is not None:
        return {"available": False, "reason": refusal["reason"]}

    facts = _language_facts(root)
    diagnostic = body.get("diagnostic") if intent == "explain-diagnostic" else None
    prompt = _build_prompt(intent, rel, text, line, facts, diagnostic if isinstance(diagnostic, dict) else None)
    suggestion = _call_ollama(base_url, model, prompt)
    if suggestion is None:
        return {"available": False, "reason": f"le modèle local {model!r} n'a pas répondu à temps"}

    return {
        "available": True,
        "model": model,
        "intent": intent,
        "suggestion": suggestion,
        "unknown": _flag_unknown(root, suggestion, facts),
    }
