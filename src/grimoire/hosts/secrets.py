"""Credential paths, declared once for the two mechanisms that must agree.

An agent can reach a secret two ways, and each is caught by a different
mechanism:

- opening the file with a read tool — caught by the host's declarative
  permission table, at no runtime cost;
- naming it inside a shell command — caught by the ``pre_tool_use`` decision,
  which costs a process per call.

Both need the same list. When they were written separately they drifted
immediately: the regexes covered nine families, the deny globs six, and the
three missing ones (``.npmrc``, ``credentials.json``, ``service-account*.json``
among them) were silently unprotected on the declarative side. Declaring each
family once, with both of its forms, is what keeps that from happening again.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Accepted before a match: start of string, whitespace, ``=``, ``/``, a
#: quote, or a Windows separator *glued to a path* (``app\\.env``,
#: ``C:\\.env``). Patterns are tested against paths *and* command lines.
#: Issue #724: a backslash right after a quote or a space is a regex escape
#: (``grep -E '\\.env\\b'``), not a path segment, so it is not an anchor.
_BEFORE = r"(?:^|[\s=/'\"]|(?<=[A-Za-z0-9_.~)])\\)"
#: Accepted after a match: end of string, whitespace, quote, ``/``, ``:``, or a
#: Windows separator followed by a path character — ``\\b`` in a grep pattern
#: is an escape, not ``.env`` opening a directory (#724).
_AFTER = r"(?=$|[\s'\"/:]|\\(?=[A-Za-z0-9_.~]))"
#: A Windows separator that opens a real path segment (same rule as above).
_WIN_SEP = r"\\(?=[A-Za-z0-9_.~])"


@dataclass(frozen=True, slots=True)
class SecretRule:
    """One family of credential path, in both forms it must be expressed in."""

    name: str
    pattern: str
    """Regex for the runtime decision, matched against paths and command lines."""
    globs: tuple[str, ...]
    """Gitignore-style globs for a host's declarative permission table."""


SECRET_RULES: tuple[SecretRule, ...] = (
    SecretRule("dotenv", rf"{_BEFORE}\.env{_AFTER}", ("**/.env",)),
    # Issue #724: the suffix of an env *file* is lower-case by convention
    # (``.env.local``, ``.env.production``); ``(?-i:…)`` keeps it so under the
    # case-insensitive match, so a jq path like ``.env.CLAUDE_CODE_TMPDIR`` —
    # a field, not a file — is not a credential.
    SecretRule("dotenv-variant", rf"{_BEFORE}\.env\.(?-i:[a-z0-9_-]+){_AFTER}", ("**/.env.*",)),
    SecretRule("npmrc", rf"{_BEFORE}\.npmrc{_AFTER}", ("**/.npmrc",)),
    SecretRule("pypirc", rf"{_BEFORE}\.pypirc{_AFTER}", ("**/.pypirc",)),
    SecretRule(
        "ssh-key",
        rf"{_BEFORE}id_(?:rsa|ed25519|ecdsa){_AFTER}",
        ("**/id_rsa", "**/id_ed25519", "**/id_ecdsa"),
    ),
    # A *directory* named secret(s): the separator must open a path segment —
    # ``'secret\|recursive'`` in a grep pattern is a word, not a path (#724).
    SecretRule("secrets-dir", rf"{_BEFORE}secrets?(?:/|{_WIN_SEP})", ("**/secret/**", "**/secrets/**")),
    SecretRule(
        "key-material",
        r"\.(?:pem|p12|pfx|keystore|jks)" + _AFTER,
        ("**/*.pem", "**/*.p12", "**/*.pfx", "**/*.keystore", "**/*.jks"),
    ),
    SecretRule(
        "credentials-file",
        rf"{_BEFORE}credentials(?:\.json|\.yaml|\.yml)?{_AFTER}",
        ("**/credentials", "**/credentials.json", "**/credentials.yaml", "**/credentials.yml"),
    ),
    SecretRule(
        "service-account",
        rf"{_BEFORE}service-account[a-z0-9_-]*\.json{_AFTER}",
        ("**/service-account*.json",),
    ),
)


def secret_patterns() -> tuple[str, ...]:
    """Regexes for the runtime decision."""
    return tuple(rule.pattern for rule in SECRET_RULES)


def secret_read_globs() -> tuple[str, ...]:
    """Globs for a declarative permission table, deduplicated, order kept."""
    seen: list[str] = []
    for rule in SECRET_RULES:
        for glob in rule.globs:
            if glob not in seen:
                seen.append(glob)
    return tuple(seen)
