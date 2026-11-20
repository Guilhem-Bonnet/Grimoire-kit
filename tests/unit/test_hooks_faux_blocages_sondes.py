"""Sondes de la relecture adverse de #1265 : de vraies écritures dans le projet restent comptées.

La première version du correctif des faux blocages ne comptait plus :

- une commande qui contenait un mot de vérification n'importe où (``ruff check --fix``,
  ``./gen.sh # pytest``, ``git apply p.patch && pytest -q``, un chemin ``pytest-of-…``) ;
- une commande illisible qui citait un seul chemin absolu hors projet (``git apply /dehors/p.patch``,
  ``./gen.sh > /dehors/log``, ``git -C /dehors status && make gen``) ;
- toute commande d'un sous-agent qui n'était ni prouvée ni destructive (``make gen``,
  ``git apply``, ``npm install``, ``patch``) ;
- et le ``Stop`` ne refusait plus qu'une fois par session.

Chaque cas ci-dessous était rouge sur la tête relue (``89ae4e52b``).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import HookInput, Outcome
from grimoire.hosts.decisions.session_writes import counts_as_session_write
from grimoire.hosts.decisions.tool_facts import classify_tool
from grimoire.hosts.events import HookEvent
from grimoire.hosts.runtime import run_hook
from grimoire.policies.session_state import session_mutations

SESSION = "sonde000-0000-4000-8000-000000000001"


@pytest.fixture
def lieux() -> Iterator[tuple[Path, Path]]:
    """Un projet et un dossier « dehors » qui porte un lien vers lui, dans un dossier neutre."""
    with tempfile.TemporaryDirectory(prefix="sonde-") as raw:
        base = Path(raw).resolve()
        root = base / "projet"
        (root / "src").mkdir(parents=True)
        out = base / "dehors"
        out.mkdir()
        (out / "lien").symlink_to(root)
        yield root, out


def _counts(root: Path, command: str, *, agent: str = "", cwd: Path | None = None) -> bool:
    hook = HookInput(
        event=HookEvent.POST_TOOL_USE,
        project_root=root,
        tool_name="Bash",
        tool_input={"command": command},
        session_id="s1",
        agent_id=agent,
        raw={"cwd": str(cwd or root)},
    )
    return counts_as_session_write(hook, classify_tool("Bash", {"command": command}))


ECRITURES = [
    "ruff check --fix src/",
    "ruff format src/",
    "pytest --basetemp=src/tmpdir",
    "eslint --fix src",
    "tsc -p .",
    "./gen.sh # pytest",
    "make gen # ruff",
    "git apply p.patch && pytest -q",
    "bash scripts/regen.sh; ruff check .",
    "python scripts/gen.py /tmp/out",
    "make gen",
    "./gen.sh",
    "sed -i s/a/b/ src/a.py",
    "echo x > src/a.py; pytest",
    "git apply p.patch",
    "git commit -am x",
    "git checkout -- src/a.py",
    "git stash pop",
    "git restore src/a.py",
    "bash scripts/regen.sh",
    "python scripts/gen.py",
    "npm install",
    "perl -pi -e s/a/b/ src/a.py",
    "patch -p1 < x.patch",
    "cp /tmp/a src/a",
    "tee src/a.py < /tmp/x",
    "mv src/a src/b",
    "touch src/new.py",
    "mkdir src/newdir",
    "python -c \"open('src/a','w').write('x')\"",
    "git reset --hard",
    "rm src/a.py",
    "echo x > $F",
    "cd src && cp /tmp/a b",
]


@pytest.mark.parametrize("agent", ["", "sous-agent-1"])
@pytest.mark.parametrize("command", ECRITURES)
def test_une_ecriture_possible_dans_le_projet_compte(lieux: tuple[Path, Path], command: str, agent: str) -> None:
    root, _ = lieux
    assert _counts(root, command, agent=agent)


CHEMINS = [
    "cp /tmp/a {out}/lien/src/a",
    "bash {out}/lien/gen.sh",
    "cd {out}/lien && make gen",
    "cd {out} && ./lien/gen.sh",
    "cd {out} && make -C lien gen",
    "git -C {out} status && make gen",
    "git -C {out} status && ./gen.sh",
    "python scripts/gen.py --out {out}/x",
    "python scripts/gen.py {out}/cfg.yaml",
    "make gen OUT={out}/x",
    "./gen.sh > {out}/log.txt",
    "git apply {out}/p.patch",
    "patch -p1 < {out}/x.patch",
    "python {root}/scripts/gen.py",
    "make -C {root} gen",
    "cd {out} && cp /tmp/a ../projet/src/a",
    "cd {out} && sed -i s/a/b/ ../projet/src/a.py",
]


@pytest.mark.parametrize("agent", ["", "sous-agent-1"])
@pytest.mark.parametrize("template", CHEMINS)
def test_un_chemin_hors_projet_ne_fait_pas_sortir_une_ecriture(
    lieux: tuple[Path, Path], template: str, agent: str
) -> None:
    root, out = lieux
    assert _counts(root, template.format(out=out.as_posix(), root=root.as_posix()), agent=agent)


def test_un_cwd_hors_projet_ne_fait_pas_sortir_une_ecriture_relative(lieux: tuple[Path, Path]) -> None:
    root, out = lieux
    assert _counts(root, "cp /tmp/a src/a", agent="sous-agent-1", cwd=out)
    assert _counts(root, "cp /tmp/a ../projet/src/a", agent="sous-agent-1", cwd=out)


LIBRES = [
    "cat > {out}/notes.md <<'EOF'\nvoir src/app.py\nEOF",
    "grep -rn foo src > {out}/hits.txt",
    "mkdir -p {out}/x && cp README.md {out}/x/",
    "python -m pytest tests -q",
    "timeout 300 uv run pytest -q -n 4",
    "GRIMOIRE_TASK_ID= PYTHONPATH=src python -m pytest tests/unit -q",
    "ruff check src && mypy src",
    "tsc --noEmit -p .",
    "cd src && pytest -q",
]


@pytest.mark.parametrize("agent", ["", "sous-agent-1"])
@pytest.mark.parametrize("template", LIBRES)
def test_un_brouillon_hors_projet_ou_une_verification_ne_compte_pas(
    lieux: tuple[Path, Path], template: str, agent: str
) -> None:
    root, out = lieux
    assert not _counts(root, template.format(out=out.as_posix()), agent=agent)


# ── Stop, de bout en bout ────────────────────────────────────────────────────


@pytest.fixture
def gouverne(lieux: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for var in ("GRIMOIRE_TASK_ID", "GRIMOIRE_ACTOR", "GRIMOIRE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GRIMOIRE_DONE_GATE"):
        monkeypatch.delenv(var, raising=False)
    root, out = lieux
    setup_standard_profile(root, profile_id="governed", task_id="bootstrap")
    registry = root / "_grimoire/standard/llm-provider-registry.yaml"
    registry.write_text(
        registry.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1), encoding="utf-8"
    )
    return root, out


def _hook(root: Path, event: HookEvent, **extra: Any) -> Any:
    payload = {"session_id": SESSION, "hook_event_name": event.value, "cwd": str(root), **extra}
    return run_hook(payload, host_id=HostId.CLAUDE_CODE_CLI, event=event, project_root=root)[1]


def _post(root: Path, tool: str, tool_input: dict[str, Any], agent: str = "") -> None:
    extra: dict[str, Any] = {"tool_name": tool, "tool_input": tool_input, "tool_response": {"exit_code": 0}}
    if agent:
        extra |= {"agent_id": agent, "agent_type": "general-purpose"}
    _hook(root, HookEvent.POST_TOOL_USE, **extra)


def _stop(root: Path) -> Any:
    return _hook(root, HookEvent.STOP, stop_hook_active=False)


def test_des_ecritures_illisibles_bloquent_le_stop(gouverne: tuple[Path, Path]) -> None:
    root, out = gouverne
    _post(root, "Bash", {"command": f"git apply {out.as_posix()}/p.patch"})
    _post(root, "Bash", {"command": "ruff check --fix src/ && ruff format src/"})
    _post(root, "Bash", {"command": "./regen.sh  # pytest"})
    _post(root, "Bash", {"command": "make gen"}, agent="sous-agent-1")
    _post(root, "Bash", {"command": "patch -p1 < x.patch"}, agent="sous-agent-1")
    assert session_mutations(root, SESSION) == 5
    assert _stop(root).outcome is Outcome.BLOCK


def test_quarante_ecritures_apres_le_refus_rebloquent(gouverne: tuple[Path, Path]) -> None:
    root, _ = gouverne
    _post(root, "Write", {"file_path": str(root / "src/a.py"), "content": "x"})
    assert _stop(root).outcome is Outcome.BLOCK
    for i in range(40):
        _post(root, "Write", {"file_path": str(root / f"src/f{i}.py"), "content": "x"})
    assert _stop(root).outcome is Outcome.BLOCK


def test_une_redirection_vers_le_brouillon_neutre_ne_compte_pas(gouverne: tuple[Path, Path]) -> None:
    root, out = gouverne
    _post(root, "Bash", {"command": f"cat > {out.as_posix()}/notes.md <<'EOF'\nvoir src/app.py\nEOF"})
    assert session_mutations(root, SESSION) == 0
