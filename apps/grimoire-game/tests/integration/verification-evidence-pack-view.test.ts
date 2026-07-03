import type { GameState } from '../../src/state/game-state';
import {
  createVerificationEvidencePackView,
  queryVerificationEvidencePacks
} from '../../src/state/verification-evidence-pack-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 33,
    hydratedAt: '2026-04-10T00:00:00.000Z',
    agents: {
      'orch-1': {
        id: 'orch-1',
        name: 'Orchestrator',
        role: 'orchestrator',
        status: 'idle',
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
      },
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'working',
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
        status: 'done',
        assigneeId: 'dev-1'
      },
      'task-observability': {
        id: 'task-observability',
        title: 'Add observability panel',
        status: 'review',
        assigneeId: 'qa-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 12,
        timestamp: '2026-04-10T00:00:12.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'runTests',
        params: { query: 'observability panel', task_id: 'task-observability' },
        sourceEventType: 'test_run',
        traceId: 'session-002',
        sequenceId: 31,
        timestamp: '2026-04-10T00:00:31.000Z',
        agentId: 'qa-1'
      }
    ],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Implement auth',
        sourceEventType: 'routing',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          intent: 'Implement auth',
          missionPack: {
            objective: 'Ship auth runtime flow with proof',
            scope: ['src/auth.ts', 'tests/auth.test.ts'],
            canonicalSources: ['src/auth.ts', 'tests/auth.test.ts'],
            constraints: ['repo-first', 'evidence-before-done'],
            expectedOutput: 'patch',
            expectedProof: [
              'verify://task-auth/runtime-dashboard',
              'control://tests:unit',
              'tests://grimoire-game/runtime-dashboard#task-auth'
            ],
            mode: 'commit'
          },
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 10,
        timestamp: '2026-04-10T00:00:10.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT RS256 stateless',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
          verdict: 'PASS',
          runId: 'run-001',
          correlationId: 'corr-001'
        },
        sequenceId: 11,
        timestamp: '2026-04-10T00:00:11.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Add observability panel',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-observability',
        metadata: {
          intent: 'Add observability panel',
          runId: 'run-002',
          correlationId: 'corr-002'
        },
        sequenceId: 30,
        timestamp: '2026-04-10T00:00:30.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

function createImportedReviewFallbackState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 44,
    hydratedAt: '2026-04-11T09:00:00.000Z',
    agents: {
      'qa-1': {
        id: 'qa-1',
        name: 'Quinn',
        role: 'agent',
        status: 'working',
        roomId: 'qa-room',
        position: { x: 12, y: 6 }
      }
    },
    tasks: {
      'task-auth': {
        id: 'task-auth',
        title: 'Validate auth handoff',
        status: 'review',
        assigneeId: 'qa-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: Validate auth handoff',
        sourceEventType: 'routing',
        traceId: 'trace-host-001',
        taskId: 'task-auth',
        metadata: {
          intent: 'Validate auth handoff',
          missionPack: {
            objective: 'Validate the external auth handoff before merge',
            scope: ['src/auth.ts', 'tests/auth.test.ts'],
            canonicalSources: ['src/auth.ts', 'tests/auth.test.ts'],
            constraints: ['repo-first', 'evidence-before-done'],
            expectedOutput: 'review artifact',
            expectedProof: ['verify://task-auth/handoff', 'artifact://host-bridge/review-001'],
            mode: 'preview'
          },
          runId: 'run-host-001',
          correlationId: 'corr-host-001'
        },
        sequenceId: 40,
        timestamp: '2026-04-11T09:00:40.000Z',
        agentId: 'qa-1'
      }
    ],
    hostBindings: {
      'host-mammouth': {
        sequenceId: 41,
        timestamp: '2026-04-11T09:00:41.000Z',
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
          lastSeenAt: '2026-04-11T09:00:39.000Z'
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
    recentHostReviews: [
      {
        sequenceId: 42,
        timestamp: '2026-04-11T09:00:42.000Z',
        review: {
          reviewId: 'review-001',
          hostId: 'host-mammouth',
          sourceType: 'other',
          subjectRef: 'task:task-auth',
          verdict: 'warn',
          findings: [
            {
              id: 'finding-proof',
              severity: 'medium',
              message: 'Attach the imported review artifact to the governed proof set.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['verify://task-auth/handoff', 'artifact://host-bridge/review-001'],
          importedAt: '2026-04-11T09:00:42.000Z',
          traceId: 'trace-host-001',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'trace-host-001',
          taskId: 'task-auth',
          correlationId: 'corr-host-001',
          hostId: 'host-mammouth'
        }
      }
    ],
    lastErrors: []
  };
}

describe('verification evidence pack view', () => {
  it('builds evidence packs linking action, controls, verdict and attestation', () => {
    const view = createVerificationEvidencePackView(createBaseState());

    expect(view.summary).toEqual({
      packCount: 1,
      attestedCount: 1,
      unattestedCount: 0,
      missingEvidenceCount: 0,
      missionPackLinkedCount: 1,
      missionPackCoveredCount: 1,
      missingExpectedProofCount: 0
    });

    expect(view.packs[0]).toMatchObject({
      missionId: 'mission:task:task-auth',
      taskRef: 'task-auth',
      verificationId: 'verification-record:task-auth',
      verificationRef: 'verify://task-auth/runtime-dashboard',
      actionId: 'task.transition.done',
      status: 'accepted',
      verdict: 'pass',
      controlRefs: ['control://tests:unit', 'control://review:critical-findings'],
      missionPack: {
        objective: 'Ship auth runtime flow with proof',
        mode: 'commit'
      },
      proofCoverage: {
        expectedProofCount: 3,
        coverageRatio: 1,
        fullyCovered: true
      }
    });
    expect(view.packs[0]?.evidence.map((record) => record.evidenceRef)).toContain(
      'tests://grimoire-game/runtime-dashboard#task-auth'
    );
    expect(view.packs[0]?.proofCoverage?.satisfiedExpectedProofRefs).toEqual([
      'control://tests:unit',
      'tests://grimoire-game/runtime-dashboard#task-auth',
      'verify://task-auth/runtime-dashboard'
    ]);
    expect(view.packs[0]?.attestation).toMatchObject({
      verificationId: 'verification-record:task-auth',
      subjectRef: 'task:task-auth',
      type: 'verification_attestation'
    });
  });

  it('supports lookup by mission, verification and evidence reference', () => {
    const view = createVerificationEvidencePackView(createBaseState());

    const query = queryVerificationEvidencePacks(view, {
      missionId: 'mission:task:task-auth',
      verificationRef: 'verify://task-auth/runtime-dashboard',
      evidenceRef: 'tests://grimoire-game/runtime-dashboard#task-auth'
    });

    expect(query.totalCount).toBe(1);
    expect(query.packs[0]?.verificationId).toBe('verification-record:task-auth');
  });

  it('builds verification evidence packs from imported host reviews when workflow metadata is absent', () => {
    const view = createVerificationEvidencePackView(createImportedReviewFallbackState());

    expect(view.summary).toEqual({
      packCount: 1,
      attestedCount: 1,
      unattestedCount: 0,
      missingEvidenceCount: 0,
      missionPackLinkedCount: 1,
      missionPackCoveredCount: 1,
      missingExpectedProofCount: 0
    });

    expect(view.packs[0]).toMatchObject({
      missionId: 'mission:task:task-auth',
      taskRef: 'task-auth',
      verificationId: 'verification-record:task-auth',
      verificationRef: 'verify://task-auth/handoff',
      actionId: 'host.review.import:review-001',
      status: 'needs_work',
      verdict: 'warn',
      controlRefs: ['control://host_review:other'],
      externalReviews: [
        {
          reviewId: 'review-001',
          hostId: 'host-mammouth',
          openFindingCount: 1
        }
      ],
      missionPack: {
        objective: 'Validate the external auth handoff before merge',
        mode: 'preview'
      },
      proofCoverage: {
        expectedProofCount: 2,
        coverageRatio: 1,
        fullyCovered: true
      }
    });
    expect(view.packs[0]?.evidenceRefs).toEqual(
      expect.arrayContaining(['artifact://host-bridge/review-001', 'verify://task-auth/handoff'])
    );
    expect(view.packs[0]?.attestation).toMatchObject({
      verificationId: 'verification-record:task-auth',
      type: 'verification_attestation'
    });
  });

  it('exposes structured proof metadata and linked audit sequence ids when a verification gate is present', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Verification gate PASS',
        detail: 'task.transition.done: verify://task-auth/runtime-dashboard',
        sourceEventType: 'verification_gate',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/runtime-dashboard',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
          typedEvidenceRefs: [{ kind: 'test', ref: 'tests://grimoire-game/runtime-dashboard#task-auth' }],
          verdict: 'PASS',
          correlationId: 'req-proof-auth-1',
          requestId: 'req-proof-auth-1',
          idempotencyKey: 'task-done-auth-1',
          actorId: 'orch-1',
          actorRole: 'orchestrator'
        },
        sequenceId: 13,
        timestamp: '2026-04-10T00:00:13.000Z',
        agentId: 'orch-1'
      }
    ];

    const view = createVerificationEvidencePackView(state);

    expect(view.packs[0]).toMatchObject({
      gateSequenceId: 13,
      correlationId: 'req-proof-auth-1',
      requestId: 'req-proof-auth-1',
      idempotencyKey: 'task-done-auth-1',
      actorId: 'orch-1',
      actorRole: 'orchestrator',
      controlsExecuted: ['tests:unit', 'review:critical-findings'],
      typedEvidenceRefs: [{ kind: 'test', ref: 'tests://grimoire-game/runtime-dashboard#task-auth' }]
    });
    expect(view.packs[0]?.linkedSequenceIds).toEqual([10, 11, 12, 13]);
  });
});