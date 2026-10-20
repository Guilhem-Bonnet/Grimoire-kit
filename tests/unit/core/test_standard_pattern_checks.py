"""Les quatre vérificateurs nés de l'issue #636.

``runtime-journal``, ``governed-observability-cockpit``,
``skill-classification-matrix`` et ``redis-hot-memory-soft-gate`` n'avaient
aucun check. Chaque cas négatif ci-dessous est rouge sur ``origin/main``
(l'identifiant n'y est jamais émis) et vert après ; chaque cas nominal
prouve qu'un projet ``governed`` fraîchement scaffoldé ne rougit pas.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import (
    calculate_compliance_score,
    setup_standard_profile,
    verify_standard_profile,
)
from grimoire.core.standard_checks.runtime_journal_check import (
    CLAIM_EVENT,
    JOURNAL_PATH,
    LEDGER_EVENTS_PATH,
    STALE_GRACE_S,
)

_STANDARD = Path("_grimoire/standard")
_OBSERVABILITY = _STANDARD / "observability-policy.yaml"
_MEMORY = _STANDARD / "memory-policy.yaml"
_HOOKS = _STANDARD / "hook-registry.yaml"
_SKILLS = Path("_grimoire/overrides/skills")


def _ids(result, prefix: str) -> list[str]:
    return [c.id for c in result.checks if c.id.startswith(prefix)]


def _check(result, check_id: str):
    found = [c for c in result.checks if c.id == check_id]
    assert found, f"{check_id} non émis ; émis : {sorted({c.id for c in result.checks})}"
    return found[0]


def _edit(root: Path, rel: Path, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{rel} ne contient pas {old!r}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    return tmp_path


# ── runtime-journal ──────────────────────────────────────────────────────────


def _write_claim(root: Path, at: datetime) -> None:
    path = root / LEDGER_EVENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {"event_type": CLAIM_EVENT, "entity_id": "GAO-1", "created_at": at.isoformat()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _write_journal(root: Path, *lines: str) -> None:
    path = root / JOURNAL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _event(at: datetime) -> str:
    return json.dumps({"event_type": "gate.checked", "timestamp": at.isoformat(), "task_id": "GAO-1"})


def test_journal_absent_sur_un_projet_neuf_est_un_avertissement(governed: Path) -> None:
    check = _check(verify_standard_profile(governed), "journal.missing")
    assert check.severity == "warning"
    assert _ids(verify_standard_profile(governed), "journal.") == ["journal.missing"]


def test_journal_absent_malgre_un_claim_est_une_erreur_en_governed(governed: Path) -> None:
    _write_claim(governed, datetime.now(UTC))
    check = _check(verify_standard_profile(governed), "journal.missing")
    assert check.severity == "error"
    assert "claim" in check.message


def test_journal_absent_malgre_un_claim_reste_un_avertissement_sous_governed(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")
    _write_claim(tmp_path, datetime.now(UTC))
    assert _check(verify_standard_profile(tmp_path), "journal.missing").severity == "warning"


def test_une_ligne_qui_n_est_pas_un_evenement_est_une_erreur(governed: Path) -> None:
    now = datetime.now(UTC)
    _write_journal(governed, _event(now), "{pas du json", json.dumps({"timestamp": now.isoformat()}))
    result = verify_standard_profile(governed)
    check = _check(result, "journal.line_invalid")
    assert check.severity == "error"
    assert "2 line(s)" in check.message and "line 2, 3" in check.message
    assert not result.ok


def test_journal_valide_et_claim_plus_ancien_ne_dit_rien(governed: Path) -> None:
    now = datetime.now(UTC)
    _write_claim(governed, now - timedelta(hours=2))
    _write_journal(governed, _event(now - timedelta(hours=1)))
    assert _ids(verify_standard_profile(governed), "journal.") == []


def test_claim_plus_recent_que_le_journal_au_dela_de_la_marge_est_en_retard(governed: Path) -> None:
    now = datetime.now(UTC)
    _write_journal(governed, _event(now - timedelta(seconds=STALE_GRACE_S + 60)))
    _write_claim(governed, now)
    check = _check(verify_standard_profile(governed), "journal.stale")
    assert check.severity == "warning"


def test_claim_dans_la_marge_du_dernier_evenement_n_est_pas_en_retard(governed: Path) -> None:
    now = datetime.now(UTC)
    _write_journal(governed, _event(now - timedelta(seconds=5)))
    _write_claim(governed, now)
    assert "journal.stale" not in _ids(verify_standard_profile(governed), "journal.")


def test_le_score_ne_compte_le_journal_qu_une_fois(governed: Path) -> None:
    """Avant #636 le score ajoutait son propre avertissement « journal vide » à
    côté des checks ; `journal.missing` le remplace, le bucket ne double pas."""
    score = calculate_compliance_score(governed)
    bucket = score.dimensions["runtime_journal"]
    assert bucket["warnings"] == 1 and bucket["errors"] == 0


# ── governed-observability-cockpit ───────────────────────────────────────────


def test_politique_d_observabilite_du_gabarit_passe(governed: Path) -> None:
    assert _ids(verify_standard_profile(governed), "observability.") == []


def test_politique_absente_en_governed_est_une_erreur(governed: Path) -> None:
    (governed / _OBSERVABILITY).unlink()
    assert _check(verify_standard_profile(governed), "observability.policy_missing").severity == "error"


def test_politique_absente_sous_governed_ne_dit_rien(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")
    assert not (tmp_path / _OBSERVABILITY).is_file()
    assert _ids(verify_standard_profile(tmp_path), "observability.") == []


def test_aucune_entree_declaree_est_une_erreur(governed: Path) -> None:
    _edit(governed, _OBSERVABILITY, "declared_inputs:\n", "declared_inputs: []\ndeclared_inputs_off:\n")
    assert _check(verify_standard_profile(governed), "observability.inputs_undeclared").severity == "error"


def test_une_entree_sans_proprietaire_est_une_erreur(governed: Path) -> None:
    _edit(governed, _OBSERVABILITY, '    owner: "maintainers"\n', '    owner: ""\n')
    check = _check(verify_standard_profile(governed), "observability.inputs_undeclared")
    assert "task_board" in check.message


def test_une_vue_qui_lit_une_entree_non_declaree_est_une_erreur(governed: Path) -> None:
    _edit(governed, _OBSERVABILITY, 'inputs: ["task_board", "evidence_packs"]', 'inputs: ["task_board", "fantome"]')
    check = _check(verify_standard_profile(governed), "observability.inputs_undeclared")
    assert "task_and_evidence_overlay:fantome" in check.message


def test_export_sans_interdiction_des_secrets_est_une_erreur(governed: Path) -> None:
    _edit(governed, _OBSERVABILITY, "    - secrets\n", "")
    check = _check(verify_standard_profile(governed), "observability.secret_export_allowed")
    assert "secrets" in check.message


def test_export_sans_controle_de_caviardage_est_une_erreur(governed: Path) -> None:
    _edit(governed, _OBSERVABILITY, "export_requires_redaction_check: true", "export_requires_redaction_check: false")
    _check(verify_standard_profile(governed), "observability.secret_export_allowed")


# ── skill-classification-matrix ──────────────────────────────────────────────


#: Fin du bloc ``managed_sources.skills`` du gabarit — le bloc ``hooks`` juste
#: au-dessus porte les mêmes clés, l'ancre doit être propre aux skills.
_SKILLS_TAIL = '      - ".github/skills/grimoire-test-architecture/SKILL.md"\n    project_pack: []\n'


def _project_skill(root: Path, slug: str) -> None:
    (root / _SKILLS).mkdir(parents=True, exist_ok=True)
    (root / _SKILLS / f"{slug}.md").write_text(
        f"---\nname: {slug}\ndescription: quand tester\n---\n\nCorps.\n", encoding="utf-8"
    )


def test_projet_sans_skill_propre_ne_dit_rien(governed: Path) -> None:
    assert _ids(verify_standard_profile(governed), "skills.") == []


def test_skill_du_projet_sans_classe_est_signale(governed: Path) -> None:
    _project_skill(governed, "revue-maison")
    check = _check(verify_standard_profile(governed), "skills.unclassified")
    assert check.severity == "warning" and "revue-maison" in check.message


def test_skill_classe_par_slug_passe(governed: Path) -> None:
    _project_skill(governed, "revue-maison")
    _edit(governed, _HOOKS, _SKILLS_TAIL, _SKILLS_TAIL.replace("project_pack: []", "project_pack: [revue-maison]"))
    assert _ids(verify_standard_profile(governed), "skills.") == []


def test_skill_classe_par_chemin_de_skill_md_passe(governed: Path) -> None:
    _project_skill(governed, "revue-maison")
    _edit(
        governed, _HOOKS,
        _SKILLS_TAIL + "    incubator: []\n",
        _SKILLS_TAIL + '    incubator: [".github/skills/revue-maison/SKILL.md"]\n',
    )
    assert _ids(verify_standard_profile(governed), "skills.") == []


def test_registre_sans_managed_sources_laisse_le_skill_non_classe(governed: Path) -> None:
    _project_skill(governed, "revue-maison")
    _edit(governed, _HOOKS, "managed_sources:", "managed_sources_off:")
    _check(verify_standard_profile(governed), "skills.unclassified")


# ── redis-hot-memory-soft-gate ───────────────────────────────────────────────


def test_degradation_du_gabarit_passe(governed: Path) -> None:
    assert "memory.hot_degradation_undeclared" not in _ids(verify_standard_profile(governed), "memory.")


def test_degradation_absente_est_signalee(governed: Path) -> None:
    _edit(governed, _MEMORY, "  hot_memory_degradation:", "  hot_memory_degradation_off:")
    check = _check(verify_standard_profile(governed), "memory.hot_degradation_undeclared")
    assert check.severity == "warning" and "no memory_os.hot_memory_degradation" in check.message


def test_degradation_qui_echoue_au_lieu_de_degrader_est_signalee(governed: Path) -> None:
    _edit(governed, _MEMORY, 'on_unavailable: "degrade"', 'on_unavailable: "fail"')
    check = _check(verify_standard_profile(governed), "memory.hot_degradation_undeclared")
    assert "'fail'" in check.message


def test_degradation_sans_repli_nomme_est_signalee(governed: Path) -> None:
    _edit(governed, _MEMORY, 'fallback: "sqlite"', 'fallback: ""')
    check = _check(verify_standard_profile(governed), "memory.hot_degradation_undeclared")
    assert "fallback" in check.message
