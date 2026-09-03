"""``grimoire setup`` — synchronise user configuration across project config files.

Source of truth: ``project-context.yaml`` — ``apply`` writes it first, then the
mirrors, then verifies the mirrors against the file it just wrote. Verifying
against the values held in memory reported "in sync" over a divergence the
command had itself created.

Target files (when they exist):
  - ``.github/copilot-instructions.md``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class UserValues:
    """Flat bag of user-editable values."""

    project_name: str = ""
    user_name: str = ""
    communication_language: str = "Français"
    document_output_language: str = "Français"
    user_skill_level: str = "expert"


@dataclass
class ConfigDiff:
    file: str
    key: str
    current: str
    expected: str


@dataclass
class SetupResult:
    diffs: list[ConfigDiff] = field(default_factory=list)
    updated_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_synced(self) -> bool:
        return len(self.diffs) == 0


# ── YAML helpers (simple, no PyYAML dependency) ──────────────────────────────


def _read_key(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    val = m.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1]
    return val


def _update_key(text: str, key: str, new_value: str) -> str:
    pat = re.compile(rf"^({re.escape(key)}:\s*).+$", re.MULTILINE)
    return pat.sub(lambda m: m.group(1) + new_value, text)


# ── Extract UserValues from project-context.yaml ─────────────────────────────


def load_user_values(path: Path) -> UserValues:
    """Parse ``project-context.yaml`` into a flat :class:`UserValues`."""
    text = path.read_text(encoding="utf-8")
    vals = UserValues()
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if line and not line[0].isspace() and ":" in s:
            section = s.split(":")[0].strip()
            continue
        if section == "project" and s.startswith("name:"):
            vals.project_name = _read_key(s, "name") or ""
        elif section == "user":
            if s.startswith("name:"):
                vals.user_name = _read_key(s, "name") or ""
            elif s.startswith("language:"):
                vals.communication_language = _read_key(s, "language") or "Français"
            elif s.startswith("document_language:"):
                vals.document_output_language = _read_key(s, "document_language") or "Français"
            elif s.startswith("skill_level:"):
                vals.user_skill_level = _read_key(s, "skill_level") or "expert"
    return vals


# ── Source of truth ──────────────────────────────────────────────────────────

_USER_KEYS: tuple[tuple[str, str], ...] = (
    ("name", "user_name"),
    ("language", "communication_language"),
    ("document_language", "document_output_language"),
    ("skill_level", "user_skill_level"),
)


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    """Line range ``[start, end)`` of a top-level ``section:`` block, or ``None``."""
    start = next((i for i, ln in enumerate(lines) if ln.rstrip() == f"{section}:"), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and not ln[0].isspace() and not ln.lstrip().startswith("#"):
            break
        end += 1
    return start, end


def _apply_project_context(path: Path, vals: UserValues) -> bool:
    """Write the user values into the ``user:`` section of *path*.

    Scoped to that section on purpose: ``project:`` carries a ``name`` key
    too, and a file-wide ``^name:`` substitution would rename the project.
    A missing section is created after ``project:``; the rest of the file —
    comments, ordering, other sections — is left as it was.
    """
    if not path.is_file():
        return False
    original = path.read_text(encoding="utf-8")
    lines = original.split("\n")
    wanted = {key: getattr(vals, attr) for key, attr in _USER_KEYS if getattr(vals, attr)}
    if not wanted:
        return False

    span = _section_span(lines, "user")
    if span is None:
        project = _section_span(lines, "project")
        insert_at = project[1] if project else len(lines)
        block = ["user:"] + [f'  {key}: "{value}"' for key, value in wanted.items()] + [""]
        lines[insert_at:insert_at] = block
    else:
        start, end = span
        seen: set[str] = set()
        for i in range(start + 1, end):
            stripped = lines[i].strip()
            for key in wanted:
                if stripped.startswith(f"{key}:"):
                    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                    lines[i] = f'{indent}{key}: "{wanted[key]}"'
                    seen.add(key)
        missing = [f'  {key}: "{value}"' for key, value in wanted.items() if key not in seen]
        tail = end
        while tail > start + 1 and not lines[tail - 1].strip():
            tail -= 1
        lines[tail:tail] = missing

    updated = "\n".join(lines)
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


# ── Check / Apply helpers ─────────────────────────────────────────────────────


def _check_copilot(path: Path, vals: UserValues) -> list[ConfigDiff]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    diffs: list[ConfigDiff] = []
    checks = [
        (r"\*\*Project\*\*:\s*(.+)", "Project", vals.project_name),
        (r"\*\*User\*\*:\s*(.+)", "User", vals.user_name),
        (r"\*\*Communication Language\*\*:\s*(.+)", "Communication Language", vals.communication_language),
        (r"\*\*Document Output Language\*\*:\s*(.+)", "Document Output Language", vals.document_output_language),
        (r"\*\*User Skill Level\*\*:\s*(.+)", "User Skill Level", vals.user_skill_level),
    ]
    for pat, field_name, expected in checks:
        m = re.search(pat, text)
        if m and m.group(1).strip() != expected:
            diffs.append(ConfigDiff(file=str(path), key=field_name, current=m.group(1).strip(), expected=expected))
    return diffs


def _apply_copilot(path: Path, vals: UserValues) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    # ``[^\S\n]*`` — horizontal whitespace only. With ``\s*`` the match ran
    # past the end of the line, so an empty value swallowed the newline and
    # overwrote the *next* field. Only visible once a field could legitimately
    # be empty, which is why it survived this long.
    replacements = [
        (r"(\*\*Project\*\*:[^\S\n]*).*", vals.project_name),
        (r"(\*\*User\*\*:[^\S\n]*).*", vals.user_name),
        (r"(\*\*Communication Language\*\*:[^\S\n]*).*", vals.communication_language),
        (r"(\*\*Document Output Language\*\*:[^\S\n]*).*", vals.document_output_language),
        (r"(\*\*User Skill Level\*\*:[^\S\n]*).*", vals.user_skill_level),
    ]
    updated = text
    for pat, value in replacements:
        # An empty value means the project simply does not declare that field —
        # not that it wants the field blanked. Writing it through erased
        # identity a human had put in the file by hand.
        if not value:
            continue

        def replace_value(match: re.Match[str], replacement: str = value) -> str:
            return match.group(1) + replacement

        updated = re.sub(pat, replace_value, updated)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────


def check(project_root: Path, vals: UserValues) -> SetupResult:
    """Audit project config files against *vals* — pure read, no writes."""
    result = SetupResult()
    result.diffs.extend(_check_copilot(project_root / ".github" / "copilot-instructions.md", vals))
    return result


def apply(project_root: Path, vals: UserValues) -> SetupResult:
    """Write *vals* into the source of truth, then every mirror, return a report."""
    result = SetupResult()

    pcy = project_root / "project-context.yaml"
    if _apply_project_context(pcy, vals):
        result.updated_files.append("project-context.yaml")

    ci = project_root / ".github" / "copilot-instructions.md"
    if ci.exists():
        if _apply_copilot(ci, vals):
            result.updated_files.append(".github/copilot-instructions.md")
    else:
        result.skipped_files.append(".github/copilot-instructions.md")

    # Post-apply verification — against the file, not the values in memory.
    # That is the only comparison that can catch a write that did not land.
    truth = load_user_values(pcy) if pcy.is_file() else vals
    result.diffs = check(project_root, truth).diffs
    return result
