"""Regression for #426: an inline comment on a quoted scalar got corrupted.

``grimoire up`` (via ``cmd_setup.apply``) rewrote every ``user:`` key it knew
about unconditionally, line by line, with a ``.+`` regex that swallowed the
whole tail of the line — value *and* trailing comment — as "the value". That
string was then wrapped in a fresh pair of quotes, so::

    skill_level: "expert"  # beginner | intermediate | expert

became::

    skill_level: ""expert"  # beginner | intermediate | expert"

on every single ``up``, even when the value itself never changed, and even
though only ``skill_level`` was touched — every other line in the file,
comments included, must come out byte-identical.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.cli.cmd_setup import _apply_project_context, _split_scalar_and_comment, load_user_values

# A realistic ``project-context.yaml``: inline comments (quoted and unquoted
# scalars), a block comment, an inline list with a trailing comment, and a
# blank line — everything ``up``'s identity step must leave untouched except
# the ``user:`` keys it actually owns.
_RICH_CONTEXT = """project:
  name: "projet-test"
  stack: "python"  # détecté automatiquement

# Section identité — lue et propagée par `grimoire up`.
user:
  name: "Guilhem"
  language: "Français"
  document_language: "Français"
  skill_level: "expert"  # beginner | intermediate | expert — adapte la verbosité des agents
  metaphor: "forteresse"  # forteresse = sécurité first : remparts (firewall), douves (DMZ), sentinelles (monitoring)
  active: true  # bascule rapide

memory:
  backend: "auto"
  tags: ["a", "b"]  # liste inline avec commentaire
"""


def _write(tmp_path: Path, content: str) -> Path:
    root = tmp_path
    (root / "project-context.yaml").write_text(content, encoding="utf-8")
    return root


class TestIssue426Verbatim:
    """The exact before/after from the issue report, byte for byte."""

    def test_skill_level_survives_a_no_op_apply(self, tmp_path: Path) -> None:
        root = _write(tmp_path, _RICH_CONTEXT)
        pcy = root / "project-context.yaml"
        before = pcy.read_text(encoding="utf-8")

        vals = load_user_values(pcy)
        assert vals.user_skill_level == "expert"  # not '"expert"  # ...' — the comment must not leak into the value

        _apply_project_context(pcy, vals)

        after = pcy.read_text(encoding="utf-8")
        assert after == before, after

    def test_a_real_skill_level_change_keeps_its_comment(self, tmp_path: Path) -> None:
        root = _write(tmp_path, _RICH_CONTEXT)
        pcy = root / "project-context.yaml"

        vals = load_user_values(pcy)
        vals.user_skill_level = "beginner"
        _apply_project_context(pcy, vals)

        line = next(ln for ln in pcy.read_text(encoding="utf-8").splitlines() if ln.strip().startswith("skill_level:"))
        assert line == '  skill_level: "beginner"  # beginner | intermediate | expert — adapte la verbosité des agents'


class TestUnrelatedLinesAreByteIdentical:
    """``up`` may only touch the ``user:`` keys it manages — nothing else."""

    def test_only_the_changed_line_moves(self, tmp_path: Path) -> None:
        root = _write(tmp_path, _RICH_CONTEXT)
        pcy = root / "project-context.yaml"
        before_lines = pcy.read_text(encoding="utf-8").splitlines()

        vals = load_user_values(pcy)
        vals.user_name = "Quelqu'un d'autre"
        _apply_project_context(pcy, vals)

        after_lines = pcy.read_text(encoding="utf-8").splitlines()
        assert len(after_lines) == len(before_lines)
        changed = [
            (i, b, a) for i, (b, a) in enumerate(zip(before_lines, after_lines, strict=True)) if b != a
        ]
        assert [i for i, _b, _a in changed] == [next(i for i, ln in enumerate(before_lines) if ln.strip().startswith("name:") and i > 3)]
        assert changed[0][2] == '  name: "Quelqu\'un d\'autre"'


class TestReadKeyDoesNotAbsorbTheComment:
    """The regression's root cause: reading a commented scalar back."""

    def test_quoted_value_with_inline_comment(self) -> None:
        vals_text = 'skill_level: "expert"  # beginner | intermediate | expert\n'
        from grimoire.cli.cmd_setup import _read_key

        assert _read_key(vals_text, "skill_level") == "expert"

    def test_unquoted_value_with_inline_comment(self) -> None:
        from grimoire.cli.cmd_setup import _read_key

        assert _read_key("language: Français  # locale utilisateur\n", "language") == "Français"


class TestSplitScalarAndComment:
    """Unit coverage for the helper the fix introduces."""

    def test_quoted_scalar_with_comment(self) -> None:
        value, comment = _split_scalar_and_comment(' "expert"  # beginner | intermediate | expert')
        assert value == '"expert"'
        assert comment == "  # beginner | intermediate | expert"

    def test_unquoted_scalar_with_comment(self) -> None:
        value, comment = _split_scalar_and_comment(" true  # bascule rapide")
        assert value == "true"
        assert comment == "  # bascule rapide"

    def test_scalar_without_comment_round_trips(self) -> None:
        value, comment = _split_scalar_and_comment(' "Guilhem"')
        assert value == '"Guilhem"'
        assert comment == ""

    def test_hash_inside_quotes_is_not_a_comment(self) -> None:
        value, comment = _split_scalar_and_comment(' "a # b"  # real comment')
        assert value == '"a # b"'
        assert comment == "  # real comment"
