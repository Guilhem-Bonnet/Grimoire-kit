"""Caviardage : jamais moins que l'ancienne version, jamais en plein mot, toujours linéaire.

Trois garde-fous durables pour les règles « par forme » (seconde série) :

- **jamais moins** : une copie figée des anciens motifs (avant la seconde série)
  sert d'étalon ; sur un corpus ciblé, la nouvelle sortie s'obtient de l'ancienne
  en remplaçant des étendues par des marqueurs, jamais en en retirant ;
- **jamais en plein mot** : un nom n'est pas tronqué, une valeur n'est pas laissée
  en fragment, et ce qui n'est pas une valeur sensible n'est pas touché ;
- **linéaire par construction** : aucun lookahead ou lookbehind ne porte de
  quantificateur non borné, et la recherche du chiffre des affectations parcourt
  chaque caractère un nombre borné de fois (comptage, pas durée).

Fausses valeurs assemblées à l'exécution ; constantes sans vocabulaire sensible.
"""

from __future__ import annotations

import importlib
import random
import re
import string
from typing import Any

import pytest

from grimoire.memory import validation
from grimoire.memory.validation import normalize_for_storage, redact_secrets, redaction_audit

# ── Étalon : les anciens motifs, figés (état de main avant la seconde série) ──────────────────────────────────

_OLD_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{12,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer-token", re.compile(
        r"(?i)\bBearer:?\s+(?:(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}|[A-Za-z0-9._~+/=-]{20,})")),
    ("url-basic-auth", re.compile(r"(?<=://)[^\s/:@]+:[^\s/:@]{6,}(?=@)")),
    ("credential-assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?key|auth[_-]?token|"
        r"password|passwd|secret|token)\b\s*[:=]\s*[\"']?(?=[^\s\"']*\d)"
        r"[A-Za-z0-9_\-+/=.~]{12,}[\"']?"
    )),
)
_OLD_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")


def _old_redact(text: str) -> str:
    out = normalize_for_storage(text)[0]
    for label, pattern in _OLD_RULES:
        if label == "private-key-block":
            parts: list[str] = []
            pos = 0
            while (start := pattern.search(out, pos)) is not None:
                end = _OLD_PEM_END.search(out, start.end())
                parts += [out[pos:start.start()], f"[redacted:{label}]"]
                if end is None:
                    pos = len(out)
                    break
                pos = end.end()
            out = "".join(parts) + out[pos:]
        else:
            out = pattern.sub(f"[redacted:{label}]", out)
    return out


_MARK = "\x00"


def _covers_at_least(old: str, new: str) -> bool:
    """``new`` s'obtient de ``old`` en remplaçant des étendues par des marqueurs."""
    marker = re.compile(r"\[redacted:[a-z-]+\]")
    pieces = marker.sub(_MARK, new).split(_MARK)
    return re.fullmatch(".*".join(re.escape(piece) for piece in pieces), marker.sub(_MARK, old), re.DOTALL) is not None


# ── Corpus ciblé ──────────────────────────────────────────────────────────────────────────────────────────────

_RND = random.Random(1418)
_LETTERS = string.ascii_letters
_ALNUM = string.ascii_letters + string.digits


def _rnd(alphabet: str, size: int) -> str:
    return "".join(_RND.choice(alphabet) for _ in range(size))


_NAME_PW = "pass" + "word"
_NAME_TK = "to" + "ken"
_NAME_SC = "sec" + "ret"
_NAME_AK = "api_" + "key"
_DASHES = "-" * 5


def _targeted() -> list[str]:
    value = _rnd(_ALNUM, 18)
    cases = [
        # Premier chiffre au-delà de 256 caractères : l'ancien module caviardait, jamais moins.
        _NAME_PW + "=" + _rnd(_LETTERS, 300) + "9",
        "https://h/x?" + _NAME_PW + "=" + _rnd(_LETTERS, 16) + "&q=" + "b" * 300 + "&p=1",
        _NAME_TK + "=" + _rnd(_LETTERS, 20) + ";" + _rnd(_LETTERS, 260) + "7",
        _NAME_TK + "=" + _rnd(_LETTERS, 20) + ";" + _rnd(_LETTERS, 200) + "7",
        _NAME_SC + ": " + _rnd(_LETTERS, 600) + "5 suite",
        # Fin de bloc d'un AUTRE type au milieu d'un bloc : le bloc ne se ferme pas dessus.
        f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}\nMIIEpA" + _rnd(_ALNUM, 40) + f"\n{_DASHES}END PGP PRIVATE KEY BLOCK{_DASHES}\n"
        + _rnd(_ALNUM, 40) + f"\n{_DASHES}END RSA PRIVATE KEY{_DASHES}\napres",
        f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}\n" + _rnd(_ALNUM, 40) + f"\n{_DASHES}END EC PRIVATE KEY{_DASHES}\n"
        + _rnd(_ALNUM, 40) + f"\n{_DASHES}END RSA PRIVATE KEY{_DASHES}\napres",
        f"{_DASHES}BEGIN PRIVATE KEY{_DASHES}\n" + _rnd(_ALNUM, 40) + f"\n{_DASHES}END PRIVATE KEY{_DASHES}\napres",
        # Formes courantes.
        '{"' + _NAME_PW + '": "' + value + '1"}',
        _NAME_AK + ":\n  " + value + "2",
        "https://user:" + value + "@host/x",
        "Authorization: Bearer " + _rnd(_LETTERS, 24),
        "X-Api-" + "Key: " + value + "3",
        _NAME_SC + "='" + value + "4'",
    ]
    names = (_NAME_PW, "passwd", _NAME_SC, _NAME_TK, _NAME_AK, "apikey", "secret-key", "access_key", "auth_token")
    separators = (":", "=", " = ", ": ", ":\n  ", '":"', "': '", '="', "\t=\t")
    prefixes = ("", "x", "db_", "é", "_", "1", "(", '"', "https://h/?", "-", "GITHUB_")
    suffixes = ("", '"', "'", " suite", "&a=1", ";" + "z" * 200 + "5", ";" + "z" * 400 + "5")
    for _ in range(900):
        body = _RND.choice((_rnd(_ALNUM + "-_.+/=~", _RND.randint(8, 400)), _rnd(_LETTERS, _RND.randint(8, 400)) + _RND.choice(("", "1"))))
        cases.append(_RND.choice(prefixes) + _RND.choice(names) + _RND.choice(separators) + body + _RND.choice(suffixes))
    return cases


@pytest.mark.parametrize("text", _targeted(), ids=lambda text: text[:24].encode("unicode_escape").decode())
def test_never_redacts_less_than_before(text: str) -> None:
    assert _covers_at_least(_old_redact(text), redact_secrets(text)[0])


def test_block_is_not_closed_by_a_foreign_end() -> None:
    body = _rnd(_ALNUM, 40)
    tail = _rnd(_ALNUM, 40)
    text = (
        f"{_DASHES}BEGIN RSA PRIVATE KEY{_DASHES}\n{body}\n{_DASHES}END PGP PRIVATE KEY BLOCK{_DASHES}\n{tail}\n"
        f"{_DASHES}END RSA PRIVATE KEY{_DASHES}\napres"
    )
    assert redact_secrets(text)[0] == "[redacted:private-key-block]\napres"


def test_unterminated_block_still_redacts_to_the_end() -> None:
    text = f"avant\n{_DASHES}BEGIN EC PRIVATE KEY{_DASHES}\n" + _rnd(_ALNUM, 40)
    assert redact_secrets(text)[0] == "avant\n[redacted:private-key-block]"


def test_assignment_value_far_digit_is_still_redacted() -> None:
    value = _rnd(_LETTERS, 300) + "9"
    redacted, hits = redact_secrets(_NAME_PW + "=" + value)
    assert value[:40] not in redacted
    assert hits == ("credential-assignment",)


# ── Jamais en plein mot, jamais sur autre chose qu'une valeur sensible ───────────────────────────────────────

# Du texte ordinaire que la seconde série ne doit pas toucher : la sortie est celle de l'ancienne version.
_LEFT_AS_IS = (
    "access_" + "to" + "ken = get_" + "to" + "ken_v2_from_environment()",
    _NAME_AK + " = settings.api_key_v2_default",
    "SECRET_" + "KEY = config.secret_key_2026",
    "budget." + _NAME_TK + " = 1000000000000",
    "max_" + _NAME_TK + ": 1700000000000",
    '"last_' + _NAME_TK + '": "2026-11-26T10:00:00Z"',
    "last_" + _NAME_TK + "=2026-11-26T10:00:00+00:00",
    "next_" + _NAME_TK + ": 2026-11-26",
    "db_" + _NAME_PW + " = fetchCredentialsV2FromVault(env)",
    "x_" + _NAME_SC + ": true",
    "task-V1StGXR8Z5jdHi6BmyT0Ab",
    "desk-TFQXEFAjowBo2i4BoQTQdX",
    "mask-Q3Review2026BoardNotesFinalXyz",
    "01ARZ3NDEKTSV4RRFFQ69G5FAV ASIAN2026REPORTABCDEFG",
)


@pytest.mark.parametrize("text", _LEFT_AS_IS)
def test_ordinary_text_is_not_touched_by_the_shape_rules(text: str) -> None:
    # Certains de ces textes sont déjà caviardés par l'ancienne version (nom sans préfixe) : la seconde série
    # n'y ajoute rien, et ne touche pas ceux que l'ancienne laissait.
    assert redact_secrets(text)[0] == _old_redact(text)


def test_a_redaction_never_cuts_the_name() -> None:
    value = "Tr0ub4dor3" + "xKm9Qz!"
    out, _ = redact_secrets("db_" + _NAME_PW + "=" + value + " fin")
    assert out == "db_" + _NAME_PW + "=[redacted:credential-assignment] fin"
    out, _ = redact_secrets("HTTP" + "Bearer " + "a1b2c3d4e5f6g7h8i9j0k1")
    assert out == "HTTPBearer [redacted:bearer-token]"
    out, _ = redact_secrets('{"x_' + _NAME_TK + '": "' + value + '"}')
    assert out == '{"x_' + _NAME_TK + '": "[redacted:credential-assignment]"}'


def test_short_prefix_at_the_end_of_a_word_is_not_cut() -> None:
    word = "task" + "-I12345678Y" + "githubabcdefghij"
    assert redact_secrets(word)[0] == word
    # Choix assumé : derrière une lettre, `sk-` est la fin d'un mot. Collé à un chiffre, un souligné ou une
    # ponctuation, il est reconnu.
    assert redact_secrets("1" + word[2:])[0] == "1[redacted:openai-key]"


def test_redaction_audit_agrees_with_the_redaction() -> None:
    for text in _LEFT_AS_IS:
        assert bool(redaction_audit(text)) == (_old_redact(text) != normalize_for_storage(text)[0])
    assert redaction_audit("db_" + _NAME_PW + "=" + "Tr0ub4dor3xKm9Qz") == ("credential-assignment",)


# ── Linéaire par construction ─────────────────────────────────────────────────────────────────────────────────

_UNBOUNDED_REPEATS = frozenset({"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"})


def _assertion_with_unbounded_repeat(items: Any, *, inside: bool = False) -> bool:
    """Vrai si un lookahead ou un lookbehind contient un quantificateur sans borne haute."""
    maxrepeat = importlib.import_module("re._constants").MAXREPEAT
    for op, arg in items:
        name = str(op)
        if name in _UNBOUNDED_REPEATS:
            _, high, sub = arg
            if (inside and high == maxrepeat) or _assertion_with_unbounded_repeat(sub, inside=inside):
                return True
        elif name in ("ASSERT", "ASSERT_NOT"):
            if _assertion_with_unbounded_repeat(arg[1], inside=True):
                return True
        elif name == "SUBPATTERN":
            if _assertion_with_unbounded_repeat(arg[-1], inside=inside):
                return True
        elif name == "BRANCH" and any(_assertion_with_unbounded_repeat(sub, inside=inside) for sub in arg[1]):
            return True
    return False


@pytest.mark.parametrize(
    ("label", "pattern"),
    [(label, pattern) for label, pattern, _, _ in validation._SHAPE_RULES],
    ids=lambda value: value if isinstance(value, str) else value.pattern[:30],
)
def test_shape_rules_have_no_unbounded_assertion(label: str, pattern: re.Pattern[str]) -> None:
    parsed = importlib.import_module("re._parser").parse(pattern.pattern, pattern.flags)
    assert not _assertion_with_unbounded_repeat(parsed), f"{label} : lookahead/lookbehind non borné (quadratique)"


def _has_unbounded_repeat(items: Any) -> bool:
    maxrepeat = importlib.import_module("re._constants").MAXREPEAT
    for op, arg in items:
        name = str(op)
        if name in _UNBOUNDED_REPEATS:
            if arg[1] == maxrepeat or _has_unbounded_repeat(arg[2]):
                return True
        elif (name == "SUBPATTERN" and _has_unbounded_repeat(arg[-1])) or (
            name == "BRANCH" and any(_has_unbounded_repeat(sub) for sub in arg[1])
        ):
            return True
    return False


@pytest.mark.parametrize("name", ["_NOT_A_VALUE_CALL", "_NOT_A_VALUE_IDENT"])
def test_not_a_value_patterns_have_only_bounded_quantifiers(name: str) -> None:
    pattern: re.Pattern[str] = getattr(validation, name)
    assert not _has_unbounded_repeat(importlib.import_module("re._parser").parse(pattern.pattern, pattern.flags))


def test_the_structural_check_catches_an_unbounded_lookahead() -> None:
    parsed = importlib.import_module("re._parser").parse(r"x(?=[a-z]*\d)[a-z]{12,}", 0)
    assert _assertion_with_unbounded_repeat(parsed)
    bounded = importlib.import_module("re._parser").parse(r"x(?=[a-z]{0,60}\d)[a-z]{12,}", 0)
    assert not _assertion_with_unbounded_repeat(bounded)


_HOSTILE = (
    ("repeated-name", (_NAME_PW + "=") * 6000),
    ("short-values-then-digit", (_NAME_PW + "=a,") * 6000 + "1"),
    ("long-values", (_NAME_PW + "=" + "a" * 14 + ",") * 3000),
    ("quoted-names", ('"' + _NAME_TK + '":') * 6000),
    ("spaces-after-name", _NAME_TK + " " * 40000),
    # Les deux entrées que décrivait CodeQL (py/polynomial-redos) : une longue suite d'une même lettre.
    ("many-capital-A", _NAME_PW + "=" + "A" * 40000 + "1"),
    ("many-small-a", _NAME_PW + "=" + "a" * 40000 + "1"),
    ("many-names-one-run",("x_" + _NAME_SC + "=" + "k" * 13) * 3000 + "7"),
)


@pytest.mark.parametrize(("name", "text"), _HOSTILE, ids=[name for name, _ in _HOSTILE])
def test_assignment_scan_reads_each_character_a_bounded_number_of_times(
    name: str, text: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scans: list[validation._RunScan] = []

    class _Counting(validation._RunScan):
        __slots__ = ()

        def __init__(self, source: str) -> None:
            super().__init__(source)
            scans.append(self)

    monkeypatch.setattr(validation, "_RunScan", _Counting)
    redact_secrets(text)
    assert scans, name
    # Deux séries, deux recherches (chiffre, fin de suite) chacune amortie : jamais plus de quelques passes.
    assert sum(scan.scanned for scan in scans) <= 4 * len(scans) * (len(text) + 1)
