import type { GameState } from '../../src/state/game-state';
import { createHostHandoffView, queryHostHandoffView } from '../../src/state/host-handoff-view';
import { createRuntimeDashboardUiView } from '../../src/state/runtime-dashboard-ui-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 70,
    hydratedAt: '2026-04-11T12:00:00.000Z',
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
        parentId: 'orch-1',
        lastTool: 'create_file'
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Ship auth handoff',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-observability': {
        id: 'task-observability',
        title: 'Add observability docs',
        status: 'in_progress',
        assigneeId: 'orch-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Ship auth handoff',
        sourceEventType: 'routing',
        traceId: 'trace-auth',
        taskId: 'task-auth',
        metadata: {
          intent: 'Ship auth handoff',
          missionPack: {
            objective: 'Prepare a governed handoff for external review',
            scope: ['src/auth.ts', 'tests/auth.test.ts'],
            canonicalSources: ['src/auth.ts', 'tests/auth.test.ts'],
            constraints: ['repo-first', 'proof-before-merge'],
            expectedOutput: 'review-ready patch',
            expectedProof: ['verify://task-auth/handoff', 'tests://auth#handoff'],
            mode: 'preview'
          }
        },
        sequenceId: 11,
        timestamp: '2026-04-11T12:00:11.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Add observability docs',
        sourceEventType: 'routing',
        traceId: 'trace-observability',
        taskId: 'task-observability',
        metadata: {
          intent: 'Add observability docs'
        },
        sequenceId: 14,
        timestamp: '2026-04-11T12:00:14.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {
      'host-copilot': {
        sequenceId: 40,
        timestamp: '2026-04-11T12:00:40.000Z',
        binding: {
          hostId: 'host-copilot',
          hostType: 'copilot',
          displayName: 'GitHub Copilot',
          authMode: 'oauth',
          connectionState: 'online',
          trustStatus: 'trusted',
          scopes: ['fs', 'network'],
          capabilityManifestRef: 'manifest://host-copilot',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T12:00:39.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-copilot',
          hostId: 'host-copilot',
          routines: ['review.pull_request'],
          toolProviders: ['github-mcp'],
          reviewChannels: ['copilot_review'],
          contextSources: ['review_summary', 'selection'],
          permissionMode: 'hybrid',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: 'connected'
      },
      'host-mammouth': {
        sequenceId: 41,
        timestamp: '2026-04-11T12:00:41.000Z',
        binding: {
          hostId: 'host-mammouth',
          hostType: 'other',
          displayName: 'Mammouth AI',
          authMode: 'token',
          connectionState: 'online',
          trustStatus: 'review',
          scopes: ['network'],
          capabilityManifestRef: 'manifest://host-mammouth',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T12:00:38.000Z'
        },
        manifest: {
          manifestId: 'manifest://host-mammouth',
          hostId: 'host-mammouth',
          routines: ['review.external'],
          toolProviders: ['mammouth-api'],
          reviewChannels: ['other'],
          contextSources: ['session_context', 'review_summary'],
          permissionMode: 'policy',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: false
        },
        reason: 'connected'
      }
    },
    recentHostInvocationDecisions: [
      {
        sequenceId: 51,
        timestamp: '2026-04-11T12:00:51.000Z',
        envelope: {
          envelopeId: 'env://host-mammouth/task-auth',
          hostId: 'host-mammouth',
          actionKind: 'review_import',
          mode: 'validate',
          correlationId: 'corr-auth',
          idempotencyKey: 'idem-auth',
          traceId: 'trace-auth',
          taskId: 'task-auth',
          requestedScopes: ['network'],
          payload: {
            reviewId: 'review://task-auth/mammouth'
          },
          evidencePolicy: 'strict'
        },
        decision: 'PROMPT',
        reason: 'Need a final human confirmation before importing the review.',
        meta: {
          traceId: 'trace-auth',
          taskId: 'task-auth',
          correlationId: 'corr-auth',
          hostId: 'host-mammouth'
        }
      }
    ],
    recentHostReviews: [
      {
        sequenceId: 52,
        timestamp: '2026-04-11T12:00:52.000Z',
        review: {
          reviewId: 'review://task-auth/mammouth',
          hostId: 'host-mammouth',
          sourceType: 'other',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-auth-proof',
              severity: 'medium',
              message: 'Expected proof references must be attached before commit.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['verify://task-auth/handoff'],
          importedAt: '2026-04-11T12:00:52.000Z',
          traceId: 'trace-auth',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'trace-auth',
          taskId: 'task-auth',
          correlationId: 'corr-auth',
          hostId: 'host-mammouth'
        }
      }
    ],
    recentHostContextEntries: [
      {
        sequenceId: 53,
        timestamp: '2026-04-11T12:00:53.000Z',
        entry: {
          entryId: 'context://task-auth/mission-pack',
          hostId: 'host-copilot',
          sourceType: 'session_context',
          visibility: 'shared',
          confidence: 9,
          importedAt: '2026-04-11T12:00:53.000Z',
          ttlSeconds: 3600,
          contentRef: 'artifact://mission-pack/task-auth',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'trace-auth',
          taskId: 'task-auth',
          correlationId: 'corr-auth',
          hostId: 'host-copilot'
        }
      }
    ],
    lastErrors: []
  };
}

describe('host handoff view', () => {
  it('builds reusable host handoff packets from mission packs, canonical envelopes and imported reviews', () => {
    const state = createBaseState();
    const view = createHostHandoffView(state);
    const dashboard = createRuntimeDashboardView(state);
    const uiView = createRuntimeDashboardUiView(dashboard);

    expect(view.summary).toMatchObject({
      packetCount: 2,
      readyCount: 0,
      reviewPendingCount: 1,
      blockedCount: 1,
      missionPackCount: 1,
      missingMissionPackCount: 1,
      missingCanonicalEnvelopeCount: 0,
      openReviewFindingCount: 1
    });

    const authPacket = view.packets.find((packet) => packet.taskId === 'task-auth');
    expect(authPacket).toMatchObject({
      packetId: 'host-handoff:task-auth',
      traceId: 'trace-auth',
      status: 'review_pending',
      readyForDispatch: false,
      latestDecision: 'PROMPT',
      latestDecisionHostId: 'host-mammouth',
      latestReviewVerdict: 'warn',
      reviewCount: 1,
      openReviewFindingCount: 1,
      contextEntryCount: 1,
      canonicalEnvelopeCount: 5
    });
    expect(authPacket?.missionPack).toMatchObject({
      objective: 'Prepare a governed handoff for external review',
      mode: 'preview'
    });
    expect(authPacket?.readyHostIds).toEqual(['host-copilot', 'host-mammouth']);
    expect(authPacket?.canonicalMessageTypes).toEqual([
      'host.context',
      'host.invocation',
      'host.review',
      'task.update',
      'workflow.step'
    ]);
    expect(authPacket?.missingRequirements).toEqual([]);

    const blockedPacket = view.packets.find((packet) => packet.taskId === 'task-observability');
    expect(blockedPacket).toMatchObject({
      status: 'blocked',
      readyForDispatch: false,
      canonicalEnvelopeCount: 2
    });
    expect(blockedPacket?.missingRequirements).toContain('mission_pack');

    const reviewPending = queryHostHandoffView(view, { status: 'review_pending' });
    expect(reviewPending.totalCount).toBe(1);
    expect(reviewPending.packets[0]?.taskId).toBe('task-auth');

    expect(dashboard.summary).toMatchObject({
      hostHandoffPacketCount: 2,
      readyHostHandoffCount: 0,
      reviewPendingHostHandoffCount: 1,
      blockedHostHandoffCount: 1
    });
    expect(uiView.statCards.find((card) => card.id === 'host-handoffs')).toMatchObject({
      value: 2,
      tone: 'critical',
      hint: '0 ready, 1 review, 1 blocked'
    });
  });
});