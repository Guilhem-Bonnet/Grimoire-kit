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
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from grimoire.tools.ext_manager import (
    MANIFEST_NAME,
    ExtensionError,
    InstallResult,
    install_extension,
    list_installed,
    load_manifest,
    remove_extension,
)

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
BLUEPRINTS_RELPATH = Path("_grimoire") / "blueprints"

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

    # ── wizard ────────────────────────────────────────────────────────────

    def archetypes(self) -> list[dict[str, Any]]:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        result: list[dict[str, Any]] = []
        base = self.kit_root / "archetypes"
        if not base.is_dir():
            return result
        for dna in sorted(base.glob("*/archetype.dna.yaml")):
            data = yaml.load(dna.read_text(encoding="utf-8")) or {}
            result.append(
                {
                    "id": data.get("id", dna.parent.name),
                    "name": data.get("name", dna.parent.name),
                    "description": data.get("description", ""),
                    "tags": data.get("tags", []),
                }
            )
        return result

    def setup_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        archetype = payload.get("archetype", "minimal")
        extensions = payload.get("extensions", [])
        installed, errors = [], []
        for ext_id in extensions:
            try:
                result = self.extension_add(ext_id)
                installed.append(f"{result.extension_id} v{result.version}")
            except ExtensionError as exc:
                errors.append(f"{ext_id} : {exc}")
        plan = {
            "plannedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "name": payload.get("name", ""),
            "user": payload.get("user", ""),
            "archetype": archetype,
            "extensionsInstalled": installed,
            "extensionErrors": errors,
            "initCommand": (
                f'bash {self.kit_root / "grimoire-init.sh"} '
                f'--name "{payload.get("name", "")}" --user "{payload.get("user", "")}" '
                f"--archetype {archetype}"
            ),
        }
        plan_path = self.project_root / "_grimoire" / "setup-plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return plan

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
        return errors

    def blueprint_lint(self, blueprint: dict[str, Any]) -> dict[str, Any]:
        """Lint normatif (H4) : erreurs bloquantes + avertissements catalogue.

        Avertissements dérivés du catalogue : dépendances de patterns
        absentes du flow, exigences des extensions, heuristiques
        anti-patterns (flow sans preuve, node isolé).
        """
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

        return {"errors": errors, "warnings": warnings}

    def _extension_node_patterns(self, ref: str) -> list[str]:
        ext_id = str(ref).split("/")[0]
        manifest_path = self.kit_root / "extensions" / ext_id / MANIFEST_NAME
        if not manifest_path.is_file():
            return []
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return cast(list[str], manifest.get("patterns", {}).get("implements", []))

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
            try:
                body = self._body()
                if path == "/api/extensions/add":
                    result = api.extension_add(str(body.get("source", "")))
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
                    self._json({"removed": body.get("id")})
                elif path == "/api/setup/plan":
                    self._json(api.setup_plan(body))
                elif path.startswith("/api/blueprints/") and path.endswith("/validate"):
                    bp_id = path.split("/")[3]
                    blueprint = body or api.blueprint_get(bp_id)
                    self._json(api.blueprint_lint(blueprint))
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
            try:
                if path.startswith("/api/blueprints/"):
                    bp_id = path.rsplit("/", 1)[1]
                    self._json(api.blueprint_put(bp_id, self._body()))
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
            if not str(target).startswith(str(api.ui_dir)):
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
        ui_dir = candidate if candidate.is_dir() else None

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
