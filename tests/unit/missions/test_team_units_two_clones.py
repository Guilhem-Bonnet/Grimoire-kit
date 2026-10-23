"""W7-02 : unités partagées entre deux humains, chacun dans son clone git d'un même dépôt.

Un dépôt nu joue l'``origin`` ; Alice et Bob clonent, écrivent, committent, poussent et
tirent comme deux postes. On vérifie : fusion sans conflit textuel, réservations
concurrentes détectées après ``git pull`` et tranchées seulement par un geste
explicite (fail-closed), adoption d'un bail expiré, livraison prouvée et revue.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireMissionError
from grimoire.missions import team_units as tu

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.no-hooks", *args],
        capture_output=True, text=True, encoding="utf-8", check=check,
    )


def _clone(origin: Path, dest: Path, email: str) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, capture_output=True)
    _git(dest, "config", "user.email", email)
    _git(dest, "config", "user.name", email.split("@")[0])
    _git(dest, "config", "pull.rebase", "false")
    return dest


def _share(clone: Path, message: str) -> None:
    """Committe les fichiers d'unités un par un, tire, pousse."""
    for line in _git(clone, "status", "--porcelain", "--untracked-files=all").stdout.splitlines():
        rel = line[3:]
        if rel.startswith(tu.UNITS_DIR.as_posix()):
            _git(clone, "add", "--", rel)
    _git(clone, "commit", "-q", "-m", message)
    _pull(clone)
    _git(clone, "push", "-q", "origin", "HEAD")


def _pull(clone: Path) -> None:
    done = _git(clone, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main", check=False)
    assert done.returncode == 0, done.stdout + done.stderr  # aucune fusion ne doit échouer
    status = _git(clone, "status", "--porcelain").stdout
    assert "UU " not in status and "AA " not in status, status


@pytest.fixture
def team(tmp_path: Path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README.md").write_text("projet\n", encoding="utf-8", newline="\n")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.email=s@example.com", "-c", "user.name=S", "commit", "-q", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    alice = _clone(origin, tmp_path / "alice", "alice@example.com")
    bob = _clone(origin, tmp_path / "bob", "bob@example.com")
    return alice, bob


def _publish(root: Path, unit: str = "api-profil") -> None:
    actor, src = tu.resolve_identity(root)
    tu.record(root, unit, "publish", actor, identity=src, now=T0,
              data={"title": "API de profil", "criteria": ["GET /profile rend 200", "tests verts"]})


def _state(root: Path, unit: str = "api-profil") -> tu.UnitState:
    state = tu.load_unit(root, unit)
    assert state is not None
    return state


def test_concurrent_claims_merge_without_conflict_and_nobody_wins(team: tuple[Path, Path]) -> None:
    alice, bob = team
    _publish(alice)
    _share(alice, "publish")
    _pull(bob)
    assert _state(bob).status == "open"

    # Les deux réservent sur la même tête, chacun dans son clone, sans s'être vus.
    tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0)
    tu.record(bob, "api-profil", "claim", "bob@example.com", now=T0)
    assert _state(alice).holder == "alice@example.com"
    assert _state(bob).holder == "bob@example.com"
    _share(alice, "alice claim")
    _share(bob, "bob claim")  # tire la réservation d'Alice : fusion sans conflit textuel
    _pull(alice)

    for clone in (alice, bob):
        state = _state(clone)
        assert state.status == "contested"
        assert sorted(b.holder for b in state.branches) == ["alice@example.com", "bob@example.com"]
        for verb in ("claim", "deliver", "release", "renew"):
            with pytest.raises(GrimoireMissionError, match="contestée"):
                tu.record(clone, "api-profil", verb, "alice@example.com", now=T0, data={"evidence": []})

    # Bob cède : il retient la tête d'Alice ; l'historique de la sienne reste lisible.
    alice_head = next(b.head for b in _state(bob).branches if b.holder == "alice@example.com")
    tu.record(bob, "api-profil", "resolve", "bob@example.com", data={"keep": alice_head[:8]}, now=T0)
    _share(bob, "resolve")
    _pull(alice)
    for clone in (alice, bob):
        state = _state(clone)
        assert (state.status, state.holder) == ("claimed", "alice@example.com")
        assert state.events == 4  # publish, deux claims, resolve : la branche écartée reste au dépôt
    assert len(list((alice / tu.UNITS_DIR / "api-profil").glob("*.json"))) == 4


def test_concurrent_publish_of_same_unit_is_contested(team: tuple[Path, Path]) -> None:
    alice, bob = team
    _publish(alice)
    _publish(bob)
    _share(alice, "a")
    _share(bob, "b")
    state = _state(bob)
    assert state.status == "contested" and len(state.heads) == 2


def test_adopt_expired_lease_and_late_holder_conflict(team: tuple[Path, Path]) -> None:
    alice, bob = team
    _publish(alice)
    tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0, hours=1)
    _share(alice, "claim")
    _pull(bob)

    with pytest.raises(GrimoireMissionError, match="court jusqu'à"):
        tu.record(bob, "api-profil", "adopt", "bob@example.com", now=T0 + timedelta(minutes=30))
    assert _state(bob).expired(T0 + timedelta(hours=2))
    with pytest.raises(GrimoireMissionError, match="adopt"):
        tu.record(bob, "api-profil", "claim", "bob@example.com", now=T0 + timedelta(hours=2))
    tu.record(bob, "api-profil", "adopt", "bob@example.com", now=T0 + timedelta(hours=2))
    state = _state(bob)
    assert (state.holder, state.adopted_from) == ("bob@example.com", "alice@example.com")

    # Alice, sans avoir tiré, renouvelle son bail expiré pendant ce temps : fork détecté à la fusion.
    tu.record(alice, "api-profil", "renew", "alice@example.com", now=T0 + timedelta(hours=2))
    _share(bob, "adopt")
    _share(alice, "renew")
    assert _state(alice).status == "contested"

    # Sans fork : une fois l'adoption tirée, Alice n'est plus titulaire.
    bob_head = next(b.head for b in _state(alice).branches if b.holder == "bob@example.com")
    tu.record(alice, "api-profil", "resolve", "alice@example.com", data={"keep": bob_head}, now=T0)
    _share(alice, "resolve")
    _pull(bob)
    with pytest.raises(GrimoireMissionError, match=r"titulaire bob@example\.com"):
        tu.record(alice, "api-profil", "deliver", "alice@example.com", data={"evidence": [{"ref": "x"}]})
    assert _state(bob).holder == "bob@example.com"


def test_deliver_needs_resolved_proof_and_review_by_someone_else(team: tuple[Path, Path]) -> None:
    alice, bob = team
    _publish(alice)
    tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0)
    with pytest.raises(GrimoireMissionError, match="introuvable"):
        tu.resolve_evidence(alice, ["deadbeef0000", "../outside.txt"])
    proofs = tu.resolve_evidence(alice, ["HEAD", "README.md"])
    assert len(proofs[0]["commit"]) == 40 and len(proofs[1]["sha256"]) == 64
    tu.record(alice, "api-profil", "deliver", "alice@example.com", data={"evidence": proofs}, now=T0)
    with pytest.raises(GrimoireMissionError, match="propre livraison"):
        tu.record(alice, "api-profil", "review", "alice@example.com", data={"verdict": "accept"}, now=T0)
    _share(alice, "deliver")
    _pull(bob)
    tu.record(bob, "api-profil", "review", "bob@example.com", data={"verdict": "reject"}, now=T0)
    assert (_state(bob).status, _state(bob).holder) == ("claimed", "alice@example.com")
    _share(bob, "reject")
    _pull(alice)
    tu.record(alice, "api-profil", "deliver", "alice@example.com", data={"evidence": proofs}, now=T0)
    tu.record(alice, "api-profil", "review", "bob@example.com", data={"verdict": "accept"}, now=T0)
    assert _state(alice).status == "done"


def test_tampered_or_partial_unit_blocks_every_write(team: tuple[Path, Path]) -> None:
    alice, _ = team
    _publish(alice)
    _, path = tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["actor"] = "mallory@example.com"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _state(alice).status == "invalid"
    with pytest.raises(GrimoireMissionError, match="invalid"):
        tu.record(alice, "api-profil", "release", "alice@example.com", now=T0)
    path.unlink()

    # Un parent absent (clone partiel, fichier perdu) : incomplete, jamais deviné.
    _, claim_path = tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0)
    tu.record(alice, "api-profil", "renew", "alice@example.com", now=T0)
    claim_path.unlink()
    assert _state(alice).status == "incomplete"
    with pytest.raises(GrimoireMissionError, match="incomplete"):
        tu.record(alice, "api-profil", "renew", "alice@example.com", now=T0)


def test_event_file_survives_crlf_checkout(team: tuple[Path, Path]) -> None:
    alice, _ = team
    _publish(alice)
    _, path = tu.record(alice, "api-profil", "claim", "alice@example.com", now=T0)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # core.autocrlf=true sous Windows
    assert _state(alice).status == "claimed"


def test_unknown_identity_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "config", "user.email", "")
    with pytest.raises(GrimoireMissionError, match="identité inconnue"):
        tu.resolve_identity(repo)
    assert tu.resolve_identity(repo, "carol") == ("carol", "option")
    with pytest.raises(GrimoireMissionError, match="identifiant"):
        tu.record(repo, "API", "publish", "carol", data={"title": "t", "criteria": ["c"]})
