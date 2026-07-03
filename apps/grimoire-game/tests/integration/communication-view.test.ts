import type { GameState } from '../../src/state/game-state';
import { createCommunicationView } from '../../src/state/communication-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 22,
    hydratedAt: '2026-04-11T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'working',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      },
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 },
        parentId: 'orch-1'
      },
      'review-1': {
        id: 'review-1',
        name: 'Paige',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 9, y: 8 },
        parentId: 'orch-1'
      },
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'working',
        roomId: 'qa-room',
        position: { x: 10, y: 8 },
        parentId: 'orch-1'
      }
    },
    tasks: {
      'task-local': {
        id: 'task-local',
        title: 'Peer review auth slice',
        status: 'review',
        assigneeId: 'review-1'
      },
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'qa-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Prepare peer review',
        detail: 'Need a local pass on the auth slice.',
        sourceEventType: 'routing',
        traceId: 'trace-local',
        taskId: 'task-local',
        metadata: {
          intent: 'Peer review auth slice'
        },
        sequenceId: 10,
        timestamp: '2026-04-11T00:00:10.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Peer review started',
        detail: 'Peer review picked up in the same room.',
        sourceEventType: 'decision',
        traceId: 'trace-local',
        taskId: 'task-local',
        metadata: {
          topic: 'auth'
        },
        sequenceId: 11,
        timestamp: '2026-04-11T00:00:11.000Z',
        agentId: 'review-1'
      },
      {
        step: 'Security review broadcast',
        detail: 'Need build-room and qa-room review on the auth flow.',
        sourceEventType: 'security_finding',
        traceId: 'trace-security',
        taskId: 'task-auth',
        metadata: {
          title: 'Security review broadcast',
          severity: 'high',
          status: 'open',
          confidenceScore: 9.2,
          exploitScenario: 'Cross-room review must confirm runtime guardrails.',
          surfaceId: 'auth-runtime'
        },
        sequenceId: 20,
        timestamp: '2026-04-11T00:00:20.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Build room picks up auth hardening',
        detail: 'Build room starts the hardening pass.',
        sourceEventType: 'decision',
        traceId: 'trace-security',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth-hardening'
        },
        sequenceId: 21,
        timestamp: '2026-04-11T00:00:21.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'QA room validates auth hardening',
        detail: 'QA room continues verification on the same trace.',
        sourceEventType: 'decision',
        traceId: 'trace-security',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth-verification'
        },
        sequenceId: 22,
        timestamp: '2026-04-11T00:00:22.000Z',
        agentId: 'qa-1'
      }
    ],
    lastErrors: []
  };
}

describe('createCommunicationView', () => {
  it('projects local messages, inter-room handoffs and broadcasts on one canonical timeline', () => {
    const view = createCommunicationView(createBaseState());

    expect(view.metrics).toEqual({
      totalBubbleCount: 5,
      filteredBubbleCount: 5,
      messageCount: 1,
      handoffCount: 3,
      broadcastCount: 1,
      interRoomCount: 3,
      criticalCount: 1,
      threadCount: 2
    });

    const localMessage = view.bubbles.find((bubble) => bubble.sequenceId === 10);
    expect(localMessage).toMatchObject({
      kind: 'message',
      messageType: 'workflow.step',
      traceId: 'trace-local',
      scope: 'intra_room',
      source: {
        agentId: 'dev-1',
        roomId: 'build-room',
        teamId: 'build-room'
      },
      targets: [
        expect.objectContaining({
          agentId: 'review-1',
          roomId: 'build-room'
        })
      ]
    });
    expect(view.timeline.find((bubble) => bubble.sequenceId === 10)?.id).toBe(localMessage?.id);

    const interRoomHandoff = view.bubbles.find((bubble) => bubble.sequenceId === 22);
    expect(interRoomHandoff).toMatchObject({
      kind: 'handoff',
      messageType: 'task.handoff',
      traceId: 'trace-security',
      scope: 'inter_room',
      source: {
        agentId: 'dev-1',
        roomId: 'build-room'
      },
      targets: [
        expect.objectContaining({
          agentId: 'qa-1',
          roomId: 'qa-room'
        })
      ]
    });

    const broadcast = view.bubbles.find((bubble) => bubble.sequenceId === 20);
    expect(broadcast).toMatchObject({
      kind: 'broadcast',
      traceId: 'trace-security',
      criticality: 'critical',
      scope: 'multi_room',
      source: {
        agentId: 'orch-1',
        roomId: 'war-room'
      }
    });
    expect(broadcast?.targets.map((target) => target.agentId)).toEqual(['dev-1', 'qa-1']);
    expect(view.links).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          bubbleId: broadcast?.id,
          sourceAgentId: 'orch-1',
          targetAgentId: 'qa-1',
          scope: 'multi_room'
        }),
        expect.objectContaining({
          bubbleId: interRoomHandoff?.id,
          sourceAgentId: 'dev-1',
          targetAgentId: 'qa-1',
          scope: 'inter_room'
        })
      ])
    );
  });

  it('keeps critical communications filterable by agent, team and trace', () => {
    const view = createCommunicationView(createBaseState(), {
      agentId: 'orch-1',
      teamId: 'qa-room',
      traceId: 'trace-security',
      criticalities: ['critical']
    });

    expect(view.hasActiveFilters).toBe(true);
    expect(view.bubbles).toHaveLength(1);
    expect(view.bubbles[0]).toMatchObject({
      kind: 'broadcast',
      traceId: 'trace-security',
      criticality: 'critical',
      source: {
        agentId: 'orch-1'
      },
      targets: [
        expect.objectContaining({ agentId: 'dev-1' }),
        expect.objectContaining({ agentId: 'qa-1' })
      ]
    });
    expect(view.timeline.map((bubble) => bubble.sequenceId)).toEqual([20]);
    expect(view.threads).toEqual([
      expect.objectContaining({
        traceId: 'trace-security',
        criticalCount: 1,
        bubbleIds: [view.bubbles[0]?.id]
      })
    ]);
  });
});