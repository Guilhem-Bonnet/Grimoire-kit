"""Capacités des fournisseurs et sonde sans dépense (épique #1281, lot L3).

Garanties vérifiées : un registre sans ``capabilities:`` se lit à l'identique et
ne dit jamais « oui » par défaut ; les trois provenances (``declared``,
``probed``, ``documented``) se départagent dans cet ordre ; la sonde ne fait
aucun appel de génération, rend « non mesuré » sans clé ni réseau, et la clé
n'apparaît ni dans le fichier écrit ni dans la sortie. Aucun appel réseau réel :
``urllib.request.urlopen`` est remplacé dans chaque test qui sonde.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_providers import providers_app
from grimoire.providers import capability_probe
from grimoire.providers.capabilities import (
    DOCUMENTED_DEFAULTS,
    MODEL_CAPABILITIES,
    CapabilityProbe,
    ProbedModel,
    canonical_provider,
    parse_capabilities,
    resolve_capabilities,
)
from grimoire.providers.capability_probe import (
    PROVIDERS_CAPABILITIES_FILE,
    load_probes,
    probe_capabilities,
    probe_provider_capabilities,
    read_capabilities,
)
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import set_secret

REGISTRY = Path("_grimoire/standard/llm-provider-registry.yaml")
SECRET = "valeur-factice"  # noqa: S105 — jeton factice de test
_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "ACME_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    for name in _KEY_VARS:
        monkeypatch.delenv(name, raising=False)


def _no_network(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("aucun appel réseau attendu")


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _fake_urlopen(routes: dict[str, Any], seen: list[urllib.request.Request] | None = None) -> Callable[..., _Response]:
    """Rend la charge JSON de la première route dont le préfixe correspond ; une exception est levée."""

    def opener(request: urllib.request.Request, timeout: float = 0) -> _Response:
        assert timeout > 0
        if seen is not None:
            seen.append(request)
        for prefix, payload in routes.items():
            if request.full_url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                return _Response(body)
        raise AssertionError(f"URL inattendue : {request.full_url}")

    return opener


def _write_registry(root: Path, body: str) -> None:
    path = root / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n' + body, encoding="utf-8")


_PLAIN = """\
  - id: "anthropic"
    enabled: true
    provider_type: "hosted"
    models:
      - id: "claude-haiku-4-5"
        tier: "cheap"
  - id: "acme"
    enabled: true
    provider_type: "hosted"
    models:
      - id: "acme-1"
        tier: "mid"
"""


# ── Rétro-compatibilité ─────────────────────────────────────────────────────


def test_registry_without_capabilities_loads_unchanged(tmp_path: Path) -> None:
    _write_registry(tmp_path, _PLAIN)
    specs = read_registry(tmp_path)
    assert [spec.capabilities for spec in specs] == [(), ()]
    assert all(model.capabilities == () for spec in specs for model in spec.models)


def test_absent_capability_is_unknown_never_yes(tmp_path: Path) -> None:
    _write_registry(tmp_path, _PLAIN)
    anthropic, acme = read_capabilities(tmp_path)
    for name in MODEL_CAPABILITIES:
        capability = acme.model("acme-1").get(name)
        assert capability.value is None
        assert capability.provenance == "unknown"
        assert not capability.known
    assert acme.openai_compatible.provenance == "unknown"
    # Anthropic : la doc couvre outils et cache, pas la vision ni le contexte.
    haiku = anthropic.model("claude-haiku-4-5")
    assert haiku.tools.value is True
    assert haiku.tools.provenance == "documented"
    assert haiku.tools.source.startswith("https://")
    assert haiku.vision.provenance == "unknown"
    assert haiku.context_window.value is None


def test_every_documented_value_cites_an_url() -> None:
    for provider, values in DOCUMENTED_DEFAULTS.items():
        for name, (_value, url) in values.items():
            assert url.startswith("https://"), (provider, name)


def test_aliases_and_local_type_resolve_to_documented_provider() -> None:
    assert canonical_provider("kimi") == "moonshot"
    assert canonical_provider("Ollama") == "local"
    assert canonical_provider("mon-ollama", "local") == "local"
    assert canonical_provider("deepseek") == "deepseek"


# ── Déclaration ─────────────────────────────────────────────────────────────


def test_declared_values_override_and_invalid_ones_are_ignored(tmp_path: Path) -> None:
    _write_registry(
        tmp_path,
        """\
  - id: "deepseek"
    enabled: true
    provider_type: "hosted"
    capabilities:
      vision: false
      context_window: true
      tools: "yes"
    models:
      - id: "deepseek-flash"
        tier: "cheap"
        capabilities:
          vision: true
          context_window: 1048576
      - id: "deepseek-v4-pro"
        tier: "strong"
""",
    )
    (deepseek,) = read_capabilities(tmp_path)
    flash = deepseek.model("deepseek-flash")
    assert (flash.vision.value, flash.vision.provenance) == (True, "declared")
    assert (flash.context_window.value, flash.context_window.provenance) == (1048576, "declared")
    pro = deepseek.model("deepseek-v4-pro")
    assert (pro.vision.value, pro.vision.provenance) == (False, "declared")
    # `context_window: true` et `tools: "yes"` ne disent rien : inconnu, puis la doc.
    assert pro.context_window.provenance == "unknown"
    assert pro.tools.provenance == "documented"
    assert deepseek.anthropic_compatible.value is True
    # Un modèle non déclaré reçoit les capacités du fournisseur.
    assert deepseek.model("autre").model_id == "autre"
    assert deepseek.model("autre").vision.value is False


def test_parse_capabilities_rejects_wrong_types() -> None:
    assert parse_capabilities(None) == ()
    assert parse_capabilities({"context_window": 0, "tools": 1, "unknown_key": True, "thinking": True}) == (
        ("thinking", True),
    )


def test_model_capabilities_get_rejects_unknown_name() -> None:
    caps = resolve_capabilities("acme", models=(("m", ()),))
    with pytest.raises(KeyError):
        caps.model("m").get("openai_compatible")


def test_precedence_declared_then_probed_then_documented() -> None:
    probe = CapabilityProbe(
        "deepseek",
        True,
        "ok",
        "2026-11-22T00:00:00+00:00",
        "https://api.deepseek.com/models",
        (ProbedModel("m", (("tools", False), ("context_window", 64000), ("vision", True))),),
    )
    caps = resolve_capabilities("deepseek", declared=(("vision", False),), models=(("m", ()),), probe=probe)
    model = caps.model("m")
    assert (model.vision.value, model.vision.provenance) == (False, "declared")
    assert (model.tools.value, model.tools.provenance) == (False, "probed")
    assert model.context_window.source == "https://api.deepseek.com/models (2026-11-22T00:00:00+00:00)"
    assert model.prompt_cache.provenance == "documented"


def test_unmeasured_probe_brings_no_value() -> None:
    probe = CapabilityProbe("acme", False, "non mesuré : clé absente", models=(ProbedModel("m", (("tools", True),)),))
    caps = resolve_capabilities("acme", models=(("m", ()),), probe=probe)
    assert caps.model("m").tools.provenance == "unknown"
    assert caps.to_dict()["probe"]["measured"] is False


# ── Sonde ───────────────────────────────────────────────────────────────────


def _provider(root: Path, body: str, provider_id: str) -> Any:
    _write_registry(root, body)
    return next(spec for spec in read_registry(root) if spec.id == provider_id)


_DEEPSEEK = """\
  - id: "deepseek"
    enabled: true
    provider_type: "hosted"
    models:
      - id: "deepseek-flash"
        tier: "cheap"
      - id: "deepseek-v4-pro"
        tier: "strong"
"""


def test_probe_without_key_is_unmeasured_and_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    probe = probe_provider_capabilities(_provider(tmp_path, _DEEPSEEK, "deepseek"))
    assert probe.measured is False
    assert "clé absente (DEEPSEEK_API_KEY" in probe.note


def test_probe_reads_model_list_with_store_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    seen: list[urllib.request.Request] = []
    payload = {
        "object": "list",
        "data": [
            {"id": "deepseek-flash", "context_window": 1048576, "input_modalities": ["text", "image"]},
            {"id": "deepseek-v4-pro", "context_window": 1048576, "input_modalities": ["text"]},
        ],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://api.deepseek.com/models": payload}, seen))
    _write_registry(tmp_path, _DEEPSEEK)

    (probe,) = probe_capabilities(tmp_path)

    assert probe.measured is True
    assert seen[0].get_method() == "GET"
    assert seen[0].get_header("Authorization") == f"Bearer {SECRET}"
    assert SECRET not in seen[0].full_url
    (deepseek,) = read_capabilities(tmp_path)
    flash, pro = deepseek.models
    assert (flash.vision.value, flash.vision.provenance) == (True, "probed")
    assert pro.vision.value is False
    assert (pro.context_window.value, pro.context_window.provenance) == (1048576, "probed")
    written = (tmp_path / PROVIDERS_CAPABILITIES_FILE).read_text(encoding="utf-8")
    assert SECRET not in written


def test_probe_falls_back_to_environment_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", SECRET)
    payload = {
        "data": [
            {
                "id": "kimi-k3.1-pro",
                "context_length": 262144,
                "supports_image_in": True,
                "supports_reasoning": False,
            }
        ]
    }
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://api.moonshot.ai/v1/models": payload}))
    body = '  - id: "kimi"\n    enabled: true\n    models:\n      - id: "kimi-k3.1-pro"\n        tier: "mid"\n'
    probe = probe_provider_capabilities(_provider(tmp_path, body, "kimi"))
    model = probe.model("kimi-k3.1-pro")
    assert model is not None
    assert dict(model.values) == {"context_window": 262144, "vision": True, "thinking": False}


def test_probe_parses_anthropic_models_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("anthropic", "ANTHROPIC_API_KEY", SECRET)
    seen: list[urllib.request.Request] = []
    payload = {
        "data": [
            {
                "id": "claude-haiku-4-5",
                "max_input_tokens": 200000,
                "capabilities": {
                    "image_input": {"supported": True},
                    "structured_outputs": {"supported": True},
                    "thinking": {"supported": True, "types": {}},
                },
            }
        ]
    }
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen({"https://api.anthropic.com/v1/models": payload}, seen)
    )
    probe = probe_provider_capabilities(_provider(tmp_path, _PLAIN, "anthropic"))
    assert seen[0].get_header("X-api-key") == SECRET
    assert seen[0].get_header("Anthropic-version")
    model = probe.model("claude-haiku-4-5")
    assert model is not None
    assert dict(model.values) == {
        "context_window": 200000,
        "vision": True,
        "thinking": True,
        "structured_output": True,
    }


def test_probe_parses_gemini_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("google-gemini", "GEMINI_API_KEY", SECRET)
    seen: list[urllib.request.Request] = []
    payload = {"models": [{"name": "models/gemini-2.5-pro", "inputTokenLimit": 1048576, "thinking": True}]}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://generativelanguage": payload}, seen))
    body = '  - id: "google-gemini"\n    enabled: true\n    models:\n      - id: "gemini-2.5-pro"\n        tier: "strong"\n'
    probe = probe_provider_capabilities(_provider(tmp_path, body, "google-gemini"))
    assert seen[0].get_header("X-goog-api-key") == SECRET
    assert SECRET not in seen[0].full_url
    model = probe.model("gemini-2.5-pro")
    assert model is not None
    assert dict(model.values) == {"context_window": 1048576, "thinking": True}


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (urllib.error.HTTPError("https://api.deepseek.com/models", 401, "nope", None, None), "clé refusée (HTTP 401)"),  # type: ignore[arg-type]
        (
            urllib.error.HTTPError("https://api.deepseek.com/models", 500, "boom", None, None),
            "réponse en erreur (HTTP 500)",
        ),  # type: ignore[arg-type]
        (urllib.error.URLError("no route"), "injoignable"),
        (b"pas du json", "réponse illisible"),
        ({"data": "pas une liste"}, "réponse illisible"),
        (["liste"], "réponse illisible"),
    ],
)
def test_probe_failures_are_unmeasured_without_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Any, expected: str
) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://api.deepseek.com/models": failure}))
    probe = probe_provider_capabilities(_provider(tmp_path, _DEEPSEEK, "deepseek"))
    assert probe.measured is False
    assert expected in probe.note
    assert SECRET not in probe.note


def test_unknown_provider_uses_anthropic_base_url_or_reports_no_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    body = _PLAIN + '  - id: "relay"\n    enabled: true\n    anthropic_base_url: "https://relay.example/anthropic/"\n'
    _write_registry(tmp_path, body)
    specs = {spec.id: spec for spec in read_registry(tmp_path)}
    assert probe_provider_capabilities(specs["acme"]).note == "non mesuré : aucun point d'entrée connu"
    relay = probe_provider_capabilities(specs["relay"])
    assert relay.endpoint == "https://relay.example/anthropic/v1/models"
    assert "RELAY_API_KEY" in relay.note


_LOCAL = """\
  - id: "local"
    enabled: true
    provider_type: "local"
    models:
      - id: "qwen3-coder"
        tier: "mid"
      - id: "llava"
        tier: "cheap"
      - id: "absent"
        tier: "cheap"
"""


def test_local_probe_reads_ollama_tags_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []
    tags = {"models": [{"name": "qwen3-coder:latest"}, {"name": "llava"}, {"name": "autre:7b"}]}
    show = {
        "capabilities": ["completion", "tools", "thinking"],
        "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 262144},
    }

    def opener(request: urllib.request.Request, timeout: float = 0) -> _Response:
        seen.append(request)
        if request.full_url.endswith("/api/tags"):
            return _Response(json.dumps(tags).encode())
        body = json.loads(request.data or b"{}")  # type: ignore[arg-type]
        if body["model"] == "llava":
            raise urllib.error.URLError("show en panne")
        return _Response(json.dumps(show).encode())

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    probe = probe_provider_capabilities(_provider(tmp_path, _LOCAL, "local"))
    assert probe.measured is True
    assert probe.endpoint == "http://localhost:11434/api/tags"
    qwen = probe.model("qwen3-coder")
    assert qwen is not None
    assert dict(qwen.values) == {"tools": True, "vision": False, "thinking": True, "context_window": 262144}
    llava = probe.model("llava")
    assert llava is not None
    assert llava.values == ()
    assert probe.model("autre:7b") is not None
    assert probe.model("absent") is None
    assert seen[1].get_method() == "POST"
    caps = resolve_capabilities("local", provider_type="local", models=(("qwen3-coder", ()),), probe=probe)
    assert caps.model("qwen3-coder").prompt_cache.value is False  # documenté : pas de cache côté Ollama


def test_local_probe_without_ollama_is_unmeasured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11500")
    monkeypatch.setattr(
        urllib.request, "urlopen", _fake_urlopen({"http://127.0.0.1:11500": urllib.error.URLError("x")})
    )
    probe = probe_provider_capabilities(_provider(tmp_path, _LOCAL, "local"))
    assert probe.measured is False
    assert probe.note.startswith("non mesuré : Ollama injoignable")


def test_probe_one_provider_keeps_previous_others(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    _write_registry(tmp_path, _PLAIN + _DEEPSEEK)
    probe_capabilities(tmp_path)
    assert set(load_probes(tmp_path)) == {"anthropic", "acme", "deepseek"}
    (only,) = probe_capabilities(tmp_path, "deepseek")
    assert only.provider_id == "deepseek"
    assert set(load_probes(tmp_path)) == {"anthropic", "acme", "deepseek"}


@pytest.mark.parametrize("content", ["{pas du json", '["liste"]', '{"providers": []}'])
def test_unreadable_probe_file_means_nothing_measured(tmp_path: Path, content: str) -> None:
    path = tmp_path / PROVIDERS_CAPABILITIES_FILE
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert load_probes(tmp_path) == {}


def test_probe_file_tolerates_malformed_entries(tmp_path: Path) -> None:
    path = tmp_path / PROVIDERS_CAPABILITIES_FILE
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"providers": {"a": "x", "b": {"measured": True, "models": {"m": {"tools": True}, "n": 3}}}}),
        encoding="utf-8",
    )
    probes = load_probes(tmp_path)
    assert list(probes) == ["b"]
    assert probes["b"].models == (ProbedModel("m", (("tools", True),)),)


# ── Surfaces : CLI, cockpit, MCP ────────────────────────────────────────────


@pytest.fixture
def runner() -> Iterator[CliRunner]:
    yield CliRunner()


def test_cli_capabilities_json_and_table(tmp_path: Path, runner: CliRunner) -> None:
    _write_registry(tmp_path, _PLAIN)
    result = runner.invoke(providers_app, ["capabilities", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    anthropic = data["providers"][0]
    assert anthropic["id"] == "anthropic"
    assert anthropic["api"]["anthropic_compatible"]["provenance"] == "documented"
    assert anthropic["models"][0]["capabilities"]["vision"] == {"value": None, "provenance": "unknown", "source": None}

    table = runner.invoke(providers_app, ["capabilities", "acme", "--project-root", str(tmp_path)])
    assert table.exit_code == 0, table.output
    assert "inconnu" in table.output
    assert "anthropic" not in table.output.split("Provenance")[0]


def test_cli_capabilities_without_registry_and_with_broken_one(tmp_path: Path, runner: CliRunner) -> None:
    empty = runner.invoke(providers_app, ["capabilities", "--project-root", str(tmp_path)])
    assert empty.exit_code == 0
    assert "Aucun fournisseur" in empty.output
    (tmp_path / REGISTRY).parent.mkdir(parents=True)
    (tmp_path / REGISTRY).write_text("providers: [\n", encoding="utf-8")
    broken = runner.invoke(providers_app, ["capabilities", "--project-root", str(tmp_path)])
    assert broken.exit_code == 1


def test_cli_probe_capabilities_json_and_text(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    payload = {"data": [{"id": "deepseek-flash", "context_window": 1048576}]}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://api.deepseek.com/models": payload}))
    _write_registry(tmp_path, _DEEPSEEK)

    result = runner.invoke(providers_app, ["probe", "--capabilities", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    data = json.loads(result.output)
    (deepseek,) = data["capabilities"]
    flash = deepseek["models"][0]["capabilities"]["context_window"]
    assert (flash["value"], flash["provenance"]) == (1048576, "probed")

    text = runner.invoke(providers_app, ["probe", "deepseek", "--capabilities", "--project-root", str(tmp_path)])
    assert text.exit_code == 0, text.output
    assert "mesuré" in text.output
    assert "1048576" in text.output
    assert SECRET not in text.output


def test_cli_probe_without_flag_is_unchanged(
    tmp_path: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    _write_registry(tmp_path, _DEEPSEEK)
    result = runner.invoke(providers_app, ["probe", "--project-root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.output)) == {"probed", "reopened"}
    assert not (tmp_path / PROVIDERS_CAPABILITIES_FILE).exists()


def test_status_cockpit_and_mcp_expose_capabilities(tmp_path: Path, runner: CliRunner) -> None:
    from grimoire.mcp.server import grimoire_providers_status
    from grimoire.tools.workspace_config import providers_view

    _write_registry(tmp_path, _PLAIN)
    status = json.loads(runner.invoke(providers_app, ["status", "--project-root", str(tmp_path), "--json"]).output)
    assert status["providers"][0]["capabilities"]["defaults"]["tools"]["provenance"] == "documented"

    view = providers_view(tmp_path)
    assert view["providers"][1]["capabilities"]["defaults"]["tools"]["provenance"] == "unknown"

    raw = grimoire_providers_status(str(tmp_path))
    mcp = json.loads(raw if isinstance(raw, str) else "".join(getattr(b, "text", "") for b in raw.content))
    assert mcp["providers"][0]["capabilities"]["api"]["openai_compatible"]["value"] is True
    assert mcp["next_choice"]["cheap"] == "anthropic"


def test_module_constants_are_coherent() -> None:
    assert capability_probe.PROVIDERS_CAPABILITIES_FILE.name == "providers-capabilities.json"


def test_probe_ignores_entries_without_id_and_odd_show_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_secret("deepseek", "DEEPSEEK_API_KEY", SECRET)
    payload = {"data": [{"object": "model"}, "texte", {"id": "deepseek-flash"}]}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen({"https://api.deepseek.com/models": payload}))
    probe = probe_provider_capabilities(_provider(tmp_path, _DEEPSEEK, "deepseek"))
    assert probe.models == (ProbedModel("deepseek-flash"),)

    routes = {
        "http://localhost:11434/api/tags": {"models": [{"name": "qwen3-coder"}]},
        "http://localhost:11434/api/show": [],
    }
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen(routes))
    local = probe_provider_capabilities(_provider(tmp_path, _LOCAL, "local"))
    assert local.models == (ProbedModel("qwen3-coder"),)


def test_cli_probe_capabilities_with_broken_registry_exits_1(tmp_path: Path, runner: CliRunner) -> None:
    (tmp_path / REGISTRY).parent.mkdir(parents=True)
    (tmp_path / REGISTRY).write_text("providers: [\n", encoding="utf-8")
    result = runner.invoke(providers_app, ["probe", "--capabilities", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
