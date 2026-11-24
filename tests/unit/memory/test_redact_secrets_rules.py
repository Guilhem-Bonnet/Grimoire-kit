"""Règles de caviardage : github_pat_, Bearer opaque, motif PEM linéaire."""

from __future__ import annotations

import time

from grimoire.memory.validation import redact_secrets

# Fixtures assemblées à l'exécution : aucun secret d'apparence réelle en clair dans le dépôt.
_DASHES = "-" * 5
_PEM = f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}\nMIIE\n{_DASHES}END RSA PRIVATE KEY{_DASHES}"
_JWT = ".".join(("ey" + "J" + "hbGciOiJIUzI1NiJ9", "ey" + "J" + "zdWIiOiIxMjM0NTY3ODkwIn0", "abcdefghijklmnop"))
_PAT = "github" + "_pat_" + "11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789"


def test_github_fine_grained_pat_is_redacted() -> None:
    text, hits = redact_secrets(f"token {_PAT} fin")
    assert "github_pat_" not in text
    assert "github-pat" in hits


def test_opaque_bearer_is_redacted() -> None:
    text, hits = redact_secrets("Authorization: Bearer abcDEF123456xyz")
    assert "abcDEF123456xyz" not in text
    assert hits == ("bearer-token",)


def test_short_bearer_is_kept() -> None:
    assert redact_secrets("Bearer short")[0] == "Bearer short"


def test_bearer_prose_is_kept() -> None:
    for prose in ("Bearer authentication failed", "the bearer responsibilities"):
        assert redact_secrets(prose)[0] == prose


def test_bearer_with_colon_is_redacted() -> None:
    text, hits = redact_secrets("Bearer: abcDEF123456xyz")
    assert "abcDEF123456xyz" not in text
    assert hits == ("bearer-token",)


def test_bearer_jwt_stays_a_jwt() -> None:
    jwt = _JWT
    text, hits = redact_secrets(f"Authorization: Bearer {jwt}")
    assert jwt not in text
    assert hits == ("jwt",)


def test_pem_block_is_redacted() -> None:
    pem = _PEM
    text, hits = redact_secrets(f"a {pem} b {pem} c")
    assert text == "a [redacted:private-key-block] b [redacted:private-key-block] c"
    assert hits == ("private-key-block",)


def _best_of(text: str, runs: int = 5) -> tuple[float, str, tuple[str, ...]]:
    best = float("inf")
    out, hits = "", ()
    for _ in range(runs):
        start = time.perf_counter()
        out, hits = redact_secrets(text)
        best = min(best, time.perf_counter() - start)
    return best, out, hits


def test_redact_secrets_is_fast_on_trapped_pem_input() -> None:
    unit = f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}"
    small, _, _ = _best_of(unit * (50_000 // 30))
    best, text, hits = _best_of(unit * (200_000 // 30))
    # Coût linéaire, pas quadratique : une entrée quatre fois plus longue coûte environ quatre fois plus
    # (seize fois en quadratique). Un rapport plutôt qu'une durée seule : un runner partagé est lent, pas
    # quadratique (le seuil fixe de 100 ms cassait main à 130-240 ms). Le plafond large garde l'ordre de
    # grandeur : le motif d'origine prenait une dizaine de secondes.
    assert best < max(small, 0.001) * 10
    assert best < 2.0
    assert "BEGIN" not in text
    assert "private-key-block" in hits
