"""Passerelle LiteLLM comme type de fournisseur (épique #1281, lot L8).

Un fournisseur ``provider_type: litellm`` porte la racine du proxy dans ``base_url``. Garanties : la sonde
lit ``/v1/models`` puis ``/v1/model/info`` sans génération, la clé vient du magasin seulement (jamais de
l'environnement), part en en-tête vers une URL admise (``https://``, ``http://`` en boucle locale), aucune
redirection n'est suivie, la réponse est bornée et les identifiants filtrés. Le fournisseur sert dans la
table palier → modèle comme tout autre. Aucun appel réseau réel : l'ouvreur HTTP est remplacé.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grimoire.cli.cmd_providers import providers_app
from grimoire.core.agentic_standard import verify_standard_profile
from grimoire.hosts.model_table import load_model_table, resolve_model
from grimoire.providers import capability_probe
from grimoire.providers.capabilities import DOCUMENTED_DEFAULTS, LITELLM, canonical_provider
from grimoire.providers.capability_probe import (
    PROVIDERS_CAPABILITIES_FILE,
    probe_capabilities,
    probe_provider_capabilities,
    read_capabilities,
)
from grimoire.providers.registry import read_registry
from grimoire.providers.secrets import set_secret

STANDARD = Path("_grimoire/standard")
REGISTRY = STANDARD / "llm-provider-registry.yaml"
SECRET = "jeton-factice-litellm"  # noqa: S105 — jeton factice de test
KEY_VAR = "PROXY_API_KEY"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in (KEY_VAR, "OPENAI_API_KEY", "LITELLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _no_network(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("aucun appel réseau attendu")


def _fake(routes: dict[str, Any], seen: list[urllib.request.Request] | None = None) -> Callable[..., _Response]:
    """Rend la charge de la route exacte demandée ; une exception de la table est levée."""

    def opener(request: urllib.request.Request, timeout: float = 0) -> _Response:
        assert timeout > 0
        if seen is not None:
            seen.append(request)
        payload = routes.get(request.full_url)
        if payload is None:
            raise AssertionError(f"URL inattendue : {request.full_url}")
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    return opener


def _entry(base_url: str | None = "https://llm.example/", provider_id: str = "proxy") -> str:
    url = f'    base_url: "{base_url}"\n' if base_url is not None else ""
    return (
        f'  - id: "{provider_id}"\n'
        "    enabled: true\n"
        '    provider_type: "litellm"\n'
        f"{url}"
        '    allowed_capabilities: ["chat", "code"]\n'
        '    default_models: ["claude-haiku", "qwen-coder"]\n'
        '    currency: "api"\n'
        '    invocation: "llm {model} {prompt}"\n'
        "    models:\n"
        '      - id: "claude-haiku"\n'
        '        tier: "cheap"\n'
        '      - id: "qwen-coder"\n'
        '        tier: "mid"\n'
        "    data_policy:\n"
        '      allowed_data_classes: ["public-docs"]\n'
        '      forbidden_data_classes: ["secrets"]\n'
        "    fallback_order: []\n"
    )


def _write(root: Path, body: str) -> None:
    path = root / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('$schema: "grimoire-llm-provider-registry/v1"\nproviders:\n' + body, encoding="utf-8")


def _spec(root: Path, body: str, provider_id: str = "proxy") -> Any:
    _write(root, body)
    return next(spec for spec in read_registry(root) if spec.id == provider_id)


_MODELS = {
    "object": "list",
    "data": [
        {"id": "claude-haiku", "object": "model", "owned_by": "openai"},
        {"id": "qwen-coder", "object": "model", "owned_by": "openai"},
        {"id": "bad id\x1b[31m", "object": "model"},
    ],
}
_INFO = {
    "data": [
        {
            "model_name": "claude-haiku",
            "litellm_params": {"model": "anthropic/claude-haiku-4-5"},
            "model_info": {"max_input_tokens": 200000, "supports_function_calling": True, "supports_vision": True},
        },
        {
            "model_name": "qwen-coder",
            "model_info": {"max_input_tokens": 32768, "supports_function_calling": False, "supports_vision": None},
        },
        {"model_name": "<script>", "model_info": {"max_input_tokens": 1}},
        {"model_name": "claude-haiku", "model_info": {"max_input_tokens": 1}},
        "pas-une-table",
    ]
}


# ── Registre et valeurs documentées ─────────────────────────────────────────


def test_registry_reads_base_url_and_type_maps_to_litellm(tmp_path: Path) -> None:
    spec = _spec(tmp_path, _entry())
    assert spec.base_url == "https://llm.example/"
    assert spec.provider_type == LITELLM
    assert canonical_provider(spec.id, spec.provider_type) == LITELLM
    assert canonical_provider("litellm") == LITELLM
    (resolved,) = read_capabilities(tmp_path)
    assert (resolved.openai_compatible.value, resolved.openai_compatible.provenance) == (True, "documented")
    assert resolved.anthropic_compatible.value is True
    assert resolved.anthropic_compatible.source.startswith("https://docs.litellm.ai/")
    # Ce qui dépend du modèle derrière le proxy n'est jamais documenté d'office.
    assert resolved.model("claude-haiku").tools.value is None
    assert all(url.startswith("https://docs.litellm.ai/") for _value, url in DOCUMENTED_DEFAULTS[LITELLM].values())


def test_other_providers_have_no_base_url(tmp_path: Path) -> None:
    body = '  - id: "deepseek"\n    enabled: true\n    provider_type: "hosted"\n    base_url: "  "\n'
    assert _spec(tmp_path, body, "deepseek").base_url is None


# ── Sonde ───────────────────────────────────────────────────────────────────


def test_probe_lists_models_then_reads_model_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    seen: list[urllib.request.Request] = []
    routes = {"https://llm.example/v1/models": _MODELS, "https://llm.example/v1/model/info": _INFO}
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake(routes, seen))
    _write(tmp_path, _entry())

    (probe,) = probe_capabilities(tmp_path)

    assert probe.measured is True
    assert probe.endpoint == "https://llm.example/v1/models"
    assert probe.note == "2 modèle(s) listé(s), détail de 2 par /model/info"
    assert [request.get_method() for request in seen] == ["GET", "GET"]
    assert all(request.get_header("Authorization") == f"Bearer {SECRET}" for request in seen)
    assert all(SECRET not in request.full_url for request in seen)
    assert [model.model_id for model in probe.models] == ["claude-haiku", "qwen-coder"]
    (resolved,) = read_capabilities(tmp_path)
    haiku = resolved.model("claude-haiku")
    assert (haiku.context_window.value, haiku.context_window.provenance) == (200000, "probed")
    assert (haiku.tools.value, haiku.vision.value) == (True, True)
    qwen = resolved.model("qwen-coder")
    assert qwen.context_window.value == 32768
    # `false` et `null` de /model/info ne disent rien (doc LiteLLM) : la capacité reste inconnue.
    assert (qwen.tools.value, qwen.vision.value) == (None, None)
    written = (tmp_path / PROVIDERS_CAPABILITIES_FILE).read_text(encoding="utf-8")
    assert SECRET not in written
    assert "\x1b" not in written
    assert "<script>" not in written


@pytest.mark.parametrize(
    "info",
    [
        urllib.error.HTTPError("https://llm.example/v1/model/info", 403, "Forbidden", {}, None),  # type: ignore[arg-type]
        b"pas du json",
        {"data": "pas une liste"},
        ["pas", "une", "table"],
    ],
)
def test_model_info_refused_keeps_listed_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, info: Any) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    routes = {"https://llm.example/v1/models": _MODELS, "https://llm.example/v1/model/info": info}
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake(routes))

    probe = probe_provider_capabilities(_spec(tmp_path, _entry()))

    assert probe.measured is True
    assert probe.note == "2 modèle(s) listé(s)"
    assert all(model.values == () for model in probe.models)


def test_probe_key_comes_from_store_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_VAR, SECRET)
    monkeypatch.setattr(capability_probe._OPENER, "open", _no_network)

    probe = probe_provider_capabilities(_spec(tmp_path, _entry()))

    assert probe.measured is False
    assert probe.note == f"non mesuré : clé absente ({KEY_VAR}, voir `grimoire providers set-key`)"


def test_reserved_key_variable_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("openai", "OPENAI_API_KEY", SECRET)
    monkeypatch.setattr(capability_probe._OPENER, "open", _no_network)

    probe = probe_provider_capabilities(_spec(tmp_path, _entry(provider_id="openai"), "openai"))

    assert probe.measured is False
    assert "variable de clé réservée" in probe.note


@pytest.mark.parametrize(
    "base_url",
    [
        None,
        "",
        "http://llm.example",
        "ftp://llm.example",
        "https://user:pass@llm.example",
        "https://llm.example/?token=x",
        "https://llm.example/#frag",
        "https://:443",
        "https://llm.example:99999",
        "http://localhost.evil.example:4000",
    ],
)
def test_unadmitted_base_url_is_never_contacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str | None
) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    monkeypatch.setattr(capability_probe._OPENER, "open", _no_network)

    probe = probe_provider_capabilities(_spec(tmp_path, _entry(base_url)))

    assert probe.measured is False
    assert "base_url absente ou non admise" in probe.note
    assert probe.endpoint == ""


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://127.0.0.1:4000", "http://127.0.0.1:4000/v1/models"),
        ("http://localhost:4000/v1/", "http://localhost:4000/v1/models"),
        ("http://[::1]:4000/", "http://[::1]:4000/v1/models"),
        ("https://gw.example/litellm/v1", "https://gw.example/litellm/v1/models"),
    ],
)
def test_admitted_base_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base_url: str, expected: str) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    info_url = expected.removesuffix("/models") + "/model/info"
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake({expected: _MODELS, info_url: {"data": []}}))

    probe = probe_provider_capabilities(_spec(tmp_path, _entry(base_url)))

    assert probe.measured is True
    assert probe.endpoint == expected


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (urllib.error.HTTPError("u", 307, "Redirect", {}, None), "redirection refusée (HTTP 307)"),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 401, "Unauthorized", {}, None), "clé refusée (HTTP 401)"),  # type: ignore[arg-type]
        (urllib.error.URLError("refused"), "injoignable"),
        (b"x" * (2 * 1024 * 1024 + 1), "réponse trop volumineuse"),
        ({"pas": "de liste"}, "réponse illisible"),
    ],
)
def test_probe_failures_are_unmeasured_without_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Any, expected: str
) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake({"https://llm.example/v1/models": failure}))

    probe = probe_provider_capabilities(_spec(tmp_path, _entry()))

    assert probe.measured is False
    assert probe.note.startswith("non mesuré : LiteLLM ")
    assert expected in probe.note
    assert SECRET not in probe.note


def test_model_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    many = {"data": [{"id": f"m-{index}"} for index in range(2500)]}
    routes = {"https://llm.example/v1/models": many, "https://llm.example/v1/model/info": {"data": []}}
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake(routes))

    probe = probe_provider_capabilities(_spec(tmp_path, _entry()))

    assert len(probe.models) == 2000


# ── Table palier → modèle ───────────────────────────────────────────────────


def test_litellm_provider_is_usable_in_the_model_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    info = {"data": [{"model_name": "qwen-coder", "model_info": {"max_input_tokens": 32768}}]}
    routes = {"https://llm.example/v1/models": _MODELS, "https://llm.example/v1/model/info": info}
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake(routes))
    body = _entry().replace(
        '        tier: "mid"\n', '        tier: "mid"\n        capabilities:\n          tools: false\n'
    )
    _write(tmp_path, body)
    probe_capabilities(tmp_path)
    policy = tmp_path / STANDARD / "orchestration-policy.yaml"
    policy.write_text(
        "dispatch:\n  models:\n    claude:\n      proxy:\n        cheap: claude-haiku\n        mid: qwen-coder\n",
        encoding="utf-8",
    )
    table = load_model_table(tmp_path)
    (capabilities,) = read_capabilities(tmp_path)

    cheap = resolve_model("cheap", "claude", "proxy", table=table, capabilities=capabilities, requires=("tools",))
    assert (cheap.model, cheap.source, cheap.provider) == ("claude-haiku", "project", "proxy")
    # `tools: false` déclaré au registre écarte le modèle du worker ; la couche suivante (le kit) prend le relais.
    mid = resolve_model("mid", "claude", "proxy", table=table, capabilities=capabilities, requires=("tools",))
    assert mid.skipped == (("qwen-coder", "tools: non (declared)"),)
    assert mid.model is None


# ── Vérification du standard ────────────────────────────────────────────────


def _verify(root: Path, body: str) -> set[str]:
    _write(root, body)
    (root / STANDARD / "standard-profile.yaml").write_text("profile: controlled\n", encoding="utf-8")
    return {check.id for check in verify_standard_profile(root, profile_id="controlled").checks}


def test_verify_requires_base_url_and_forbidden_classes(tmp_path: Path) -> None:
    assert "providers.litellm_base_url_missing" not in _verify(tmp_path, _entry())
    assert "providers.litellm_base_url_missing" in _verify(tmp_path, _entry(None))
    no_forbidden = _entry().replace('      forbidden_data_classes: ["secrets"]\n', "")
    assert "providers.hosted_forbidden_missing" in _verify(tmp_path, no_forbidden)


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_probe_capabilities_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret("proxy", KEY_VAR, SECRET)
    routes = {"https://llm.example/v1/models": _MODELS, "https://llm.example/v1/model/info": _INFO}
    monkeypatch.setattr(capability_probe._OPENER, "open", _fake(routes))
    _write(tmp_path, _entry())

    result = CliRunner().invoke(
        providers_app, ["probe", "proxy", "--capabilities", "--json", "--project-root", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    payload = json.loads(result.output)
    (item,) = payload["capabilities"]
    assert item["id"] == "proxy"
    assert item["probe"]["measured"] is True
    assert item["api"]["anthropic_compatible"]["provenance"] == "documented"
