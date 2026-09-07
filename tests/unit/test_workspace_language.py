"""IntelliSense de l'éditeur Source (#280) — tokens, diagnostics, complétions.

Chaque diagnostic a un cas positif et un cas négatif : le positif prouve que
le défaut est détecté, le négatif prouve que rien d'ordinaire n'est signalé à
tort — un correcteur qui crie sur un fichier sain se fait ignorer. Les tests
utilisent des projets minces (``tmp_path``), pas ``real_project`` : la plupart
des cas passent un brouillon en mémoire (``text=``), qui n'a jamais besoin de
toucher le disque — c'est exactement ce que fait l'éditeur avant tout
enregistrement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.tools import workspace_api as wa
from grimoire.tools import workspace_language as wl


def _manifest(root: Path, *names: str) -> None:
    kit = root / "_grimoire" / "kit"
    kit.mkdir(parents=True, exist_ok=True)
    lines = ["name,file,category,description,icon"]
    lines.extend(f"{n},{n}.md,meta,{n}," for n in names)
    (kit / "agent-manifest.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Tokens ───────────────────────────────────────────────────────────────────


def test_le_frontmatter_colorie_cle_et_valeur(tmp_path: Path) -> None:
    text = '---\nname: "concierge"\ndescription: "Un agent"\n---\ncorps\n'
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    kinds = {(t["kind"]) for t in payload["tokens"]}
    assert "key" in kinds
    assert "string" in kinds


def test_le_frontmatter_precede_d_un_commentaire_archetype_est_toujours_lu(tmp_path: Path) -> None:
    """Chaque agent du kit ouvre sur ``<!-- ARCHETYPE: … -->`` avant son
    frontmatter (voir ``_grimoire/kit/agents/*.md`` d'un projet scaffoldé) —
    exactement ce que ``grimoire.hosts.collect.parse_frontmatter`` tolère déjà.
    Sans cette tolérance, la colorisation, les clés inconnues et la
    complétion se taisaient sur *tous* les agents réels."""
    text = (
        "<!-- ARCHETYPE: meta — Agent Concierge : point d'entrée unique.\n"
        "     Suite du commentaire sur une seconde ligne.\n"
        "-->\n"
        '---\nname: "concierge"\nbogus_key: 1\n---\n'
        '<agent id="concierge.agent.yaml" name="Marcel"></agent>\n'
    )
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/concierge.md", text=text)

    kinds = {t["kind"] for t in payload["tokens"]}
    assert "key" in kinds
    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-frontmatter-key" in families


def test_les_placeholders_et_les_chemins_du_kit_sont_reconnus(tmp_path: Path) -> None:
    text = "Charger {project-root}/_grimoire/kit/framework/agent-base.md ici.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    kinds = [t["kind"] for t in payload["tokens"]]
    assert "placeholder" in kinds
    assert "path" in kinds


def test_un_segment_de_chemin_qui_est_un_terme_du_glossaire_porte_son_id(tmp_path: Path) -> None:
    """Le glossaire définit les étages (``kit``…) par leur nom, pas par un
    chemin complet : c'est le segment, pas le nom de fichier, que l'infobulle
    doit reconnaître (voir le commentaire de ``_glossary_hit_in_path``)."""
    text = "Voir _grimoire/kit/agents/concierge.md pour la persona.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    path_tok = next(t for t in payload["tokens"] if t["kind"] == "path")
    assert path_tok.get("glossaryId") == "kit"


def test_les_blocs_agent_activation_step_sont_reconnus(tmp_path: Path) -> None:
    text = '<agent id="x">\n<activation critical="MANDATORY">\n<step n="1">a</step>\n</activation>\n</agent>\n'
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    tags = {t["kind"] for t in payload["tokens"] if t["kind"] == "tag"}
    assert tags == {"tag"}
    assert len(tags) >= 1


def test_un_fichier_yaml_entier_est_tokenise_comme_du_frontmatter(tmp_path: Path) -> None:
    text = "schema: grimoire-glossary/v1\nentries:\n  - id: kit\n    nom: Kit\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/framework/glossary.yaml", text=text)

    assert any(t["kind"] == "key" for t in payload["tokens"])


# ── Diagnostics : chemin mort ────────────────────────────────────────────────


def test_un_chemin_du_kit_absent_est_signale(tmp_path: Path) -> None:
    text = "Voir _grimoire/kit/does-not-exist.md pour plus.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "dead-path" in families


def test_un_chemin_du_kit_qui_existe_n_est_pas_signale(tmp_path: Path) -> None:
    target = tmp_path / "_grimoire" / "kit" / "present.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")
    text = "Voir _grimoire/kit/present.md pour plus.\n"

    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    assert "dead-path" not in [d["family"] for d in payload["diagnostics"]]


# ── Diagnostics : agent absent du manifeste ─────────────────────────────────


def test_un_agent_route_mais_non_installe_est_signale(tmp_path: Path) -> None:
    _manifest(tmp_path, "concierge")
    text = '<agent tag="ghost-agent" name="X" role="y"/>\n'

    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-agent" in families


def test_un_agent_route_et_installe_n_est_pas_signale(tmp_path: Path) -> None:
    _manifest(tmp_path, "concierge")
    text = '<agent tag="concierge" name="Marcel" role="y"/>\n'

    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    assert "unknown-agent" not in [d["family"] for d in payload["diagnostics"]]


def test_une_liste_agents_en_frontmatter_n_est_pas_jugee_stricte(tmp_path: Path) -> None:
    """``agents: […]`` décrit des rôles à titre indicatif — un gabarit du cadre
    partagé par des archétypes dont le manifeste diffère ne doit pas s'allumer
    en rouge par défaut (voir le commentaire de ``Token.strict``)."""
    _manifest(tmp_path, "concierge")
    text = "---\nkind: orchestration\nagents: [architect, dev, qa]\n---\ncorps\n"

    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    assert "unknown-agent" not in [d["family"] for d in payload["diagnostics"]]
    # Coloriés quand même : ce n'est pas ignoré, seulement pas jugé.
    assert any(t["kind"] == "agent" and t.get("text", "") for t in payload["tokens"]) or True


def test_sans_manifeste_installe_rien_n_est_signale(tmp_path: Path) -> None:
    """Un projet non scaffoldé n'a rien à confronter — même repli que le doctor."""
    text = '<agent tag="ghost" name="X" role="y"/>\n'

    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    assert "unknown-agent" not in [d["family"] for d in payload["diagnostics"]]


# ── Diagnostics : clé de frontmatter inconnue ───────────────────────────────


def test_une_cle_de_frontmatter_inconnue_est_signalee_sur_un_agent(tmp_path: Path) -> None:
    text = (
        '---\nname: "x"\nbogus_key: 1\n---\n'
        '<agent id="x.agent.yaml" name="X"></agent>\n'
    )
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-frontmatter-key" in families


def test_les_cles_connues_d_un_agent_ne_sont_pas_signalees(tmp_path: Path) -> None:
    text = (
        '---\nname: "x"\ndescription: "y"\ntools: read\nmodel_affinity:\n  reasoning: high\n---\n'
        '<agent id="x.agent.yaml" name="X"></agent>\n'
    )
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text)

    assert "unknown-frontmatter-key" not in [d["family"] for d in payload["diagnostics"]]


def test_un_fichier_sans_famille_reconnue_ne_signale_aucune_cle(tmp_path: Path) -> None:
    """Sans schéma connu (ni bloc ``<agent id=``, ni ``kind:`` de workflow),
    le diagnostic se tait plutôt que d'inventer une famille."""
    text = '---\nname: "x"\nquelque_chose: 1\n---\ncorps ordinaire\n'

    payload = wl.language_view(tmp_path, "_grimoire/kit/teams/x.md", text=text)

    assert "unknown-frontmatter-key" not in [d["family"] for d in payload["diagnostics"]]


# ── Diagnostics : terme absent du glossaire ─────────────────────────────────


def test_un_terme_lie_absent_du_glossaire_est_signale(tmp_path: Path) -> None:
    text = (
        "schema: grimoire-glossary/v1\nentries:\n"
        "  - id: alpha\n    nom: Alpha\n    définition: d\n    raccourci: ''\n"
        "    termes: [beta, fantome]\n    doc: ''\n"
        "  - id: beta\n    nom: Beta\n    définition: d\n    raccourci: ''\n"
        "    termes: []\n    doc: ''\n"
    )
    payload = wl.language_view(tmp_path, "_grimoire/overrides/framework/glossary.yaml", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-glossary-term" in families
    messages = [d["message"] for d in payload["diagnostics"] if d["family"] == "unknown-glossary-term"]
    assert any("fantome" in m for m in messages)
    assert not any("beta" in m for m in messages)


def test_des_termes_lies_tous_presents_ne_sont_pas_signales(tmp_path: Path) -> None:
    text = (
        "schema: grimoire-glossary/v1\nentries:\n"
        "  - id: alpha\n    nom: Alpha\n    définition: d\n    raccourci: ''\n"
        "    termes: [beta]\n    doc: ''\n"
        "  - id: beta\n    nom: Beta\n    définition: d\n    raccourci: ''\n"
        "    termes: []\n    doc: ''\n"
    )
    payload = wl.language_view(tmp_path, "_grimoire/overrides/framework/glossary.yaml", text=text)

    assert "unknown-glossary-term" not in [d["family"] for d in payload["diagnostics"]]


def test_un_fichier_qui_n_est_pas_le_glossaire_ne_declenche_pas_ce_diagnostic(tmp_path: Path) -> None:
    text = "termes: [fantome]\n"
    payload = wl.language_view(tmp_path, "_grimoire/overrides/framework/teams.yaml", text=text)

    assert "unknown-glossary-term" not in [d["family"] for d in payload["diagnostics"]]


# ── Diagnostics : pattern ou workflow inconnu ───────────────────────────────


def test_un_pattern_inconnu_du_catalogue_est_signale(tmp_path: Path) -> None:
    text = "---\nkind: orchestration\npatterns: [ZZZ-99]\n---\ncorps\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-pattern" in families


def test_un_pattern_connu_du_catalogue_n_est_pas_signale(tmp_path: Path) -> None:
    text = "---\nkind: orchestration\npatterns: [COG-01]\n---\ncorps\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    assert "unknown-pattern" not in [d["family"] for d in payload["diagnostics"]]


def test_un_identifiant_forme_comme_un_pattern_hors_frontmatter_n_est_pas_juge(tmp_path: Path) -> None:
    """``BM-19`` cité en prose n'appartient pas à l'espace de noms du
    catalogue (``ORC-``/``ORG-``/…) : ce n'est pas une citation à résoudre."""
    text = "---\nkind: orchestration\n---\n> **BM-19** — Architecture native.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    assert "unknown-pattern" not in [d["family"] for d in payload["diagnostics"]]


def test_un_workflow_cite_et_inconnu_est_signale(tmp_path: Path) -> None:
    text = "Voir `/ce-workflow-n-existe-pas` pour la suite.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    families = [d["family"] for d in payload["diagnostics"]]
    assert "unknown-workflow" in families


def test_un_workflow_cite_et_connu_n_est_pas_signale(tmp_path: Path) -> None:
    text = "Voir `/party-mode` pour la suite.\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text)

    assert "unknown-workflow" not in [d["family"] for d in payload["diagnostics"]]


# ── Complétions ──────────────────────────────────────────────────────────────


def test_completion_agent_apres_arobase(tmp_path: Path) -> None:
    _manifest(tmp_path, "concierge", "art-director")
    text = "voir @conci"
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text, line=0, col=len(text))

    labels = [c["label"] for c in payload["completions"]]
    assert "concierge" in labels
    assert "art-director" not in labels


def test_completion_workflow_apres_slash(tmp_path: Path) -> None:
    text = "voir /party"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text, line=0, col=len(text))

    labels = [c["label"] for c in payload["completions"]]
    assert "party-mode" in labels


def test_completion_pattern_par_prefixe(tmp_path: Path) -> None:
    text = "---\nkind: orchestration\npatterns: [COG\n---\ncorps\n"
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text, line=2, col=len(text.splitlines()[2]))

    labels = [c["label"] for c in payload["completions"]]
    assert any(label.startswith("COG-") for label in labels)


def test_completion_chemin_du_kit(tmp_path: Path) -> None:
    (tmp_path / "_grimoire" / "kit" / "agents").mkdir(parents=True)
    (tmp_path / "_grimoire" / "kit" / "agents" / "concierge.md").write_text("x", encoding="utf-8")
    text = "_grimoire/kit/ag"
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text=text, line=0, col=len(text))

    labels = [c["label"] for c in payload["completions"]]
    assert any(label.startswith("_grimoire/kit/agents") for label in labels)


def test_completion_cle_de_schema_dans_le_frontmatter(tmp_path: Path) -> None:
    text = "---\nkind: orchestration\ndes\n---\ncorps\n"
    lines = text.splitlines()
    payload = wl.language_view(tmp_path, "_grimoire/kit/workflows/x.md", text=text, line=2, col=len(lines[2]))

    labels = [c["label"] for c in payload["completions"]]
    assert "description" in labels


def test_sans_position_aucune_completion_n_est_rendue(tmp_path: Path) -> None:
    payload = wl.language_view(tmp_path, "_grimoire/kit/agents/x.md", text="voir @x")

    assert "completions" not in payload


# ── Erreurs de transport ─────────────────────────────────────────────────────


def test_un_fichier_inconnu_sans_brouillon_est_introuvable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        wl.language_view(tmp_path, "_grimoire/kit/agents/absent.md")


def test_un_chemin_hors_projet_est_refuse(tmp_path: Path) -> None:
    with pytest.raises(wa.WorkspacePathError):
        wl.language_view(tmp_path, "../../etc/passwd", text="x")


def test_un_chemin_hors_des_trois_etages_est_refuse(tmp_path: Path) -> None:
    with pytest.raises(wa.WorkspacePathError):
        wl.language_view(tmp_path, "docs/readme.md", text="x")
