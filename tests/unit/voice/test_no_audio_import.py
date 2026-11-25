"""Vocal éteint = coût nul : ni ``import grimoire`` ni le cockpit ne chargent un module audio (#1343 V1).

Le test tourne dans un interpréteur neuf (celui de pytest a pu importer n'importe quoi).
Pour qu'il ne passe pas à vide sur une machine sans l'extra, des paquets factices
``faster_whisper``, ``torch``, ``onnxruntime`` et ``pocket_tts`` sont posés sur le
``PYTHONPATH`` : importables, ils apparaîtraient dans ``sys.modules`` au moindre import.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import grimoire

AUDIO_MODULES = ("faster_whisper", "torch", "onnxruntime", "pocket_tts")

_SCRIPT = r"""
import importlib.util, json, sys, threading, urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

AUDIO = %(audio)r
assert all(importlib.util.find_spec(m) is not None for m in AUDIO), "paquets factices introuvables"

import grimoire
import grimoire.cli.app  # l'arbre de commandes complet, doctor compris
from grimoire.cli.cmd_cockpit import _CockpitHandler

serve = Path(%(serve)r)
serve.mkdir(parents=True, exist_ok=True)
(serve / "index.html").write_text("ok", encoding="utf-8")
httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(_CockpitHandler, directory=str(serve)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]
for path in ("/index.html", "/api/projects"):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=20) as resp:
        resp.read()
httpd.shutdown()

# Le contrôle doctor et la résolution des réglages ne chargent rien non plus.
from rich.console import Console
from grimoire.cli.doctor_voice import apply_voice_check
from grimoire.voice.engines import VoiceEngines
from grimoire.voice.settings import settings_from_mapping

results = []
apply_voice_check({"interaction": {"mode": "voice"}, "voice": {"allow_download": True}}, results, fmt="json", console=Console(quiet=True))
VoiceEngines.from_settings(settings_from_mapping({"interaction": {"mode": "voice"}}))

print(json.dumps({
    "loaded": sorted(m for m in sys.modules if m.split(".")[0] in AUDIO),
    "stt_available": [r["available"] for r in results if r["name"] == "voice_stt"],
}))
"""


def test_cockpit_start_loads_no_audio_module(tmp_path: Path) -> None:
    fakes = tmp_path / "fakes"
    for name in AUDIO_MODULES:
        (fakes / name).mkdir(parents=True)
        (fakes / name / "__init__.py").write_text("", encoding="utf-8")
    src = Path(grimoire.__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(src), str(fakes)]),
        "GRIMOIRE_COCKPIT_HOME": str(tmp_path / "cockpit"),
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
    }
    script = _SCRIPT % {"audio": AUDIO_MODULES, "serve": str(tmp_path / "serve")}
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["loaded"] == []
    assert report["stt_available"] == [True]  # les factices sont bien vus : le test ne passe pas à vide
