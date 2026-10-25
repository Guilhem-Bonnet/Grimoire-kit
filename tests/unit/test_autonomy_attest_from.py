"""#876 — ``--attest-from`` : attester un octroi depuis la session d'un autre projet.

Le journal des tours humains n'est écrit que par le hook du projet où tourne la
session. Un mandat (ou un octroi d'autonomie) demandé sur un projet *cible*
depuis la session d'un autre projet échouait toujours en
``human_authority_unprovable``. ``--attest-from <source>`` désigne le projet dont
le journal atteste ; toutes les autres règles restent : même session, tour
``user`` postérieur au challenge, tour consommé une seule fois — registre tenu
dans la source, donc partagé par toutes les cibles et par la source elle-même.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.exceptions import GrimoireRuntimeError
from grimoire.flows.attestation import CONSUMED_TURNS_FILENAME, FLOWS_RELPATH, NO_HUMAN_TURN
from grimoire.flows.autonomy import GrantJournal, GrantRequest
from grimoire.hosts.decisions import HookInput, run_decision
from grimoire.hosts.decisions.human_turns import turns_path
from grimoire.hosts.events import HookEvent
from grimoire.missions import mandate as md
from grimoire.missions.mandate import MandateRequest, MandateStore

runner = CliRunner()
UNPROVABLE = "human_authority_unprovable"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "user-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for var in (
        "GRIMOIRE_TASK_ID",
        "GRIMOIRE_ACTOR",
        "CLAUDE_CODE_SESSION_ID",
        "GRIMOIRE_UNATTENDED",
        md.PRINCIPAL_ENV,
        md.PROVENANCE_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GRIMOIRE_SESSION_ID", "S1")


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    return root.resolve()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Le projet où tourne la session (la Forge) : son hook a déjà écrit au moins un tour."""
    root = _project(tmp_path, "forge")
    _human_turn(root, prompt="bonjour")
    return root


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """Le projet cible (le kit) : aucun hook de prompt n'y écrit."""
    return _project(tmp_path, "kit")


def _human_turn(root: Path, session_id: str = "S1", prompt: str = "ok") -> None:
    """Le vrai chemin du hook ``UserPromptSubmit`` : la ligne est écrite dans le journal de *root*."""
    time.sleep(0.02)
    run_decision(
        "grimoire.task-context",
        HookInput(
            event=HookEvent.USER_PROMPT_SUBMIT,
            project_root=root,
            prompt=prompt,
            session_id=session_id,
            host="host-claude-code-cli",
        ),
    )
    time.sleep(0.02)


def _request() -> MandateRequest:
    return MandateRequest.build(("accept",), ("implementation",), 2, "+1h")


def _consumed(root: Path) -> list[dict[str, Any]]:
    path = root / FLOWS_RELPATH / "authority" / CONSUMED_TURNS_FILENAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── Le cas normal : piloter le kit depuis la session de la Forge ──────────────


def test_le_mandat_s_accorde_avec_attest_from_quand_le_tour_est_dans_la_source(source: Path, target: Path) -> None:
    store = MandateStore(target)
    pending, first = store.request_grant(_request(), attest_from=source)
    assert pending is None and first.reason == NO_HUMAN_TURN and first.source == str(source)
    _human_turn(source)
    mandate, attestation = store.request_grant(_request(), attest_from=source)
    assert mandate is not None and attestation.attested
    assert mandate.project == str(target) and mandate.attest_from == str(source)
    assert store.state().active
    entries = md.AuthorityJournal().read()
    # Le challenge et le mandat sont ceux de la cible ; le mandat garde la trace de sa source.
    assert {e["project"] for e in entries if e["type"] in ("grant_challenge", "grant")} == {str(target)}
    grant = next(e for e in entries if e["type"] == "grant")
    assert grant["attest_from"] == str(source) and grant["session_id"] == "S1"
    # Le tour est consommé dans le registre de la source, au nom de la cible ; rien n'est écrit dans la cible.
    used = _consumed(source)
    assert len(used) == 1 and used[0]["for_project"] == str(target) and used[0]["node_id"] == mandate.id
    assert _consumed(target) == []
    assert not turns_path(target).exists()


def test_sans_l_option_le_mandat_reste_inprouvable(source: Path, target: Path) -> None:
    store = MandateStore(target)
    store.request_grant(_request())
    _human_turn(source)
    mandate, attestation = store.request_grant(_request())
    assert mandate is None and attestation.reason == UNPROVABLE and attestation.source == ""
    assert "source" not in attestation.to_dict()  # sans l'option, la sortie est celle d'avant


def test_l_octroi_d_autonomie_partage_le_mecanisme(source: Path, target: Path) -> None:
    journal = GrantJournal(target / FLOWS_RELPATH, target)
    request = GrantRequest.build(["blueprint:bp"], 1.0, "+1h")
    grant, attestation = journal.request(request)
    assert grant is None and attestation.reason == UNPROVABLE
    _human_turn(source)
    grant, attestation = journal.request(request, attest_from=source)
    assert grant is not None and attestation.attested and grant["attest_from"] == str(source)
    assert journal.states()[0].to_dict()["attest_from"] == str(source)
    assert [e["type"] for e in journal.entries()] == ["grant_challenge", "grant"]  # challenge et octroi dans la cible
    assert len(_consumed(source)) == 1 and _consumed(target) == []


# ── Les autres règles ne bougent pas ───────────────────────────────────────────


def test_un_tour_d_une_autre_session_n_atteste_pas(source: Path, target: Path) -> None:
    store = MandateStore(target)
    store.request_grant(_request(), attest_from=source)
    _human_turn(source, session_id="S2")
    mandate, attestation = store.request_grant(_request(), attest_from=source)
    assert mandate is None and attestation.reason == NO_HUMAN_TURN


def test_un_tour_anterieur_au_challenge_n_atteste_pas(source: Path, target: Path) -> None:
    _human_turn(source)  # tapé avant que la demande n'existe
    store = MandateStore(target)
    mandate, attestation = store.request_grant(_request(), attest_from=source)
    assert mandate is None and attestation.reason == NO_HUMAN_TURN
    assert _consumed(source) == []


def test_un_meme_tour_ne_sert_pas_deux_octrois_meme_via_deux_cibles(source: Path, tmp_path: Path) -> None:
    kit, other = _project(tmp_path, "kit"), _project(tmp_path, "autre")
    stores = [MandateStore(kit), MandateStore(other)]
    for store in stores:
        store.request_grant(_request(), attest_from=source)
    flows = GrantJournal(other / FLOWS_RELPATH, other)
    flow_request = GrantRequest.build(["blueprint:bp"], 1.0, "+1h")
    flows.request(flow_request, attest_from=source)
    on_source = MandateStore(source)
    on_source.request_grant(_request())  # sans option : la source elle-même
    _human_turn(source)  # un seul tour humain

    first, _ = stores[0].request_grant(_request(), attest_from=source)
    assert first is not None
    second, refused = stores[1].request_grant(_request(), attest_from=source)
    assert second is None and refused.reason == NO_HUMAN_TURN
    grant, refused = flows.request(flow_request, attest_from=source)
    assert grant is None and refused.reason == NO_HUMAN_TURN
    itself, refused = on_source.request_grant(_request())
    assert itself is None and refused.reason == NO_HUMAN_TURN
    # Un lien symbolique vers la source est résolu : même registre, même refus.
    link = tmp_path / "lien-forge"
    link.symlink_to(source, target_is_directory=True)
    again, refused = stores[1].request_grant(_request(), attest_from=link)
    assert again is None and refused.reason == NO_HUMAN_TURN
    assert len(_consumed(source)) == 1


def test_un_tour_consomme_dans_la_cible_ne_ressert_pas_depuis_la_source(source: Path, target: Path) -> None:
    """Le registre de la cible compte aussi : un tour déjà inscrit là-bas reste consommé."""
    store = MandateStore(target)
    store.request_grant(_request(), attest_from=source)
    _human_turn(source)
    mandate, _attestation = store.request_grant(_request(), attest_from=source)
    assert mandate is not None
    # Le même tour recopié dans le registre de la cible (et retiré de la source) ne rouvre rien.
    entry = _consumed(source)[0]
    registry = target / FLOWS_RELPATH / "authority" / CONSUMED_TURNS_FILENAME
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    (source / FLOWS_RELPATH / "authority" / CONSUMED_TURNS_FILENAME).unlink()
    store.revoke()
    pending, _ = store.request_grant(_request(), attest_from=source)
    assert pending is None


# ── Source absente ou illisible : refus clair, rien d'écrit ───────────────────


def test_une_source_absente_ou_illisible_est_refusee(tmp_path: Path, target: Path) -> None:
    missing = tmp_path / "nulle-part"
    with pytest.raises(GrimoireRuntimeError, match="dossier introuvable"):
        MandateStore(target).request_grant(_request(), attest_from=missing)
    bare = _project(tmp_path, "sans-hook")
    with pytest.raises(GrimoireRuntimeError, match="aucun journal des tours humains"):
        MandateStore(target).request_grant(_request(), attest_from=bare)
    broken = _project(tmp_path, "casse")
    turns_path(broken).mkdir(parents=True)  # un dossier à la place du journal
    with pytest.raises(GrimoireRuntimeError, match="illisible"):
        GrantJournal(target / FLOWS_RELPATH, target).request(
            GrantRequest.build(["blueprint:bp"], 1.0, "+1h"), attest_from=broken
        )
    garbled = _project(tmp_path, "binaire")
    turns_path(garbled).parent.mkdir(parents=True)
    turns_path(garbled).write_bytes(b"\xff\xfe\x00")
    with pytest.raises(GrimoireRuntimeError, match="illisible"):
        MandateStore(target).request_grant(_request(), attest_from=garbled)
    # Aucun challenge émis : le refus précède toute écriture.
    assert not (Path.home() / ".grimoire" / "control" / md.JOURNAL_NAME).exists()
    assert not (target / FLOWS_RELPATH).exists()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="droits POSIX")
def test_un_registre_source_qui_ne_s_ecrit_pas_n_accorde_rien(source: Path, target: Path) -> None:
    """Échec fermé : un tour qu'on ne peut pas consommer ne laisse ni mandat actif ni octroi."""
    store = MandateStore(target)
    store.request_grant(_request(), attest_from=source)
    journal = GrantJournal(target / FLOWS_RELPATH, target)
    request = GrantRequest.build(["blueprint:bp"], 1.0, "+1h")
    journal.request(request, attest_from=source)
    _human_turn(source)
    source.chmod(0o555)  # la source se lit, son registre ne peut pas être créé
    try:
        with pytest.raises(GrimoireRuntimeError, match="aucun octroi inscrit"):
            journal.request(request, attest_from=source)
        with pytest.raises(md.MandateRefusedError, match="révoqué aussitôt"):
            store.request_grant(_request(), attest_from=source)
    finally:
        source.chmod(0o755)
    assert [e["type"] for e in journal.entries()] == ["grant_challenge"]
    assert store.state().reason == md.REVOKED
    # Le tour n'a été consommé nulle part : il reste utilisable une fois le registre réparé.
    mandate, _ = store.request_grant(_request(), attest_from=source)
    assert mandate is not None and store.state().active


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_mandate_grant_attest_from(source: Path, target: Path) -> None:
    argv = [
        "-o", "json", "autonomy", "mandate", "grant", "--gesture", "accept", "--task-type", "implementation",
        "--max-sessions-per-day", "2", "--until", "+1h", "--project-root", str(target),
    ]  # fmt: skip
    without = runner.invoke(app, argv)
    assert without.exit_code == 3, without.output
    payload = json.loads(without.stdout)
    assert payload["attestation"]["reason"] == UNPROVABLE and "--attest-from" in payload["hint"]
    _human_turn(source)
    with_it = runner.invoke(app, [*argv, "--attest-from", str(source)])
    assert with_it.exit_code == 0, with_it.output
    assert json.loads(with_it.stdout)["mandate"]["attest_from"] == str(source)


def test_cli_le_conseil_s_affiche_en_texte_et_pas_avec_l_option(source: Path, target: Path) -> None:
    argv = ["autonomy", "grant", "--scope", "blueprint:bp", "--max-cost-usd", "1", "--until", "+1h"]
    argv += ["--project-root", str(target)]
    text = runner.invoke(app, argv)
    assert text.exit_code == 3 and "--attest-from" in text.output
    with_it = runner.invoke(app, ["-o", "json", *argv, "--attest-from", str(source)])
    assert with_it.exit_code == 3, with_it.output
    payload = json.loads(with_it.stdout)
    assert payload["attestation"]["reason"] == NO_HUMAN_TURN and "hint" not in payload
    refused = runner.invoke(app, [*argv, "--attest-from", str(target / "absent")])
    assert refused.exit_code == 1 and "dossier introuvable" in refused.output
