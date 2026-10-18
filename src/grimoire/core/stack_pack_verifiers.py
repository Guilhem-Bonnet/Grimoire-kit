"""Vérificateurs mécaniques des packs de règles par stack (W6-08).

Une règle de pack n'est « vérifiée » que si un de ces vérificateurs, fermé et
déterministe, la lit sur le code du projet. Aucun sous-processus, aucun réseau,
aucun modèle : un parcours de fichiers et, pour Python, le module ``ast``.

Quatre natures de vérificateur (``kind``) :

- ``forbid_regex``  : aucun fichier de la portée ne contient le motif ;
- ``require_regex`` : au moins un fichier de la portée contient le motif ;
- ``require_file``  : au moins un fichier correspond à la portée ;
- ``python_ast``    : aucune occurrence d'un contrôle nommé, lu dans l'arbre
  syntaxique (``bare_except``, ``mutable_default_argument``, ``eval_call``).

Un verdict a trois états, jamais deux : ``pass``, ``fail`` et ``unevaluated``.
Une preuve qu'on n'a pas pu faire n'est pas un succès : fichier trop gros, non
UTF-8, syntaxe illisible, parcours tronqué, dossier illisible ou portée vide valent ``unevaluated``
(sauf si une violation a déjà été trouvée : ``fail`` tient). Un ``pass`` veut
dire que tous les fichiers de la portée ont été lus, qu'aucun dossier du projet
n'est resté illisible et que rien n'a été trouvé.

Limites connues : un motif est appliqué au texte entier du fichier (``re.M``),
sans garde contre une expression à rétro-traçage catastrophique : les motifs
sont ceux des packs livrés, relus avec eux. Le parcours ignore les dossiers
générés (:data:`EXCLUDED_DIRS`) et ne suit aucun lien symbolique.
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from grimoire.core.rule_resolution import scope_matches

__all__ = [
    "AST_CHECKS",
    "EXCLUDED_DIRS",
    "KINDS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "STATUS_FAIL",
    "STATUS_PASS",
    "STATUS_UNEVALUATED",
    "Corpus",
    "Finding",
    "Verdict",
    "Verifier",
    "scan_project",
    "verify",
]

KIND_FORBID_REGEX = "forbid_regex"
KIND_REQUIRE_REGEX = "require_regex"
KIND_REQUIRE_FILE = "require_file"
KIND_PYTHON_AST = "python_ast"
KINDS: tuple[str, ...] = (KIND_FORBID_REGEX, KIND_REQUIRE_REGEX, KIND_REQUIRE_FILE, KIND_PYTHON_AST)

STATUS_PASS = "pass"  # noqa: S105 — verdict d'un vérificateur
STATUS_FAIL = "fail"
STATUS_UNEVALUATED = "unevaluated"

#: Dossiers qui ne sont pas le code du projet : générés, installés ou propres à l'outillage.
EXCLUDED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".eggs", "site-packages", "build", "dist",
    "_grimoire", "_grimoire-output", "_grimoire-runtime", "_grimoire-runtime-output",
})

#: Garde-fous du parcours : au-delà, le verdict n'est plus un ``pass``.
MAX_FILES = 20000
MAX_FILE_BYTES = 1024 * 1024
MAX_FINDINGS = 20


@dataclass(frozen=True, slots=True)
class Verifier:
    """Un vérificateur déclaré par une règle de pack (déjà validé par le chargeur)."""

    kind: str
    paths: tuple[str, ...]
    pattern: str = ""
    check: str = ""


@dataclass(frozen=True, slots=True)
class Finding:
    """Une violation, avec de quoi la localiser."""

    path: str
    line: int
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.detail}" if self.line else f"{self.path} {self.detail}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Résultat d'un vérificateur sur un projet."""

    status: str
    findings: tuple[Finding, ...] = ()
    reason: str = ""


# ── Parcours du projet ────────────────────────────────────────────────────────


def scan_project(root: Path) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
    """Chemins relatifs (``/``) des fichiers du projet, triés, si le parcours est tronqué,
    et les dossiers qui n'ont pas pu être listés (relatifs, triés ; ``.`` pour la racine).

    Les liens symboliques ne sont ni suivis ni listés : un lien ne doit pas faire
    lire un fichier hors de la racine. Un dossier illisible n'est pas passé sous
    silence : ce qu'il contient n'a pas été vu, donc aucun ``pass`` ne peut le couvrir.
    """
    base = Path(root)
    found: list[str] = []
    unreadable: set[str] = set()

    def on_error(err: OSError) -> None:
        name = getattr(err, "filename", None)
        try:
            rel = Path(os.fsdecode(name)).relative_to(base).as_posix() if name else "."
        except ValueError:
            rel = str(name)
        unreadable.add(rel)

    for current, dirs, files in os.walk(base, onerror=on_error, followlinks=False):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (here / d).is_symlink())
        prefix = here.relative_to(base).as_posix()
        for name in sorted(files):
            if (here / name).is_symlink():
                continue
            found.append(name if prefix == "." else f"{prefix}/{name}")
            if len(found) >= MAX_FILES:
                return tuple(found), True, tuple(sorted(unreadable))
    return tuple(found), False, tuple(sorted(unreadable))


class Corpus:
    """Les fichiers d'un projet, lus une fois par évaluation (texte et arbre en cache)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.paths, self.truncated, self.unreadable_dirs = scan_project(self.root)
        self._text: dict[str, str | None] = {}
        self._tree: dict[str, ast.AST | None] = {}

    def gap(self) -> str:
        """Ce que le parcours n'a pas vu (tronqué, dossiers illisibles), ou chaîne vide."""
        reasons: list[str] = []
        if self.unreadable_dirs:
            shown = ", ".join(self.unreadable_dirs[:3]) + (" …" if len(self.unreadable_dirs) > 3 else "")
            reasons.append(f"{len(self.unreadable_dirs)} dossier(s) illisible(s) : {shown}")
        if self.truncated:
            reasons.append(f"parcours tronqué à {MAX_FILES} fichiers")
        return " ; ".join(reasons)

    def select(self, globs: Iterable[str]) -> tuple[str, ...]:
        patterns = tuple(globs)
        return tuple(p for p in self.paths if any(scope_matches(g, p) for g in patterns))

    def text(self, rel: str) -> str | None:
        """Texte UTF-8 du fichier, ou ``None`` quand il n'a pas pu être lu en entier."""
        if rel not in self._text:
            self._text[rel] = self._read(rel)
        return self._text[rel]

    def tree(self, rel: str) -> ast.AST | None:
        """Arbre syntaxique Python du fichier, ou ``None`` quand il n'est pas lisible."""
        if rel not in self._tree:
            self._tree[rel] = self._parse(rel)
        return self._tree[rel]

    def _read(self, rel: str) -> str | None:
        path = self.root / rel
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
            return path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _parse(self, rel: str) -> ast.AST | None:
        source = self.text(rel)
        if source is None:
            return None
        try:
            return ast.parse(source, filename=rel)
        except (SyntaxError, ValueError, RecursionError):
            return None


# ── Contrôles AST ─────────────────────────────────────────────────────────────

_AstCheck = Callable[[ast.AST], list[tuple[int, str]]]

_MUTABLE_FACTORIES = frozenset({"list", "dict", "set"})
_MUTABLE_LITERALS = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)


def _bare_except(tree: ast.AST) -> list[tuple[int, str]]:
    return [
        (node.lineno, "`except:` nu")
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is None
    ]


def _is_mutable_default(node: ast.expr) -> bool:
    if isinstance(node, _MUTABLE_LITERALS):
        return True
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _MUTABLE_FACTORIES


def _mutable_default_argument(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        args = node.args
        defaults = [*args.defaults, *(d for d in args.kw_defaults if d is not None)]
        out.extend((d.lineno, "valeur par défaut mutable") for d in defaults if _is_mutable_default(d))
    return out


def _eval_call(tree: ast.AST) -> list[tuple[int, str]]:
    return [
        (node.lineno, "appel de `eval`")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "eval"
    ]


AST_CHECKS: dict[str, _AstCheck] = {
    "bare_except": _bare_except,
    "mutable_default_argument": _mutable_default_argument,
    "eval_call": _eval_call,
}


# ── Verdicts ──────────────────────────────────────────────────────────────────


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _incomplete(corpus: Corpus, unread: list[str]) -> str:
    """Pourquoi un « rien trouvé » ne prouve pas l'absence, ou chaîne vide quand il la prouve."""
    reasons: list[str] = []
    if unread:
        shown = ", ".join(unread[:3]) + (" …" if len(unread) > 3 else "")
        reasons.append(f"{len(unread)} fichier(s) illisible(s) (trop gros, non UTF-8 ou syntaxe invalide) : {shown}")
    gap = corpus.gap()
    if gap:
        reasons.append(gap)
    return " ; ".join(reasons)


def _scan_findings(
    corpus: Corpus, scoped: tuple[str, ...], finder: Callable[[str], list[Finding] | None]
) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    unread: list[str] = []
    for rel in scoped:
        got = finder(rel)
        if got is None:
            unread.append(rel)
        else:
            findings.extend(got)
    return findings, unread


def _forbid(corpus: Corpus, verifier: Verifier) -> Verdict:
    pattern = re.compile(verifier.pattern, re.MULTILINE)

    def finder(rel: str) -> list[Finding] | None:
        text = corpus.text(rel)
        if text is None:
            return None
        return [Finding(rel, _line_of(text, m.start()), f"motif interdit « {m.group(0)[:60]} »") for m in pattern.finditer(text)]

    return _conclude(corpus, verifier, finder)


def _python_ast(corpus: Corpus, verifier: Verifier) -> Verdict:
    check = AST_CHECKS[verifier.check]

    def finder(rel: str) -> list[Finding] | None:
        tree = corpus.tree(rel)
        if tree is None:
            return None
        return [Finding(rel, line, detail) for line, detail in check(tree)]

    return _conclude(corpus, verifier, finder)


def _conclude(corpus: Corpus, verifier: Verifier, finder: Callable[[str], list[Finding] | None]) -> Verdict:
    scoped = corpus.select(verifier.paths)
    if not scoped:
        return Verdict(STATUS_UNEVALUATED, reason=f"aucun fichier dans la portée {', '.join(verifier.paths)} : rien n'a été lu")
    findings, unread = _scan_findings(corpus, scoped, finder)
    if findings:
        return Verdict(STATUS_FAIL, tuple(findings[:MAX_FINDINGS]))
    why = _incomplete(corpus, unread)
    return Verdict(STATUS_UNEVALUATED, reason=why) if why else Verdict(STATUS_PASS)


def _require_file(corpus: Corpus, verifier: Verifier) -> Verdict:
    if corpus.select(verifier.paths):
        return Verdict(STATUS_PASS)
    if gap := corpus.gap():
        return Verdict(STATUS_UNEVALUATED, reason=gap)
    return Verdict(STATUS_FAIL, (Finding(", ".join(verifier.paths), 0, "fichier absent"),))


def _require_regex(corpus: Corpus, verifier: Verifier) -> Verdict:
    scoped = corpus.select(verifier.paths)
    if not scoped:
        if gap := corpus.gap():
            return Verdict(STATUS_UNEVALUATED, reason=gap)
        return Verdict(STATUS_FAIL, (Finding(", ".join(verifier.paths), 0, "fichier absent"),))
    pattern = re.compile(verifier.pattern, re.MULTILINE)
    unread: list[str] = []
    for rel in scoped:
        text = corpus.text(rel)
        if text is None:
            unread.append(rel)
        elif pattern.search(text):
            return Verdict(STATUS_PASS)
    why = _incomplete(corpus, unread)
    if why:
        return Verdict(STATUS_UNEVALUATED, reason=why)
    return Verdict(STATUS_FAIL, tuple(Finding(rel, 0, f"motif requis absent « {verifier.pattern} »") for rel in scoped[:MAX_FINDINGS]))


_DISPATCH: dict[str, Callable[[Corpus, Verifier], Verdict]] = {
    KIND_FORBID_REGEX: _forbid,
    KIND_REQUIRE_REGEX: _require_regex,
    KIND_REQUIRE_FILE: _require_file,
    KIND_PYTHON_AST: _python_ast,
}


def verify(corpus: Corpus, verifier: Verifier) -> Verdict:
    """Le verdict d'un vérificateur sur un corpus. ``ValueError`` si la nature est inconnue."""
    handler = _DISPATCH.get(verifier.kind)
    if handler is None:
        raise ValueError(f"nature de vérificateur inconnue : {verifier.kind!r}")
    return handler(corpus, verifier)

