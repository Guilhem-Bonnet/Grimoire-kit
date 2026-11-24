"""Interface commune des moteurs vocaux, registre, chargement à la demande.

- :class:`SpeechToText` et :class:`TextToSpeech` : ce que tout moteur offre.
- :data:`ENGINES` : le registre, clé ``(kind, nom)``. Une entrée nomme les modules
  dont le moteur a besoin et l'endroit de sa classe (``"module:Classe"``) ; rien
  n'est importé tant qu'on ne charge pas le moteur. :func:`availability` le dit
  installé ou non par :func:`importlib.util.find_spec`, sans l'importer.
- :class:`EngineSlot` : un moteur chargé au premier appel, déchargé après un délai
  d'inactivité réglable ou par :meth:`EngineSlot.unload`.
- :class:`VoiceEngines` : la paire reconnaissance + synthèse d'un projet.

Un moteur absent ou qui échoue lève :class:`VoiceError` (``EngineUnavailableError`` ou
``EngineCallError``) dont :attr:`VoiceError.reason` est une phrase lisible, sans trace :
c'est elle que l'interface montrera.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar

if TYPE_CHECKING:
    from grimoire.voice.settings import EngineChoice, VoiceSettings

__all__ = [
    "ENGINES",
    "Availability",
    "EngineCallError",
    "EngineSlot",
    "EngineSpec",
    "EngineUnavailableError",
    "Speech",
    "SpeechToText",
    "TextToSpeech",
    "Transcript",
    "VoiceEngines",
    "VoiceError",
    "availability",
    "engine_names",
    "get_spec",
    "install_hint",
    "register_engine",
    "voice_extra_installed",
]

Kind = Literal["stt", "tts"]
VOICE_EXTRA = "voice"


def install_hint(extra: str = VOICE_EXTRA) -> str:
    """Remède d'un extra manquant, selon le gestionnaire qui a installé le kit (pipx, uv, pip)."""
    from grimoire.cli.updater import extras_install_hint

    return extras_install_hint([extra])
_T = TypeVar("_T")


# ── Erreurs lisibles ──────────────────────────────────────────────────────────


class VoiceError(Exception):
    """Une erreur vocale portant une raison à montrer telle quelle."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EngineUnavailableError(VoiceError):
    """Le moteur ne peut pas être chargé : non installé, modèle absent, réglage inconnu."""


class EngineCallError(VoiceError):
    """Le moteur est chargé mais l'appel a échoué."""


def _one_line(exc: BaseException) -> str:
    """``Type: message`` sur une ligne, borné : jamais une trace."""
    text = " ".join(str(exc).split()) or "sans message"
    return f"{type(exc).__name__}: {text[:200]}"


# ── Interface commune ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Transcript:
    """Le texte reconnu. ``language`` et ``duration_s`` quand le moteur les donne."""

    text: str
    language: str | None = None
    duration_s: float | None = None


@dataclass(frozen=True, slots=True)
class Speech:
    """Un message parlé : WAV en octets, ou ``client_side`` — le navigateur le dira lui-même."""

    text: str
    audio: bytes | None = None
    sample_rate: int | None = None
    client_side: bool = False


class SpeechToText(ABC):
    """Reconnaissance de la parole."""

    #: Vrai quand la transcription se fait dans le navigateur, pas sur le serveur.
    client_side: ClassVar[bool] = False

    @abstractmethod
    def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> Transcript:
        """Transcrit ``audio`` (fichier ou octets). ``vocabulary`` : mots du projet à favoriser."""

    def close(self) -> None:  # noqa: B027 - facultatif : rien à libérer par défaut
        """Libère le modèle. Appelé au déchargement."""


class TextToSpeech(ABC):
    """Synthèse vocale."""

    client_side: ClassVar[bool] = False

    @abstractmethod
    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        """Rend ``text`` en message parlé."""

    def close(self) -> None:  # noqa: B027 - facultatif : rien à libérer par défaut
        """Libère le modèle. Appelé au déchargement."""


# ── Repli « navigateur » : aucun modèle côté serveur ──────────────────────────


class BrowserSpeechToText(SpeechToText):
    """La transcription se fait dans le navigateur (Web Speech) : le serveur reçoit déjà du texte."""

    client_side = True

    def __init__(self, **_options: Any) -> None:
        pass

    def transcribe(
        self,
        audio: bytes | Path,
        *,
        language: str | None = None,
        vocabulary: Sequence[str] = (),
    ) -> Transcript:
        raise EngineCallError(
            "le moteur « navigateur » transcrit dans la page : le serveur ne reçoit pas d'audio à transcrire"
        )


class BrowserTextToSpeech(TextToSpeech):
    """Le navigateur lit le texte avec ses propres voix : rien n'est synthétisé ici."""

    client_side = True

    def __init__(self, **_options: Any) -> None:
        pass

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        return Speech(text=text, client_side=True)


# ── Registre ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """Une entrée du registre. ``factory`` : ``"module:Classe"``, importé au chargement seulement."""

    name: str
    kind: Kind
    label: str
    factory: str
    modules: tuple[str, ...] = ()
    extra: str = VOICE_EXTRA


@dataclass(frozen=True, slots=True)
class Availability:
    ok: bool
    reason: str


ENGINES: dict[tuple[str, str], EngineSpec] = {}


def register_engine(spec: EngineSpec) -> None:
    """Ajoute (ou remplace) un moteur du registre."""
    ENGINES[(spec.kind, spec.name)] = spec


for _spec in (
    EngineSpec(
        "faster-whisper",
        "stt",
        "Whisper (faster-whisper)",
        "grimoire.voice.faster_whisper_engine:FasterWhisperSpeechToText",
        ("faster_whisper",),
    ),
    EngineSpec("browser", "stt", "Navigateur (Web Speech)", f"{__name__}:BrowserSpeechToText"),
    EngineSpec(
        "pocket-tts",
        "tts",
        "Pocket TTS (Kyutai)",
        "grimoire.voice.pocket_tts_engine:PocketTextToSpeech",
        ("pocket_tts",),
    ),
    EngineSpec("browser", "tts", "Voix du navigateur", f"{__name__}:BrowserTextToSpeech"),
):
    register_engine(_spec)


def engine_names(kind: Kind) -> list[str]:
    return sorted(name for k, name in ENGINES if k == kind)


def get_spec(kind: Kind, name: str) -> EngineSpec:
    spec = ENGINES.get((kind, name))
    if spec is None:
        what = "reconnaissance" if kind == "stt" else "synthèse"
        raise EngineUnavailableError(f"moteur de {what} « {name} » inconnu (connus : {', '.join(engine_names(kind))})")
    return spec


def _module_installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # module présent dans sys.modules sans spec, chemin cassé
        return False


def availability(spec: EngineSpec) -> Availability:
    """Le moteur peut-il se charger ? Ne l'importe pas : ``find_spec`` ne lit que le chemin."""
    missing = [m for m in spec.modules if not _module_installed(m)]
    if missing:
        return Availability(
            False, f"{spec.label} n'est pas installé ({', '.join(missing)} absent) : {install_hint(spec.extra)}"
        )
    return Availability(True, f"{spec.label} installé")


def voice_extra_installed() -> bool:
    """Vrai quand les moteurs locaux de l'extra ``voice`` sont tous installés."""
    return all(availability(spec).ok for spec in ENGINES.values() if spec.extra == VOICE_EXTRA and spec.modules)


# ── Chargement à la demande ───────────────────────────────────────────────────


class EngineSlot:
    """Un moteur chargé au premier appel, déchargé après ``idle_unload_s`` sans appel.

    ``idle_unload_s <= 0`` : jamais déchargé tout seul, seulement par :meth:`unload`.
    Les appels d'un même moteur sont sérialisés : un modèle n'est pas réentrant, et le
    déchargement attend la fin de l'appel en cours.
    """

    def __init__(
        self,
        spec: EngineSpec,
        options: dict[str, Any] | None = None,
        *,
        idle_unload_s: float,
        clock: Callable[[], float] = time.monotonic,
        timer_factory: Callable[[float, Callable[[], object]], Any] | None = None,
    ) -> None:
        self.spec = spec
        self.options = dict(options or {})
        self.idle_unload_s = idle_unload_s
        self._clock = clock
        self._timer_factory = timer_factory or _daemon_timer
        self._lock = threading.RLock()
        self._engine: Any = None
        self._last_used = 0.0
        self._timer: Any = None

    @property
    def loaded(self) -> bool:
        return self._engine is not None

    def check(self) -> Availability:
        return availability(self.spec)

    def _load(self) -> Any:
        avail = availability(self.spec)
        if not avail.ok:
            raise EngineUnavailableError(avail.reason)
        module_name, _, attr = self.spec.factory.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), attr)
        except Exception as exc:
            raise EngineUnavailableError(
                f"{self.spec.label} ne s'importe pas ({_one_line(exc)}) : {install_hint(self.spec.extra)}"
            ) from None
        try:
            return cls(**self.options)
        except VoiceError:
            raise
        except Exception as exc:
            raise EngineUnavailableError(f"{self.spec.label} n'a pas pu se charger ({_one_line(exc)})") from None

    def run(self, call: Callable[[Any], _T]) -> _T:
        """Charge le moteur s'il ne l'est pas, lui passe ``call``, relance le délai d'inactivité."""
        with self._lock:
            if self._engine is None:
                self._engine = self._load()
            try:
                return call(self._engine)
            except VoiceError:
                raise
            except Exception as exc:
                raise EngineCallError(f"{self.spec.label} a échoué ({_one_line(exc)})") from None
            finally:
                self._last_used = self._clock()
                self._arm_timer()

    def unload(self) -> bool:
        """Décharge le moteur. Faux s'il n'était pas chargé."""
        with self._lock:
            self._cancel_timer()
            engine, self._engine = self._engine, None
            if engine is None:
                return False
            with contextlib.suppress(Exception):  # au mieux : la référence est lâchée quand même
                engine.close()
            return True

    def reap_idle(self) -> bool:
        """Décharge le moteur s'il est inactif depuis ``idle_unload_s``. Vrai s'il l'a été."""
        with self._lock:
            if self._engine is None or self.idle_unload_s <= 0:
                return False
            if self._clock() - self._last_used < self.idle_unload_s:
                self._arm_timer()  # un appel est passé entre-temps : on attend la suite
                return False
            return self.unload()

    def _arm_timer(self) -> None:
        self._cancel_timer()
        if self.idle_unload_s > 0 and self._engine is not None:
            remaining = max(0.0, self.idle_unload_s - (self._clock() - self._last_used))
            self._timer = self._timer_factory(remaining, self.reap_idle)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def _daemon_timer(delay: float, callback: Callable[[], object]) -> threading.Timer:
    timer = threading.Timer(delay, callback)
    timer.daemon = True  # un modèle chargé ne retient pas la sortie du processus
    timer.start()
    return timer


@dataclass
class VoiceEngines:
    """La paire reconnaissance + synthèse d'un projet, chacune chargée à son premier appel."""

    stt: EngineSlot
    tts: EngineSlot
    language: str = "fr"

    @classmethod
    def from_settings(cls, settings: VoiceSettings, **slot_kwargs: Any) -> VoiceEngines:
        """Construit les deux emplacements sans rien charger. Un moteur inconnu lève ``EngineUnavailableError``."""
        return cls(
            stt=_slot("stt", settings.stt, settings, slot_kwargs),
            tts=_slot("tts", settings.tts, settings, slot_kwargs),
            language=settings.language,
        )

    def transcribe(self, audio: bytes | Path, *, vocabulary: Sequence[str] = ()) -> Transcript:
        def call(engine: SpeechToText) -> Transcript:
            return engine.transcribe(audio, language=self.language, vocabulary=vocabulary)

        return self.stt.run(call)

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        def call(engine: TextToSpeech) -> Speech:
            return engine.synthesize(text, voice=voice)

        return self.tts.run(call)

    def unload(self) -> None:
        self.stt.unload()
        self.tts.unload()


def _slot(kind: Kind, choice: EngineChoice, settings: VoiceSettings, slot_kwargs: dict[str, Any]) -> EngineSlot:
    spec = get_spec(kind, choice.engine)
    options = dict(choice.options)
    if spec.modules:  # moteurs locaux : la langue et l'accord de téléchargement viennent du projet
        options.setdefault("allow_download", settings.allow_download)
        options.setdefault("language", settings.language)
    return EngineSlot(spec, options, idle_unload_s=settings.idle_unload_s, **slot_kwargs)
