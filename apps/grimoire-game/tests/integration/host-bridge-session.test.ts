import {
  createHostBindingStateEvent,
  createHostContextLedgerUpdateEvent,
  createHostInvocationDecisionEvent,
  createHostReviewArtifactEvent,
  createStateSnapshotEvent,
  RUNTIME_PROTOCOL_VERSION,
  type GameStateSnapshot
} from '../../src/contracts/events';
import { createAuditView } from '../../src/state/audit-view';
import { applyServerEvents, createEmptyGameState } from '../../src/state/game-state';
import { createSessionView } from '../../src/state/session-view';

describe('host bridge audit and session views', () => {
  it('keeps unscoped bindings in audit while grouping traced host activity into one session', () => {
    const snapshot: GameStateSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: '2026-04-10T12:00:00.000Z',
      lastSequenceId: 0,
      agents: [],
      tasks: [
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'review',
          assigneeId: null
        }
      ],
      config: {},
      recentToolCalls: [],
      recentWorkflowSteps: []
    };

    const events = [
      createStateSnapshotEvent(0, snapshot, '2026-04-10T12:00:00.000Z'),
      createHostBindingStateEvent(
        5,
        {
          binding: {
            hostId: 'host-copilot',
            hostType: 'copilot',
            displayName: 'GitHub Copilot',
            authMode: 'oauth',
            connectionState: 'online',
            trustStatus: 'trusted',
            scopes: ['fs'],
            capabilityManifestRef: 'manifest-copilot',
            sourceOfTruth: 'secondary'
          },
          manifest: {
            manifestId: 'manifest-copilot',
            hostId: 'host-copilot',
            routines: ['code-review'],
            toolProviders: ['github-mcp'],
            reviewChannels: ['review-import'],
            contextSources: ['selection'],
            permissionMode: 'hybrid',
            supportsStreaming: true,
            supportsReviewImport: true,
            supportsContextImport: true,
            supportsPreviewCommit: true
          }
        },
        {
          timestamp: '2026-04-10T12:00:05.000Z'
        }
      ),
      createHostInvocationDecisionEvent(
        6,
        {
          envelope: {
            envelopeId: 'env-copilot-001',
            hostId: 'host-copilot',
            actionKind: 'tool_call',
            mode: 'preview',
            correlationId: 'corr-host-002',
            idempotencyKey: 'idem-host-002',
            traceId: 'session-host-002',
            taskId: 'task-auth',
            requestedScopes: ['fs'],
            payload: {
              tool: 'semantic_search'
            },
            evidencePolicy: 'basic'
          },
          decision: 'ALLOW',
          reason: 'Host invocation satisfies the current Host Bridge policy.',
          meta: {
            traceId: 'session-host-002',
            taskId: 'task-auth',
            correlationId: 'corr-host-002',
            hostId: 'host-copilot'
          }
        },
        {
          timestamp: '2026-04-10T12:00:06.000Z'
        }
      ),
      createHostReviewArtifactEvent(
        7,
        {
          review: {
            reviewId: 'review-copilot-001',
            hostId: 'host-copilot',
            sourceType: 'copilot_review',
            subjectRef: 'task:task-auth',
            verdict: 'pass',
            findings: [
              {
                id: 'finding-001',
                severity: 'info',
                message: 'No blocking issue found.',
                resolutionStatus: 'resolved'
              }
            ],
            linkedEvidenceRefs: ['artifact://host-bridge/review-copilot-001'],
            importedAt: '2026-04-10T12:00:07.000Z',
            traceId: 'session-host-002',
            taskId: 'task-auth'
          },
          meta: {
            traceId: 'session-host-002',
            taskId: 'task-auth',
            correlationId: 'corr-host-002',
            hostId: 'host-copilot'
          }
        },
        {
          timestamp: '2026-04-10T12:00:07.000Z'
        }
      ),
      createHostContextLedgerUpdateEvent(
        8,
        {
          entry: {
            entryId: 'ctx-copilot-001',
            hostId: 'host-copilot',
            sourceType: 'selection',
            visibility: 'shared',
            confidence: 8.8,
            importedAt: '2026-04-10T12:00:08.000Z',
            ttlSeconds: 300,
            contentRef: 'selection://host-bridge/auth',
            trustStatus: 'trusted'
          },
          meta: {
            traceId: 'session-host-002',
            taskId: 'task-auth',
            correlationId: 'corr-host-002',
            hostId: 'host-copilot'
          }
        },
        {
          timestamp: '2026-04-10T12:00:08.000Z'
        }
      )
    ];

    const state = applyServerEvents(createEmptyGameState(), events);
    const auditView = createAuditView(state);
    const sessionView = createSessionView(state);

    expect(auditView.entries.map((entry) => entry.kind)).toEqual([
      'host_context',
      'host_review',
      'host_invocation',
      'host_binding'
    ]);
    expect(sessionView.metrics).toMatchObject({
      sessionCount: 1,
      unscopedEntryCount: 1
    });
    expect(sessionView.unscopedEntries[0]?.kind).toBe('host_binding');
    expect(sessionView.sessions[0]?.summary).toMatchObject({
      traceId: 'session-host-002',
      taskIds: ['task-auth'],
      entryCount: 3
    });
    expect(sessionView.sessions[0]?.canonicalEnvelopes.map((envelope) => envelope.header.messageType)).toEqual([
      'task.update',
      'host.invocation',
      'host.review',
      'host.context'
    ]);
  });
});