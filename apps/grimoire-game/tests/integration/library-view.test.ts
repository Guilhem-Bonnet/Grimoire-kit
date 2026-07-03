import type { GameState } from '../../src/state/game-state';
import { createLibraryView, queryLibraryView } from '../../src/state/library-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 35,
    hydratedAt: '2026-04-10T00:21:00.000Z',
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
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'orch-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-host-001',
        taskId: 'task-auth',
        metadata: {
          runId: 'run-host-001',
          correlationId: 'corr-host-001',
          requestId: 'req-host-001'
        },
        sequenceId: 10,
        timestamp: '2026-04-10T00:10:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth context imported for review',
        sourceEventType: 'decision',
        traceId: 'session-host-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          runId: 'run-host-001',
          correlationId: 'corr-host-001',
          evidenceRefs: ['artifact://host/review-001']
        },
        sequenceId: 11,
        timestamp: '2026-04-10T00:10:10.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {
      'host-claude': {
        sequenceId: 20,
        timestamp: '2026-04-10T00:10:20.000Z',
        binding: {
          hostId: 'host-claude',
          hostType: 'claude',
          displayName: 'Claude Code',
          authMode: 'token',
          connectionState: 'degraded',
          trustStatus: 'review',
          scopes: ['fs'],
          capabilityManifestRef: 'manifest-claude',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-10T00:20:30.000Z'
        },
        manifest: {
          manifestId: 'manifest-claude',
          hostId: 'host-claude',
          routines: ['code-review'],
          toolProviders: ['claude-cli'],
          reviewChannels: ['review-import'],
          contextSources: ['selection', 'summary'],
          permissionMode: 'prompt',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: 'Connector drift forces review-only mode.'
      }
    },
    recentHostInvocationDecisions: [],
    recentHostReviews: [
      {
        sequenceId: 30,
        timestamp: '2026-04-10T00:20:00.000Z',
        review: {
          reviewId: 'review-claude-001',
          hostId: 'host-claude',
          sourceType: 'claude_review',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-001',
              severity: 'medium',
              message: 'Imported context should be refreshed.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['artifact://host/review-001'],
          importedAt: '2026-04-10T00:20:00.000Z',
          traceId: 'session-host-001',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'session-host-001',
          taskId: 'task-auth',
          correlationId: 'corr-host-001',
          hostId: 'host-claude'
        }
      }
    ],
    recentHostContextEntries: [
      {
        sequenceId: 21,
        timestamp: '2026-04-10T00:10:30.000Z',
        entry: {
          entryId: 'ctx-claude-001',
          hostId: 'host-claude',
          sourceType: 'selection',
          visibility: 'shared',
          confidence: 6.5,
          importedAt: '2026-04-10T00:10:30.000Z',
          ttlSeconds: 60,
          contentRef: 'selection://auth/runtime-dashboard',
          trustStatus: 'review'
        },
        meta: {
          traceId: 'session-host-001',
          taskId: 'task-auth',
          correlationId: 'corr-host-001',
          hostId: 'host-claude'
        }
      },
      {
        sequenceId: 31,
        timestamp: '2026-04-10T00:20:30.000Z',
        entry: {
          entryId: 'ctx-claude-002',
          hostId: 'host-claude',
          sourceType: 'review_summary',
          visibility: 'shared',
          confidence: 8.2,
          importedAt: '2026-04-10T00:20:30.000Z',
          ttlSeconds: 600,
          contentRef: 'summary://auth/runtime-dashboard',
          supersedes: 'ctx-claude-001',
          trustStatus: 'trusted'
        },
        meta: {
          traceId: 'session-host-001',
          taskId: 'task-auth',
          correlationId: 'corr-host-001',
          hostId: 'host-claude'
        }
      }
    ],
    lastErrors: []
  };
}

describe('library view', () => {
  it('projects imported host context, stale memory, reviews and trace volumes', () => {
    const view = createLibraryView(createBaseState());

    expect(view.summary).toEqual({
      hostCount: 1,
      shelfCount: 5,
      contextEntryCount: 2,
      staleContextCount: 1,
      supersededContextCount: 1,
      reviewArtifactCount: 1,
      openReviewFindingCount: 1,
      linkedTraceCount: 1
    });
    expect(view.hosts[0]).toMatchObject({
      hostId: 'host-claude',
      displayName: 'Claude Code',
      contextEntryCount: 2,
      staleContextEntryCount: 1,
      reviewArtifactCount: 1,
      linkedTraceCount: 1
    });
    expect(view.contextEntries.map((entry) => [entry.entryId, entry.stale, entry.supersededBy])).toEqual([
      ['ctx-claude-001', true, 'ctx-claude-002'],
      ['ctx-claude-002', false, null]
    ]);
    expect(view.reviews[0]).toMatchObject({
      reviewId: 'review-claude-001',
      verdict: 'warn',
      openFindingCount: 1,
      taskId: 'task-auth'
    });
    expect(view.traces[0]).toMatchObject({
      traceId: 'session-host-001',
      contextEntryIds: ['ctx-claude-001', 'ctx-claude-002'],
      reviewIds: ['review-claude-001'],
      staleContextCount: 1
    });
  });

  it('supports query by host, trace, task and stale-only filters', () => {
    const view = createLibraryView(createBaseState());
    const query = queryLibraryView(view, {
      hostId: 'host-claude',
      traceId: 'session-host-001',
      taskId: 'task-auth',
      staleOnly: true
    });

    expect(query.contextEntries.map((entry) => entry.entryId)).toEqual(['ctx-claude-001']);
    expect(query.reviews.map((review) => review.reviewId)).toEqual(['review-claude-001']);
    expect(query.traces.map((trace) => trace.traceId)).toEqual(['session-host-001']);
    expect(query.totalCount).toBe(3);
  });
});