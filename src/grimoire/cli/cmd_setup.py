"""``grimoire setup`` — synchronise user configuration across all config files.

Source of truth: ``project-context.yaml``

Target files (when they exist):
  - ``_bmad/{bmm,core,cis,tea,bmb}/config.yaml``
  - ``_bmad/_memory/config.yaml``
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


# ── Fields map ────────────────────────────────────────────────────────────────

_COMMON = {
    "user_name": "user_name",
    "communication_language": "communication_language",
    "document_output_language": "document_output_language",
}

_BMM_EXTRA = {
    "project_name": "project_name",
    "user_skill_level": "user_skill_level",
}

_MODULES = ["bmm", "core", "cis", "tea", "bmb"]

# ── Check / Apply helpers ─────────────────────────────────────────────────────


def _check_file(path: Path, vals: UserValues, module: str) -> list[ConfigDiff]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    diffs: list[ConfigDiff] = []
    rel = str(path)
    fields = dict(_COMMON)
    if module == "bmm":
        fields.update(_BMM_EXTRA)
    for attr, key in fields.items():
        cur = _read_key(text, key)
        exp = getattr(vals, attr)
        if cur is not None and cur != exp:
            diffs.append(ConfigDiff(file=rel, key=key, current=cur, expected=exp))
    return diffs


def _apply_file(path: Path, vals: UserValues, module: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    original = text
    fields = dict(_COMMON)
    if module == "bmm":
        fields.update(_BMM_EXTRA)
    for attr, key in fields.items():
        cur = _read_key(text, key)
        exp = getattr(vals, attr)
        if cur is not None and cur != exp:
            text = _update_key(text, key, exp)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


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
    replacements = [
        (r"(\*\*Project\*\*:\s*).+", vals.project_name),
        (r"(\*\*User\*\*:\s*).+", vals.user_name),
        (r"(\*\*Communication Language\*\*:\s*).+", vals.communication_language),
        (r"(\*\*Document Output Language\*\*:\s*).+", vals.document_output_language),
        (r"(\*\*User Skill Level\*\*:\s*).+", vals.user_skill_level),
    ]
    updated = text
    for pat, value in replacements:
        updated = re.sub(pat, lambda m, v=value: m.group(1) + v, updated)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────


def check(project_root: Path, vals: UserValues) -> SetupResult:
    """Audit all config files against *vals* — pure read, no writes."""
    result = SetupResult()
    bmad = project_root / "_bmad"
    for mod in _MODULES:
        result.diffs.extend(_check_file(bmad / mod / "config.yaml", vals, mod))
    result.diffs.extend(_check_file(bmad / "_memory" / "config.yaml", vals, "_memory"))
    result.diffs.extend(_check_copilot(project_root / ".github" / "copilot-instructions.md", vals))
    return result


def apply(project_root: Path, vals: UserValues) -> SetupResult:
    """Write *vals* into every target file, return a report."""
    result = SetupResult()
    bmad = project_root / "_bmad"

    for mod in _MODULES:
        p = bmad / mod / "config.yaml"
        if not p.exists():
            result.skipped_files.append(f"_bmad/{mod}/config.yaml")
            continue
        if _apply_file(p, vals, mod):
            result.updated_files.append(f"_bmad/{mod}/config.yaml")

    mem = bmad / "_memory" / "config.yaml"
    if mem.exists():
        if _apply_file(mem, vals, "_memory"):
            result.updated_files.append("_bmad/_memory/config.yaml")
    else:
        result.skipped_files.append("_bmad/_memory/config.yaml")

    ci = project_root / ".github" / "copilot-instructions.md"
    if ci.exists():
        if _apply_copilot(ci, vals):
            result.updated_files.append(".github/copilot-instructions.md")
    else:
        result.skipped_files.append(".github/copilot-instructions.md")

    # Post-apply verification
    result.diffs = check(project_root, vals).diffs
    return result
