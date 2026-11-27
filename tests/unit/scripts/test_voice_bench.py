"""Tests de scripts/voice_bench.py : moteurs simulés, aucun modèle chargé ni téléchargé."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import wave
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "voice_bench.py"
_spec = importlib.util.spec_from_file_location("voice_bench", SCRIPT)
assert _spec and _spec.loader
vb = importlib.util.module_from_spec(_spec)
sys.modules["voice_bench"] = vb
_spec.loader.exec_module(vb)

ABSENT = "module_qui_n_existe_pas_voice_bench"


def _stt(name: str, module: str = "os", text: str | None = None, seen: list[bool] | None = None) -> vb.SttEngine:
    def load(download: bool):
        if seen is not None:
            seen.append(download)

        def transcribe(path: Path) -> tuple[str, float]:
            return (text if text is not None else path.with_suffix(".txt").read_text(encoding="utf-8")), 2.0

        return transcribe

    return vb.SttEngine(name, module, load)


def _tts(name: str, module: str = "os", first: float | None = None) -> vb.TtsEngine:
    def load(download: bool):
        return lambda text: ([0.1, -0.1] * 800, 16000, first)

    return vb.TtsEngine(name, module, load)


def test_wer_compte_les_mots() -> None:
    assert vb.word_error_rate("Le chat dort.", "le chat dort") == 0.0
    assert vb.word_error_rate("un deux trois quatre", "un deux quatre") == pytest.approx(0.25)
    assert vb.word_error_rate("", "") == 0.0


def test_stt_moteur_absent_est_liste_sans_erreur(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "a.txt").write_text("bonjour le monde", encoding="utf-8")
    out = tmp_path / "r.json"
    rc = vb.main(
        ["stt", str(tmp_path), "--json", str(out)],
        stt_engines=[_stt("fw-ok", text="bonjour monde"), _stt("fw-absent", module=ABSENT)],
    )
    assert rc == 0
    rows = {r["engine"]: r for r in json.loads(out.read_text())["results"]}
    assert rows["fw-absent"]["status"] == "non installé"
    assert rows["fw-ok"]["status"] == "ok"
    assert rows["fw-ok"]["wer"] == pytest.approx(1 / 3)
    assert rows["fw-ok"]["rtf"] is not None
    assert "non installé" in capsys.readouterr().out


def test_stt_sans_download_ne_telecharge_pas(tmp_path: Path) -> None:
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    seen: list[bool] = []
    vb.main(["stt", str(tmp_path)], stt_engines=[_stt("e", seen=seen)])
    vb.main(["stt", str(tmp_path), "--download"], stt_engines=[_stt("e", seen=seen)])
    assert seen == [False, True]


def test_sans_download_la_bibliotheque_est_mise_hors_ligne(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    vb._offline_env(True)
    assert "HF_HUB_OFFLINE" not in os.environ
    vb._offline_env(False)
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    monkeypatch.delenv("HF_HUB_OFFLINE")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE")


def test_modele_absent_du_cache_n_est_pas_une_erreur(tmp_path: Path) -> None:
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")

    def load(download: bool):
        raise vb.EngineUnavailableError("absent du cache")

    report = vb.run_stt(tmp_path, [vb.SttEngine("e", "os", load)], False)
    assert report.results == [{"engine": "e", "status": "absent du cache"}]


def test_tts_huit_phrases_wav_anonymises_et_correspondance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "wav"
    rc = vb.main(
        ["tts", "--out", str(out), "--json", str(tmp_path / "r.json")],
        tts_engines=[_tts("a", first=0.05), _tts("b"), _tts("c", module=ABSENT)],
    )
    assert rc == 0
    assert len(vb.PHRASES) == 8
    wavs = sorted(out.glob("*.wav"))
    assert len(wavs) == 16
    assert all(re.fullmatch(r"\d{3}-[0-9a-f]{6}\.wav", w.name) for w in wavs)
    mapping = json.loads((out / "_correspondance.json").read_text())
    assert {m["file"] for m in mapping} == {w.name for w in wavs}
    assert {m["engine"] for m in mapping} == {"a", "b"}
    with wave.open(str(wavs[0])) as fh:
        assert fh.getframerate() == 16000
    rows = {r["engine"]: r for r in json.loads((tmp_path / "r.json").read_text())["results"]}
    assert rows["c"]["status"] == "non installé"
    assert rows["a"]["first_audio_s"] == pytest.approx(0.05)
    assert rows["b"]["first_audio_s"] > 0
    assert rows["a"]["rtf"] > 0
    assert "peak_rss_mb" in rows["a"]
    assert "1er son" in capsys.readouterr().out


def test_moteur_qui_plante_ne_coupe_pas_le_banc(tmp_path: Path) -> None:
    def load(download: bool):
        def synth(text: str):
            raise RuntimeError("boom")

        return synth

    report = vb.run_tts(tmp_path, [vb.TtsEngine("x", "os", load), _tts("y")], False)
    assert report.results[0]["status"].startswith("erreur")
    assert report.results[1]["status"] == "ok"


def test_moteurs_reels_absents_sont_non_installes_sans_rien_charger(tmp_path: Path) -> None:
    # Aucune dépendance vocale n'est requise : l'état « installé » ne fait qu'un find_spec.
    for eng in [*vb.default_stt_engines(), *vb.default_tts_engines()]:
        assert isinstance(eng.installed(), bool)


def test_pocket_tts_parle_avec_le_modele_et_la_voix_francais(monkeypatch: pytest.MonkeyPatch) -> None:
    """Régression : le premier passage réel chargeait le modèle anglais et la voix « alba »."""
    import types

    seen: dict[str, object] = {}

    class FakeModel:
        sample_rate = 24000

        @classmethod
        def load_model(cls, language: str | None = None) -> FakeModel:
            seen["language"] = language
            return cls()

        def get_state_for_audio_prompt(self, voice: str) -> str:
            seen["voice"] = voice
            return voice

        def generate_audio_stream(self, state: str, text: str):
            yield [0.1, -0.1]
            yield [0.2]

    monkeypatch.setitem(sys.modules, "pocket_tts", types.SimpleNamespace(TTSModel=FakeModel))
    engine = next(e for e in vb.default_tts_engines() if e.name == "pocket-tts-fr")
    samples, rate, first = engine.load(False)("Bonjour.")
    assert seen == {"language": "french", "voice": "estelle"}
    assert list(samples) == [0.1, -0.1, 0.2] and rate == 24000 and first is not None


def test_qwen3_tts_a_un_moteur_a_voix_decrite_en_francais() -> None:
    names = [e.name for e in vb.default_tts_engines()]
    assert "qwen3-tts-1.7b-design" in names
    assert "français" in vb._VOICE_DESIGN.lower() or "française" in vb._VOICE_DESIGN.lower()


# --- clonage Qwen3-TTS 0.6B Base ------------------------------------------------------------


def _fake_qwen(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Simule torch et qwen_tts : trace les appels, ne charge aucun modèle."""
    import contextlib
    import types

    calls: dict[str, list] = {"threads": [], "load": [], "prompt": [], "gen": [], "quant": [], "inference": []}

    class FakeQwen:
        model = "modèle"

        @classmethod
        def from_pretrained(cls, repo: str, **kwargs: object) -> FakeQwen:
            calls["load"].append((repo, kwargs))
            return cls()

        def create_voice_clone_prompt(self, **kwargs: object) -> str:
            calls["prompt"].append(kwargs)
            return "invite"

        def generate_voice_clone(self, **kwargs: object):
            calls["gen"].append(kwargs)
            return [[0.1, -0.1]], 24000

    @contextlib.contextmanager
    def inference_mode():
        calls["inference"].append(1)
        yield

    def quantize_dynamic(model: object, layers: set, dtype: object, inplace: bool = False) -> str:
        assert inplace, "la copie profonde échoue sur le vrai modèle"
        calls["quant"].append(dtype)
        return "quantifié"

    fake_torch = types.SimpleNamespace(
        bfloat16="bf16",
        qint8="int8",
        nn=types.SimpleNamespace(Linear=object),
        set_num_threads=calls["threads"].append,
        inference_mode=inference_mode,
        ao=types.SimpleNamespace(quantization=types.SimpleNamespace(quantize_dynamic=quantize_dynamic)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_tts", types.SimpleNamespace(Qwen3TTSModel=FakeQwen))
    return calls


def _clone(variant: str, ref: Path | None, text: str | None = "Bonjour.") -> vb.TtsEngine:
    name = "qwen3-tts-0.6b-clone" + (f"-{variant}" if variant else "")
    engines = vb.default_tts_engines(str(ref) if ref else None, text)
    return next(e for e in engines if e.name == name)


def test_clone_expose_les_variantes_sans_audio_dans_le_depot() -> None:
    names = [e.name for e in vb.default_tts_engines()]
    for variant in vb.CLONE_VARIANTS:
        assert "qwen3-tts-0.6b-clone" + (f"-{variant}" if variant else "") in names
    assert not list(SCRIPT.parent.glob("*.wav"))


def test_clone_sans_voix_de_reference_est_indisponible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_qwen(monkeypatch)
    with pytest.raises(vb.EngineUnavailableError, match="--ref-audio"):
        _clone("", None).load(False)
    with pytest.raises(vb.EngineUnavailableError, match="introuvable"):
        _clone("", tmp_path / "absent.wav").load(False)


def test_clone_de_base_reencode_la_reference_a_chaque_phrase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_qwen(monkeypatch)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"")
    synth = _clone("", ref).load(True)
    samples, rate, first = synth("Phrase.")
    assert list(samples) == [0.1, -0.1] and rate == 24000 and first is None
    assert calls["load"][0][0] == "Qwen/Qwen3-TTS-12Hz-0.6B-Base" and calls["load"][0][1] == {}
    assert calls["prompt"] == [] and calls["threads"] == [] and calls["quant"] == []
    assert calls["gen"][0] == {"text": "Phrase.", "language": "French", "ref_audio": str(ref), "ref_text": "Bonjour."}


def test_clone_variantes_appliquent_leur_reglage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_qwen(monkeypatch)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"")
    _clone("threads8", ref).load(True)
    assert calls["threads"] == [8]
    synth = _clone("prompt", ref).load(True)
    synth("A.")
    synth("B.")
    assert len(calls["prompt"]) == 1 and calls["prompt"][0]["x_vector_only_mode"] is False
    assert all(g["voice_clone_prompt"] == "invite" and "ref_audio" not in g for g in calls["gen"][-2:])
    _clone("xvector", ref).load(True)
    assert calls["prompt"][-1]["x_vector_only_mode"] is True
    _clone("int8", ref).load(True)
    assert calls["quant"] == ["int8"]
    _clone("bf16", ref).load(True)
    assert calls["load"][-1][1] == {"dtype": "bf16"}
    _clone("inference", ref).load(True)("C.")
    assert calls["inference"] == [1]
    _clone("gpu", ref).load(True)
    assert calls["load"][-1][1] == {"dtype": "bf16", "device_map": "cuda:0"}


def test_intelligibilite_compte_les_mots_faux(tmp_path: Path) -> None:
    phrases = ("un deux trois quatre", "cinq six")
    heard = {0: "un deux quatre", 1: "cinq six"}

    def load(download: bool):
        return lambda text: ([0.1] * 100, 16000, None)

    seen: list[Path] = []

    def transcribe(path: Path) -> str:
        seen.append(path)
        return heard[len(seen) - 1]

    report = vb.run_tts(tmp_path, [vb.TtsEngine("x", "os", load)], False, phrases=phrases, transcribe=transcribe)
    row = report.results[0]
    assert row["word_errors"] == "1/6" and "_made" not in row
    assert "erreurs de mots" in report.table() and "charge s" in report.table()


def test_intelligibilite_absente_sans_faster_whisper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vb, "_module_present", lambda module: False)
    assert vb._intelligibility("small", False) is None
    report = vb.run_tts(tmp_path, [_tts("a")], False)
    assert report.results[0].get("word_errors") is None


def test_retranscription_qui_plante_garde_les_mesures(tmp_path: Path) -> None:
    def boom(path: Path) -> str:
        raise TypeError("av")

    report = vb.run_tts(tmp_path, [_tts("a", first=0.1)], False, transcribe=boom)
    row = report.results[0]
    assert row["status"] == "ok" and row["word_errors"] == "erreur : TypeError" and row["rtf"] > 0
