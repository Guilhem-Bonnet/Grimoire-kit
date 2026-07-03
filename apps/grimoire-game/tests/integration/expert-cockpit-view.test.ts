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
import { createDeepInspectionActionAuditEntry } from '../../src/state/deep-inspection-view';
import { createExpertCockpitView } from '../../src/state/expert-cockpit-view';
import { applyServerEvents, createEmptyGameState } from '../../src/state/game-state';

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
  generatedAt: '2026-04-11T12:00:00.000Z',
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
  config: {
    agentProfiles: {
      'dev-1': {
        model: 'gpt-5.4',
        branch: 'feature/auth',
        systemPrompt: 'You are Amelia and expose only verified actions.',
        tokens: {
          budget: 8_000,
          used: 2_400
        }
      }
    }
  },
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
  sourceOfTruth: 'secondary'
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
  envelopeId: 'env-preview-002',
  hostId: 'host-copilot',
  actionKind: 'tool_call',
  mode: 'preview',
  correlationId: 'corr-auth-success',
  idempotencyKey: 'idem-auth-preview-002',
  traceId: 'trace-auth-success',
  taskId: 'task-auth',
  requestedScopes: ['fs'],
  payload: {
    tool: 'semantic_search',
    query: 'expert cockpit critical flow'
  },
  evidencePolicy: 'basic'
};

const VALIDATION_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-validate-002',
  hostId: 'host-copilot',
  actionKind: 'review_import',
  mode: 'validate',
  correlationId: 'corr-auth-success',
  idempotencyKey: 'idem-auth-validate-002',
  traceId: 'trace-auth-success',
  taskId: 'task-auth',
  requestedScopes: [],
  payload: {
    reviewId: 'review-copilot-002'
  },
  evidencePolicy: 'strict'
};

const DIRECT_COMMIT_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-commit-denied-002',
  hostId: 'host-copilot',
  actionKind: 'tool_call',
  mode: 'commit',
  correlationId: 'corr-auth-refused',
  idempotencyKey: 'idem-auth-commit-refused-002',
  traceId: 'trace-auth-refused',
  taskId: 'task-auth',
  requestedScopes: ['config_write'],
  payload: {
    tool: 'create_file',
    path: 'src/auth.ts'
  },
  evidencePolicy: 'strict'
};

describe('expert cockpit view', () => {
  it('unifies inspection, decisions, proof and replay on the proved critical flow', () => {
    const previewDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, PREVIEW_ENVELOPE);
    const validationDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, VALIDATION_ENVELOPE);
    const directCommitDecision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, DIRECT_COMMIT_ENVELOPE);
    const events = [
      createStateSnapshotEvent(1, SNAPSHOT, '2026-04-11T12:00:00.000Z'),
      createHostBindingStateEvent(
        2,
        {
          binding: HOST_BINDING,
          manifest: HOST_MANIFEST
        },
        {
          timestamp: '2026-04-11T12:00:01.000Z'
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
          timestamp: '2026-04-11T12:00:03.000Z'
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
          timestamp: '2026-04-11T12:00:04.000Z',
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
          timestamp: '2026-04-11T12:00:05.000Z'
        }
      ),
      createHostReviewArtifactEvent(
        6,
        {
          review: {
            reviewId: 'review-copilot-002',
            hostId: 'host-copilot',
            sourceType: 'copilot_review',
            subjectRef: 'task:task-auth',
            verdict: 'pass',
            findings: [
              {
                id: 'finding-001',
                severity: 'info',
                message: 'Critical flow validated for bounded commit.',
                resolutionStatus: 'resolved'
              }
            ],
            linkedEvidenceRefs: ['artifact://host/review-auth-002'],
            importedAt: '2026-04-11T12:00:06.000Z',
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
          timestamp: '2026-04-11T12:00:06.000Z'
        }
      ),
      createVerificationGateEvent(
        7,
        {
          result: 'PASS',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/expert-cockpit',
          evidenceRefs: [
            { kind: 'test', ref: 'tests://grimoire-game/expert-cockpit#task-auth' },
            { kind: 'artifact', ref: 'artifact://host/review-auth-002' }
          ],
          controlsExecuted: ['tests:unit', 'host-review:copilot'],
          traceId: 'trace-auth-success',
          taskId: 'task-auth'
        },
        {
          timestamp: '2026-04-11T12:00:07.000Z'
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
          timestamp: '2026-04-11T12:00:08.000Z',
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
          timestamp: '2026-04-11T12:00:09.000Z'
        }
      )
    ];
    const state = applyServerEvents(createEmptyGameState(), events);
    const actor = {
      principalId: 'orch-1',
      role: 'orchestrator' as const
    };
    const auditTrail = [
      createDeepInspectionActionAuditEntry(
        actor,
        {
          action: 'restart',
          targetAgentId: 'dev-1',
          taskId: 'task-auth',
          traceId: 'trace-auth-success',
          detail: 'Operator restart after review.'
        },
        {
          allowed: true,
          reason: null,
          requiredRole: 'orchestrator'
        },
        '2026-04-11T12:00:10.000Z'
      )
    ];

    const view = createExpertCockpitView(state, {
      actor,
      auditTrail
    });

    expect(view.status).toBe('accepted');
    expect(view.traceId).toBe('trace-auth-success');
    expect(view.taskId).toBe('task-auth');
    expect(view.hostId).toBe('host-copilot');
    expect(view.hostDisplayName).toBe('GitHub Copilot');
    expect(view.summary).toContain('mirror path');
    expect(view.decisions).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          decision: 'ALLOW',
          mode: 'preview',
          traceId: 'trace-auth-success'
        }),
        expect.objectContaining({
          decision: 'ALLOW',
          mode: 'validate',
          traceId: 'trace-auth-success'
        }),
        expect.objectContaining({
          decision: 'DENY',
          mode: 'commit',
          traceId: 'trace-auth-refused'
        })
      ])
    );
    expect(view.inspection?.profile).toMatchObject({
      model: 'gpt-5.4',
      branch: 'feature/auth',
      systemPrompt: 'You are Amelia and expose only verified actions.',
      activeTool: 'runTests'
    });
    expect(view.inspection?.auditTrail[0]).toMatchObject({
      action: 'restart',
      actorRole: 'orchestrator',
      allowed: true
    });
    expect(view.workflow).toMatchObject({
      traceId: 'trace-auth-success',
      stepCount: 1,
      decisionCount: 1
    });
    expect(view.workflow.currentStep).toContain('Verification gate PASS');
    expect(view.proof).toMatchObject({
      verificationRef: 'verify://task-auth/expert-cockpit',
      queueStatus: 'accepted',
      verdict: 'PASS',
      evidenceRefCount: 2,
      externalReviewCount: 1
    });
    expect(view.replay).toMatchObject({
      traceId: 'trace-auth-success',
      canonicalEnvelopeCount: 5
    });
    expect(view.replay.messageTypes).toEqual(
      expect.arrayContaining(['task.update', 'host.invocation', 'host.review', 'verification.gate'])
    );
    expect(view.cockpit.proofs).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: 'external_review',
          hostId: 'host-copilot',
          verificationRef: 'verify://task-auth/expert-cockpit'
        })
      ])
    );
  });
});