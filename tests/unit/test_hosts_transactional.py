"""D-05 : ``host sync`` est transactionnel, et la propriété d'un fichier se prouve par empreinte.

Trois promesses, chacune avec son test de panne :

- une panne au milieu d'une synchronisation laisse l'arbre identique octet à octet ;
- un fichier généré puis édité à la main (marqueur conservé) est un conflit, pas un écrasement ;
- le contenu de l'utilisateur autour du bloc géré d'``AGENTS.md`` / ``GEMINI.md`` survit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grimoire.bridges.schemas import HostId
from grimoire.hosts.collect import build_surface
from grimoire.hosts.emitters import EmitPlan, EmittedFile, JsonMerge, apply_plan, emitter_for
from grimoire.hosts.emitters.base import MANAGED_MARKER, managed_header

AGENT_DIR = Path("_grimoire/_config/custom/agents")


def _write_agent(root: Path, name: str, body: str) -> None:
    (root / AGENT_DIR).mkdir(parents=True, exist_ok=True)
    (root / AGENT_DIR / f"{name}.md").write_text(
        f'---\nname: "{name}"\ndescription: "{name} — rôle de test"\n---\n{body}\n',
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    _write_agent(tmp_path, "concierge", "Tu tries et tu routes.")
    _write_agent(tmp_path, "scribe", "Tu rédiges la documentation.")
    return tmp_path


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Chaque fichier (octets) et chaque dossier (``None``) de l'arbre."""
    tree: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        tree[rel] = path.read_bytes() if path.is_file() else None
    return tree


def _managed(name: str, body: str = "corps") -> EmittedFile:
    return EmittedFile(relpath=Path(name), content=f"{managed_header('.md')}\n\n{body}\n")


# ── transaction ──────────────────────────────────────────────────────────────


def test_a_failure_on_the_third_file_leaves_the_tree_identical(tmp_path: Path) -> None:
    (tmp_path / "blocked").write_text("je suis un fichier, pas un dossier\n", encoding="utf-8")
    kept = tmp_path / "kept.md"
    kept.write_text(_managed("kept.md", "version précédente").content, encoding="utf-8")
    plan = EmitPlan(
        host_id=HostId.CLAUDE_CODE_CLI,
        files=(
            _managed("kept.md", "version nouvelle"),
            _managed("fresh/deep/second.md"),
            _managed("blocked/third.md"),  # impossible : « blocked » est un fichier
            _managed("never-reached.md"),
        ),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(OSError):
        apply_plan(plan, tmp_path)

    assert _snapshot(tmp_path) == before


def test_a_failure_in_a_later_host_rolls_back_the_earlier_hosts_too(tmp_path: Path) -> None:
    from grimoire.hosts.emitters.base import apply_plans

    (tmp_path / "blocked").write_text("fichier\n", encoding="utf-8")
    first = EmitPlan(host_id=HostId.CLAUDE_CODE_CLI, files=(_managed("a/one.md"), _managed("a/two.md")))
    second = EmitPlan(host_id=HostId.GITHUB_COPILOT, files=(_managed("b/ok.md"), _managed("blocked/x.md")))
    before = _snapshot(tmp_path)

    with pytest.raises(OSError):
        apply_plans([first, second], tmp_path)

    assert _snapshot(tmp_path) == before


def test_a_failing_json_merge_rolls_back_the_files_written_before_it(tmp_path: Path) -> None:
    def boom(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("fusion cassée")

    plan = EmitPlan(
        host_id=HostId.CLAUDE_CODE_CLI,
        files=(_managed("one.md"), _managed("two.md")),
        merges=(JsonMerge(relpath=Path("settings.json"), merge=boom),),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(RuntimeError, match="fusion cassée"):
        apply_plan(plan, tmp_path)

    assert _snapshot(tmp_path) == before


def test_a_dry_run_touches_nothing_and_reports_the_same_verdicts(project: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    plan = emitter.plan(build_surface(project), project)
    before = _snapshot(project)

    dry = apply_plan(plan, project, dry_run=True)

    assert _snapshot(project) == before
    real = apply_plan(plan, project)
    assert dry.written == real.written


# ── propriété par empreinte ──────────────────────────────────────────────────


def test_an_edited_generated_file_is_a_conflict_not_an_overwrite(project: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    target = project / ".claude/agents/concierge.md"
    edited = target.read_text(encoding="utf-8").replace("Applique-les sans les résumer.", "Fais ce que je veux.")
    assert "Fais ce que je veux." in edited and MANAGED_MARKER in edited  # en-tête conservé, corps changé
    target.write_text(edited, encoding="utf-8")

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    label = ".claude/agents/concierge.md"
    assert label in result.skipped
    assert result.conflicts[label] == "edited"
    assert not result.ok
    assert target.read_text(encoding="utf-8") == edited

    forced = apply_plan(emitter.plan(build_surface(project), project), project, force=True)
    assert label in forced.written
    assert "Fais ce que je veux." not in target.read_text(encoding="utf-8")


def test_a_hand_written_file_is_a_foreign_conflict(project: Path) -> None:
    target = project / ".claude/agents/concierge.md"
    target.parent.mkdir(parents=True)
    target.write_text("écrit à la main\n", encoding="utf-8")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    assert result.conflicts[".claude/agents/concierge.md"] == "foreign"
    assert "foreign" in json.dumps(result.to_dict()["conflicts"])


def test_a_file_from_the_previous_version_is_migrated_without_conflict(project: Path) -> None:
    """Avant D-05 aucun fichier ne portait d'empreinte : le marqueur seul disait « c'est au kit »."""
    target = project / ".claude/agents/concierge.md"
    target.parent.mkdir(parents=True)
    target.write_text(f"{managed_header('.md')}\n\nancien rendu du kit\n", encoding="utf-8")
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    assert ".claude/agents/concierge.md" in result.written
    assert ".claude/agents/concierge.md" not in result.skipped
    assert re.search(rf"{MANAGED_MARKER} sha256=[0-9a-f]{{16}}", target.read_text(encoding="utf-8"))
    again = apply_plan(emitter.plan(build_surface(project), project), project)
    assert ".claude/agents/concierge.md" in again.unchanged


def test_a_stamped_file_is_unchanged_on_resync(project: Path) -> None:
    emitter = emitter_for(HostId.CLAUDE_CODE_CLI)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    snapshot = _snapshot(project)

    again = apply_plan(emitter.plan(build_surface(project), project), project)

    assert not again.written and not again.skipped
    assert _snapshot(project) == snapshot


# ── bloc géré d'AGENTS.md / GEMINI.md ────────────────────────────────────────


@pytest.mark.parametrize(("host", "entry"), [(HostId.CODEX, "AGENTS.md"), (HostId.GEMINI_CLI, "GEMINI.md")])
def test_user_content_around_the_managed_block_survives_a_sync(project: Path, host: HostId, entry: str) -> None:
    emitter = emitter_for(host)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    path = project / entry
    path.write_text(f"# Règles de mon équipe\n\nNe jamais déployer le vendredi.\n\n{path.read_text(encoding='utf-8')}"
                    "\n## Notes perso\n\nÀ garder.\n", encoding="utf-8")
    _write_agent(project, "archiviste", "Tu ranges les archives.")

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    text = path.read_text(encoding="utf-8")
    assert entry in result.written
    assert text.startswith("# Règles de mon équipe\n\nNe jamais déployer le vendredi.\n")
    assert text.rstrip().endswith("## Notes perso\n\nÀ garder.")
    assert "archiviste" in text  # le bloc, lui, a bien été régénéré
    assert text.count("<!-- grimoire:begin") == 1


def test_an_edit_inside_the_managed_block_is_a_conflict_and_the_surroundings_stay(project: Path) -> None:
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    path = project / "AGENTS.md"
    edited = "avant\n" + path.read_text(encoding="utf-8").replace("concierge", "concierge-modifié") + "après\n"
    path.write_text(edited, encoding="utf-8")

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    assert result.conflicts["AGENTS.md"] == "edited"
    assert path.read_text(encoding="utf-8") == edited
    forced = apply_plan(emitter.plan(build_surface(project), project), project, force=True)
    text = path.read_text(encoding="utf-8")
    assert "AGENTS.md" in forced.written
    assert text.startswith("avant\n") and text.endswith("après\n")
    assert "concierge-modifié" not in text


def test_a_hand_written_entrypoint_is_never_replaced_and_force_appends_the_block(project: Path) -> None:
    path = project / "AGENTS.md"
    path.write_text("# Mon AGENTS.md\n\nContenu à moi.\n", encoding="utf-8")
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None

    result = apply_plan(emitter.plan(build_surface(project), project), project)
    assert result.conflicts["AGENTS.md"] == "foreign"
    assert path.read_text(encoding="utf-8") == "# Mon AGENTS.md\n\nContenu à moi.\n"

    apply_plan(emitter.plan(build_surface(project), project), project, force=True)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Mon AGENTS.md\n\nContenu à moi.\n")
    assert "concierge" in text


def test_an_entrypoint_from_the_previous_version_is_migrated_to_a_block(project: Path) -> None:
    path = project / "AGENTS.md"
    path.write_text(f"{managed_header('.md')}\n\n# ancien catalogue\n", encoding="utf-8")
    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None

    result = apply_plan(emitter.plan(build_surface(project), project), project)

    assert "AGENTS.md" in result.written and "AGENTS.md" not in result.skipped
    assert "ancien catalogue" not in path.read_text(encoding="utf-8")
    assert "<!-- grimoire:begin" in path.read_text(encoding="utf-8")


def test_the_cursor_rule_stays_a_whole_file_so_its_frontmatter_stays_first(project: Path) -> None:
    emitter = emitter_for(HostId.CURSOR)
    assert emitter is not None
    apply_plan(emitter.plan(build_surface(project), project), project)
    text = (project / ".cursor/rules/grimoire.mdc").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "<!-- grimoire:begin" not in text


def test_pruning_never_deletes_an_entrypoint_that_carries_user_content(project: Path) -> None:
    from grimoire.hosts.emitters import owned_managed_paths

    emitter = emitter_for(HostId.CODEX)
    assert emitter is not None
    plan = emitter.plan(build_surface(project), project)
    apply_plan(plan, project)
    assert project / "AGENTS.md" in owned_managed_paths(plan, project)  # bloc seul : au kit

    path = project / "AGENTS.md"
    path.write_text("à moi\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    assert project / "AGENTS.md" not in owned_managed_paths(plan, project)


# ── revue D-05 : blocs mal formés, liens symboliques, fins de ligne ──────────


def _block_plan(catalog: str = "catalogue v2") -> EmitPlan:
    content = f"{managed_header('.md')}\n\n# Proj — Codex\n\n{catalog}\n"
    return EmitPlan(host_id=HostId.CODEX, files=(EmittedFile(relpath=Path("AGENTS.md"), content=content, block=True),))


def _old_block() -> str:
    from grimoire.hosts.emitters.base import render_block

    return render_block(f"{managed_header('.md')}\n\n# Proj — Codex\n\ncatalogue v1\n")


_TEAM = "# Règles d'équipe\n\nNe jamais pousser le vendredi.\n\n"


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda b: b.replace("<!-- grimoire:end -->", "<!-- grimoire:end --> "), id="end-with-trailing-space"),
        pytest.param(lambda b: b.replace("<!-- grimoire:end -->\n", "") + "\n## Après\nTexte après\n", id="end-missing"),
    ],
)
@pytest.mark.parametrize("force", [False, True], ids=["plain", "force"])
def test_a_block_whose_end_is_damaged_never_swallows_the_user_content(
    tmp_path: Path, damage: object, force: bool
) -> None:
    path = tmp_path / "AGENTS.md"
    original = _TEAM + damage(_old_block())  # type: ignore[operator]
    path.write_text(original, encoding="utf-8")

    result = apply_plan(_block_plan(), tmp_path, force=force)

    assert result.conflicts == {"AGENTS.md": "edited"}
    assert result.skipped and not result.written
    assert path.read_text(encoding="utf-8") == original


def test_user_text_before_an_unstamped_legacy_header_is_not_a_legacy_file(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    original = _TEAM + f"{managed_header('.md')}\n\n# ancien catalogue\n"
    path.write_text(original, encoding="utf-8")

    result = apply_plan(_block_plan(), tmp_path)

    assert result.conflicts == {"AGENTS.md": "foreign"}
    assert path.read_text(encoding="utf-8") == original
    apply_plan(_block_plan(), tmp_path, force=True)
    forced = path.read_text(encoding="utf-8")
    assert forced.startswith(original.rstrip("\n"))  # --force ajoute le bloc sans rien retirer
    assert forced.count("<!-- grimoire:begin") == 1


def test_pruning_keeps_a_file_whose_block_is_damaged(tmp_path: Path) -> None:
    from grimoire.hosts.emitters import owned_managed_paths

    path = tmp_path / "AGENTS.md"
    path.write_text(f"{managed_header('.md')}\n" + _old_block().replace("<!-- grimoire:end -->\n", ""), encoding="utf-8")

    assert tmp_path / "AGENTS.md" not in owned_managed_paths(_block_plan(), tmp_path)


def test_a_sync_keeps_user_lines_byte_for_byte_when_the_file_is_crlf(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    prefix = "# Règles\r\n\r\nLigne A\r\n\r\n".encode()
    suffix = b"\r\nApres\r\n"
    path.write_bytes(prefix + _old_block().replace("\n", "\r\n").encode() + suffix)

    result = apply_plan(_block_plan(), tmp_path)

    after = path.read_bytes()
    assert result.written == ["AGENTS.md"] and not result.skipped
    assert after.startswith(prefix) and after.endswith(suffix)
    assert b"catalogue v2" in after
    assert b"\n" not in after.replace(b"\r\n", b"")  # aucun LF nu : le style du fichier est suivi
    again = apply_plan(_block_plan(), tmp_path)
    assert again.unchanged == ["AGENTS.md"] and path.read_bytes() == after


def test_a_lf_block_in_a_crlf_file_leaves_the_crlf_lines_around_it_alone(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    prefix = "# Règles\r\n\r\nLigne A\r\n\r\n".encode()
    suffix = b"\r\nApres\r\n"
    path.write_bytes(prefix + _old_block().encode() + suffix)

    apply_plan(_block_plan(), tmp_path)

    after = path.read_bytes()
    assert after.startswith(prefix) and after.endswith(suffix)


def test_force_appends_a_block_to_a_crlf_file_without_touching_its_lines(tmp_path: Path) -> None:
    path = tmp_path / "AGENTS.md"
    original = "# À moi\r\n\r\nContenu.\r\n".encode()
    path.write_bytes(original)

    apply_plan(_block_plan(), tmp_path, force=True)

    after = path.read_bytes()
    assert after.startswith(original)
    assert b"\n" not in after.replace(b"\r\n", b"")


symlinks = pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="liens symboliques indisponibles")


@symlinks
def test_a_symlinked_entrypoint_stays_a_link_and_its_target_gets_the_block(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Équipe\n\nx\n", encoding="utf-8")
    link = tmp_path / "GEMINI.md"
    link.symlink_to("AGENTS.md")
    content = f"{managed_header('.md')}\n\n# Proj\n\ncatalogue\n"
    plan = EmitPlan(host_id=HostId.GEMINI_CLI, files=(EmittedFile(relpath=Path("GEMINI.md"), content=content, block=True),))

    result = apply_plan(plan, tmp_path, force=True)

    assert result.written == ["GEMINI.md"]
    assert link.is_symlink() and link.readlink() == Path("AGENTS.md")
    target = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert target.startswith("# Équipe\n\nx\n") and "catalogue" in target


@symlinks
def test_a_symlinked_json_merge_updates_the_target_and_keeps_the_link(tmp_path: Path) -> None:
    (tmp_path / "dot").mkdir()
    (tmp_path / "dot" / "settings.json").write_text('{"a": 1}\n', encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    link = tmp_path / ".claude" / "settings.json"
    link.symlink_to(Path("..") / "dot" / "settings.json")
    plan = EmitPlan(
        host_id=HostId.CLAUDE_CODE_CLI,
        merges=(JsonMerge(relpath=Path(".claude/settings.json"), merge=lambda d: {**d, "b": 2}),),
    )

    apply_plan(plan, tmp_path)

    assert link.is_symlink()
    assert json.loads((tmp_path / "dot" / "settings.json").read_text(encoding="utf-8")) == {"a": 1, "b": 2}


@symlinks
def test_a_rollback_keeps_a_symlink_and_restores_its_target(tmp_path: Path) -> None:
    (tmp_path / "blocked").write_text("fichier\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Équipe\n\nx\n", encoding="utf-8")
    (tmp_path / "GEMINI.md").symlink_to("AGENTS.md")
    content = f"{managed_header('.md')}\n\n# Proj\n\ncatalogue\n"
    plan = EmitPlan(
        host_id=HostId.GEMINI_CLI,
        files=(EmittedFile(relpath=Path("GEMINI.md"), content=content, block=True), _managed("blocked/third.md")),
    )
    before = _snapshot(tmp_path)

    with pytest.raises(OSError):
        apply_plan(plan, tmp_path, force=True)

    assert (tmp_path / "GEMINI.md").is_symlink()
    assert _snapshot(tmp_path) == before
