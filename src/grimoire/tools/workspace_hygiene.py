"""Santé de la mémoire et file d'hygiène (P25).

Deux surfaces lisent ce module :

- ``GET /api/workspace/memory/quality`` (:func:`memory_quality`) — la santé
  d'UN projet : textes distincts, utilisateurs, taux de provenance, part de
  projections, dérive store / lexical / graphe comptée sur la collection du
  projet, résultats de ``memory lint``, supersessions orphelines, motifs
  transverses à reconfirmer, fraîcheur du graphe de code. Chaque mesure
  devient un critère ``fail`` / ``ok`` / ``unknown`` ; un seul échec met le
  projet en alerte ;
- la route d'attention (``workspace_attention``) — les mêmes constats, en
  éléments TYPÉS (:data:`HYGIENE_KINDS`), chacun avec son geste.

Refus tenus ici :

- lecture seule : rien n'est écrit chez le projet parcouru, et un store qui
  n'existe pas encore n'est pas ouvert (l'ouvrir le créerait) ;
- une mesure impossible est ``unknown`` avec son motif, jamais un zéro ; un
  taux sur zéro souvenir n'est pas un taux ;
- aucun geste inventé : chaque élément porte la commande qui le traite
  (``gesture.command``), à copier ; ce module ne déclenche aucune écriture.
  Une purge commence toujours par un export ;
- les chemins viennent du registre (ou du projet que l'hôte sert), jamais de
  la requête.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.memory.supersession import ORPHAN, SUPERSEDED, is_retracted, supersession_state

if TYPE_CHECKING:
    from grimoire.memory.backends.base import MemoryEntry
    from grimoire.tools.workspace_knowledge import Ownership

__all__ = [
    "CODEGRAPH_STALE",
    "HYGIENE_KINDS",
    "MEMORY_CONTRADICTION",
    "MEMORY_ORPHAN",
    "MEMORY_PARITY",
    "MEMORY_QUALITY",
    "QUALITY_SCHEMA_VERSION",
    "SHARED_AGING",
    "cached_report",
    "memory_quality",
    "project_report",
]

QUALITY_SCHEMA_VERSION = "grimoire-memory-quality/v1"

MEMORY_QUALITY = "memory_quality"
MEMORY_CONTRADICTION = "memory_contradiction"
MEMORY_ORPHAN = "memory_orphan_supersession"
MEMORY_PARITY = "memory_parity_drift"
CODEGRAPH_STALE = "codegraph_stale"
SHARED_AGING = "shared_aging"
#: Les éléments d'hygiène, dans leur ordre d'urgence.
HYGIENE_KINDS: tuple[str, ...] = (
    MEMORY_QUALITY, MEMORY_CONTRADICTION, MEMORY_ORPHAN, MEMORY_PARITY, CODEGRAPH_STALE, SHARED_AGING,
)

OK, FAIL, UNKNOWN = "ok", "fail", "unknown"

#: Entrées parcourues au plus ; au-delà, les mesures portent sur ce qui a été lu (``complete`` faux).
MAX_QUALITY_SCAN = 20_000
SCAN_BATCH = 500
#: Un même texte stocké au moins autant de fois est une contamination, pas un hasard.
DUPLICATE_ALERT = 3
#: En dessous de cette part de souvenirs à l'origine connue, le critère échoue.
PROVENANCE_FLOOR = 0.5
#: Le lint compare les entrées deux à deux : au-delà, il se lance à la main.
LINT_MAX_ENTRIES = 1000
#: Éléments d'un même type rendus au plus (le critère porte le compte total).
MAX_ITEMS_PER_KIND = 5
#: Âge maximal d'un rapport repris par la route d'attention, en secondes.
HYGIENE_MAX_AGE = 600.0
#: Identités de fixture : leur présence dans un store réel trahit des restes de tests.
TEST_IDENTITIES = frozenset({
    "alice", "bob", "carol", "dave", "eve", "mallory", "test", "tester", "test-user", "testuser", "dummy", "foo",
})
#: Backends dont le store est un fichier du projet : les ouvrir ne touche pas le réseau.
_FILE_BACKENDS = frozenset({"local", "lexical", "auto", "tantivy-local", "qdrant-local", "mempalace"})
_PROVENANCE_KEYS = ("actor", "agent", "agent_name", "session_id", "task_ref", "task_id", "source_uri", "file_path")
_TEXT_MAX = 90
_CODEGRAPH_ENABLED_OFF = frozenset({"", "planned", "disabled", "none", "off"})
_CODEGRAPH_REASONS = {
    "no_receipt": "jamais indexé : aucun reçu d'indexation",
    "receipt_schema_unknown": "reçu d'indexation d'un schéma inconnu",
    "receipt_unreadable": "reçu d'indexation illisible",
    "source_dir_missing": "dossier source du reçu introuvable",
    "source_unreadable": "sources illisibles",
}


def _short(text: str, limit: int = _TEXT_MAX) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


def _pct(rate: float) -> str:
    return f"{round(rate * 100)} %"


def _criterion(cid: str, label: str, state: str, value: str, detail: str) -> dict[str, str]:
    return {"id": cid, "label": label, "state": state, "value": value, "detail": detail}


# ── Lecture du store ────────────────────────────────────────────────────────


def has_provenance(entry: MemoryEntry) -> bool:
    """Vrai quand l'entrée dit d'où elle vient : agent, session, tâche ou source relisible."""
    meta = entry.metadata or {}
    if entry.task_ref or str(entry.source or "").strip() not in ("", "inconnue"):
        return True
    if any(str(meta.get(key) or "").strip() for key in _PROVENANCE_KEYS):
        return True
    source = (entry.provenance or {}).get("source")
    return bool(isinstance(source, dict) and source.get("uri"))


@dataclass
class _StoreScan:
    """Ce qu'un parcours du store a vu des entrées du projet."""

    total: int = 0
    texts: Counter[str] = field(default_factory=Counter)
    samples: dict[str, str] = field(default_factory=dict)
    text_users: dict[str, Counter[str]] = field(default_factory=dict)
    users: Counter[str] = field(default_factory=Counter)
    souvenirs: int = 0
    projections: int = 0
    with_provenance: int = 0
    retracted: int = 0
    superseded: int = 0
    orphans: list[dict[str, str]] = field(default_factory=list)
    others: int = 0
    unattributed: int = 0
    complete: bool = False

    def take(self, entry: MemoryEntry, owners: Ownership, recall: Any) -> None:
        from grimoire.tools.workspace_knowledge import classify

        visibility = owners.visibility(entry)
        if visibility == "other":
            self.others += 1
            return
        if visibility == "unattributed":
            self.unattributed += 1
            return
        if is_retracted(entry):
            self.retracted += 1
            return
        self.total += 1
        key = " ".join(entry.text.split()).casefold()
        self.texts[key] += 1
        self.samples.setdefault(key, entry.text)
        user = str(entry.user_id or "global")
        self.users[user] += 1
        self.text_users.setdefault(key, Counter())[user] += 1
        kind, _ = classify(entry)
        if kind == "projection":
            self.projections += 1
        else:
            self.souvenirs += 1
            self.with_provenance += has_provenance(entry)
        state = supersession_state(entry, recall)
        if state == SUPERSEDED:
            self.superseded += 1
        elif state == ORPHAN:
            target = str((entry.metadata or {}).get("superseded_by") or "")
            self.orphans.append({
                "id": entry.id, "text": _short(entry.text), "target": target,
                "since": str(entry.updated_at or entry.created_at or ""),
            })


def _memoized(recall: Any) -> Any:
    cache: dict[str, Any] = {}

    def cached(entry_id: str) -> Any:
        if entry_id not in cache:
            try:
                cache[entry_id] = recall(entry_id)
            except Exception:
                cache[entry_id] = None
        return cache[entry_id]

    return cached


def _scan(mgr: Any, owners: Ownership) -> _StoreScan:
    scan = _StoreScan()
    recall = _memoized(mgr.backend.recall)
    offset = 0
    while offset < MAX_QUALITY_SCAN:
        batch = mgr.backend.get_all(offset=offset, limit=SCAN_BATCH)
        for entry in batch:
            scan.take(entry, owners, recall)
        offset += len(batch)
        if len(batch) < SCAN_BATCH:
            scan.complete = True
            break
    return scan


# ── Dérive store / lexical / graphe, sur la collection du projet ───────────


def _collection(cfg: Any, root: Path) -> str:
    """La collection Weaviate du projet ; vide pour les autres backends (les nœuds n'en portent pas)."""
    if cfg.memory.backend != "weaviate-server":
        return ""
    from grimoire.memory.backends.weaviate import normalize_weaviate_collection
    from grimoire.memory.manager import store_identity

    return normalize_weaviate_collection(store_identity(cfg, root)[1])


def _drift(cfg: Any, root: Path, mgr: Any, owners: Ownership) -> dict[str, Any]:
    collection = _collection(cfg, root)
    drift: dict[str, Any] = {
        "collection": collection, "store": None, "lexical": None, "graph": None,
        "lexicalDrift": None, "graphDrift": None, "notes": [],
    }
    try:
        drift["store"] = int(mgr.backend.count())
    except Exception as exc:
        drift["notes"].append(f"compte du store illisible : {exc}")
        return drift
    companion = mgr.lexical_companion
    if companion is not None:
        if owners.shared_with:
            drift["notes"].append("collection partagée : l'index lexical ne porte que ce projet, écart non mesurable")
        else:
            try:
                drift["lexical"] = int(companion.count())
                drift["lexicalDrift"] = drift["store"] - drift["lexical"]
            except Exception as exc:
                drift["notes"].append(f"index lexical illisible : {exc}")
    graph = mgr.memory_graph
    if graph is not None:
        counter = getattr(graph, "count_memories_in", None)
        if not callable(counter):
            drift["notes"].append("ce graphe ne sait pas compter par collection")
        else:
            try:
                drift["graph"] = int(counter(collection))
                drift["graphDrift"] = drift["store"] - drift["graph"]
            except Exception as exc:
                drift["notes"].append(f"graphe illisible : {exc}")
    return drift


# ── Lint, motifs transverses, graphe de code ───────────────────────────────


def _lint(root: Path) -> dict[str, Any]:
    from grimoire.tools.memory_lint import MemoryLint, collect_memory_files

    files = collect_memory_files(root)
    entries = sum(len(f.entries) for f in files)
    base: dict[str, Any] = {
        "files": len(files), "entries": entries, "errors": None, "warnings": None, "contradictions": [],
        "reason": None,
    }
    if not files:
        return {**base, "reason": "aucun fichier de mémoire Markdown à relire"}
    if entries > LINT_MAX_ENTRIES:
        return {**base, "reason": f"{entries} entrées : trop pour le lint en ligne, lance grimoire memory lint"}
    report = MemoryLint(root).run()
    contradictions = [i for i in report.issues if i.category == "contradiction"]
    return {
        **base, "errors": report.error_count, "warnings": report.warning_count,
        "contradictions": [
            {"id": i.issue_id, "files": list(i.files), "entries": list(i.entries), "fix": i.fix_suggestion}
            for i in contradictions
        ],
    }


def _shared(cfg: Any) -> dict[str, Any]:
    """Motifs transverses liés à ce projet, passés en ``aging`` ou en ``hypothesis``."""
    from grimoire.memory import shared as sh
    from grimoire.memory.taxonomy import slugify

    out: dict[str, Any] = {"enabled": sh.is_enabled(cfg), "motifs": [], "reason": None}
    if not out["enabled"]:
        return out
    here = slugify(str(cfg.project.name or ""))
    try:
        manager = sh.open_shared(cfg)
        entries = manager.get_all(limit=MAX_QUALITY_SCAN) if manager is not None else []
    except Exception as exc:
        return {**out, "reason": f"store transverse illisible : {exc}"}
    for entry in entries:
        meta = entry.metadata or {}
        linked = [str(p) for p in (meta.get("learned_in") or [])] + [str(p) for p in (meta.get("confirmed_in") or [])]
        if here not in linked:
            continue
        freshness, age = sh.freshness_of(entry)
        if freshness in (sh.FRESHNESS_AGING, sh.FRESHNESS_HYPOTHESIS):
            out["motifs"].append({"id": entry.id, "text": _short(entry.text), "freshness": freshness, "age": age})
    return out


def _codegraph(cfg: Any, root: Path) -> dict[str, Any] | None:
    """Verdict de fraîcheur du graphe de code ; ``None`` quand le projet n'en a ni n'en veut."""
    from grimoire.codegraph.freshness import RECEIPT_PATH, check_freshness

    receipt = root / RECEIPT_PATH
    wanted = str(cfg.memory.code_graph or "").strip().lower() not in _CODEGRAPH_ENABLED_OFF
    if not receipt.is_file() and not wanted:
        return None
    verdict = check_freshness(root)
    recorded = ""
    if receipt.is_file():
        try:
            recorded = datetime.fromtimestamp(receipt.stat().st_mtime, UTC).isoformat(timespec="seconds")
        except OSError:
            recorded = ""
    changed = len(verdict.added) + len(verdict.modified) + len(verdict.removed)
    return {"verdict": verdict.verdict, "reason": verdict.reason, "target": verdict.target,
            "changed": changed, "recordedAt": recorded}


# ── Critères ────────────────────────────────────────────────────────────────


def _store_criteria(scan: _StoreScan | None, reason: str | None) -> list[dict[str, str]]:
    labels = (("distinct", "Textes distincts"), ("users", "Utilisateurs"),
              ("provenance", "Provenance"), ("content", "Souvenirs réels"))
    if scan is None:
        why = reason or "store illisible"
        return [_criterion(cid, label, UNKNOWN, "inconnu", why) for cid, label in labels]
    partial = "" if scan.complete else f" (sur les {scan.total} premières entrées lues)"
    out: list[dict[str, str]] = []
    if not scan.total:
        out.append(_criterion("distinct", "Textes distincts", OK, "0", "store vide"))
    else:
        top, count = scan.texts.most_common(1)[0]
        detail = f"sur {_plural(scan.total, 'entrée', 'entrées')}{partial}"
        if count > 1:
            detail += f" · « {_short(scan.samples[top], 48)} » ×{count}"
        out.append(_criterion("distinct", "Textes distincts", FAIL if count >= DUPLICATE_ALERT else OK,
                              str(len(scan.texts)), detail))
    tests = sorted(u for u in scan.users if u.casefold() in TEST_IDENTITIES)
    if not scan.users:
        out.append(_criterion("users", "Utilisateurs", OK, "aucun", "store vide"))
    else:
        names = ", ".join(u for u, _ in scan.users.most_common(3))
        detail = (f"identité de test : {', '.join(f'{u} ({scan.users[u]})' for u in tests)}" if tests
                  else _plural(len(scan.users), "identité", "identités"))
        out.append(_criterion("users", "Utilisateurs", FAIL if tests else OK, names, detail))
    if not scan.souvenirs:
        out.append(_criterion("provenance", "Provenance", UNKNOWN, "inconnu", "aucun souvenir à mesurer"))
    else:
        rate = scan.with_provenance / scan.souvenirs
        out.append(_criterion(
            "provenance", "Provenance", FAIL if rate < PROVENANCE_FLOOR else OK, _pct(rate),
            f"{scan.with_provenance} sur {_plural(scan.souvenirs, 'souvenir', 'souvenirs')} avec agent, session, "
            "tâche ou source",
        ))
    if not scan.total:
        out.append(_criterion("content", "Souvenirs réels", UNKNOWN, "inconnu", "store vide : rien à juger"))
    else:
        share = scan.projections / scan.total
        out.append(_criterion(
            "content", "Souvenirs réels", OK if scan.souvenirs else FAIL, str(scan.souvenirs),
            f"{_pct(share)} de projections ({scan.projections}), copies régénérables du code et du ledger",
        ))
    if scan.orphans:
        out.append(_criterion("supersession", "Remplacements", FAIL, str(len(scan.orphans)),
                              "marque superseded_by sans remplaçant lisible"))
    else:
        out.append(_criterion("supersession", "Remplacements", OK, "0 orpheline",
                              f"{_plural(scan.superseded, 'entrée remplacée', 'entrées remplacées')}, "
                              "remplaçant lisible" if scan.superseded else "aucune entrée remplacée"))
    return out


def _drift_criterion(drift: dict[str, Any] | None, reason: str | None) -> dict[str, str]:
    label = "Parité des index"
    if drift is None:
        return _criterion("drift", label, UNKNOWN, "inconnu", reason or "store illisible")
    parts = [f"store {drift['store']}"] if drift["store"] is not None else []
    if drift["lexical"] is not None:
        parts.append(f"lexical {drift['lexical']}")
    if drift["graph"] is not None:
        parts.append(f"graphe {drift['graph']} (collection {drift['collection'] or 'sans collection'})")
    gaps = [abs(d) for d in (drift["lexicalDrift"], drift["graphDrift"]) if d]
    detail = " · ".join(parts + drift["notes"])
    if gaps:
        return _criterion("drift", label, FAIL, f"{max(gaps)} d'écart", detail)
    if drift["notes"]:
        return _criterion("drift", label, UNKNOWN, "inconnu", detail)
    if drift["lexical"] is None and drift["graph"] is None:
        return _criterion("drift", label, OK, "aucun index", "ni index lexical compagnon ni graphe : rien à comparer")
    return _criterion("drift", label, OK, "0", detail)


def _lint_criterion(lint: dict[str, Any]) -> dict[str, str]:
    label = "Cohérence (memory lint)"
    if lint["errors"] is None:
        return _criterion("lint", label, UNKNOWN, "inconnu", str(lint["reason"]))
    detail = (f"{_plural(lint['files'], 'fichier', 'fichiers')}, {_plural(lint['entries'], 'entrée', 'entrées')}"
              f" · {_plural(lint['warnings'], 'avertissement', 'avertissements')}")
    if lint["errors"]:
        return _criterion("lint", label, FAIL, _plural(lint["errors"], "erreur", "erreurs"), detail)
    return _criterion("lint", label, OK, "0 erreur", detail)


def _shared_criterion(shared: dict[str, Any]) -> dict[str, str] | None:
    if not shared["enabled"]:
        return None
    label = "Motifs transverses"
    if shared["reason"]:
        return _criterion("shared", label, UNKNOWN, "inconnu", shared["reason"])
    count = len(shared["motifs"])
    if count:
        return _criterion("shared", label, FAIL, f"{count} à reconfirmer", "vieillissants ou en hypothèse")
    return _criterion("shared", label, OK, "à jour", "motifs liés à ce projet confirmés depuis moins de 90 jours")


def _codegraph_criterion(code: dict[str, Any] | None) -> dict[str, str] | None:
    if code is None:
        return None
    label = "Graphe de code"
    if code["verdict"] == "current":
        return _criterion("codegraph", label, OK, "à jour", "empreinte des sources égale à celle du reçu")
    if code["verdict"] == "stale":
        return _criterion("codegraph", label, FAIL, "périmé",
                          f"{_plural(code['changed'], 'fichier changé', 'fichiers changés')} depuis l'indexation")
    return _criterion("codegraph", label, UNKNOWN, "inconnu",
                      _CODEGRAPH_REASONS.get(code["reason"], code["reason"] or "non vérifié"))


# ── Éléments d'hygiène ──────────────────────────────────────────────────────


def _hygiene_item(kind: str, key: str, title: str, reason: str, gesture: dict[str, Any],
                  *, since: str | None = None, **ref: Any) -> dict[str, Any]:
    return {"kind": kind, "key": key, "title": title, "reason": reason, "since": since or None,
            "ref": {**ref, "gesture": gesture}}


def _contamination(scan: _StoreScan) -> dict[str, Any] | None:
    repeated = [(k, n) for k, n in scan.texts.most_common() if n >= DUPLICATE_ALERT]
    tests = sorted(u for u in scan.users if u.casefold() in TEST_IDENTITIES)
    if not repeated and not tests:
        return None
    copies = sum(n - 1 for _, n in scan.texts.items() if n > 1)
    bits = [f"« {_short(scan.samples[k], 48)} » ×{n}" for k, n in repeated[:2]]
    if tests:
        bits.append(f"utilisateur {', '.join(tests)}")
    preview = [
        {"text": _short(scan.samples[k]), "count": n, "users": sorted(scan.text_users.get(k, {}))}
        for k, n in repeated[:MAX_ITEMS_PER_KIND]
    ]
    commands = ["grimoire memory export --file memoire-avant-purge.json"]
    note = "L'export garde tout le store en JSON avant toute suppression."
    if copies:
        commands.append("grimoire memory gc")
        note += " gc retire ensuite les copies exactes (même texte, même utilisateur) : une seule reste."
    if tests:
        note += " Les entrées des identités de test qui restent se suppriment une à une depuis Recherche."
    title = (f"{_plural(copies, 'copie', 'copies')} et des entrées de test dans le store" if copies and tests
             else f"{_plural(copies, 'copie', 'copies')} dans le store" if copies
             else "Entrées de test dans le store")
    return _hygiene_item(
        MEMORY_QUALITY, "contamination", title, " ; ".join(bits) + ". Export JSON avant purge.",
        {"label": "Purger (aperçu)…", "tone": "danger", "command": " && ".join(commands), "note": note,
         "preview": preview},
        copies=copies, testUsers=tests,
    )


def _resolution_terms(fix: str) -> str:
    marker = "Resolved: "
    start = fix.find(marker)
    if start < 0:
        return "<termes>"
    end = fix.find("'", start)
    return fix[start + len(marker): end if end > 0 else None].strip() or "<termes>"


def _hygiene(report: dict[str, Any], scan: _StoreScan | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if scan is not None:
        quality = _contamination(scan)
        if quality:
            items.append(quality)
    lint = report["measures"]["lint"]
    for issue in lint["contradictions"][:MAX_ITEMS_PER_KIND]:
        a, b = (issue["entries"] + ["", ""])[:2]
        files = issue["files"] or ["?", "?"]
        items.append(_hygiene_item(
            MEMORY_CONTRADICTION, f"{issue['id']}:{'+'.join(files)}",
            f"Contradiction : {' et '.join(files)}", f"« {_short(a, 60)} » contre « {_short(b, 60)} »",
            {"label": "Aperçu…", "command": "grimoire memory lint",
             "note": ("Tranche entre les deux entrées, puis consigne la résolution dans contradiction-log.md : "
                      f"- [AAAA-MM-JJ] Resolved: {_resolution_terms(issue['fix'])}"),
             "preview": [{"text": _short(a), "file": files[0]}, {"text": _short(b), "file": files[-1]}]},
            files=files,
        ))
    for orphan in (scan.orphans if scan is not None else [])[:MAX_ITEMS_PER_KIND]:
        items.append(_hygiene_item(
            MEMORY_ORPHAN, orphan["id"], f"Remplaçant introuvable : {_short(orphan['text'], 60)}",
            f"superseded_by vise {orphan['target']}, absent du store : l'entrée reste servie comme un fait",
            {"label": "Aperçu…", "command": f"grimoire memory show {orphan['id']}", "entry": orphan["id"],
             "note": ("Ouvre l'entrée : remplace-la par une décision existante (grimoire memory supersede) "
                      "ou retire la marque.")},
            since=orphan["since"], entry=orphan["id"], target=orphan["target"],
        ))
    drift = report["measures"]["drift"]
    if drift is not None:
        where = f"collection {drift['collection'] or 'sans collection'}"
        if drift["graphDrift"]:
            items.append(_hygiene_item(
                MEMORY_PARITY, "graph", "Dérive store / graphe",
                f"store {drift['store']}, graphe {drift['graph']} ({where}) : {abs(drift['graphDrift'])} d'écart",
                {"label": "Aperçu…", "command": "grimoire memory gate --sync",
                 "note": "Resynchronise le graphe et les vecteurs depuis le store, sans perte."},
                store=drift["store"], graph=drift["graph"], collection=drift["collection"],
            ))
        if drift["lexicalDrift"]:
            items.append(_hygiene_item(
                MEMORY_PARITY, "lexical", "Dérive store / index lexical",
                f"store {drift['store']}, index lexical {drift['lexical']} : {abs(drift['lexicalDrift'])} d'écart",
                {"label": "Aperçu…", "command": "grimoire memory reindex-lexical",
                 "note": "Recopie le store dans l'index lexical compagnon (BM25)."},
                store=drift["store"], lexical=drift["lexical"],
            ))
    code = report["measures"]["codegraph"]
    if code is not None and code["verdict"] == "stale":
        neo4j = code["target"] == "neo4j"
        items.append(_hygiene_item(
            CODEGRAPH_STALE, "codegraph", "Graphe de code périmé",
            f"{_plural(code['changed'], 'fichier changé', 'fichiers changés')} depuis l'indexation",
            {"label": "Aperçu…",
             "command": "grimoire memory graph sync-code" if neo4j else "grimoire_codegraph_index",
             "note": ("Réindexe le code dans Neo4j." if neo4j
                      else "Outil MCP grimoire_codegraph_index, à lancer depuis ton agent.")},
            since=code["recordedAt"], changed=code["changed"], target=code["target"],
        ))
    for motif in report["measures"]["shared"]["motifs"][:MAX_ITEMS_PER_KIND]:
        aging = motif["freshness"] == "aging"
        items.append(_hygiene_item(
            SHARED_AGING, motif["id"],
            f"Motif transverse {'vieillissant' if aging else 'en hypothèse'} : {_short(motif['text'], 60)}",
            f"dernière confirmation il y a {motif['age']} jours" if motif["age"] is not None else "jamais confirmé",
            {"label": "Aperçu…", "command": f"grimoire memory shared confirm {motif['id']}",
             "note": ("Confirme seulement s'il tient encore ici ; sinon laisse-le vieillir : "
                      "il reste servi comme hypothèse.")},
            freshness=motif["freshness"],
        ))
    return items


# ── Rapport d'un projet ─────────────────────────────────────────────────────


def _open_store(cfg: Any, root: Path, *, attention: bool) -> tuple[Any, str | None]:
    """Le manager du projet en lecture seule, ou pourquoi on ne l'ouvre pas."""
    from grimoire.memory.store_probe import store_exists
    from grimoire.tools.workspace_knowledge import open_readonly

    backend = str(cfg.memory.backend or "")
    if attention and backend not in _FILE_BACKENDS:
        # La route d'attention ne touche jamais le réseau : seul un statut déjà sondé et sain l'y autorise.
        from grimoire.tools.memory_link import memory_link_status

        if memory_link_status(root).get("state") != "ok":
            return None, "mémoire non sondée : ouvre Savoir › Santé et carte pour la mesurer"
    if store_exists(cfg, root) is False:
        return None, "aucun store encore créé"
    _cfg, mgr, reason = open_readonly(root)
    return mgr, reason


def project_report(root: Path, *, slug: str = "", name: str = "", attention: bool = False) -> dict[str, Any]:
    """Le rapport de santé d'un projet lisible : mesures, critères, alerte, hygiène."""
    from grimoire.core.config import GrimoireConfig
    from grimoire.tools.workspace_knowledge import ownership

    report: dict[str, Any] = {
        "schemaVersion": QUALITY_SCHEMA_VERSION,
        "project": {"slug": slug, "name": name},
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "reason": None, "backend": None, "storeReason": None, "alert": False, "alertReasons": [],
        "summary": {"fail": 0, "ok": 0, "unknown": 0, "total": 0},
        "criteria": [], "hygiene": [], "measures": {},
    }
    config_path = root / "project-context.yaml"
    if not config_path.is_file():
        return {**report, "reason": "projet non initialisé (pas de project-context.yaml)"}
    try:
        cfg = GrimoireConfig.from_yaml(config_path)
    except Exception as exc:
        return {**report, "reason": f"configuration illisible : {exc}"}
    report["project"]["name"] = name or str(cfg.project.name or root.name)
    report["backend"] = cfg.memory.backend
    scan: _StoreScan | None = None
    drift: dict[str, Any] | None = None
    store_reason: str | None
    try:
        mgr, store_reason = _open_store(cfg, root, attention=attention)
    except Exception as exc:
        mgr, store_reason = None, f"store illisible : {exc}"
    if mgr is not None:
        owners = ownership(cfg, root)
        try:
            scan = _scan(mgr, owners)
            drift = _drift(cfg, root, mgr, owners)
        except Exception as exc:
            scan, drift, store_reason = None, None, f"lecture du store impossible : {exc}"
    try:
        lint = _lint(root)
    except Exception as exc:
        lint = {"files": 0, "entries": 0, "errors": None, "warnings": None, "contradictions": [],
                "reason": f"lint impossible : {exc}"}
    shared = _shared(cfg)
    try:
        code = _codegraph(cfg, root)
    except Exception as exc:
        code = {"verdict": "unverified", "reason": f"lecture impossible : {exc}", "target": "", "changed": 0,
                "recordedAt": ""}
    report["storeReason"] = store_reason
    report["measures"] = {
        "entries": scan.total if scan else None,
        "scanned": (scan.total + scan.others + scan.unattributed + scan.retracted) if scan else 0,
        "complete": bool(scan and scan.complete),
        "distinctTexts": len(scan.texts) if scan else None,
        "duplicates": [
            {"text": _short(scan.samples[k]), "count": n} for k, n in scan.texts.most_common(5) if n > 1
        ] if scan else [],
        "users": [
            {"id": u, "count": n, "test": u.casefold() in TEST_IDENTITIES} for u, n in scan.users.most_common()
        ] if scan else [],
        "provenance": {
            "souvenirs": scan.souvenirs if scan else None,
            "withProvenance": scan.with_provenance if scan else None,
            "rate": (scan.with_provenance / scan.souvenirs) if scan and scan.souvenirs else None,
        },
        "projections": {
            "count": scan.projections if scan else None,
            "share": (scan.projections / scan.total) if scan and scan.total else None,
        },
        "hidden": {"otherProjects": scan.others, "unattributed": scan.unattributed} if scan else None,
        "supersession": {"superseded": scan.superseded, "orphans": len(scan.orphans)} if scan else None,
        "drift": drift,
        "lint": lint,
        "shared": shared,
        "codegraph": code,
    }
    criteria = [*_store_criteria(scan, store_reason), _drift_criterion(drift, store_reason), _lint_criterion(lint)]
    criteria += [c for c in (_shared_criterion(shared), _codegraph_criterion(code)) if c is not None]
    states = Counter(c["state"] for c in criteria)
    report["criteria"] = criteria
    report["summary"] = {"fail": states[FAIL], "ok": states[OK], "unknown": states[UNKNOWN], "total": len(criteria)}
    report["alert"] = bool(states[FAIL])
    report["alertReasons"] = [c["label"] for c in criteria if c["state"] == FAIL]
    report["hygiene"] = _hygiene(report, scan)
    return report


# ── Cache de la route d'attention ───────────────────────────────────────────


_CACHE: dict[Path, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()


def _remember(root: Path, report: dict[str, Any]) -> None:
    with _CACHE_LOCK:
        _CACHE[root.resolve()] = (time.monotonic(), report)


def cached_report(root: Path, *, slug: str = "", name: str = "", max_age: float = HYGIENE_MAX_AGE) -> dict[str, Any]:
    """Le rapport du projet pour la route d'attention : repris s'il a moins de *max_age* secondes.

    Un rapport recalculé ici ne touche jamais le réseau (``attention=True``).
    """
    with _CACHE_LOCK:
        hit = _CACHE.get(root.resolve())
    if hit is not None and time.monotonic() - hit[0] <= max_age:
        return hit[1]
    report = project_report(root, slug=slug, name=name, attention=True)
    _remember(root, report)
    return report


def memory_quality(project_root: Path, *, slug: str | None = None) -> dict[str, Any]:
    """Charge utile de ``GET /api/workspace/memory/quality`` — toujours une mesure fraîche."""
    from grimoire.tools.workspace_knowledge import _target

    target = _target(project_root, slug)
    if target.root is None:
        return {
            "schemaVersion": QUALITY_SCHEMA_VERSION, "project": {"slug": target.slug, "name": target.name},
            "reason": target.reason, "alert": False, "alertReasons": [], "criteria": [], "hygiene": [],
            "summary": {"fail": 0, "ok": 0, "unknown": 0, "total": 0}, "measures": {},
        }
    report = project_report(target.root, slug=target.slug, name=target.name)
    _remember(target.root, report)
    return report
