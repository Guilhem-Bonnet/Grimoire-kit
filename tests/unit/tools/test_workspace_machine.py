"""Flotte › Données : la carte machine « qui partage quoi » (P26).

Docker, le réseau et l'environnement sont rejoués par des sondes factices
(:class:`MachineProbes`) : ces tests ne demandent ni Docker ni service mémoire,
et prouvent ce que la route rend dans chaque cas, en particulier que Docker
injoignable donne « inconnu », jamais « arrêté ».
"""

from __future__ import annotations

import json
import socket
import sys
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.tools import project_registry as reg
from grimoire.tools import workspace_machine as wmach
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX, workspace_get

SECRET = "s3cr3t-valeur-neo4j"  # noqa: S105 - valeur factice, prouve qu'elle ne sort jamais


@pytest.fixture(autouse=True)
def _cockpit_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))


def _project(root: Path, name: str, memory: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project-context.yaml").write_text(f"project:\n  name: {name}\nmemory:\n{memory}", encoding="utf-8")
    return root


FULL = (
    "  backend: weaviate-server\n  weaviate_url: http://localhost:8080\n  weaviate_collection: GrimoireMemory\n"
    "  neo4j_uri: bolt://localhost:7687\n  knowledge_graph: neo4j\n"
    "  short_term_backend: redis\n  redis_url: redis://localhost:6379/0\n"
)


def _ps(name: str, image: str, port: int | None, state: str = "running") -> dict[str, Any]:
    ports = f"0.0.0.0:{port}->{port}/tcp, [::]:{port}->{port}/tcp" if port and state == "running" else ""
    return {"Names": name, "Image": image, "Ports": ports, "State": state}


def _inspect(name: str, image: str, ports: list[int], *, status: str = "running", exit_code: int = 0,
             health: str | None = "healthy", workdir: str | None = None, restart: str = "unless-stopped") -> dict[str, Any]:
    labels = {}
    if workdir:
        labels = {
            "com.docker.compose.project": Path(workdir).name.lower(),
            "com.docker.compose.service": name.split("-")[-1],
            "com.docker.compose.project.working_dir": workdir,
        }
    state: dict[str, Any] = {"Status": status, "ExitCode": exit_code}
    if health:
        state["Health"] = {"Status": health}
    return {
        "Name": f"/{name}",
        "Config": {"Image": image, "Labels": labels, "Env": [f"NEO4J_AUTH=neo4j/{SECRET}", "PATH=/usr/bin"]},
        "State": state,
        "HostConfig": {
            "RestartPolicy": {"Name": restart, "MaximumRetryCount": 0},
            "PortBindings": {f"{p}/tcp": [{"HostIp": "", "HostPort": str(p)}] for p in ports},
        },
        "Mounts": [{"Type": "volume", "Name": f"{name}-data", "Source": "/var/lib/x", "Destination": "/data"}],
    }


class FakeDocker:
    """``docker ps`` / ``docker inspect`` rejoués ; toute autre commande est une faute."""

    def __init__(self, containers: list[tuple[dict[str, Any], dict[str, Any]]] | None = None, *,
                 down: tuple[int, str] | None = None) -> None:
        self.containers = containers or []
        self.down = down
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(argv)
        assert argv[:2] in (["docker", "ps"], ["docker", "inspect"]), argv
        if self.down:
            return self.down[0], "", self.down[1]
        if argv[1] == "ps":
            return 0, "\n".join(json.dumps(ps) for ps, _ in self.containers), ""
        wanted = set(argv[2:])
        return 0, json.dumps([i for _, i in self.containers if i["Name"].lstrip("/") in wanted]), ""


def _probes(docker: FakeDocker, *, tcp: dict[str, str] | None = None, installed: bool = True,
            env: dict[str, str] | None = None, which: str | None = None) -> wmach.MachineProbes:
    states = tcp or {}
    return wmach.MachineProbes(
        docker=docker,
        tcp=lambda kind, _url: states.get(kind, "arrete"),
        version=lambda kind, _scheme, _host, _port: ({"redis": "7.4.9"}.get(kind), None if kind == "redis" else "muet"),
        which=lambda _name: which,
        module_installed=lambda _modules: installed,
        env=env if env is not None else {"GRIMOIRE_NEO4J_PASSWORD": SECRET},
    )


def _by_kind(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["kind"]: s for s in payload["services"] if s["host"] == "localhost"}


# ── 1. La route data/machine ──────────────────────────────────────────────────


def test_la_route_rend_services_conteneurs_proprietaires_extras_et_partages(tmp_path: Path) -> None:
    forge = _project(tmp_path / "forge", "Forge", FULL)
    kit = _project(tmp_path / "kit", "Kit", FULL)
    house = _project(tmp_path / "house", "House", "  backend: qdrant-server\n  qdrant_url: http://localhost:6333\n")
    reg.save_registry([
        {"slug": "forge", "name": "Forge", "path": str(forge)},
        {"slug": "kit", "name": "Kit", "path": str(kit)},
        {"slug": "house", "name": "House", "path": str(house)},
    ])
    other = str(tmp_path / "brainstrom")
    docker = FakeDocker([
        (_ps("forge-weaviate", "semitechnologies/weaviate:1.35.19", 8080),
         _inspect("forge-weaviate", "semitechnologies/weaviate:1.35.19", [8080], workdir=str(forge))),
        (_ps("forge-neo4j", "neo4j:5.26.25", 7687), _inspect("forge-neo4j", "neo4j:5.26.25", [7474, 7687], workdir=str(forge))),
        (_ps("forge-qdrant", "qdrant/qdrant:v1.19.1", 6333),
         _inspect("forge-qdrant", "qdrant/qdrant:v1.19.1", [6333, 6334], workdir=str(forge), restart="always")),
        (_ps("brainstrom-redis-1", "redis:7.4-alpine", 6379),
         _inspect("brainstrom-redis-1", "redis:7.4-alpine", [6379], workdir=other, health=None)),
    ])
    tcp = dict.fromkeys(("weaviate", "qdrant", "neo4j", "redis"), "en_ligne")

    payload = wmach.machine_view(forge, probes=_probes(docker, tcp=tcp))
    services = _by_kind(payload)

    weaviate = services["weaviate"]
    assert weaviate["state"] == "en_ligne"
    assert weaviate["version"] == "1.35.19" and weaviate["versionSource"] == "image"
    assert weaviate["container"]["name"] == "forge-weaviate"
    assert weaviate["container"]["compose"]["workingDir"] == str(forge)
    assert weaviate["owner"] == {"slug": "forge", "name": "Forge"}
    assert weaviate["container"]["restartPolicy"] == "unless-stopped"
    assert weaviate["container"]["volumes"][0]["name"] == "forge-weaviate-data"
    assert {c["slug"] for c in weaviate["consumers"]} == {"forge", "kit"}
    assert services["qdrant"]["container"]["restartPolicy"] == "always"
    assert services["redis"]["version"] == "7.4.9" and services["redis"]["versionSource"] == "service"
    assert services["redis"]["state"] == "emprunte"
    assert "brainstrom" in services["redis"]["reason"]
    assert services["neo4j"]["console"]["url"] == "http://localhost:7474/browser/"

    assert {e["kind"]: e["installed"] for e in payload["extras"]} == dict.fromkeys(
        ("weaviate", "qdrant", "neo4j", "redis", "ollama"), True)
    assert payload["neo4jPassword"] == {"env": "GRIMOIRE_NEO4J_PASSWORD", "present": True}
    assert payload["docker"]["reachable"] is True

    shares = {(s["kind"], s["target"]): s for s in payload["shares"]}
    assert {p["slug"] for p in shares[("collection", "GrimoireMemory")]["projects"]} == {"forge", "kit"}
    assert {p["slug"] for p in shares[("base", "neo4j")]["projects"]} == {"forge", "kit"}
    redis_share = shares[("redis", "0")]
    assert redis_share["namespaces"] == ["forge", "kit"] and redis_share["collision"] is False
    assert redis_share in services["redis"]["shares"]
    house_row = next(p for p in payload["projects"] if p["slug"] == "house")
    assert house_row["uses"]["qdrant"]["url"] == "http://localhost:6333"

    # Aucun secret ne sort : ni la valeur du mot de passe, ni l'Env du conteneur.
    assert SECRET not in json.dumps(payload)
    # Lecture seule : ps et inspect, rien d'autre (FakeDocker refuse le reste).
    assert {argv[1] for argv in docker.calls} == {"ps", "inspect"}


def test_le_mot_de_passe_neo4j_absent_est_un_booleen_faux_et_rend_neo4j_incomplet(tmp_path: Path) -> None:
    forge = _project(tmp_path / "forge", "Forge", FULL)
    docker = FakeDocker([(_ps("forge-neo4j", "neo4j:5", 7687), _inspect("forge-neo4j", "neo4j:5", [7687], workdir=str(forge)))])
    payload = wmach.machine_view(forge, probes=_probes(docker, tcp={"neo4j": "en_ligne"}, env={}))
    assert payload["neo4jPassword"] == {"env": "GRIMOIRE_NEO4J_PASSWORD", "present": False}
    neo4j = _by_kind(payload)["neo4j"]
    assert neo4j["state"] == "incomplet"
    assert neo4j["command"]["text"] == "export GRIMOIRE_NEO4J_PASSWORD=<mot de passe>"


def test_la_route_est_une_lecture_de_la_table_jamais_une_ecriture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    route = f"{PREFIX}data/machine"
    assert route in GET_ROUTES
    assert not any("data/" in r for r in POST_ROUTES)
    seen: list[Path] = []
    monkeypatch.setattr(wmach, "machine_view", lambda root: seen.append(root) or {"ok": True})
    assert workspace_get(tmp_path, route, {}) == {"ok": True}
    assert seen == [tmp_path]


# ── 2. Les huit états, distincts ──────────────────────────────────────────────


def _one(tmp_path: Path, kind: str, tcp: str, containers: list[Any] | None = None, *, down: tuple[int, str] | None = None,
         installed: bool = True, memory: str = FULL, which: str | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    forge = _project(tmp_path / "forge", "Forge", memory)
    payload = wmach.machine_view(forge, probes=_probes(
        FakeDocker(containers, down=down), tcp={kind: tcp}, installed=installed, which=which, env=env))
    return _by_kind(payload)[kind]


def test_en_ligne(tmp_path: Path) -> None:
    forge = str(tmp_path / "forge")
    s = _one(tmp_path, "weaviate", "en_ligne", [(_ps("w", "semitechnologies/weaviate:1", 8080),
                                                  _inspect("w", "semitechnologies/weaviate:1", [8080], workdir=forge))])
    assert (s["state"], s["stateLabel"]) == ("en_ligne", "En ligne")
    assert s["command"]["text"] == "curl -s http://localhost:8080/v1/meta"


def test_demarrage_par_le_healthcheck_ou_par_le_port_qui_ne_repond_pas_encore(tmp_path: Path) -> None:
    forge = str(tmp_path / "forge")
    starting = [(_ps("n", "neo4j:5", 7687), _inspect("n", "neo4j:5", [7687], health="starting", workdir=forge))]
    s = _one(tmp_path, "neo4j", "en_ligne", starting)
    assert (s["state"], s["stateLabel"]) == ("demarrage", "Démarrage")
    assert s["command"]["text"] == "docker logs -f n"
    s = _one(tmp_path, "neo4j", "arrete", starting)
    assert s["state"] == "demarrage"


def test_incomplet_quand_l_extra_python_manque(tmp_path: Path) -> None:
    forge = str(tmp_path / "forge")
    s = _one(tmp_path, "redis", "en_ligne", [(_ps("r", "redis:7", 6379), _inspect("r", "redis:7", [6379], workdir=forge))],
             installed=False)
    assert (s["state"], s["stateLabel"]) == ("incomplet", "Incomplet")
    assert "redis" in s["command"]["text"]


def test_emprunte(tmp_path: Path) -> None:
    s = _one(tmp_path, "redis", "en_ligne", [(_ps("x-redis-1", "redis:7", 6379),
                                              _inspect("x-redis-1", "redis:7", [6379], workdir=str(tmp_path / "x")))])
    assert (s["state"], s["stateLabel"]) == ("emprunte", "Emprunté")
    assert s["owner"] == {"slug": None, "name": "x"}
    assert "com.docker.compose.project.working_dir" in s["command"]["text"]


def test_en_echec_reseau_healthcheck_malade_ou_sortie_en_erreur(tmp_path: Path) -> None:
    assert _one(tmp_path, "qdrant", "en_echec")["stateLabel"] == "En échec"
    sick = [(_ps("q", "qdrant/qdrant:v1", 6333), _inspect("q", "qdrant/qdrant:v1", [6333], health="unhealthy"))]
    assert _one(tmp_path, "qdrant", "en_ligne", sick)["state"] == "en_echec"
    crashed = [(_ps("q", "qdrant/qdrant:v1", None, "exited"),
                _inspect("q", "qdrant/qdrant:v1", [6333], status="exited", exit_code=1, health=None))]
    s = _one(tmp_path, "qdrant", "arrete", crashed)
    assert s["state"] == "en_echec" and "code de sortie 1" in s["reason"]
    assert s["command"]["text"] == "docker logs --tail 50 q"


def test_arrete_quand_le_conteneur_existe_mais_est_arrete(tmp_path: Path) -> None:
    stopped = [(_ps("q", "qdrant/qdrant:v1", None, "exited"),
                _inspect("q", "qdrant/qdrant:v1", [6333], status="exited", exit_code=0, health=None))]
    s = _one(tmp_path, "qdrant", "arrete", stopped)
    assert (s["state"], s["stateLabel"]) == ("arrete", "Arrêté")
    assert s["command"] == {"label": "Démarrer le conteneur existant", "text": "docker start q"}


def test_un_conteneur_arrete_sur_un_autre_port_n_est_pas_rattache(tmp_path: Path) -> None:
    elsewhere = [(_ps("q2", "qdrant/qdrant:v1", None, "exited"),
                  _inspect("q2", "qdrant/qdrant:v1", [7333], status="exited", health=None))]
    s = _one(tmp_path, "qdrant", "arrete", elsewhere)
    assert s["container"] is None and s["state"] == "absent"


def test_absent_quand_docker_repond_et_qu_aucun_conteneur_n_existe(tmp_path: Path) -> None:
    s = _one(tmp_path, "weaviate", "arrete", [])
    assert (s["state"], s["stateLabel"]) == ("absent", "Absent")
    assert s["command"]["text"] == "docker compose -f docker-compose.memory-target.yml up -d"


@pytest.mark.parametrize("down", [(127, "commande docker introuvable sur ce poste"),
                                  (1, "Cannot connect to the Docker daemon"),
                                  (124, "Docker n'a pas répondu en 3 s")])
@pytest.mark.parametrize("kind", ["weaviate", "qdrant", "neo4j", "redis", "ollama"])
def test_docker_injoignable_donne_inconnu_jamais_arrete(tmp_path: Path, kind: str, down: tuple[int, str]) -> None:
    s = _one(tmp_path, kind, "arrete", down=down)
    assert (s["state"], s["stateLabel"]) == ("inconnu", "Inconnu")
    assert down[1] in s["reason"]
    assert s["command"]["text"] == "docker info"


def test_docker_injoignable_rend_son_motif_et_la_carte_le_dit(tmp_path: Path) -> None:
    forge = _project(tmp_path / "forge", "Forge", FULL)
    payload = wmach.machine_view(forge, probes=_probes(FakeDocker(down=(1, "Cannot connect to the Docker daemon"))))
    assert payload["docker"]["reachable"] is False
    assert "Cannot connect" in payload["docker"]["reason"]
    assert payload["summary"]["byState"]["arrete"] == 0
    assert payload["summary"]["byState"]["absent"] == 0


def test_un_service_qui_repond_reste_en_ligne_meme_sans_docker(tmp_path: Path) -> None:
    s = _one(tmp_path, "weaviate", "en_ligne", down=(1, "daemon"))
    assert s["state"] == "en_ligne" and "non lus" in s["reason"]


def test_une_sonde_expiree_est_inconnue(tmp_path: Path) -> None:
    s = _one(tmp_path, "neo4j", "inconnu")
    assert s["state"] == "inconnu" and "0.5" in s["reason"]


def test_ollama_installe_mais_eteint_est_arrete(tmp_path: Path) -> None:
    assert _one(tmp_path, "ollama", "arrete", which="/usr/bin/ollama")["state"] == "arrete"


def test_un_hote_distant_qui_refuse_est_inconnu(tmp_path: Path) -> None:
    memory = "  backend: qdrant-server\n  qdrant_url: http://qdrant.lan:6333\n"
    forge = _project(tmp_path / "forge", "Forge", memory)
    payload = wmach.machine_view(forge, probes=_probes(FakeDocker([])))
    remote = next(s for s in payload["services"] if s["host"] == "qdrant.lan")
    assert remote["state"] == "inconnu"


def test_chaque_etat_a_un_libelle_distinct() -> None:
    ids = [s for s, _ in wmach.STATES]
    labels = [label for _, label in wmach.STATES]
    assert ids == ["en_ligne", "demarrage", "incomplet", "emprunte", "en_echec", "arrete", "absent", "inconnu"]
    assert len(set(labels)) == 8


def test_chaque_service_porte_une_console_et_une_commande(tmp_path: Path) -> None:
    forge = _project(tmp_path / "forge", "Forge", FULL)
    payload = wmach.machine_view(forge, probes=_probes(FakeDocker([])))
    for service in payload["services"]:
        assert service["console"]["url"].split(":", 1)[0] in ("http", "https", "redis")
        assert service["command"]["text"]


def test_projets_sans_config_ou_config_cassee_nomment_leur_raison(tmp_path: Path) -> None:
    forge = _project(tmp_path / "forge", "Forge", "  backend: qdrant-local\n")
    broken = _project(tmp_path / "cassé", "Cassé", "  backend: inexistant\n")
    bare = tmp_path / "nu"
    bare.mkdir()
    reg.save_registry([
        {"slug": "casse", "name": "", "path": str(broken)},
        {"slug": "nu", "name": "Nu", "path": str(bare)},
        {"slug": "parti", "name": "Parti", "path": str(tmp_path / "parti")},
        {"slug": "vide", "name": "", "path": ""},
    ])
    payload = wmach.machine_view(forge, probes=_probes(FakeDocker([])))
    rows = {p["slug"] or "servi": p for p in payload["projects"]}
    assert rows["servi"]["uses"] == {"embarque": {"collection": "grimoire"}}
    assert rows["casse"]["reason"].startswith("config illisible")
    assert rows["nu"]["reason"].startswith("projet non initialisé")
    assert rows["parti"]["reason"] == "dossier introuvable"
    assert "vide" not in rows


def test_inspect_en_echec_est_nomme(tmp_path: Path) -> None:
    class BrokenInspect(FakeDocker):
        def __call__(self, argv: list[str]) -> tuple[int, str, str]:
            if argv[1] == "inspect":
                return 1, "", "permission denied"
            return super().__call__(argv)

    forge = _project(tmp_path / "forge", "Forge", FULL)
    docker = BrokenInspect([(_ps("r", "redis:7", 6379), _inspect("r", "redis:7", [6379]))])
    payload = wmach.machine_view(forge, probes=_probes(docker, tcp={"redis": "en_ligne"}))
    assert "permission denied" in payload["docker"]["reason"]


# ── 3. Les sondes par défaut, sans Docker ni service ─────────────────────────


def test_la_sonde_docker_par_defaut_ne_leve_jamais(monkeypatch: pytest.MonkeyPatch) -> None:
    probes = wmach.MachineProbes()

    def missing(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", missing)
    assert probes.docker(["docker", "ps"])[0] == 127

    def slow(*_a: Any, **_k: Any) -> Any:
        raise subprocess.TimeoutExpired("docker", 3)

    monkeypatch.setattr(subprocess, "run", slow)
    code, _, err = probes.docker(["docker", "ps"])
    assert code == 124 and "3 s" in err

    def denied(*_a: Any, **_k: Any) -> Any:
        raise PermissionError("refusé")

    monkeypatch.setattr(subprocess, "run", denied)
    assert probes.docker(["docker", "ps"])[0] == 126
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, "ok", ""))
    assert probes.docker(["docker", "ps"]) == (0, "ok", "")


def _serve_once(reply: bytes) -> int:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer() -> None:
        conn, _ = server.accept()
        with conn:
            conn.recv(1024)
            conn.sendall(reply)
        server.close()

    threading.Thread(target=answer, daemon=True).start()
    return int(server.getsockname()[1])


def test_la_version_redis_se_lit_dans_info_server() -> None:
    version = wmach.MachineProbes().version
    port = _serve_once(b"$40\r\n# Server\r\nredis_version:7.4.9\r\nredis_mode:standalone\r\n")
    assert version("redis", "redis", "127.0.0.1", port) == ("7.4.9", None)
    port = _serve_once(b"-NOAUTH Authentication required.\r\n")
    assert "authentification" in version("redis", "redis", "127.0.0.1", port)[1]
    port = _serve_once(b"+OK\r\n")
    assert "inattendue" in version("redis", "redis", "127.0.0.1", port)[1]


def test_la_version_http_et_son_motif_d_absence() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"version": "1.19.1"} if self.path == "/" else ["pas", "un", "objet"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a: Any) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = int(httpd.server_address[1])
        version = wmach.MachineProbes().version
        assert version("qdrant", "http", "127.0.0.1", port) == ("1.19.1", None)
        missing, reason = version("ollama", "http", "127.0.0.1", port)
        assert missing is None and "/api/version" in reason
        assert version("weaviate", "ftp", "127.0.0.1", port)[0] is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_la_sonde_tcp_par_defaut_dit_arrete_quand_rien_n_ecoute() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    state = wmach.MachineProbes().tcp("redis", f"redis://127.0.0.1:{port}")
    # Windows ne refuse pas aussitôt un port local fermé : il réessaie ~2 s, au-delà du
    # délai de la sonde, qui dit alors « inconnu » (un délai dépassé ne prouve pas
    # l'arrêt). Ailleurs, le refus immédiat dit « arrêté ». Jamais « en ligne ».
    expected = {"arrete", "inconnu"} if sys.platform == "win32" else {"arrete"}
    assert state in expected
