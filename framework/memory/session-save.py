#!/usr/bin/env python3
"""
Grimoire Session Saver — Persiste l'état de session dans session-state.md.

Usage:
    python session-save.py <agent_tag> [--work "description"] [--files "f1,f2"] [--next "recommandation"]

Exemples:
    python session-save.py forge --work "Déployé monitoring complet" --files "docker-compose.yml,prometheus.yml" --next "Vérifier les targets"
    python session-save.py hawk --work "Créé dashboard Docker" --next "Tester l'alerting"

Peut aussi être appelé programmatiquement :
    from session_save import save_session
    save_session("forge", work=["action1"], files=["file1.yml"], next_step="...")
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path(__file__).parent
SESSION_FILE = MEMORY_DIR / "session-state.md"
SESSION_SUMMARIES_DIR = MEMORY_DIR / "session-summaries"


def _load_valid_agents() -> set[str]:
    """Charge les noms d'agents valides depuis agent-manifest.csv."""
    manifest = MEMORY_DIR.parent / "_config" / "agent-manifest.csv"
    agents = set()
    if manifest.exists():
        with open(manifest, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("name", "").strip()
                if name:
                    agents.add(name)
    # Fallback meta agents si manifest vide
    if not agents:
        agents = {"atlas", "sentinel", "mnemo"}
    return agents


VALID_AGENTS = _load_valid_agents()


def _get_project_name() -> str:
    """Récupère le nom du projet depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return "Grimoire Project"
    for parent in [MEMORY_DIR.parent.parent, MEMORY_DIR.parent.parent.parent]:
        ctx_file = parent / "project-context.yaml"
        if ctx_file.exists():
            with open(ctx_file, encoding="utf-8") as f:
                ctx = yaml.safe_load(f) or {}
                return ctx.get("project", {}).get("name", "Grimoire Project")
    return "Grimoire Project"


def save_session(
    agent: str,
    work: list[str] | None = None,
    files: list[str] | None = None,
    state: str = "",
    next_step: str = "",
    handoffs: list[str] | None = None,
    duration: str = "—",
) -> None:
    """Écrit session-state.md et archive dans session-summaries/."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M")
    date_file = now.strftime("%Y%m%d-%H%M")
    project_name = _get_project_name()

    work_items = work or ["(aucune action enregistrée)"]
    file_items = files or ["(aucun)"]
    handoff_items = handoffs or ["(aucune)"]

    content = f"""# État de Session — {project_name}

> Mis à jour automatiquement par chaque agent en fin de session.
> Chargé à l'activation pour assurer la continuité inter-sessions.
> Un seul état actif à la fois (écrasé à chaque session).

## Dernière Session

| Champ | Valeur |
|-------|--------|
| **Agent** | {agent} |
| **Date** | {date_str} |
| **Durée estimée** | {duration} |

## Travail effectué

> Liste des actions réalisées pendant la session

{chr(10).join(f"- {w}" for w in work_items)}

## Fichiers modifiés

> Chemins des fichiers créés ou modifiés

{chr(10).join(f"- {f}" for f in file_items)}

## État actuel

> Où en est-on ? Qu'est-ce qui marche, qu'est-ce qui est cassé ?

{state or "(aucun état enregistré)"}

## Prochaine étape recommandée

> Que devrait faire le prochain agent (ou le même) ?

{next_step or "(aucune recommandation)"}

## Requêtes inter-agents générées

> Requêtes ajoutées dans shared-context.md pendant cette session

{chr(10).join(f"- {h}" for h in handoff_items)}
"""

    # Write current session state
    SESSION_FILE.write_text(content)
    print(f"✅ Session sauvegardée dans session-state.md")
    print(f"   Agent: {agent} | Date: {date_str}")

    # Archive in session-summaries/
    SESSION_SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)
    archive_file = SESSION_SUMMARIES_DIR / f"{date_file}-{agent}.md"
    archive_file.write_text(content)
    print(f"   Archive: session-summaries/{archive_file.name}")


def main():
    parser = argparse.ArgumentParser(description="Grimoire Session Saver")
    parser.add_argument("agent", help=f"Tag de l'agent ({', '.join(sorted(VALID_AGENTS))})")
    parser.add_argument("--work", type=str, default="", help="Description du travail (séparé par |)")
    parser.add_argument("--files", type=str, default="", help="Fichiers modifiés (séparé par ,)")
    parser.add_argument("--state", type=str, default="", help="État actuel du système")
    parser.add_argument("--next", type=str, default="", help="Prochaine étape recommandée")
    parser.add_argument("--handoffs", type=str, default="", help="Requêtes inter-agents (séparé par |)")
    parser.add_argument("--duration", type=str, default="—", help="Durée estimée de la session")

    args = parser.parse_args()

    if args.agent not in VALID_AGENTS:
        print(f"❌ Agent inconnu: '{args.agent}'. Agents valides: {', '.join(sorted(VALID_AGENTS))}")
        sys.exit(1)

    work = [w.strip() for w in args.work.split("|") if w.strip()] or None
    files = [f.strip() for f in args.files.split(",") if f.strip()] or None
    handoffs = [h.strip() for h in args.handoffs.split("|") if h.strip()] or None

    save_session(
        agent=args.agent,
        work=work,
        files=files,
        state=args.state,
        next_step=args.next,
        handoffs=handoffs,
        duration=args.duration,
    )


if __name__ == "__main__":
    main()
