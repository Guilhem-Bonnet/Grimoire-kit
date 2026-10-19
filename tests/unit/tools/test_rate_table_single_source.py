"""Une seule table de tarifs dans le kit (W4-02, correctifs de revue).

Défauts visés sur 3d3d3e53 :
- le cockpit (observer.js) apparie encore une famille par sous-chaîne, avec le
  tarif de la version la plus récente : sonnet-4-6 (3/15) y valait 2/10, opus-4-1
  (15/75) y valait 4/20, et opus-4-2, absent de la table, recevait un prix ;
- la vue COÛT (bp2-cost.js, bp2-team.js) garde ses propres tarifs Anthropic
  (haiku 0.8/4, sonnet 3/15, opus 15/75) : opus à 15/75 dans le cockpit et à 4/20
  en Python.

Le code web est exécuté par Node (comme tests/unit/test_site_chrome_local.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from grimoire.tools import cost_model as cm

WEB = Path(__file__).resolve().parents[3] / "web"

MODEL_IDS = [
    "claude-sonnet-4-6",
    "claude-opus-4-1",
    "claude-opus-4-2",
    "claude-opus",
    "claude-haiku-4.5-20251001[1m]",
    "Claude-Sonnet-5-5",
    "  claude-opus-5-5  ",
    "gpt-4.1",
]


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node absent : le code du cockpit ne peut pas être exécuté")
    return node


def _run(node: str, script: str, *args: str) -> object:
    proc = subprocess.run(
        [node, "-e", script, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert proc.returncode == 0, f"node a échoué ({proc.returncode}) :\n{proc.stderr}"
    return json.loads(proc.stdout)


_OBSERVER = r"""
const fs = require('fs');
const path = require('path');
const [src, tmp, payloadJson, idsJson] = process.argv.slice(-4);
// observer.js importe désormais ../shell.js : on copie l'arbre workspace entier
// (les .js y sont lus comme ES modules via un package.json local).
const root = path.join(tmp, 'workspace');
fs.cpSync(path.dirname(path.dirname(src)), root, { recursive: true });
fs.writeFileSync(path.join(root, 'package.json'), '{"type":"module"}');
const target = path.join(root, 'spaces', 'observer.js');
// shell.js démarre la coque (DOM) dès l'import : seules les fonctions pures de
// tarification sont sous test, on le remplace par un stub aux deux exports utilisés.
fs.writeFileSync(path.join(root, 'shell.js'),
  'export const icon = () => null; export const stateIcon = () => null;');
import(require('url').pathToFileURL(target).href).then((m) => {
  const payload = JSON.parse(payloadJson);
  const span = (model) => ({ attributes: {
    'gen_ai.request.model': model,
    'gen_ai.usage.input_tokens': 1000000, 'gen_ai.usage.output_tokens': 1000000 } });
  const out = { normalized: JSON.parse(idsJson).map((i) => m.normalizeModelId(i)), cost: {} };
  for (const id of ['claude-sonnet-4-6', 'claude-opus-4-1', 'claude-opus-4-2', 'claude-opus']) {
    out.cost[id] = m.spanCostUsd(span(id), payload.modelRates);
  }
  process.stdout.write(JSON.stringify(out));
});
"""


class TestObserverPricesByExactVersion:
    @pytest.fixture
    def observed(self, tmp_path: Path) -> dict[str, object]:
        out = _run(
            _node(),
            _OBSERVER,
            str(WEB / "workspace" / "spaces" / "observer.js"),
            str(tmp_path),
            json.dumps(cm.cost_model()),
            json.dumps(MODEL_IDS),
        )
        assert isinstance(out, dict)
        return out

    def test_known_versions_keep_their_own_rate(self, observed: dict[str, object]) -> None:
        cost = observed["cost"]
        assert isinstance(cost, dict)
        # 1 MTok en entrée + 1 MTok en sortie : tarif in + tarif out.
        assert cost["claude-sonnet-4-6"] == 3.0 + 15.0
        assert cost["claude-opus-4-1"] == 15.0 + 75.0

    def test_unlisted_version_has_unknown_cost_not_a_family_price(
        self, observed: dict[str, object]
    ) -> None:
        cost = observed["cost"]
        assert isinstance(cost, dict)
        assert cost["claude-opus-4-2"] is None
        assert cost["claude-opus"] is None

    def test_observer_and_python_normalize_ids_identically(
        self, observed: dict[str, object]
    ) -> None:
        assert observed["normalized"] == [cm.normalize_model_id(i) for i in MODEL_IDS]

    def test_observer_agrees_with_rate_for_on_every_listed_version(self) -> None:
        payload = cm.cost_model()
        for key, row in cm.MODEL_RATES.items():
            assert payload["modelRates"][key] == row
            assert cm.rate_for(f"claude-{key}") == row


class TestPayloadIsPerVersion:
    def test_model_rates_is_the_single_table_by_exact_version(self) -> None:
        payload = cm.cost_model()
        assert payload["modelRates"] == cm.MODEL_RATES
        assert payload["modelRates"]["sonnet-4-6"] == {"in": 3.0, "out": 15.0}
        assert "opus" not in payload["modelRates"]

    def test_aliases_point_to_rows_of_the_table(self) -> None:
        aliases = cm.cost_model()["modelAliases"]
        assert set(aliases) == {"opus", "sonnet", "haiku"}
        assert all(version in cm.MODEL_RATES for version in aliases.values())


_BLUEPRINT = r"""
const fs = require('fs');
const vm = require('vm');
const [teamPath, costPath, payloadJson] = process.argv.slice(-3);
const noop = () => {};
globalThis.window = globalThis;
globalThis.document = { querySelector: () => null, querySelectorAll: () => [], addEventListener: noop };
globalThis.localStorage = { getItem: () => null, setItem: noop };
globalThis.Atelier = { toast: noop, project: () => ({}) };
globalThis.fetch = () => Promise.reject(new Error('hors serveur'));
vm.runInThisContext(fs.readFileSync(costPath, 'utf8'));
vm.runInThisContext(fs.readFileSync(teamPath, 'utf8'));
const agent = (model) => ({ id: 'a', kind: 'agent', role: 'agent', name: 'a', model,
  tools: [], mcp: [], skills: [], hooks: [], when: [], then: [] });
const graphOf = (model) => ({ nodes: [agent(model)], edges: [] });
const specOf = () => null;
const usd = (model) => BP2Cost.totals(graphOf(model)).usd;
const out = { offline: { rate: BP2Team.rateOf('opus'), usd: usd('opus') } };
BP2Team.setAnthropicRates(JSON.parse(payloadJson));
out.online = {
  opus: BP2Team.rateOf('opus'), sonnet: BP2Team.rateOf('sonnet'), haiku: BP2Team.rateOf('haiku'),
  other: BP2Team.rateOf('gpt-4.1'), unknown: BP2Team.rateOf('nope'),
  label: BP2Team.rateLabel('opus'),
  usd: usd('opus'),
};
process.stdout.write(JSON.stringify(out));
"""


class TestCostViewReadsThePythonTable:
    @pytest.fixture
    def view(self) -> dict[str, dict[str, object]]:
        out = _run(
            _node(),
            _BLUEPRINT,
            str(WEB / "bp2-team.js"),
            str(WEB / "bp2-cost.js"),
            json.dumps(cm.cost_model()),
        )
        assert isinstance(out, dict)
        return out  # type: ignore[return-value]

    def test_cost_view_has_no_anthropic_rate_of_its_own(self, view: dict[str, dict[str, object]]) -> None:
        # Sans réponse de /api/cost-model : tarif inconnu, jamais un tarif en dur.
        assert view["offline"]["rate"] is None
        assert view["offline"]["usd"] is None

    def test_alias_is_priced_by_the_python_row(self, view: dict[str, dict[str, object]]) -> None:
        online = view["online"]
        aliases = cm.cost_model()["modelAliases"]
        for alias in ("opus", "sonnet", "haiku"):
            assert online[alias] == cm.MODEL_RATES[aliases[alias]]
        assert online["opus"] == {"in": 4.0, "out": 20.0}  # et non plus 15/75 en dur
        assert online["label"] == "4/20 $ par MTok"
        # Un agent : 20 k-tokens en entrée, 9 en sortie, au tarif Python d'opus.
        assert online["usd"] == pytest.approx((20 * 4.0 + 9 * 20.0) / 1000)

    def test_other_providers_and_unknown_models_are_unchanged(
        self, view: dict[str, dict[str, object]]
    ) -> None:
        assert view["online"]["other"] == {"in": 2, "out": 8}
        assert view["online"]["unknown"] is None
