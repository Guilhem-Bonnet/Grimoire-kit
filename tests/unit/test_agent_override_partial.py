"""Issue #427 — un override d'agent peut rester partiel et signale sa dérive.

Quatre défauts qu'une copie intégrale silencieuse permettait, à couvrir un
par un (le critère d'arrêt de l'issue) :

- un override écrit avant que le kit ne change ne dit jamais que le kit a
  bougé depuis ;
- ``extends: kit`` doit fusionner le frontmatter, hériter le corps, et
  refuser nommément sans contrepartie kit ;
- ``grimoire up`` doit lister les overrides à revoir sans jamais les
  toucher ;
- ``grimoire agent override convert`` doit convertir une copie intégrale
  fidèle, et refuser — en nommant les lignes — une copie dont le corps a
  divergé.

Les tests de fusion pure (``collect_agents``) écrivent directement les
fichiers kit/overrides sous ``tmp_path`` — pas de ``grimoire init`` : la
fusion ne dépend d'aucun autre étage du projet. Les tests de bout en bout
(``doctor``, ``up``, la commande CLI) lancent un vrai sous-processus
``python -m grimoire``, comme ``tests/test_command_contracts.py`` — c'est la
seule preuve que le câblage, pas seulement la fonction, fonctionne.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from grimoire.core.exceptions import GrimoireAgentError
from grimoire.core.override_drift import (
    OverrideConversionRefusedError,
    compute_kit_source_hash,
    convert_override,
    describe_drift,
    project_override_drift,
)
from grimoire.hosts.collect import collect_agents

KIT_AGENTS = "_grimoire/kit/agents"
OVERRIDE_AGENTS = "_grimoire/overrides/agents"


def _write(path: Path, meta_lines: list[str], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + "\n".join(meta_lines) + "\n---\n" + body, encoding="utf-8")


def _kit_agent(root: Path, name: str, *, extra: list[str] | None = None, body: str = "Corps du kit.\n") -> Path:
    path = root / KIT_AGENTS / f"{name}.md"
    meta = [f'name: "{name}"', f'description: "{name} — persona de test"', 'use_when: "Cas kit."', 'dont_use_when: "Hors kit."']
    _write(path, meta + (extra or []), body)
    return path


def _override(root: Path, name: str, meta_lines: list[str], body: str = "") -> Path:
    path = root / OVERRIDE_AGENTS / f"{name}.md"
    _write(path, meta_lines, body)
    return path


# ── Fusion `extends: kit` (volet 2) ─────────────────────────────────────────


@pytest.mark.parametrize("backend", ["python", "rust"])
def test_extends_kit_merges_only_declared_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    """``extends: kit`` + ``model_affinity`` seul : le corps et le reste du
    frontmatter viennent du kit, seule l'affinité vient de l'override — sous
    les deux backends (rien ne doit changer côté Rust, la fusion est un dict
    Python fait avant tout appel au port optionnel)."""
    import grimoire.hosts.surface as surface_mod

    if backend == "rust" and surface_mod._rust_core is None:  # type: ignore[attr-defined]
        pytest.skip("grimoire_hosts_core non construit ici")
    monkeypatch.setenv("GRIMOIRE_HOSTS_BACKEND", backend)

    root = tmp_path
    _kit_agent(root, "ops-engineer", extra=["tools: [read, execute]"])
    _override(root, "ops-engineer", ["extends: kit", "model_affinity:", "  reasoning: high"])

    agents = collect_agents(root, known_skills=frozenset())
    agent = next(a for a in agents if a.name == "ops-engineer")

    assert agent.override_ref == f"{OVERRIDE_AGENTS}/ops-engineer.md"
    assert agent.override_kind == "partial"
    assert agent.definition_ref == f"{KIT_AGENTS}/ops-engineer.md", "le corps se lit dans le fichier kit"
    assert agent.affinity.reasoning == "high"
    assert agent.description == "ops-engineer — persona de test"
    assert (root / agent.definition_ref).read_text(encoding="utf-8").strip().endswith("Corps du kit.")


def test_extends_kit_sans_agent_kit_refuse_nommement(tmp_path: Path) -> None:
    root = tmp_path
    _override(root, "fantome", ["extends: kit", 'description: "Rien derrière."'])

    with pytest.raises(GrimoireAgentError, match="fantome"):
        collect_agents(root, known_skills=frozenset())


def test_extends_kit_conserve_les_champs_non_redefinis(tmp_path: Path) -> None:
    """Les champs que l'override ne touche pas (ici `tools`) restent ceux du kit."""
    root = tmp_path
    _kit_agent(root, "monitoring-specialist", extra=["tools: [read, edit]"])
    _override(root, "monitoring-specialist", ["extends: kit", "context: ['project-context.yaml']"])
    (root / "project-context.yaml").write_text("project: {}\n", encoding="utf-8")

    agents = collect_agents(root, known_skills=frozenset())
    agent = next(a for a in agents if a.name == "monitoring-specialist")
    assert {t.value for t in agent.tools} == {"read", "edit"}
    assert agent.context == ("project-context.yaml",)


# ── Signal de dérive (volet 1) ───────────────────────────────────────────────


def test_copie_integrale_perimee_est_signalee_warn_avec_section(tmp_path: Path) -> None:
    root = tmp_path
    kit_path = _kit_agent(root, "concierge", body="Corps original.\n")
    h = compute_kit_source_hash(kit_path)
    kit_text = kit_path.read_text(encoding="utf-8")
    override_text = kit_text.replace("---\n", f"---\nkit_source_hash: {h}\n", 1)
    (root / OVERRIDE_AGENTS).mkdir(parents=True, exist_ok=True)
    (root / OVERRIDE_AGENTS / "concierge.md").write_text(override_text, encoding="utf-8")

    # Le kit change sous la copie — nouvelle section de frontmatter, corps modifié.
    kit_path.write_text(
        kit_text.replace("---\n", "---\nmax_turns: 5\n", 1).rstrip("\n") + "\nLigne ajoutée par le kit.\n",
        encoding="utf-8",
    )

    drifts = project_override_drift(root)
    drift = next(d for d in drifts if d.name == "concierge")
    assert drift.status == "stale"
    level, detail = describe_drift(drift)
    assert level == "warn"
    assert "concierge" in detail
    assert "max_turns" in drift.added_sections


def test_override_sans_empreinte_est_info_empreinte_inconnue(tmp_path: Path) -> None:
    root = tmp_path
    _kit_agent(root, "systems-debugger")
    _override(root, "systems-debugger", ["extends: kit", "model_affinity: {reasoning: high}"])

    drifts = project_override_drift(root)
    drift = next(d for d in drifts if d.name == "systems-debugger")
    assert drift.status == "unknown"
    level, detail = describe_drift(drift)
    assert level == "info"
    assert "empreinte inconnue, revoir à la main" in detail


def test_override_a_jour_n_est_pas_signale(tmp_path: Path) -> None:
    root = tmp_path
    kit_path = _kit_agent(root, "ops-engineer")
    h = compute_kit_source_hash(kit_path)
    _override(root, "ops-engineer", ["extends: kit", f"kit_source_hash: {h}"])

    drifts = project_override_drift(root)
    drift = next(d for d in drifts if d.name == "ops-engineer")
    assert drift.status == "fresh"


# ── `grimoire agent override convert` (volet 3) ─────────────────────────────


def test_convert_dry_run_n_ecrit_rien(tmp_path: Path) -> None:
    root = tmp_path
    kit_path = _kit_agent(root, "ops-engineer", extra=["model_affinity: {reasoning: medium}"])
    kit_text = kit_path.read_text(encoding="utf-8")
    override_path = root / OVERRIDE_AGENTS / "ops-engineer.md"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(kit_text.replace("reasoning: medium", "reasoning: high"), encoding="utf-8")
    before = override_path.read_text(encoding="utf-8")

    result = convert_override(root, "ops-engineer", dry_run=True)

    assert override_path.read_text(encoding="utf-8") == before, "dry-run n'écrit rien"
    assert not result.written
    assert "extends: kit" in result.rendered


def test_convert_reel_produit_un_override_partiel_equivalent(tmp_path: Path) -> None:
    root = tmp_path
    kit_path = _kit_agent(root, "ops-engineer", extra=["model_affinity: {reasoning: medium}"])
    kit_text = kit_path.read_text(encoding="utf-8")
    override_path = root / OVERRIDE_AGENTS / "ops-engineer.md"
    override_path.parent.mkdir(parents=True, exist_ok=True)
    override_path.write_text(kit_text.replace("reasoning: medium", "reasoning: high"), encoding="utf-8")

    result = convert_override(root, "ops-engineer", dry_run=False)
    assert result.written

    agents = collect_agents(root, known_skills=frozenset())
    agent = next(a for a in agents if a.name == "ops-engineer")
    assert agent.override_kind == "partial"
    assert agent.affinity.reasoning == "high"
    assert agent.description == "ops-engineer — persona de test"


def test_convert_refuse_quand_le_corps_diverge(tmp_path: Path) -> None:
    root = tmp_path
    _kit_agent(root, "ops-engineer", body="Corps du kit.\n")
    _override(root, "ops-engineer", ['name: "ops-engineer"', 'description: "ops-engineer — persona de test"'], body="Corps édité à la main.\n")

    with pytest.raises(OverrideConversionRefusedError, match="corps"):
        convert_override(root, "ops-engineer")

    # Refus atomique : le fichier n'a pas bougé.
    assert "Corps édité à la main." in (root / OVERRIDE_AGENTS / "ops-engineer.md").read_text(encoding="utf-8")


# ── Bout en bout : `doctor`, `up`, la commande CLI ──────────────────────────


def _grimoire(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["GRIMOIRE_NO_COCKPIT"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "grimoire", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )


@pytest.fixture(scope="module")
def drifted_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Un vrai projet initialisé, avec une copie intégrale rendue périmée."""
    root = tmp_path_factory.mktemp("override-427") / "projet"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=False, capture_output=True)
    created = _grimoire(["init", ".", "-y", "--name", "projet-427"], root)
    if not (root / "_grimoire" / "kit" / "agents").is_dir():
        pytest.skip(f"`grimoire init` indisponible ici : {created.stderr[-400:]}")

    from grimoire.core.layout import agent_identity

    kit_dir = root / KIT_AGENTS
    candidates = [
        p
        for p in kit_dir.glob("*.md")
        if not p.name.endswith(".tpl.md") and p.stem != "custom-agent" and agent_identity(p) is not None
    ]
    kit_path = candidates[0]
    agent_name = kit_path.stem
    kit_text = kit_path.read_text(encoding="utf-8")
    override_dir = root / OVERRIDE_AGENTS
    override_dir.mkdir(parents=True, exist_ok=True)
    # Empreinte délibérément fausse — un hash qu'aurait produit une version du
    # kit antérieure à celle installée ici. Ne pas modifier le fichier kit
    # lui-même : `grimoire up` le régénère depuis l'archétype installé à sa
    # première étape (`refresh`), donc un bidouillage du contenu kit ne
    # survivrait pas jusqu'à l'étape `override_review` qui le suit — la seule
    # façon fidèle de simuler « le kit a bougé depuis que cette copie a été
    # prise » est une empreinte enregistrée qui ne correspond à aucune version
    # installée, jamais une modification transitoire du fichier kit.
    (override_dir / f"{agent_name}.md").write_text(
        kit_text.replace("---\n", "---\nkit_source_hash: 0000000000000000\n", 1), encoding="utf-8"
    )
    return root


def test_doctor_warn_nomme_l_agent_et_ne_fail_jamais(drifted_project: Path) -> None:
    proc = _grimoire(["doctor", "."], drifted_project)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = proc.stdout + proc.stderr
    assert "WARN" in output
    assert "obsolète" in output


def test_up_liste_sans_toucher(drifted_project: Path) -> None:
    agent_name = next(p.stem for p in (drifted_project / OVERRIDE_AGENTS).glob("*.md"))
    override_path = drifted_project / OVERRIDE_AGENTS / f"{agent_name}.md"
    before = override_path.read_text(encoding="utf-8")

    proc = _grimoire(["up", "."], drifted_project)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = proc.stdout + proc.stderr
    assert "override_review" in output
    assert agent_name in output
    assert override_path.read_text(encoding="utf-8") == before, "`up` ne doit jamais écrire l'override"


def test_cli_convert_dry_run_puis_reel(drifted_project: Path) -> None:
    agent_name = next(p.stem for p in (drifted_project / OVERRIDE_AGENTS).glob("*.md"))
    override_path = drifted_project / OVERRIDE_AGENTS / f"{agent_name}.md"
    before = override_path.read_text(encoding="utf-8")

    dry = _grimoire(["agent", "override", "convert", agent_name, "--dry-run"], drifted_project)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert override_path.read_text(encoding="utf-8") == before

    real = _grimoire(["agent", "override", "convert", agent_name], drifted_project)
    assert real.returncode == 0, real.stdout + real.stderr
    after = override_path.read_text(encoding="utf-8")
    assert "extends: kit" in after
    assert override_path.read_text(encoding="utf-8") != before

    # `doctor` ne signale plus de dérive pour cet agent : l'empreinte est fraîche.
    proc = _grimoire(["doctor", "."], drifted_project)
    assert f"override_drift_{agent_name}" not in (proc.stdout + proc.stderr).replace(" ", "")
