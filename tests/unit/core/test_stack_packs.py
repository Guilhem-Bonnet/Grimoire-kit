"""Packs de règles par stack (W6-08) : chaque règle est vérifiée ou advisory.

Chez AI-DLC, les règles d'organisation livrées avec le framework sont de la
prose (``core/memory/org.md``) : rien ne dit si le code les respecte. Ici un pack
est un fichier de règles de la grammaire de W6-03 où chaque règle porte un
vérificateur lu sur le code du projet, ou se déclare ``advisory`` et se compte
à part. Un pack de plus de 50 % d'advisory est refusé au chargement ; la CI
charge tous les packs livrés.

Vrais fichiers, vraie CLI du noyau : aucun double, sauf la panne simulée d'un
pack du kit.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

import grimoire.core.stack_pack_verifiers as verifiers
from grimoire.core.agentic_standard import calculate_compliance_score, verify_standard_profile
from grimoire.core.layered_rules import add_rule, analyze
from grimoire.core.stack_pack_verifiers import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNEVALUATED,
    Corpus,
    Verifier,
    verify,
)
from grimoire.core.stack_packs import (
    PACK_SCHEMA,
    StackPackError,
    evaluate_project,
    install_pack,
    load_pack_file,
    load_packs,
    packs_dir,
    parse_pack,
    rules_score_fields,
)

SHIPPED = sorted(packs_dir().glob("*.yaml"))


def _verified(text: str, **verifier: Any) -> dict[str, Any]:
    return {"rule": text, "rationale": "parce que", "verifier": verifier}


def _advisory(text: str) -> dict[str, Any]:
    return {"rule": text, "rationale": "parce que", "advisory": True}


BARE = _verified("NEVER use bare-except", kind="python_ast", check="bare_except")
PIN = _verified("ALWAYS pin requires-python @pyproject.toml", kind="require_file", paths=["pyproject.toml"])


def _pack(*rules: dict[str, Any], **top: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"$schema": PACK_SCHEMA, "id": "demo", "stack": "demo", "title": "Démo", "rules": list(rules)}
    data.update(top)
    return data


def _code(data: object, expected_id: str | None = "demo") -> str:
    with pytest.raises(StackPackError) as info:
        parse_pack(data, expected_id=expected_id)
    return info.value.code


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── Le schéma exige un vérificateur ou advisory ───────────────────────────────


def test_un_pack_valide_se_charge() -> None:
    pack = parse_pack(_pack(BARE, PIN, _advisory("ALWAYS prefer pathlib")))
    assert (pack.verified, pack.advisory) == (2, 1)
    assert pack.rules[0].verifier is not None
    assert pack.rules[2].advisory


def test_une_regle_sans_verificateur_ni_advisory_est_refusee() -> None:
    assert _code(_pack(BARE, {"rule": "ALWAYS prefer pathlib", "rationale": "x"})) == "stack_pack.rule_unclassified"


def test_advisory_false_ne_vaut_pas_declaration() -> None:
    assert _code(_pack(BARE, {"rule": "ALWAYS prefer pathlib", "rationale": "x", "advisory": False})) == "stack_pack.rule_unclassified"


def test_une_regle_verifiee_et_advisory_est_refusee() -> None:
    both = {**BARE, "advisory": True}
    assert _code(_pack(both)) == "stack_pack.rule_both"


def test_plus_de_la_moitie_advisory_est_refuse() -> None:
    data = _pack(BARE, _advisory("ALWAYS prefer pathlib"), _advisory("ALWAYS annotate public-functions"))
    assert _code(data) == "stack_pack.advisory_ratio"


def test_exactement_la_moitie_advisory_est_admise() -> None:
    pack = parse_pack(_pack(BARE, _advisory("ALWAYS prefer pathlib")))
    assert (pack.verified, pack.advisory) == (1, 1)


def test_un_pack_entierement_advisory_est_refuse() -> None:
    assert _code(_pack(_advisory("ALWAYS prefer pathlib"))) == "stack_pack.advisory_ratio"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d.update({"$schema": "autre/v1"}), "stack_pack.schema"),
        (lambda d: d.update({"inconnue": 1}), "stack_pack.schema"),
        (lambda d: d.update({"id": "Demo!"}), "stack_pack.schema"),
        (lambda d: d.update({"id": "autre"}), "stack_pack.schema"),
        (lambda d: d.update({"rules": []}), "stack_pack.schema"),
        (lambda d: d.update({"title": ""}), "stack_pack.schema"),
        (lambda d: d["rules"][0].update({"rule": "never use bare-except"}), "stack_pack.rule_syntax"),
        (lambda d: d["rules"][0].update({"rule": "NEVER use Bare-Except"}), "stack_pack.rule_not_canonical"),
        (lambda d: d["rules"][0].update({"surprise": True}), "stack_pack.schema"),
        (lambda d: d["rules"][0].update({"rationale": ""}), "stack_pack.schema"),
        (lambda d: d["rules"][0]["verifier"].update({"kind": "shell"}), "stack_pack.verifier"),
        (lambda d: d["rules"][0]["verifier"].update({"check": "inconnu"}), "stack_pack.verifier"),
        (lambda d: d["rules"][0]["verifier"].update({"command": "rm -rf ."}), "stack_pack.verifier"),
        (lambda d: d["rules"][0]["verifier"].update({"paths": ["../etc/*"]}), "stack_pack.verifier"),
        (lambda d: d["rules"][0]["verifier"].update({"paths": ["/etc/passwd"]}), "stack_pack.verifier"),
        (lambda d: d["rules"][0]["verifier"].update({"paths": []}), "stack_pack.verifier"),
        (lambda d: d["rules"].append(copy.deepcopy(d["rules"][0])), "stack_pack.duplicate_rule"),
        (lambda d: d["rules"].append(_verified("ALWAYS use bare-except", kind="python_ast", check="bare_except")), "stack_pack.conflict"),
    ],
)
def test_un_pack_hors_schema_est_refuse(mutate: Any, code: str) -> None:
    data = _pack(copy.deepcopy(BARE), copy.deepcopy(PIN))
    mutate(data)
    assert _code(data) == code


def test_un_motif_illisible_est_refuse() -> None:
    bad = _verified("NEVER use todo-markers", kind="forbid_regex", pattern="(", paths=["**/*.py"])
    assert _code(_pack(bad)) == "stack_pack.verifier"


def test_une_nature_sans_motif_est_refusee() -> None:
    bad = _verified("NEVER use todo-markers", kind="forbid_regex", paths=["**/*.py"])
    assert _code(_pack(bad)) == "stack_pack.schema"


def test_un_pack_qui_n_est_pas_un_mapping_est_refuse() -> None:
    assert _code(["rules"]) == "stack_pack.schema"


def test_un_fichier_yaml_illisible_est_refuse(tmp_path: Path) -> None:
    (tmp_path / "demo.yaml").write_text("rules: [oups\n", encoding="utf-8")
    with pytest.raises(StackPackError) as info:
        load_pack_file(tmp_path / "demo.yaml")
    assert info.value.code == "stack_pack.unreadable"


def test_une_regle_dans_deux_packs_est_refusee(tmp_path: Path) -> None:
    yaml = YAML(typ="safe")
    for name in ("un", "deux"):
        with (tmp_path / f"{name}.yaml").open("w", encoding="utf-8") as handle:
            yaml.dump(_pack(copy.deepcopy(BARE), id=name), handle)
    with pytest.raises(StackPackError) as info:
        load_packs(tmp_path)
    assert info.value.code == "stack_pack.duplicate_rule"


# ── Les packs livrés : la porte de CI ─────────────────────────────────────────


def test_il_existe_au_moins_un_pack_livre() -> None:
    assert [p.name for p in SHIPPED] == ["python.yaml"]


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_chaque_pack_livre_se_charge_et_tient_le_plafond_advisory(path: Path) -> None:
    pack = load_pack_file(path)
    assert pack.advisory * 2 <= len(pack.rules)
    assert all(r.verifier is not None or r.advisory for r in pack.rules)


def test_le_pack_python_a_cinq_regles_dont_trois_verifiees() -> None:
    pack = next(p for p in load_packs() if p.id == "python")
    assert len(pack.rules) >= 5
    assert pack.verified >= 3


@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_chaque_regle_livree_passe_la_frontiere_de_confiance(path: Path, tmp_path: Path) -> None:
    pack = load_pack_file(path)
    outcome = install_pack(tmp_path, pack, "project")
    assert outcome.refused == ()
    assert len(outcome.written) == len(pack.rules)
    assert install_pack(tmp_path, pack, "project").present == outcome.written


def test_un_conflit_avec_une_couche_exterieure_refuse_la_regle_sans_bloquer_les_autres(tmp_path: Path) -> None:
    add_rule(tmp_path, "organization", "ALWAYS use bare-except")
    pack = next(p for p in load_packs() if p.id == "python")
    outcome = install_pack(tmp_path, pack, "project")
    assert [(rule, code) for rule, code, _ in outcome.refused] == [("NEVER use bare-except", "rules.conflict")]
    assert len(outcome.written) == len(pack.rules) - 1
    assert "NEVER use bare-except" not in (tmp_path / "_grimoire/standard/rules/project.rules").read_text(encoding="utf-8")


# ── Vérificateurs : trois états, jamais deux ──────────────────────────────────


def _ast(check: str, paths: tuple[str, ...] = ("**/*.py",)) -> Verifier:
    return Verifier(kind="python_ast", paths=paths, check=check)


@pytest.mark.parametrize(
    ("check", "bad", "good"),
    [
        ("bare_except", "try:\n    pass\nexcept:\n    pass\n", "try:\n    pass\nexcept ValueError:\n    pass\n"),
        ("mutable_default_argument", "def f(a=[]):\n    pass\n", "def f(a=None):\n    pass\n"),
        ("mutable_default_argument", "def f(*, a={}):\n    pass\n", "def f(*, a=()):\n    pass\n"),
        ("mutable_default_argument", "g = lambda a=set(): a\n", "g = lambda a=frozenset(): a\n"),
        ("mutable_default_argument", "async def f(a=dict()):\n    pass\n", "async def f(a=1):\n    pass\n"),
        ("eval_call", "x = eval('1')\n", "import ast\nx = ast.literal_eval('1')\n"),
    ],
)
def test_controles_ast_positif_et_negatif(tmp_path: Path, check: str, bad: str, good: str) -> None:
    _write(tmp_path, "bad/m.py", bad)
    verdict = verify(Corpus(tmp_path), _ast(check, ("bad/*.py",)))
    assert verdict.status == STATUS_FAIL
    assert verdict.findings[0].path == "bad/m.py"
    assert verdict.findings[0].line >= 1
    _write(tmp_path, "good/m.py", good)
    assert verify(Corpus(tmp_path), _ast(check, ("good/*.py",))).status == STATUS_PASS


def test_forbid_regex_localise_la_ligne(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "x = 1\n# TODO nettoyer\n")
    v = Verifier(kind="forbid_regex", paths=("src/**",), pattern=r"TODO")
    verdict = verify(Corpus(tmp_path), v)
    assert verdict.status == STATUS_FAIL
    assert verdict.findings[0].line == 2


def test_require_regex_passe_echoue_et_ne_confond_pas_fichier_absent(tmp_path: Path) -> None:
    v = Verifier(kind="require_regex", paths=("pyproject.toml",), pattern=r"^requires-python\s*=")
    assert verify(Corpus(tmp_path), v).status == STATUS_FAIL  # le fichier requis n'existe pas
    _write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    assert verify(Corpus(tmp_path), v).status == STATUS_FAIL
    _write(tmp_path, "pyproject.toml", "[project]\nrequires-python = '>=3.11'\n")
    assert verify(Corpus(tmp_path), v).status == STATUS_PASS


def test_require_file(tmp_path: Path) -> None:
    v = Verifier(kind="require_file", paths=("LICENSE*",))
    assert verify(Corpus(tmp_path), v).status == STATUS_FAIL
    _write(tmp_path, "LICENSE.md", "x")
    assert verify(Corpus(tmp_path), v).status == STATUS_PASS


def test_une_portee_vide_n_est_pas_un_succes(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "rien de Python ici\n")
    verdict = verify(Corpus(tmp_path), _ast("bare_except"))
    assert verdict.status == STATUS_UNEVALUATED
    assert "rien n'a été lu" in verdict.reason


def test_un_fichier_illisible_empeche_le_succes_mais_pas_l_echec(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", "x = 1\n")
    (tmp_path / "latin.py").write_bytes("# é\n".encode("latin-1"))
    assert verify(Corpus(tmp_path), _ast("bare_except")).status == STATUS_UNEVALUATED
    _write(tmp_path, "mauvais.py", "try:\n    pass\nexcept:\n    pass\n")
    assert verify(Corpus(tmp_path), _ast("bare_except")).status == STATUS_FAIL


def test_une_syntaxe_invalide_n_est_pas_un_succes(tmp_path: Path) -> None:
    _write(tmp_path, "casse.py", "def (:\n")
    verdict = verify(Corpus(tmp_path), _ast("bare_except"))
    assert verdict.status == STATUS_UNEVALUATED
    assert "casse.py" in verdict.reason


def test_un_fichier_trop_gros_n_est_pas_lu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifiers, "MAX_FILE_BYTES", 10)
    _write(tmp_path, "gros.py", "x = 1\n" * 10)
    assert verify(Corpus(tmp_path), _ast("bare_except")).status == STATUS_UNEVALUATED


def test_un_parcours_tronque_n_est_pas_un_succes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifiers, "MAX_FILES", 2)
    for name in ("a", "b", "c"):
        _write(tmp_path, f"{name}.py", "x = 1\n")
    corpus = Corpus(tmp_path)
    assert corpus.truncated
    assert verify(corpus, _ast("bare_except")).status == STATUS_UNEVALUATED
    assert verify(corpus, Verifier(kind="require_file", paths=("zzz.txt",))).status == STATUS_UNEVALUATED


def test_les_dossiers_generes_ne_sont_pas_parcourus(tmp_path: Path) -> None:
    for folder in (".venv/lib", "node_modules/x", "_grimoire/standard"):
        _write(tmp_path, f"{folder}/m.py", "try:\n    pass\nexcept:\n    pass\n")
    _write(tmp_path, "src/ok.py", "x = 1\n")
    assert verify(Corpus(tmp_path), _ast("bare_except")).status == STATUS_PASS


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="liens symboliques indisponibles")
def test_un_lien_symbolique_ne_fait_pas_lire_hors_de_la_racine(tmp_path: Path) -> None:
    outside = tmp_path / "dehors"
    outside.mkdir()
    _write(outside, "secret.py", "try:\n    pass\nexcept:\n    pass\n")
    root = tmp_path / "proj"
    root.mkdir()
    _write(root, "ok.py", "x = 1\n")
    try:
        (root / "lien.py").symlink_to(outside / "secret.py")
        (root / "dossier").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("création de lien symbolique refusée")
    corpus = Corpus(root)
    assert corpus.paths == ("ok.py",)
    assert verify(corpus, _ast("bare_except")).status == STATUS_PASS


def test_une_nature_inconnue_leve(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inconnue"):
        verify(Corpus(tmp_path), Verifier(kind="shell", paths=("*",)))


# ── Évaluation d'un projet ────────────────────────────────────────────────────


def _install_python(root: Path) -> None:
    pack = next(p for p in load_packs() if p.id == "python")
    assert install_pack(root, pack, "project").refused == ()


def _clean_project(root: Path) -> None:
    _write(root, "pyproject.toml", "[project]\nrequires-python = '>=3.11'\n")
    _write(root, ".gitignore", ".venv/\n")
    _write(root, "src/app.py", "def f(a=None):\n    return a\n")


def test_un_projet_conforme_passe_toutes_les_regles_verifiees(tmp_path: Path) -> None:
    _clean_project(tmp_path)
    _install_python(tmp_path)
    report = evaluate_project(tmp_path, analyze(tmp_path).layers)
    assert (report.enforced, report.advisory, report.failing, report.unevaluated, report.unclassified) == (5, 2, 0, 0, 0)
    assert {s.status for s in report.statuses if s.status != "advisory"} == {STATUS_PASS}


def test_un_projet_fautif_echoue_regle_par_regle(tmp_path: Path) -> None:
    _write(tmp_path, "m.py", "def f(a=[]):\n    try:\n        return eval('1')\n    except:\n        pass\n")
    _install_python(tmp_path)
    report = evaluate_project(tmp_path, analyze(tmp_path).layers)
    failing = {s.rule for s in report.statuses if s.status == STATUS_FAIL}
    assert failing == {
        "NEVER use bare-except", "NEVER use mutable-default-arguments", "NEVER call eval",
        "ALWAYS pin requires-python @pyproject.toml", "ALWAYS list .venv @.gitignore",
    }


def test_une_regle_hors_pack_est_comptee_a_part(tmp_path: Path) -> None:
    add_rule(tmp_path, "project", "ALWAYS run unit-tests before commit")
    add_rule(tmp_path, "project", "NEVER use bare-except")
    report = evaluate_project(tmp_path, analyze(tmp_path).layers)
    assert (report.enforced, report.advisory, report.unclassified) == (1, 0, 1)


def test_sans_regle_verifiee_le_projet_n_est_pas_parcouru(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_rule(tmp_path, "project", "ALWAYS prefer pathlib")

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("parcours inutile")

    monkeypatch.setattr(verifiers, "scan_project", boom)
    report = evaluate_project(tmp_path, analyze(tmp_path).layers)
    assert (report.enforced, report.advisory) == (0, 1)


# ── standard verify et standard score ─────────────────────────────────────────


def _checks(root: Path, check_id: str) -> list[Any]:
    return [c for c in verify_standard_profile(root, profile_id="starter").checks if c.id == check_id]


def test_verify_echoue_sur_une_regle_de_pack_violee(tmp_path: Path) -> None:
    _clean_project(tmp_path)
    _write(tmp_path, "src/bad.py", "try:\n    pass\nexcept:\n    pass\n")
    _install_python(tmp_path)
    found = _checks(tmp_path, "rules.pack_violation")
    assert len(found) == 1
    assert found[0].severity == "error"
    assert "src/bad.py:3" in found[0].message


def test_verify_se_tait_sur_un_projet_conforme(tmp_path: Path) -> None:
    _clean_project(tmp_path)
    _install_python(tmp_path)
    assert _checks(tmp_path, "rules.pack_violation") == []
    assert _checks(tmp_path, "rules.pack_unevaluated") == []


def test_verify_avertit_quand_le_verificateur_n_a_pas_pu_conclure(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", "[project]\nrequires-python = '>=3.11'\n")
    _write(tmp_path, ".gitignore", ".venv/\n")
    _install_python(tmp_path)  # aucun fichier .py : les trois contrôles AST n'ont rien lu
    found = _checks(tmp_path, "rules.pack_unevaluated")
    assert len(found) == 3
    assert {c.severity for c in found} == {"warning"}


def test_verify_signale_un_pack_du_kit_illisible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_rule(tmp_path, "project", "ALWAYS prefer pathlib")

    def broken(*_a: object, **_k: object) -> None:
        raise StackPackError("stack_pack.unreadable", "pack cassé")

    monkeypatch.setattr("grimoire.core.stack_packs.load_packs", broken)
    found = _checks(tmp_path, "rules.pack_invalid")
    assert [c.severity for c in found] == ["error"]


def test_verify_ne_charge_aucun_pack_sans_regle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("chargement inutile")

    monkeypatch.setattr("grimoire.core.stack_packs.load_packs", boom)
    assert _checks(tmp_path, "rules.pack_invalid") == []


def test_standard_score_rapporte_separement_controlees_et_advisory(tmp_path: Path) -> None:
    _clean_project(tmp_path)
    _install_python(tmp_path)
    add_rule(tmp_path, "project", "ALWAYS run unit-tests before commit")
    result = calculate_compliance_score(tmp_path, profile_id="starter")
    assert result.rules["rules_enforced"] == 5
    assert result.rules["rules_advisory"] == 2
    assert result.rules["rules_failing"] == 0
    assert result.rules["rules_unclassified"] == 1
    saved = YAML(typ="safe").load((tmp_path / result.output_path).read_text(encoding="utf-8"))
    assert saved["rules"]["rules_enforced"] == 5
    assert saved["rules"]["rules_advisory"] == 2


def test_standard_score_sans_pack_compte_zero_et_non_inconnu(tmp_path: Path) -> None:
    fields = rules_score_fields(tmp_path)
    assert fields["rules_enforced"] == 0
    assert fields["rules_advisory"] == 0


def test_un_pack_illisible_rend_inconnu_et_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add_rule(tmp_path, "project", "ALWAYS prefer pathlib")

    def broken(*_a: object, **_k: object) -> None:
        raise StackPackError("stack_pack.unreadable", "pack cassé")

    monkeypatch.setattr("grimoire.core.stack_packs.load_packs", broken)
    fields = rules_score_fields(tmp_path)
    assert fields["rules_enforced"] is None
    assert fields["rules_advisory"] is None
