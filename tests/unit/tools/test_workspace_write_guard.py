"""La garde d'écriture commune (ADR-008 D3) : jeton, origine, journal, fail-closed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from grimoire.tools import request_guard
from grimoire.tools.request_guard import SESSION_HEADER
from grimoire.tools.workspace_write_guard import (
    cockpit_journal_path,
    guard_write,
    journal_event,
    state_fingerprint,
)

if TYPE_CHECKING:
    from email.message import Message


class _H(dict[str, str]):
    """En-têtes minimaux : le contrat ``get`` d'``email.message.Message``."""

    def get(self, name: str, default: str | None = None, /) -> str | None:  # type: ignore[override]
        return super().get(name, default)


def _h(values: dict[str, str]) -> Message[str, str]:
    return cast("Message[str, str]", _H(values))


def _ok_headers() -> Message[str, str]:
    return _h({SESSION_HEADER: request_guard.session_token(), "Content-Type": "application/json"})


def _lines(journal: Path) -> list[dict[str, object]]:
    return [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines()]


# ── Verdicts ────────────────────────────────────────────────────────────────


def test_une_ecriture_legitime_passe_et_est_journalisee(tmp_path: Path) -> None:
    journal = tmp_path / "runtime" / "write-guard.jsonl"
    verdict = guard_write(
        _ok_headers(), 80, method="POST", route="/api/x", journal=journal, with_body=True
    )
    assert verdict is None
    (line,) = _lines(journal)
    assert line["verdict"] == "acceptée"
    assert line["route"] == "/api/x"
    assert line["method"] == "POST"
    assert "ts" in line and "code" not in line


def test_le_jeton_absent_est_refuse_avec_la_marche_a_suivre(tmp_path: Path) -> None:
    journal = tmp_path / "write-guard.jsonl"
    verdict = guard_write(
        _h({"Content-Type": "application/json"}),
        80,
        method="POST",
        route="/api/x",
        journal=journal,
        with_body=True,
    )
    assert verdict is not None
    code, reason = verdict
    assert code == 403
    assert reason.startswith("jeton de session requis")
    assert "rechargez la page" in reason
    assert SESSION_HEADER in reason
    (line,) = _lines(journal)
    assert line["verdict"] == "refusée"
    assert line["code"] == 403
    assert line["reason"] == reason


def test_une_origine_etrangere_est_refusee_et_journalisee(tmp_path: Path) -> None:
    journal = tmp_path / "write-guard.jsonl"
    headers = _h({**_ok_headers(), "Origin": "https://evil.example"})
    verdict = guard_write(
        headers, 80, method="POST", route="/api/x", journal=journal, with_body=True
    )
    assert verdict == (403, "origine non autorisée")
    (line,) = _lines(journal)
    assert line["reason"] == "origine non autorisée"


def test_un_pair_hors_loopback_est_refuse(tmp_path: Path) -> None:
    journal = tmp_path / "write-guard.jsonl"
    verdict = guard_write(
        _ok_headers(),
        80,
        method="POST",
        route="/api/x",
        journal=journal,
        with_body=True,
        peer="203.0.113.7",
    )
    assert verdict == (403, "cockpit local only")
    (line,) = _lines(journal)
    assert line["verdict"] == "refusée"


def test_un_pair_loopback_passe(tmp_path: Path) -> None:
    journal = tmp_path / "write-guard.jsonl"
    verdict = guard_write(
        _ok_headers(),
        80,
        method="PUT",
        route="/api/x",
        journal=journal,
        with_body=True,
        peer="127.0.0.1",
    )
    assert verdict is None


# ── Journal : ajout seul, fail-closed ───────────────────────────────────────


def test_le_journal_est_en_ajout_seul(tmp_path: Path) -> None:
    journal = tmp_path / "write-guard.jsonl"
    for _ in range(3):
        guard_write(_ok_headers(), 80, method="POST", route="/api/x", journal=journal, with_body=True)
    assert len(_lines(journal)) == 3


def test_un_journal_non_inscriptible_refuse_l_ecriture(tmp_path: Path) -> None:
    """Fail-closed : pas de journal, pas d'effet — message actionnable."""
    blocker = tmp_path / "runtime"
    blocker.write_text("pas un dossier")  # mkdir(parents) échouera
    journal = blocker / "write-guard.jsonl"
    verdict = guard_write(
        _ok_headers(), 80, method="POST", route="/api/x", journal=journal, with_body=True
    )
    assert verdict is not None
    code, reason = verdict
    assert code == 503
    assert "journal de la garde non inscriptible" in reason
    assert str(journal) in reason


def test_un_refus_reste_un_refus_meme_sans_journal(tmp_path: Path) -> None:
    blocker = tmp_path / "runtime"
    blocker.write_text("pas un dossier")
    verdict = guard_write(
        _h({"Content-Type": "application/json"}),
        80,
        method="POST",
        route="/api/x",
        journal=blocker / "write-guard.jsonl",
        with_body=True,
    )
    assert verdict is not None
    assert verdict[0] == 403  # le refus d'origine, pas le 503


# ── Point d'extension apply (ADR-008) ───────────────────────────────────────


def test_journal_event_leve_si_le_journal_est_ferme(tmp_path: Path) -> None:
    blocker = tmp_path / "runtime"
    blocker.write_text("pas un dossier")
    with pytest.raises(OSError):
        journal_event(blocker / "j.jsonl", {"kind": "plan"})


def test_journal_event_ajoute_une_ligne_horodatee(tmp_path: Path) -> None:
    journal = tmp_path / "actions" / "journal.jsonl"
    journal_event(journal, {"kind": "plan", "fingerprint": state_fingerprint(b"etat")})
    (line,) = _lines(journal)
    assert line["kind"] == "plan"
    assert line["fingerprint"] == state_fingerprint(b"etat")
    assert "ts" in line


def test_l_empreinte_d_etat_est_stable_et_discriminante() -> None:
    assert state_fingerprint(b"a") == state_fingerprint(b"a")
    assert state_fingerprint(b"a") != state_fingerprint(b"b")
    assert len(state_fingerprint(b"")) == 64


def test_le_journal_du_cockpit_vit_dans_son_dossier_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GRIMOIRE_COCKPIT_HOME", str(tmp_path / "ck"))
    assert cockpit_journal_path() == tmp_path / "ck" / "write-guard.jsonl"
