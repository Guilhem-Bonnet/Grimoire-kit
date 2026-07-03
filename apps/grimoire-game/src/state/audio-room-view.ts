import type { GameState, WorkflowStepLogEntry } from './game-state';

export const AUDIO_CHANNEL_ORDER = ['effects', 'music', 'voice'] as const;

export type AudioChannel = (typeof AUDIO_CHANNEL_ORDER)[number];

export interface AudioSettingsSnapshot {
  masterMute: boolean;
  masterVolume: number;
  musicEnabled: boolean;
  effectsEnabled: boolean;
  voiceEnabled: boolean;
  spectatorMuteStrict: boolean;
}

export interface AudioEventRecord {
  name: string;
  channel: AudioChannel;
  source: 'workflow' | 'persisted';
  sequenceId: number | null;
  timestamp: string | null;
  taskId: string | null;
}

export interface AudioRoomTheme {
  roomId: string;
  track: string;
}

export interface AudioRoomPersistenceState {
  settings: AudioSettingsSnapshot;
  pendingEvents: readonly Pick<AudioEventRecord, 'name' | 'channel'>[];
}

export interface AudioRoomSummary {
  pendingEventCount: number;
  dedupedEventCount: number;
  mutedEventCount: number;
  roomThemeCount: number;
  strictMuteActive: boolean;
}

export interface AudioRoomView {
  settings: AudioSettingsSnapshot;
  pendingEvents: readonly AudioEventRecord[];
  roomThemes: readonly AudioRoomTheme[];
  persistenceState: AudioRoomPersistenceState;
  summary: AudioRoomSummary;
}

export function createAudioRoomView(state: GameState): AudioRoomView {
  const settings = createAudioSettingsSnapshot(state);
  const roomThemes = createRoomThemes(state);
  const strictMuteActive =
    settings.masterMute || (settings.spectatorMuteStrict && Object.values(state.agents).some((agent) => agent.role === 'spectator'));
  const persistedEvents = readPersistedEvents(state);
  const workflowEvents = collectWorkflowAudioEvents(state.recentWorkflowSteps);
  const allEvents = [...persistedEvents, ...workflowEvents];
  const seenKeys = new Set<string>();
  const pendingEvents: AudioEventRecord[] = [];
  let dedupedEventCount = 0;
  let mutedEventCount = 0;

  for (const event of allEvents) {
    if (strictMuteActive || !isChannelEnabled(settings, event.channel)) {
      mutedEventCount += 1;
      continue;
    }

    const eventKey = `${event.channel}:${event.name}`;
    if (seenKeys.has(eventKey)) {
      dedupedEventCount += 1;
      continue;
    }

    seenKeys.add(eventKey);
    pendingEvents.push(event);
  }

  return {
    settings,
    pendingEvents,
    roomThemes,
    persistenceState: {
      settings,
      pendingEvents: pendingEvents.map((event) => ({
        name: event.name,
        channel: event.channel
      }))
    },
    summary: {
      pendingEventCount: pendingEvents.length,
      dedupedEventCount,
      mutedEventCount,
      roomThemeCount: roomThemes.length,
      strictMuteActive
    }
  };
}

function createAudioSettingsSnapshot(state: GameState): AudioSettingsSnapshot {
  return {
    masterMute: readConfigBoolean(state.config, 'audio.masterMute', false),
    masterVolume: clampVolume(readConfigNumber(state.config, 'audio.masterVolume', 1)),
    musicEnabled: readConfigBoolean(state.config, 'audio.musicEnabled', true),
    effectsEnabled: readConfigBoolean(state.config, 'audio.effectsEnabled', true),
    voiceEnabled: readConfigBoolean(state.config, 'audio.voiceEnabled', true),
    spectatorMuteStrict: readConfigBoolean(state.config, 'audio.spectatorMuteStrict', false)
  };
}

function createRoomThemes(state: GameState): AudioRoomTheme[] {
  const roomThemes = state.config['audio.roomThemes'];
  if (roomThemes === undefined || roomThemes === null || Array.isArray(roomThemes) || typeof roomThemes !== 'object') {
    return [];
  }

  return Object.entries(roomThemes)
    .filter((entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].trim().length > 0)
    .map(([roomId, track]) => ({ roomId, track: track.trim() }))
    .sort((left, right) => left.roomId.localeCompare(right.roomId));
}

function readPersistedEvents(state: GameState): AudioEventRecord[] {
  const pendingEvents = state.config['audio.pendingEvents'];
  if (!Array.isArray(pendingEvents)) {
    return [];
  }

  return pendingEvents.flatMap((entry) => {
    if (entry === null || Array.isArray(entry) || typeof entry !== 'object') {
      return [];
    }

    const name = readRecordString(entry, ['name']);
    const channel = readAudioChannel(entry, ['channel']);
    if (name === null || channel === null) {
      return [];
    }

    return [
      {
        name,
        channel,
        source: 'persisted' as const,
        sequenceId: null,
        timestamp: null,
        taskId: null
      }
    ];
  });
}

function collectWorkflowAudioEvents(workflowSteps: readonly WorkflowStepLogEntry[]): AudioEventRecord[] {
  return [...workflowSteps]
    .sort((left, right) => left.sequenceId - right.sequenceId)
    .flatMap((workflowStep) => {
      const explicitEvent = readRecordString(workflowStep.metadata, ['audioEvent', 'audio_event']);
      const explicitChannel = readAudioChannel(workflowStep.metadata, ['audioChannel', 'audio_channel']);
      const derived = explicitEvent === null ? deriveAudioEventFromWorkflowStep(workflowStep) : { name: explicitEvent, channel: explicitChannel ?? 'effects' };
      if (derived === null) {
        return [];
      }

      return [
        {
          name: derived.name,
          channel: derived.channel,
          source: 'workflow' as const,
          sequenceId: workflowStep.sequenceId,
          timestamp: workflowStep.timestamp,
          taskId: workflowStep.taskId ?? null
        }
      ];
    });
}

function deriveAudioEventFromWorkflowStep(
  workflowStep: WorkflowStepLogEntry
): Pick<AudioEventRecord, 'name' | 'channel'> | null {
  const actionId = readRecordString(workflowStep.metadata, ['actionId', 'action_id']);
  if (actionId === 'task.transition.done') {
    return { name: 'task_done', channel: 'effects' };
  }

  if (workflowStep.sourceEventType === 'error') {
    return { name: 'error_bip', channel: 'effects' };
  }

  if (workflowStep.sourceEventType.startsWith('challenge_')) {
    return { name: 'challenge_ping', channel: 'effects' };
  }

  if (workflowStep.sourceEventType === 'message' || readRecordString(workflowStep.metadata, ['messageId', 'message_id']) !== null) {
    return { name: 'message_received', channel: 'voice' };
  }

  const roomTrack = readRecordString(workflowStep.metadata, ['roomTheme', 'room_theme']);
  if (roomTrack !== null) {
    return { name: roomTrack, channel: 'music' };
  }

  return null;
}

function isChannelEnabled(settings: AudioSettingsSnapshot, channel: AudioChannel): boolean {
  if (channel === 'music') {
    return settings.musicEnabled;
  }

  if (channel === 'voice') {
    return settings.voiceEnabled;
  }

  return settings.effectsEnabled;
}

function readConfigBoolean(config: GameState['config'], key: string, defaultValue: boolean): boolean {
  const value = config[key];
  return typeof value === 'boolean' ? value : defaultValue;
}

function readConfigNumber(config: GameState['config'], key: string, defaultValue: number): number {
  const value = config[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : defaultValue;
}

function clampVolume(value: number): number {
  return Math.max(0, Math.min(1, Number(value.toFixed(2))));
}

function readRecordString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalized = value.trim();
    if (normalized.length > 0) {
      return normalized;
    }
  }

  return null;
}

function readAudioChannel(record: Record<string, unknown>, keys: readonly string[]): AudioChannel | null {
  const value = readRecordString(record, keys);
  if (value === null) {
    return null;
  }

  return AUDIO_CHANNEL_ORDER.includes(value as AudioChannel) ? (value as AudioChannel) : null;
}