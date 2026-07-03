import {
  LEASE_STORE_VERSION,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence
} from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import {
  authorizeDeepInspectionAction,
  createDeepInspectionActionAuditEntry,
  createDeepInspectionView
} from '../../src/state/deep-inspection-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

const ORCHESTRATOR: AgentPresence = {
  id: 'orch-1',
  name: 'Orchestrator',
  role: 'orchestrator',
  status: 'idle',
  roomId: 'war-room',
  position: { x: 4, y: 4 }
};

const DEV_AGENT: AgentPresence = {
  id: 'dev-1',
  name: 'Amelia',
  role: 'agent',
  status: 'working',
  roomId: 'build-room',
  position: { x: 8, y: 8 },
  parentId: 'orch-1',
  lastTool: 'runTests'
};

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 48,
    hydratedAt: '2026-04-11T10:00:00.000Z',
    agents: {
      'orch-1': ORCHESTRATOR,
      'dev-1': DEV_AGENT,
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'paused',
        roomId: 'qa-room',
        position: { x: 10, y: 8 },
        parentId: 'orch-1',
        lastTool: 'runTests'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    },
    config: {
      agentProfiles: {
        'dev-1': {
          model: 'gpt-5.4',
          systemPrompt: 'You are Amelia and stay read-only on inspection.',
          tokens: {
            budget: 8_000,
            used: 2_300
          }
        }
      }
    },
    recentToolCalls: [
      {
        tool: 'semantic_search',
        params: {
          query: 'auth strategy',
          task_id: 'task-auth'
        },
        sourceEventType: 'graph_update',
        traceId: 'session-001',
        sequenceId: 40,
        timestamp: '2026-04-11T10:00:40.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'create_file',
        params: {
          path: 'src/auth.ts',
          task_id: 'task-auth'
        },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 41,
        timestamp: '2026-04-11T10:00:41.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'runTests',
        params: {
          files: ['tests/auth.test.ts'],
          task_id: 'task-auth'
        },
        sourceEventType: 'test_run',
        traceId: 'session-001',
        sequenceId: 43,
        timestamp: '2026-04-11T10:00:43.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { intent: 'Implement auth' },
        sequenceId: 39,
        timestamp: '2026-04-11T10:00:39.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth'
        },
        sequenceId: 42,
        timestamp: '2026-04-11T10:00:42.000Z',
        agentId: 'dev-1'
      }
    ],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

describe('deep inspection view', () => {
  it('assembles a complete inspection panel from runtime state, config and lease ownership', () => {
    const state = createBaseState();
    const dashboard = createRuntimeDashboardView(state, {}, {
      projectRegistry: null,
      nodeRegistry: null,
      leaseStore: {
        registryVersion: LEASE_STORE_VERSION,
        generatedAt: '2026-04-11T10:00:48.000Z',
        projectId: 'grimoire-game',
        runId: 'run-42',
        leases: [
          {
            protocolVersion: RUNTIME_PROTOCOL_VERSION,
            projectId: 'grimoire-game',
            runId: 'run-42',
            leaseId: 'lease-auth',
            taskId: 'task-auth',
            nodeId: 'node-alpha',
            workerId: 'worker-dev-1',
            worktreeId: 'wt-auth',
            branch: 'feature/auth',
            claimedAt: '2026-04-11T10:00:00.000Z',
            lastRenewedAt: '2026-04-11T10:00:30.000Z',
            expiresAt: '2026-04-11T10:01:00.000Z',
            ttlMs: 30_000,
            ageMs: 18_000,
            status: 'active',
            messageCount: 3,
            lastSequenceId: 48,
            channels: ['runtime'],
            messageTypes: ['task.update']
          }
        ],
        summary: {
          leaseCount: 1,
          activeLeaseCount: 1,
          expiredLeaseCount: 0
        }
      }
    });
    const actor = {
      principalId: 'orch-1',
      role: 'orchestrator' as const
    };
    const request = {
      action: 'restart' as const,
      targetAgentId: 'dev-1',
      taskId: 'task-auth',
      traceId: 'session-001',
      detail: 'Restart requested after inspection.'
    };
    const decision = authorizeDeepInspectionAction(actor, request);
    const auditEntry = createDeepInspectionActionAuditEntry(actor, request, decision, '2026-04-11T10:00:49.000Z');

    const view = createDeepInspectionView(state, 'dev-1', {
      actor,
      dashboard,
      auditTrail: [auditEntry]
    });

    expect(view).not.toBeNull();
    expect(view?.profile).toMatchObject({
      model: 'gpt-5.4',
      branch: 'feature/auth',
      systemPrompt: 'You are Amelia and stay read-only on inspection.',
      activeTool: 'runTests'
    });
    expect(view?.profile.tokenUsage).toMatchObject({
      budget: 8_000,
      used: 2_300,
      remaining: 5_700,
      source: 'config'
    });
    expect(view?.sessionSummary).toMatchObject({
      toolCallCount: 3,
      uniqueFileCount: 2,
      testRunCount: 1,
      workflowStepCount: 1,
      decisionCardCount: 2,
      traceCount: 1
    });
    expect(view?.toolHistory[0]).toMatchObject({
      tool: 'runTests',
      testRelated: true,
      fileRefs: ['tests/auth.test.ts']
    });
    expect(view?.toolHistory[1]).toMatchObject({
      tool: 'create_file',
      fileRefs: ['src/auth.ts']
    });
    expect(view?.actions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ kind: 'pause', allowed: true }),
        expect.objectContaining({ kind: 'chat_direct', allowed: true }),
        expect.objectContaining({ kind: 'redirect', allowed: true }),
        expect.objectContaining({ kind: 'restart', allowed: true })
      ])
    );
    expect(view?.auditTrail[0]).toMatchObject({
      action: 'restart',
      allowed: true,
      actorRole: 'orchestrator',
      targetAgentId: 'dev-1',
      traceId: 'session-001'
    });
  });

  it('surfaces verification proof references and latest gate correlation for the inspected agent', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Verification gate PASS',
        detail: 'task.transition.done: verify://task-auth/inspection',
        sourceEventType: 'verification_gate',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          verificationRef: 'verify://task-auth/inspection',
          verdict: 'PASS',
          correlationId: 'req-inspection-proof-1'
        },
        sequenceId: 44,
        timestamp: '2026-04-11T10:00:44.000Z',
        agentId: 'dev-1'
      }
    ];
    state.lastSequenceId = 49;

    const view = createDeepInspectionView(state, 'dev-1');

    expect(view).not.toBeNull();
    expect(view).toMatchObject({
      verificationProofRefs: ['verify://task-auth/inspection'],
      latestVerificationVerdict: 'PASS',
      latestVerificationCorrelationId: 'req-inspection-proof-1'
    });
  });

  it('enforces role-based action guards and produces auditable decisions', () => {
    const spectator = {
      principalId: 'spec-1',
      role: 'spectator' as const
    };
    const agent = {
      principalId: 'qa-1',
      role: 'agent' as const
    };
    const selfAgent = {
      principalId: 'dev-1',
      role: 'agent' as const
    };

    const spectatorDecision = authorizeDeepInspectionAction(spectator, {
      action: 'pause',
      targetAgentId: 'dev-1'
    });
    const agentChatDecision = authorizeDeepInspectionAction(agent, {
      action: 'chat_direct',
      targetAgentId: 'dev-1',
      taskId: 'task-auth',
      traceId: 'session-001',
      detail: 'Need a live status update.'
    });
    const agentRestartDecision = authorizeDeepInspectionAction(agent, {
      action: 'restart',
      targetAgentId: 'dev-1'
    });
    const selfPauseDecision = authorizeDeepInspectionAction(selfAgent, {
      action: 'pause',
      targetAgentId: 'dev-1'
    });
    const auditEntry = createDeepInspectionActionAuditEntry(
      agent,
      {
        action: 'chat_direct',
        targetAgentId: 'dev-1',
        taskId: 'task-auth',
        traceId: 'session-001',
        detail: 'Need a live status update.'
      },
      agentChatDecision,
      '2026-04-11T10:00:50.000Z'
    );

    expect(spectatorDecision).toMatchObject({
      allowed: false,
      requiredRole: 'agent'
    });
    expect(spectatorDecision.reason).toContain('read-only');
    expect(agentChatDecision).toMatchObject({
      allowed: true,
      requiredRole: 'agent'
    });
    expect(agentRestartDecision).toMatchObject({
      allowed: false,
      requiredRole: 'orchestrator'
    });
    expect(selfPauseDecision).toMatchObject({
      allowed: true,
      requiredRole: 'agent'
    });
    expect(auditEntry).toMatchObject({
      actorId: 'qa-1',
      actorRole: 'agent',
      action: 'chat_direct',
      allowed: true,
      targetAgentId: 'dev-1',
      taskId: 'task-auth'
    });
  });
});