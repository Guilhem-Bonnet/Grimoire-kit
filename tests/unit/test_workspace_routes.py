"""« La même coque, deux hôtes » — prouvé par requête, pas par intention.

La spécification (§5, §6.8) demande qu'une seule interface serve l'atelier
mono-projet et le cockpit multi-projets, et qu'un test montre que **chaque
route honore la cible**. Deux projets réels sont initialisés, servis par les
deux hôtes en même temps, et chaque lecture doit répondre pour le projet qu'on
lui a désigné — jamais pour l'autre, jamais pour « le dernier sélectionné ».

Le second volet est le contraire : le cockpit se déclare ``readOnly`` — sauf
pour le projet qu'il sert en direct (``_HOME_SLUG``, résolu au lancement
depuis le dossier courant, #351/#356 : fusionner ``serve`` dans ``cockpit
serve`` ne devait pas retirer la capacité d'écrire depuis ce projet-là). Les
écritures de la vue de travail (réclamer une tâche, prendre un override,
lancer une commande) restent donc refusées sur tout AUTRE projet du registre —
celui qu'on ne fait que regarder — mais honorées sur le projet de lancement,
exactement comme sur l'hôte mono-projet historique.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from grimoire.cli import cmd_cockpit
from grimoire.data import web_path
from grimoire.tools import project_registry as reg
from grimoire.tools.forge_server import ForgeAPI, make_handler
from grimoire.tools.workspace_routes import GET_ROUTES, POST_ROUTES, PREFIX

# Les lectures sans paramètre obligatoire : celles qu'on peut interroger telles
# quelles sur les deux hôtes. `file`, `file/diff`, `file/usage` et
# `file/history` exigent un `?path=` et sont testées séparément ; `doctor`
# lance un sous-processus et a son propre test, plus lent.
SHARED_READS = sorted(
    set(GET_ROUTES)
    - {
        f"{PREFIX}file", f"{PREFIX}file/diff", f"{PREFIX}file/usage", f"{PREFIX}file/history",
        f"{PREFIX}doctor", f"{PREFIX}language",
    }
)


def _get(port: int, path: str) -> tuple[int, Any]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


def _post(port: int, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 — loopback de test
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}


@pytest.fixture
def atelier(real_project: Path) -> Iterator[int]:
    """`grimoire serve` : un projet, l'atelier."""
    api = ForgeAPI(real_project, Path(__file__).resolve().parents[2], web_path())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def cockpit(real_project: Path, second_project: Path, tmp_path: Path, monkeypatch) -> Iterator[int]:
    """`grimoire cockpit serve` : deux projets au registre, résolus par `?project=`."""
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit"))
    cmd_cockpit._API_CACHE.clear()
    reg.register_project(real_project, "projet-a")
    reg.register_project(second_project, "projet-b")
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


@pytest.fixture
def cockpit_home(
    real_project: Path, second_project: Path, tmp_path: Path, monkeypatch
) -> Iterator[int]:
    """Le même cockpit, mais lancé depuis ``projet-a`` (#351/#356).

    ``monkeypatch.setattr`` sur ``_HOME_SLUG`` mime ce que ``serve()`` ferait
    via ``_select_cwd_project`` sans passer par un vrai ``cwd`` ni écrire
    l'état persistant du registre — et s'annule tout seul en fin de test, pour
    qu'aucun autre test de ce fichier n'hérite d'un projet de lancement.
    """
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "cockpit-home"))
    monkeypatch.setattr(cmd_cockpit, "_HOME_SLUG", "projet-a")
    cmd_cockpit._API_CACHE.clear()
    reg.register_project(real_project, "projet-a")
    reg.register_project(second_project, "projet-b")
    serve_dir = tmp_path / "serve-home"
    serve_dir.mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()


# ── 1. Chaque route honore la cible ─────────────────────────────────────────


@pytest.mark.parametrize("route", SHARED_READS)
def test_chaque_lecture_est_servie_par_les_deux_hotes(
    route: str, atelier: int, cockpit: int
) -> None:
    """Une route qui ne vivrait que dans l'atelier casserait la coque unique.

    Le cockpit ne recopie rien : il appelle la même table. Ce test échoue si
    quelqu'un rebranche une lecture sur le transport de l'atelier.
    """
    code_atelier, _ = _get(atelier, route)
    code_cockpit, _ = _get(cockpit, f"{route}?project=projet-a")

    assert code_atelier == 200, f"{route} absente de l'atelier"
    assert code_cockpit == 200, f"{route} absente du cockpit"


def test_le_cockpit_repond_pour_le_projet_demande_et_pas_pour_un_autre(
    cockpit: int, real_project: Path, second_project: Path
) -> None:
    """Le défaut que ce test ferme : servir le « projet sélectionné » quel que
    soit le `?project=`. Deux projets homonymes, ou deux onglets ouverts, et
    l'utilisateur agit sur le mauvais dépôt."""
    _, a = _get(cockpit, f"{PREFIX}files?project=projet-a")
    _, b = _get(cockpit, f"{PREFIX}files?project=projet-b")

    assert a["projectRoot"] == str(real_project.resolve())
    assert b["projectRoot"] == str(second_project.resolve())
    assert a["projectRoot"] != b["projectRoot"]


def test_l_atelier_ne_sert_que_son_projet(atelier: int, real_project: Path) -> None:
    """`?project=` sur l'atelier ne doit pas ouvrir un autre dépôt : l'hôte
    mono-projet n'a qu'une racine, et c'est une garantie, pas une limite."""
    _, ignored = _get(atelier, f"{PREFIX}files?project=projet-b")

    assert ignored["projectRoot"] == str(real_project.resolve())


def test_un_projet_inconnu_est_refuse_par_le_cockpit(cockpit: int) -> None:
    code, _ = _get(cockpit, f"{PREFIX}files?project=projet-fantome")

    assert code == 404


# ── 2. Les écritures n'existent que sur le projet de lancement ──────────────


@pytest.mark.parametrize("route", sorted(POST_ROUTES))
def test_le_cockpit_refuse_les_ecritures_sur_un_projet_qu_il_ne_lance_pas(
    route: str, cockpit: int
) -> None:
    """Le cockpit se déclare `readOnly` — sauf sur son projet de lancement.

    Cette fixture n'en a pas (`_select_cwd_project` n'a jamais tourné) : les
    deux projets du registre sont donc de simples entrées qu'on regarde, pas
    celui qu'on sert en direct. Réclamer une tâche ou créer un override dans
    l'un ou l'autre resterait une régression de gouvernance — la route
    EXISTE désormais des deux côtés (#356), le refus est donc un 403, plus
    précis que le 404 d'avant sa fusion dans `cockpit serve` (#351).
    """
    code, _ = _post(cockpit, f"{route}?project=projet-a", {"path": "x", "argv": ["version"]})

    assert code == 403


@pytest.mark.parametrize("route", sorted(POST_ROUTES))
def test_le_cockpit_refuse_toujours_l_autre_projet_meme_avec_un_lancement_direct(
    route: str, cockpit_home: int
) -> None:
    """`cockpit_home` sert `projet-a` en direct — mais pas `projet-b`.

    Le pilier de la garantie #356 : avoir UN projet ouvert en écriture ne rend
    pas le cockpit généralement inscriptible. Un clic malheureux sur une autre
    carte du registre ne doit jamais écrire là où l'utilisateur ne fait que
    regarder.
    """
    code, _ = _post(cockpit_home, f"{route}?project=projet-b", {"path": "x", "argv": ["version"]})

    assert code == 403


def test_le_cockpit_execute_une_commande_de_la_liste_blanche_sur_son_projet_de_lancement(
    cockpit_home: int,
) -> None:
    """Miroir cockpit de `test_l_atelier_execute_une_commande_de_la_liste_blanche`
    (#356) : le projet de lancement retrouve la capacité que `serve` avait
    avant sa fusion dans `cockpit serve` (#351)."""
    code, payload = _post(cockpit_home, f"{PREFIX}command?project=projet-a", {"argv": ["version"]})

    assert code == 200
    assert payload["ok"] is True
    assert "grimoire-kit" in payload["output"]


def test_l_atelier_execute_une_commande_de_la_liste_blanche(atelier: int) -> None:
    code, payload = _post(atelier, f"{PREFIX}command", {"argv": ["version"]})

    assert code == 200
    assert payload["ok"] is True
    assert "grimoire-kit" in payload["output"]


def test_l_atelier_refuse_une_commande_hors_liste_blanche(atelier: int) -> None:
    """Critère 6 de la spec, vu du transport : le refus est un 400 explicite,
    pas une trace de 500."""
    code, payload = _post(atelier, f"{PREFIX}command", {"argv": ["ls", "-la"]})

    assert code == 400
    assert "refus" in payload["error"].lower()


def test_un_chemin_hors_projet_est_un_403_a_travers_le_transport(atelier: int) -> None:
    code, payload = _get(atelier, f"{PREFIX}file?path=../../etc/passwd")

    assert code == 403
    assert payload["error"]


def test_une_route_inconnue_sous_le_prefixe_reste_un_404(atelier: int) -> None:
    """Le préfixe ne doit pas devenir un fourre-tout qui avale les fautes de frappe."""
    code, _ = _get(atelier, f"{PREFIX}nexiste-pas")

    assert code == 404


# ── 3. La coque est servie par les deux hôtes ───────────────────────────────


def test_l_atelier_sert_la_coque_de_la_vue_de_travail(atelier: int) -> None:
    """`web/workspace/index.html` doit être atteignable : c'est la coque unique."""
    req = urllib.request.Request(f"http://127.0.0.1:{atelier}/workspace/index.html")
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback de test
        body = resp.read().decode("utf-8")

    assert resp.status == 200
    assert 'id="shell"' in body
    assert './shell.js' in body


# ── 4. L'inspecteur : usage et historique, sur les deux hôtes ───────────────


def _kit_markdown_path(port: int) -> str:
    _, tree = _get(port, f"{PREFIX}files?tier=kit")
    sample = next(f for f in tree["tiers"][0]["files"] if f["path"].endswith(".md"))
    return str(sample["path"])


def test_file_usage_et_file_history_repondent_sur_les_deux_hotes(
    atelier: int, cockpit: int
) -> None:
    """Les deux onglets de l'inspecteur ont besoin d'un `?path=`, donc ils ne
    sont pas dans :data:`SHARED_READS` — mais la promesse « chaque route honore
    la cible » leur reste due."""
    path = _kit_markdown_path(atelier)

    code_a, usage_a = _get(atelier, f"{PREFIX}file/usage?path={path}")
    code_b, usage_b = _get(cockpit, f"{PREFIX}file/usage?path={path}&project=projet-a")
    code_c, history_a = _get(atelier, f"{PREFIX}file/history?path={path}")
    code_d, history_b = _get(cockpit, f"{PREFIX}file/history?path={path}&project=projet-a")

    assert code_a == code_b == 200
    assert usage_a["path"] == usage_b["path"] == path
    assert "projections" in usage_a and "loaded_by" in usage_a
    assert code_c == code_d == 200
    assert history_a["path"] == history_b["path"] == path
    assert "commits" in history_a


def test_language_repond_sur_les_deux_hotes_avec_tokens_et_diagnostics(
    atelier: int, cockpit: int
) -> None:
    """L'IntelliSense de Source (#280) a aussi besoin d'un ``?path=`` — même
    promesse de cible que ``file/usage`` et ``file/history``."""
    path = _kit_markdown_path(atelier)

    code_a, payload_a = _get(atelier, f"{PREFIX}language?path={path}")
    code_b, payload_b = _get(cockpit, f"{PREFIX}language?path={path}&project=projet-a")

    assert code_a == code_b == 200
    assert payload_a["path"] == payload_b["path"] == path
    assert "tokens" in payload_a and "diagnostics" in payload_a
    assert "completions" not in payload_a, "pas de complétions sans line/col"


def test_language_avec_un_brouillon_ignore_le_disque(atelier: int) -> None:
    """``text=`` porte le brouillon affiché : il prime sur le fichier réel,
    pour que la colorisation reste juste avant tout enregistrement."""
    path = _kit_markdown_path(atelier)

    code, payload = _get(
        atelier, f"{PREFIX}language?path={path}&text=" + "voir%20_grimoire%2Fkit%2Fabsent.md"
    )

    assert code == 200
    assert any(d["family"] == "dead-path" for d in payload["diagnostics"])


def test_language_avec_position_rend_des_completions(atelier: int) -> None:
    code, payload = _get(
        atelier,
        f"{PREFIX}language?path=_grimoire/kit/agents/x.md&text=voir%20%40&line=0&col=6",
    )

    assert code == 200
    assert isinstance(payload["completions"], list)


# ── 5. Un chemin hostile est refusé, symlink compris ────────────────────────


def test_un_symlink_qui_sort_du_projet_est_refuse(atelier: int, real_project: Path) -> None:
    """Un lien symbolique n'est pas un détour autour du garde de chemin : la
    résolution suit le lien, et la cible réelle est ce qui compte."""
    escape = real_project / "vers-ailleurs"
    try:
        escape.symlink_to("/etc")
    except OSError:
        pytest.skip("liens symboliques indisponibles sur ce système de fichiers")

    code, payload = _get(atelier, f"{PREFIX}file?path=vers-ailleurs/passwd")

    assert code == 403
    assert payload["error"]


# ── 6. Refus explicite hors loopback, vu depuis la vue de travail ──────────


def test_une_lecture_de_la_vue_de_travail_refuse_un_host_etranger(atelier: int) -> None:
    """Le garde anti rebinding-DNS de `forge_http` est générique — ce test
    prouve qu'il couvre bien le préfixe `/api/workspace/`, pas seulement les
    routes héritées qui ont leur propre test dans `test_serve_hardening.py`."""
    req = urllib.request.Request(f"http://127.0.0.1:{atelier}{PREFIX}glossary")
    req.add_header("Host", "evil.example.com")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback de test
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code

    assert code == 403


# ── 7. Agents (#374) : refus explicable à travers le transport cockpit ─────
#
# Projet dédié plutôt que ``real_project``/``cockpit``/``cockpit_home`` : ces
# tests écrivent un override d'agent, et les fixtures partagées ci-dessus
# sont à portée session — les polluer romprait des tests d'autres fichiers
# qui échantillonnent le premier agent du kit par ordre alphabétique.


@pytest.fixture(scope="module")
def agents_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """Un cockpit qui sert en direct un projet dédié aux tests d'agents.

    Portée module, sans ``monkeypatch`` (fonction seulement par défaut) :
    l'environnement et ``_HOME_SLUG`` sont posés et restaurés à la main.
    """
    import os
    import subprocess

    root = tmp_path_factory.mktemp("agents-home") / "projet-agents-home"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    subprocess.run(
        [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", "projet-agents-home"],
        cwd=str(root), check=False, capture_output=True, timeout=180,
    )
    if not (root / "_grimoire" / "kit" / "agents").is_dir():
        pytest.skip("`grimoire init` indisponible ici")

    tmp_path = tmp_path_factory.mktemp("agents-home-cockpit")
    previous_env = os.environ.get("GRIMOIRE_COCKPIT_HOME")
    previous_home_slug = cmd_cockpit._HOME_SLUG
    os.environ["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path / "cockpit")
    cmd_cockpit._HOME_SLUG = "projet-agents-home"
    cmd_cockpit._API_CACHE.clear()
    reg.register_project(root, "projet-agents-home")
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()
        cmd_cockpit._HOME_SLUG = previous_home_slug
        if previous_env is None:
            os.environ.pop("GRIMOIRE_COCKPIT_HOME", None)
        else:
            os.environ["GRIMOIRE_COCKPIT_HOME"] = previous_env


def test_lire_les_agents_a_travers_le_cockpit(agents_home: int) -> None:
    code, payload = _get(agents_home, f"{PREFIX}agents?project=projet-agents-home")

    assert code == 200
    assert payload["agents"], "l'archétype meta livre des agents"


def test_assigner_un_skill_a_travers_le_cockpit_cree_l_override(agents_home: int) -> None:
    code, payload = _post(
        agents_home,
        f"{PREFIX}agents/concierge/skill?project=projet-agents-home",
        {"skill": "grimoire-memory", "action": "assign"},
    )

    assert code == 200
    agent = next(a for a in payload["agents"] if a["name"] == "concierge")
    assert "grimoire-memory" in agent["skills"]


def test_un_skill_inconnu_a_travers_le_cockpit_rend_un_400_explicable(agents_home: int) -> None:
    """Le correctif de ce lot : avant lui, une exception levée par
    ``workspace_post`` sur le cockpit atteignait ``http.server`` sans être
    traduite — la connexion se coupait sans réponse JSON, jamais un 400."""
    code, payload = _post(
        agents_home,
        f"{PREFIX}agents/agent-optimizer/skill?project=projet-agents-home",
        {"skill": "un-skill-qui-n-existe-pas", "action": "assign"},
    )

    assert code == 400
    assert "introuvable" in payload["error"].lower()


def test_un_agent_inconnu_a_travers_le_cockpit_rend_un_404_explicable(agents_home: int) -> None:
    code, payload = _post(
        agents_home,
        f"{PREFIX}agents/n-existe-pas/skill?project=projet-agents-home",
        {"skill": "grimoire-memory", "action": "assign"},
    )

    assert code == 404
    assert payload["error"]


# ── 8. Tâches (#140) : la cible et le gate à travers le transport ──────────
#
# Les actions de tâche (``claim``/``move``/``block``/``close``) sont un chemin
# paramétré de ``workspace_post`` (``/api/workspace/tasks/<id>/<action>``),
# donc absent de :data:`POST_ROUTES` — les tests 2 ci-dessus ne les couvrent
# jamais. C'est exactement la lacune que l'issue nomme : rien ne prouvait
# jusqu'ici, au niveau transport, qu'un gate rouge nomme la preuve manquante
# ni qu'un projet qui n'est pas celui de lancement reste refusé pour une
# tâche comme il l'est déjà pour un override ou une commande.


@pytest.fixture(scope="module")
def tasks_home(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[int, Path]]:
    """Un cockpit qui lance en direct un projet gouverné, et sert en lecture
    seule un second projet du registre.

    Même principe que :func:`agents_home` : un projet dédié, pas les fixtures
    de session ``real_project``/``second_project`` partagées par le reste de
    la suite — écrire une transition dessus polluerait des tests qui lisent
    leurs tâches par position. Rend la racine du projet de lancement plutôt
    qu'un identifiant de tâche : chaque test mint la sienne (voir
    :func:`_new_task`), pour rester indépendant de l'ordre d'exécution des
    autres tests de cette section — une transition écrite par l'un ne doit
    pas devenir la précondition silencieuse d'un autre.
    """
    import os
    import subprocess

    home_root = tmp_path_factory.mktemp("tasks-home") / "projet-tasks-home"
    away_root = tmp_path_factory.mktemp("tasks-away") / "projet-tasks-away"
    for root, name in ((home_root, "projet-tasks-home"), (away_root, "projet-tasks-away")):
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
        subprocess.run(
            [sys.executable, "-m", "grimoire", "init", ".", "-y", "--name", name],
            cwd=str(root), check=False, capture_output=True, timeout=180,
        )
        subprocess.run(
            [sys.executable, "-m", "grimoire", "standard", "init", "--profile", "governed"],
            cwd=str(root), check=False, capture_output=True, timeout=180,
        )
    if not (home_root / "_grimoire" / "kit").is_dir():
        pytest.skip("`grimoire init` indisponible ici")

    tmp_path = tmp_path_factory.mktemp("tasks-home-cockpit")
    previous_env = os.environ.get("GRIMOIRE_COCKPIT_HOME")
    previous_home_slug = cmd_cockpit._HOME_SLUG
    os.environ["GRIMOIRE_COCKPIT_HOME"] = str(tmp_path / "cockpit")
    cmd_cockpit._HOME_SLUG = "projet-tasks-home"
    cmd_cockpit._API_CACHE.clear()
    reg.register_project(home_root, "projet-tasks-home")
    reg.register_project(away_root, "projet-tasks-away")
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir(parents=True)
    handler = partial(cmd_cockpit._CockpitHandler, directory=str(serve_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1], home_root
    finally:
        httpd.shutdown()
        httpd.server_close()
        cmd_cockpit._API_CACHE.clear()
        cmd_cockpit._HOME_SLUG = previous_home_slug
        if previous_env is None:
            os.environ.pop("GRIMOIRE_COCKPIT_HOME", None)
        else:
            os.environ["GRIMOIRE_COCKPIT_HOME"] = previous_env


def _new_task(home_root: Path, title: str) -> str:
    """Ouvre une tâche fraîche (``proposed``, acceptance et owner déclarés)
    dans le projet de lancement, et rend son identifiant.

    ``grimoire task add`` imprime sur ``stderr`` (``cmd_task.console =
    Console(stderr=True)``) : chercher l'identifiant sur ``stdout`` seul
    échoue toujours et masquerait un vrai échec derrière un skip.
    """
    import subprocess

    added = subprocess.run(
        [
            sys.executable, "-m", "grimoire", "task", "add", title,
            "-a", "Le board affiche la tâche", "-a", "Le refus nomme la preuve",
            "--owner", "winston",
        ],
        cwd=str(home_root), capture_output=True, text=True, check=False, timeout=180,
    )
    match = re.search(r"(GAO-[a-zA-Z0-9-]+)", added.stdout + added.stderr)
    if not match:
        pytest.skip(
            f"`grimoire task add` n'a pas ouvert de tâche ici : "
            f"{added.stdout[-400:]} {added.stderr[-400:]}"
        )
    return match.group(1)


def test_une_transition_de_tache_reussie_ecrit_au_ledger_via_le_cockpit(
    tasks_home: tuple[int, Path],
) -> None:
    """``proposed -> ready`` : acceptance et owner sont déjà déclarés à la
    création, la porte est ouverte — la carte doit avancer."""
    port, home_root = tasks_home
    task_id = _new_task(home_root, "Faire avancer une porte ouverte")
    code, payload = _post(
        port, f"{PREFIX}tasks/{task_id}/move?project=projet-tasks-home", {"to": "ready"}
    )

    assert code == 200
    assert payload.get("blocked") is not True
    assert payload["status"] == "ready"
    assert payload["transition"] == "proposed → ready"


def test_une_transition_refusee_nomme_la_preuve_manquante_via_le_cockpit(
    tasks_home: tuple[int, Path],
) -> None:
    """``ready -> claimed`` (réclamer) exige un context bundle et un
    fournisseur activé au registre : un projet fraîchement initialisé n'a ni
    l'un ni l'autre. La réponse reste un 200 — ce n'est pas une panne du
    serveur, c'est LA réponse — avec l'artefact manquant nommé, jamais une
    carte qui avance en silence ni une erreur muette. Passe d'abord par
    ``proposed -> ready`` (porte ouverte) pour atteindre l'état où
    ``claim`` est l'edge légal de la machine à états — la porte fermée est
    celle d'après, pas celle-ci."""
    port, home_root = tasks_home
    task_id = _new_task(home_root, "Réclamer sans fournisseur activé")
    ready_code, _ = _post(
        port, f"{PREFIX}tasks/{task_id}/move?project=projet-tasks-home", {"to": "ready"}
    )
    assert ready_code == 200
    code, payload = _post(port, f"{PREFIX}tasks/{task_id}/claim?project=projet-tasks-home", {})

    assert code == 200
    assert payload["blocked"] is True
    assert payload["refusals"], "le refus doit nommer au moins un artefact manquant"
    named = " ".join(r["evidence"] for r in payload["refusals"]).lower()
    assert "context" in named or "provider" in named or "fournisseur" in named
    assert all(r["remedy"] for r in payload["refusals"]), "chaque refus nomme aussi un remède"


def test_une_transition_de_tache_est_refusee_hors_projet_de_lancement(
    tasks_home: tuple[int, Path],
) -> None:
    """Le pilier #356 vu depuis les tâches : ``projet-tasks-away`` n'est qu'un
    projet du registre que ce cockpit regarde, jamais celui qu'il sert en
    direct — la carte ne doit pas bouger, gate rouge ou pas. Le corps cible
    délibérément une tâche qui n'existe même pas dans ``projet-tasks-away`` :
    le refus doit intervenir avant toute résolution de tâche, sur la seule
    base du projet visé.
    """
    port, _home_root = tasks_home
    code, payload = _post(
        port, f"{PREFIX}tasks/GAO-quelconque-001/move?project=projet-tasks-away", {"to": "ready"}
    )

    assert code == 403
    assert "lecture seule" in payload["error"].lower()


def test_un_mouvement_de_tache_vers_un_etat_inconnu_est_un_400(
    tasks_home: tuple[int, Path],
) -> None:
    port, home_root = tasks_home
    task_id = _new_task(home_root, "Viser un état qui n'existe pas")
    code, payload = _post(
        port, f"{PREFIX}tasks/{task_id}/move?project=projet-tasks-home", {"to": "vaporisee"}
    )

    assert code == 400
    assert "vaporisee" in payload["error"]
