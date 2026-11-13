"""Simulation Windows sur Linux — encodage UTF-8 et séparateurs de chemin.

Le job « Tests (Python 3.12, windows-latest) » ne tourne pas sur les PR : les
défauts propres à Windows restaient invisibles jusqu'au run 43352310660, rouge
sur `main`. Ce fichier rejoue sur n'importe quelle plateforme les deux
conditions qui n'y existent que sous Windows :

- l'encodage préféré cp1252, forcé par ``locale.getpreferredencoding`` /
  ``locale.getencoding`` (ce que ``subprocess(text=True)`` consulte) et par
  ``PYTHONIOENCODING`` (ce que lit un enfant Python) ;
- des chemins ``PureWindowsPath``, c'est-à-dire des ``\\`` là où le contrat
  d'émission exige un chemin POSIX.

Chaque cas appelle le code réellement exécuté sous Windows (le lanceur de
fournisseur, l'aide de décodage de la vue en fils, le remplissage d'une
fixture Gemini) : sur `main`, chacun échoue ; après correction, tous passent.
C'est un test de régression, pas une démonstration.
"""

from __future__ import annotations

import locale
import shlex
import shutil
import sys
from pathlib import Path, PureWindowsPath

import pytest

from grimoire.missions import dispatch, dispatch_provider_env
from grimoire.providers.registry import ProviderSpec
from tests.unit.hosts.test_runtime_gemini import _payload as _gemini_payload
from tests.unit.test_workspace_live_view import EXAMPLE as LIVE_EXAMPLE
from tests.unit.test_workspace_live_view import _layout as _live_layout


def _simulate_windows_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force l'encodage préféré à cp1252, comme une console Windows française.

    ``locale.getencoding`` (3.11+) est ce que ``subprocess`` consulte pour
    ``text=True`` ; ``getpreferredencoding`` reste patché pour le code qui
    l'appelle encore. ``PYTHONIOENCODING`` est ce qu'un enfant Python lit pour
    choisir l'encodage de son stdout.
    """
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252")
    if hasattr(locale, "getencoding"):
        monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")


# ── Encodage : la sortie d'un sous-processus est relue en UTF-8 ─────────────


def test_un_fournisseur_python_ecrit_en_utf8_malgre_une_console_cp1252(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le pipe est relu en UTF-8 : le fournisseur doit y écrire en UTF-8.

    Sans la consigne explicite à l'enfant, un Python sous Windows écrit en
    cp1252 et « résumé » revient en ``r\\ufffdsum\\ufffd``.
    """
    _simulate_windows_encoding(monkeypatch)
    monkeypatch.setattr(dispatch_provider_env, "env_for_provider", lambda provider_id: {})

    script = tmp_path / "fournisseur.py"
    script.write_text(
        'import sys\nsys.stdout.write("résumé du morceau 1\\n")\n',
        encoding="utf-8",
        newline="\n",
    )
    provider = ProviderSpec(
        id="local",
        enabled=True,
        provider_type="local",
        allowed_capabilities=("chat",),
        default_models=("fake-model",),
        fallback_order=(),
        invocation=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {{prompt}}",
    )

    code, stdout, _stderr, _duration, _kind, _signal = dispatch._run_provider_call(
        tmp_path, provider, "fake-model", "un prompt", call_timeout=30
    )

    assert code == 0
    assert stdout == "résumé du morceau 1\n"


def test_la_vue_en_fils_relit_le_json_de_node_en_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """``node`` écrit ses tuyaux en UTF-8 : les décoder en cp1252 casse les accents."""
    if shutil.which("node") is None:
        pytest.skip("node absent")
    _simulate_windows_encoding(monkeypatch)

    layout = _live_layout(LIVE_EXAMPLE)

    assert [change["label"] for change in layout["changes"]] == ["Sonnet → Opus", "Opus → Flash"]


# ── Chemins : une fixture JSON ne s'interpole pas à la main ──────────────────


def test_une_fixture_gemini_reste_du_json_avec_un_chemin_windows() -> None:
    """Un ``C:\\Users\\…`` inséré brut dans une chaîne JSON produit un `\\U` invalide."""
    project = PureWindowsPath(r"C:\Users\dev\projet")

    payload = _gemini_payload("before-tool", project)

    assert payload["cwd"] == str(project)
    assert payload["transcript_path"].startswith(str(project.parent / "home"))
