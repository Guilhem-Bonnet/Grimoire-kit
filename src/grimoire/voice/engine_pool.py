"""Un seul moteur de reconnaissance et un seul moteur de synthèse chargés pour tout le processus (#1343 V5).

Le cockpit sert plusieurs projets enrôlés : un moteur par projet multiplierait la mémoire (un modèle de
synthèse pèse plusieurs Go). :class:`EnginePool` garde les emplacements (:class:`EngineSlot`) par clé
``(nature, moteur, options)`` — options comprenant modèle, voix, langue et accord de téléchargement — : deux
projets aux mêmes réglages partagent le même modèle chargé. Pour une même nature, au plus
:attr:`EnginePool.capacity` emplacement est gardé : prendre un autre emplacement décharge et oublie le moins
récemment utilisé. Les appels d'une même nature sont sérialisés sous le verrou du pool, si bien qu'un
emplacement évincé ne peut pas se recharger en parallèle. Le déchargement après inactivité de chaque
emplacement reste celui d':class:`EngineSlot`.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from grimoire.voice.engines import EngineSlot, Kind, Speech, SpeechToText, TextToSpeech, Transcript, get_spec

if TYPE_CHECKING:
    from grimoire.voice.settings import EngineChoice, VoiceSettings

__all__ = ["EnginePool", "PooledEngines", "pool"]

_T = TypeVar("_T")


def _options_key(options: dict[str, Any]) -> str:
    return json.dumps(options, sort_keys=True, default=repr)


class EnginePool:
    """Les emplacements de moteurs du processus, au plus ``capacity`` par nature (``stt``, ``tts``)."""

    def __init__(self, capacity: int = 1, **slot_kwargs: Any) -> None:
        self.capacity = max(1, capacity)
        self._slot_kwargs = slot_kwargs
        self._slots: dict[str, OrderedDict[tuple[str, str], EngineSlot]] = {"stt": OrderedDict(), "tts": OrderedDict()}
        self._locks = {"stt": threading.RLock(), "tts": threading.RLock()}

    def _acquire(self, kind: Kind, choice: EngineChoice, settings: VoiceSettings) -> EngineSlot:
        """Emplacement de la clé, créé au besoin ; les autres de la même nature au-delà de la capacité sont évincés."""
        spec = get_spec(kind, choice.engine)
        options = dict(choice.options)
        if spec.modules:  # moteurs locaux : l'accord de téléchargement est celui du projet, jamais celui du moteur
            options["allow_download"] = settings.allow_download
            options.setdefault("language", settings.language)
        key = (spec.name, _options_key(options))
        slots = self._slots[kind]
        slot = slots.get(key)
        if slot is None:
            slot = slots[key] = EngineSlot(spec, options, idle_unload_s=settings.idle_unload_s, **self._slot_kwargs)
        else:
            slot.idle_unload_s = settings.idle_unload_s  # le dernier réglage lu fait foi
        slots.move_to_end(key)
        while len(slots) > self.capacity:
            _key, evicted = slots.popitem(last=False)
            evicted.unload()
        return slot

    def run(self, kind: Kind, choice: EngineChoice, settings: VoiceSettings, call: Callable[[Any], _T]) -> _T:
        """Passe ``call`` au moteur ``choice`` (chargé au besoin), sous le verrou de sa nature."""
        with self._locks[kind]:
            return self._acquire(kind, choice, settings).run(call)

    def loaded(self, kind: Kind) -> int:
        """Le nombre de moteurs de cette nature actuellement chargés en mémoire."""
        with self._locks[kind]:
            return sum(1 for slot in self._slots[kind].values() if slot.loaded)

    def reset(self) -> None:
        """Décharge et oublie tous les moteurs (tests, arrêt)."""
        for kind, lock in self._locks.items():
            with lock:
                held = list(self._slots[kind].values())
                self._slots[kind].clear()
            for slot in held:
                slot.unload()


#: Le pool du processus du cockpit.
pool = EnginePool()


class PooledEngines:
    """La paire reconnaissance + synthèse d'un projet, servie par le pool du processus."""

    def __init__(self, settings: VoiceSettings, engine_pool: EnginePool | None = None) -> None:
        self.settings = settings
        self._pool = engine_pool or pool

    def transcribe(self, audio: bytes | Path, *, vocabulary: Sequence[str] = ()) -> Transcript:
        language = self.settings.language

        def call(engine: SpeechToText) -> Transcript:
            return engine.transcribe(audio, language=language, vocabulary=vocabulary)

        return self._pool.run("stt", self.settings.stt, self.settings, call)

    def synthesize(self, text: str, *, voice: str | None = None) -> Speech:
        def call(engine: TextToSpeech) -> Speech:
            return engine.synthesize(text, voice=voice)

        return self._pool.run("tts", self.settings.tts, self.settings, call)
