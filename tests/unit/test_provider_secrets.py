"""Magasin léger de jetons API (``~/.env-store/``) — issue #310 suite.

``HOME`` est isolé sur ``tmp_path`` pour chaque test : ce module écrit hors
du projet, dans le profil de l'utilisateur, et ne doit jamais toucher le
``~/.env-store`` réel de la machine qui lance la suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.providers.secrets import env_for_provider, list_secrets, remove_secret, set_secret, store_dir


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))


def test_set_secret_writes_file_and_index(tmp_path: Path) -> None:
    path = set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-test-123")

    assert path == store_dir() / "deepseek.env"
    assert path.read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=sk-test-123\n"
    entries = list_secrets()
    assert len(entries) == 1
    assert entries[0].provider == "deepseek"
    assert entries[0].env_var == "DEEPSEEK_API_KEY"


def test_set_secret_restricts_file_permissions(tmp_path: Path) -> None:
    path = set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-test-123")

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600
    assert (store_dir().stat().st_mode & 0o777) == 0o700


def test_set_secret_rejects_blank_value(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="vide"):
        set_secret("deepseek", "DEEPSEEK_API_KEY", "   ")


def test_set_secret_twice_updates_same_var_without_duplicate_entry(tmp_path: Path) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-first")
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-second")

    entries = list_secrets()
    assert len(entries) == 1
    assert env_for_provider("deepseek") == {"DEEPSEEK_API_KEY": "sk-second"}


def test_set_secret_two_vars_same_provider_coexist(tmp_path: Path) -> None:
    set_secret("openai", "OPENAI_API_KEY", "sk-a")
    set_secret("openai", "OPENAI_ORG_ID", "org-b")

    assert env_for_provider("openai") == {"OPENAI_API_KEY": "sk-a", "OPENAI_ORG_ID": "org-b"}
    assert {e.env_var for e in list_secrets()} == {"OPENAI_API_KEY", "OPENAI_ORG_ID"}


def test_env_for_provider_without_secret_returns_empty_dict(tmp_path: Path) -> None:
    assert env_for_provider("never-configured") == {}


def test_list_secrets_without_store_returns_empty_tuple(tmp_path: Path) -> None:
    assert list_secrets() == ()


def test_remove_secret_single_var_keeps_others(tmp_path: Path) -> None:
    set_secret("openai", "OPENAI_API_KEY", "sk-a")
    set_secret("openai", "OPENAI_ORG_ID", "org-b")

    remove_secret("openai", "OPENAI_ORG_ID")

    assert env_for_provider("openai") == {"OPENAI_API_KEY": "sk-a"}
    assert {e.env_var for e in list_secrets()} == {"OPENAI_API_KEY"}


def test_remove_secret_without_var_drops_whole_provider_file(tmp_path: Path) -> None:
    set_secret("openai", "OPENAI_API_KEY", "sk-a")
    set_secret("openai", "OPENAI_ORG_ID", "org-b")

    remove_secret("openai")

    assert env_for_provider("openai") == {}
    assert list_secrets() == ()
    assert not (store_dir() / "openai.env").exists()


def test_set_secret_never_leaks_value_into_index_file(tmp_path: Path) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-should-not-appear-in-index")

    index_text = (store_dir() / "index.json").read_text(encoding="utf-8")
    assert "sk-should-not-appear-in-index" not in index_text


def test_env_file_skips_blank_comment_and_malformed_lines(tmp_path: Path) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-a")
    path = store_dir() / "deepseek.env"
    path.write_text(path.read_text(encoding="utf-8") + "\n# un commentaire\n\nligne-sans-egal\n", encoding="utf-8")

    assert env_for_provider("deepseek") == {"DEEPSEEK_API_KEY": "sk-a"}


def test_list_secrets_tolerates_corrupted_index_json(tmp_path: Path) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", "sk-a")
    (store_dir() / "index.json").write_text("{ceci n'est pas du json", encoding="utf-8")

    assert list_secrets() == ()


def test_remove_secret_unknown_var_is_a_noop(tmp_path: Path) -> None:
    set_secret("openai", "OPENAI_API_KEY", "sk-a")

    remove_secret("openai", "NE_EXISTE_PAS")

    assert env_for_provider("openai") == {"OPENAI_API_KEY": "sk-a"}
