"""Standard artifacts: policies follow the kit, decisions stay with the project.

``_grimoire/standard/`` mixes two things that must not share a fate — policies
the kit ships (rule packs, mission brief) and records the project owns (waivers,
scores, task board). Before the generation manifest, ``standard init`` froze
both (``if dst.exists(): skip``) and ``--force`` flattened both.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.agentic_standard import STANDARD_PROFILE_FILE, setup_standard_profile
from grimoire.core.standard_generation import is_project_owned, load_generation_manifest


def _init(root: Path, **kwargs: object) -> object:
    return setup_standard_profile(
        root, profile_id="starter", task_id="bootstrap",
        project_name="demo", **kwargs,  # type: ignore[arg-type]
    )


def _first_tracked(root: Path) -> str:
    manifest = load_generation_manifest(root)
    assert manifest, "expected the generation manifest to be populated"
    return next(k for k in sorted(manifest) if k.endswith(".md"))


class TestGenerationManifest:
    def test_first_generation_records_what_it_wrote(self, tmp_path: Path) -> None:
        _init(tmp_path)
        manifest = load_generation_manifest(tmp_path)
        assert manifest
        assert str(STANDARD_PROFILE_FILE) in manifest

    def test_untouched_artifact_is_not_project_owned(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        assert not is_project_owned(tmp_path, tracked)

    def test_edited_artifact_becomes_project_owned(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        (tmp_path / tracked).write_text("my own content", encoding="utf-8")
        assert is_project_owned(tmp_path, tracked)

    def test_unknown_artifact_is_assumed_project_owned(self, tmp_path: Path) -> None:
        # Nothing recorded: the safe answer is "the project's", never "the kit's".
        assert is_project_owned(tmp_path, "_grimoire/standard/waivers.yaml")


class TestRefresh:
    def test_refresh_updates_an_untouched_artifact(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        target = tmp_path / tracked
        # Simulate the project running an older kit: content differs from the
        # current template, but the manifest still matches what was written.
        stale = "stale content from an older version\n"
        target.write_text(stale, encoding="utf-8")
        from grimoire.core.standard_generation import _artifact_digest, save_generation_manifest
        save_generation_manifest(tmp_path, {tracked: _artifact_digest(target)})

        _init(tmp_path, refresh=True)
        assert target.read_text(encoding="utf-8") != stale

    def test_refresh_preserves_an_edited_artifact(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        target = tmp_path / tracked
        target.write_text("my compliance decision", encoding="utf-8")

        _init(tmp_path, refresh=True)
        assert target.read_text(encoding="utf-8") == "my compliance decision"

    def test_without_refresh_nothing_existing_is_rewritten(self, tmp_path: Path) -> None:
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        target = tmp_path / tracked
        target.write_text("older content", encoding="utf-8")

        _init(tmp_path)
        assert target.read_text(encoding="utf-8") == "older content"

    def test_refresh_is_idempotent(self, tmp_path: Path) -> None:
        _init(tmp_path)
        _init(tmp_path, refresh=True)
        result = _init(tmp_path, refresh=True)
        assert result.written == []  # type: ignore[attr-defined]


class TestDateStamp:
    def test_daily_date_stamp_does_not_look_like_an_edit(self, tmp_path: Path) -> None:
        # The renderer stamps today's date; a naive byte comparison would call
        # every artifact "changed" once a day and defeat the manifest.
        _init(tmp_path)
        tracked = _first_tracked(tmp_path)
        content = (tmp_path / tracked).read_text(encoding="utf-8")
        if "- Date:" in content:
            stamped = content.replace("- Date:", "- Date: 1999-01-01", 1)
            (tmp_path / tracked).write_text(stamped, encoding="utf-8")
            _init(tmp_path, refresh=True)
            assert not is_project_owned(tmp_path, tracked)
