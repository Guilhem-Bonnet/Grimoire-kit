import {
  createHostBindingStateEvent,
  createHostInvocationDecisionEvent,
  createHostReviewArtifactEvent,
  createStateSnapshotEvent,
  createTaskUpdateEvent,
  createToolCallEvent,
  createVerificationGateEvent,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type CapabilityManifest,
  type GameStateSnapshot,
  type HostBinding,
  type InvocationEnvelope
} from '../../src/contracts/events';
import { evaluateHostInvocationPolicy } from '../../src/bridge/host-invocation-policy';
import { createRuntimeCockpitView } from '../../src/state/runtime-cockpit-view';
import { createRuntimeDashboardViewFromEvents } from '../../src/state/runtime-dashboard-view';
import { queryVerificationEvidencePacks } from '../../src/state/verification-evidence-pack-view';

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

const SNAPSHOT: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-11T11:00:00.000Z',
  lastSequenceId: 1,
  agents: [ORCHESTRATOR, DEV_AGENT],
  tasks: [
    {
      id: 'task-auth',
      title: 'Implement auth',
      status: 'in_progress',
      assigneeId: 'dev-1'
    }
  ],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

const HOST_BINDING: HostBinding = {
  hostId: 'host-copilot',
  hostType: 'copilot',
  displayName: 'GitHub Copilot',
  authMode: 'oauth',
  connectionState: 'online',
  trustStatus: 'trusted',
  scopes: ['fs', 'config_write'],
  capabilityManifestRef: 'manifest-copilot',
  sourceOfTruth: 'secondary',
  lastSeenAt: '2026-04-11T11:00:01.000Z'
};

const HOST_MANIFEST: CapabilityManifest = {
  manifestId: 'manifest-copilot',
  hostId: 'host-copilot',
  routines: ['code-review'],
  toolProviders: ['github-mcp'],
  reviewChannels: ['review-import'],
  contextSources: ['selection'],
  permissionMode: 'policy',
  supportsStreaming: true,
  supportsReviewImport: true,
  supportsContextImport: true,
  supportsPreviewCommit: true
};

const PREVIEW_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-preview-001',
  hostId: 'host-copilot',
  actionKind: 'tool_call',
  mode: 'preview',
  correlationId: 'corr-auth-success',
  idempotencyKey: 'idem-auth-preview-001',
  traceId: 'trace-auth-success',
  taskId: 'task-auth',
  requestedScopes: ['fs'],
  payload: {
    tool: 'semantic_search',
    query: 'auth runtime critical flow'
  },
  evidencePolicy: 'basic'
};

const VALIDATION_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-validate-001',
  hostId: 'host-copilot',
  actionKind: 'review_import',
  mode: 'validate',
  correlationId: 'corr-auth-success',
  idempotencyKey: 'idem-auth-validate-001',
  traceId: 'trace-auth-success',
  taskId: 'task-auth',
  requestedScopes: [],
  payload: {
    reviewId: 'review-copilot-001'
  },
  evidencePolicy: 'strict'
};

const DIRECT_COMMIT_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-commit-denied-001',
  hostId: 'host-copilot',
  actionKind: 'tool_call',
  mode: 'commit',
  correlationId: 'corr-auth-refused',
  idempotencyKey: 'idem-auth-commit-refused-001',
  traceId: 'trace-auth-refused',
  taskId: 'task-auth',
  requestedScopes: ['config_write'],
  payload: {
    tool: 'create_file',
    path: 'src/auth.ts'
  },
  evidencePolicy: 'strict'
};

describe('critical mono-host flow proof', () => {
  it('proves preview -> validation -> bounded commit and refuses a direct commit mirror path', () => {
    const previewDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, PREVIEW_ENVELOPE);
    const validationDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, VALIDATION_ENVELOPE);
    const directCommitDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, DIRECT_COMMIT_ENVELOPE);

    expect(previewDecision.decision).toBe('ALLOW');
    expect(validationDecision.decision).toBe('ALLOW');
    expect(directCommitDecision.decision).toBe('DENY');
    expect(directCommitDecision.reason).toContain('cannot start directly in commit mode');

    const events = [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T11:00:00.000Z'),
      createHostBindingStateEvent(
        2,
        {
          binding: HOST_BINDING,
          manifest: HOST_MANIFEST
        },
        {
          timestamp: '2026-04-11T11:00:01.000Z'
        }
      ),
      createHostInvocationDecisionEvent(
        3,
        {
          envelope: PREVIEW_ENVELOPE,
          decision: previewDecision.decision,
          reason: previewDecision.reason,
          meta: previewDecision.meta
        },
        {
          timestamp: '2026-04-11T11:00:03.000Z'
        }
      ),
      createToolCallEvent(
        4,
        {
          tool: 'runTests',
          params: {
            files: ['tests/auth.test.ts'],
            task_id: 'task-auth'
          },
          sourceEventType: 'test_run',
          traceId: 'trace-auth-success'
        },
        {
          timestamp: '2026-04-11T11:00:04.000Z',
          agent: DEV_AGENT
        }
      ),
      createHostInvocationDecisionEvent(
        5,
        {
          envelope: VALIDATION_ENVELOPE,
          decision: validationDecision.decision,
          reason: validationDecision.reason,
          meta: validationDecision.meta
        },
        {
          timestamp: '2026-04-11T11:00:05.000Z'
        }
      ),
      createHostReviewArtifactEvent(
        6,
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
                message: 'Preview plan validated for bounded commit.',
                resolutionStatus: 'resolved'
              }
            ],
            linkedEvidenceRefs: ['artifact://host/review-auth-001'],
            importedAt: '2026-04-11T11:00:06.000Z',
            traceId: 'trace-auth-success',
            taskId: 'task-auth'
          },
          meta: {
            traceId: 'trace-auth-success',
            taskId: 'task-auth',
            correlationId: 'corr-auth-success',
            hostId: 'host-copilot'
          }
        },
        {
          timestamp: '2026-04-11T11:00:06.000Z'
        }
      ),
      createVerificationGateEvent(
        7,
        {
          result: 'PASS',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/critical-flow',
          evidenceRefs: [
            { kind: 'test', ref: 'tests://grimoire-game/critical-flow#task-auth' },
            { kind: 'artifact', ref: 'artifact://host/review-auth-001' }
          ],
          controlsExecuted: ['tests:unit', 'host-review:copilot'],
          traceId: 'trace-auth-success',
          taskId: 'task-auth'
        },
        {
          timestamp: '2026-04-11T11:00:07.000Z'
        }
      ),
      createTaskUpdateEvent(
        8,
        {
          id: 'task-auth',
          title: 'Implement auth',
          status: 'done',
          assigneeId: 'dev-1'
        },
        {
          timestamp: '2026-04-11T11:00:08.000Z',
          agent: DEV_AGENT
        }
      ),
      createHostInvocationDecisionEvent(
        9,
        {
          envelope: DIRECT_COMMIT_ENVELOPE,
          decision: directCommitDecision.decision,
          reason: directCommitDecision.reason,
          meta: directCommitDecision.meta
        },
        {
          timestamp: '2026-04-11T11:00:09.000Z'
        }
      )
    ];

    const dashboard = createRuntimeDashboardViewFromEvents(events);
    const cockpit = createRuntimeCockpitView(dashboard);
    const successSession = dashboard.session.sessions.find((session) => session.summary.traceId === 'trace-auth-success');
    const refusalSession = dashboard.session.sessions.find((session) => session.summary.traceId === 'trace-auth-refused');
    const evidencePackQuery = queryVerificationEvidencePacks(dashboard.verificationEvidencePacks, {
      verificationRef: 'verify://task-auth/critical-flow',
      evidenceRef: 'artifact://host/review-auth-001'
    });

    expect(dashboard.hostBridge.invocations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          decision: 'ALLOW',
          envelope: expect.objectContaining({
            traceId: 'trace-auth-success',
            mode: 'preview'
          })
        }),
        expect.objectContaining({
          decision: 'ALLOW',
          envelope: expect.objectContaining({
            traceId: 'trace-auth-success',
            mode: 'validate'
          })
        }),
        expect.objectContaining({
          decision: 'DENY',
          reason: expect.stringContaining('cannot start directly in commit mode'),
          envelope: expect.objectContaining({
            traceId: 'trace-auth-refused',
            mode: 'commit'
          })
        })
      ])
    );
    expect(dashboard.verificationQueue.items[0]).toMatchObject({
      taskId: 'task-auth',
      queueStatus: 'accepted',
      verificationRef: 'verify://task-auth/critical-flow',
      verdict: 'PASS'
    });
    expect(dashboard.verificationEvidencePacks.summary).toMatchObject({
      packCount: 1,
      attestedCount: 1,
      missingEvidenceCount: 0
    });
    expect(dashboard.verificationEvidencePacks.packs[0]).toMatchObject({
      taskRef: 'task-auth',
      verificationRef: 'verify://task-auth/critical-flow',
      status: 'accepted',
      verdict: 'pass'
    });
    expect(dashboard.verificationEvidencePacks.packs[0]?.externalReviews).toMatchObject([
      {
        reviewId: 'review-copilot-001',
        hostId: 'host-copilot',
        verdict: 'pass'
      }
    ]);
    expect(evidencePackQuery.totalCount).toBe(1);
    expect(successSession?.canonicalEnvelopes.map((envelope) => envelope.header.messageType)).toEqual(
      expect.arrayContaining(['task.update', 'host.invocation', 'host.review', 'verification.gate'])
    );
    expect(refusalSession?.canonicalEnvelopes.map((envelope) => envelope.header.messageType)).toEqual(
      expect.arrayContaining(['task.update', 'host.invocation'])
    );
    expect(cockpit.proofs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'verification',
          verificationRef: 'verify://task-auth/critical-flow',
          taskId: 'task-auth'
        }),
        expect.objectContaining({
          source: 'external_review',
          hostId: 'host-copilot',
          verificationRef: 'verify://task-auth/critical-flow',
          taskId: 'task-auth'
        })
      ])
    );
  });
});