import type { GameState } from '../../src/state/game-state';
import { createHostBridgeView } from '../../src/state/host-bridge-view';
import { createLibraryView } from '../../src/state/library-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 42,
    hydratedAt: '2026-04-11T10:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'working',
        roomId: 'war-room',
        position: { x: 4, y: 4 }
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Stabilize host review import',
        status: 'review',
        assigneeId: 'orch-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Import host review',
        detail: 'Copilot review imported for auth task',
        sourceEventType: 'host_review_import',
        traceId: 'trace-host-1',
        taskId: 'task-auth',
        metadata: {
          correlationId: 'corr-host-1',
          verificationRef: 'verify://task-auth/host-review'
        },
        sequenceId: 41,
        timestamp: '2026-04-11T10:00:41.000Z',
        agentId: 'orch-1'
      }
    ],
    hostBindings: {
      'host-copilot': {
        sequenceId: 10,
        timestamp: '2026-04-11T10:00:10.000Z',
        binding: {
          hostId: 'host-copilot',
          hostType: 'copilot',
          displayName: 'GitHub Copilot',
          authMode: 'oauth',
          connectionState: 'online',
          trustStatus: 'review',
          scopes: ['fs', 'network'],
          capabilityManifestRef: 'manifest://host-copilot',
          sourceOfTruth: 'secondary',
          lastSeenAt: '2026-04-11T10:00:40.000Z',
          notes: 'Review import active'
        },
        manifest: {
          manifestId: 'manifest://host-copilot',
          hostId: 'host-copilot',
          routines: ['review.pull_request'],
          toolProviders: ['github-mcp'],
          reviewChannels: ['copilot_review', 'github_pr_comment'],
          contextSources: ['review_summary'],
          permissionMode: 'hybrid',
          supportsStreaming: true,
          supportsReviewImport: true,
          supportsContextImport: true,
          supportsPreviewCommit: true
        },
        reason: 'connected'
      }
    },
    recentHostInvocationDecisions: [
      {
        sequenceId: 20,
        timestamp: '2026-04-11T10:00:20.000Z',
        envelope: {
          envelopeId: 'env://host-copilot/review-import',
          hostId: 'host-copilot',
          actionKind: 'review_import',
          mode: 'validate',
          correlationId: 'corr-host-1',
          idempotencyKey: 'host-review-import-1',
          traceId: 'trace-host-1',
          taskId: 'task-auth',
          requestedScopes: ['fs'],
          payload: {
            reviewId: 'review://task-auth/1',
            subjectRef: 'task:task-auth'
          },
          evidencePolicy: 'strict'
        },
        decision: 'PROMPT',
        reason: 'Need verification gate before commit',
        meta: {
          traceId: 'trace-host-1',
          taskId: 'task-auth',
          correlationId: 'corr-host-1',
          hostId: 'host-copilot'
        }
      }
    ],
    recentHostReviews: [
      {
        sequenceId: 30,
        timestamp: '2026-04-11T10:00:30.000Z',
        review: {
          reviewId: 'review://task-auth/1',
          hostId: 'host-copilot',
          sourceType: 'copilot_review',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-auth-proof',
              severity: 'high',
              message: 'Missing verification gate reference',
              resolutionStatus: 'open'
            },
            {
              id: 'finding-auth-doc',
              severity: 'low',
              message: 'Document prompt provenance',
              resolutionStatus: 'acknowledged'
            }
          ],
          linkedEvidenceRefs: ['verify://task-auth/host-review', 'artifact://review/task-auth'],
          importedAt: '2026-04-11T10:00:30.000Z',
          traceId: 'trace-host-1',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'trace-host-1',
          taskId: 'task-auth',
          correlationId: 'corr-host-1',
          hostId: 'host-copilot'
        }
      }
    ],
    recentHostContextEntries: [
      {
        sequenceId: 31,
        timestamp: '2026-04-11T10:00:31.000Z',
        entry: {
          entryId: 'context://task-auth/review-summary',
          hostId: 'host-copilot',
          sourceType: 'review_summary',
          visibility: 'shared',
          confidence: 7,
          importedAt: '2026-04-11T10:00:31.000Z',
          ttlSeconds: 3600,
          contentRef: 'docs://reviews/task-auth-summary',
          trustStatus: 'review'
        },
        meta: {
          traceId: 'trace-host-1',
          taskId: 'task-auth',
          correlationId: 'corr-host-1',
          hostId: 'host-copilot'
        }
      }
    ],
    lastErrors: []
  };
}

describe('library-view host reviews', () => {
  it('projects imported host reviews into bridge, library and dashboard summaries', () => {
    const state = createBaseState();
    const hostBridge = createHostBridgeView(state);
    const library = createLibraryView(state);
    const dashboard = createRuntimeDashboardView(state);

    expect(hostBridge.metrics).toMatchObject({
      hostCount: 1,
      promptDecisionCount: 1,
      reviewArtifactCount: 1,
      contextEntryCount: 1
    });

    expect(library.summary).toMatchObject({
      hostCount: 1,
      reviewArtifactCount: 1,
      openReviewFindingCount: 2,
      linkedTraceCount: 1
    });

    expect(library.hosts[0]).toMatchObject({
      hostId: 'host-copilot',
      reviewArtifactCount: 1,
      contextEntryCount: 1,
      linkedTraceCount: 1
    });

    expect(library.reviews[0]).toMatchObject({
      reviewId: 'review://task-auth/1',
      subjectRef: 'task:task-auth',
      verdict: 'warn',
      findingCount: 2,
      openFindingCount: 2,
      linkedEvidenceRefs: ['verify://task-auth/host-review', 'artifact://review/task-auth']
    });

    expect(library.traces[0]).toMatchObject({
      traceId: 'trace-host-1',
      reviewIds: ['review://task-auth/1']
    });

    expect(dashboard.summary).toMatchObject({
      importedHostReviewCount: 1,
      importedHostContextCount: 1,
      libraryOpenReviewFindingCount: 2
    });
  });
});