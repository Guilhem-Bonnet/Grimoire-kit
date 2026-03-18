#!/usr/bin/env python3
"""
self-healing.py — Auto-réparation des workflows Grimoire.
=======================================================

Quand un workflow échoue, ce système tente de réparer avant d'escalader :
  1. Retry : ré-essayer l'étape échouée (transient errors)
  2. Alternative : trouver un chemin alternatif
  3. Rollback : revenir à un état cohérent connu
  4. Diagnose : identifier la cause et proposer une correction

Inspiré de la cicatrisation : le système se répare lui-même.

Features :
  1. `diagnose` — Diagnostiquer un échec workflow
  2. `heal`     — Tenter la réparation automatique
  3. `history`  — Historique des réparations
  4. `playbook` — Afficher les stratégies de réparation connues
  5. `status`   — État de santé des workflows

Usage :
  python3 self-healing.py --project-root . diagnose --error "file not found: stories/STORY-42.md"
  python3 self-healing.py --project-root . heal --error "merge conflict in shared-context.md"
  python3 self-healing.py --project-root . history
  python3 self-healing.py --project-root . playbook
  python3 self-healing.py --project-root . status
  python3 self-healing.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

SELF_HEALING_VERSION = "1.1.0"
HEALING_LOG = "healing-log.json"

# Stratégies de réparation
class Strategy:
    RETRY = "🔄 RETRY"
    ALTERNATIVE = "🔀 ALTERNATIVE"
    ROLLBACK = "⏪ ROLLBACK"
    CREATE = "📝 CREATE"
    FIX = "🔧 FIX"
    ESCALATE = "🆘 ESCALATE"


# ── Healing Playbook ────────────────────────────────────────────────────────

PLAYBOOK = [
    # File not found
    {
        "id": "HE-001",
        "pattern": r"(?:file\s+not\s+found|FileNotFoundError|no\s+such\s+file|introuvable)",
        "name": "Fichier manquant",
        "strategy": Strategy.CREATE,
        "description": "Un fichier requis est manquant",
        "actions": [
            "Vérifier si le fichier attendu a été renommé (git log --diff-filter=R)",
            "Chercher un template correspondant dans framework/prompt-templates/",
            "Créer le fichier depuis le template si disponible",
            "Si pas de template, créer un fichier minimal avec les sections obligatoires",
        ],
        "auto_heal": True,
    },
    # Merge conflict
    {
        "id": "HE-002",
        "pattern": r"(?:merge\s+conflict|conflict\s+in|<<<<<<)",
        "name": "Conflit de merge",
        "strategy": Strategy.ROLLBACK,
        "description": "Conflit de merge détecté",
        "actions": [
            "Identifier les fichiers en conflit (git diff --name-only --diff-filter=U)",
            "Si fichier mémoire : prendre la version la plus récente (ours)",
            "Si fichier code : escalader pour résolution manuelle",
            "Après résolution : vérifier la cohérence via memory-lint",
        ],
        "auto_heal": False,
    },
    # Permission denied
    {
        "id": "HE-003",
        "pattern": r"(?:permission\s+denied|PermissionError|EACCES)",
        "name": "Permission refusée",
        "strategy": Strategy.FIX,
        "description": "Problème de permissions fichier",
        "actions": [
            "Vérifier les permissions du fichier (ls -la)",
            "Si script : ajouter le bit d'exécution (chmod +x)",
            "Si dossier _grimoire : vérifier que l'utilisateur a les droits",
        ],
        "auto_heal": True,
    },
    # Invalid YAML/JSON
    {
        "id": "HE-004",
        "pattern": r"(?:yaml\.scanner|JSONDecodeError|invalid\s+yaml|parse\s+error|syntax\s+error)",
        "name": "Erreur de parsing",
        "strategy": Strategy.ROLLBACK,
        "description": "Fichier de config corrompu",
        "actions": [
            "Identifier le fichier corrompu",
            "Tenter de récupérer depuis git (git checkout HEAD -- <file>)",
            "Si pas dans git : recréer depuis le template",
            "Valider le fichier réparé avec le schema-validator",
        ],
        "auto_heal": True,
    },
    # Memory corruption / contradiction
    {
        "id": "HE-005",
        "pattern": r"(?:contradiction|inconsist|corrupt|mismatch|incohéren)",
        "name": "Incohérence mémoire",
        "strategy": Strategy.FIX,
        "description": "Contradiction ou corruption dans la mémoire",
        "actions": [
            "Lancer memory-lint pour identifier les incohérences",
            "Comparer les versions avec git diff",
            "Si contradiction dans shared-context : utiliser la section decisions comme vérité",
            "Loguer la contradiction dans contradiction-log.md",
        ],
        "auto_heal": False,
    },
    # Tool/Command not found
    {
        "id": "HE-006",
        "pattern": r"(?:command\s+not\s+found|ModuleNotFoundError|not\s+installed|tool.*missing)",
        "name": "Outil manquant",
        "strategy": Strategy.ALTERNATIVE,
        "description": "Un outil requis n'est pas installé",
        "actions": [
            "Identifier l'outil manquant",
            "Vérifier si un équivalent est disponible (which, command -v)",
            "Proposer l'installation via le gestionnaire de paquets",
            "Si outil Grimoire : vérifier que le PATH inclut framework/tools/",
        ],
        "auto_heal": False,
    },
    # Timeout
    {
        "id": "HE-007",
        "pattern": r"(?:timeout|timed?\s*out|deadline\s+exceeded|too\s+long)",
        "name": "Timeout / Délai dépassé",
        "strategy": Strategy.RETRY,
        "description": "Opération trop longue",
        "actions": [
            "Réessayer avec un timeout plus long",
            "Vérifier la charge système (top, free -h)",
            "Si git : vérifier la connexion réseau",
            "Limiter le scope de l'opération (--changed-only, --quick)",
        ],
        "auto_heal": True,
    },
    # Empty/Missing section
    {
        "id": "HE-008",
        "pattern": r"(?:missing\s+section|empty\s+(?:section|field)|required\s+field|manquant)",
        "name": "Section/champ manquant",
        "strategy": Strategy.CREATE,
        "description": "Un artefact est incomplet",
        "actions": [
            "Identifier la section manquante",
            "Chercher la structure attendue dans le template",
            "Ajouter la section avec un placeholder TODO",
            "Marquer l'artefact comme incomplet dans le session-state",
        ],
        "auto_heal": True,
    },
    # Disk space
    {
        "id": "HE-009",
        "pattern": r"(?:no\s+space|disk\s+full|ENOSPC|espace\s+disque)",
        "name": "Espace disque insuffisant",
        "strategy": Strategy.FIX,
        "description": "Espace disque insuffisant",
        "actions": [
            "Vérifier l'espace disque (df -h)",
            "Nettoyer les caches Grimoire (_grimoire-output/test-artifacts/)",
            "Nettoyer les fichiers temporaires (git gc, __pycache__)",
        ],
        "auto_heal": False,
    },
    # Generic / Unknown
    {
        "id": "HE-999",
        "pattern": r".*",
        "name": "Erreur non catégorisée",
        "strategy": Strategy.ESCALATE,
        "description": "Erreur non reconnue — escalade requise",
        "actions": [
            "Collecter les logs complets de l'erreur",
            "Vérifier les logs git récents (git log --oneline -10)",
            "Lancer un diagnostic complet (preflight-check + memory-lint)",
            "Documenter dans failure-museum pour apprentissage futur",
        ],
        "auto_heal": False,
    },
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Diagnosis:
    """Diagnostic d'un problème."""
    error: str
    matched_rule: str = ""
    rule_name: str = ""
    strategy: str = ""
    actions: list[str] = field(default_factory=list)
    auto_healable: bool = False
    healed: bool = False
    heal_result: str = ""

    def to_dict(self) -> dict:
        return {
            "error": self.error[:200],
            "rule": self.matched_rule,
            "name": self.rule_name,
            "strategy": self.strategy,
            "actions": self.actions,
            "auto_healable": self.auto_healable,
            "healed": self.healed,
            "result": self.heal_result,
        }


@dataclass
class HealingRecord:
    """Enregistrement d'une tentative de guérison."""
    timestamp: str
    error: str
    rule_id: str
    strategy: str
    success: bool
    detail: str = ""


# ── Diagnosis Engine ────────────────────────────────────────────────────────

def diagnose_error(error: str) -> Diagnosis:
    """Diagnostique une erreur et propose une stratégie."""
    for rule in PLAYBOOK:
        if re.search(rule["pattern"], error, re.IGNORECASE):
            return Diagnosis(
                error=error,
                matched_rule=rule["id"],
                rule_name=rule["name"],
                strategy=rule["strategy"],
                actions=rule["actions"],
                auto_healable=rule.get("auto_heal", False),
            )

    # Fallback
    return Diagnosis(
        error=error,
        matched_rule="HE-999",
        rule_name="Erreur non catégorisée",
        strategy=Strategy.ESCALATE,
        actions=PLAYBOOK[-1]["actions"],
        auto_healable=False,
    )


# ── Auto-Healing ────────────────────────────────────────────────────────────

def attempt_heal(project_root: Path, diagnosis: Diagnosis) -> Diagnosis:
    """Tente une réparation automatique."""
    if not diagnosis.auto_healable:
        diagnosis.heal_result = "Non auto-réparable — intervention manuelle requise"
        return diagnosis

    rule_id = diagnosis.matched_rule

    if rule_id == "HE-001":  # Fichier manquant
        # Extraire le nom du fichier
        file_match = re.search(r'(?:file|fichier)[:\s]+([^\s]+)', diagnosis.error, re.IGNORECASE)
        if file_match:
            missing_file = file_match.group(1)
            target = project_root / missing_file
            if not target.exists():
                # Chercher un template
                templates_dir = project_root / "framework" / "prompt-templates"
                stem = target.stem.split("-")[0].lower()
                template = None
                if templates_dir.exists():
                    for tpl in templates_dir.glob(f"*{stem}*"):
                        template = tpl
                        break

                if template:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        template.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    diagnosis.healed = True
                    diagnosis.heal_result = f"Fichier créé depuis template {template.name}"
                else:
                    # Créer un fichier minimal
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        f"# {target.stem}\n\n> TODO: Compléter ce fichier\n",
                        encoding="utf-8",
                    )
                    diagnosis.healed = True
                    diagnosis.heal_result = f"Fichier minimal créé : {missing_file}"

    elif rule_id == "HE-003":  # Permission
        file_match = re.search(r'(?:file|script)[:\s]+([^\s]+)', diagnosis.error, re.IGNORECASE)
        if file_match:
            target = project_root / file_match.group(1)
            if target.exists():
                import stat
                current = target.stat().st_mode
                target.chmod(current | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
                diagnosis.healed = True
                diagnosis.heal_result = f"Permissions corrigées sur {file_match.group(1)}"

    elif rule_id == "HE-007":  # Timeout
        diagnosis.healed = False
        diagnosis.heal_result = "Retry recommandé avec --timeout plus long ou --quick"

    elif rule_id == "HE-008":  # Section manquante
        diagnosis.healed = False
        diagnosis.heal_result = "Section TODO ajoutée — compléter manuellement"

    if not diagnosis.heal_result:
        diagnosis.heal_result = "Réparation tentée mais non concluante"

    return diagnosis


# ── History ─────────────────────────────────────────────────────────────────

def load_history(project_root: Path) -> list[HealingRecord]:
    """Charge l'historique des guérisons."""
    log_file = project_root / "_grimoire" / "_memory" / HEALING_LOG
    if not log_file.exists():
        return []
    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
        return [
            HealingRecord(**rec) for rec in data.get("records", [])
        ]
    except (json.JSONDecodeError, OSError, TypeError):
        return []


def save_to_history(project_root: Path, diagnosis: Diagnosis):
    """Sauvegarde une tentative dans l'historique."""
    records = load_history(project_root)
    records.append(HealingRecord(
        timestamp=datetime.now().isoformat(),
        error=diagnosis.error[:200],
        rule_id=diagnosis.matched_rule,
        strategy=diagnosis.strategy,
        success=diagnosis.healed,
        detail=diagnosis.heal_result,
    ))

    log_file = project_root / "_grimoire" / "_memory" / HEALING_LOG
    log_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": SELF_HEALING_VERSION,
        "records": [
            {
                "timestamp": r.timestamp,
                "error": r.error,
                "rule_id": r.rule_id,
                "strategy": r.strategy,
                "success": r.success,
                "detail": r.detail,
            }
            for r in records[-50:]  # Garder les 50 derniers
        ],
    }
    log_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Formatters ───────────────────────────────────────────────────────────────

def format_diagnosis(diag: Diagnosis) -> str:
    lines = [
        "🏥 Diagnostic Self-Healing",
        f"   Erreur : {diag.error[:120]}",
        f"   Règle : [{diag.matched_rule}] {diag.rule_name}",
        f"   Stratégie : {diag.strategy}",
        f"   Auto-réparable : {'oui' if diag.auto_healable else 'non'}",
        "",
        "   Actions recommandées :",
    ]
    for i, action in enumerate(diag.actions, 1):
        lines.append(f"      {i}. {action}")

    if diag.healed:
        lines.append(f"\n   ✅ Réparé : {diag.heal_result}")
    elif diag.heal_result:
        lines.append(f"\n   ⚠️ {diag.heal_result}")

    return "\n".join(lines)


def format_playbook() -> str:
    lines = ["📋 Self-Healing Playbook — Stratégies de réparation", ""]
    for rule in PLAYBOOK:
        auto = "✅ auto" if rule.get("auto_heal") else "👤 manuel"
        lines.append(f"   [{rule['id']}] {rule['name']}  {rule['strategy']}  ({auto})")
        lines.append(f"      {rule['description']}")
        for a in rule["actions"][:2]:
            lines.append(f"      → {a}")
        lines.append("")
    return "\n".join(lines)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_diagnose(args: argparse.Namespace) -> int:
    diag = diagnose_error(args.error)
    if args.json:
        print(json.dumps(diag.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_diagnosis(diag))
    return 0


def cmd_heal(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    diag = diagnose_error(args.error)
    diag = attempt_heal(project_root, diag)
    save_to_history(project_root, diag)

    if args.json:
        print(json.dumps(diag.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_diagnosis(diag))
    return 0 if diag.healed else 1


def cmd_history(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    records = load_history(project_root)

    if args.json:
        print(json.dumps([{
            "timestamp": r.timestamp, "error": r.error,
            "rule_id": r.rule_id, "strategy": r.strategy,
            "success": r.success, "detail": r.detail,
        } for r in records], indent=2, ensure_ascii=False))
    else:
        if not records:
            print("📜 Aucun historique de guérison")
            return 0
        print(f"📜 Historique Self-Healing ({len(records)} entrées)\n")
        for r in records[-10:]:
            status = "✅" if r.success else "❌"
            print(f"   {status} [{r.timestamp[:16]}] {r.rule_id} {r.strategy}")
            print(f"      {r.error[:80]}")
            if r.detail:
                print(f"      → {r.detail}")
            print()
    return 0


def cmd_playbook(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([{
            "id": r["id"], "name": r["name"], "strategy": r["strategy"],
            "auto_heal": r.get("auto_heal", False), "actions": r["actions"],
        } for r in PLAYBOOK], indent=2, ensure_ascii=False))
    else:
        print(format_playbook())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    records = load_history(project_root)

    total = len(records)
    success = sum(1 for r in records if r.success)
    rate = success / total * 100 if total > 0 else 0

    print("🏥 Self-Healing — Statut\n")
    print(f"   Tentatives totales : {total}")
    print(f"   Réussites : {success} ({rate:.0f}%)")
    print(f"   Règles connues : {len(PLAYBOOK)}")
    print(f"   Auto-réparables : {sum(1 for r in PLAYBOOK if r.get('auto_heal'))}")

    if records:
        # Patterns les plus fréquents
        from collections import Counter
        freq = Counter(r.rule_id for r in records)
        print("\n   Erreurs les plus fréquentes :")
        for rule_id, count in freq.most_common(3):
            name = next((r["name"] for r in PLAYBOOK if r["id"] == rule_id), rule_id)
            print(f"      {count}× [{rule_id}] {name}")

    return 0


# ── Proactive Improvements ──────────────────────────────────────────────────

def suggest_improvements(project_root: Path) -> list[dict]:
    """Analyse l'historique et propose des améliorations proactives.

    Détecte les patterns récurrents dans les échecs et génère des suggestions
    d'amélioration plutôt que de simplement réagir aux erreurs.
    """
    records = load_history(project_root)
    if not records:
        return []

    from collections import Counter
    rule_freq = Counter(r.rule_id for r in records)
    fail_freq = Counter(r.rule_id for r in records if not r.success)
    total = len(records)
    suggestions: list[dict] = []

    # 1. Erreurs récurrentes → besoin d'une règle plus forte
    for rule_id, count in rule_freq.most_common(5):
        if count >= 3:
            rule_name = next(
                (r["name"] for r in PLAYBOOK if r["id"] == rule_id), rule_id
            )
            success_count = count - fail_freq.get(rule_id, 0)
            rate = success_count / count * 100
            suggestions.append({
                "type": "recurrent_pattern",
                "rule_id": rule_id,
                "rule_name": rule_name,
                "occurrences": count,
                "success_rate": round(rate, 1),
                "suggestion": (
                    f"Le pattern [{rule_id}] {rule_name} apparaît {count}× "
                    f"(succès: {rate:.0f}%). Envisager un guard-rail préventif."
                ),
            })

    # 2. Faible taux de réparation global
    success_total = sum(1 for r in records if r.success)
    global_rate = success_total / total * 100 if total > 0 else 0
    if global_rate < 50 and total >= 5:
        suggestions.append({
            "type": "low_heal_rate",
            "success_rate": round(global_rate, 1),
            "total": total,
            "suggestion": (
                f"Taux de guérison faible ({global_rate:.0f}% sur {total} tentatives). "
                "Enrichir le playbook avec de nouvelles stratégies auto-heal."
            ),
        })

    # 3. Règles non auto-réparables fréquentes → candidats à l'automatisation
    manual_rules = {r["id"] for r in PLAYBOOK if not r.get("auto_heal")}
    for rule_id, count in rule_freq.most_common():
        if rule_id in manual_rules and count >= 2:
            rule_name = next(
                (r["name"] for r in PLAYBOOK if r["id"] == rule_id), rule_id
            )
            suggestions.append({
                "type": "automation_candidate",
                "rule_id": rule_id,
                "rule_name": rule_name,
                "occurrences": count,
                "suggestion": (
                    f"[{rule_id}] {rule_name} est manuel mais apparaît {count}×. "
                    "Candidat à l'automatisation."
                ),
            })

    return suggestions


# ── MCP Interface ────────────────────────────────────────────────────────────

def mcp_self_healing(
    project_root: str,
    action: str = "status",
    error: str = "",
) -> dict:
    """MCP tool ``bmad_self_healing`` — diagnostic et réparation automatique.

    Args:
        project_root: Racine du projet.
        action: diagnose | heal | status | suggest.
        error: Message d'erreur (requis pour diagnose/heal).

    Returns:
        dict avec le résultat de l'action.
    """
    root = Path(project_root).resolve()

    if action == "diagnose":
        if not error:
            return {"status": "error", "error": "Paramètre 'error' requis"}
        diag = diagnose_error(error)
        return {"status": "ok", **diag.to_dict()}

    if action == "heal":
        if not error:
            return {"status": "error", "error": "Paramètre 'error' requis"}
        diag = diagnose_error(error)
        diag = attempt_heal(root, diag)
        save_to_history(root, diag)
        return {"status": "ok", "healed": diag.healed, **diag.to_dict()}

    if action == "status":
        records = load_history(root)
        total = len(records)
        success = sum(1 for r in records if r.success)
        return {
            "status": "ok",
            "total_attempts": total,
            "successes": success,
            "rate": round(success / total * 100, 1) if total > 0 else 0,
            "rules_count": len(PLAYBOOK),
        }

    if action == "suggest":
        suggestions = suggest_improvements(root)
        return {"status": "ok", "suggestions": suggestions}

    return {"status": "error", "error": f"Unknown action: {action}"}


# ── CLI Builder ──────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Self-Healing — Auto-réparation des workflows",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")

    subs = parser.add_subparsers(dest="command", help="Commande")

    p = subs.add_parser("diagnose", help="Diagnostiquer un échec")
    p.add_argument("--error", type=str, required=True, help="Message d'erreur")
    p.set_defaults(func=cmd_diagnose)

    p = subs.add_parser("heal", help="Tenter la réparation automatique")
    p.add_argument("--error", type=str, required=True, help="Message d'erreur")
    p.set_defaults(func=cmd_heal)

    p = subs.add_parser("history", help="Historique des réparations")
    p.set_defaults(func=cmd_history)

    p = subs.add_parser("playbook", help="Stratégies de réparation connues")
    p.set_defaults(func=cmd_playbook)

    p = subs.add_parser("status", help="Statut du système")
    p.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
