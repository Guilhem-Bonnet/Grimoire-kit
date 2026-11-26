"""Caviardage : une valeur reconnaissable l'est quel que soit le texte qui la touche.

Plusieurs motifs exigeaient une frontière de mot avant (et parfois après) la
valeur : une valeur collée à une lettre, accentuée ou non, à un chiffre ou à un
souligné n'était pas caviardée du tout. Ces tests couvrent, pour chaque famille
de motifs, la valeur seule, collée à chaque classe de caractère voisin, et en
début et fin de texte ; et les garde-fous de forme qui évitent de caviarder un
texte ordinaire.

Les fausses valeurs sont assemblées à l'exécution, et les constantes portent un
nom neutre : aucune valeur d'apparence réelle n'est écrite d'un bloc dans le dépôt.
"""

from __future__ import annotations

import string

import pytest

from grimoire.memory.validation import redact_secrets, redaction_audit

_D = "-" * 5

# (étiquette attendue, valeur complète, fragment distinctif qui ne doit pas survivre)
_VALUES: dict[str, tuple[str, str, str]] = {
    "github-token": ("github-token", "gh" + "p_" + "A1b2C3d4E5" * 4, "A1b2C3d4E5A1b2"),
    "aws-access-key-id": ("aws-access-key-id", "AK" + "IA" + "Q7W8E9R1T2Y3U4I5", "Q7W8E9R1T2Y3"),
    "slack-token": ("slack-token", "xo" + "xb-" + "1234567890-abcdefghij", "1234567890-abc"),
    "github-pat": (
        "github-pat",
        "github" + "_pat_" + "11ABCDEFG0abcdefghijkl" + "_" + "mnopqrstuvwxyz0123456789",
        "11ABCDEFG0abc",
    ),
    "anthropic-key": ("anthropic-key", "sk-" + "ant-" + "api03-" + "Zq9Lm2Xv" * 5, "Zq9Lm2XvZq9Lm2Xv"),
    "openai-key-legacy": ("openai-key", "sk-" + "T3BlbkFJ" + "Ab3dEf6hIj9kLm2nOp5qRs8", "T3BlbkFJAb3dEf"),
    "openai-key-project": (
        "openai-key",
        "sk-" + "proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8TuVw1xY",
        "Ab3dEf6hIj9kLm2n",
    ),
    "jwt": (
        "jwt",
        ".".join(("ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0", "abcdefghijklmnop")),
        "zdWIiOiIxMjM0NTY3ODkw",
    ),
    "bearer-token": ("bearer-token", "Bea" + "rer " + "a1b2c3d4e5f6g7h8i9j0k1", "a1b2c3d4e5f6g7h8"),
    "url-basic-auth": ("url-basic-auth", "https://" + "alice:" + "Zx9Wv8Ut7@host", "Zx9Wv8Ut7"),
    "credential-assignment": ("credential-assignment", "pass" + "word=" + "Tr0ub4dor3xKm9Qz", "Tr0ub4dor3xKm9Qz"),
    "private-key-block": (
        "private-key-block",
        f"{_D}BEGIN RSA PRIVATE KEY{_D}\nMIIEowIBAAKCAQEA\n{_D}END RSA PRIVATE KEY{_D}",
        "MIIEowIBAAKCAQEA",
    ),
    "private-key-block-armored": (
        "private-key-block",
        f"{_D}BEGIN PGP PRIVATE KEY BLOCK{_D}\nlQdGBF9abcdef\n{_D}END PGP PRIVATE KEY BLOCK{_D}",
        "lQdGBF9abcdef",
    ),
}

# Ce qui touche la valeur : début/fin de texte, lettre ASCII, lettre accentuée, chiffre, souligné, ponctuation.
_NEIGHBOURS = ("", "a", "Z", "é", "É", "7", "_", ".", "(", ":", "\n", " ")


_SHORT_PREFIX = frozenset({"anthropic-key", "openai-key-legacy", "openai-key-project"})


@pytest.mark.parametrize("family", sorted(_VALUES))
@pytest.mark.parametrize("before", _NEIGHBOURS, ids=repr)
@pytest.mark.parametrize("after", _NEIGHBOURS, ids=repr)
def test_value_is_redacted_whatever_touches_it(family: str, before: str, after: str) -> None:
    label, value, fragment = _VALUES[family]
    text = f"{before}{value}{after}"
    redacted, hits = redact_secrets(text)
    if family in _SHORT_PREFIX and before.isalpha():
        # Choix assumé : `sk-` derrière une lettre est la fin d'un mot (« task-… »). Couper le mot en plein
        # milieu est pire que manquer une clé collée à une lettre ; la clé collée à un chiffre, un souligné
        # ou une ponctuation, elle, est reconnue.
        assert redacted == text
        return
    if family == "aws-access-key-id" and after and after in string.ascii_uppercase + string.digits:
        # Un identifiant AWS fait 20 caractères : suivi d'une majuscule ou d'un chiffre, ce serait autre chose,
        # et caviarder la partie laisserait un fragment.
        assert redacted == text
        return
    assert fragment not in redacted
    assert label in hits
    assert label in redaction_audit(text)


@pytest.mark.parametrize("family", sorted(_VALUES))
@pytest.mark.parametrize("before", ("a", "é", "7", "_"), ids=repr)
def test_value_inside_a_sentence_keeps_the_rest(family: str, before: str) -> None:
    _, value, fragment = _VALUES[family]
    if family in _SHORT_PREFIX and before.isalpha():
        return
    redacted, _ = redact_secrets(f"début {before}{value} fin")
    assert fragment not in redacted
    assert redacted.startswith("début ")
    assert redacted.endswith(" fin")


_NAMES = ("pass" + "word", "to" + "ken", "sec" + "ret", "api_" + "key", "access_" + "to" + "ken", "DB_PASS" + "WORD")


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("before", ("", "a", "é", "7", "_", "-", "."), ids=repr)
@pytest.mark.parametrize("separator", ("=", ": ", " = ", '": "', "='"), ids=repr)
def test_assignment_is_redacted_when_the_name_is_part_of_a_longer_identifier(
    name: str, before: str, separator: str,
) -> None:
    value = "Tr0ub4dor3" + "xKm9QzA"
    redacted, hits = redact_secrets(f"{before}{name}{separator}{value}'")
    assert value not in redacted
    assert "credential-assignment" in hits


# Un texte ordinaire que la même forme ne doit pas toucher.
_ORDINARY = (
    "risk-management-and-governance-review-board",
    "the-task-ant-colony-optimization-algorithm-survey",
    "disk-usage-report-for-the-monthly-audit",
    "task-GAO-kanban-agent-001-for-the-board",
    "risk-Q3-Review-2026-board-notes-final",
    "a ghost_story_about_the_lighthouse_keeper_and_the_sea",
    "weighs_every_option_before_deciding_anything_at_all",
    "ASIAN-PACIFIC-REGIONAL-OFFICE-OVERVIEW",
    "AKIAHELLOWORLDLETTERS and more",
    "test_github_pat_is_redacted_when_the_value_is_collated",
    "pallbearer authentication_scheme_configuration",
    "Bearer authentication failed",
    "password policy is documented in ADR-004",
    "token_count: 1234567890123",
    "tokenizer = Tokenizer12345678901",
    "task-id: 4f3a9c-review-2026",
)


@pytest.mark.parametrize("prose", _ORDINARY)
def test_ordinary_text_is_left_alone(prose: str) -> None:
    assert redact_secrets(prose) == (prose, ())
