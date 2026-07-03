import type { GameState } from '../../src/state/game-state';
import {
  createAgentFactoryView,
  evaluateAgentFactoryMutationGate
} from '../../src/state/agent-factory-view';

function createAgentFactoryState(restartConfirmed: boolean): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 15,
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
      'agent-source': {
        id: 'agent-source',
        name: 'Source',
        role: 'agent',
        status: 'working',
        roomId: 'forge-room',
        position: { x: 6, y: 6 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      },
      'agent-nova': {
        id: 'agent-nova',
        name: 'Nova',
        role: 'agent',
        status: 'idle',
        roomId: 'forge-room',
        position: { x: 8, y: 6 },
        parentId: 'orch-1',
        lastTool: 'semantic_search'
      },
      'agent-source-clone': {
        id: 'agent-source-clone',
        name: 'Source Clone',
        role: 'agent',
        status: 'paused',
        roomId: 'forge-room',
        position: { x: 10, y: 6 },
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-agent-factory': {
        id: 'task-agent-factory',
        title: 'Complete agent factory flows',
        status: 'review',
        assigneeId: 'orch-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Agent created',
        detail: 'Nova was created from the in-world agent factory.',
        sourceEventType: 'decision',
        traceId: 'agent-factory-001',
        taskId: 'task-agent-factory',
        metadata: {
          agentFactoryAction: 'create',
          targetAgentId: 'agent-nova',
          agentName: 'Nova',
          agentRole: 'agent',
          model: 'gpt-5.4',
          promptRef: 'prompt://nova/default',
          toolIds: ['runTests', 'semantic_search'],
          roomId: 'forge-room'
        },
        sequenceId: 12,
        timestamp: '2026-04-08T00:00:12.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Agent deployed',
        detail: 'Nova appeared immediately in the forge room.',
        sourceEventType: 'decision',
        traceId: 'agent-factory-001',
        taskId: 'task-agent-factory',
        metadata: {
          agentFactoryAction: 'deploy',
          targetAgentId: 'agent-nova',
          roomId: 'forge-room'
        },
        sequenceId: 13,
        timestamp: '2026-04-08T00:00:13.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Agent cloned',
        detail: 'Source was cloned without XP or runtime history inheritance.',
        sourceEventType: 'decision',
        traceId: 'agent-factory-002',
        taskId: 'task-agent-factory',
        metadata: {
          agentFactoryAction: 'clone',
          targetAgentId: 'agent-source-clone',
          agentName: 'Source Clone',
          agentRole: 'agent',
          sourceAgentId: 'agent-source',
          model: 'gpt-5.4',
          promptRef: 'prompt://source/default',
          toolIds: ['runTests', 'create_file'],
          roomId: 'forge-room',
          sourceXp: 240,
          clonedXp: 0,
          sourceHistoryCount: 2,
          clonedHistoryCount: 0
        },
        sequenceId: 14,
        timestamp: '2026-04-08T00:00:14.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Sensitive agent mutation requested',
        detail: 'The deployed clone requested a model switch requiring restart.',
        sourceEventType: 'decision',
        traceId: 'agent-factory-003',
        taskId: 'task-agent-factory',
        metadata: {
          agentFactoryAction: 'configure',
          targetAgentId: 'agent-source-clone',
          model: 'gpt-5.4-mini',
          promptRef: 'prompt://source/optimized',
          toolIds: ['runTests', 'create_file', 'semantic_search'],
          restartRequired: true,
          restartConfirmed
        },
        sequenceId: 15,
        timestamp: '2026-04-08T00:00:15.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

function createRejectedAgentCreateState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 11,
    hydratedAt: '2026-04-08T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    },
    tasks: {
      'task-agent-factory': {
        id: 'task-agent-factory',
        title: 'Complete agent factory flows',
        status: 'review',
        assigneeId: 'orch-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Invalid create rejected',
        detail: 'The factory rejected an invalid create payload with an actionable error.',
        sourceEventType: 'error',
        traceId: 'agent-factory-004',
        taskId: 'task-agent-factory',
        metadata: {
          agentFactoryAction: 'create',
          targetAgentId: 'agent-invalid',
          roomId: 'forge-room',
          rejected: true,
          validationError: 'agentName must be a non-empty string'
        },
        sequenceId: 11,
        timestamp: '2026-04-08T00:00:11.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

describe('agent factory view', () => {
  it('tracks create, clone and deploy flows with immediate room appearance', () => {
    const view = createAgentFactoryView(createAgentFactoryState(true));

    expect(view.summary).toEqual({
      agentCount: 2,
      operationCount: 4,
      createdCount: 1,
      clonedCount: 1,
      deployedCount: 1,
      rejectedCreateCount: 0,
      blockedMutationCount: 0
    });
    expect(view.agents).toMatchObject([
      {
        agentId: 'agent-nova',
        name: 'Nova',
        roomId: 'forge-room',
        isClone: false,
        pendingIssues: []
      },
      {
        agentId: 'agent-source-clone',
        name: 'Source Clone',
        sourceAgentId: 'agent-source',
        isClone: true,
        hasResetProgress: true,
        roomId: 'forge-room',
        pendingIssues: []
      }
    ]);
  });

  it('records rejected invalid create attempts as actionable audit evidence', () => {
    const view = createAgentFactoryView(createRejectedAgentCreateState());

    expect(view.summary.rejectedCreateCount).toBe(1);
    expect(view.operations).toMatchObject([
      {
        action: 'create',
        targetAgentId: 'agent-invalid',
        rejected: true,
        validationError: 'agentName must be a non-empty string',
        issues: []
      }
    ]);
  });

  it('blocks sensitive post-deploy mutations until restart is confirmed', () => {
    const gate = evaluateAgentFactoryMutationGate(createAgentFactoryState(false), 'agent-source-clone');

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReady).toBe(false);
    expect(gate?.issueCodes).toEqual(['AGENT_FACTORY_RESTART_CONFIRMATION_REQUIRED']);
  });
});