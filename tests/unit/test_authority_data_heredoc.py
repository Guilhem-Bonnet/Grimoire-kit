"""#1173 : le texte d'un heredoc est une donnée ; seule la cible de la redirection compte.

Un brief écrit par heredoc dans le scratchpad, qui cite la zone de contrôle dans son texte, n'est pas une
écriture dans la zone. Le chemin de la zone est construit par code, jamais écrit en dur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.hosts.decisions import authority_guard as ag
from grimoire.hosts.decisions.tool_facts import classify_tool

_ZONE = "/".join(("~", ".grimoire", "control"))  # noqa: FLY002 — chemin construit par code, jamais en dur
_JOURNAL = "mandates" + ".authority" + ".jsonl"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "home"
    (root / ".grimoire" / "control").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.chdir(work)


def _vector(command: str):  # type: ignore[no-untyped-def]
    return ag.forgery_vector(classify_tool("Bash", {"command": command}))


ALLOWED = [
    f"cat > scratch/brief.md <<'EOF'\nLa zone {_ZONE} est protégée.\nEOF",
    f"cat >> scratch/brief.md <<'EOF'\n{_ZONE}/cockpit-key\nEOF",
    f"tee scratch/brief.md <<'EOF'\n{_ZONE}\nEOF",
    f"cat <<'EOF' > scratch/brief.md\nvoir {_ZONE}\nEOF",
    f"cat > notes.md <<'EOF'\nsed -n 'w {_ZONE}/f' ; git clone /src {_ZONE}/x\nEOF",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_a_heredoc_to_a_file_outside_the_zone_is_allowed(command: str) -> None:
    assert ag.is_data_heredoc(command)
    assert _vector(command) is None


REFUSED_AND_REVOKED = [
    f"cat > {_ZONE}/{_JOURNAL} <<'EOF'\nx\nEOF",
    f"cat >> {_ZONE}/cockpit-key <<'EOF'\nx\nEOF",
    f"tee {_ZONE}/host-contracts.json <<'EOF'\nx\nEOF",
    f"cd {_ZONE} && cat > y <<'EOF'\nx\nEOF",
    f"cat > scratch/a <<'EOF'\n{_ZONE}\nEOF\ncat > {_ZONE}/y <<'EOF'\nx\nEOF",
]


@pytest.mark.parametrize("command", REFUSED_AND_REVOKED)
def test_a_redirection_into_the_zone_is_still_refused_and_revokes(command: str) -> None:
    assert not ag.is_data_heredoc(command)
    vector = _vector(command)
    assert vector is not None and vector.code == "control-zone" and vector.revokes is True


NOT_DATA = [
    # tag non quoté : le shell développe le corps
    f"cat > scratch/a <<EOF\n{_ZONE} $(date)\nEOF",
    # le corps est exécuté
    f"bash <<'EOF'\necho x > {_ZONE}/y\nEOF",
    f"python3 - <<'P'\nopen('{_ZONE}/y', 'w')\nP",
    f"cat <<'EOF' | bash\necho x > {_ZONE}/y\nEOF",
    f"cat > scratch/a.sh <<'EOF'\n{_ZONE}\nEOF\nbash scratch/a.sh",
    f"sudo tee scratch/a <<'EOF'\n{_ZONE}\nEOF",
    f"git commit -F - <<'EOF'\n{_ZONE}\nEOF",
]


@pytest.mark.parametrize("command", NOT_DATA)
def test_anything_but_a_plain_data_sink_stays_refused(command: str) -> None:
    assert not ag.is_data_heredoc(command)
    vector = _vector(command)
    assert vector is not None and vector.code == "control-zone"


def test_a_parse_error_is_never_an_authorisation() -> None:
    assert not ag.is_data_heredoc(f"cat > a <<'EOF'\n{_ZONE}")
    assert not ag.is_data_heredoc("")


def test_a_refusal_after_a_proven_write_says_the_mandate_was_revoked() -> None:
    vector = _vector(f"cat > {_ZONE}/{_JOURNAL} <<'EOF'\nx\nEOF")
    assert vector is not None and "révoqué" in vector.what
    mention = _vector(f"cat {_ZONE}/cockpit-key")
    assert mention is not None and mention.revokes is False and "pas révoqué" in mention.what
