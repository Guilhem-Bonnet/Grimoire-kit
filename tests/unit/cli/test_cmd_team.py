"""W7-02 : ``grimoire team unit …`` de bout en bout sur deux clones d'un même dépôt."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app

pytestmark = pytest.mark.covers(
    "cli:team unit publish", "cli:team unit claim", "cli:team unit renew", "cli:team unit release",
    "cli:team unit adopt", "cli:team unit deliver", "cli:team unit review", "cli:team unit resolve",
    "cli:team unit list", "cli:team unit check",
)

runner = CliRunner()


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.no-hooks", *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return done.stdout.strip()


def _team(tmp_path: Path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "README.md").write_text("projet\n", encoding="utf-8", newline="\n")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.email=s@example.com", "-c", "user.name=S", "commit", "-q", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    clones = []
    for name in ("alice", "bob"):
        dest = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True)
        _git(dest, "config", "user.email", f"{name}@example.com")
        _git(dest, "config", "user.name", name)
        _git(dest, "config", "pull.rebase", "false")
        _git(dest, "config", "core.hooksPath", ".no-hooks")
        _git(dest, "config", "commit.gpgsign", "false")
        clones.append(dest)
    return clones[0], clones[1]


def _run(root: Path, args: list[str], code: int = 0) -> str:
    result = runner.invoke(app, [*args, "--project-root", str(root)])
    assert result.exit_code == code, result.output
    return result.output


def _sync(clone: Path) -> None:
    _git(clone, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    _git(clone, "push", "-q", "origin", "HEAD")


def test_two_humans_through_the_cli(tmp_path: Path) -> None:
    alice, bob = _team(tmp_path)
    _run(alice, ["team", "unit", "publish", "export-csv", "--title", "Export CSV", "--criterion", "un test", "--commit"])
    _sync(alice)
    _sync(bob)
    assert "open" in _run(bob, ["team", "unit", "list"])

    _run(alice, ["team", "unit", "claim", "export-csv", "--commit"])
    _run(bob, ["team", "unit", "claim", "export-csv", "--commit"])
    _sync(alice)
    _sync(bob)  # fusion : deux fichiers distincts, aucun conflit
    _sync(alice)
    out = _run(alice, ["team", "unit", "check"], code=1)
    assert "CONTESTED" in out and "alice@example.com" in out and "bob@example.com" in out
    assert "contestée" in _run(bob, ["team", "unit", "renew", "export-csv"], code=1)

    listed = json.loads(_run(bob, ["-o", "json", "team", "unit", "list", "export-csv"]))
    keep = next(b["head"] for b in listed[0]["branches"] if b["holder"] == "bob@example.com")
    _run(alice, ["team", "unit", "resolve", "export-csv", "--keep", keep[:10], "--commit"])
    _sync(alice)
    _sync(bob)
    assert "Unités partagées cohérentes" in _run(bob, ["team", "unit", "check"])

    _run(bob, ["team", "unit", "release", "export-csv", "--commit"])
    _run(alice, ["team", "unit", "release", "export-csv"], code=1)  # Alice n'est pas titulaire
    _sync(bob)
    _sync(alice)
    _run(alice, ["team", "unit", "claim", "export-csv", "--hours", "0.0002", "--commit"])  # bail de 0,72 s
    _run(alice, ["team", "unit", "renew", "export-csv", "--hours", "0.0002", "--commit"])
    _sync(alice)
    time.sleep(1.5)  # horloge Windows grossière : large marge
    _run(bob, ["team", "unit", "adopt", "export-csv"], code=1)  # pas encore tiré : l'unité est ouverte chez Bob
    _sync(bob)
    assert "EXPIRÉ" in _run(bob, ["team", "unit", "list", "export-csv"])
    _run(bob, ["team", "unit", "adopt", "export-csv", "--commit"])
    _run(bob, ["team", "unit", "deliver", "export-csv", "--evidence", "missing-ref"], code=1)
    _run(bob, ["team", "unit", "deliver", "export-csv", "--evidence", "HEAD", "--evidence", "README.md", "--commit"])
    _run(bob, ["team", "unit", "review", "export-csv", "--accept"], code=1)  # sa propre livraison
    _sync(bob)
    _sync(alice)
    _run(alice, ["team", "unit", "review", "export-csv", "--accept", "--commit"])
    state = json.loads(_run(alice, ["-o", "json", "team", "unit", "list", "export-csv"]))[0]
    assert (state["status"], state["holder"], state["adopted_from"]) == ("done", "bob@example.com", "alice@example.com")
    assert _git(alice, "status", "--porcelain") == ""
