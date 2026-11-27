"""Atelier de spec : comparer deux versions d'un chantier, restaurer une phase (ou un bloc), sans git en écriture.

De vrais dépôts git temporaires. Comparer : deux commits, ``working``, identifiant forgé / étranger / option injectée.
Restaurer : contenu exact à l'octet, conflit (409), document accepté, exigence validée protégée, annulation, fils de
commentaires. Aucun test n'écrit ailleurs que dans son ``tmp_path`` ; un espion prouve qu'aucune commande git n'écrit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from grimoire.core.chantiers import new_chantier
from grimoire.missions import requirement_gate as rg
from grimoire.tools import atelier_blocs
from grimoire.tools import workspace_atelier as wa
from grimoire.tools import workspace_atelier_comments as comments
from grimoire.tools import workspace_atelier_history as h
from grimoire.tools import workspace_atelier_restore as r

P = "/api/workspace/atelier/chantiers/"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git absent")

BRIEF_1 = "# Brief\n\nUne ligne.\nDeux lignes.\n\nUn autre paragraphe stable.\n"
BRIEF_2 = "# Brief\n\nUne ligne.\nTrois lignes.\nQuatre.\n\nUn autre paragraphe stable.\n"
BRIEF_3 = "# Brief\n\nUne ligne modifiée.\nTrois lignes.\nQuatre.\n\nUn autre paragraphe stable.\n"
EXIG_1 = "# Exigences\n\n- REQ-001 [Must] : dicter\n"
EXIG_2 = "# Exigences\n\n- REQ-001 [Must] : dicter\n- REQ-002 [Should] : repli\n"


def git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_AUTHOR_NAME": "Ada",
        "GIT_AUTHOR_EMAIL": "ada@example.org", "GIT_COMMITTER_NAME": "Ada", "GIT_COMMITTER_EMAIL": "ada@example.org",
    })
    done = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=root, capture_output=True, text=True, check=True, env=env)
    return done.stdout.strip()


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)
    monkeypatch.setattr(rg, "_agent_signal", lambda: "")  # le test joue la MOA humaine : le geste de validation ne voit pas l'agent
    r._UNDO.clear()


class Repo:
    def __init__(self, root: Path, slug: str, shas: list[str]) -> None:
        self.root, self.slug, self.shas = root, slug, shas
        base = root / "_grimoire" / "cadrage" / slug
        self.brief, self.exig, self.cdc = base / "01-brief.md", base / "04-exigences.md", base / "05-cahier-des-charges.md"

    def fp(self, path: Path) -> str:
        return atelier_blocs.fingerprint(path.read_text(encoding="utf-8"))

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def commit(self, message: str, *paths: Path) -> str:
        git(self.root, "add", "--", *[self.rel(p) for p in paths])
        git(self.root, "commit", "-q", "-m", message)
        sha = git(self.root, "rev-parse", "HEAD")
        self.shas.append(sha)
        return sha

    def restore(self, **body: Any) -> tuple[int, dict[str, Any]]:
        return wa.post_http(self.root, f"{P}{self.slug}/restore", body)

    def compare(self, frm: str, to: str) -> dict[str, Any]:
        return wa.get(self.root, f"{P}{self.slug}/compare", {"from": [frm], "to": [to]})  # type: ignore[no-any-return]


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Trois états du brief : deux commits, puis une modification non commitée ; les exigences en deux commits."""
    root = tmp_path / "p"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: p\n", encoding="utf-8")
    wa.set_atelier(root, True)
    slug = new_chantier(root, "Dictée vocale").slug
    out = Repo(root, slug, [])
    git(root, "init", "-q", "-b", "main")
    shutil.rmtree(root / ".git" / "hooks", ignore_errors=True)
    out.brief.write_text(BRIEF_1, encoding="utf-8")
    out.exig.write_text(EXIG_1, encoding="utf-8")
    out.commit("feat: premier jet", out.brief, out.exig)
    out.brief.write_text(BRIEF_2, encoding="utf-8")
    out.exig.write_text(EXIG_2, encoding="utf-8")
    out.commit("feat: deuxième jet", out.brief, out.exig)
    out.brief.write_text(BRIEF_3, encoding="utf-8")  # non commité
    return out


# ── Comparer ─────────────────────────────────────────────────────────────────────────────────────────────


def test_compare_two_commits(repo: Repo) -> None:
    first, second = repo.shas
    out = repo.compare(first, second)
    assert out["from"]["id"] == first and out["to"]["id"] == second and out["from"]["subject"] == "feat: premier jet"
    assert out["restorable"] is False
    assert [p["id"] for p in out["phases"]] == ["brief", "exigences"]
    brief = out["phases"][0]
    assert (brief["added"], brief["removed"]) == (2, 1)
    assert "blocks" not in brief and "refusal" not in brief
    assert (out["added"], out["removed"]) == (3, 1) and out["truncated"] is False


def test_compare_commit_with_working_offers_the_restore(repo: Repo) -> None:
    first = repo.shas[0]
    out = repo.compare(first, "working")
    assert out["to"]["kind"] == "working" and out["restorable"] is True
    brief = next(p for p in out["phases"] if p["id"] == "brief")
    assert brief["restorable"] is True and brief["refusal"] == "" and brief["file_fp"] == repo.fp(repo.brief)
    assert brief["blocks"], "un bloc de même rang diffère : il se restaure seul"
    block = brief["blocks"][0]
    assert block["old_text"].startswith("Une ligne.") and block["text"].startswith("Une ligne modifiée.")
    assert {"id", "fp", "prev", "next", "twins", "line", "kind"} <= set(block)
    exig = next(p for p in out["phases"] if p["id"] == "exigences")
    assert exig["restorable"] is True  # REQ-002 ajoutée depuis : la phase revient à EXIG_1


def test_compare_working_with_itself_and_reverse(repo: Repo) -> None:
    assert repo.compare("working", "working")["phases"] == []
    reverse = repo.compare("working", repo.shas[0])
    assert reverse["restorable"] is False
    assert reverse["from"]["kind"] == "working"


def test_compare_same_version_has_no_phase(repo: Repo) -> None:
    out = repo.compare(repo.shas[1], repo.shas[1])
    assert out["phases"] == [] and (out["added"], out["removed"]) == (0, 0)


def test_compare_accepts_abbreviated_ids(repo: Repo) -> None:
    out = repo.compare(repo.shas[0][:8].upper(), repo.shas[1][:10])
    assert out["from"]["id"] == repo.shas[0] and out["to"]["id"] == repo.shas[1]


def test_compare_refuses_forged_foreign_and_injected_ids(repo: Repo) -> None:
    other = new_chantier(repo.root, "Autre chantier")
    note = repo.root / "_grimoire" / "cadrage" / other.slug / "01-brief.md"
    note.write_text("# Brief\n\nautre\n", encoding="utf-8")
    foreign = repo.commit("chore: autre", note)
    for bad in ("--output=/tmp/x", "-p", "HEAD", "HEAD~1", "main", "ffffffff", "g" * 12, "../x", "1234", "0" * 40, foreign):
        with pytest.raises((ValueError, FileNotFoundError)):
            repo.compare(repo.shas[0], bad)
        with pytest.raises((ValueError, FileNotFoundError)):
            repo.compare(bad, "working")
    with pytest.raises(ValueError):
        wa.get(repo.root, f"{P}{repo.slug}/compare", {})
    with pytest.raises(ValueError):
        wa.get(repo.root, f"{P}{repo.slug}/compare", {"from": [repo.shas[0]]})
    with pytest.raises(FileNotFoundError):
        wa.get(repo.root, f"{P}inconnu/compare", {"from": [repo.shas[0]], "to": ["working"]})


def test_compare_passes_only_full_hashes_to_git(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    real = h._git

    def spy(root: Path, args: list[str], **kw: Any) -> h._Out:
        seen.append(args)
        return real(root, args, **kw)

    monkeypatch.setattr(h, "_git", spy)
    repo.compare(repo.shas[0][:8], "working")
    revs = [a for call in seen if call[0] in ("show", "ls-tree") for a in call if repo.shas[0] in a]
    assert revs and all(a.startswith(repo.shas[0]) for a in revs)
    assert not any(repo.shas[0][:8] == a for call in seen for a in call)


def test_compare_without_repo_or_old_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "nogit"
    root.mkdir()
    (root / "project-context.yaml").write_text("project:\n  name: p\n", encoding="utf-8")
    wa.set_atelier(root, True)
    slug = new_chantier(root, "Sans git").slug
    with pytest.raises(FileNotFoundError, match="dépôt git"):
        r.compare(root, slug, "abcdef1", "working")
    brief = root / "_grimoire" / "cadrage" / slug / "01-brief.md"
    status, payload = wa.post_http(root, f"{P}{slug}/restore", {"phase": "brief", "expected": "x", "version": "abcdef1"})
    assert status == 404 and "dépôt git" in payload["error"] and brief.is_file()


def test_compare_old_git_is_refused(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    real = h._git

    def old(root: Path, args: list[str], **kw: Any) -> h._Out:
        return h._Out(0, b"git version 2.43.0\n") if args == ["--version"] else real(root, args, **kw)

    monkeypatch.setattr(h, "_git", old)
    with pytest.raises(FileNotFoundError, match="trop ancienne"):
        repo.compare(repo.shas[0], "working")
    before = repo.brief.read_bytes()
    status, _payload = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert status == 404 and repo.brief.read_bytes() == before


def test_compare_notes_when_a_version_lacks_the_file_or_has_it_unreadable(repo: Repo) -> None:
    repo.cdc.write_text("# Cahier\n\n- REQ-001 [Must] : dicter\n", encoding="utf-8")
    out = repo.compare(repo.shas[0], "working")
    cdc = next(p for p in out["phases"] if p["id"] == "cahier-des-charges")
    assert cdc["restorable"] is False and "n'a pas ce fichier" in cdc["restore_note"]
    code, payload = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=repo.shas[0])
    assert code == 400 and "n'a pas ce fichier" in payload["error"]


def test_compare_binary_and_oversized_files_stay_read_only(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.cdc.write_bytes(b"\x00\x01binaire")
    git(repo.root, "add", "--", repo.rel(repo.cdc))
    git(repo.root, "commit", "-q", "-m", "binaire")
    repo.cdc.write_bytes(b"\x00\x02binaire")
    binary = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "cahier-des-charges")
    assert "binaire" in binary["note"] and binary["restorable"] is False and "binaire" in binary["restore_note"]
    monkeypatch.setattr(h, "MAX_BLOB", 30)
    big = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "brief")
    assert big["restorable"] is False and "volumineux" in big["restore_note"]


def test_compare_budget_exhausted_is_reported(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(h, "REQUEST_BUDGET", -1.0)
    with pytest.raises(FileNotFoundError):
        repo.compare(repo.shas[0], "working")  # le dépôt n'a pas pu être lu dans le budget : refus net, rien d'inventé

    monkeypatch.undo()
    real = h._git
    calls = {"n": 0}

    def slow(root: Path, args: list[str], **kw: Any) -> h._Out:
        if args[0] == "show":
            return h._Out(124, b"", failed="delai")
        calls["n"] += 1
        return real(root, args, **kw)

    monkeypatch.setattr(h, "_git", slow)
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)
    out = repo.compare(repo.shas[0], repo.shas[1])
    assert out["truncated"] is True and out["reason"] == "delai"


def test_compare_unreadable_object_is_noted(repo: Repo) -> None:
    rel = repo.rel(repo.brief)
    blob = git(repo.root, "rev-parse", f"{repo.shas[0]}:{rel}")
    (repo.root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    out = repo.compare(repo.shas[0], repo.shas[1])
    brief = next(p for p in out["phases"] if p["id"] == "brief")
    assert brief["rows"] == [] and "indisponible" in brief["note"] and out["truncated"] is True


def test_compare_blocks_only_for_one_to_one_replacements(repo: Repo) -> None:
    # Un bloc ajouté et un bloc retiré ne se rétablissent pas bloc par bloc ; un titre ne se réécrit jamais.
    repo.brief.write_text("# Autre titre\n\nUne ligne.\nTrois lignes.\n\nNouveau bloc.\n\nUn autre paragraphe stable.\n", encoding="utf-8")
    brief = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "brief")
    assert all(b["kind"] != "titre" for b in brief["blocks"])


def test_compare_blocks_are_capped(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(r, "MAX_BLOCKS", 1)
    brief = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "brief")
    assert brief["blocks"] == [] and brief["restorable"] is True
    monkeypatch.setattr(r, "MAX_BLOCKS", 400)
    monkeypatch.setattr(r, "MAX_PAIRS", 1)
    many = "# Brief\n\n" + "\n\n".join(f"Paragraphe {i} nouveau." for i in range(4)) + "\n"
    old = "# Brief\n\n" + "\n\n".join(f"Paragraphe {i} ancien." for i in range(4)) + "\n"
    assert len(r._pairs("brief", old, many)) == 1


def test_compare_skips_blocks_that_edit_block_would_refuse(repo: Repo) -> None:
    # Un titre dans le texte ancien structurerait le fichier : le bloc n'est pas proposé.
    assert r._pairs("brief", "# Brief\n\n- point ancien\n\n- # titre caché\n", "# Brief\n\n- point nouveau\n\n- autre\n")[0]["old_text"] == "- point ancien"
    assert r._pairs("brief", "# Brief\n\n" + "x" * 20_001 + "\n", "# Brief\n\npetit\n") == []


# ── Restaurer un bloc : la route d'édition existante, aucun nouveau chemin ───────────────────────────────


def test_restore_a_block_by_the_existing_block_route(repo: Repo) -> None:
    brief = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "brief")
    block = brief["blocks"][0]
    saved = wa.edit_block(repo.root, repo.slug, block["id"], {
        "text": block["old_text"], "block_fp": block["fp"], "prev": block["prev"], "next": block["next"],
        "twins": block["twins"], "base": block["text"],
    })
    assert saved["ok"] is True
    text = repo.brief.read_text(encoding="utf-8")
    assert "Une ligne.\nDeux lignes." in text and "Une ligne modifiée." not in text
    assert text == BRIEF_1, "le bloc revient tel qu'il était, les autres blocs ne bougent pas d'un octet"
    # Le bloc a changé depuis : l'ancien contrôle de conflit par bloc répond 409 (rien n'est écrit).
    before = repo.brief.read_bytes()
    with pytest.raises(wa.ConflictError):
        wa.edit_block(repo.root, repo.slug, block["id"], {
            "text": block["old_text"], "block_fp": block["fp"], "prev": block["prev"], "next": block["next"],
            "twins": block["twins"], "base": block["text"],
        })
    assert repo.brief.read_bytes() == before


# ── Restaurer une phase ──────────────────────────────────────────────────────────────────────────────────


def test_restore_phase_writes_the_exact_bytes_of_the_version(repo: Repo) -> None:
    repo.brief.write_bytes("# Brief\r\n\r\nÉté 😀 — ligne\r\nsans fin".encode())
    git(repo.root, "add", "--", repo.rel(repo.brief))
    git(repo.root, "commit", "-q", "-m", "octets")
    odd = git(repo.root, "rev-parse", "HEAD")
    repo.brief.write_text(BRIEF_3, encoding="utf-8")
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=odd)
    assert code == 200 and out["ok"] is True and out["committed"] is False and out["unchanged"] is False
    assert repo.brief.read_bytes() == "# Brief\r\n\r\nÉté 😀 — ligne\r\nsans fin".encode()
    assert out["phase"]["fingerprint"] == atelier_blocs.fingerprint(repo.brief.read_bytes().decode("utf-8"))
    assert git(repo.root, "rev-parse", "HEAD") == odd, "rien n'est commité par le kit"


def test_restore_phase_from_an_older_commit_and_report_the_lines(repo: Repo) -> None:
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 200 and repo.brief.read_text(encoding="utf-8") == BRIEF_1
    assert (out["added"], out["removed"]) == (2, 3)
    assert out["undo"] and out["phase"]["id"] == "brief"
    assert git(repo.root, "status", "--porcelain", "--", repo.rel(repo.brief)).startswith("M")  # une modification non commitée


def test_restore_unchanged_phase_writes_nothing(repo: Repo) -> None:
    repo.brief.write_text(BRIEF_2, encoding="utf-8")
    before = repo.brief.stat().st_mtime_ns
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[1])
    assert code == 200 and out["unchanged"] is True and out["added"] == 0 and repo.brief.stat().st_mtime_ns == before
    assert not r._UNDO


def test_restore_conflict_when_the_file_changed_since_it_was_read(repo: Repo) -> None:
    seen = repo.fp(repo.brief)
    repo.brief.write_text(BRIEF_3 + "\nAjout d'un autre auteur.\n", encoding="utf-8")
    after = repo.brief.read_bytes()
    code, out = repo.restore(phase="brief", expected=seen, version=repo.shas[0])
    assert code == 409 and out["conflict"] is True and out["reason"] == "fichier-modifie"
    assert repo.brief.read_bytes() == after


def test_restore_validates_its_body(repo: Repo) -> None:
    fp = repo.fp(repo.brief)
    sha = repo.shas[0]
    before = repo.brief.read_bytes()
    cases: list[tuple[dict[str, Any], int]] = [
        ({"expected": fp, "version": sha}, 400),  # phase manquante
        ({"phase": "brief", "version": sha}, 400),  # empreinte manquante
        ({"phase": "brief", "expected": fp}, 400),  # ni version ni annulation
        ({"phase": "brief", "expected": fp, "version": sha, "undo": "x"}, 400),  # les deux
        ({"phase": "brief", "expected": fp, "version": 12}, 400),
        ({"phase": "brief", "expected": fp, "version": "working"}, 400),  # le fichier sur disque n'est pas une version
        ({"phase": "brief", "expected": fp, "version": "--output=x"}, 400),
        ({"phase": "brief", "expected": fp, "version": "ffffffff"}, 404),
        ({"phase": "brief", "expected": fp, "version": "HEAD"}, 400),
        ({"phase": "inconnue", "expected": fp, "version": sha}, 404),
        ({"phase": "../x", "expected": fp, "version": sha}, 404),
        ({"phase": "brief", "expected": fp, "undo": ""}, 400),
        ({"phase": "brief", "expected": fp, "undo": "inconnu"}, 404),
        ({"phase": "brief", "expected": fp, "undo": 5}, 400),
    ]
    for body, want in cases:
        code, _payload = repo.restore(**body)
        assert code == want, (body, code)
    assert repo.brief.read_bytes() == before
    code, _ = wa.post_http(repo.root, f"{P}inconnu/restore", {"phase": "brief", "expected": fp, "version": sha})
    assert code == 404


def test_restore_refuses_a_foreign_version(repo: Repo) -> None:
    other = new_chantier(repo.root, "Autre chantier")
    note = repo.root / "_grimoire" / "cadrage" / other.slug / "01-brief.md"
    note.write_text("# Brief\n\nautre\n", encoding="utf-8")
    foreign = repo.commit("chore: autre", note)
    before = repo.brief.read_bytes()
    code, _ = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=foreign)
    assert code == 404 and repo.brief.read_bytes() == before


def test_restore_needs_the_atelier_flag(repo: Repo) -> None:
    wa.set_atelier(repo.root, False)
    before = repo.brief.read_bytes()
    code, _ = repo.restore(phase="brief", expected="x", version=repo.shas[0])
    assert code == 404 and repo.brief.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        repo.compare(repo.shas[0], "working")


def test_restore_refuses_a_missing_or_foreign_encoding_file(repo: Repo) -> None:
    repo.brief.write_bytes(b"\xff\xfe# pas utf-8\n")
    code, out = repo.restore(phase="brief", expected="x", version=repo.shas[0])
    assert code == 400 and "UTF-8" in out["error"]
    repo.brief.unlink()
    code, out = repo.restore(phase="brief", expected="x", version=repo.shas[0])
    assert code == 404


def test_restore_refuses_a_version_that_is_not_utf8(repo: Repo) -> None:
    repo.brief.write_bytes(b"\xff\xfe# pas utf-8\n")
    git(repo.root, "add", "--", repo.rel(repo.brief))
    git(repo.root, "commit", "-q", "-m", "latin")
    bad = git(repo.root, "rev-parse", "HEAD")
    repo.brief.write_text(BRIEF_3, encoding="utf-8")
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=bad)
    assert code == 400 and "UTF-8" in out["error"]
    repo.brief.write_bytes(b"avant\x00apres")
    git(repo.root, "add", "--", repo.rel(repo.brief))
    git(repo.root, "commit", "-q", "-m", "nul")
    nul = git(repo.root, "rev-parse", "HEAD")
    repo.brief.write_text(BRIEF_3, encoding="utf-8")
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=nul)
    assert code == 400 and "binaire" in out["error"]


def test_restore_refuses_an_oversized_or_unreadable_version(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    fp = repo.fp(repo.brief)
    monkeypatch.setattr(h, "MAX_BLOB", 10)
    code, out = repo.restore(phase="brief", expected=fp, version=repo.shas[0])
    assert code == 400 and "volumineux" in out["error"]
    monkeypatch.undo()
    monkeypatch.setattr(rg, "_confirm_human_turn", lambda *_: True)
    rel = repo.rel(repo.brief)
    blob = git(repo.root, "rev-parse", f"{repo.shas[0]}:{rel}")
    (repo.root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    code, out = repo.restore(phase="brief", expected=fp, version=repo.shas[0])
    assert code == 400 and "indisponible" in out["error"]
    assert repo.fp(repo.brief) == fp


def test_restore_git_timeout_is_a_clean_refusal(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    real = h._git

    def slow(root: Path, args: list[str], **kw: Any) -> h._Out:
        return h._Out(124, b"", failed="delai") if args[0] in ("ls-tree", "show") else real(root, args, **kw)

    monkeypatch.setattr(h, "_git", slow)
    before = repo.brief.read_bytes()
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 400 and "trop de temps" in out["error"] and repo.brief.read_bytes() == before


def test_restore_refuses_an_accepted_document(repo: Repo) -> None:
    repo.cdc.write_text("---\nstatus: accepted\n---\n# Cahier\n\n- REQ-001 [Must] : dicter\n", encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.cdc))
    git(repo.root, "commit", "-q", "-m", "cdc")
    accepted_sha = git(repo.root, "rev-parse", "HEAD")
    repo.cdc.write_text("---\nstatus: accepted\n---\n# Cahier\n\n- REQ-001 [Must] : dicter autrement\n", encoding="utf-8")
    before = repo.cdc.read_bytes()
    code, out = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=accepted_sha)
    assert code == 403 and "accepté" in out["error"] and repo.cdc.read_bytes() == before
    info = next(p for p in repo.compare(accepted_sha, "working")["phases"] if p["id"] == "cahier-des-charges")
    assert info["restorable"] is False and "accepté" in info["refusal"]
    for status in ("validé", "'valide'", "accepté", "Accepted"):
        repo.cdc.write_text(f"---\nstatus: {status}\n---\n# Cahier\n", encoding="utf-8")
        code, _ = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=accepted_sha)
        assert code == 403, status


def test_restore_never_pronounces_an_acceptance(repo: Repo) -> None:
    repo.cdc.write_text("---\nstatus: accepted\n---\n# Cahier\n", encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.cdc))
    git(repo.root, "commit", "-q", "-m", "cdc accepté")
    sha = git(repo.root, "rev-parse", "HEAD")
    repo.cdc.write_text("---\nstatus: draft\n---\n# Cahier\n", encoding="utf-8")
    before = repo.cdc.read_bytes()
    code, out = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=sha)
    assert code == 403 and "acceptation" in out["error"] and repo.cdc.read_bytes() == before


def _declare_moa(repo: Repo) -> None:
    data = yaml.safe_load((repo.root / "project-context.yaml").read_text(encoding="utf-8"))
    data["governance"] = {"roles": {"moa": "Ada"}, "requirement_gate": "warn"}
    (repo.root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_restore_protects_a_validated_requirement(repo: Repo) -> None:
    _declare_moa(repo)
    repo.exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter à voix haute\n- REQ-002 [Should] : repli\n", encoding="utf-8")
    key = rg.normalize_key("REQ-001", repo.slug)
    rg.approve_requirement(repo.root, key, actor="ada")
    assert rg.validation_of(repo.root, key)[0] is not None
    before = repo.exig.read_bytes()
    # La version 1 dit « dicter » : restaurer modifierait la formulation validée.
    code, out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 403 and "validée par la MOA" in out["error"] and key in out["error"]
    assert repo.exig.read_bytes() == before
    info = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "exigences")
    assert info["restorable"] is False and key in info["refusal"]
    # Une version qui retire l'exigence validée est refusée aussi.
    repo.exig.write_text("# Exigences\n\n- REQ-002 [Should] : repli\n", encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.exig))
    git(repo.root, "commit", "-q", "-m", "sans REQ-001")
    removed = git(repo.root, "rev-parse", "HEAD")
    repo.exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter à voix haute\n- REQ-002 [Should] : repli\n", encoding="utf-8")
    code, out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=removed)
    assert code == 403 and key in out["error"]
    # Une version qui garde l'exigence validée telle quelle se restaure.
    repo.exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter à voix haute\n- REQ-002 [Should] : repli\n", encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.exig))
    git(repo.root, "commit", "-q", "-m", "même REQ-001")
    same = git(repo.root, "rev-parse", "HEAD")
    repo.exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter à voix haute\n- REQ-002 [Should] : autre repli\n", encoding="utf-8")
    code, _ = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=same)
    assert code == 200
    assert "- REQ-002 [Should] : repli" in repo.exig.read_text(encoding="utf-8")


def test_restore_allows_changing_a_requirement_that_is_not_validated(repo: Repo) -> None:
    _declare_moa(repo)
    code, _out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 200 and repo.exig.read_text(encoding="utf-8") == EXIG_1


def test_restore_fails_closed_when_the_authority_journal_is_unreadable(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _declare_moa(repo)
    from grimoire.missions import mandate

    def broken(_self: Any) -> list[dict[str, Any]]:
        raise mandate.JournalError("rompu")

    monkeypatch.setattr(mandate.AuthorityJournal, "read", broken)
    before = repo.exig.read_bytes()
    code, out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 403 and "illisible" in out["error"] and repo.exig.read_bytes() == before


@pytest.mark.parametrize(
    "governance",
    [
        {"roles": "Ada", "requirement_gate": "block"},  # section mal formée
        {"roles": {"moa": ""}, "requirement_gate": "block"},  # MOA vidée
        {"roles": {"moa": "Bob"}, "requirement_gate": "block"},  # autre MOA
        {},  # section retirée
    ],
)
def test_restore_keeps_protecting_a_validated_requirement_when_the_moa_is_corrupted(repo: Repo, governance: dict[str, Any]) -> None:
    _declare_moa(repo)
    repo.exig.write_text("# Exigences\n\n- REQ-001 [Must] : dicter à voix haute\n", encoding="utf-8")
    rg.approve_requirement(repo.root, rg.normalize_key("REQ-001", repo.slug), actor="ada")
    data = yaml.safe_load((repo.root / "project-context.yaml").read_text(encoding="utf-8"))
    data["governance"] = governance
    (repo.root / "project-context.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    before = repo.exig.read_bytes()
    code, out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 403 and repo.exig.read_bytes() == before, out
    info = next(p for p in repo.compare(repo.shas[0], "working")["phases"] if p["id"] == "exigences")
    assert info["restorable"] is False and info["refusal"]


def test_restore_refuses_any_accepted_status_key(repo: Repo) -> None:
    sneaky = "---\nstatus: draft\nstatus: accepted\n---\n# Cahier\n"
    repo.cdc.write_text(sneaky, encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.cdc))
    git(repo.root, "commit", "-q", "-m", "cdc double statut")
    sha = git(repo.root, "rev-parse", "HEAD")
    repo.cdc.write_text("---\nstatus: draft\n---\n# Cahier\n", encoding="utf-8")
    before = repo.cdc.read_bytes()
    code, out = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=sha)
    assert code == 403 and "acceptation" in out["error"] and repo.cdc.read_bytes() == before  # la version
    repo.cdc.write_text("---\nstatus: draft\n---\n# Cahier\n", encoding="utf-8")
    git(repo.root, "add", "--", repo.rel(repo.cdc))
    git(repo.root, "commit", "-q", "-m", "cdc brouillon")
    clean = git(repo.root, "rev-parse", "HEAD")
    repo.cdc.write_text(sneaky, encoding="utf-8")  # le fichier courant porte un statut accepté en seconde clé
    code, out = repo.restore(phase="cahier-des-charges", expected=repo.fp(repo.cdc), version=clean)
    assert code == 403 and "accepté" in out["error"] and repo.cdc.read_text(encoding="utf-8") == sneaky


def test_restore_refuses_an_empty_version(repo: Repo) -> None:
    for text in ("", "\n\n  \n"):
        repo.brief.write_text(text, encoding="utf-8")
        git(repo.root, "add", "--", repo.rel(repo.brief))
        git(repo.root, "commit", "-q", "--allow-empty", "-m", "vide")
        empty = git(repo.root, "rev-parse", "HEAD")
        repo.brief.write_text(BRIEF_3, encoding="utf-8")
        info = next(p for p in repo.compare(empty, "working")["phases"] if p["id"] == "brief")
        assert info["restorable"] is False and "vide" in info["refusal"]
        code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=empty)
        assert code == 409 and out["reason"] == "version-vide" and repo.brief.read_text(encoding="utf-8") == BRIEF_3


def test_restore_refuses_a_current_file_too_big_to_keep_for_undo(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    limit = len(BRIEF_1.encode()) + 4
    assert len(BRIEF_3.encode()) > limit
    monkeypatch.setattr(h, "MAX_BLOB", limit)
    before = repo.brief.read_bytes()
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 400 and "trop gros" in out["error"] and repo.brief.read_bytes() == before
    assert not r._UNDO


# ── Annuler ──────────────────────────────────────────────────────────────────────────────────────────────


def test_undo_puts_the_previous_content_back(repo: Repo) -> None:
    before = repo.brief.read_bytes()
    code, done = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 200 and repo.brief.read_text(encoding="utf-8") == BRIEF_1
    code, undone = repo.restore(phase="brief", expected=repo.fp(repo.brief), undo=done["undo"])
    assert code == 200 and repo.brief.read_bytes() == before and undone["undo"] == ""


def test_an_undo_token_works_once(repo: Repo) -> None:
    code, done = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    token = done["undo"]
    code, _ = repo.restore(phase="brief", expected=repo.fp(repo.brief), undo=token)
    assert code == 200
    repo.brief.write_text(BRIEF_2, encoding="utf-8")  # une édition entre-temps
    edited = repo.brief.read_bytes()
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), undo=token)
    assert code == 404 and "plus disponible" in out["error"] and repo.brief.read_bytes() == edited


def test_undo_keeps_the_conflict_control_and_the_other_guards(repo: Repo) -> None:
    code, done = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    token = done["undo"]
    repo.brief.write_text(BRIEF_1 + "\nEdité entre-temps.\n", encoding="utf-8")
    edited = repo.brief.read_bytes()
    code, out = repo.restore(phase="brief", expected=done["phase"]["fingerprint"], undo=token)
    assert code == 409 and repo.brief.read_bytes() == edited
    # Un jeton ne vaut que pour SA phase et SON chantier.
    code, _ = repo.restore(phase="exigences", expected=repo.fp(repo.exig), undo=token)
    assert code == 404
    other = new_chantier(repo.root, "Autre chantier").slug
    code, _ = wa.post_http(repo.root, f"{P}{other}/restore", {"phase": "brief", "expected": "x", "undo": token})
    assert code == 404
    # Le document accepté depuis refuse aussi l'annulation.
    repo.brief.write_text(BRIEF_1, encoding="utf-8")
    repo.cdc.write_text("---\nstatus: accepted\n---\n# Cahier\n", encoding="utf-8")
    code, done2 = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 200
    repo.exig.write_text("---\nstatus: accepted\n---\n# Exigences\n", encoding="utf-8")
    code, out = repo.restore(phase="exigences", expected=repo.fp(repo.exig), undo=done2["undo"])
    assert code == 403 and "accepté" in out["error"]


def test_undo_tokens_expire_and_are_bounded(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    code, done = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    key = next(iter(r._UNDO))
    root, slug, phase, data, stamp = r._UNDO[key]
    r._UNDO[key] = (root, slug, phase, data, stamp - r.UNDO_TTL - 1)
    code, out = repo.restore(phase="brief", expected=repo.fp(repo.brief), undo=done["undo"])
    assert code == 404 and "plus disponible" in out["error"]
    monkeypatch.setattr(r, "UNDO_KEEP", 2)
    for text in (BRIEF_2, BRIEF_3, BRIEF_1, BRIEF_2):
        repo.brief.write_text(text, encoding="utf-8")
        repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert len(r._UNDO) <= 2


# ── Commentaires ─────────────────────────────────────────────────────────────────────────────────────────


def test_restore_resyncs_the_comment_threads(repo: Repo) -> None:
    document = wa.document(repo.root, repo.slug)
    stable = next(b for p in document["phases"] for b in p["blocks"] if b["text"].startswith("Un autre paragraphe"))
    changing = next(b for p in document["phases"] for b in p["blocks"] if b["text"].startswith("Une ligne modifiée."))
    keep = comments.create(repo.root, repo.slug, {"block_id": stable["id"], "texte": "À garder"}, "humain")["comment"]
    gone = comments.create(repo.root, repo.slug, {"block_id": changing["id"], "texte": "Sur le bloc réécrit"}, "humain")["comment"]
    code, _ = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 200
    threads = {c["id"]: c for c in comments.listing(repo.root, repo.slug)["comments"]}
    assert threads[keep["id"]]["anchor"]["state"] != "orphelin"  # le bloc inchangé garde son fil
    assert gone["id"] in threads  # le fil n'est jamais perdu : retrouvé ou orphelin, mais gardé


def test_resync_without_a_store_or_with_a_broken_one_is_harmless(repo: Repo) -> None:
    comments.resync(repo.root, repo.slug)  # aucun magasin
    document = wa.document(repo.root, repo.slug)
    block = next(b for p in document["phases"] for b in p["blocks"] if b["text"].startswith("Un autre paragraphe"))
    comments.create(repo.root, repo.slug, {"block_id": block["id"], "texte": "x"}, "humain")
    store = next((repo.root / "_grimoire" / "cadrage" / repo.slug).glob("comments.json"))
    store.write_bytes(b"{pas du json")
    comments.resync(repo.root, repo.slug)  # magasin abîmé : sans effet, sans levée


# ── Aucune commande git qui écrit ────────────────────────────────────────────────────────────────────────

READ_ONLY_VERBS = {"rev-parse", "--version", "log", "show", "ls-tree"}


def test_no_mutating_git_command_is_ever_run(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []
    real = subprocess.Popen

    def spy(argv: list[str], *a: Any, **kw: Any) -> Any:
        launched.append(list(argv))
        return real(argv, *a, **kw)

    monkeypatch.setattr(h.subprocess, "Popen", spy)
    first = repo.shas[0]
    repo.compare(first, "working")
    repo.compare(first, repo.shas[1])
    code, done = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=first)
    assert code == 200
    repo.restore(phase="brief", expected=repo.fp(repo.brief), undo=done["undo"])
    assert launched
    verbs = set()
    for argv in launched:
        assert argv[0] == "git"
        index = len(argv)
        for i, item in enumerate(argv):
            if item in READ_ONLY_VERBS or item in {"checkout", "restore", "reset", "commit", "stash", "add", "apply", "merge", "rebase", "clean", "rm", "mv", "update-ref", "write-tree", "hash-object", "config", "tag", "branch", "cherry-pick", "revert", "fetch", "pull", "push", "init", "gc"}:
                index = i
                break
        verbs.add(argv[index])
        assert argv[index] in READ_ONLY_VERBS, argv
    assert verbs <= READ_ONLY_VERBS
    forbidden = ("--force", "--hard", "-C", "--git-dir", "--work-tree", "--exec-path", "--upload-pack")
    assert not any(item in forbidden for argv in launched for item in argv)
    assert git(repo.root, "status", "--porcelain", "--", repo.rel(repo.exig)) == ""  # l'index n'a pas bougé


def test_restore_does_not_touch_the_index_or_history(repo: Repo) -> None:
    head, index = git(repo.root, "rev-parse", "HEAD"), (repo.root / ".git" / "index").read_bytes()
    refs = git(repo.root, "for-each-ref")
    code, _ = repo.restore(phase="brief", expected=repo.fp(repo.brief), version=repo.shas[0])
    assert code == 200
    assert git(repo.root, "rev-parse", "HEAD") == head and git(repo.root, "for-each-ref") == refs
    assert (repo.root / ".git" / "index").read_bytes() == index


def test_restore_route_is_a_write_that_updates_the_chantier(repo: Repo) -> None:
    from grimoire.core.chantiers import read_chantier

    before = read_chantier(repo.root, repo.slug)
    code, _ = repo.restore(phase="exigences", expected=repo.fp(repo.exig), version=repo.shas[0])
    assert code == 200
    assert read_chantier(repo.root, repo.slug) is not None and before is not None
    # Un POST inconnu reste inconnu.
    with pytest.raises(FileNotFoundError):
        wa.post(repo.root, f"{P}{repo.slug}/restorer", {})
