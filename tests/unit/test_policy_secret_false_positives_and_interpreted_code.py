"""Issue #724 : politique `pre_tool_use` (Bash) — faux positifs « secret » et code interprété.

§1 : `.env` dans un chemin jq, `secret` dans un motif grep, un heredoc qui décrit
le bug — refusés comme accès à un secret alors que rien ne lit un secret.
§2 : `python3 - <<'EOF' … shutil.rmtree(…) … EOF` passait sans un mot alors que
`rm -rf` est refusé.

Critère de fait de l'issue : les commandes du §1 sont autorisées, `cat .env`,
`cat ".env"` et `cat config/.env.local` restent bloqués, l'exemple du §2 produit
au minimum un avertissement nommé.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.core.agentic_standard import setup_standard_profile
from grimoire.hosts.decisions import classify_tool, decide_tool_policy
from grimoire.hosts.decisions._shared import HookEvent, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import interpreted_bodies, interpreted_destructive_reason
from grimoire.hosts.runtime import render
from grimoire.policies.schemas import ActionKind, MutationClass

DOTENV = "." + "env"  # assemblé : la politique de l'hôte lit aussi cette commande de test


def _secret(command: str) -> str:
    return classify_tool("Bash", {"command": command}).secret_target


# ── §1 : ce qui n'est pas un chemin n'est pas un secret ─────────────────────


@pytest.mark.parametrize(
    "command",
    [
        f"jq '{DOTENV}.CLAUDE_CODE_TMPDIR = $t' ~/.claude/settings.json",
        f"grep -rn -E '\\{DOTENV}\\b|secret' src/grimoire/hosts",
        "grep -n -i 'secret\\|recursive' src/grimoire/hosts/decisions/tool_facts.py",
        "rg -n 'secrets?\\b' src",
        "git commit -m 'policy: the secret pattern over-matched'",
    ],
)
def test_words_in_patterns_and_jq_paths_are_not_secret_reads(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.secret_target == "", (command, facts.secret_target)
    assert facts.kind is not ActionKind.SECRET_ACCESS


def test_a_heredoc_that_documents_the_bug_is_data_not_a_read() -> None:
    command = (
        "gh issue create --title 'faux positif' --body-file - <<'EOF'\n"
        f"`cat {DOTENV}` est refusé, `cat config/{DOTENV}.local` aussi, et ~/.ssh/id_rsa.\n"
        "EOF\n"
    )
    assert _secret(command) == ""


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (f"cat {DOTENV}", DOTENV),
        (f'cat "{DOTENV}"', DOTENV),
        (f"cat config/{DOTENV}.local", DOTENV + ".local"),
        (f"cat {DOTENV}.production", DOTENV + ".production"),
        (f"CAT {DOTENV.upper()}", DOTENV),
        ("cat secrets/token.txt", "secrets/"),
        (f"type app\\{DOTENV}", DOTENV),
        ("cat ~/.ssh/id_rsa", "id_rsa"),
    ],
)
def test_real_secret_reads_stay_detected(command: str, expected: str) -> None:
    # Le fragment rapporté garde le séparateur qui l'ancre (`/.env.local`) — comme avant #724.
    assert expected in _secret(command)


def test_windows_paths_still_anchor_a_secret() -> None:
    assert classify_tool("Read", {"file_path": f"app\\{DOTENV}"}).secret_target
    assert classify_tool("Read", {"file_path": r"C:\proj\secrets\token.txt"}).secret_target


def test_a_secret_read_inside_interpreted_code_is_still_a_secret_read() -> None:
    command = f"python3 - <<'EOF'\nprint(open('{DOTENV}').read())\nEOF\n"
    assert _secret(command) == DOTENV


# ── §2 : le code interprété est inspecté, et nommé quand il ne l'est pas ────

RMTREE_HEREDOC = "python3 - <<'EOF'\nimport shutil; shutil.rmtree('/chemin')\nEOF\n"


def test_interpreted_bodies_find_the_heredoc_fed_to_an_interpreter() -> None:
    assert interpreted_bodies(RMTREE_HEREDOC) == (("python", "import shutil; shutil.rmtree('/chemin')\n"),)
    assert interpreted_bodies("cat <<'EOF' | node\nfs.rmSync('x')\nEOF\n")[0][0] == "javascript"
    assert interpreted_bodies("python -c 'import os; os.remove(\"f\")'") == (("python", 'import os; os.remove("f")'),)
    assert interpreted_bodies("node -e \"require('fs').rmSync('x', {recursive: true})\"")[0][0] == "javascript"
    # Un heredoc écrit dans un fichier est de la donnée, pas du code.
    assert interpreted_bodies("cat > notes.md <<'EOF'\nshutil.rmtree('/x')\nEOF\n") == ()
    assert interpreted_bodies("cat <<'EOF' > notes.md\nrm -rf /x\nEOF\n") == ()


@pytest.mark.parametrize(
    "command",
    [
        RMTREE_HEREDOC,
        "python3 - <<'EOF'\nfrom pathlib import Path\nPath('x').unlink()\nEOF\n",
        "python3 -c 'import os, shutil; shutil.rmtree(\"build\")'",
        "node -e \"require('fs').rmSync('dist', {recursive: true})\"",
        "node - <<'EOF'\nconst fs = require('fs'); fs.rmdirSync('dist');\nEOF\n",
        "perl -e 'unlink glob \"*.bak\"'",
        "ruby -e 'require \"fileutils\"; FileUtils.rm_rf(\"tmp\")'",
        "bash <<'EOF'\nrm -rf /chemin\nEOF\n",
        "python3 - <<'EOF'\nimport os; os.system('rm -rf /chemin')\nEOF\n",
    ],
)
def test_destructive_interpreted_code_is_classified_destructive(command: str) -> None:
    facts = classify_tool("Bash", {"command": command})
    assert facts.mutation is MutationClass.DESTRUCTIVE, command
    assert "interprété" in facts.destructive_reason, facts.destructive_reason


def test_a_python_string_that_only_mentions_rm_rf_is_not_destructive() -> None:
    command = "python3 - <<'EOF'\nopen('CHANGELOG.md', 'a').write('- fix: `rm -rf` is refused\\n')\nEOF\n"
    facts = classify_tool("Bash", {"command": command})
    assert facts.destructive_reason == ""
    assert facts.interpreted_code == "python"


def test_interpreted_destructive_reason_names_language_and_call() -> None:
    reason = interpreted_destructive_reason((("python", "shutil.rmtree('x')"),))
    assert reason == "recursive delete (shutil.rmtree) — code python interprété"
    assert interpreted_destructive_reason((("python", "print('hello')"),)) == ""


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", task_id="bootstrap")
    return tmp_path


def _bash(root: Path, command: str) -> HookInput:
    return HookInput(event=HookEvent.PRE_TOOL_USE, project_root=root, tool_name="Bash", tool_input={"command": command})


def test_the_rmtree_heredoc_is_refused_like_rm_rf(governed: Path) -> None:
    """L'exemple du §2 : au moins un avertissement nommé — ici le même refus que `rm -rf`."""
    plain = decide_tool_policy(_bash(governed, "rm -rf /chemin"))
    interpreted = decide_tool_policy(_bash(governed, RMTREE_HEREDOC))
    assert plain.outcome is Outcome.DENY
    assert interpreted.outcome is Outcome.DENY
    assert "shutil.rmtree" in interpreted.reason
    assert "code python interprété" in interpreted.reason
    assert interpreted.detail["interpreted_code"] == "python"


def test_uninspected_interpreted_code_is_named_when_allowed(governed: Path) -> None:
    command = "python3 - <<'EOF'\nprint('hello')\nEOF\n"
    hook = _bash(governed, command)
    decision = decide_tool_policy(hook)
    assert decision.outcome is Outcome.ALLOW
    assert decision.detail["interpreted_code"] == "python"
    assert "code python interprété" in decision.context
    rendered = render(decision, hook, HostId.CLAUDE_CODE_CLI)
    assert rendered["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "interprété" in rendered["systemMessage"]
    assert "interprété" in rendered["hookSpecificOutput"]["additionalContext"]


def test_a_plain_command_carries_no_interpreted_warning(governed: Path) -> None:
    hook = _bash(governed, "mkdir -p build && touch build/x")
    decision = decide_tool_policy(hook)
    assert decision.outcome is Outcome.ALLOW
    assert "interpreted_code" not in decision.detail
    assert decision.context == ""
    assert "systemMessage" not in render(decision, hook, HostId.CLAUDE_CODE_CLI)


def test_the_jq_and_grep_commands_are_allowed_in_a_governed_project(governed: Path) -> None:
    for command in (
        f"jq '{DOTENV}.CLAUDE_CODE_TMPDIR = $t' ~/.claude/settings.json",
        f"grep -rn -E '\\{DOTENV}\\b|secret' src/grimoire/hosts",
        "grep -n -i 'secret\\|recursive' src/grimoire/hosts/decisions/tool_facts.py",
    ):
        decision = decide_tool_policy(_bash(governed, command))
        assert decision.outcome is not Outcome.DENY, (command, decision.reason)
    for command in (f"cat {DOTENV}", f'cat "{DOTENV}"', f"cat config/{DOTENV}.local"):
        assert decide_tool_policy(_bash(governed, command)).outcome is Outcome.DENY, command
