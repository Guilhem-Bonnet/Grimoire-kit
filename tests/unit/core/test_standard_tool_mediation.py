"""Le vérificateur `tools.mediated-before-use` (B5).

Le pattern `tool-mediation-gate` était exigé par le profil `governed` avec
`checks: []` : aucun code ne le vérifiait. Un projet gouverné pouvait donc
faire tourner quatre serveurs MCP sans qu'aucun figure au registre d'outils —
c'est exactement le verrou décoratif que ce dépôt a déjà mesuré six fois, et
OWASP ASI02 (*Tool Misuse*) côté menace.

La règle : tout serveur MCP **résolu à l'exécution** — `.mcp.json` du projet et
portée utilisateur (`~/.claude.json`, `~/.claude/settings.json`) — doit être
déclaré dans `tool-registry.yaml` avec un risque, ou explicitement mis hors
périmètre avec une raison. Un registre peuplé mais périmé échoue aussi : c'est
le critère ajouté en section 7 du plan d'exécution, sans lequel le vérificateur
naîtrait fail-open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile

_REGISTRY = Path("_grimoire/standard/tool-registry.yaml")


def _write_mcp_json(root: Path, *names: str) -> None:
    payload = {
        "mcpServers": {
            name: {"command": "node", "args": ["server.js"], "env": {"SECRET_TOKEN": "s3cr3t-value"}}
            for name in names
        }
    }
    (root / ".mcp.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _declare(root: Path, *entries: str) -> None:
    text = (root / _REGISTRY).read_text(encoding="utf-8")
    assert "mcp_servers: []" in text
    body = "mcp_servers:\n" + "".join(entries)
    (root / _REGISTRY).write_text(text.replace("mcp_servers: []", body, 1), encoding="utf-8")


def _entry(server: str, *, risk: str = "moyen", extra: str = "") -> str:
    return (
        f"  - id: MCP-{server}\n"
        f"    server: {server}\n"
        f"    owner: guilhem\n"
        f"    scopes: [read]\n"
        f"    timeout_s: 30\n"
        f"    logging:\n"
        f"      requests: true\n"
        f"      errors: true\n"
        f"      secrets_masked: true\n"
        + (f"    risk: {risk}\n" if risk else "")
        + extra
    )


def _ids(result, prefix: str = "mediation.") -> list[str]:
    return [c.id for c in result.checks if c.id.startswith(prefix)]


@pytest.fixture
def governed(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="governed", project_name="Demo")
    return tmp_path


def test_registre_vide_et_serveur_actif_echoue(governed: Path) -> None:
    _write_mcp_json(governed, "grimoire", "playwright")
    result = verify_standard_profile(governed)
    failures = [
        c for c in result.checks if c.id == "mediation.server_undeclared" and c.severity == "error"
    ]
    assert len(failures) == 2
    assert not result.ok


def test_registre_complet_reussit(governed: Path) -> None:
    _write_mcp_json(governed, "grimoire")
    _declare(governed, _entry("grimoire"))
    result = verify_standard_profile(governed)
    assert _ids(result) == []


def test_registre_perime_echoue(governed: Path) -> None:
    """Un serveur déclaré que plus aucune source ne résout : le registre ment."""
    _write_mcp_json(governed, "grimoire")
    _declare(governed, _entry("grimoire"), _entry("serveur-retire"))
    result = verify_standard_profile(governed)
    stale = [c for c in result.checks if c.id == "mediation.registry_stale"]
    assert stale and stale[0].severity == "error"
    assert "serveur-retire" in stale[0].message


def test_un_serveur_declare_sans_risque_echoue(governed: Path) -> None:
    _write_mcp_json(governed, "grimoire")
    _declare(governed, _entry("grimoire", risk=""))
    result = verify_standard_profile(governed)
    assert "mediation.server_risk_missing" in _ids(result)


def test_hors_perimetre_sans_raison_echoue(governed: Path) -> None:
    _write_mcp_json(governed, "grimoire")
    _declare(governed, _entry("grimoire", risk="", extra="    out_of_scope: true\n"))
    result = verify_standard_profile(governed)
    assert "mediation.out_of_scope_without_reason" in _ids(result)


def test_hors_perimetre_avec_raison_passe(governed: Path) -> None:
    _write_mcp_json(governed, "grimoire")
    _declare(
        governed,
        _entry(
            "grimoire",
            risk="",
            extra="    out_of_scope: true\n    out_of_scope_reason: hébergé par l'hôte, hors périmètre du projet\n",
        ),
    )
    result = verify_standard_profile(governed)
    assert _ids(result) == []


def test_la_portee_utilisateur_compte(governed: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Décision 5 du plan : le périmètre inclut les serveurs de portée
    utilisateur résolus à l'exécution, pas seulement `.mcp.json`."""
    home = tmp_path / "faux-home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"hostinger": {"command": "npx"}}}), encoding="utf-8"
    )
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"mcpServers": {"context7": {"command": "npx"}}}), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    result = verify_standard_profile(governed)
    undeclared = [c.message for c in result.checks if c.id == "mediation.server_undeclared"]
    assert any("hostinger" in m for m in undeclared)
    assert any("context7" in m for m in undeclared)


def test_aucun_secret_n_est_lu_ni_journalise(governed: Path) -> None:
    """Lecture tolérante : seuls les *noms* de serveurs sortent des sources."""
    _write_mcp_json(governed, "grimoire")
    result = verify_standard_profile(governed)
    rendered = " ".join(c.message for c in result.checks)
    assert "s3cr3t-value" not in rendered
    assert "SECRET_TOKEN" not in rendered


def test_une_source_illisible_est_signalee_pas_avalee(governed: Path) -> None:
    """Fail-open relevé par la revue : le silence confondait « rien à déclarer »
    avec « je n'ai pas pu regarder ». Avertissement dès `governed`."""
    (governed / ".mcp.json").write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    result = verify_standard_profile(governed)
    found = [c for c in result.checks if c.id == "mediation.source_unreadable"]
    assert found and found[0].severity == "warning"
    assert ".mcp.json" in found[0].message
    # Un avertissement ne bloque pas : la vérification reste exploitable.
    assert not [c for c in result.checks if c.id == "mediation.source_unreadable" and c.severity == "error"]


def test_une_source_illisible_est_une_erreur_en_production(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="production", project_name="Demo")
    (tmp_path / ".mcp.json").write_text("{ pas du JSON", encoding="utf-8")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "mediation.source_unreadable"]
    assert found and found[0].severity == "error"
    assert not result.ok


def test_une_source_absente_reste_muette(governed: Path) -> None:
    """Absente n'est pas cassée : un projet sans `.mcp.json` n'a rien à signaler."""
    assert not (governed / ".mcp.json").exists()
    result = verify_standard_profile(governed)
    assert _ids(result) == []


def test_severite_moindre_hors_gouverne(tmp_path: Path) -> None:
    setup_standard_profile(tmp_path, profile_id="controlled", project_name="Demo")
    _write_mcp_json(tmp_path, "grimoire")
    result = verify_standard_profile(tmp_path)
    found = [c for c in result.checks if c.id == "mediation.server_undeclared"]
    assert found and found[0].severity == "warning"


def test_le_check_est_declare_par_la_capacite() -> None:
    """Le pattern `tool-mediation-gate` ne peut plus avoir `checks: []`."""
    from grimoire.core.agentic_standard import load_capability_map

    spec = load_capability_map()["patterns"]["tool-mediation-gate"]
    assert spec["checks"], "tool-mediation-gate sans check : verrou décoratif"
    assert "mediation.server_undeclared" in spec["checks"]
