"""Path and template variable resolution for BMAD projects.

Resolves ``{project-root}``, ``{user_name}``, and other variables in
path strings and template strings.  All paths are resolved relative to
the project root.

Usage::

    resolver = PathResolver(Path("/my/project"))
    p = resolver.resolve_path("{project-root}/agents/my-agent.md")
    s = resolver.resolve_template("Hello {user_name}!", {"user_name": "Guilhem"})
"""

from __future__ import annotations

import re
from pathlib import Path

from bmad.core.exceptions import BmadConfigError

_VAR_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_-]*)\}")


class PathResolver:
    """Resolve paths and template variables relative to a project root."""

    def __init__(self, project_root: Path) -> None:
        self._root = project_root.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, raw: str, *, must_exist: bool = False) -> Path:
        """Resolve a path string, substituting ``{project-root}``.

        Relative paths are resolved against the project root.

        Raises :class:`BmadConfigError` if *must_exist* is ``True``
        and the resolved path does not exist.
        """
        substituted = raw.replace("{project-root}", str(self._root))
        path = Path(substituted)
        if not path.is_absolute():
            path = self._root / path
        resolved = path.resolve()
        if must_exist and not resolved.exists():
            raise BmadConfigError(f"Path does not exist: {resolved}")
        return resolved

    def resolve_template(self, template: str, context: dict[str, str]) -> str:
        """Substitute ``{var}`` placeholders in *template*.

        ``{project-root}`` is always available.  Unknown variables raise
        :class:`BmadConfigError`.
        """
        full_ctx = {"project-root": str(self._root), **context}

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in full_ctx:
                raise BmadConfigError(
                    f"Unknown variable '{{{key}}}' in template. "
                    f"Available: {sorted(full_ctx)}"
                )
            return full_ctx[key]

        return _VAR_PATTERN.sub(_replace, template)
