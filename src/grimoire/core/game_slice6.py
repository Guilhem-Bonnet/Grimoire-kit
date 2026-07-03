"""Minimal deterministic primitives for Slice 6 (GAME-TKT-029..036).

This module intentionally focuses on a stable, dependency-free API surface that
is easy to test and persist.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from grimoire.core.exceptions import GrimoireError

__all__ = [
    "AUDIO_CHANNELS",
    "BOARD_CONFIGURATION_SCHEMA",
    "DEFAULT_ONBOARDING_STEPS",
    "DEFAULT_XP_PER_LEVEL",
    "INVESTIGATION_PHASE_SEQUENCE",
    "ROOM_TRANSITIONS",
    "AgentFactory",
    "AgentFactoryError",
    "AgentState",
    "AudioChannel",
    "AudioEvent",
    "AudioSettings",
    "AudioSystem",
    "AudioSystemError",
    "BoardConfiguration",
    "BranchFinishDecision",
    "BranchFinishOption",
    "BranchFinisher",
    "BranchFinisherError",
    "ConfigurationManager",
    "ConfigurationValidationError",
    "CriticalSecurityBlockingError",
    "DeskDirectoryMap",
    "DeskDirectoryMapError",
    "DiscardReason",
    "DiscardReasonRequiredError",
    "DoubleCreditError",
    "GameSlice6Error",
    "GridPosition",
    "InvestigationCriticalBlockingError",
    "InvestigationPhase",
    "InvestigationPhaseError",
    "InvestigationRootCauseError",
    "InvestigationState",
    "InvestigationWorkflow",
    "MapConstraintError",
    "MapEditor",
    "MapEditorError",
    "MapReadOnlyError",
    "OnboardingFlow",
    "OnboardingFlowError",
    "OnboardingState",
    "ProgressState",
    "ProgressionEngine",
    "ProgressionError",
    "RoomStatus",
    "RoomTransitionError",
    "SecurityAudit",
    "SecurityAuditError",
    "SecurityFinding",
    "SecuritySeverity",
    "SecurityTicket",
    "SensitiveMutationError",
    "SnapshotSource",
    "WorktreeRoom",
    "WorktreeRoomLifecycle",
    "WorktreeRoomLifecycleError",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GameSlice6Error(GrimoireError):
    """Base exception for slice-6 game domain errors."""


class AgentFactoryError(GameSlice6Error):
    """Invalid or unsafe agent factory operation."""


class SensitiveMutationError(AgentFactoryError):
    """Sensitive mutation attempted without restart confirmation."""


class ConfigurationValidationError(GameSlice6Error):
    """Invalid board configuration payload or values."""


class AudioSystemError(GameSlice6Error):
    """Audio system persistence/state error."""


class ProgressionError(GameSlice6Error):
    """Progression engine operation error."""


class DoubleCreditError(ProgressionError):
    """Action already granted XP and cannot be credited twice."""


class OnboardingFlowError(GameSlice6Error):
    """Onboarding flow state transition error."""


class InvestigationPhaseError(GameSlice6Error):
    """Invalid investigation phase transition."""


class InvestigationRootCauseError(InvestigationPhaseError):
    """Cannot enter FIX_PROPOSED without a root cause."""


class InvestigationCriticalBlockingError(InvestigationPhaseError):
    """Cannot complete investigation while critical issues remain."""


class SecurityAuditError(GameSlice6Error):
    """Invalid security audit operation."""


class BranchFinisherError(GameSlice6Error):
    """Invalid branch finishing operation."""


class DiscardReasonRequiredError(BranchFinisherError):
    """Discard option requires a typed discard reason."""


class CriticalSecurityBlockingError(BranchFinisherError):
    """Shipping is blocked by unresolved critical security findings."""


class MapEditorError(GameSlice6Error):
    """Map editor operation error."""


class MapReadOnlyError(MapEditorError):
    """Mutation attempted while map editor is read-only."""


class MapConstraintError(MapEditorError):
    """Grid constraints were violated by a map operation."""


class DeskDirectoryMapError(GameSlice6Error):
    """Desk-directory mapping conflict or persistence error."""


class WorktreeRoomLifecycleError(GameSlice6Error):
    """Worktree room lifecycle operation error."""


class RoomTransitionError(WorktreeRoomLifecycleError):
    """Invalid room lifecycle transition."""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_non_empty(value: str, *, field_name: str, exc_type: type[GameSlice6Error]) -> str:
    if not value or not value.strip():
        raise exc_type(f"'{field_name}' must be a non-empty string")
    return value.strip()


def _require_non_negative_int(value: Any, *, field_name: str, exc_type: type[GameSlice6Error]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise exc_type(f"'{field_name}' must be a non-negative integer")
    return value


def _require_positive_int(value: Any, *, field_name: str, exc_type: type[GameSlice6Error]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise exc_type(f"'{field_name}' must be a positive integer")
    return value


# ---------------------------------------------------------------------------
# 1) AgentFactory
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentState:
    """Serializable agent state for deterministic gameplay operations."""

    agent_id: str
    name: str
    archetype: str
    xp: int = 0
    history: tuple[str, ...] = ()
    sensitive_mode: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.agent_id, field_name="agent_id", exc_type=AgentFactoryError)
        _require_non_empty(self.name, field_name="name", exc_type=AgentFactoryError)
        _require_non_empty(self.archetype, field_name="archetype", exc_type=AgentFactoryError)
        _require_non_negative_int(self.xp, field_name="xp", exc_type=AgentFactoryError)


class AgentFactory:
    """Factory for deterministic agent creation, cloning, and sensitive mutation."""

    def create_agent(
        self,
        agent_id: str,
        name: str,
        archetype: str,
        xp: int = 0,
        history: Iterable[str] | None = None,
        sensitive_mode: bool = False,
    ) -> AgentState:
        entries: tuple[str, ...] = tuple(history or ())
        return AgentState(
            agent_id=agent_id,
            name=name,
            archetype=archetype,
            xp=xp,
            history=entries,
            sensitive_mode=sensitive_mode,
        )

    def clone_agent(self, source: AgentState, new_agent_id: str, new_name: str | None = None) -> AgentState:
        """Clone an agent without inheriting XP/history."""
        _require_non_empty(new_agent_id, field_name="new_agent_id", exc_type=AgentFactoryError)
        clone_name = new_name if new_name is not None else source.name
        return AgentState(
            agent_id=new_agent_id,
            name=clone_name,
            archetype=source.archetype,
            xp=0,
            history=(),
            sensitive_mode=source.sensitive_mode,
        )

    def mutate_sensitive(
        self,
        agent: AgentState,
        sensitive_mode: bool,
        restart_confirmed: bool,
    ) -> AgentState:
        """Apply sensitive mode mutation only when restart is explicitly confirmed."""
        if not restart_confirmed:
            raise SensitiveMutationError(
                "Sensitive mutation requires restart confirmation (restart_confirmed=True)"
            )
        return replace(agent, sensitive_mode=sensitive_mode)


# ---------------------------------------------------------------------------
# 2) ConfigurationManager + BoardConfiguration
# ---------------------------------------------------------------------------


class SnapshotSource(StrEnum):
    """Source of truth used by restart synchronization."""

    RUNTIME = "runtime"
    STORAGE = "storage"


BOARD_CONFIGURATION_SCHEMA: Mapping[str, type[object]] = MappingProxyType({
    "width": int,
    "height": int,
    "max_desks": int,
    "theme": str,
})


@dataclass(frozen=True, slots=True)
class BoardConfiguration:
    """Validated board settings shared by runtime and persisted storage."""

    width: int
    height: int
    max_desks: int
    theme: str = "classic"

    def __post_init__(self) -> None:
        width = _require_positive_int(self.width, field_name="width", exc_type=ConfigurationValidationError)
        height = _require_positive_int(self.height, field_name="height", exc_type=ConfigurationValidationError)
        max_desks = _require_positive_int(
            self.max_desks,
            field_name="max_desks",
            exc_type=ConfigurationValidationError,
        )
        if max_desks > width * height:
            raise ConfigurationValidationError("'max_desks' cannot exceed board capacity (width * height)")
        _require_non_empty(self.theme, field_name="theme", exc_type=ConfigurationValidationError)

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "max_desks": self.max_desks,
            "theme": self.theme,
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> BoardConfiguration:
        expected = set(BOARD_CONFIGURATION_SCHEMA)
        provided = set(payload)
        missing = sorted(expected - provided)
        if missing:
            raise ConfigurationValidationError(f"Missing configuration keys: {', '.join(missing)}")

        unknown = sorted(provided - expected)
        if unknown:
            raise ConfigurationValidationError(f"Unknown configuration keys: {', '.join(unknown)}")

        width = _require_positive_int(payload["width"], field_name="width", exc_type=ConfigurationValidationError)
        height = _require_positive_int(
            payload["height"],
            field_name="height",
            exc_type=ConfigurationValidationError,
        )
        max_desks = _require_positive_int(
            payload["max_desks"],
            field_name="max_desks",
            exc_type=ConfigurationValidationError,
        )
        theme_value = payload["theme"]
        if not isinstance(theme_value, str):
            raise ConfigurationValidationError("'theme' must be a string")
        return cls(width=width, height=height, max_desks=max_desks, theme=theme_value)


class ConfigurationManager:
    """Maintains runtime/storage board snapshots and restart synchronization."""

    validation_schema: Mapping[str, type[object]] = BOARD_CONFIGURATION_SCHEMA

    def __init__(self, initial_configuration: BoardConfiguration) -> None:
        self._runtime = initial_configuration
        self._storage = initial_configuration

    @property
    def runtime(self) -> BoardConfiguration:
        return self._runtime

    @property
    def storage(self) -> BoardConfiguration:
        return self._storage

    def validate_schema(self, payload: Mapping[str, Any]) -> BoardConfiguration:
        return BoardConfiguration.from_snapshot(payload)

    def update_runtime(self, configuration: BoardConfiguration) -> None:
        self._runtime = configuration

    def update_storage(self, configuration: BoardConfiguration) -> None:
        self._storage = configuration

    def runtime_snapshot(self) -> dict[str, Any]:
        return self._runtime.to_snapshot()

    def storage_snapshot(self) -> dict[str, Any]:
        return self._storage.to_snapshot()

    def detect_divergence(self) -> bool:
        return self._runtime != self._storage

    def restart_sync(self, *, source: SnapshotSource = SnapshotSource.STORAGE) -> BoardConfiguration:
        if source is SnapshotSource.STORAGE:
            self._runtime = self._storage
        else:
            self._storage = self._runtime
        return self._runtime


# ---------------------------------------------------------------------------
# 3) AudioSystem + AudioSettings
# ---------------------------------------------------------------------------


class AudioChannel(StrEnum):
    """Supported audio channels with independent toggles."""

    MUSIC = "music"
    EFFECTS = "effects"
    VOICE = "voice"


AUDIO_CHANNELS: tuple[AudioChannel, ...] = (
    AudioChannel.MUSIC,
    AudioChannel.EFFECTS,
    AudioChannel.VOICE,
)


@dataclass(frozen=True, slots=True)
class AudioSettings:
    """Independent channel toggles plus master mute."""

    master_mute: bool = False
    music_enabled: bool = True
    effects_enabled: bool = True
    voice_enabled: bool = True

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "master_mute": self.master_mute,
            "music_enabled": self.music_enabled,
            "effects_enabled": self.effects_enabled,
            "voice_enabled": self.voice_enabled,
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> AudioSettings:
        return cls(
            master_mute=bool(payload.get("master_mute", False)),
            music_enabled=bool(payload.get("music_enabled", True)),
            effects_enabled=bool(payload.get("effects_enabled", True)),
            voice_enabled=bool(payload.get("voice_enabled", True)),
        )


@dataclass(frozen=True, slots=True)
class AudioEvent:
    """Deterministic audio event descriptor."""

    name: str
    channel: AudioChannel = AudioChannel.EFFECTS

    def __post_init__(self) -> None:
        _require_non_empty(self.name, field_name="name", exc_type=AudioSystemError)

    @property
    def key(self) -> str:
        return f"{self.channel.value}:{self.name}"


class AudioSystem:
    """Audio system with de-duplicated events and persistence support."""

    def __init__(self, settings: AudioSettings | None = None) -> None:
        self._settings = settings or AudioSettings()
        self._pending_events: list[AudioEvent] = []
        self._pending_keys: set[str] = set()

    @property
    def settings(self) -> AudioSettings:
        return self._settings

    def set_master_mute(self, enabled: bool) -> None:
        self._settings = replace(self._settings, master_mute=bool(enabled))

    def set_music_enabled(self, enabled: bool) -> None:
        self._settings = replace(self._settings, music_enabled=bool(enabled))

    def set_effects_enabled(self, enabled: bool) -> None:
        self._settings = replace(self._settings, effects_enabled=bool(enabled))

    def set_voice_enabled(self, enabled: bool) -> None:
        self._settings = replace(self._settings, voice_enabled=bool(enabled))

    def trigger_event(
        self,
        event: AudioEvent | str,
        channel: AudioChannel | str = AudioChannel.EFFECTS,
    ) -> bool:
        """Queue an event once; duplicate pending events are ignored."""
        audio_event = self._coerce_event(event=event, channel=channel)
        if self._settings.master_mute:
            return False
        if not self._channel_enabled(audio_event.channel):
            return False
        if audio_event.key in self._pending_keys:
            return False

        self._pending_events.append(audio_event)
        self._pending_keys.add(audio_event.key)
        return True

    def drain_events(self) -> tuple[AudioEvent, ...]:
        drained = tuple(self._pending_events)
        self._pending_events.clear()
        self._pending_keys.clear()
        return drained

    def persistence_state(self) -> dict[str, Any]:
        return {
            "settings": self._settings.to_snapshot(),
            "pending_events": [
                {"name": event.name, "channel": event.channel.value}
                for event in self._pending_events
            ],
        }

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        settings_payload = payload.get("settings")
        if not isinstance(settings_payload, Mapping):
            raise AudioSystemError("Invalid audio persistence payload: 'settings' mapping is required")

        events_payload = payload.get("pending_events", [])
        if not isinstance(events_payload, list):
            raise AudioSystemError("Invalid audio persistence payload: 'pending_events' must be a list")

        self._settings = AudioSettings.from_snapshot(settings_payload)
        self._pending_events.clear()
        self._pending_keys.clear()

        for item in events_payload:
            if not isinstance(item, Mapping):
                raise AudioSystemError("Invalid event payload: each event must be a mapping")
            name = item.get("name")
            channel = item.get("channel", AudioChannel.EFFECTS.value)
            if not isinstance(name, str) or not isinstance(channel, str):
                raise AudioSystemError("Invalid event payload: 'name' and 'channel' must be strings")
            event = self._coerce_event(event=name, channel=channel)
            if event.key not in self._pending_keys:
                self._pending_events.append(event)
                self._pending_keys.add(event.key)

    def _coerce_event(self, event: AudioEvent | str, channel: AudioChannel | str) -> AudioEvent:
        if isinstance(event, AudioEvent):
            return event
        if not isinstance(event, str):
            raise AudioSystemError("Audio event must be an AudioEvent or string")
        return AudioEvent(name=event, channel=self._coerce_channel(channel))

    @staticmethod
    def _coerce_channel(channel: AudioChannel | str) -> AudioChannel:
        if isinstance(channel, AudioChannel):
            return channel
        if not isinstance(channel, str):
            raise AudioSystemError("Audio channel must be an AudioChannel or string")
        try:
            return AudioChannel(channel)
        except ValueError as exc:
            raise AudioSystemError(f"Unsupported audio channel: {channel}") from exc

    def _channel_enabled(self, channel: AudioChannel) -> bool:
        if channel is AudioChannel.MUSIC:
            return self._settings.music_enabled
        if channel is AudioChannel.VOICE:
            return self._settings.voice_enabled
        return self._settings.effects_enabled


# ---------------------------------------------------------------------------
# 4) ProgressionEngine + ProgressState
# ---------------------------------------------------------------------------


DEFAULT_XP_PER_LEVEL = 100


@dataclass(frozen=True, slots=True)
class ProgressState:
    """Persistent progression snapshot with anti double-credit memory."""

    total_xp: int = 0
    level: int = 1
    credited_actions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _require_non_negative_int(
            self.total_xp,
            field_name="total_xp",
            exc_type=ProgressionError,
        )
        _require_positive_int(self.level, field_name="level", exc_type=ProgressionError)

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "total_xp": self.total_xp,
            "level": self.level,
            "credited_actions": sorted(self.credited_actions),
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> ProgressState:
        total_xp = _require_non_negative_int(
            payload.get("total_xp", 0),
            field_name="total_xp",
            exc_type=ProgressionError,
        )
        level = _require_positive_int(payload.get("level", 1), field_name="level", exc_type=ProgressionError)
        actions_payload = payload.get("credited_actions", [])
        if not isinstance(actions_payload, list) or not all(isinstance(item, str) for item in actions_payload):
            raise ProgressionError("'credited_actions' must be a list[str]")
        return cls(total_xp=total_xp, level=level, credited_actions=frozenset(actions_payload))


class ProgressionEngine:
    """Deterministic XP progression with duplicate-credit blocking."""

    def __init__(self, state: ProgressState | None = None, *, xp_per_level: int = DEFAULT_XP_PER_LEVEL) -> None:
        self._xp_per_level = _require_positive_int(
            xp_per_level,
            field_name="xp_per_level",
            exc_type=ProgressionError,
        )
        self._state = state or ProgressState()
        self._state = replace(self._state, level=self.level_for_xp(self._state.total_xp))

    @property
    def state(self) -> ProgressState:
        return self._state

    @property
    def xp_per_level(self) -> int:
        return self._xp_per_level

    def level_for_xp(self, total_xp: int) -> int:
        xp = _require_non_negative_int(total_xp, field_name="total_xp", exc_type=ProgressionError)
        return 1 + (xp // self._xp_per_level)

    def award_xp(self, *, action_id: str, amount: int) -> ProgressState:
        """Award XP once per action id to prevent deterministic double credit."""
        normalized_action = _require_non_empty(action_id, field_name="action_id", exc_type=ProgressionError)
        delta = _require_positive_int(amount, field_name="amount", exc_type=ProgressionError)

        if normalized_action in self._state.credited_actions:
            raise DoubleCreditError(f"Action already credited: {normalized_action}")

        total_xp = self._state.total_xp + delta
        credited_actions = frozenset((*self._state.credited_actions, normalized_action))
        self._state = ProgressState(
            total_xp=total_xp,
            level=self.level_for_xp(total_xp),
            credited_actions=credited_actions,
        )
        return self._state

    def persistence_state(self) -> dict[str, Any]:
        payload = self._state.to_snapshot()
        payload["xp_per_level"] = self._xp_per_level
        return payload

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        xp_per_level = payload.get("xp_per_level", self._xp_per_level)
        self._xp_per_level = _require_positive_int(
            xp_per_level,
            field_name="xp_per_level",
            exc_type=ProgressionError,
        )
        loaded_state = ProgressState.from_snapshot(payload)
        self._state = replace(loaded_state, level=self.level_for_xp(loaded_state.total_xp))


# ---------------------------------------------------------------------------
# 5) OnboardingFlow + OnboardingState
# ---------------------------------------------------------------------------


DEFAULT_ONBOARDING_STEPS = ("welcome", "controls", "first-investigation", "ready")


@dataclass(frozen=True, slots=True)
class OnboardingState:
    """Persistent onboarding state supporting skip and step resume."""

    steps: tuple[str, ...] = DEFAULT_ONBOARDING_STEPS
    current_step_index: int = 0
    started: bool = True
    completed: bool = False
    skipped_permanently: bool = False

    def __post_init__(self) -> None:
        if not self.steps:
            raise OnboardingFlowError("'steps' must not be empty")
        if not all(isinstance(step, str) and step.strip() for step in self.steps):
            raise OnboardingFlowError("Each onboarding step must be a non-empty string")
        if self.current_step_index < 0 or self.current_step_index >= len(self.steps):
            raise OnboardingFlowError("'current_step_index' is outside onboarding steps range")

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "steps": list(self.steps),
            "current_step_index": self.current_step_index,
            "started": self.started,
            "completed": self.completed,
            "skipped_permanently": self.skipped_permanently,
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> OnboardingState:
        raw_steps = payload.get("steps", list(DEFAULT_ONBOARDING_STEPS))
        if not isinstance(raw_steps, list) or not all(isinstance(step, str) for step in raw_steps):
            raise OnboardingFlowError("'steps' must be a list[str]")

        current_step_index = _require_non_negative_int(
            payload.get("current_step_index", 0),
            field_name="current_step_index",
            exc_type=OnboardingFlowError,
        )
        started = payload.get("started", False)
        completed = payload.get("completed", False)
        skipped = payload.get("skipped_permanently", False)
        if not isinstance(started, bool) or not isinstance(completed, bool) or not isinstance(skipped, bool):
            raise OnboardingFlowError(
                "'started', 'completed', and 'skipped_permanently' must be booleans"
            )

        return cls(
            steps=tuple(raw_steps),
            current_step_index=current_step_index,
            started=started,
            completed=completed,
            skipped_permanently=skipped,
        )


class OnboardingFlow:
    """First-run onboarding flow with definitive skip and step resumption."""

    def __init__(self, state: OnboardingState | None = None) -> None:
        # first-run autostart: if no persisted state, onboarding starts immediately
        self._state = state or OnboardingState(started=True)

    @property
    def state(self) -> OnboardingState:
        return self._state

    def current_step(self) -> str | None:
        if self._state.completed or self._state.skipped_permanently:
            return None
        return self._state.steps[self._state.current_step_index]

    def advance(self) -> OnboardingState:
        if self._state.skipped_permanently:
            raise OnboardingFlowError("Onboarding has been skipped permanently")
        if self._state.completed:
            return self._state

        if self._state.current_step_index + 1 >= len(self._state.steps):
            self._state = replace(self._state, completed=True, started=False)
            return self._state

        self._state = replace(
            self._state,
            current_step_index=self._state.current_step_index + 1,
            started=True,
        )
        return self._state

    def skip_permanently(self) -> OnboardingState:
        self._state = replace(
            self._state,
            skipped_permanently=True,
            completed=True,
            started=False,
        )
        return self._state

    def resume_step(self, step_index: int | None = None) -> OnboardingState:
        if self._state.skipped_permanently:
            raise OnboardingFlowError("Cannot resume onboarding: permanently skipped")
        if self._state.completed:
            return self._state
        if step_index is not None:
            if step_index < 0 or step_index >= len(self._state.steps):
                raise OnboardingFlowError("step_index out of range")
            self._state = replace(self._state, current_step_index=step_index)
        self._state = replace(self._state, started=True)
        return self._state

    def persistence_state(self) -> dict[str, Any]:
        return self._state.to_snapshot()

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        self._state = OnboardingState.from_snapshot(payload)


# ---------------------------------------------------------------------------
# 6) InvestigationWorkflow + InvestigationState
# ---------------------------------------------------------------------------


class InvestigationPhase(StrEnum):
    """Strict four-phase investigation lifecycle."""

    DETECTION = "detection"
    ROOT_CAUSE = "root_cause"
    FIX_PROPOSED = "fix_proposed"
    VERIFICATION = "verification"


INVESTIGATION_PHASE_SEQUENCE: tuple[InvestigationPhase, ...] = (
    InvestigationPhase.DETECTION,
    InvestigationPhase.ROOT_CAUSE,
    InvestigationPhase.FIX_PROPOSED,
    InvestigationPhase.VERIFICATION,
)


@dataclass(frozen=True, slots=True)
class InvestigationState:
    """Persistent state for strict investigation workflow progression."""

    phase: InvestigationPhase = InvestigationPhase.DETECTION
    root_cause: str | None = None
    fix_proposal: str | None = None
    unresolved_critical: int = 0
    fix_failed_count: int = 0
    escalated: bool = False
    phase_history: tuple[InvestigationPhase, ...] = (InvestigationPhase.DETECTION,)

    def __post_init__(self) -> None:
        _require_non_negative_int(
            self.unresolved_critical,
            field_name="unresolved_critical",
            exc_type=InvestigationPhaseError,
        )
        _require_non_negative_int(
            self.fix_failed_count,
            field_name="fix_failed_count",
            exc_type=InvestigationPhaseError,
        )

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "root_cause": self.root_cause,
            "fix_proposal": self.fix_proposal,
            "unresolved_critical": self.unresolved_critical,
            "fix_failed_count": self.fix_failed_count,
            "escalated": self.escalated,
            "phase_history": [phase.value for phase in self.phase_history],
        }

    @classmethod
    def from_snapshot(cls, payload: Mapping[str, Any]) -> InvestigationState:
        phase_raw = payload.get("phase", InvestigationPhase.DETECTION.value)
        if not isinstance(phase_raw, str):
            raise InvestigationPhaseError("'phase' must be a string")
        try:
            phase = InvestigationPhase(phase_raw)
        except ValueError as exc:
            raise InvestigationPhaseError(f"Unknown investigation phase: {phase_raw}") from exc

        history_payload = payload.get("phase_history", [phase.value])
        if not isinstance(history_payload, list) or not all(isinstance(item, str) for item in history_payload):
            raise InvestigationPhaseError("'phase_history' must be a list[str]")
        phase_history_values: list[InvestigationPhase] = []
        for item in history_payload:
            try:
                phase_history_values.append(InvestigationPhase(item))
            except ValueError as exc:
                raise InvestigationPhaseError(f"Unknown phase in phase_history: {item}") from exc
        phase_history = tuple(phase_history_values)

        root_cause = payload.get("root_cause")
        fix_proposal = payload.get("fix_proposal")
        if root_cause is not None and not isinstance(root_cause, str):
            raise InvestigationPhaseError("'root_cause' must be a string or None")
        if fix_proposal is not None and not isinstance(fix_proposal, str):
            raise InvestigationPhaseError("'fix_proposal' must be a string or None")
        return cls(
            phase=phase,
            root_cause=root_cause,
            fix_proposal=fix_proposal,
            unresolved_critical=_require_non_negative_int(
                payload.get("unresolved_critical", 0),
                field_name="unresolved_critical",
                exc_type=InvestigationPhaseError,
            ),
            fix_failed_count=_require_non_negative_int(
                payload.get("fix_failed_count", 0),
                field_name="fix_failed_count",
                exc_type=InvestigationPhaseError,
            ),
            escalated=bool(payload.get("escalated", False)),
            phase_history=phase_history,
        )


class InvestigationWorkflow:
    """Implements strict phase sequencing and escalation policy."""

    def __init__(self, state: InvestigationState | None = None) -> None:
        self._state = state or InvestigationState()

    @property
    def state(self) -> InvestigationState:
        return self._state

    def set_root_cause(self, description: str) -> InvestigationState:
        normalized = _require_non_empty(
            description,
            field_name="description",
            exc_type=InvestigationRootCauseError,
        )
        self._state = replace(self._state, root_cause=normalized)
        return self._state

    def set_fix_proposal(self, proposal: str) -> InvestigationState:
        normalized = _require_non_empty(proposal, field_name="proposal", exc_type=InvestigationPhaseError)
        self._state = replace(self._state, fix_proposal=normalized)
        return self._state

    def report_critical_issue(self, *, count: int = 1) -> InvestigationState:
        delta = _require_positive_int(count, field_name="count", exc_type=InvestigationPhaseError)
        self._state = replace(self._state, unresolved_critical=self._state.unresolved_critical + delta)
        return self._state

    def resolve_critical_issue(self, *, count: int = 1) -> InvestigationState:
        delta = _require_positive_int(count, field_name="count", exc_type=InvestigationPhaseError)
        if delta > self._state.unresolved_critical:
            raise InvestigationPhaseError("Cannot resolve more critical issues than currently open")
        self._state = replace(self._state, unresolved_critical=self._state.unresolved_critical - delta)
        return self._state

    def advance_phase(self) -> InvestigationState:
        sequence = INVESTIGATION_PHASE_SEQUENCE
        current_index = sequence.index(self._state.phase)
        if current_index + 1 >= len(sequence):
            raise InvestigationPhaseError("Investigation already in final phase")

        next_phase = sequence[current_index + 1]
        if next_phase is InvestigationPhase.FIX_PROPOSED and not self._state.root_cause:
            raise InvestigationRootCauseError("Root cause is required before entering FIX_PROPOSED")
        if next_phase is InvestigationPhase.VERIFICATION and self._state.unresolved_critical > 0:
            raise InvestigationCriticalBlockingError(
                "Cannot enter VERIFICATION while critical issues are unresolved"
            )

        self._state = replace(
            self._state,
            phase=next_phase,
            phase_history=(*self._state.phase_history, next_phase),
        )
        return self._state

    def mark_fix_failed(self) -> InvestigationState:
        failures = self._state.fix_failed_count + 1
        self._state = replace(self._state, fix_failed_count=failures, escalated=failures >= 3)
        return self._state

    def persistence_state(self) -> dict[str, Any]:
        return self._state.to_snapshot()

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        self._state = InvestigationState.from_snapshot(payload)


# ---------------------------------------------------------------------------
# 7) BranchFinisher + SecurityAudit + SecurityFinding
# ---------------------------------------------------------------------------


class SecuritySeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BranchFinishOption(StrEnum):
    MERGE = "merge"
    PR = "pr"
    KEEP = "keep"
    DISCARD = "discard"


class DiscardReason(StrEnum):
    DUPLICATE = "duplicate"
    ABANDONED_EXPERIMENT = "abandoned_experiment"
    OBSOLETE = "obsolete"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class SecurityFinding:
    """A security finding discovered by audit checks."""

    finding_id: str
    title: str
    severity: SecuritySeverity
    resolved: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.finding_id, field_name="finding_id", exc_type=SecurityAuditError)
        _require_non_empty(self.title, field_name="title", exc_type=SecurityAuditError)


@dataclass(frozen=True, slots=True)
class SecurityTicket:
    """Generated ticket for unresolved security work."""

    ticket_id: str
    finding_id: str
    severity: SecuritySeverity
    title: str


class SecurityAudit:
    """Collects findings, enforces critical blockers, and generates tickets."""

    def __init__(self, findings: Iterable[SecurityFinding] | None = None) -> None:
        self._findings: dict[str, SecurityFinding] = {}
        for finding in findings or ():
            self.add_finding(finding)

    def add_finding(self, finding: SecurityFinding) -> None:
        if finding.finding_id in self._findings:
            raise SecurityAuditError(f"Duplicate finding id: {finding.finding_id}")
        self._findings[finding.finding_id] = finding

    def resolve_finding(self, finding_id: str) -> SecurityFinding:
        normalized = _require_non_empty(finding_id, field_name="finding_id", exc_type=SecurityAuditError)
        finding = self._findings.get(normalized)
        if finding is None:
            raise SecurityAuditError(f"Unknown finding id: {normalized}")
        updated = replace(finding, resolved=True)
        self._findings[normalized] = updated
        return updated

    def findings(self) -> tuple[SecurityFinding, ...]:
        return tuple(self._findings[key] for key in sorted(self._findings))

    def unresolved_findings(self) -> tuple[SecurityFinding, ...]:
        return tuple(finding for finding in self.findings() if not finding.resolved)

    def has_critical_blocker(self) -> bool:
        return any(
            finding.severity is SecuritySeverity.CRITICAL
            for finding in self.unresolved_findings()
        )

    def can_ship(self) -> bool:
        return not self.has_critical_blocker()

    def generate_security_tickets(self, *, prefix: str = "SEC") -> tuple[SecurityTicket, ...]:
        _require_non_empty(prefix, field_name="prefix", exc_type=SecurityAuditError)
        tickets: list[SecurityTicket] = []
        for index, finding in enumerate(self.unresolved_findings(), start=1):
            tickets.append(SecurityTicket(
                ticket_id=f"{prefix}-{index:04d}",
                finding_id=finding.finding_id,
                severity=finding.severity,
                title=finding.title,
            ))
        return tuple(tickets)


@dataclass(frozen=True, slots=True)
class BranchFinishDecision:
    """Deterministic output for branch finalization."""

    option: BranchFinishOption
    ship_allowed: bool
    discard_reason: DiscardReason | None = None
    generated_security_tickets: tuple[SecurityTicket, ...] = ()


class BranchFinisher:
    """Finalizes branch strategy with explicit security gates."""

    def finalize(
        self,
        option: BranchFinishOption | str,
        audit: SecurityAudit,
        discard_reason: DiscardReason | None = None,
    ) -> BranchFinishDecision:
        chosen = self._normalize_option(option)

        if chosen is BranchFinishOption.DISCARD:
            if discard_reason is None or not isinstance(discard_reason, DiscardReason):
                raise DiscardReasonRequiredError(
                    "discard_reason must be a DiscardReason when option=DISCARD"
                )
            return BranchFinishDecision(
                option=chosen,
                ship_allowed=False,
                discard_reason=discard_reason,
                generated_security_tickets=(),
            )

        if chosen in (BranchFinishOption.MERGE, BranchFinishOption.PR):
            if not audit.can_ship():
                raise CriticalSecurityBlockingError(
                    "Cannot ship branch with unresolved critical security finding(s)"
                )
            return BranchFinishDecision(
                option=chosen,
                ship_allowed=True,
                discard_reason=None,
                generated_security_tickets=audit.generate_security_tickets(),
            )

        # KEEP
        return BranchFinishDecision(
            option=chosen,
            ship_allowed=False,
            discard_reason=None,
            generated_security_tickets=audit.generate_security_tickets(),
        )

    @staticmethod
    def _normalize_option(option: BranchFinishOption | str) -> BranchFinishOption:
        if isinstance(option, BranchFinishOption):
            return option
        if not isinstance(option, str):
            raise BranchFinisherError("Branch finish option must be a BranchFinishOption or string")
        normalized = option.lower().replace("-", "_")
        if normalized == "pull_request":
            return BranchFinishOption.PR
        try:
            return BranchFinishOption(normalized)
        except ValueError as exc:
            raise BranchFinisherError(f"Unsupported branch finish option: {option}") from exc


# ---------------------------------------------------------------------------
# 8) MapEditor + DeskDirectoryMap + WorktreeRoomLifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GridPosition:
    x: int
    y: int

    def __post_init__(self) -> None:
        _require_non_negative_int(self.x, field_name="x", exc_type=MapConstraintError)
        _require_non_negative_int(self.y, field_name="y", exc_type=MapConstraintError)


@dataclass(frozen=True, slots=True)
class _MapEdit:
    before: tuple[tuple[str, GridPosition], ...]
    after: tuple[tuple[str, GridPosition], ...]


class MapEditor:
    """Grid editor with bounds checks, undo/redo, and read-only gate."""

    def __init__(self, *, width: int, height: int, read_only: bool = False) -> None:
        self._width = _require_positive_int(width, field_name="width", exc_type=MapConstraintError)
        self._height = _require_positive_int(height, field_name="height", exc_type=MapConstraintError)
        self._read_only = bool(read_only)
        self._desks: dict[str, GridPosition] = {}
        self._occupied: dict[GridPosition, str] = {}
        self._undo_stack: list[_MapEdit] = []
        self._redo_stack: list[_MapEdit] = []

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def read_only(self) -> bool:
        return self._read_only

    def set_read_only(self, enabled: bool) -> None:
        self._read_only = bool(enabled)

    def place_desk(
        self,
        desk_id: str,
        x: int | None = None,
        y: int | None = None,
        *,
        position: GridPosition | None = None,
    ) -> None:
        self._assert_writable()
        normalized_id = _require_non_empty(desk_id, field_name="desk_id", exc_type=MapConstraintError)
        position = self._coerce_position(x=x, y=y, position=position)
        self._assert_in_bounds(position)
        if normalized_id in self._desks:
            raise MapConstraintError(f"Desk already exists: {normalized_id}")
        owner = self._occupied.get(position)
        if owner is not None:
            raise MapConstraintError(
                f"Cell already occupied by desk '{owner}' at ({position.x}, {position.y})"
            )

        before = self._snapshot()
        self._desks[normalized_id] = position
        self._occupied[position] = normalized_id
        self._record_edit(before)

    def move_desk(
        self,
        desk_id: str,
        x: int | None = None,
        y: int | None = None,
        *,
        position: GridPosition | None = None,
    ) -> None:
        self._assert_writable()
        normalized_id = _require_non_empty(desk_id, field_name="desk_id", exc_type=MapConstraintError)
        position = self._coerce_position(x=x, y=y, position=position)
        self._assert_in_bounds(position)
        current = self._desks.get(normalized_id)
        if current is None:
            raise MapConstraintError(f"Unknown desk id: {normalized_id}")

        owner = self._occupied.get(position)
        if owner is not None and owner != normalized_id:
            raise MapConstraintError(
                f"Cell already occupied by desk '{owner}' at ({position.x}, {position.y})"
            )

        before = self._snapshot()
        if current != position:
            del self._occupied[current]
            self._occupied[position] = normalized_id
            self._desks[normalized_id] = position
            self._record_edit(before)

    def remove_desk(self, desk_id: str) -> None:
        self._assert_writable()
        normalized_id = _require_non_empty(desk_id, field_name="desk_id", exc_type=MapConstraintError)
        current = self._desks.get(normalized_id)
        if current is None:
            raise MapConstraintError(f"Unknown desk id: {normalized_id}")

        before = self._snapshot()
        del self._desks[normalized_id]
        del self._occupied[current]
        self._record_edit(before)

    def position_for_desk(self, desk_id: str) -> GridPosition | None:
        return self._desks.get(desk_id)

    def snapshot(self) -> dict[str, tuple[int, int]]:
        return {
            desk_id: (position.x, position.y)
            for desk_id, position in sorted(self._desks.items(), key=lambda item: item[0])
        }

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        edit = self._undo_stack.pop()
        self._restore(edit.before)
        self._redo_stack.append(edit)
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        edit = self._redo_stack.pop()
        self._restore(edit.after)
        self._undo_stack.append(edit)
        return True

    def persistence_state(self) -> dict[str, Any]:
        return {
            "width": self._width,
            "height": self._height,
            "read_only": self._read_only,
            "desks": [
                {"desk_id": desk_id, "x": position.x, "y": position.y}
                for desk_id, position in sorted(self._desks.items(), key=lambda item: item[0])
            ],
        }

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        width = _require_positive_int(payload.get("width", self._width), field_name="width", exc_type=MapConstraintError)
        height = _require_positive_int(
            payload.get("height", self._height),
            field_name="height",
            exc_type=MapConstraintError,
        )
        if width != self._width or height != self._height:
            raise MapConstraintError("Map dimensions mismatch between editor and persistence payload")

        desks_payload = payload.get("desks", [])
        if not isinstance(desks_payload, list):
            raise MapConstraintError("'desks' must be a list")

        restored: dict[str, GridPosition] = {}
        occupied: dict[GridPosition, str] = {}
        for item in desks_payload:
            if not isinstance(item, Mapping):
                raise MapConstraintError("Each persisted desk entry must be a mapping")
            desk_id = item.get("desk_id")
            if not isinstance(desk_id, str):
                raise MapConstraintError("Persisted desk_id must be a string")
            position = GridPosition(
                x=_require_non_negative_int(item.get("x"), field_name="x", exc_type=MapConstraintError),
                y=_require_non_negative_int(item.get("y"), field_name="y", exc_type=MapConstraintError),
            )
            self._assert_in_bounds(position)
            owner = occupied.get(position)
            if owner is not None and owner != desk_id:
                raise MapConstraintError("Two desks cannot share the same persisted cell")
            if desk_id in restored:
                raise MapConstraintError(f"Duplicate persisted desk id: {desk_id}")
            restored[desk_id] = position
            occupied[position] = desk_id

        self._desks = restored
        self._occupied = occupied
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._read_only = bool(payload.get("read_only", self._read_only))

    def _assert_writable(self) -> None:
        if self._read_only:
            raise MapReadOnlyError("Map editor is read-only")

    def _assert_in_bounds(self, position: GridPosition) -> None:
        if position.x >= self._width or position.y >= self._height:
            raise MapConstraintError(
                f"Position ({position.x}, {position.y}) outside grid bounds {self._width}x{self._height}"
            )

    @staticmethod
    def _coerce_position(
        *,
        x: int | None,
        y: int | None,
        position: GridPosition | None,
    ) -> GridPosition:
        if position is not None:
            return position
        if x is None or y is None:
            raise MapConstraintError("Position requires either a GridPosition or both x and y")
        return GridPosition(x=x, y=y)

    def _snapshot(self) -> tuple[tuple[str, GridPosition], ...]:
        return tuple(sorted(self._desks.items(), key=lambda item: item[0]))

    def _restore(self, snapshot: tuple[tuple[str, GridPosition], ...]) -> None:
        self._desks = dict(snapshot)
        self._occupied = {position: desk_id for desk_id, position in snapshot}

    def _record_edit(self, before: tuple[tuple[str, GridPosition], ...]) -> None:
        after = self._snapshot()
        self._undo_stack.append(_MapEdit(before=before, after=after))
        self._redo_stack.clear()


class DeskDirectoryMap:
    """One-to-one desk<->directory map with JSON persistence."""

    def __init__(self, entries: Mapping[str, str] | None = None) -> None:
        self._desk_to_directory: dict[str, Path] = {}
        self._directory_to_desk: dict[Path, str] = {}
        for desk_id, directory in (entries or {}).items():
            self.bind(desk_id, directory)

    def bind(self, desk_id: str, directory: Path | str) -> None:
        normalized_id = _require_non_empty(desk_id, field_name="desk_id", exc_type=DeskDirectoryMapError)
        normalized_directory = Path(directory).expanduser().resolve(strict=False)

        existing_directory = self._desk_to_directory.get(normalized_id)
        if existing_directory is not None and existing_directory != normalized_directory:
            raise DeskDirectoryMapError(
                f"Desk '{normalized_id}' is already mapped to '{existing_directory}'"
            )

        existing_desk = self._directory_to_desk.get(normalized_directory)
        if existing_desk is not None and existing_desk != normalized_id:
            raise DeskDirectoryMapError(
                f"Directory '{normalized_directory}' is already mapped to desk '{existing_desk}'"
            )

        self._desk_to_directory[normalized_id] = normalized_directory
        self._directory_to_desk[normalized_directory] = normalized_id

    def unbind(self, desk_id: str) -> None:
        normalized_id = _require_non_empty(desk_id, field_name="desk_id", exc_type=DeskDirectoryMapError)
        directory = self._desk_to_directory.pop(normalized_id, None)
        if directory is None:
            raise DeskDirectoryMapError(f"Unknown desk id: {normalized_id}")
        self._directory_to_desk.pop(directory, None)

    def directory_for_desk(self, desk_id: str) -> Path | None:
        return self._desk_to_directory.get(desk_id)

    def desk_for_directory(self, directory: Path | str) -> str | None:
        return self._directory_to_desk.get(Path(directory).expanduser().resolve(strict=False))

    def snapshot(self) -> dict[str, str]:
        return {
            desk_id: str(self._desk_to_directory[desk_id])
            for desk_id in sorted(self._desk_to_directory)
        }

    def persist(self, *, storage_path: Path) -> None:
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(
            json.dumps(self.snapshot(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, *, storage_path: Path) -> DeskDirectoryMap:
        if not storage_path.is_file():
            raise DeskDirectoryMapError(f"Desk directory map file not found: {storage_path}")
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise DeskDirectoryMapError("Invalid desk directory map payload")
        return cls(entries=payload)


class RoomStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSED = "closed"


ROOM_TRANSITIONS: Mapping[RoomStatus, frozenset[RoomStatus]] = MappingProxyType({
    RoomStatus.CREATED: frozenset({RoomStatus.ACTIVE}),
    RoomStatus.ACTIVE: frozenset({RoomStatus.ARCHIVED}),
    RoomStatus.ARCHIVED: frozenset({RoomStatus.CLOSED}),
    RoomStatus.CLOSED: frozenset(),
})


@dataclass(frozen=True, slots=True)
class WorktreeRoom:
    room_id: str
    directory: Path
    status: RoomStatus = RoomStatus.CREATED

    def __post_init__(self) -> None:
        _require_non_empty(self.room_id, field_name="room_id", exc_type=WorktreeRoomLifecycleError)


class WorktreeRoomLifecycle:
    """Strict room lifecycle manager: created -> active -> archived -> closed."""

    def __init__(self, rooms: Iterable[WorktreeRoom] | None = None) -> None:
        self._rooms: dict[str, WorktreeRoom] = {}
        for room in rooms or ():
            self._rooms[room.room_id] = room

    def create_room(self, room_id: str, directory: Path | str) -> WorktreeRoom:
        normalized_id = _require_non_empty(room_id, field_name="room_id", exc_type=WorktreeRoomLifecycleError)
        if normalized_id in self._rooms:
            raise WorktreeRoomLifecycleError(f"Room already exists: {normalized_id}")
        room = WorktreeRoom(
            room_id=normalized_id,
            directory=Path(directory).expanduser().resolve(strict=False),
            status=RoomStatus.CREATED,
        )
        self._rooms[normalized_id] = room
        return room

    def activate_room(self, room_id: str) -> WorktreeRoom:
        return self._transition(room_id=room_id, target=RoomStatus.ACTIVE)

    def archive_room(self, room_id: str) -> WorktreeRoom:
        return self._transition(room_id=room_id, target=RoomStatus.ARCHIVED)

    def close_room(self, room_id: str) -> WorktreeRoom:
        return self._transition(room_id=room_id, target=RoomStatus.CLOSED)

    def room(self, room_id: str) -> WorktreeRoom:
        normalized_id = _require_non_empty(room_id, field_name="room_id", exc_type=WorktreeRoomLifecycleError)
        room = self._rooms.get(normalized_id)
        if room is None:
            raise WorktreeRoomLifecycleError(f"Unknown room id: {normalized_id}")
        return room

    def rooms(self) -> tuple[WorktreeRoom, ...]:
        return tuple(self._rooms[key] for key in sorted(self._rooms))

    def persistence_state(self) -> dict[str, Any]:
        return {
            "rooms": [
                {
                    "room_id": room.room_id,
                    "directory": str(room.directory),
                    "status": room.status.value,
                }
                for room in self.rooms()
            ],
        }

    def load_persistence_state(self, payload: Mapping[str, Any]) -> None:
        rooms_payload = payload.get("rooms", [])
        if not isinstance(rooms_payload, list):
            raise WorktreeRoomLifecycleError("'rooms' must be a list")

        restored: dict[str, WorktreeRoom] = {}
        for item in rooms_payload:
            if not isinstance(item, Mapping):
                raise WorktreeRoomLifecycleError("Each room payload entry must be a mapping")
            room_id = item.get("room_id")
            directory = item.get("directory")
            status = item.get("status", RoomStatus.CREATED.value)
            if not isinstance(room_id, str) or not isinstance(directory, str) or not isinstance(status, str):
                raise WorktreeRoomLifecycleError(
                    "room_id, directory, and status must all be strings"
                )
            try:
                room_status = RoomStatus(status)
            except ValueError as exc:
                raise WorktreeRoomLifecycleError(f"Unknown room status: {status}") from exc

            room = WorktreeRoom(
                room_id=room_id,
                directory=Path(directory).expanduser().resolve(strict=False),
                status=room_status,
            )
            if room.room_id in restored:
                raise WorktreeRoomLifecycleError(f"Duplicate room id in persistence payload: {room.room_id}")
            restored[room.room_id] = room

        self._rooms = restored

    def _transition(self, *, room_id: str, target: RoomStatus) -> WorktreeRoom:
        room = self.room(room_id)
        allowed_targets = ROOM_TRANSITIONS[room.status]
        if target not in allowed_targets:
            raise RoomTransitionError(
                f"Invalid room transition from '{room.status.value}' to '{target.value}'"
            )

        updated = replace(room, status=target)
        self._rooms[updated.room_id] = updated
        return updated