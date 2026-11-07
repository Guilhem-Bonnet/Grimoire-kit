"""``grimoire digest`` — résumé borné, erreurs citées mot pour mot, repli déterministe.

Quatre garanties sont tenues ici, sans jamais appeler un vrai fournisseur :
la sortie ANSI d'un fournisseur local est nettoyée ; un texte de trois morceaux
produit trois appels plus une synthèse ; une ligne ``FAILED`` du texte source est
rendue telle quelle même quand le faux modèle l'omet ; et sans fournisseur
admissible, le repli déterministe s'applique et la commande sort en 0.

Le faux fournisseur est un script Python local, invoqué comme le serait
``ollama run`` : ``_run_provider_call`` le lance pour de vrai (subprocess), ce qui
couvre aussi le retrait ANSI à la source.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grimoire.cli.app import app
from grimoire.core.ansi import strip_ansi
from grimoire.missions.digest import CHUNK_CHARS, digest_text, split_chunks

pytestmark = pytest.mark.covers("cli:digest")

runner = CliRunner()
STANDARD = Path("_grimoire/standard")
REGISTRY_PATH = STANDARD / "llm-provider-registry.yaml"

_FAKE_PROVIDER = r"""
import json
import os
import sys
from pathlib import Path

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
log = Path(os.environ["FAKE_LOG"])
calls = Path(os.environ["FAKE_CALLS"])
count = int(log.read_text().strip() or 0) if log.exists() else 0
count += 1
log.write_text(str(count))
with calls.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"n": count, "script": os.path.abspath(__file__), "prompt": prompt}) + "\n")
mode = os.environ.get("FAKE_MODE", "summary")
if mode == "ansi":
    sys.stdout.write("…une Asser\x1b[5D\x1b[KAssertionError…\n")
elif mode == "error":
    sys.stderr.write("fake provider down\n")
    raise SystemExit(3)
else:
    sys.stdout.write(f"résumé du morceau {count}\n")
"""


def _fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str = "fake_provider.py") -> str:
    """Écrit un faux fournisseur et rend son invocation ``{prompt}``."""
    script = tmp_path / name
    script.write_text(_FAKE_PROVIDER, encoding="utf-8")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_CALLS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_MODE", "summary")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{prompt}}"


def _write_registry(
    root: Path,
    invocation: str,
    *,
    provider_id: str = "local",
    provider_type: str = "local",
    currency: str = "local",
    default_data_class: str | None = None,
) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    routing_extra = f'\n  default_data_class: "{default_data_class}"' if default_data_class else ""
    (root / REGISTRY_PATH).write_text(
        f"""$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: {json.dumps(provider_id)}
    enabled: true
    provider_type: {json.dumps(provider_type)}
    allowed_capabilities: ["chat"]
    default_models: ["fake-model"]
    currency: {json.dumps(currency)}
    invocation: {json.dumps(invocation)}
    models:
      - id: "fake-model"
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: {json.dumps(provider_id)}
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true{routing_extra}
""",
        encoding="utf-8",
    )


def _write_registry_without_provider(root: Path) -> None:
    (root / STANDARD).mkdir(parents=True, exist_ok=True)
    (root / REGISTRY_PATH).write_text(
        """$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers: []
routing:
  default_provider: ""
  default_fallback_chain: []
  require_capability_match: true
  require_data_policy_match: true
""",
        encoding="utf-8",
    )


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_strip_ansi_removes_csi_and_osc() -> None:
    raw = "…une Asser\x1b[5D\x1b[KAssertionError…\x1b]0;titre\x07 fin"
    assert strip_ansi(raw) == "…une AsserAssertionError… fin"


def test_fake_provider_answer_is_cleaned_of_ansi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invocation = _fake(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "ansi")
    _write_registry(tmp_path, invocation)

    digest = digest_text(tmp_path, "un petit journal\n")

    assert digest.used_fallback is False
    assert "\x1b" not in digest.render()
    assert "…une AsserAssertionError…" in digest.summary


def test_three_chunks_make_three_calls_then_one_synthesis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invocation = _fake(tmp_path, monkeypatch)
    _write_registry(tmp_path, invocation)
    line = "ligne de journal " + "x" * 80 + "\n"
    text = line * 180
    assert len(text) > 2 * CHUNK_CHARS
    assert len(split_chunks(text)) == 3

    digest = digest_text(tmp_path, text)

    calls = _calls(tmp_path)
    assert len(calls) == 4
    assert "Fusionne-les" in str(calls[-1]["prompt"])
    assert digest.summary.startswith("résumé du morceau 4")


def test_error_line_is_cited_verbatim_even_if_the_model_omits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation = _fake(tmp_path, monkeypatch)
    _write_registry(tmp_path, invocation)
    text = "préambule\nligne ok\nFAILED tests/x.py::t - AssertionError: nope\nfin\n"

    output = digest_text(tmp_path, text).render()

    assert "résumé du morceau 1" in output
    assert "  3: FAILED tests/x.py::t - AssertionError: nope\n" in output


def test_local_currency_wins_over_an_admitted_external_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = _fake(tmp_path, monkeypatch, name="external.py")
    local = _fake(tmp_path, monkeypatch, name="local.py")
    (tmp_path / STANDARD).mkdir(parents=True, exist_ok=True)
    (tmp_path / REGISTRY_PATH).write_text(
        f"""$schema: "grimoire-llm-provider-registry/v1"
metadata:
  project: "demo"
  owner: ""
  policy: "No provider/model call outside this registry."
providers:
  - id: "external"
    enabled: true
    provider_type: "hosted"
    allowed_capabilities: ["chat"]
    default_models: ["fake-model"]
    currency: "api"
    invocation: {json.dumps(external)}
    models:
      - id: "fake-model"
        tier: "cheap"
    data_policy:
      allowed_data_classes: ["public-docs"]
      forbidden_data_classes: ["secrets"]
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
  - id: "local"
    enabled: true
    provider_type: "local"
    allowed_capabilities: ["chat"]
    default_models: ["fake-model"]
    currency: "local"
    invocation: {json.dumps(local)}
    models:
      - id: "fake-model"
        tier: "cheap"
    data_policy:
      allowed_data_classes: []
      forbidden_data_classes: []
      retention_notes: ""
    fallback_order: []
    audit:
      log_prompts: false
      log_metadata: true
routing:
  default_provider: "external"
  default_fallback_chain: ["local"]
  require_capability_match: true
  require_data_policy_match: true
  default_data_class: "public-docs"
""",
        encoding="utf-8",
    )

    digest = digest_text(tmp_path, "un petit journal\n")

    assert digest.provider == "local"
    calls = _calls(tmp_path)
    assert len(calls) == 1
    assert str(calls[0]["script"]).endswith("local.py")


def test_external_provider_is_refused_without_a_declared_data_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation = _fake(tmp_path, monkeypatch)
    _write_registry(tmp_path, invocation, provider_type="hosted", currency="api")

    digest = digest_text(tmp_path, "un petit journal\n")

    assert digest.used_fallback
    assert "politique de données" in digest.summary
    assert _calls(tmp_path) == []


def test_without_provider_the_fallback_applies_and_the_command_exits_zero(tmp_path: Path) -> None:
    _write_registry_without_provider(tmp_path)
    input_file = tmp_path / "journal.log"
    input_file.write_text(
        "\n".join(["ligne 0", *[f"ligne {i}" for i in range(1, 60)], "FAILED tests/x.py::t", "ligne 99"]),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["digest", str(input_file), "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "résumé non généré" in result.output
    assert "Début :" in result.output
    assert "Fin :" in result.output
    assert "FAILED tests/x.py::t" in result.output


def test_provider_failure_falls_back_and_the_command_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invocation = _fake(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_MODE", "error")
    _write_registry(tmp_path, invocation)
    input_file = tmp_path / "journal.log"
    input_file.write_text("un journal qui ne sera pas résumé\n", encoding="utf-8")

    result = runner.invoke(app, ["digest", str(input_file), "--project-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "résumé non généré" in result.output
    assert "fake provider down" in result.output
