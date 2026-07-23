"""Serveur local ``grimoire serve`` — mode local de l'UI Forge (H2).

Sert l'UI statique du site (si disponible) et expose une API locale :
état du projet, artefacts gouvernés (« mon setup »), gestion des
extensions, archetypes pour le wizard, CRUD des blueprints et stream SSE
de la télémétrie (``events.jsonl``).

Principes :
- Bind sur 127.0.0.1 uniquement : c'est un outil local, pas un service.
- Le serveur ne devient jamais un moteur d'exécution : il lit, valide et
  écrit des artefacts ; le runtime existant exécute.
- Stdlib uniquement (http.server), cohérent avec ext_manager.

Usage standalone::

    python3 -m grimoire.tools.forge_server --project-root . --port 4173
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from grimoire.tools.blueprint_context import (
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_EDGE_CHANNEL,
    DIGEST_CONTRACTS,
    DIGEST_TOKENS,
    EDGE_CHANNELS,
    NODE_BASE_TOKENS,
    isolation_regions,
    region_membership,
)
from grimoire.tools.blueprint_context import as_dict as _as_dict
from grimoire.tools.blueprint_context import context_policy as _context_policy
from grimoire.tools.blueprint_context import context_section as _context_section
from grimoire.tools.blueprint_context import (
    context_shape_errors as _context_shape_errors,
)
from grimoire.tools.blueprint_gate import (
    compile_gate_section,
    gate_lint,
    gate_shape_errors,
)
from grimoire.tools.blueprint_primitives import PRIMITIVE_NAMES, is_valid_role
from grimoire.tools.blueprint_primitives import (
    primitives_catalogue as _primitives_catalogue,
)
from grimoire.tools.blueprint_resilience import (
    compile_resilience_section,
    resilience_lint,
    resilience_shape_errors,
    trace_failure,
)
from grimoire.tools.cost_model import cost_model as _cost_model
from grimoire.tools.cost_model import node_entry_tokens
from grimoire.tools.ext_manager import (
    MANIFEST_NAME,
    ExtensionError,
    InstallResult,
    install_extension,
    list_installed,
    load_manifest,
    remove_extension,
)
from grimoire.tools.memory_link import backend_catalogue, memory_link_status
from grimoire.tools.project_setup import archetypes_catalogue, build_setup_plan

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PATTERN_REF_RE = re.compile(r"^[A-Z]{3}-\d{2}$")
BLUEPRINTS_RELPATH = Path("_grimoire") / "blueprints"

# Hypothèse de promotion beta→stable de la stigmergie (QUA-13 : la mesure
# sert une décision). Exposée telle quelle par /api/stigmergy (bloc behavior)
# pour que les ratios soient lus contre la thèse qu'ils testent.
STIGMERGY_TARGET_USEFUL_RATIO = 0.4
STIGMERGY_PROMOTION_MIN_EMITTED = 20
STIGMERGY_PROMOTION_HYPOTHESIS = (
    "Le board coordonne réellement si au moins 40 % des signaux émis "
    "produisent une coordination utile (résolution ou relais), mesuré "
    "sur au moins 20 émissions."
)

# Pins par famille pour les blueprints du Studio (v2) : même heuristique que
# web/atelier-nav.js — à remplacer par une curation par pattern dans le
# catalogue quand elle existera. `handoff-packet` circule partout.
STUDIO_FAMILY_PINS: dict[str, dict[str, list[str]]] = {
    "ORG": {"in": ["handoff-packet"], "out": ["task-envelope"]},
    "ORC": {"in": ["task-envelope"], "out": ["task-envelope", "handoff-packet"]},
    "GOV": {"in": ["task-envelope"], "out": ["task-envelope"]},
    "MOD": {"in": ["task-envelope"], "out": ["handoff-packet"]},
    "COG": {"in": ["task-envelope", "context-pack"], "out": ["handoff-packet"]},
    "QUA": {
        "in": ["handoff-packet", "evidence-pack"],
        "out": ["evidence-pack", "verification-verdict"],
    },
    "KNO": {
        "in": ["handoff-packet", "evidence-pack"],
        "out": ["context-pack", "memory-record"],
    },
    "RUN": {"in": ["handoff-packet"], "out": ["telemetry-event"]},
}


ARTIFACT_SURFACES = {
    "agents": (".github/agents", "*.agent.md"),
    "workflows": (".github/prompts", "*.prompt.md"),
    "skills": (".github/skills", "*/SKILL.md"),
    "instructions": (".github/instructions", "*.instructions.md"),
    "hooks": (".github/hooks", "*.json"),
}

EVENT_SOURCES = (
    ("hook-runtime", Path("_grimoire-runtime-output") / "hook-runtime" / "events.jsonl"),
    ("task-flow", Path("_grimoire-runtime-output") / "task-flow" / "events.jsonl"),
)


class ForgeAPI:
    """Logique métier de l'API locale, testable sans HTTP."""

    def __init__(self, project_root: Path, kit_root: Path, ui_dir: Path | None) -> None:
        self.project_root = project_root.resolve()
        self.kit_root = kit_root.resolve()
        self.ui_dir = ui_dir.resolve() if ui_dir else None

    # ── état ──────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        version_file = self.kit_root / "version.txt"
        return {
            "projectRoot": str(self.project_root),
            "kitRoot": str(self.kit_root),
            "kitVersion": version_file.read_text(encoding="utf-8").strip()
            if version_file.is_file()
            else "dev",
            "ui": str(self.ui_dir) if self.ui_dir else None,
        }

    def setup_view(self) -> dict[str, Any]:
        artifacts: dict[str, list[str]] = {}
        for kind, (rel, pattern) in ARTIFACT_SURFACES.items():
            base = self.project_root / rel
            artifacts[kind] = sorted(
                str(p.relative_to(self.project_root)) for p in base.glob(pattern)
            ) if base.is_dir() else []
        return {
            "artifacts": artifacts,
            "extensions": list_installed(self.project_root),
            "blueprints": [b["id"] for b in self.blueprints_list()],
        }

    # ── modèle de coût calibré (C2) ─────────────────────────────────────────

    def cost_model_view(self, model: str | None = None) -> dict[str, Any]:
        """Modèle de coût calibré — source de vérité unique de la vue COÛT,
        de la pression de contexte, du Gate(budget) et de ``cost-under``.

        Remplace la table statique ``NODE_COST`` de ``web/bp2-cost.js``.
        """
        return _cost_model(model)

    def primitives_view(self) -> dict[str, Any]:
        """Les 7 primitives de node (`role`) + mapping des cases XXL (P0.3).

        Source de vérité de la re-catégorisation de la palette.
        """
        return _primitives_catalogue()

    # ── wizard (logique dans project_setup — B2, compile vers grimoire up) ──

    def archetypes(self) -> list[dict[str, Any]]:
        return archetypes_catalogue(self.kit_root)

    def setup_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return build_setup_plan(
            self.project_root, payload, install=self.extension_add
        )

    def memory_link_view(self) -> dict[str, Any]:
        """Lien projet ↔ BDD mémoire (B1) : backend configuré, santé, volume."""
        return memory_link_status(self.project_root)

    # ── extensions ────────────────────────────────────────────────────────

    def extensions_view(self) -> dict[str, Any]:
        available = []
        base = self.kit_root / "extensions"
        if base.is_dir():
            for manifest_path in sorted(base.glob("*/extension.json")):
                try:
                    m = load_manifest(manifest_path.parent)
                except ExtensionError:
                    continue
                available.append(
                    {
                        "id": m["id"],
                        "kind": m.get("kind"),
                        "name": m["name"],
                        "version": m["version"],
                        "description": m["description"],
                        "patterns": m["patterns"].get("implements", []),
                        "nodes": m.get("provides", {}).get("nodes", []),
                        "source": str(manifest_path.parent),
                    }
                )
        return {"installed": list_installed(self.project_root), "available": available}

    def extension_add(self, source: str) -> InstallResult:
        ext_dir = (
            self.kit_root / "extensions" / source
            if SLUG_RE.match(source)
            else Path(source)
        )
        return install_extension(ext_dir, self.project_root)

    def extension_remove(self, ext_id: str) -> None:
        remove_extension(ext_id, self.project_root)

    # ── blueprints ────────────────────────────────────────────────────────

    def _blueprint_path(self, bp_id: str) -> Path:
        if not SLUG_RE.match(bp_id):
            raise ValueError(f"id de blueprint invalide : {bp_id}")
        return self.project_root / BLUEPRINTS_RELPATH / f"{bp_id}.blueprint.json"

    def blueprints_list(self) -> list[dict[str, Any]]:
        base = self.project_root / BLUEPRINTS_RELPATH
        result = []
        if base.is_dir():
            for p in sorted(base.glob("*.blueprint.json")):
                try:
                    bp = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                result.append(
                    {
                        "id": bp.get("id", p.stem.replace(".blueprint", "")),
                        "name": bp.get("name", ""),
                        "nodes": len(bp.get("nodes", [])),
                        "edges": len(bp.get("edges", [])),
                    }
                )
        return result

    def blueprint_get(self, bp_id: str) -> dict[str, Any]:
        path = self._blueprint_path(bp_id)
        if not path.is_file():
            raise FileNotFoundError(bp_id)
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def blueprint_put(self, bp_id: str, blueprint: dict[str, Any]) -> dict[str, Any]:
        lint = self.blueprint_lint(blueprint)
        path = self._blueprint_path(bp_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"saved": bp_id, **lint}

    # ── pont Studio (v2) → format compilable (v1) ─────────────────────────

    @staticmethod
    def _is_studio(blueprint: dict[str, Any]) -> bool:
        """Un blueprint du Studio (v2) : nodes positionnés sans pins déclarés."""
        if blueprint.get("blueprintVersion") == 2:
            return True
        nodes = blueprint.get("nodes", [])
        return bool(nodes) and all("pins" not in n for n in nodes)

    def _ext_node_index(self) -> dict[str, str]:
        """node_id du manifeste -> id d'extension, pour retrouver `ext/node`."""
        index: dict[str, str] = {}
        base = self.kit_root / "extensions"
        if not base.is_dir():
            return index
        for manifest_path in sorted(base.glob(f"*/{MANIFEST_NAME}")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            for node in manifest.get("provides", {}).get("nodes", []):
                if isinstance(node, dict) and node.get("id"):
                    index[str(node["id"])] = str(manifest.get("id", manifest_path.parent.name))
        return index

    def _studio_to_v1(self, blueprint: dict[str, Any]) -> dict[str, Any]:
        """Convertit un état Studio en blueprint v1 lint/simulable/compilable.

        Les groupes (sous-flows C4) sont aplatis ; les pins de chaque node
        sont dérivés des contrats de ses edges (plus, pour les patterns,
        l'heuristique par famille) : la structure du Studio est la source
        de vérité, le typage v1 en est la projection.
        """
        if not self._is_studio(blueprint):
            return blueprint
        ext_index = self._ext_node_index()
        catalogue = self._catalogue()
        names = {p["id"]: p["name"] for p in (catalogue or {}).get("patterns", [])}

        flat_nodes: list[dict[str, Any]] = []
        flat_edges: list[dict[str, Any]] = []

        def walk(graph: dict[str, Any]) -> None:
            for n in graph.get("nodes", []):
                if n.get("kind") == "group":
                    group_node: dict[str, Any] = {
                        "id": n.get("id"),
                        "kind": "composite-inline",
                        "ref": n.get("name", "sous-flow"),
                        "label": n.get("name", "sous-flow"),
                    }
                    if isinstance(n.get("config"), dict):
                        group_node["config"] = n["config"]
                    flat_nodes.append(group_node)
                    walk(n.get("sub", {}))
                    continue
                ref = str(n.get("ref", ""))
                if n.get("kind") in ("agent", "trigger"):
                    kind, label = "agent-spec", n.get("name", n.get("kind"))
                elif ref in ext_index:
                    kind, label = "extension-node", ref
                    ref = f"{ext_index[ref]}/{ref}"
                elif PATTERN_REF_RE.match(ref):
                    kind, label = "pattern", names.get(ref, ref)
                else:
                    kind, label = "artifact", ref
                flat: dict[str, Any] = {
                    "id": n.get("id"), "kind": kind, "ref": ref, "label": label,
                }
                if isinstance(n.get("config"), dict):
                    flat["config"] = n["config"]
                flat_nodes.append(flat)
            for e in graph.get("edges", []):
                flat_edges.append(dict(e))

        walk(blueprint)

        # pins dérivés : contrats des edges + heuristique famille des patterns
        pins_in: dict[str, set[str]] = {str(n["id"]): set() for n in flat_nodes}
        pins_out: dict[str, set[str]] = {str(n["id"]): set() for n in flat_nodes}
        for e in flat_edges:
            contract = str(e.get("contract") or "handoff-packet")
            src, dst = str(e.get("from", "")), str(e.get("to", ""))
            if src in pins_out:
                pins_out[src].add(contract)
            if dst in pins_in:
                pins_in[dst].add(contract)
        for n in flat_nodes:
            if n["kind"] == "pattern":
                family = str(n["ref"])[:3]
                heur = STUDIO_FAMILY_PINS.get(family, {})
                pins_in[str(n["id"])].update(heur.get("in", []))
                # Un node isolé ne sort que par digest : pas de pins de sortie
                # heuristiques, seuls les contrats de ses edges font foi (R-C5).
                if _context_policy(n).get("isolation") != "isolated":
                    pins_out[str(n["id"])].update(heur.get("out", []))
            n["pins"] = [
                {"id": f"in-{c}", "direction": "in", "contract": c}
                for c in sorted(pins_in[str(n["id"])])
            ] + [
                {"id": f"out-{c}", "direction": "out", "contract": c}
                for c in sorted(pins_out[str(n["id"])])
            ]

        edges_v1 = [
            {
                "from": f"{e.get('from')}.out-{e.get('contract') or 'handoff-packet'}",
                "to": f"{e.get('to')}.in-{e.get('contract') or 'handoff-packet'}",
                "contract": e.get("contract") or "handoff-packet",
            }
            for e in flat_edges
        ]
        meta = blueprint.get("meta") or {}
        return {
            "blueprintVersion": 1,
            "id": blueprint.get("id", "blueprint"),
            "name": blueprint.get("name", meta.get("name", blueprint.get("id", ""))),
            "description": blueprint.get("description", ""),
            "catalogRef": {
                "version": (catalogue or {}).get("catalogVersion", "inconnue")
            },
            "nodes": flat_nodes,
            "edges": edges_v1,
        }

    def _catalogue(self) -> dict[str, Any] | None:
        if self.ui_dir:
            candidate = self.ui_dir / "data" / "catalogue-export.json"
            if candidate.is_file():
                return cast(dict[str, Any], json.loads(candidate.read_text(encoding="utf-8")))
        return None

    def blueprint_validate(self, blueprint: dict[str, Any]) -> list[str]:
        """Erreurs bloquantes : structure + pins typés (H4).

        Une connexion dont les contrats de pins ne correspondent pas ne
        compile pas.
        """
        errors: list[str] = []
        nodes = blueprint.get("nodes", [])
        node_ids = [n.get("id") for n in nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("ids de nodes non uniques")
        for n in nodes:
            role = n.get("role")
            if not is_valid_role(role):
                errors.append(
                    f"role invalide ({role}) sur node {n.get('id')} — attendu "
                    f"l'une des 7 primitives : {' | '.join(PRIMITIVE_NAMES)}"
                )
        pin_contracts = {
            f"{n.get('id')}.{p.get('id')}": p.get("contract")
            for n in nodes
            for p in n.get("pins", [])
        }
        for edge in blueprint.get("edges", []):
            missing = False
            for end in ("from", "to"):
                if edge.get(end) not in pin_contracts:
                    errors.append(f"edge {end} inconnu : {edge.get(end)}")
                    missing = True
            if missing:
                continue
            c_from = pin_contracts[edge["from"]]
            c_to = pin_contracts[edge["to"]]
            if c_from != c_to:
                errors.append(
                    f"connexion invalide {edge['from']} -> {edge['to']} : "
                    f"contrats incompatibles ({c_from} != {c_to})"
                )
            elif edge.get("contract") and edge["contract"] != c_from:
                errors.append(
                    f"edge {edge['from']} -> {edge['to']} : contrat déclaré "
                    f"({edge['contract']}) != contrat des pins ({c_from})"
                )
            channel = edge.get("channel")
            if channel is not None and channel not in EDGE_CHANNELS:
                errors.append(
                    f"edge {edge['from']} -> {edge['to']} : channel invalide "
                    f"({channel}) — attendu {' | '.join(EDGE_CHANNELS)}"
                )

        # Régions d'isolation (C3) : membres existants + quarantaine (R-C7).
        regions = isolation_regions(blueprint)
        if regions:
            id_set = set(node_ids)
            membership = region_membership(regions)
            for region in regions:
                if not region["members"]:
                    errors.append(f"région {region['id']} : aucun membre")
                for m in region["members"]:
                    if m not in id_set:
                        errors.append(f"région {region['id']} : membre inconnu {m}")
            for edge in blueprint.get("edges", []):
                src = str(edge.get("from", "")).split(".")[0]
                dst = str(edge.get("to", "")).split(".")[0]
                src_region = membership.get(src)
                if src_region and membership.get(dst) != src_region:
                    contract = pin_contracts.get(edge.get("from")) or edge.get("contract")
                    if contract not in DIGEST_CONTRACTS:
                        errors.append(
                            f"R-C7 : la région {src_region} exporte via "
                            f"{edge.get('from')} -> {edge.get('to')} un contrat "
                            f"non-digest ({contract}) — sortir via "
                            f"{' ou '.join(DIGEST_CONTRACTS)} (quarantaine)"
                        )

        # Gate universel (P2.1) : forme des politiques (dont R-G4, schéma
        # résolu contre le projet) + frontière de confiance R-G1/R-G2.
        def _schema_resolves(rel: str) -> bool:
            return (self.project_root / rel).is_file()

        for n in nodes:
            errors.extend(gate_shape_errors(n, resolve_schema=_schema_resolves))
        gate_errors, _ = gate_lint(nodes, blueprint.get("edges", []))
        errors.extend(gate_errors)

        # Famille résilience (P2.2) : forme (R-F1) + plan de défaillance
        # (R-F2 contrat d'erreur, R-F4 escalation terminale).
        for n in nodes:
            errors.extend(resilience_shape_errors(n))
        res_errors, _ = resilience_lint(nodes, blueprint.get("edges", []))
        errors.extend(res_errors)

        catalogue = self._catalogue()
        if catalogue:
            known = {p["id"] for p in catalogue.get("patterns", [])}
            contracts = {c["id"] for c in catalogue.get("contracts", [])}
            for n in nodes:
                if n.get("kind") == "pattern" and n.get("ref") not in known:
                    errors.append(f"pattern inconnu du catalogue : {n.get('ref')}")
                for p in n.get("pins", []):
                    if p.get("contract") and p["contract"] not in contracts:
                        errors.append(
                            f"contrat inconnu : {p['contract']} (node {n.get('id')})"
                        )
        for n in nodes:
            if n.get("kind") == "artifact":
                target = self.project_root / str(n.get("ref", ""))
                if not target.exists():
                    errors.append(f"artefact absent du projet : {n.get('ref')}")
            elif n.get("kind") == "composite":
                ref = str(n.get("ref", ""))
                if ref.startswith("use-case:"):
                    if catalogue is not None:
                        known_uc = {u["id"] for u in catalogue.get("useCases", [])}
                        if ref.removeprefix("use-case:") not in known_uc:
                            errors.append(f"use-case inconnu du catalogue : {ref}")
                elif ref.endswith(".blueprint.json"):
                    if not (self.project_root / ref).is_file():
                        errors.append(f"sous-blueprint absent du projet : {ref}")
                else:
                    errors.append(
                        f"ref composite invalide (use-case:<id> ou "
                        f"chemin .blueprint.json) : {ref}"
                    )

        # ── politique de contexte (C1) : forme + R-C4 + R-C5 ──
        for n in nodes:
            errors.extend(_context_shape_errors(n))
        for n in nodes:
            if _context_policy(n).get("isolation") != "isolated":
                continue
            nid = str(n.get("id"))
            bad: set[str] = set()
            for p in n.get("pins", []):
                if p.get("direction") == "out" and p.get("contract") not in DIGEST_CONTRACTS:
                    bad.add(str(p.get("contract")))
            for edge in blueprint.get("edges", []):
                if str(edge.get("from", "")).split(".")[0] != nid:
                    continue
                contract = edge.get("contract")
                if contract and contract not in DIGEST_CONTRACTS:
                    bad.add(str(contract))
            for contract in sorted(bad):
                errors.append(
                    f"R-C5 : node isolé {nid} exporte un contrat non-digest "
                    f"({contract}) — sortir via handoff-packet ou context-pack, "
                    f"ou lever l'isolation"
                )
        return errors

    def blueprint_lint(self, blueprint: dict[str, Any]) -> dict[str, Any]:
        """Lint normatif (H4) : erreurs bloquantes + avertissements catalogue.

        Avertissements dérivés du catalogue : dépendances de patterns
        absentes du flow, exigences des extensions, heuristiques
        anti-patterns (flow sans preuve, node isolé).
        """
        blueprint = self._studio_to_v1(blueprint)
        errors = self.blueprint_validate(blueprint)
        warnings: list[str] = []
        nodes = blueprint.get("nodes", [])
        flow_patterns = {n.get("ref") for n in nodes if n.get("kind") == "pattern"}
        catalogue = self._catalogue()
        use_case_patterns = {
            u["id"]: u.get("patterns", [])
            for u in (catalogue or {}).get("useCases", [])
        }
        for n in nodes:
            if n.get("kind") == "extension-node":
                flow_patterns.update(self._extension_node_patterns(n.get("ref", "")))
            elif n.get("kind") == "composite":
                ref = str(n.get("ref", ""))
                if ref.startswith("use-case:"):
                    flow_patterns.update(
                        use_case_patterns.get(ref.removeprefix("use-case:"), [])
                    )
        if catalogue and flow_patterns:
            names = {p["id"]: p["name"] for p in catalogue.get("patterns", [])}
            for rel in catalogue.get("relations", []):
                if (
                    rel.get("kind") == "depends"
                    and rel.get("from") in flow_patterns
                    and rel.get("to") not in flow_patterns
                ):
                    warnings.append(
                        f"{rel['from']} dépend de {rel['to']} "
                        f"({names.get(rel['to'], '?')}), absent du flow"
                    )
            # Anti-pattern « Faux Done » : un flow multi-nodes sans preuve QUA
            if len(nodes) >= 2 and not any(
                str(p).startswith("QUA-") for p in flow_patterns
            ):
                warnings.append(
                    "aucun pattern de preuve (QUA-*) dans le flow — "
                    "risque d'anti-pattern Faux Done"
                )

        # Node isolé : présent mais connecté à rien
        connected: set[str] = set()
        for edge in blueprint.get("edges", []):
            for end in ("from", "to"):
                connected.add(str(edge.get(end, "")).split(".")[0])
        for n in nodes:
            if len(nodes) >= 2 and n.get("id") not in connected:
                warnings.append(f"node isolé : {n.get('id')} ({n.get('label')})")

        warnings.extend(self._context_warnings(blueprint))
        # Gate universel (P2.1) : R-G3 (block sans alternative) et R-G5
        # (sortie sans guardrail out) — non bloquants.
        _, gate_warnings = gate_lint(nodes, blueprint.get("edges", []))
        warnings.extend(gate_warnings)
        # Résilience (P2.2) : R-F3 (node externe sans chemin de défaillance).
        _, res_warnings = resilience_lint(nodes, blueprint.get("edges", []))
        warnings.extend(res_warnings)

        return {"errors": errors, "warnings": warnings}

    @staticmethod
    def _context_warnings(blueprint: dict[str, Any]) -> list[str]:
        """Lint de la politique de contexte (C1) : R-C1, R-C2, R-C3."""
        warnings: list[str] = []
        nodes = blueprint.get("nodes", [])
        node_by_id = {str(n.get("id")): n for n in nodes}
        edge_pairs = [
            (
                str(e.get("from", "")).split(".")[0],
                str(e.get("to", "")).split(".")[0],
            )
            for e in blueprint.get("edges", [])
        ]

        # R-C1 : contenu externe (extension) injecté dans une fenêtre partagée
        seen: set[str] = set()
        for src, dst in edge_pairs:
            s_n, d_n = node_by_id.get(src), node_by_id.get(dst)
            if not s_n or not d_n or dst in seen:
                continue
            if s_n.get("kind") != "extension-node":
                continue
            if _context_policy(d_n).get("isolation", "shared") == "shared":
                seen.add(dst)
                warnings.append(
                    f"R-C1 : node {dst} alimenté par l'extension "
                    f"{s_n.get('ref')} en fenêtre partagée — passer en "
                    f'isolation:"isolated" (quarantaine du contenu externe)'
                )

        # R-C2 : chaîne de 4 nodes ou plus sans compaction digest ni ORC-03.
        # Plus longue chaîne « sans digest » terminant sur chaque node,
        # calculée en ordre topologique (un cycle est signalé ailleurs).
        plain: dict[str, bool] = {}
        for nid, n in node_by_id.items():
            compaction = _context_policy(n).get("compaction")
            strategy = (
                compaction.get("strategy") if isinstance(compaction, dict) else None
            )
            plain[nid] = strategy != "digest" and str(n.get("ref", "")) != "ORC-03"
        preds: dict[str, set[str]] = {nid: set() for nid in node_by_id}
        succs: dict[str, set[str]] = {nid: set() for nid in node_by_id}
        for src, dst in edge_pairs:
            if src in preds and dst in preds and src != dst:
                preds[dst].add(src)
                succs[src].add(dst)
        indeg = {nid: len(p) for nid, p in preds.items()}
        queue = sorted(nid for nid, d in indeg.items() if not d)
        run: dict[str, int] = {}
        while queue:
            nid = queue.pop(0)
            upstream = max((run.get(p, 0) for p in preds[nid]), default=0)
            run[nid] = upstream + 1 if plain[nid] else 0
            for nxt in sorted(succs[nid]):
                indeg[nxt] -= 1
                if not indeg[nxt]:
                    queue.append(nxt)
        longest = max(run.values(), default=0)
        if longest >= 4:
            warnings.append(
                f"R-C2 : chaîne de {longest} nodes consécutifs sans compaction "
                f'"digest" ni node ORC-03 — insérer un handoff digest'
            )

        # R-C3 : escalade deep sans justification (discipline ORC-08)
        for nid, n in node_by_id.items():
            budget = _context_policy(n).get("budget")
            if not isinstance(budget, dict):
                continue
            if budget.get("tier") == "deep" and not budget.get("justification"):
                warnings.append(
                    f'R-C3 : budget.tier "deep" sans justification — node {nid} '
                    f"(justifier ou redescendre de tier, discipline ORC-08)"
                )
        return warnings

    def blueprint_simulate(
        self,
        blueprint: dict[str, Any],
        inject_failure: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Simulation pré-exécution (H4) : dry-run du flow avant apply.

        Ne produit aucun effet : ordonne le graphe, vérifie les prérequis
        de chaque node (artefact présent, extension installée, contrôles du
        pattern) et rend un verdict. Le runtime existant reste le seul
        exécutant.

        ``inject_failure`` (P3.1) : ``{"nodeId": ..., "class": ...}`` — mode
        what-if de résilience ; la simulation nominale reste le plan happy, et
        le champ ``failureInjection`` trace le plan de défaillance suivi.
        """
        blueprint = self._studio_to_v1(blueprint)
        lint = self.blueprint_lint(blueprint)
        blockers: list[str] = list(lint["errors"])
        nodes = {str(n["id"]): n for n in blueprint.get("nodes", [])}

        # Ordre topologique (Kahn) sur les connexions node -> node. Seuls les
        # edges du canal nominal ``happy`` portent l'ordre et l'accumulation de
        # contexte (P0.2) ; ``failure``/``escalation`` sont des routes
        # alternatives, hors du chemin nominal.
        channels = dict.fromkeys(EDGE_CHANNELS, 0)
        deps: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for edge in blueprint.get("edges", []):
            channel = edge.get("channel", DEFAULT_EDGE_CHANNEL)
            if channel in channels:
                channels[channel] += 1
            if channel != DEFAULT_EDGE_CHANNEL:
                continue
            src = str(edge.get("from", "")).split(".")[0]
            dst = str(edge.get("to", "")).split(".")[0]
            if src in nodes and dst in nodes:
                deps[dst].add(src)
        order: list[str] = []
        remaining = {nid: set(d) for nid, d in deps.items()}
        while remaining:
            ready = sorted(nid for nid, d in remaining.items() if not d)
            if not ready:
                cycle = ", ".join(sorted(remaining))
                blockers.append(f"cycle détecté dans le flow : {cycle}")
                order.extend(sorted(remaining))
                break
            for nid in ready:
                order.append(nid)
                del remaining[nid]
            for d in remaining.values():
                d.difference_update(ready)

        catalogue = self._catalogue()
        controls = {
            p["id"]: p.get("controls", [])
            for p in (catalogue or {}).get("patterns", [])
        }
        installed = list_installed(self.project_root)

        steps: list[dict[str, Any]] = []
        for position, node_id in enumerate(order, start=1):
            n = nodes[node_id]
            kind, ref = n.get("kind"), str(n.get("ref", ""))
            step: dict[str, Any] = {
                "order": position,
                "id": node_id,
                "kind": kind,
                "ref": ref,
                "label": n.get("label", ""),
            }
            if kind == "pattern":
                step["action"] = "appliquer le pattern"
                step["requirements"] = controls.get(ref, [])
            elif kind == "artifact":
                step["action"] = "exécuté par le runtime existant"
                step["ready"] = (self.project_root / ref).exists()
            elif kind == "extension-node":
                ext_id = ref.split("/")[0]
                step["action"] = f"délégué à l'extension {ext_id}"
                step["ready"] = ext_id in installed
                if ext_id not in installed:
                    blockers.append(
                        f"extension non installée dans le projet : {ext_id} "
                        f"(node {node_id})"
                    )
            elif kind == "composite":
                step["action"] = "expansion du composite"
            steps.append(step)

        # ── pression de contexte (C1) : charge estimée par fenêtre ──
        # charge = base du node (ou budget.maxTokens) + report d'amont ;
        # le report dépend de la compaction du node amont, un node isolé
        # remet le report à zéro pour son aval (seul le digest sort).
        pressure: list[dict[str, Any]] = []
        carry: dict[str, float] = {}
        for node_id in order:
            node = nodes[node_id]
            ctx = _context_policy(node)
            budget = _as_dict(ctx.get("budget"))
            max_tokens = budget.get("maxTokens")
            if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
                base = max_tokens
            elif node.get("ref"):
                # Coût d'entrée calibré du pattern (C2) ; défaut générique sinon.
                base = node_entry_tokens(
                    str(node.get("ref")), is_ext=node.get("kind") == "ext"
                )
            else:
                base = NODE_BASE_TOKENS
            inherited = sum(carry.get(p, 0.0) for p in deps.get(node_id, ()))
            estimated = int(base + inherited)
            pct = round(estimated / CONTEXT_WINDOW_TOKENS * 100, 1)
            verdict = "ok" if pct < 60 else ("warn" if pct <= 85 else "critical")
            pressure.append(
                {
                    "nodeId": node_id,
                    "estimatedTokens": estimated,
                    "windowPct": pct,
                    "verdict": verdict,
                }
            )
            compaction = ctx.get("compaction")
            strategy = (
                compaction.get("strategy") if isinstance(compaction, dict) else None
            ) or "full"
            if ctx.get("isolation") == "isolated":
                carry[node_id] = 0.0
            elif strategy == "digest":
                carry[node_id] = float(DIGEST_TOKENS)
            elif strategy in ("selective", "index-guided"):
                carry[node_id] = estimated * 0.5
            else:
                carry[node_id] = float(estimated)

        warnings = list(lint["warnings"])
        critical = [p["nodeId"] for p in pressure if p["verdict"] == "critical"]
        if critical:
            warnings.append(
                "R-C6 : pression de contexte critique sur "
                + ", ".join(critical)
                + " — ajouter compaction ou isolation en amont"
            )

        exits = [
            nid for nid in order if not any(nid in d for d in deps.values())
        ]
        # Régions d'isolation (C3) : pression agrégée par région quarantinée.
        regions_out: list[dict[str, Any]] = []
        by_node = {p["nodeId"]: p["estimatedTokens"] for p in pressure}
        for region in isolation_regions(blueprint):
            total = sum(by_node.get(m, 0) for m in region["members"])
            pct = round(total / CONTEXT_WINDOW_TOKENS * 100, 1)
            regions_out.append(
                {
                    "id": region["id"],
                    "mode": region["mode"],
                    "members": region["members"],
                    "estimatedTokens": total,
                    "windowPct": pct,
                    "verdict": "ok" if pct < 60 else ("warn" if pct <= 85 else "critical"),
                }
            )
        # Injection d'échec (P3.1) : what-if de résilience. La simulation
        # nominale ci-dessus reste le plan happy ; ce champ trace le plan de
        # défaillance suivi quand le node ciblé échoue.
        failure_injection = None
        if inject_failure:
            failure_injection = trace_failure(
                blueprint,
                str(inject_failure.get("nodeId", "")),
                str(inject_failure.get("class", "unknown")),
            )
        return {
            "verdict": "prêt à appliquer" if not blockers else "bloqué",
            "blockers": blockers,
            # Champ additif (rétro-compatible) : chaque blocker avec sa remédiation.
            "blockerDetails": [
                {"message": b, "hint": self._blocker_hint(b)} for b in blockers
            ],
            # warnings = lint R-C1/R-C2/R-C3 + R-C6 (pression de contexte, C1)
            "warnings": warnings,
            "entryNodes": [nid for nid in order if not deps.get(nid)],
            "exitNodes": exits,
            "steps": steps,
            "contextPressure": pressure,
            # Répartition des edges par canal (P0.2) : le graphe montre ses
            # chemins d'échec et d'escalade, pas seulement le chemin nominal.
            "channels": channels,
            # Régions d'isolation (C3) : chaque région = une fenêtre quarantinée.
            "regions": regions_out,
            # Injection d'échec (P3.1) : null en mode nominal.
            "failureInjection": failure_injection,
        }

    @staticmethod
    def _blocker_hint(blocker: str) -> str:
        """Remédiation concrète pour un blocker de simulation/compilation."""
        if blocker.startswith("extension non installée dans le projet : "):
            ext_id = blocker.removeprefix(
                "extension non installée dans le projet : "
            ).split()[0]
            return (
                f"installer l'extension : grimoire ext add {ext_id} "
                "--registry <registry-dir> (ou grimoire ext add <dossier-extension>)"
            )
        if blocker.startswith("cycle détecté"):
            return "retirer une connexion du cycle : le flow doit être acyclique pour compiler"
        if blocker.startswith("artefact absent du projet : "):
            ref = blocker.removeprefix("artefact absent du projet : ")
            return f"créer {ref} dans le projet ou corriger le ref du node"
        if blocker.startswith(("edge from inconnu", "edge to inconnu")):
            return "chaque extrémité doit être <nodeId>.<pinId> avec un pin déclaré sur le node"
        if "contrats incompatibles" in blocker:
            return "aligner le contrat des deux pins connectés (ou insérer un node adaptateur)"
        if "contrat déclaré" in blocker:
            return "faire correspondre edge.contract au contrat des pins connectés (ou le retirer)"
        if blocker.startswith("pattern inconnu du catalogue"):
            return "utiliser un id de pattern du catalogue (web/data/catalogue-export.json)"
        if blocker.startswith("contrat inconnu"):
            return "utiliser un contrat déclaré dans le catalogue (ex. task-envelope, handoff-packet)"
        if blocker.startswith("use-case inconnu"):
            return "référencer un use-case du catalogue (ref use-case:<id>)"
        if blocker.startswith("sous-blueprint absent"):
            return "créer le sous-blueprint référencé ou corriger son chemin"
        if blocker.startswith("ref composite invalide"):
            return "ref attendu : use-case:<id> ou chemin vers un .blueprint.json du projet"
        if blocker.startswith("ids de nodes non uniques"):
            return "renommer les nodes pour que chaque id soit unique"
        return ""

    def blueprint_compile(self, blueprint: dict[str, Any]) -> dict[str, Any]:
        """Compilation v1 (H4) : le blueprint devient un mission pack gouverné.

        Un blueprint « bloqué » à la simulation ne compile pas. L'artefact
        généré est un ``.prompt.md`` exécutable par l'orchestrateur existant ;
        la section ``compiled`` du blueprint trace le hash pour la détection
        de dérive. Aucun apply automatique : le diff git reste la revue.

        Un blueprint du Studio (v2) est compilé via sa projection v1 ; la
        section ``compiled`` est écrite dans le blueprint d'origine, qui
        reste la source de vérité de l'éditeur.
        """
        source = blueprint
        blueprint = self._studio_to_v1(blueprint)
        report = self.blueprint_simulate(blueprint)
        if report["blockers"]:
            # Fail-closed inchangé : on enrichit seulement le diagnostic
            # (chaque blocker avec sa remédiation).
            details = report.get("blockerDetails") or [
                {"message": b, "hint": ""} for b in report["blockers"]
            ]
            rendered = " ; ".join(
                d["message"] + (f" [fix : {d['hint']}]" if d.get("hint") else "")
                for d in details
            )
            raise ValueError(f"compilation refusée, blueprint bloqué : {rendered}")

        bp_id = str(blueprint.get("id", "blueprint"))
        name = blueprint.get("name", bp_id)
        now = datetime.now(UTC).isoformat()
        catalog_version = blueprint.get("catalogRef", {}).get("version", "inconnue")

        # P0.1 — compilation reproductible : AUCUN horodatage dans le contenu
        # hashé. La date de compilation vit dans les métadonnées du blueprint
        # (``compiled.at``), jamais dans le mission pack : même blueprint +
        # même catalogue => même contenu => même hash.
        lines = [
            "---",
            f"name: {bp_id}-blueprint",
            f"description: Mission pack compilé depuis le blueprint {name}",
            f"generatedFrom: _grimoire/blueprints/{bp_id}.blueprint.json",
            f"catalogVersion: {catalog_version}",
            "---",
            "",
            f"# {name} — mission pack compilé",
            "",
        ]
        if blueprint.get("description"):
            lines += [str(blueprint["description"]), ""]
        lines += [
            "> Artefact généré par la compilation du blueprint — ne pas éditer",
            "> à la main, recompiler depuis l'éditeur. L'exécution appartient au",
            "> runtime existant et passe par ses gates.",
            "",
            "## Plan d'exécution",
            "",
        ]
        nodes_by_id = {str(n.get("id")): n for n in blueprint.get("nodes", [])}
        for step in report["steps"]:
            lines.append(f"### {step['order']}. {step['label']}")
            lines.append("")
            lines.append(f"- Node : `{step['id']}` ({step['kind']} : `{step['ref']}`)")
            if step.get("action"):
                lines.append(f"- Action : {step['action']}")
            if step.get("requirements"):
                lines.append(
                    "- Obligations (contrôles du pattern) : "
                    + ", ".join(step["requirements"])
                )
            if "ready" in step:
                lines.append(f"- Prêt : {'oui' if step['ready'] else 'NON'}")
            step_node = nodes_by_id.get(str(step["id"]), {})
            lines.extend(compile_gate_section(step_node))
            lines.extend(
                compile_resilience_section(step_node, blueprint.get("edges", []))
            )
            lines.extend(_context_section(step_node))
            lines.append("")

        edges = blueprint.get("edges", [])
        if edges:
            lines += ["## Contrats aux frontières", ""]
            lines += [
                f"- `{e['from']}` -> `{e['to']}` : contrat `{e.get('contract', '?')}`"
                for e in edges
            ]
            lines.append("")
        lines += [
            "## Entrées / sorties du flow",
            "",
            f"- Entrées : {', '.join(report['entryNodes']) or '—'}",
            f"- Sorties : {', '.join(report['exitNodes']) or '—'}",
            "",
        ]
        if report.get("regions"):
            lines += ["## Régions d'isolation", ""]
            for region in report["regions"]:
                members = ", ".join(f"`{m}`" for m in region["members"])
                lines += [
                    f"### Région `{region['id']}` — dispatch quarantiné unique",
                    "",
                    f"- Membres ({len(region['members'])}) : {members}",
                    "- Dispatch : un seul sous-agent à capsule minimale exécute "
                    "toute la région dans une fenêtre partagée quarantinée.",
                    "- Sortie : exclusivement via un contrat de digest "
                    "(`handoff-packet` / `context-pack`) — le contenu interne ne "
                    "fuit pas vers l'aval (R-C7).",
                    f"- Pression estimée : {region['estimatedTokens']} tokens "
                    f"({region['windowPct']} % de la fenêtre) — {region['verdict']}.",
                    "",
                ]
        if report["warnings"]:
            lines += ["## Avertissements du lint normatif", ""]
            lines += [f"- {w}" for w in report["warnings"]]
            lines.append("")
        content = "\n".join(lines)

        artifact_rel = f".github/prompts/{bp_id}.blueprint.prompt.md"
        artifact_path = self.project_root / artifact_rel
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        source["compiled"] = {
            "at": now,
            "catalogVersion": str(catalog_version),
            "artifacts": [
                {"path": artifact_rel, "hash": f"sha256:{digest}", "sourceNode": bp_id}
            ],
        }
        self._blueprint_path(bp_id).parent.mkdir(parents=True, exist_ok=True)
        self._blueprint_path(bp_id).write_text(
            json.dumps(source, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "compiled": bp_id,
            "artifact": artifact_rel,
            "hash": f"sha256:{digest}",
            "warnings": report["warnings"],
        }

    def _extension_node_patterns(self, ref: str) -> list[str]:
        ext_id = str(ref).split("/")[0]
        manifest_path = self.kit_root / "extensions" / ext_id / MANIFEST_NAME
        if not manifest_path.is_file():
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return cast(list[str], manifest.get("patterns", {}).get("implements", []))

    # ── canaux de features (beta / experimental) ──────────────────────────

    def features_view(self) -> list[dict[str, Any]]:
        from grimoire.tools import features as feat
        from grimoire.tools import stigmergy_hooks as sh

        entries = feat.list_features(self.project_root)
        for entry in entries:
            if entry["id"] == "stigmergy-hooks":
                entry["installed"] = sh.hooks_installed(self.project_root)
        return entries

    def feature_toggle(self, feature_id: str, enabled: bool) -> dict[str, Any]:
        from grimoire.tools import features as feat

        feat.set_enabled(self.project_root, feature_id, enabled)
        note = None
        if feature_id == "stigmergy-hooks":
            from grimoire.tools import stigmergy_hooks as sh

            if enabled:
                note = sh.install_hooks(self.project_root)
            else:
                removed, registry_note = sh.uninstall_hooks(self.project_root)
                note = f"{removed} fichier(s) retiré(s) · {registry_note}"
        return {**feat.feature_state(self.project_root, feature_id), "note": note}

    # ── télémétrie ────────────────────────────────────────────────────────

    def event_files(self) -> list[tuple[str, Path]]:
        return [
            (name, self.project_root / rel)
            for name, rel in EVENT_SOURCES
            if (self.project_root / rel).is_file()
        ]

    def events_log(self, limit: int = 200) -> dict[str, Any]:
        """Dernières lignes des flux events.jsonl, pour le replay blueprint."""
        log: dict[str, list[Any]] = {}
        for name, path in self.event_files():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            entries = []
            for line in lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    entries.append({"raw": line})
            log[name] = entries
        return log

    # ── coordination stigmergique (expérimental) ──────────────────────────

    def stigmergy_view(self) -> dict[str, Any]:
        """Vue live du tableau phéromonique du projet (signaux actifs + trails).

        Lit ``_grimoire-output/pheromone-board.json`` via le module SDK ;
        l'intensité est décroissante (calculée à la lecture). Rien n'est écrit.
        """
        from grimoire.tools import stigmergy as stig

        board = stig.load_board(self.project_root)
        active = stig.sense_pheromones(board)
        signals = [
            {
                "id": p.pheromone_id,
                "type": p.pheromone_type,
                "location": p.location,
                "text": p.text,
                "emitter": p.emitter,
                "intensity": round(inten, 4),
                "reinforcements": p.reinforcements,
            }
            for p, inten in active
        ]
        trails = [
            {
                "type": t.pattern_type,
                "location": t.location,
                "description": t.description,
                "agents": list(t.involved_agents),
            }
            for t in stig.analyze_trails(board)
        ]
        by_type: dict[str, int] = {}
        for p, _ in active:
            by_type[p.pheromone_type] = by_type.get(p.pheromone_type, 0) + 1

        # Métriques comportementales (base de la promotion beta→stable) :
        # le journal dit si le board coordonne réellement ou tourne à vide.
        events = stig.read_events(self.project_root)
        actions = [str(e.get("action", "")) for e in events]
        resolved = sum(1 for p in board.pheromones if p.resolved)
        reinforcements = sum(p.reinforcements for p in board.pheromones)
        relays = sum(1 for t in trails if t["type"] == "relay")
        useful = resolved + relays
        denominator = board.total_emitted or 1
        useful_ratio = round(useful / denominator, 3)
        # Seuil de promotion beta→stable (QUA-13) : la mesure sert une décision.
        target_ratio = STIGMERGY_TARGET_USEFUL_RATIO
        min_emitted = STIGMERGY_PROMOTION_MIN_EMITTED
        return {
            "active": signals,
            "trails": trails,
            "stats": {
                "active": len(signals),
                "emitted": board.total_emitted,
                "evaporated": board.total_evaporated,
                "halfLifeHours": board.half_life_hours,
                "byType": by_type,
            },
            "behavior": {
                "senseInjections": actions.count("sense-injected"),
                "autoEmits": sum(
                    1 for e in events
                    if e.get("source") == "hook" and e.get("action") in ("emit", "reinforce")
                ),
                "manualEmits": sum(
                    1 for e in events
                    if e.get("source") != "hook" and e.get("action") in ("emit", "reinforce")
                ),
                "resolved": resolved,
                "reinforcements": reinforcements,
                "relays": relays,
                "usefulRatio": useful_ratio,
                "targetUsefulRatio": target_ratio,
                "minEmitted": min_emitted,
                "hypothesis": STIGMERGY_PROMOTION_HYPOTHESIS,
                "promotionReady": bool(board.total_emitted >= min_emitted and useful_ratio >= target_ratio),
            },
        }


def make_handler(api: ForgeAPI) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # silencieux par défaut
            pass

        def _json(self, payload: Any, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message: str, code: int = 400) -> None:
            self._json({"error": message}, code)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return cast(dict[str, Any], json.loads(self.rfile.read(length).decode("utf-8")))

        def _guard_mutation(self) -> bool:
            """Anti CSRF / DNS-rebinding vers localhost (backend-permissif).

            Refuse toute mutation dont le Host n'est pas la loopback, ou dont
            l'Origin (si présent, cas navigateur) est cross-origin. Un outil
            local ne doit répondre qu'à sa propre UI, pas à une page tierce
            ouverte dans le navigateur de l'utilisateur.
            """
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if host not in ("127.0.0.1", "localhost", "::1", ""):
                self._error("hôte non autorisé", 403)
                return False
            origin = self.headers.get("Origin")
            if origin:
                from urllib.parse import urlparse

                oh = urlparse(origin).hostname or ""
                if oh.lower() not in ("127.0.0.1", "localhost", "::1"):
                    self._error("origine non autorisée", 403)
                    return False
            return True

        def _governed_event(self, action: str, **fields: Any) -> None:
            """Trace gouvernée d'une mutation serve (QUA-08), fail-open."""
            try:
                path = (
                    api.project_root / "_grimoire-runtime-output"
                    / "hook-runtime" / "serve-mutations.jsonl"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                entry = {
                    "ts": datetime.now(UTC).isoformat(),
                    "source": "serve", "action": action, **fields,
                }
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except OSError:
                pass

        # ── GET ───────────────────────────────────────────────────────────

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            try:
                if path == "/api/status":
                    self._json(api.status())
                elif path == "/api/setup":
                    self._json(api.setup_view())
                elif path == "/api/archetypes":
                    self._json(api.archetypes())
                elif path == "/api/extensions":
                    self._json(api.extensions_view())
                elif path == "/api/blueprints":
                    self._json(api.blueprints_list())
                elif path == "/api/events/log":
                    self._json(api.events_log())
                elif path == "/api/stigmergy":
                    self._json(api.stigmergy_view())
                elif path == "/api/features":
                    self._json(api.features_view())
                elif path == "/api/cost-model":
                    model = parse_qs(urlparse(self.path).query).get("model", [None])[0]
                    self._json(api.cost_model_view(model))
                elif path == "/api/primitives":
                    self._json(api.primitives_view())
                elif path == "/api/backends":
                    self._json(backend_catalogue())
                elif path == "/api/memory/status":
                    self._json(api.memory_link_view())
                elif path.startswith("/api/blueprints/"):
                    self._json(api.blueprint_get(path.rsplit("/", 1)[1]))
                elif path == "/api/events":
                    self._sse()
                else:
                    self._static(path)
            except FileNotFoundError as exc:
                self._error(f"introuvable : {exc}", 404)
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc))

        # ── POST / PUT / DELETE ───────────────────────────────────────────

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if not self._guard_mutation():
                return
            try:
                body = self._body()
                if path == "/api/extensions/add":
                    result = api.extension_add(str(body.get("source", "")))
                    self._governed_event("extension.add", id=result.extension_id,
                                         version=result.version)
                    self._json(
                        {
                            "installed": result.extension_id,
                            "version": result.version,
                            "copied": list(result.copied),
                            "skipped": list(result.skipped),
                        }
                    )
                elif path == "/api/extensions/remove":
                    api.extension_remove(str(body.get("id", "")))
                    self._governed_event("extension.remove", id=str(body.get("id", "")))
                    self._json({"removed": body.get("id")})
                elif path in ("/api/setup", "/api/setup/plan"):
                    self._json(api.setup_plan(body))
                elif path.startswith("/api/features/"):
                    feature_id = path.rsplit("/", 1)[1]
                    try:
                        enabled = bool(body.get("enabled"))
                        toggled = api.feature_toggle(feature_id, enabled)
                        self._governed_event("feature.toggle", id=feature_id, enabled=enabled)
                        self._json(toggled)
                    except KeyError:
                        self._error(f"feature inconnue : {feature_id}", 404)
                elif path.startswith("/api/blueprints/") and path.endswith("/validate"):
                    bp_id = path.split("/")[3]
                    blueprint = body or api.blueprint_get(bp_id)
                    self._json(api.blueprint_lint(blueprint))
                elif path.startswith("/api/blueprints/") and path.endswith("/simulate"):
                    bp_id = path.split("/")[3]
                    blueprint = body or api.blueprint_get(bp_id)
                    qs = parse_qs(urlparse(self.path).query)
                    inject = None
                    if qs.get("injectNode"):
                        inject = {
                            "nodeId": qs["injectNode"][0],
                            "class": qs.get("injectClass", ["unknown"])[0],
                        }
                    self._json(api.blueprint_simulate(blueprint, inject_failure=inject))
                elif path.startswith("/api/blueprints/") and path.endswith("/compile"):
                    bp_id = path.split("/")[3]
                    blueprint = body or api.blueprint_get(bp_id)
                    compiled = api.blueprint_compile(blueprint)
                    self._governed_event("blueprint.compile", id=bp_id,
                                         artifact=compiled.get("artifact"))
                    self._json(compiled)
                else:
                    self._error("route inconnue", 404)
            except ExtensionError as exc:
                self._error(str(exc), 422)
            except FileNotFoundError as exc:
                self._error(f"introuvable : {exc}", 404)
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc))

        def do_PUT(self) -> None:
            path = self.path.split("?")[0]
            if not self._guard_mutation():
                return
            try:
                if path.startswith("/api/blueprints/"):
                    bp_id = path.rsplit("/", 1)[1]
                    saved = api.blueprint_put(bp_id, self._body())
                    self._governed_event("blueprint.put", id=bp_id)
                    self._json(saved)
                else:
                    self._error("route inconnue", 404)
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(str(exc))

        # ── SSE ───────────────────────────────────────────────────────────

        def _sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            offsets = {name: p.stat().st_size for name, p in api.event_files()}
            try:
                while True:
                    for name, p in api.event_files():
                        size = p.stat().st_size
                        start = offsets.get(name, size)
                        if size > start:
                            with p.open("r", encoding="utf-8", errors="replace") as f:
                                f.seek(start)
                                for line in f:
                                    line = line.strip()
                                    if line:
                                        self.wfile.write(
                                            f"event: {name}\ndata: {line}\n\n".encode()
                                        )
                            offsets[name] = size
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    time.sleep(1)
            except (BrokenPipeError, ConnectionResetError):
                return

        # ── statique ──────────────────────────────────────────────────────

        def _static(self, path: str) -> None:
            if api.ui_dir is None:
                self._json({"grimoire": "serve", "hint": "API disponible sous /api/"}, 200)
                return
            rel = path.lstrip("/") or "index.html"
            target = (api.ui_dir / rel).resolve()
            # is_relative_to évite la confusion de préfixe (/a/web vs /a/web2).
            if not target.is_relative_to(api.ui_dir.resolve()):
                self._error("chemin refusé", 403)
                return
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                self._error("introuvable", 404)
                return
            content_types = {
                ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                ".json": "application/json", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon",
            }
            body = target.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_types.get(target.suffix, "application/octet-stream"),
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    project_root: Path, kit_root: Path, ui_dir: Path | None, port: int
) -> ThreadingHTTPServer:
    api = ForgeAPI(project_root, kit_root, ui_dir)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="grimoire serve", description="Mode local Forge")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--kit-root", type=Path, default=Path(__file__).parents[3].parent)
    parser.add_argument("--ui-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args(argv)

    ui_dir = args.ui_dir
    if ui_dir is None:
        candidate = args.kit_root.parent / "web" / "public"
        if candidate.is_dir():
            ui_dir = candidate
        else:
            # UI embarquée dans le paquet (wheel ou editable)
            from grimoire.data import web_path

            packaged = web_path()
            ui_dir = packaged if packaged.is_dir() else None

    server = serve(args.project_root, args.kit_root, ui_dir, args.port)
    print(f"grimoire serve — http://127.0.0.1:{args.port}/ (UI : {ui_dir or 'API seule'})")
    print("Ctrl+C pour arrêter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
