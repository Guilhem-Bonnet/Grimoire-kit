import {
  createHostBindingStateEvent,
  createHostContextLedgerUpdateEvent,
  createHostInvocationDecisionEvent,
  createHostReviewArtifactEvent,
  createStateSnapshotEvent,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { createRuntimeDashboardViewFromEvents } from '../../src/state/runtime-dashboard-view';

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-10T11:00:00.000Z',
  lastSequenceId: 1,
  agents: [
    {
      id: 'orch-1',
      name: 'Orchestrator',
      role: 'orchestrator',
      status: 'idle',
      roomId: 'war-room',
      position: { x: 4, y: 4 }
    }
  ],
  tasks: [
    {
      id: 'task-auth',
      title: 'Implement auth',
      status: 'review',
      assigneeId: 'orch-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

describe('runtime-dashboard host bridge projection', () => {
  it('surfaces host status, invocation decisions, imported review and imported context', () => {
    const events = [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-10T11:00:01.000Z'),
      createHostBindingStateEvent(
        2,
        {
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
            lastSeenAt: '2026-04-10T11:00:02.000Z'
          },
          manifest: {
            manifestId: 'manifest-claude',
            hostId: 'host-claude',
            routines: ['code-review'],
            toolProviders: ['claude-cli'],
            reviewChannels: ['review-import'],
            contextSources: ['selection'],
            permissionMode: 'prompt',
            supportsStreaming: true,
            supportsReviewImport: true,
            supportsContextImport: true,
            supportsPreviewCommit: true
          },
          reason: 'Host degraded after CLI capability drift.'
        },
        {
          timestamp: '2026-04-10T11:00:02.000Z'
        }
      ),
      createHostInvocationDecisionEvent(
        3,
        {
          envelope: {
            envelopeId: 'env-claude-001',
            hostId: 'host-claude',
            actionKind: 'tool_call',
            mode: 'preview',
            correlationId: 'corr-host-001',
            idempotencyKey: 'idem-host-001',
            traceId: 'session-host-001',
            taskId: 'task-auth',
            requestedScopes: ['fs'],
            payload: {
              tool: 'semantic_search'
            },
            evidencePolicy: 'basic'
          },
          decision: 'PROMPT',
          reason: 'Preview requires a permission prompt.',
          meta: {
            traceId: 'session-host-001',
            taskId: 'task-auth',
            correlationId: 'corr-host-001',
            hostId: 'host-claude',
            promptRef: 'host-prompt:host-claude:env-claude-001'
          }
        },
        {
          timestamp: '2026-04-10T11:00:03.000Z'
        }
      ),
      createHostReviewArtifactEvent(
        4,
        {
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
                message: 'Connector is degraded and should stay read-only.',
                resolutionStatus: 'open'
              }
            ],
            linkedEvidenceRefs: ['artifact://host-bridge/review-claude-001'],
            importedAt: '2026-04-10T11:00:04.000Z',
            traceId: 'session-host-001',
            taskId: 'task-auth'
          },
          meta: {
            traceId: 'session-host-001',
            taskId: 'task-auth',
            correlationId: 'corr-host-001',
            hostId: 'host-claude'
          }
        },
        {
          timestamp: '2026-04-10T11:00:04.000Z'
        }
      ),
      createHostContextLedgerUpdateEvent(
        5,
        {
          entry: {
            entryId: 'ctx-claude-001',
            hostId: 'host-claude',
            sourceType: 'selection',
            visibility: 'shared',
            confidence: 7.5,
            importedAt: '2026-04-10T11:00:05.000Z',
            ttlSeconds: 600,
            contentRef: 'selection://host-bridge/runtime-dashboard',
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
          timestamp: '2026-04-10T11:00:05.000Z'
        }
      )
    ];

    const dashboard = createRuntimeDashboardViewFromEvents(events);

    expect(dashboard.hostBridge.metrics).toMatchObject({
      hostCount: 1,
      degradedCount: 1,
      reviewCount: 1,
      promptDecisionCount: 1,
      reviewArtifactCount: 1,
      contextEntryCount: 1
    });
    expect(dashboard.hostBridge.hosts[0]).toMatchObject({
      hostId: 'host-claude',
      displayName: 'Claude Code',
      connectionState: 'degraded',
      permissionMode: 'prompt'
    });
    expect(dashboard.summary).toMatchObject({
      hostCount: 1,
      degradedHostCount: 1,
      promptedHostDecisionCount: 1,
      importedHostReviewCount: 1,
      importedHostContextCount: 1,
      libraryContextCount: 1,
      libraryStaleContextCount: 0,
      libraryOpenReviewFindingCount: 1
    });
    expect(dashboard.library.summary).toMatchObject({
      hostCount: 1,
      contextEntryCount: 1,
      reviewArtifactCount: 1,
      linkedTraceCount: 1
    });
    expect(dashboard.library.traces[0]).toMatchObject({
      traceId: 'session-host-001',
      contextEntryIds: ['ctx-claude-001'],
      reviewIds: ['review-claude-001']
    });
    expect(dashboard.supervision.summary.releaseBlocked).toBe(true);
    expect(dashboard.session.metrics.sessionCount).toBe(1);
  });
});