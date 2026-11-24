"""Règles de caviardage : github_pat_, Bearer opaque, motif PEM linéaire."""

from __future__ import annotations

import time

from grimoire.memory.validation import redact_secrets


def test_github_fine_grained_pat_is_redacted() -> None:
    text, hits = redact_secrets("token github_pat_11ABCDEFG0abcdefghijkl_mnopqrstuvwxyz0123456789 fin")
    assert "github_pat_" not in text
    assert "github-pat" in hits


def test_opaque_bearer_is_redacted() -> None:
    text, hits = redact_secrets("Authorization: Bearer abcDEF123456xyz")
    assert "abcDEF123456xyz" not in text
    assert hits == ("bearer-token",)


def test_short_bearer_is_kept() -> None:
    assert redact_secrets("Bearer short")[0] == "Bearer short"


def test_bearer_jwt_stays_a_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    text, hits = redact_secrets(f"Authorization: Bearer {jwt}")
    assert jwt not in text
    assert hits == ("jwt",)


def test_pem_block_is_redacted() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"
    text, hits = redact_secrets(f"a {pem} b {pem} c")
    assert text == "a [redacted:private-key-block] b [redacted:private-key-block] c"
    assert hits == ("private-key-block",)


def test_redact_secrets_is_fast_on_trapped_pem_input() -> None:
    trap = "-----BEGIN RSA PRIVATE KEY-----" * (200_000 // 30)
    start = time.perf_counter()
    text, hits = redact_secrets(trap)
    assert time.perf_counter() - start < 0.1
    assert "BEGIN" not in text
    assert "private-key-block" in hits
