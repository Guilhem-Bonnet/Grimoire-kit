#!/usr/bin/env python3
"""
memory-sync.py — Synchronisation bidirectionnelle mémoire Grimoire ↔ Qdrant (BM-42 Story 2.4).
============================================================

Après chaque session agent, vectorise automatiquement les nouvelles
entrées (decisions-log, learnings, failure-museum) et les stocke dans
Qdrant. Sync bidirectionnelle : Qdrant → MD et MD → Qdrant.

Modes :
  push       — MD → Qdrant (vectorise les fichiers modifiés)
  pull       — Qdrant → MD (exporte en fichiers structurés)
  diff       — Affiche les différences entre MD et Qdrant
  hook       — Mode hook post-session (auto-detect changements)
  dedup      — Détection et nettoyage des doublons par similarité

Usage :
  python3 memory-sync.py --project-root . push
  python3 memory-sync.py --project-root . push --file decisions-log.md
  python3 memory-sync.py --project-root . pull --collection memory --output _grimoire/_memory/
  python3 memory-sync.py --project-root . diff
  python3 memory-sync.py --project-root . hook --agent dev
  python3 memory-sync.py --project-root . dedup --threshold 0.85

Dépendances optionnelles :
  pip install qdrant-client sentence-transformers

Références :
  - mem0 patterns     : https://github.com/mem0ai/mem0
  - Qdrant payload    : https://qdrant.tech/documentation/concepts/indexing/#payload-index
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.memory_sync")

# ── Version ──────────────────────────────────────────────────────────────────

MEMORY_SYNC_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4
MEMORY_DIR = "_grimoire/_memory"
SYNC_STATE_FILE = "_grimoire-output/.memory-sync-state.json"

# Fichiers mémoire trackés pour le push automatique
TRACKED_FILES: dict[str, str] = {
    "shared-context.md": "shared-context",
    "decisions-log.md": "decisions",
    "failure-museum.md": "failures",
    "session-state.md": "session",
}

# Pattern pour les learnings par agent
LEARNINGS_PATTERN = "agent-learnings/*.md"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """Une entrée mémoire à synchroniser."""
    text: str
    source_file: str
    entry_type: str  # shared-context | decisions | failures | learnings | session
    agent: str = ""
    heading: str = ""
    tags: list[str] = field(default_factory=list)
    timestamp: str = ""

    @property
    def uid(self) -> str:
        """UUID5 déterministe basé sur source + texte tronqué."""
        key = f"{self.source_file}:{self.text[:200]}"
        return str(uuid.uuid5(uuid.NAMESPACE_OID, key))


@dataclass
class SyncReport:
    """Rapport de synchronisation."""
    direction: str  # push | pull | hook
    entries_processed: int = 0
    entries_new: int = 0
    entries_updated: int = 0
    entries_skipped: int = 0
    duplicates_found: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


@dataclass
class DiffEntry:
    """Différence entre MD et Qdrant."""
    source_file: str
    status: str  # new_in_md | new_in_qdrant | modified | synced
    md_hash: str = ""
    qdrant_hash: str = ""


@dataclass
class SyncState:
    """État de synchronisation persisté."""
    last_push: str = ""
    last_pull: str = ""
    file_hashes: dict[str, str] = field(default_factory=dict)
    push_count: int = 0
    pull_count: int = 0


# ── Memory Parser ────────────────────────────────────────────────────────────

class MemoryParser:
    """Parse les fichiers mémoire MD en entrées structurées."""

    @staticmethod
    def parse_decisions_log(content: str, source_file: str) -> list[MemoryEntry]:
        """Parse decisions-log.md — format: ## {date} — {titre}."""
        entries: list[MemoryEntry] = []
        sections = re.split(r"^(##\s+.+)$", content, flags=re.MULTILINE)

        heading = ""
        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            header_match = re.match(r"^##\s+(.+)$", stripped)
            if header_match:
                heading = header_match.group(1)
                continue

            if len(stripped) < 30:
                continue

            # Extraire les tags depuis le texte
            tags = re.findall(r"#(\w[\w-]*)", stripped)

            entries.append(MemoryEntry(
                text=stripped[:3000],
                source_file=source_file,
                entry_type="decisions",
                heading=heading,
                tags=tags[:10],
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))

        return entries

    @staticmethod
    def parse_learnings(content: str, source_file: str, agent: str = "") -> list[MemoryEntry]:
        """Parse agent-learnings/{agent}.md — format: ### {titre}."""
        entries: list[MemoryEntry] = []
        sections = re.split(r"^(###?\s+.+)$", content, flags=re.MULTILINE)

        heading = ""
        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            header_match = re.match(r"^###?\s+(.+)$", stripped)
            if header_match:
                heading = header_match.group(1)
                continue

            if len(stripped) < 20:
                continue

            entries.append(MemoryEntry(
                text=stripped[:3000],
                source_file=source_file,
                entry_type="learnings",
                agent=agent,
                heading=heading,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))

        return entries

    @staticmethod
    def parse_failure_museum(content: str, source_file: str) -> list[MemoryEntry]:
        """Parse failure-museum.md — format: ## {catégorie} / ### {incident}."""
        entries: list[MemoryEntry] = []
        sections = re.split(r"^(##\s+.+)$", content, flags=re.MULTILINE)

        heading = ""
        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            header_match = re.match(r"^##\s+(.+)$", stripped)
            if header_match:
                heading = header_match.group(1)
                continue

            if len(stripped) < 30:
                continue

            # Extraire la catégorie (CC-FAIL, HALLUCINATION, etc.)
            tags = []
            cat_match = re.search(
                r"(CC-FAIL|WRONG-ASSUMPTION|CONTEXT-LOSS|HALLUCINATION|ARCH-MISTAKE|PROCESS-SKIP)",
                stripped,
            )
            if cat_match:
                tags.append(cat_match.group(1))

            entries.append(MemoryEntry(
                text=stripped[:3000],
                source_file=source_file,
                entry_type="failures",
                heading=heading,
                tags=tags,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))

        return entries

    @staticmethod
    def parse_generic(content: str, source_file: str, entry_type: str) -> list[MemoryEntry]:
        """Parse générique par sections ## pour les fichiers non spécifiques."""
        entries: list[MemoryEntry] = []
        sections = re.split(r"^(##\s+.+)$", content, flags=re.MULTILINE)

        heading = ""
        for section in sections:
            stripped = section.strip()
            if not stripped:
                continue

            header_match = re.match(r"^##\s+(.+)$", stripped)
            if header_match:
                heading = header_match.group(1)
                continue

            if len(stripped) < 30:
                continue

            entries.append(MemoryEntry(
                text=stripped[:3000],
                source_file=source_file,
                entry_type=entry_type,
                heading=heading,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))

        return entries

    @classmethod
    def parse_file(cls, filepath: Path, project_root: Path) -> list[MemoryEntry]:
        """Auto-détecte le type et parse le fichier."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        relative = str(filepath.relative_to(project_root))
        filename = filepath.name

        if filename == "decisions-log.md":
            return cls.parse_decisions_log(content, relative)
        elif filename == "failure-museum.md":
            return cls.parse_failure_museum(content, relative)
        elif "agent-learnings" in relative:
            agent = filepath.stem  # agent name from filename
            return cls.parse_learnings(content, relative, agent)
        elif filename in TRACKED_FILES:
            return cls.parse_generic(content, relative, TRACKED_FILES[filename])
        else:
            return cls.parse_generic(content, relative, "docs")


# ── Sync State Management ───────────────────────────────────────────────────

class SyncStateManager:
    """Gère l'état de synchronisation persisté."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state = self._load()

    def _load(self) -> SyncState:
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    data = json.load(f)
                return SyncState(**{
                    k: v for k, v in data.items()
                    if k in {f.name for f in SyncState.__dataclass_fields__.values()}
                })
            except (json.JSONDecodeError, OSError, TypeError):
                return SyncState()
        return SyncState()

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(asdict(self.state), f, indent=2, ensure_ascii=False)

    def file_changed(self, filepath: Path) -> bool:
        """Vérifie si un fichier a changé depuis le dernier push."""
        try:
            current_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()
        except OSError:
            return False
        key = str(filepath)
        return self.state.file_hashes.get(key) != current_hash

    def mark_synced(self, filepath: Path) -> None:
        """Marque un fichier comme synchronisé."""
        try:
            self.state.file_hashes[str(filepath)] = hashlib.sha256(filepath.read_bytes()).hexdigest()
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues


# ── Memory Syncer ────────────────────────────────────────────────────────────

class MemorySyncer:
    """Synchronisation bidirectionnelle mémoire Grimoire ↔ Qdrant."""

    def __init__(
        self,
        project_root: Path,
        qdrant_url: str = "",
        qdrant_path: str = "",
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        ollama_url: str = "",
        project_name: str = "grimoire",
    ):
        self.project_root = project_root
        self.project_name = project_name
        self.memory_dir = project_root / MEMORY_DIR

        # Sync state
        self._state_mgr = SyncStateManager(project_root / SYNC_STATE_FILE)

        # Qdrant + embedding (via rag-indexer)
        self._qdrant_url = qdrant_url
        self._qdrant_path = qdrant_path or str(project_root / "_grimoire-output" / ".qdrant_data")
        self._embedding_model = embedding_model
        self._ollama_url = ollama_url
        self._indexer = None

    def _init_indexer(self) -> bool:
        """Init lazy via rag-indexer.py."""
        if self._indexer is not None:
            return True
        try:
            import importlib.util
            indexer_path = Path(__file__).parent / "rag-indexer.py"
            if not indexer_path.exists():
                indexer_path = self.project_root / "framework" / "tools" / "rag-indexer.py"
            if not indexer_path.exists():
                return False

            spec = importlib.util.spec_from_file_location("rag_indexer_sync", indexer_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            self._indexer = mod.RAGIndexer(
                project_root=self.project_root,
                qdrant_url=self._qdrant_url,
                qdrant_path=self._qdrant_path,
                embedding_model=self._embedding_model,
                ollama_url=self._ollama_url,
                project_name=self.project_name,
            )
            self._rag_mod = mod
            return True
        except (ImportError, Exception):
            return False

    def _discover_memory_files(self) -> list[Path]:
        """Découvre tous les fichiers mémoire à synchroniser."""
        files: list[Path] = []

        # Fichiers trackés
        for filename in TRACKED_FILES:
            f = self.memory_dir / filename
            if f.exists():
                files.append(f)

        # Learnings par agent
        learnings_dir = self.memory_dir / "agent-learnings"
        if learnings_dir.exists():
            files.extend(sorted(learnings_dir.glob("*.md")))

        return files

    def push(self, specific_file: str | None = None, force: bool = False) -> SyncReport:
        """Push MD → Qdrant : vectorise les fichiers mémoire modifiés."""
        start = time.time()
        report = SyncReport(direction="push")

        if not self._init_indexer():
            report.errors.append(
                "Indexer non disponible — pip install qdrant-client sentence-transformers"
            )
            report.duration_ms = int((time.time() - start) * 1000)
            return report

        # Découvrir les fichiers
        if specific_file:
            filepath = self.memory_dir / specific_file
            if not filepath.exists():
                filepath = self.project_root / specific_file
            files = [filepath] if filepath.exists() else []
            if not files:
                report.errors.append(f"Fichier non trouvé: {specific_file}")
        else:
            files = self._discover_memory_files()

        collection = "memory"
        self._indexer._ensure_collection(collection)

        for filepath in files:
            report.entries_processed += 1

            # Skip si pas modifié (sauf force)
            if not force and not self._state_mgr.file_changed(filepath):
                report.entries_skipped += 1
                continue

            # Parser le fichier en entrées
            entries = MemoryParser.parse_file(filepath, self.project_root)
            if not entries:
                report.entries_skipped += 1
                continue

            # Convertir en chunks pour l'indexer
            chunks = []
            for entry in entries:
                chunk = self._rag_mod.Chunk(
                    text=entry.text,
                    source_file=entry.source_file,
                    chunk_type=entry.entry_type,
                    chunk_index=0,
                    heading=entry.heading,
                    estimated_tokens=len(entry.text) // CHARS_PER_TOKEN,
                    metadata={
                        "agent": entry.agent,
                        "tags": entry.tags,
                        "entry_uid": entry.uid,
                        "sync_timestamp": entry.timestamp,
                    },
                )
                chunks.append(chunk)

            # Upsert
            try:
                upserted = self._indexer._upsert_chunks(collection, chunks)
                report.entries_new += upserted
                self._state_mgr.mark_synced(filepath)
            except Exception as e:
                report.errors.append(f"{filepath.name}: {e}")

        # Save state
        self._state_mgr.state.last_push = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._state_mgr.state.push_count += 1
        self._state_mgr.save()

        report.duration_ms = int((time.time() - start) * 1000)
        return report

    def pull(self, collection: str = "memory", output_dir: Path | None = None) -> SyncReport:
        """Pull Qdrant → MD : exporte les entrées Qdrant en fichiers structurés."""
        start = time.time()
        report = SyncReport(direction="pull")
        output = output_dir or self.memory_dir

        if not self._init_indexer():
            report.errors.append("Indexer non disponible")
            report.duration_ms = int((time.time() - start) * 1000)
            return report

        name = f"{self.project_name}-{collection}"

        try:
            points, _ = self._indexer._client.scroll(
                collection_name=name,
                limit=10_000,
            )
        except Exception as e:
            report.errors.append(f"Erreur scroll {name}: {e}")
            report.duration_ms = int((time.time() - start) * 1000)
            return report

        # Grouper par source_file
        by_source: dict[str, list] = {}
        for point in points:
            source = point.payload.get("source_file", "unknown")
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(point)

        # Écrire les fichiers
        for source, points_group in by_source.items():
            report.entries_processed += 1
            out_file = output / Path(source).name
            if out_file.suffix != ".md":
                out_file = out_file.with_suffix(".md")

            lines = [
                f"# {Path(source).stem} (export Qdrant)",
                f"> Exporté le {time.strftime('%Y-%m-%d %H:%M')}",
                f"> {len(points_group)} entrées",
                "",
                "---",
                "",
            ]

            for p in sorted(points_group, key=lambda x: x.payload.get("heading", "")):
                heading = p.payload.get("heading", "")
                text = p.payload.get("text", "")
                if heading:
                    lines.append(f"## {heading}")
                    lines.append("")
                lines.append(text)
                lines.append("")

            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text("\n".join(lines), encoding="utf-8")
            report.entries_new += 1

        self._state_mgr.state.last_pull = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._state_mgr.state.pull_count += 1
        self._state_mgr.save()

        report.duration_ms = int((time.time() - start) * 1000)
        return report

    def diff(self) -> list[DiffEntry]:
        """Compare l'état MD vs Qdrant."""
        diffs: list[DiffEntry] = []
        files = self._discover_memory_files()

        for filepath in files:
            relative = str(filepath.relative_to(self.project_root))
            changed = self._state_mgr.file_changed(filepath)

            if changed:
                try:
                    current_hash = hashlib.sha256(filepath.read_bytes()).hexdigest()[:12]
                except OSError:
                    current_hash = "?"
                stored_hash = self._state_mgr.state.file_hashes.get(str(filepath), "")[:12]
                diffs.append(DiffEntry(
                    source_file=relative,
                    status="modified" if stored_hash else "new_in_md",
                    md_hash=current_hash,
                    qdrant_hash=stored_hash,
                ))
            else:
                diffs.append(DiffEntry(
                    source_file=relative,
                    status="synced",
                ))

        return diffs

    def hook(self, agent_id: str = "") -> SyncReport:
        """
        Hook post-session : push uniquement les fichiers modifiés.
        Conçu pour être appelé automatiquement après chaque session agent.
        """
        return self.push()

    def dedup(self, threshold: float = 0.85) -> SyncReport:
        """
        Détecte les doublons par similarité cosine dans Qdrant.
        Supprime les entrées avec score > threshold.
        """
        start = time.time()
        report = SyncReport(direction="dedup")

        if not self._init_indexer():
            report.errors.append("Indexer non disponible")
            report.duration_ms = int((time.time() - start) * 1000)
            return report

        name = f"{self.project_name}-memory"
        try:
            existing = [c.name for c in self._indexer._client.get_collections().collections]
            if name not in existing:
                report.errors.append(f"Collection {name} n'existe pas")
                report.duration_ms = int((time.time() - start) * 1000)
                return report

            points, _ = self._indexer._client.scroll(
                collection_name=name,
                limit=10_000,
                with_vectors=True,
            )
        except Exception as e:
            report.errors.append(f"Erreur scroll: {e}")
            report.duration_ms = int((time.time() - start) * 1000)
            return report

        # Comparer chaque paire (bruteforce — acceptable pour < 10k points)
        duplicates_to_remove: set[str] = set()
        point_list = list(points)

        for i in range(len(point_list)):
            if str(point_list[i].id) in duplicates_to_remove:
                continue
            for j in range(i + 1, len(point_list)):
                if str(point_list[j].id) in duplicates_to_remove:
                    continue

                # Cosine similarity
                vec_a = point_list[i].vector
                vec_b = point_list[j].vector
                if vec_a and vec_b:
                    sim = self._cosine_sim(vec_a, vec_b)
                    if sim >= threshold:
                        # Garder le plus récent (ou celui avec plus de metadata)
                        duplicates_to_remove.add(str(point_list[j].id))
                        report.duplicates_found += 1

        # Supprimer les doublons
        if duplicates_to_remove:
            try:
                from qdrant_client.models import PointIdsList
                self._indexer._client.delete(
                    collection_name=name,
                    points_selector=PointIdsList(points=list(duplicates_to_remove)),
                )
            except Exception as e:
                report.errors.append(f"Erreur suppression doublons: {e}")

        report.entries_processed = len(point_list)
        report.duration_ms = int((time.time() - start) * 1000)
        return report

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        """Calcule la similarité cosine entre deux vecteurs."""
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ── Config Loading ──────────────────────────────────────────────────────────

def load_sync_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("rag", data.get("memory", {}))
    return {}


def build_syncer_from_config(project_root: Path) -> MemorySyncer:
    """Construit un MemorySyncer depuis la config."""
    config = load_sync_config(project_root)

    return MemorySyncer(
        project_root=project_root,
        qdrant_url=os.environ.get("Grimoire_QDRANT_URL", config.get("qdrant_url", "")),
        embedding_model=config.get("embedding_model", "sentence-transformers/all-MiniLM-L6-v2"),
        ollama_url=os.environ.get("Grimoire_OLLAMA_URL", config.get("ollama_url", "")),
        project_name=config.get("collection_prefix", "grimoire"),
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_sync_report(report: SyncReport) -> None:
    """Affiche un rapport de sync."""
    status = "✅" if not report.errors else "⚠️"
    print(f"\n  {status} Memory Sync — {report.direction.upper()}")
    print(f"  {'─' * 50}")
    print(f"  Processed : {report.entries_processed}")
    print(f"  New       : {report.entries_new}")
    if report.entries_updated:
        print(f"  Updated   : {report.entries_updated}")
    if report.entries_skipped:
        print(f"  Skipped   : {report.entries_skipped}")
    if report.duplicates_found:
        print(f"  Doublons  : {report.duplicates_found}")
    print(f"  Duration  : {report.duration_ms}ms")

    if report.errors:
        print("\n  ⚠️  Erreurs:")
        for err in report.errors:
            print(f"     → {err}")
    print()


def _print_diff(diffs: list[DiffEntry]) -> None:
    """Affiche le diff MD vs Qdrant."""
    print("\n  📋 Memory Diff — MD vs Qdrant")
    print(f"  {'─' * 60}")

    status_icons = {
        "synced": "✅",
        "modified": "🟡",
        "new_in_md": "🆕",
        "new_in_qdrant": "☁️",
    }

    for d in diffs:
        icon = status_icons.get(d.status, "❓")
        extra = ""
        if d.status == "modified":
            extra = f" (MD: {d.md_hash}… Qdrant: {d.qdrant_hash}…)"
        print(f"  {icon} {d.source_file:45s} │ {d.status}{extra}")

    synced = sum(1 for d in diffs if d.status == "synced")
    modified = sum(1 for d in diffs if d.status in ("modified", "new_in_md"))
    print(f"\n  📊 {synced} synced, {modified} à pousser")
    print()


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="Memory Sync — Synchronisation bidirectionnelle mémoire Grimoire ↔ Qdrant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path("."),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--version", action="version", version=f"memory-sync {MEMORY_SYNC_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # push
    push_p = sub.add_parser("push", help="MD → Qdrant (vectorise les changements)")
    push_p.add_argument("--file", help="Fichier spécifique à pousser")
    push_p.add_argument("--force", action="store_true", help="Forcer le push même si non modifié")
    push_p.add_argument("--json", action="store_true", help="Output JSON")

    # pull
    pull_p = sub.add_parser("pull", help="Qdrant → MD (exporter en fichiers)")
    pull_p.add_argument("--collection", default="memory", help="Collection à exporter")
    pull_p.add_argument("--output", type=Path, help="Dossier de sortie")
    pull_p.add_argument("--json", action="store_true", help="Output JSON")

    # diff
    sub.add_parser("diff", help="Afficher les différences MD vs Qdrant")

    # hook
    hook_p = sub.add_parser("hook", help="Hook post-session (auto-push modifiés)")
    hook_p.add_argument("--agent", default="", help="Agent de la session")
    hook_p.add_argument("--json", action="store_true", help="Output JSON")

    # dedup
    dedup_p = sub.add_parser("dedup", help="Détection et suppression des doublons")
    dedup_p.add_argument("--threshold", type=float, default=0.85, help="Seuil de similarité (défaut: 0.85)")
    dedup_p.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()

    try:
        syncer = build_syncer_from_config(project_root)
    except Exception as e:
        print(f"\n  ❌ {e}\n")
        sys.exit(1)

    if args.command == "push":
        report = syncer.push(
            specific_file=getattr(args, "file", None),
            force=getattr(args, "force", False),
        )
        if getattr(args, "json", False):
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            _print_sync_report(report)

    elif args.command == "pull":
        output = getattr(args, "output", None)
        report = syncer.pull(
            collection=args.collection,
            output_dir=output,
        )
        if getattr(args, "json", False):
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            _print_sync_report(report)

    elif args.command == "diff":
        diffs = syncer.diff()
        _print_diff(diffs)

    elif args.command == "hook":
        report = syncer.hook(agent_id=getattr(args, "agent", ""))
        if getattr(args, "json", False):
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            _print_sync_report(report)

    elif args.command == "dedup":
        report = syncer.dedup(threshold=args.threshold)
        if getattr(args, "json", False):
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            _print_sync_report(report)


if __name__ == "__main__":
    main()
