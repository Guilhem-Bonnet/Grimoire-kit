import type { GameState } from '../../src/state/game-state';
import { createAudioRoomView } from '../../src/state/audio-room-view';

function createAudioState(config: GameState['config'] = {}, spectator = false): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 14,
    hydratedAt: '2026-04-08T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      ...(spectator
        ? {
            'spec-1': {
              id: 'spec-1',
              name: 'Spectator',
              role: 'spectator' as const,
              status: 'idle' as const,
              roomId: 'observatory',
              position: { x: 2, y: 2 }
            }
          }
        : {}),
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'forge-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      }
    },
    config,
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Done transition prepared',
        detail: 'Task is ready to close.',
        sourceEventType: 'decision',
        traceId: 'audio-001',
        taskId: 'task-auth',
        metadata: {
          actionId: 'task.transition.done'
        },
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Done transition duplicated',
        detail: 'Duplicate sound candidate.',
        sourceEventType: 'decision',
        traceId: 'audio-001',
        taskId: 'task-auth',
        metadata: {
          actionId: 'task.transition.done'
        },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Challenge question',
        detail: 'Challenge started.',
        sourceEventType: 'challenge_question',
        traceId: 'audio-001',
        taskId: 'task-auth',
        metadata: {},
        sequenceId: 13,
        timestamp: '2026-04-08T00:00:13.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Incoming message',
        detail: 'User sent a message.',
        sourceEventType: 'message',
        traceId: 'audio-001',
        taskId: 'task-auth',
        metadata: {
          messageId: 'msg-1'
        },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: []
  };
}

describe('audio room view', () => {
  it('publishes deduplicated critical runtime audio events', () => {
    const view = createAudioRoomView(createAudioState({
      'audio.roomThemes': {
        'forge-room': 'forge_theme'
      }
    }));

    expect(view.pendingEvents).toMatchObject([
      { name: 'task_done', channel: 'effects' },
      { name: 'challenge_ping', channel: 'effects' },
      { name: 'message_received', channel: 'voice' }
    ]);
    expect(view.summary).toEqual({
      pendingEventCount: 3,
      dedupedEventCount: 1,
      mutedEventCount: 0,
      roomThemeCount: 1,
      strictMuteActive: false
    });
  });

  it('honors independent toggles and strict spectator mute', () => {
    const view = createAudioRoomView(
      createAudioState(
        {
          'audio.effectsEnabled': false,
          'audio.musicEnabled': true,
          'audio.voiceEnabled': false,
          'audio.spectatorMuteStrict': true,
          'audio.pendingEvents': [
            { name: 'forge_theme', channel: 'music' }
          ]
        },
        true
      )
    );

    expect(view.pendingEvents).toEqual([]);
    expect(view.summary.strictMuteActive).toBe(true);
    expect(view.summary.mutedEventCount).toBe(5);
  });

  it('exports a persistence snapshot with reapplied settings and pending events', () => {
    const view = createAudioRoomView(
      createAudioState({
        'audio.masterVolume': 0.65,
        'audio.pendingEvents': [
          { name: 'task_done', channel: 'effects' },
          { name: 'forge_theme', channel: 'music' }
        ]
      })
    );

    expect(view.persistenceState).toEqual({
      settings: {
        masterMute: false,
        masterVolume: 0.65,
        musicEnabled: true,
        effectsEnabled: true,
        voiceEnabled: true,
        spectatorMuteStrict: false
      },
      pendingEvents: [
        { name: 'task_done', channel: 'effects' },
        { name: 'forge_theme', channel: 'music' },
        { name: 'challenge_ping', channel: 'effects' },
        { name: 'message_received', channel: 'voice' }
      ]
    });
  });
});