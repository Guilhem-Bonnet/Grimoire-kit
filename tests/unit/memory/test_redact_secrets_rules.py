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


def test_redact_secrets_is_fast_on_trapped_pem_input() -> None:
    trap = f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}" * (200_000 // 30)
    # Meilleur de cinq : mesure le coût de la fonction, pas la gigue d'un runner chargé.
    best = float("inf")
    for _ in range(5):
        start = time.perf_counter()
        text, hits = redact_secrets(trap)
        best = min(best, time.perf_counter() - start)
    assert best < 0.1
    assert "BEGIN" not in text
    assert "private-key-block" in hits
