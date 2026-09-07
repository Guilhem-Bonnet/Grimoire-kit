"""« La même coque, deux hôtes » — prouvé par requête, pas par intention.

La spécification (§5, §6.8) demande qu'une seule interface serve l'atelier
mono-projet et le cockpit multi-projets, et qu'un test montre que **chaque
route honore la cible**. Deux projets réels sont initialisés, servis par les
deux hôtes en même temps, et chaque lecture doit répondre pour le projet qu'on
lui a désigné — jamais pour l'autre, jamais pour « le dernier sélectionné ».

Le second volet est le contraire : le cockpit se déclare ``readOnly``. Les
écritures de la vue de travail (réclamer une tâche, prendre un override, lancer
une commande) ne doivent exister QUE sur l'hôte mono-projet.
"""

from __future__ import annotations

import json
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


# ── 2. Les écritures n'existent que sur l'hôte mono-projet ──────────────────


@pytest.mark.parametrize("route", sorted(POST_ROUTES))
def test_le_cockpit_n_expose_aucune_ecriture_de_la_vue_de_travail(
    route: str, cockpit: int
) -> None:
    """Le cockpit se déclare `readOnly` depuis sa création.

    Lui donner de quoi réclamer une tâche ou prendre un override dans un dépôt
    qu'il ne sert pas serait une régression de gouvernance. Le refus est un 404 :
    la route n'existe pas de ce côté.
    """
    code, _ = _post(cockpit, f"{route}?project=projet-a", {"path": "x", "argv": ["version"]})

    assert code == 404


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
