#!/usr/bin/env python3
"""Decision Log — Chaîne immuable de décisions architecturales.

Enregistre chaque décision avec un hash du précédent, créant une
blockchain légère pour l'audit et la traçabilité complète.

Usage:
    python decision-log.py --project-root ./mon-projet log --title "Choix de DB" --context "PostgreSQL vs MongoDB" --decision "PostgreSQL" --rationale "Données relationnelles, ACID"
    python decision-log.py --project-root ./mon-projet chain
    python decision-log.py --project-root ./mon-projet verify
    python decision-log.py --project-root ./mon-projet audit --scope architecture
    python decision-log.py --project-root ./mon-projet export --format markdown
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

DECISION_DIR = ".bmad-decisions"
CHAIN_FILE = "decision-chain.jsonl"

# ── Modèle de données ──────────────────────────────────────────


@dataclass
class Decision:
    """Une décision architecturale."""

    decision_id: str = ""
    sequence: int = 0
    timestamp: str = ""
    title: str = ""
    context: str = ""
    decision: str = ""
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    scope: str = "general"  # architecture, tool, workflow, agent, infra
    status: str = "accepted"  # accepted, superseded, deprecated, proposed
    supersedes: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    prev_hash: str = ""
    self_hash: str = ""


# ── Fonctions de chaîne ─────────────────────────────────────────


def _chain_path(root: Path) -> Path:
    """Chemin du fichier de chaîne."""
    return root / DECISION_DIR / CHAIN_FILE


def _compute_decision_hash(decision: Decision) -> str:
    """Calcule le hash d'une décision (excluant self_hash)."""
    data = asdict(decision)
    data.pop("self_hash", None)
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load_chain(root: Path) -> list[Decision]:
    """Charge la chaîne complète."""
    chain_file = _chain_path(root)
    if not chain_file.exists():
        return []

    decisions: list[Decision] = []
    for line in chain_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            decisions.append(Decision(**{k: v for k, v in data.items()
                                         if k in Decision.__dataclass_fields__}))
        except (json.JSONDecodeError, TypeError):
            continue

    return decisions


def _append_decision(root: Path, decision: Decision) -> None:
    """Ajoute une décision à la chaîne."""
    chain_dir = root / DECISION_DIR
    chain_dir.mkdir(parents=True, exist_ok=True)
    chain_file = _chain_path(root)
    with open(chain_file, "a", encoding="utf-8") as fobj:
        fobj.write(json.dumps(asdict(decision), default=str) + "\n")


# ── Commandes ───────────────────────────────────────────────────


def cmd_log(root: Path, title: str, context: str, decision: str,
            rationale: str, alternatives: list[str], scope: str,
            tags: list[str], author: str, supersedes: str,
            as_json: bool) -> dict[str, Any]:
    """Enregistre une nouvelle décision."""
    chain = _load_chain(root)

    # Déterminer le prev_hash
    if chain:
        prev_hash = chain[-1].self_hash
        seq = chain[-1].sequence + 1
    else:
        prev_hash = "genesis"
        seq = 1

    dec_id = f"DEC-{seq:04d}"

    new_decision = Decision(
        decision_id=dec_id,
        sequence=seq,
        timestamp=datetime.now().isoformat(),
        title=title,
        context=context,
        decision=decision,
        rationale=rationale,
        alternatives=alternatives,
        scope=scope,
        tags=tags,
        author=author,
        supersedes=supersedes,
        prev_hash=prev_hash,
    )

    new_decision.self_hash = _compute_decision_hash(new_decision)
    _append_decision(root, new_decision)

    # Si supersedes, marquer l'ancienne comme superseded
    if supersedes:
        _update_status(root, supersedes, "superseded")

    result = {
        "decision_id": dec_id,
        "sequence": seq,
        "title": title,
        "scope": scope,
        "self_hash": new_decision.self_hash,
        "prev_hash": prev_hash,
        "chain_length": seq,
    }

    if not as_json:
        print(f"📋 Décision enregistrée : {dec_id}")
        print(f"   Titre : {title}")
        print(f"   Scope : {scope}")
        print(f"   Hash : {new_decision.self_hash}")
        print(f"   Prev : {prev_hash}")
        if supersedes:
            print(f"   Remplace : {supersedes}")
        print(f"   Position dans la chaîne : #{seq}")

    return result


def _update_status(root: Path, dec_id: str, new_status: str) -> None:
    """Met à jour le statut d'une décision (réécriture complète)."""
    chain = _load_chain(root)
    updated = False

    for dec in chain:
        if dec.decision_id == dec_id:
            dec.status = new_status
            updated = True

    if updated:
        chain_file = _chain_path(root)
        with open(chain_file, "w", encoding="utf-8") as fobj:
            for dec in chain:
                fobj.write(json.dumps(asdict(dec), default=str) + "\n")


def cmd_chain(root: Path, limit: int, as_json: bool) -> dict[str, Any]:
    """Affiche la chaîne de décisions."""
    chain = _load_chain(root)

    if limit > 0:
        displayed = chain[-limit:]
    else:
        displayed = chain

    result = {
        "total_decisions": len(chain),
        "displayed": len(displayed),
        "chain": [asdict(dec) for dec in displayed],
    }

    if not as_json:
        if not chain:
            print("📭 Aucune décision enregistrée.")
            print("   Utilisez 'decision-log log' pour enregistrer la première.")
            return result

        print(f"⛓️ Chaîne de décisions ({len(chain)} total)")
        if limit > 0 and len(chain) > limit:
            print(f"   (affichage des {limit} dernières)")
        print()

        for dec in displayed:
            status_icon = {
                "accepted": "✅", "superseded": "🔄",
                "deprecated": "⚠️", "proposed": "💭",
            }.get(dec.status, "⚪")

            print(f"  {status_icon} [{dec.decision_id}] {dec.title}")
            print(f"     {dec.timestamp[:19]} | Scope: {dec.scope} | Status: {dec.status}")
            print(f"     Décision: {dec.decision[:80]}")
            if dec.rationale:
                print(f"     Raison: {dec.rationale[:80]}")
            print(f"     Hash: {dec.self_hash} ← {dec.prev_hash}")
            if dec.supersedes:
                print(f"     Remplace: {dec.supersedes}")
            print()

    return result


def cmd_verify(root: Path, as_json: bool) -> dict[str, Any]:
    """Vérifie l'intégrité de la chaîne."""
    chain = _load_chain(root)

    if not chain:
        result = {"valid": True, "message": "Chaîne vide", "length": 0}
        if not as_json:
            print("📭 Chaîne vide — rien à vérifier.")
        return result

    issues: list[dict[str, str]] = []

    for i, dec in enumerate(chain):
        # Vérifier le self_hash
        expected_hash = _compute_decision_hash(dec)
        if dec.self_hash != expected_hash:
            issues.append({
                "decision": dec.decision_id,
                "type": "hash_mismatch",
                "message": f"Hash incorrect : attendu {expected_hash}, trouvé {dec.self_hash}",
            })

        # Vérifier le chaînage prev_hash
        if i == 0:
            if dec.prev_hash != "genesis":
                issues.append({
                    "decision": dec.decision_id,
                    "type": "genesis_error",
                    "message": f"Premier élément devrait avoir prev_hash='genesis', trouvé '{dec.prev_hash}'",
                })
        else:
            expected_prev = chain[i - 1].self_hash
            if dec.prev_hash != expected_prev:
                issues.append({
                    "decision": dec.decision_id,
                    "type": "chain_break",
                    "message": f"Rupture de chaîne : prev_hash={dec.prev_hash}, "
                               f"attendu={expected_prev}",
                })

        # Vérifier la séquence
        if dec.sequence != i + 1:
            issues.append({
                "decision": dec.decision_id,
                "type": "sequence_gap",
                "message": f"Séquence incorrecte : attendu {i + 1}, trouvé {dec.sequence}",
            })

    is_valid = len(issues) == 0

    result = {
        "valid": is_valid,
        "chain_length": len(chain),
        "issues": issues,
        "first_hash": chain[0].self_hash if chain else "",
        "last_hash": chain[-1].self_hash if chain else "",
    }

    if not as_json:
        if is_valid:
            print(f"✅ Chaîne valide — {len(chain)} décisions, intégrité vérifiée")
            print(f"   Genesis : {chain[0].self_hash}")
            print(f"   Dernier : {chain[-1].self_hash}")
        else:
            print(f"❌ Chaîne corrompue — {len(issues)} problème(s) détecté(s)")
            for issue in issues:
                print(f"   🔴 [{issue['decision']}] {issue['type']}: {issue['message']}")

    return result


def cmd_audit(root: Path, scope: str | None, status: str | None,
              as_json: bool) -> dict[str, Any]:
    """Audite les décisions par scope ou statut."""
    chain = _load_chain(root)

    filtered = chain
    if scope:
        filtered = [dec for dec in filtered if dec.scope == scope]
    if status:
        filtered = [dec for dec in filtered if dec.status == status]

    # Statistiques
    scope_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for dec in chain:
        scope_counts[dec.scope] = scope_counts.get(dec.scope, 0) + 1
        status_counts[dec.status] = status_counts.get(dec.status, 0) + 1

    # Détection d'anomalies
    anomalies: list[str] = []
    if status_counts.get("superseded", 0) > len(chain) * 0.5:
        anomalies.append("⚠️ Plus de 50% des décisions sont superseded — instabilité décisionnelle")
    if len(scope_counts) == 1 and len(chain) > 10:
        anomalies.append("⚠️ Toutes les décisions dans le même scope — diversifier la couverture")

    # Décisions sans rationale
    no_rationale = [dec for dec in chain if not dec.rationale]
    if no_rationale:
        anomalies.append(f"ℹ️ {len(no_rationale)} décision(s) sans rationale documenté")

    result = {
        "total": len(chain),
        "filtered": len(filtered),
        "scope_filter": scope,
        "status_filter": status,
        "decisions": [asdict(dec) for dec in filtered],
        "stats": {"by_scope": scope_counts, "by_status": status_counts},
        "anomalies": anomalies,
    }

    if not as_json:
        title = "Audit"
        if scope:
            title += f" [scope: {scope}]"
        if status:
            title += f" [status: {status}]"
        print(f"🔍 {title}")
        print(f"   Total : {len(chain)} | Filtrées : {len(filtered)}")
        print()

        print("  📊 Répartition par scope :")
        for scp, cnt in sorted(scope_counts.items()):
            bar = "█" * min(cnt, 30)
            print(f"     {scp:15s} {bar} {cnt}")

        print("\n  📊 Répartition par statut :")
        for sts, cnt in sorted(status_counts.items()):
            bar = "█" * min(cnt, 30)
            print(f"     {sts:15s} {bar} {cnt}")

        if anomalies:
            print("\n  🚨 Anomalies :")
            for anomaly in anomalies:
                print(f"     {anomaly}")

        if filtered:
            print(f"\n  📋 Décisions ({len(filtered)}) :")
            for dec in filtered:
                print(f"     [{dec.decision_id}] {dec.title} ({dec.scope}/{dec.status})")

    return result


def cmd_export(root: Path, fmt: str, output: str | None,
               as_json: bool) -> dict[str, Any]:
    """Exporte la chaîne de décisions."""
    chain = _load_chain(root)

    if fmt == "markdown":
        lines = ["# Journal de Décisions Architecturales\n"]
        lines.append(f"> Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"> Total : {len(chain)} décisions\n")
        lines.append("---\n")

        for dec in chain:
            status_badge = {
                "accepted": "✅ Acceptée", "superseded": "🔄 Remplacée",
                "deprecated": "⚠️ Dépréciée", "proposed": "💭 Proposée",
            }.get(dec.status, dec.status)

            lines.append(f"## {dec.decision_id} — {dec.title}\n")
            lines.append(f"**Date** : {dec.timestamp[:19]}  ")
            lines.append(f"**Scope** : {dec.scope}  ")
            lines.append(f"**Statut** : {status_badge}\n")

            if dec.context:
                lines.append(f"### Contexte\n{dec.context}\n")
            lines.append(f"### Décision\n{dec.decision}\n")
            if dec.rationale:
                lines.append(f"### Rationale\n{dec.rationale}\n")
            if dec.alternatives:
                lines.append("### Alternatives considérées")
                for alt in dec.alternatives:
                    lines.append(f"- {alt}")
                lines.append("")
            if dec.supersedes:
                lines.append(f"*Remplace : {dec.supersedes}*\n")
            lines.append(f"<small>Hash: `{dec.self_hash}` ← `{dec.prev_hash}`</small>\n")
            lines.append("---\n")

        content = "\n".join(lines)

    elif fmt == "json":
        content = json.dumps([asdict(dec) for dec in chain], indent=2,
                             ensure_ascii=False, default=str)
    elif fmt == "csv":
        header = "ID,Date,Titre,Scope,Statut,Décision,Rationale,Hash"
        rows = [header]
        for dec in chain:
            row = (f'"{dec.decision_id}","{dec.timestamp[:19]}","{dec.title}",'
                   f'"{dec.scope}","{dec.status}","{dec.decision[:100]}",'
                   f'"{dec.rationale[:100]}","{dec.self_hash}"')
            rows.append(row)
        content = "\n".join(rows)
    else:
        return {"error": f"Format inconnu : {fmt}"}

    # Sauvegarder
    if output:
        out_path = Path(output)
        if not out_path.is_absolute():
            out_path = root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        saved = str(out_path)
    else:
        saved = None
        if not as_json:
            print(content)

    result = {
        "format": fmt,
        "decisions": len(chain),
        "output": saved,
        "size_bytes": len(content.encode("utf-8")),
    }

    if not as_json and saved:
        print(f"📤 Exporté : {saved} ({result['size_bytes']} bytes)")

    return result


# ── CLI ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        prog="decision-log",
        description="Decision Log — Chaîne immuable de décisions architecturales",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # log
    lg = subs.add_parser("log", help="Enregistrer une décision")
    lg.add_argument("--title", required=True, help="Titre de la décision")
    lg.add_argument("--context", default="", help="Contexte")
    lg.add_argument("--decision", required=True, help="La décision prise")
    lg.add_argument("--rationale", default="", help="Raisons")
    lg.add_argument("--alternatives", nargs="*", default=[], help="Alternatives considérées")
    lg.add_argument("--scope", default="general",
                    choices=["architecture", "tool", "workflow", "agent", "infra", "general"],
                    help="Scope de la décision")
    lg.add_argument("--tags", nargs="*", default=[], help="Tags")
    lg.add_argument("--author", default="", help="Auteur")
    lg.add_argument("--supersedes", default="", help="ID de la décision remplacée")

    # chain
    ch = subs.add_parser("chain", help="Afficher la chaîne")
    ch.add_argument("--limit", type=int, default=0, help="Nombre max à afficher (0=tous)")

    # verify
    subs.add_parser("verify", help="Vérifier l'intégrité de la chaîne")

    # audit
    au = subs.add_parser("audit", help="Auditer les décisions")
    au.add_argument("--scope", help="Filtrer par scope")
    au.add_argument("--status", help="Filtrer par statut")

    # export
    exp = subs.add_parser("export", help="Exporter la chaîne")
    exp.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown",
                     help="Format d'export")
    exp.add_argument("--output", help="Fichier de sortie (stdout si omis)")

    return parser


def main() -> None:
    """Point d'entrée principal."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    root = args.project_root.resolve()

    result: dict[str, Any] = {}

    if args.command == "log":
        result = cmd_log(root, args.title, args.context, args.decision,
                         args.rationale, args.alternatives, args.scope,
                         args.tags, args.author, args.supersedes, args.as_json)
    elif args.command == "chain":
        result = cmd_chain(root, args.limit, args.as_json)
    elif args.command == "verify":
        result = cmd_verify(root, args.as_json)
    elif args.command == "audit":
        result = cmd_audit(root, args.scope, getattr(args, "status", None), args.as_json)
    elif args.command == "export":
        result = cmd_export(root, args.format, args.output, args.as_json)

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
