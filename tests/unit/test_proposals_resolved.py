"""Etat « résolu » des propositions du flow de mise à jour (issue #681).

Quatre défauts relevés sur un projet réel : une proposition dont la condition
est satisfaite à la main restait ``pending`` à jamais ; accepter
``hosts-declare-enabled`` écrasait ``hosts.enabled`` ; un second ``accept``
n'était pas un no-op ; un message citait ``grimoire upgrade-flow status``,
commande inexistante.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core import layout
from grimoire.proposals import (
    accept_proposal,
    count_pending,
    create_manual_proposal,
    list_proposals,
)
from grimoire.tools._common import load_yaml


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "project-context.yaml").write_text(
        "project:\n  name: p\n  type: webapp\n"
        "user:\n  name: G\n  language: Français\n  skill_level: expert\n"
        "agents:\n  archetype: minimal\n",
        encoding="utf-8",
    )
    return tmp_path


def _declare_hosts(project: Path, hosts: list[str]) -> None:
    cfg = project / "project-context.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8") + "hosts:\n  enabled: [" + ", ".join(hosts) + "]\n",
        encoding="utf-8",
    )


def _hosts_proposal(project: Path, detected: str) -> None:
    create_manual_proposal(
        project,
        slug="hosts-declare-enabled",
        specialty="hôtes détectés",
        artifact_type="needs-hosts",
        artifact_ref=detected,
    )


def _status(project: Path, slug: str) -> str:
    return next(p.status for p in list_proposals(project) if p.slug == slug)


# ── 1. état « résolu » ───────────────────────────────────────────────────────


def test_override_migration_deja_partielle_devient_resolved_au_listing(project: Path) -> None:
    agents = layout.overrides_dir(project) / layout.AGENTS_SUBDIR
    agents.mkdir(parents=True)
    (agents / "dev.md").write_text("---\nname: dev\nextends: kit\n---\n", encoding="utf-8")
    create_manual_proposal(
        project,
        slug="override-migration-dev",
        specialty="override dev",
        artifact_type="override-migration",
        target_agent="dev",
        carrier_reason="conversion sûre : copie identique",
    )
    listed = {p.slug: p for p in list_proposals(project)}
    resolved = listed["override-migration-dev"]
    assert resolved.status == "resolved"
    assert resolved.resolved_at
    assert "partiel" in resolved.resolved_reason
    assert count_pending(project) == 0


def test_hosts_deja_declares_ou_besoins_deja_resolus_deviennent_resolved(project: Path) -> None:
    _declare_hosts(project, ["claude", "codex"])
    _hosts_proposal(project, "claude")
    cfg = project / "project-context.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "needs:\n  commands:\n    test-runner: pytest\n", encoding="utf-8")
    create_manual_proposal(
        project,
        slug="needs-declare-commands",
        specialty="besoins",
        artifact_type="needs-hosts",
        artifact_ref="test-runner",
    )
    assert _status(project, "hosts-declare-enabled") == "resolved"
    assert _status(project, "needs-declare-commands") == "resolved"


def test_une_condition_non_satisfaite_reste_pending(project: Path) -> None:
    _declare_hosts(project, ["codex"])
    _hosts_proposal(project, "claude")
    assert _status(project, "hosts-declare-enabled") == "pending"


def test_une_proposition_resolue_se_rouvre_si_le_flow_la_repropose(project: Path) -> None:
    _declare_hosts(project, ["claude"])
    _hosts_proposal(project, "claude")
    assert _status(project, "hosts-declare-enabled") == "resolved"
    cfg = project / "project-context.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("hosts:\n  enabled: [claude]\n", ""), encoding="utf-8")
    _hosts_proposal(project, "claude")
    assert _status(project, "hosts-declare-enabled") == "pending"


# ── 2. accept hosts fusionne ─────────────────────────────────────────────────


def test_accept_hosts_ne_retire_pas_un_hote_deja_declare(project: Path) -> None:
    _declare_hosts(project, ["codex"])
    _hosts_proposal(project, "claude,codex")

    result = accept_proposal(project, "hosts-declare-enabled")

    assert result["ok"] is True, result
    assert load_yaml(project / "project-context.yaml")["hosts"]["enabled"] == ["codex", "claude"]


def test_accept_hosts_avec_declaration_superieure_a_la_detection_ne_perd_rien(project: Path) -> None:
    _declare_hosts(project, ["claude", "codex"])
    _hosts_proposal(project, "claude")

    result = accept_proposal(project, "hosts-declare-enabled")

    assert result["ok"] is True, result
    assert load_yaml(project / "project-context.yaml")["hosts"]["enabled"] == ["claude", "codex"]


# ── 3. accept idempotent ─────────────────────────────────────────────────────


def test_second_accept_est_un_noop_explicite(project: Path) -> None:
    _hosts_proposal(project, "claude")
    first = accept_proposal(project, "hosts-declare-enabled")
    assert first["ok"] is True, first
    before = (project / "project-context.yaml").read_text(encoding="utf-8")

    second = accept_proposal(project, "hosts-declare-enabled")

    assert second["ok"] is True
    assert second["status"] == "already-resolved"
    assert (project / "project-context.yaml").read_text(encoding="utf-8") == before


def test_accept_d_une_proposition_resolue_est_already_resolved(project: Path) -> None:
    agents = layout.overrides_dir(project) / layout.AGENTS_SUBDIR
    agents.mkdir(parents=True)
    (agents / "dev.md").write_text("---\nname: dev\nextends: kit\n---\n", encoding="utf-8")
    create_manual_proposal(
        project,
        slug="override-migration-dev",
        specialty="override dev",
        artifact_type="override-migration",
        target_agent="dev",
        carrier_reason="conversion sûre : copie identique",
    )
    result = accept_proposal(project, "override-migration-dev")
    assert result["ok"] is True, result
    assert result["status"] == "already-resolved"


# ── 4. plus de renvoi vers une commande inexistante ──────────────────────────


def test_aucun_fichier_src_ne_cite_upgrade_flow_status() -> None:
    src = Path(__file__).resolve().parents[2] / "src"
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*")
        if p.is_file() and p.suffix in {".py", ".md", ".yaml", ".yml", ".txt", ".j2", ".tmpl"}
        and "upgrade-flow status" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
