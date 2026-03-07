#!/usr/bin/env python3
"""Fix hexagon default icons with better context-specific icons."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Header text → better icon (for headers currently showing hexagon)
FIXES = [
    # docs/adr
    ("Raison", "lightbulb"),
    ("Parallélisme réellement disponible", "cognition"),
    ("Conséquences", "clipboard"),
    ("Alternatives rejetées", "clipboard"),
    # docs general
    ("Ressources complémentaires", "rocket"),
    ("Pour aller plus loin", "rocket"),
    ("Bonnes pratiques", "lightbulb"),
    ('Clause "Use when"', "lightbulb"),
    ("Clause 'Use when'", "lightbulb"),
    ("Premiers pas", "bolt"),
    ("Cercle vertueux", "workflow"),
    ("Automatisations", "wrench"),
    ("Table des matières", "clipboard"),
    ("Fichiers et éditions", "wrench"),
    ("Modèles et rate limits", "wrench"),
    ("Diff editor", "wrench"),
    # docs/troubleshooting - numbered items
    ("2. cc-verify", "microscope"),
    ("3. Le pre-commit", "microscope"),
    ("4. ", "microscope"),
    ("5. sil-collect", "microscope"),
    ("6. ", "microscope"),
    ("7. ", "microscope"),
    ("8. Erreur", "microscope"),
    ("9. ", "microscope"),
    ("10. ", "microscope"),
    ("11. ", "microscope"),
    ("12. ", "microscope"),
    ("13. ", "microscope"),
    ("Obtenir de l'aide", "lightbulb"),
    # workflow-taxonomy
    ("Les trois catégories", "workflow"),
    ("Playbook", "workflow"),
    ("Diagramme", "chart"),
    ("Quand utiliser quoi", "lightbulb"),
    # framework/agent-base
    ("Maximes de Communication", "clipboard"),
    ("Camouflage Adaptatif", "team"),
    ("Wabi-sabi", "lightbulb"),
    ("Activation Steps", "bolt"),
    ("Menu Handlers", "wrench"),
    ("Menu Items Standard", "wrench"),
    ("Contradiction Resolution", "seal"),
    # framework/agent-mesh-network
    ("Protocole d'Enregistrement", "clipboard"),
    ("Communication Peer-to-Peer", "network"),
    ("P2P", "network"),
    ("Découverte de Services", "network"),
    ("Load Balancing", "network"),
    ("Observabilité", "chart"),
    # framework/agent-relationship-graph
    ("Structure du Graphe", "folder-tree"),
    ("Enrichissement Dynamique", "dna"),
    ("Bootstrap", "bolt"),
    # framework/bmad-trace
    ("Types d'événements", "workflow"),
    ("Protocole de lecture", "microscope"),
    ("Utilisation enterprise", "rocket"),
    # framework/cc-reference
    ("Tableau de vérification", "seal"),
    ("Script automatique", "wrench"),
    # framework/context-router
    ("Problème", "microscope"),
    ("Solution : Priorité", "lightbulb"),
    ("Niveaux de priorité", "chart"),
    ("Estimation de tokens", "chart"),
    # framework/copilot-extension
    ("Implémentation du handler", "wrench"),
    ("Roadmap", "rocket"),
    ("Dépendances avec", "network"),
    # framework/cross-validation-trust
    ("Sélection du Validateur", "seal"),
    # framework/event-log-shared-state
    ("Types d'Événements", "workflow"),
    ("Protocole d'Émission", "workflow"),
    ("Protocole d'Observation", "chart"),
    ("Garbage Collection", "wrench"),
    # framework/honest-uncertainty-protocol
    ("Pre-flight Check", "seal"),
    ("Anti-Évitement", "shield-pulse"),
    # framework/hybrid-parallelism-engine
    ("DAG de Tâches", "workflow"),
    ("Chemin Critique", "workflow"),
    ("Gestion des Échecs", "resilience"),
    ("Visualisation du DAG", "chart"),
    ("Intégration avec les Protocoles", "integration"),
    # framework/mcp
    ("Roadmap", "rocket"),
    # framework/orchestrator-gateway
    ("Intégration avec les Protocoles", "integration"),
    # framework/productive-conflict-engine
    ("Mécanisme de Vote", "team"),
    ("Rôles Dynamiques", "team"),
    # framework/question-escalation-chain
    ("Phase 1", "workflow"),
    ("Phase 2", "workflow"),
    ("Phase 3", "workflow"),
    ("Redistribution des Réponses", "boomerang"),
    # framework/selective-huddle-protocol
    ("Déclencheurs Automatiques", "bolt"),
    ("Sélection des Participants", "team"),
    # framework/sessions
    ("Structure", "folder-tree"),
    # framework/workflows/incident-response
    ("VARIANTES", "puzzle"),
    ("ÉTAPE 1", "workflow"),
    ("ÉTAPE 2", "workflow"),
    ("ÉTAPE 3", "workflow"),
    ("NOTES DE MODÉRATION", "clipboard"),
    # framework/workflows/repo-map-generator
    ("Déclenchement", "bolt"),
    ("Stratégies de génération", "lightbulb"),
    ("Mise à jour automatique", "workflow"),
    # framework/workflows/state-checkpoint
    ("Protocole de Checkpoint", "branch"),
    ("Cleanup", "wrench"),
    # framework/workflows/subagent-orchestration
    ("Stratégies de Merge", "workflow"),
    # archetypes/features/vector-memory
    ("Capacités", "sparkle"),
    ("Variables d'environnement", "wrench"),
    ("Déploiement", "rocket"),
    # archetypes/fix-loop
    ("Qu'est-ce que c'est", "lightbulb"),
    ("Ce que ça apporte", "sparkle"),
    ("Fichiers inclus", "folder-tree"),
    ("Comment ça marche", "workflow"),
    # examples
    ("Ce qui a été personnalisé", "wrench"),
]


def fix_file(filepath: Path) -> int:
    """Fix hexagon icons in a file. Returns number of fixes."""
    content = filepath.read_text(encoding="utf-8")
    fixed = 0

    for title_fragment, icon_name in FIXES:
        # Match: icons/hexagon.svg" ... alt=""> ...Title Fragment...
        pattern = re.compile(
            r'(icons/)hexagon(\.svg"[^>]*>) ' + re.escape(title_fragment),
            re.IGNORECASE,
        )
        new_content = pattern.sub(
            rf'\g<1>{icon_name}\g<2> {title_fragment}', content
        )
        if new_content != content:
            fixed += 1
            content = new_content

    if fixed:
        filepath.write_text(content, encoding="utf-8")
    return fixed


def main():
    import glob

    total_fixes = 0
    files_fixed = 0

    patterns = [
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "docs/*.md",
        "framework/*.md",
        "framework/**/*.md",
        "archetypes/**/*.md",
        "examples/**/*.md",
    ]

    all_files = set()
    for pat in patterns:
        all_files.update(PROJECT_ROOT.glob(pat))

    for filepath in sorted(all_files):
        if filepath.suffix != ".md" or filepath.name.endswith(".tpl.md"):
            continue
        fixes = fix_file(filepath)
        if fixes:
            rel = filepath.relative_to(PROJECT_ROOT)
            print(f"  {rel}: {fixes} icon(s) fixed")
            total_fixes += fixes
            files_fixed += 1

    print(f"\n{total_fixes} icons fixed across {files_fixed} files.")


if __name__ == "__main__":
    main()
