# ruff: noqa: F811
"""Flotte › Données (P26) : la machine et qui partage quoi, dans un vrai navigateur.

Le cockpit est celui d'« À traiter » (``test_workspace_a_traiter_p32``). Deux
façons de lire la carte :

- la vraie route ``data/machine``, sur ce que la machine a (en CI : aucun
  service, Docker vide ou absent) — on n'y prouve que la forme : chaque
  service a sa console native et son « Copier la commande », aucun bouton ne
  démarre quoi que ce soit, aucune requête autre que GET ne part, et la vue
  n'est jamais lue au premier rendu de la Flotte ;
- une réponse factice interceptée (sondes injectées côté navigateur) qui
  porte les huit états : chacun se lit avec son mot, et Docker injoignable se
  dit en clair.

Avec ``GRIMOIRE_E2E_CAPTURES=<dossier>``, le dernier test écrit les captures
1440x900 en clair et en sombre ; sans la variable il ne fait rien (il ne se
skippe pas : le mode strict de la CI promeut tout skip en échec).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Request

from .test_workspace_a_traiter_p32 import served_inbox  # noqa: F401  (fixture de session partagée)

ALLOWED_BUTTONS = {"Copier la commande", "Relire la machine", "Projets", "Données"}
START_WORDS = re.compile(r"d[ée]marr|lancer|start|up -d|installer", re.IGNORECASE)


@pytest.fixture
def context_page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="light", reduced_motion="reduce",
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


def _open_fleet(page: Page, served: dict[str, str], hash_: str = "#flotte") -> None:
    page.goto(f"{served['url']}/workspace/index.html?scope=all{hash_}", wait_until="domcontentloaded")
    page.wait_for_selector("body[data-ready='1']", timeout=30_000)
    page.wait_for_function("() => window.GrimoireWorkspace.space === 'flotte'")


def _service(sid: str, kind: str, state: str, label: str, reason: str, **extra: Any) -> dict[str, Any]:
    host, port = sid.split("@")[1].split(":")
    service = {
        "id": sid, "kind": kind, "label": kind.capitalize() if kind != "neo4j" else "Neo4j",
        "role": {"redis": "mémoire chaude", "neo4j": "graphe", "ollama": "embeddings"}.get(kind, "store sémantique"),
        "url": f"http://{host}:{port}", "scheme": "http", "host": host, "port": int(port),
        "extra": {"name": kind, "modules": [kind], "installed": True},
        "container": None, "owner": None, "consumers": [], "shares": [],
        "version": None, "versionSource": None, "versionReason": None,
        "state": state, "stateLabel": label, "reason": reason,
        "console": {"label": f"Console {kind}", "url": f"http://{host}:{port}/"},
        "command": {"label": "Commande", "text": f"docker start {kind}-{state}"},
    }
    service.update(extra)
    return service


def _container(name: str, image: str, *, project: str | None, workdir: str | None, status: str = "running",
               restart: str = "unless-stopped") -> dict[str, Any]:
    return {
        "name": name, "image": image, "status": status, "exitCode": 0, "health": None, "restartPolicy": restart,
        "compose": {"project": project, "service": name.split("-")[-1], "workingDir": workdir} if project else None,
        "volumes": [{"type": "volume", "name": f"{name}-data", "destination": "/data"}], "ports": [],
    }


STATES = [
    ("en_ligne", "En ligne"), ("demarrage", "Démarrage"), ("incomplet", "Incomplet"), ("emprunte", "Emprunté"),
    ("en_echec", "En échec"), ("arrete", "Arrêté"), ("absent", "Absent"), ("inconnu", "Inconnu"),
]

FORGE = {"slug": "grimoire-forge", "name": "Grimoire-Forge"}
KIT = {"slug": "grimoire-kit", "name": "grimoire-kit"}
REF = {"slug": "reference", "name": "Référence-Agentique"}
HOUSE = {"slug": "houseserver", "name": "Terraform-HouseServer"}


def _payload(*, docker_up: bool = True) -> dict[str, Any]:
    """Une machine proche de celle de la maquette, où chaque état apparaît une fois."""
    collection_share = {"kind": "collection", "service": "weaviate@localhost:8080", "target": "GrimoireMemory",
                        "projects": [FORGE, KIT, REF]}
    base_share = {"kind": "base", "service": "neo4j@localhost:7687", "target": "neo4j", "projects": [FORGE, KIT, REF]}
    services = [
        _service("weaviate@localhost:8080", "weaviate", "en_ligne", "En ligne", "le service répond", version="1.35.19",
                 container=_container("grimoire-forge-weaviate", "semitechnologies/weaviate:1.35.19",
                                      project="grimoire-forge", workdir="/srv/Grimoire-Forge"),
                 owner=FORGE, consumers=[FORGE, KIT, REF], shares=[collection_share]),
        _service("neo4j@localhost:7687", "neo4j", "incomplet", "Incomplet",
                 "le service répond, mais GRIMOIRE_NEO4J_PASSWORD absente de l'environnement du cockpit",
                 version="5.26.25", container=_container("grimoire-forge-neo4j", "neo4j:5.26.25",
                                                         project="grimoire-forge", workdir="/srv/Grimoire-Forge"),
                 owner=FORGE, consumers=[FORGE, KIT, REF], shares=[base_share],
                 command={"label": "Exporter le mot de passe Neo4j", "text": "export GRIMOIRE_NEO4J_PASSWORD=<mot de passe>"}),
        _service("qdrant@localhost:6333", "qdrant", "demarrage", "Démarrage",
                 "le conteneur démarre : son healthcheck n'a pas encore conclu", version="1.19.1",
                 container=_container("grimoire-forge-qdrant", "qdrant/qdrant:v1.19.1", project="grimoire-forge",
                                      workdir="/srv/Grimoire-Forge", restart="always"),
                 owner=FORGE, consumers=[HOUSE],
                 command={"label": "Suivre le démarrage", "text": "docker logs -f grimoire-forge-qdrant"}),
        _service("redis@localhost:6379", "redis", "emprunte", "Emprunté",
                 "utilisé par Grimoire-Forge, mais le conteneur appartient au Compose « brainstrom-conceptjeuvivant »",
                 version="7.4.9", container=_container("brainstrom-conceptjeuvivant-redis-1", "redis:7.4-alpine",
                                                       project="brainstrom-conceptjeuvivant",
                                                       workdir="/srv/Brainstrom-ConceptJeuVivant"),
                 owner={"slug": None, "name": "brainstrom-conceptjeuvivant"}, consumers=[FORGE]),
        _service("ollama@localhost:11434", "ollama", "arrete", "Arrêté", "ollama est installé sur ce poste mais ne tourne pas",
                 command={"label": "Démarrer le service", "text": "ollama serve"}),
        _service("weaviate@nas.lan:8080", "weaviate", "en_echec", "En échec",
                 "connexion impossible à nas.lan:8080 (erreur réseau)"),
        _service("qdrant@localhost:7333", "qdrant", "absent", "Absent",
                 "aucun service n'écoute et aucun conteneur de cette image n'existe",
                 command={"label": "Démarrer le service", "text": "docker compose -f docker-compose.memory.yml up -d"}),
        _service("redis@localhost:6380", "redis", "inconnu", "Inconnu",
                 "rien n'écoute, et docker injoignable : impossible de dire si un conteneur existe",
                 command={"label": "Vérifier Docker", "text": "docker info"}),
    ]
    projects = [
        {**FORGE, "path": "/srv/Grimoire-Forge", "backend": "weaviate-server", "reason": None, "uses": {
            "weaviate": {"collection": "GrimoireMemory"}, "neo4j": {"database": "neo4j"},
            "redis": {"db": 0, "namespace": "grimoire_forge"}}},
        {**KIT, "path": "/srv/grimoire-kit", "backend": "weaviate-server", "reason": None, "uses": {
            "weaviate": {"collection": "GrimoireMemory"}, "neo4j": {"database": "neo4j"}}},
        {**REF, "path": "/srv/reference", "backend": "weaviate-server", "reason": None, "uses": {
            "weaviate": {"collection": "GrimoireMemory"}, "neo4j": {"database": "neo4j"}}},
        {**HOUSE, "path": "/srv/houseserver", "backend": "qdrant-server", "reason": None, "uses": {
            "qdrant": {"collection": "grimoire_houseserver"}}},
        {"slug": "creation-meubles", "name": "Creation_Meubles", "path": "/srv/meubles", "backend": "qdrant-local",
         "reason": None, "uses": {"embarque": {"collection": "creation_meubles"}}},
        {"slug": "casse", "name": "Banc cassé", "path": "/srv/casse", "backend": None,
         "reason": "config illisible : Invalid memory backend", "uses": {}},
    ]
    counts = {sid: 0 for sid, _ in STATES}
    for service in services:
        counts[service["state"]] += 1
    return {
        "schemaVersion": "grimoire-data-machine/v1",
        "docker": {"reachable": docker_up, "reason": None if docker_up else "Docker injoignable : Cannot connect to the Docker daemon",
                   "containers": 4},
        "neo4jPassword": {"env": "GRIMOIRE_NEO4J_PASSWORD", "present": False},
        "neo4jPasswordEnvs": [{"env": "GRIMOIRE_NEO4J_PASSWORD", "present": False, "projects": ["Grimoire-Forge"]}],
        "extras": [{"kind": k, "extra": k, "modules": [k], "installed": k != "ollama"}
                   for k in ("weaviate", "qdrant", "neo4j", "redis", "ollama")],
        "services": services, "projects": projects, "shares": [collection_share, base_share],
        "states": [{"id": sid, "label": label} for sid, label in STATES],
        "summary": {"services": len(services), "byState": counts, "shares": 2},
    }


def _fake(page: Page, payload: dict[str, Any]) -> None:
    page.route("**/api/workspace/data/machine*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload)))


def _open_donnees(page: Page) -> None:
    page.locator(".pl-viewseg [data-view='donnees']").click()
    page.wait_for_selector(".dn-svc", timeout=30_000)


def test_la_flotte_ne_lit_pas_la_machine_au_premier_rendu(context_page: Page, served_inbox: dict[str, str]) -> None:
    asked: list[str] = []
    context_page.on("request", lambda r: asked.append(r.url))
    _open_fleet(context_page, served_inbox)
    context_page.wait_for_selector(".pl-fleet .tbl tbody tr", timeout=30_000)
    seg = context_page.locator(".pl-viewseg")
    assert seg.locator("[aria-pressed='true']").inner_text() == "Projets"
    assert not [u for u in asked if "data/machine" in u]


def test_chaque_service_a_sa_console_et_copier_la_commande_et_rien_ne_demarre(
    context_page: Page, served_inbox: dict[str, str],
) -> None:
    writes: list[Request] = []
    context_page.on("request", lambda r: writes.append(r) if r.method != "GET" else None)
    _open_fleet(context_page, served_inbox)
    context_page.wait_for_selector(".pl-fleet .tbl tbody tr", timeout=30_000)
    _open_donnees(context_page)
    assert "vue=donnees" in context_page.url

    cards = context_page.locator(".dn-svc")
    assert cards.count() >= 5  # les cinq services connus, au moins
    for i in range(cards.count()):
        card = cards.nth(i)
        link = card.locator("a.dn-console")
        assert link.count() == 1
        assert re.match(r"^(https?|redis)://", link.get_attribute("href") or "")
        assert link.get_attribute("target") == "_blank"
        assert card.get_by_role("button", name="Copier la commande").count() == 1
        assert card.locator("[data-state-badge]").inner_text().strip()

    buttons = [b.strip() for b in context_page.locator(".dn-wrap button").all_inner_texts()]
    for text in buttons:
        assert text in ALLOWED_BUTTONS or text.startswith("›"), text
        assert not START_WORDS.search(text), text

    first = cards.nth(0)
    first.get_by_role("button", name="Copier la commande").click()
    copied = context_page.evaluate("() => navigator.clipboard.readText()")
    assert copied == first.locator(".dn-cmd code").inner_text()
    assert "Le cockpit ne démarre rien" in first.locator(".action-status").inner_text()
    context_page.get_by_role("button", name="Relire la machine").click()
    context_page.wait_for_selector(".dn-svc")
    assert writes == [], [f"{r.method} {r.url}" for r in writes]


def test_les_huit_etats_se_lisent_chacun_avec_son_mot(context_page: Page, served_inbox: dict[str, str]) -> None:
    _fake(context_page, _payload())
    _open_fleet(context_page, served_inbox, "#flotte?vue=donnees")
    context_page.wait_for_selector(".dn-svc", timeout=30_000)
    seen = {}
    for card in context_page.locator(".dn-svc").all():
        seen[card.get_attribute("data-state")] = card.locator("[data-state-badge]").inner_text().strip()
    assert seen == dict(STATES)
    redis = context_page.locator(".dn-svc[data-service='redis@localhost:6379']")
    assert "brainstrom-conceptjeuvivant" in redis.locator(".dn-facts").inner_text()
    assert "unless-stopped" in redis.locator(".dn-facts").inner_text()
    weaviate = context_page.locator(".dn-svc[data-service='weaviate@localhost:8080']")
    assert "collection GrimoireMemory : 3 projets" in weaviate.locator(".dn-shares").inner_text()
    matrix = context_page.locator(".dn-card", has=context_page.get_by_role("heading", name="Qui partage quoi"))
    forge_row = matrix.locator("tr[data-project='grimoire-forge']")
    assert "partagé" in forge_row.inner_text()
    assert "emprunté" in forge_row.inner_text()
    assert "illisible" in matrix.locator("tr[data-project='casse']").inner_text()
    secret = context_page.locator("[data-secret='GRIMOIRE_NEO4J_PASSWORD']")
    assert secret.inner_text() == "GRIMOIRE_NEO4J_PASSWORD : absent"
    assert context_page.locator(".dn-docker").count() == 0


def test_la_matrice_garde_des_colonnes_egales_et_l_etat_sain_ne_se_repete_pas(
    context_page: Page, served_inbox: dict[str, str],
) -> None:
    """P73, maquette « Flotte — données » : densité des cartes et matrice lisible.

    Un service en ligne ne répète pas « le service répond » sous son badge (le
    motif reste dans le ``title`` du badge) ; tout autre état garde sa raison
    écrite. La matrice « Qui partage quoi » répartit ses colonnes comme la
    maquette (projet plus large, une colonne égale par service) : aucun
    en-tête n'est écrasé ni coupé par le bord du cadre.
    """
    _fake(context_page, _payload())
    _open_fleet(context_page, served_inbox, "#flotte?vue=donnees")
    context_page.wait_for_selector(".dn-svc", timeout=30_000)

    assert context_page.locator(".dn-svc[data-state='en_ligne'] .dn-reason").count() == 0
    reason = context_page.locator(".dn-svc[data-state='incomplet'] .dn-reason")
    assert "GRIMOIRE_NEO4J_PASSWORD" in reason.inner_text()

    matrix = context_page.locator(".dn-card", has=context_page.get_by_role("heading", name="Qui partage quoi"))
    table = matrix.locator("table.tbl")
    assert table.evaluate("t => getComputedStyle(t).tableLayout") == "fixed"
    wrap_box = matrix.locator(".tbl-wrap").bounding_box()
    headers = matrix.locator("thead th")
    last = headers.last
    assert last.inner_text().strip() == "Embarqué"
    box = last.bounding_box()
    assert box["x"] + box["width"] <= wrap_box["x"] + wrap_box["width"] + 1, "l'en-tête Embarqué reste entier dans le cadre"
    widths = [th.bounding_box()["width"] for th in headers.all()[1:]]
    assert max(widths) - min(widths) < 2, widths


def test_docker_injoignable_se_dit_en_clair(context_page: Page, served_inbox: dict[str, str]) -> None:
    _fake(context_page, _payload(docker_up=False))
    _open_fleet(context_page, served_inbox, "#flotte?vue=donnees")
    context_page.wait_for_selector(".dn-docker", timeout=30_000)
    note = context_page.locator(".dn-docker").inner_text()
    assert "Cannot connect to the Docker daemon" in note
    assert "jamais « arrêté »" in note
    assert "Docker injoignable" in context_page.locator(".space-sub").inner_text()
    context_page.locator(".pl-viewseg [data-view='projets']").click()
    context_page.wait_for_selector(".pl-fleet .tbl tbody tr", timeout=30_000)
    assert "vue=donnees" not in context_page.url


def test_captures_de_la_vue_donnees(browser: Browser, served_inbox: dict[str, str]) -> None:
    out = os.environ.get("GRIMOIRE_E2E_CAPTURES")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    for scheme in ("light", "dark"):
        context = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme=scheme, reduced_motion="reduce")
        page = context.new_page()
        try:
            _fake(page, _payload())
            _open_fleet(page, served_inbox, "#flotte?vue=donnees")
            page.wait_for_selector(".dn-svc", timeout=30_000)
            page.screenshot(path=str(Path(out) / f"p26-donnees-{scheme}.png"))
            page.locator(".dn-card").first.scroll_into_view_if_needed()
            page.screenshot(path=str(Path(out) / f"p26-donnees-{scheme}-partages.png"))
        finally:
            context.close()
