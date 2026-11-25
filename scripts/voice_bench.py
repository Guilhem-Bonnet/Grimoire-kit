#!/usr/bin/env python3
"""Banc de mesure vocal : reconnaissance (stt) et synthèse (tts) sur la machine de l'utilisateur.

Outil hors du paquet (épique #1343, lot V0). Aucun chiffre fiable n'existe pour le français sur
un PC sans GPU : ce script les produit. Les dépendances (faster-whisper, kokoro, pocket_tts,
qwen_tts) sont importées paresseusement et n'appartiennent pas au paquet ; un moteur dont le
module manque est listé « non installé », jamais une erreur.

Aucun modèle n'est téléchargé sans ``--download`` : sans lui, le chargement se fait hors ligne
(``local_files_only`` / ``HF_HUB_OFFLINE``) et un modèle absent du cache est signalé « absent du
cache », pas récupéré.

    python scripts/voice_bench.py stt <dossier> [--json rapport.json]
    python scripts/voice_bench.py tts --out <dossier> [--json rapport.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import sys
import time
import unicodedata
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a")

# Huit phrases françaises : nombres, liaisons, noms propres, phrase technique.
PHRASES: tuple[str, ...] = (
    "Le rendez-vous est fixé au 14 mars 2027, à 8 h 45.",
    "Il y a 1 250 000 habitants et 37,5 % d'entre eux ont plus de 65 ans.",
    "Les enfants ont aimé ces beaux oiseaux, ils les ont observés pendant deux heures.",
    "Nous irons chez eux en avant-hier ; vous avez entendu un grand homme parler.",
    "Guilhem Bonnet-Jalabert part de Toulouse pour Aix-en-Provence avec Marie-Hélène Lefèvre.",
    "Zoé et François Œuvrard ont visité Strasbourg, Besançon et Châteauroux.",
    "Le serveur redémarre quand le démon grimoire échoue : exécutez systemctl restart avec sudo.",
    "La requête HTTP renvoie un code 503 ; le délai d'attente de la file passe de 30 à 120 secondes.",
)


# --------------------------------------------------------------------------------------------
# Interface des moteurs
# --------------------------------------------------------------------------------------------


class EngineUnavailableError(Exception):
    """Le moteur est installé mais son modèle ne peut pas être chargé (cache absent, hors ligne)."""


@dataclass
class SttEngine:
    name: str
    module: str
    load: Callable[[bool], Callable[[Path], tuple[str, float]]]
    """``load(download)`` rend ``transcribe(chemin) -> (texte, durée_audio_s)``."""

    def installed(self) -> bool:
        return _module_present(self.module)


@dataclass
class TtsEngine:
    name: str
    module: str
    load: Callable[[bool], Callable[[str], tuple[Any, int, float | None]]]
    """``load(download)`` rend ``synth(texte) -> (échantillons, fréquence, délai_premier_son_s|None)``.

    Un délai ``None`` signifie « moteur non streamé » : le délai avant le premier son vaut alors
    la durée totale de synthèse."""

    def installed(self) -> bool:
        return _module_present(self.module)


def _module_present(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _offline_env(download: bool) -> None:
    """Sans ``--download``, interdit tout accès réseau des bibliothèques Hugging Face."""
    if not download:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


# --------------------------------------------------------------------------------------------
# Adaptateurs réels (importés paresseusement ; jamais exécutés par les tests)
# --------------------------------------------------------------------------------------------


def _faster_whisper(size: str) -> SttEngine:
    def load(download: bool) -> Callable[[Path], tuple[str, float]]:
        _offline_env(download)
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        try:
            model = WhisperModel(size, device="cpu", compute_type="int8", local_files_only=not download)
        except Exception as exc:  # modèle absent du cache hors ligne
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc

        def transcribe(path: Path) -> tuple[str, float]:
            segments, info = model.transcribe(str(path), language="fr")
            return " ".join(s.text.strip() for s in segments), float(info.duration)

        return transcribe

    return SttEngine(f"faster-whisper-{size}", "faster_whisper", load)


def _kokoro() -> TtsEngine:
    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        _offline_env(download)
        from kokoro import KPipeline  # type: ignore[import-not-found]

        try:
            pipe = KPipeline(lang_code="f")
        except Exception as exc:
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc

        def synth(text: str) -> tuple[Any, int, float | None]:
            t0 = time.perf_counter()
            first: float | None = None
            chunks: list[float] = []
            for _gs, _ps, audio in pipe(text, voice="ff_siwis"):
                if first is None:
                    first = time.perf_counter() - t0
                chunks.extend(_flatten(audio))
            return chunks, 24000, first

        return synth

    return TtsEngine("kokoro", "kokoro", load)


def _pocket_tts() -> TtsEngine:
    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        _offline_env(download)
        from pocket_tts import TTSModel  # type: ignore[import-not-found]

        try:
            model = TTSModel.load_model(language="french")
            state = model.get_state_for_audio_prompt("estelle")
        except Exception as exc:
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc

        def synth(text: str) -> tuple[Any, int, float | None]:
            t0 = time.perf_counter()
            first: float | None = None
            chunks: list[float] = []
            for chunk in model.generate_audio_stream(state, text):
                if first is None:
                    first = time.perf_counter() - t0
                chunks.extend(_flatten(chunk))
            return chunks, int(model.sample_rate), first

        return synth

    return TtsEngine("pocket-tts-fr", "pocket_tts", load)


_VOICE_DESIGN = (
    "Voix féminine française, posée et professionnelle, chaleureuse sans être théâtrale, "
    "débit naturel, diction claire, ton d'une présentatrice qui explique calmement."
)


def _qwen3_tts_design() -> TtsEngine:
    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        _offline_env(download)
        from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

        try:
            model = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
        except Exception as exc:
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc

        def synth(text: str) -> tuple[Any, int, float | None]:
            wavs, rate = model.generate_voice_design(text=text, instruct=_VOICE_DESIGN, language="French")
            return wavs[0], int(rate), None

        return synth

    return TtsEngine("qwen3-tts-1.7b-design", "qwen_tts", load)


def _qwen3_tts(size: str) -> TtsEngine:
    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        _offline_env(download)
        from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

        try:
            model = Qwen3TTSModel.from_pretrained(f"Qwen/Qwen3-TTS-12Hz-{size}-CustomVoice")
        except Exception as exc:
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc

        def synth(text: str) -> tuple[Any, int, float | None]:
            wavs, rate = model.generate_custom_voice(text=text, language="French", speaker="Vivian")
            return wavs[0], int(rate), None

        return synth

    return TtsEngine(f"qwen3-tts-{size.lower()}", "qwen_tts", load)


_CLONE_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

# Voix de référence du clonage (même voix pour toutes les variantes) : réglée par la CLI.
_clone_ref: dict[str, str | None] = {"audio": None, "text": None}


def _qwen3_tts_clone(variant: str) -> TtsEngine:
    """Qwen3-TTS 0.6B Base en clonage ; ``variant`` : fp32, bf16 ou int8 (quantification dynamique torch)."""

    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        _offline_env(download)
        ref_audio = _clone_ref["audio"]
        if not ref_audio:
            raise EngineUnavailableError("voix de référence absente (passer --ref-audio, et --ref-text si possible)")
        import torch  # type: ignore[import-not-found]
        from qwen_tts import Qwen3TTSModel  # type: ignore[import-not-found]

        try:
            kwargs: dict[str, Any] = {"torch_dtype": torch.bfloat16} if variant == "bf16" else {}
            model = Qwen3TTSModel.from_pretrained(_CLONE_REPO, **kwargs)
            if variant == "int8":
                inner = getattr(model, "model", model)
                quantized = torch.ao.quantization.quantize_dynamic(inner, {torch.nn.Linear}, dtype=torch.qint8)
                if inner is not model:
                    model.model = quantized
                else:
                    model = quantized
        except Exception as exc:
            raise EngineUnavailableError(f"absent du cache (relancer avec --download) : {exc}") from exc
        ref_text = _clone_ref["text"]

        def synth(text: str) -> tuple[Any, int, float | None]:
            wavs, rate = model.generate_voice_clone(
                text=text,
                language="French",
                ref_audio=ref_audio,
                ref_text=ref_text,
                x_vector_only_mode=not ref_text,
            )
            return wavs[0], int(rate), None

        return synth

    return TtsEngine(f"qwen3-tts-0.6b-clone{'' if variant == 'fp32' else '-' + variant}", "qwen_tts", load)


def _qwen3_tts_clone_port(suffix: str, module: str) -> TtsEngine:
    """Port communautaire (GGUF, ONNX) : n'existe sur PyPI sous aucun nom vérifié ; listé « non installé »."""

    def load(download: bool) -> Callable[[str], tuple[Any, int, float | None]]:
        raise EngineUnavailableError("pas d'adaptateur : aucun port installable par pip n'a été vérifié")

    return TtsEngine(f"qwen3-tts-0.6b-clone-{suffix}", module, load)


def default_stt_engines() -> list[SttEngine]:
    return [_faster_whisper(s) for s in ("base", "small", "large-v3-turbo")]


def default_tts_engines() -> list[TtsEngine]:
    return [_pocket_tts(), _qwen3_tts("0.6B"), _qwen3_tts("1.7B"), _qwen3_tts_design(), _kokoro(), *clone_engines()]


def clone_engines() -> list[TtsEngine]:
    return [
        _qwen3_tts_clone("fp32"),
        _qwen3_tts_clone("bf16"),
        _qwen3_tts_clone("int8"),
        _qwen3_tts_clone_port("gguf", "qwen3_tts_gguf"),
        _qwen3_tts_clone_port("onnx", "qwen3_tts_onnx"),
    ]


# --------------------------------------------------------------------------------------------
# Mesures
# --------------------------------------------------------------------------------------------


def normalize(text: str) -> list[str]:
    """Minuscules, sans accents de casse ni ponctuation : le taux d'erreur compte des mots."""
    text = unicodedata.normalize("NFC", text).lower().replace("’", "'")
    return re.findall(r"[\w]+(?:'[\w]+)?", text, flags=re.UNICODE)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Distance de Levenshtein sur les mots, divisée par la longueur de la référence."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def peak_rss_mb() -> float | None:
    """Pic de mémoire résidente du processus (jalon haut, pas une différence)."""
    try:
        import resource
    except ImportError:  # Windows
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def _flatten(samples: Any) -> list[float]:
    if hasattr(samples, "detach"):
        samples = samples.detach().cpu().numpy()
    if hasattr(samples, "reshape"):
        samples = samples.reshape(-1)
    if hasattr(samples, "tolist"):
        samples = samples.tolist()
    return [float(x) for x in samples]


def write_wav(path: Path, samples: Any, rate: int) -> float:
    """Écrit un WAV mono 16 bits ; rend la durée en secondes."""
    data = _flatten(samples)
    pcm = b"".join(int(max(-1.0, min(1.0, x)) * 32767).to_bytes(2, "little", signed=True) for x in data)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(pcm)
    return len(data) / rate if rate else 0.0


def find_pairs(folder: Path) -> list[tuple[Path, str]]:
    """Enregistrements du dossier avec leur transcription ``<nom>.txt`` voisine."""
    pairs = []
    for audio in sorted(folder.iterdir()):
        ref = audio.with_suffix(".txt")
        if audio.suffix.lower() in AUDIO_SUFFIXES and ref.is_file():
            pairs.append((audio, ref.read_text(encoding="utf-8").strip()))
    return pairs


@dataclass
class Report:
    kind: str
    results: list[dict[str, Any]] = field(default_factory=list)

    def table(self) -> str:
        if self.kind == "stt":
            cols = [
                ("moteur", "engine"),
                ("statut", "status"),
                ("WER", "wer"),
                ("RTF", "rtf"),
                ("fichiers", "files"),
                ("RAM pic Mo", "peak_rss_mb"),
            ]
        else:
            cols = [
                ("moteur", "engine"),
                ("statut", "status"),
                ("1er son s", "first_audio_s"),
                ("RTF", "rtf"),
                ("phrases", "files"),
                ("RAM pic Mo", "peak_rss_mb"),
            ]
        rows = [[h for h, _ in cols]]
        for r in self.results:
            rows.append([_cell(r.get(k)) for _, k in cols])
        widths = [max(len(row[i]) for row in rows) for i in range(len(cols))]
        lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows]
        lines.insert(1, "  ".join("-" * w for w in widths))
        return "\n".join(lines)

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "results": self.results}


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def run_stt(folder: Path, engines: Sequence[SttEngine], download: bool) -> Report:
    pairs = find_pairs(folder)
    if not pairs:
        raise SystemExit(f"aucun couple <audio> + <audio>.txt dans {folder}")
    report = Report("stt")
    for eng in engines:
        if not eng.installed():
            report.results.append({"engine": eng.name, "status": "non installé"})
            continue
        try:
            transcribe = eng.load(download)
            errors: list[float] = []
            compute = audio_total = 0.0
            ref_words = err_words = 0
            for path, ref in pairs:
                t0 = time.perf_counter()
                hyp, duration = transcribe(path)
                compute += time.perf_counter() - t0
                audio_total += duration
                wer = word_error_rate(ref, hyp)
                errors.append(wer)
                n = len(normalize(ref))
                ref_words += n
                err_words += round(wer * n)
        except EngineUnavailableError as exc:
            report.results.append({"engine": eng.name, "status": str(exc)})
            continue
        except Exception as exc:  # un moteur qui plante ne coupe pas le banc
            report.results.append({"engine": eng.name, "status": f"erreur : {type(exc).__name__}: {exc}"})
            continue
        report.results.append(
            {
                "engine": eng.name,
                "status": "ok",
                "wer": err_words / ref_words if ref_words else 0.0,
                "wer_mean_per_file": _mean(errors),
                "rtf": compute / audio_total if audio_total else None,
                "files": len(pairs),
                "peak_rss_mb": peak_rss_mb(),
            }
        )
    return report


def run_tts(
    out: Path, engines: Sequence[TtsEngine], download: bool, phrases: Sequence[str] = PHRASES, seed: int | None = None
) -> Report:
    out.mkdir(parents=True, exist_ok=True)
    report = Report("tts")
    rng = random.Random(seed)  # noqa: S311
    mapping: list[dict[str, Any]] = []
    jobs: list[tuple[str, int, Path]] = []
    for eng in engines:
        if not eng.installed():
            report.results.append({"engine": eng.name, "status": "non installé"})
            continue
        try:
            synth = eng.load(download)
            firsts: list[float] = []
            compute = audio_total = 0.0
            files = 0
            for i, phrase in enumerate(phrases):
                t0 = time.perf_counter()
                samples, rate, first = synth(phrase)
                elapsed = time.perf_counter() - t0
                firsts.append(elapsed if first is None else first)
                tmp = out / f".{eng.name}-{i}.wav"
                audio_total += write_wav(tmp, samples, rate)
                compute += elapsed
                jobs.append((eng.name, i, tmp))
                files += 1
        except EngineUnavailableError as exc:
            report.results.append({"engine": eng.name, "status": str(exc)})
            continue
        except Exception as exc:
            report.results.append({"engine": eng.name, "status": f"erreur : {type(exc).__name__}: {exc}"})
            continue
        report.results.append(
            {
                "engine": eng.name,
                "status": "ok",
                "first_audio_s": _mean(firsts),
                "rtf": compute / audio_total if audio_total else None,
                "files": files,
                "peak_rss_mb": peak_rss_mb(),
            }
        )
    # Écoute à l'aveugle : noms anonymes, ordre mélangé, correspondance dans un fichier à part.
    rng.shuffle(jobs)
    for n, (name, i, tmp) in enumerate(jobs, 1):
        anon = f"{n:03d}-{rng.randrange(16**6):06x}.wav"
        tmp.rename(out / anon)
        mapping.append({"file": anon, "engine": name, "phrase_index": i, "phrase": phrases[i]})
    (out / "_correspondance.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="voice_bench", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("stt", "tts"):
        s = sub.add_parser(name)
        s.add_argument("--download", action="store_true", help="autorise le téléchargement des modèles (refusé sinon)")
        s.add_argument("--json", type=Path, help="écrit le rapport JSON ici")
        s.add_argument(
            "--engine",
            action="append",
            help="ne mesurer que ce moteur (répétable) ; un par processus donne un pic mémoire propre",
        )
        if name == "stt":
            s.add_argument("folder", type=Path, help="dossier d'enregistrements <nom>.wav + <nom>.txt")
        else:
            s.add_argument("--out", type=Path, required=True, help="dossier des WAV anonymisés")
            s.add_argument("--ref-audio", help="voix de référence du clonage (qwen3-tts-0.6b-clone*), même fichier partout")
            s.add_argument("--ref-text", help="transcription de la voix de référence (qualité de clonage meilleure)")
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    stt_engines: Sequence[SttEngine] | None = None,
    tts_engines: Sequence[TtsEngine] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "stt":
        engines = list(stt_engines if stt_engines is not None else default_stt_engines())
        if args.engine:
            engines = [e for e in engines if e.name in args.engine]
        report = run_stt(args.folder, engines, args.download)
    else:
        _clone_ref.update(audio=args.ref_audio, text=args.ref_text)
        engines = list(tts_engines if tts_engines is not None else default_tts_engines())
        if args.engine:
            engines = [e for e in engines if e.name in args.engine]
        report = run_tts(args.out, engines, args.download)
    print(report.table())
    if args.json:
        args.json.write_text(json.dumps(report.as_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
