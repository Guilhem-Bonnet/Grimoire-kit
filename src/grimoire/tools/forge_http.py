"""Surface HTTP de l'atelier local — le transport, pas le métier.

Séparé de :mod:`grimoire.tools.forge_server`, qui garde :class:`ForgeAPI` : le
premier décide *quoi* répondre, celui-ci *comment*. La coupe suit une frontière
réelle — gardes d'hôte et anti-CSRF, table de routage, service de fichiers,
flux SSE — et non un simple découpage à la ligne près pour tenir sous un seuil.

Tout ce qui est ici se teste par une requête ; tout ce qui est resté se teste
par un appel de méthode.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

from grimoire.tools.ext_manager import ExtensionError
from grimoire.tools.forge_routes import API_GET_UNHANDLED, api_get
from grimoire.tools.project_registry import DEFAULT_SCAN_DEPTH, looks_grimoire, register_project
from grimoire.tools.workspace_legacy import legacy_redirect_target
from grimoire.tools.workspace_routes import PREFIX as WORKSPACE_PREFIX
from grimoire.tools.workspace_routes import WORKSPACE_UNHANDLED, workspace_post

if TYPE_CHECKING:  # pragma: no cover - uniquement pour le typage
    from grimoire.tools.forge_server import ForgeAPI

__all__ = ["make_handler", "serve"]


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

        def _redirect(self, location: str) -> None:
            """302 vers ``location`` — le basculement (ADR-006, pas 2) : une page
            héritée envoie ici plutôt que refuser ou dupliquer son contenu."""
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return cast(dict[str, Any], json.loads(self.rfile.read(length).decode("utf-8")))

        def _guard_host(self) -> bool:
            """Anti DNS-rebinding — s'applique à TOUTE requête, lectures comprises.

            Le rebinding fait résoudre un domaine attaquant vers 127.0.0.1 :
            depuis le navigateur la page devient same-origin, donc CORS ne
            protège plus la *lecture* des réponses. Un `GET` qui liste des
            dossiers ou nomme la racine du projet devient alors un oracle sur
            la machine. Le seul Host légitime d'un outil lié à la loopback est
            la loopback ; tout le reste est refusé, quelle que soit la méthode.
            """
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            if host not in ("127.0.0.1", "localhost", "::1", ""):
                self._error("hôte non autorisé", 403)
                return False
            return True

        def _guard_mutation(self) -> bool:
            """Anti CSRF, en plus du garde d'hôte (backend-permissif).

            Refuse toute mutation dont l'Origin (si présent, cas navigateur) est
            cross-origin. Un outil local ne doit répondre qu'à sa propre UI, pas
            à une page tierce ouverte dans le navigateur de l'utilisateur.
            """
            if not self._guard_host():
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
            if not self._guard_host():
                return
            try:
                if path == "/api/events":
                    self._sse()
                    return
                query = parse_qs(urlparse(self.path).query)
                payload = api_get(api, path, query)
                if payload is not API_GET_UNHANDLED:
                    self._json(payload)
                elif path == "/api/projects":
                    self._json(api.projects_view())
                elif path == "/api/fs/browse":
                    self._json(api.browse_view(query.get("path", [None])[0]))
                elif path == "/api/data/status":
                    self._json(api.data_status())
                elif path.startswith("/api/blueprints/"):
                    # Surface atelier : le cockpit multi-projet n'y touche pas.
                    if path.endswith("/diff"):
                        self._json(api.blueprint_diff(path.split("/")[3]))
                    else:
                        self._json(api.blueprint_get(path.rsplit("/", 1)[1]))
                else:
                    self._static(path, query)
            except FileNotFoundError as exc:
                self._error(f"introuvable : {exc}", 404)
            except PermissionError as exc:
                self._error(str(exc), 403)
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
                elif path == "/api/projects/select":
                    # Pas de trace gouvernée ici : ouvrir un projet n'est pas le
                    # modifier. Journaliser dans son arbre salirait le
                    # `git status` de tout dépôt qu'on se contente de regarder.
                    # L'enrôlement est déjà consigné, au registre de la machine.
                    self._json(api.select_project(
                        slug=str(body.get("slug", "")), path=str(body.get("path", ""))
                    ))
                elif path == "/api/projects/add":
                    self._json(api.project_add(str(body.get("path", ""))))
                elif path == "/api/projects/scan":
                    self._json(api.project_scan(
                        str(body.get("root", "")),
                        int(body.get("depth", DEFAULT_SCAN_DEPTH)),
                    ))
                elif path == "/api/data/refresh":
                    self._json(api.data_refresh())
                elif path == "/api/projects/update":
                    # Écriture réelle dans le dépôt : l'aperçu est le défaut, et
                    # l'alignement effectif demande un accord explicite. Une UI
                    # qui peut réécrire un projet sur un clic mal placé n'est
                    # pas un cockpit, c'est un piège.
                    dry_run = body.get("confirm") is not True
                    report = api.project_update(
                        dry_run=dry_run,
                        slug=str(body.get("project", "") or body.get("slug", "")),
                        path=str(body.get("path", "")),
                    )
                    if not dry_run:
                        self._governed_event("project.update", path=report["path"],
                                             ok=report["ok"])
                    self._json(report)
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
                elif path.startswith(WORKSPACE_PREFIX):
                    # Écritures de la vue de travail — hôte mono-projet
                    # seulement. Le cockpit ne câble pas cette branche : il se
                    # déclare `readOnly` et n'a pas de raison de réclamer une
                    # tâche ou de créer un override dans un dépôt qu'il ne sert
                    # pas. Le refus y est donc un 404, pas un oubli.
                    result = workspace_post(api.project_root, path, body)
                    if result is WORKSPACE_UNHANDLED:
                        self._error("route inconnue", 404)
                    else:
                        self._governed_event(
                            "workspace.post", route=path[len(WORKSPACE_PREFIX):]
                        )
                        self._json(result)
                else:
                    self._error("route inconnue", 404)
            except ExtensionError as exc:
                self._error(str(exc), 422)
            except FileNotFoundError as exc:
                self._error(f"introuvable : {exc}", 404)
            except PermissionError as exc:
                self._error(str(exc), 403)
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

        def _static(self, path: str, query: dict[str, list[str]] | None = None) -> None:
            if api.ui_dir is None:
                self._json({"grimoire": "serve", "hint": "API disponible sous /api/"}, 200)
                return
            rel = path.lstrip("/")
            if rel.startswith("data/"):
                self._data_file(rel[len("data/"):])
                return
            # Basculement, pas 2 (ADR-006) : une page héritée redirige vers
            # l'espace qui la remplace, sauf `?legacy=1` — la sortie de
            # secours explicite tant que le pas 3 (suppression) n'est pas
            # décidé.
            space = legacy_redirect_target(rel)
            if space is not None and not (query or {}).get("legacy"):
                self._redirect(f"/workspace/index.html#{space}")
                return
            target = (api.ui_dir / (rel or "index.html")).resolve()
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

        def _data_file(self, rel: str) -> None:
            """Sert ``data/<rel>`` — couche générée pour les données projet.

            Les instantanés de la vitrine embarqués dans la wheel décrivent
            d'autres projets (« Atlas Ops », un store à 141 entrées). Les
            servir ici afficherait les chiffres de quelqu'un d'autre sous le
            nom du projet ouvert : la couche projet ne peut venir que d'une
            génération faite sur ce projet, et son absence est un 404 honnête.
            """
            target = api.data_file(rel)
            if target is None:
                self._json(
                    {
                        "error": "couche de données absente pour ce projet",
                        "hint": "POST /api/data/refresh régénère la couche du projet servi",
                        "data": api.data_status(),
                    },
                    404,
                )
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    project_root: Path, kit_root: Path, ui_dir: Path | None, port: int
) -> ThreadingHTTPServer:
    from grimoire.tools.forge_server import ForgeAPI

    api = ForgeAPI(project_root, kit_root, ui_dir)
    # Ouvrir un projet, c'est le connaître : il entre au registre de la machine
    # et devient sélectionnable depuis n'importe quel atelier. Un dossier sans
    # le moindre marqueur (ni dépôt, ni trace Grimoire) n'y entre pas — le
    # registre décrit des projets, pas des répertoires de passage.
    if looks_grimoire(api.project_root):
        register_project(api.project_root)
    # La couche de données du projet se génère en fond : le serveur répond tout
    # de suite, et les pages runtime se remplissent quand elle est prête.
    api.data.refresh()
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
