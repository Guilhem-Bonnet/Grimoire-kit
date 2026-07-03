import type { GameState } from '../../src/state/game-state';
import { createErrorEvent } from '../../src/contracts/events';
import {
  createVerificationView,
  evaluateTaskReviewVerificationGate,
  evaluateTaskVerificationGate
} from '../../src/state/verification-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 24,
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
        title: 'Implement auth',
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-ghost': {
        id: 'task-ghost',
        title: 'Ghost review',
        status: 'review',
        assigneeId: 'ghost-agent'
      },
      'task-done-no-proof': {
        id: 'task-done-no-proof',
        title: 'Silent completion',
        status: 'done'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 22,
        timestamp: '2026-04-08T00:00:22.000Z',
        agentId: 'dev-1'
      }
    ],
    hostBindings: {
      'host-claude': {
        sequenceId: 18,
        timestamp: '2026-04-08T00:00:18.000Z',
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
          lastSeenAt: '2026-04-08T00:00:18.000Z'
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
        reason: 'Connector degraded and locked to review-only mode.'
      }
    },
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [
      {
        sequenceId: 21,
        timestamp: '2026-04-08T00:00:21.000Z',
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
              message: 'Connector remains degraded and should stay read-only.',
              resolutionStatus: 'open'
            }
          ],
          linkedEvidenceRefs: ['tests://grimoire-game/auth#jwt-middleware'],
          importedAt: '2026-04-08T00:00:21.000Z',
          traceId: 'session-001',
          taskId: 'task-auth'
        },
        meta: {
          traceId: 'session-001',
          taskId: 'task-auth',
          correlationId: 'corr-auth-001',
          hostId: 'host-claude'
        }
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
        sequenceId: 20,
        timestamp: '2026-04-08T00:00:20.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Implementation finished',
        detail: 'JWT middleware ready for review',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/1',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/auth#jwt-middleware'],
          verdict: 'PASS',
          context: 'JWT middleware implementation is ready for closeout.',
          options: ['merge the middleware', 'hold for more evidence'],
          selectedOption: 'merge the middleware',
          rationale: 'The runtime checks and host review converge on PASS.',
          impact: 'Unlocks authenticated runtime surfaces for the next slice.'
        },
        sequenceId: 23,
        timestamp: '2026-04-08T00:00:23.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Review rerouted',
        detail: 'Ghost review rerouted to another trace',
        sourceEventType: 'routing',
        traceId: 'session-ghost-b',
        taskId: 'task-ghost',
        metadata: { intent: 'Ghost review rerouted' },
        sequenceId: 19,
        timestamp: '2026-04-08T00:00:19.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: []
  };
}

function createCriticalTaskReadyState(includeFinOpsExtract: boolean): GameState {
  const state = createBaseState();
  const authTask = state.tasks['task-auth'];
  if (authTask === undefined) {
    throw new Error('Expected task-auth fixture to exist.');
  }

  return {
    ...state,
    lastSequenceId: includeFinOpsExtract ? 31 : 30,
    agents: {
      ...state.agents,
      'review-1': {
        id: 'review-1',
        name: 'Rodin',
        role: 'agent',
        status: 'working',
        roomId: 'challenge-room',
        position: { x: 12, y: 8 },
        parentId: 'orch-1',
        lastTool: 'semantic_search'
      }
    },
    tasks: {
      ...state.tasks,
      'task-auth': {
        ...authTask,
        priority: 'critical'
      }
    },
    recentToolCalls: [
      ...state.recentToolCalls,
      {
        tool: 'runTests',
        params: {
          task_id: 'task-auth',
          model: 'gpt-5.4',
          tokensUsed: 1_200,
          latencyMs: 900,
          costUsd: 0.024
        },
        sourceEventType: 'test_run',
        traceId: 'session-001',
        sequenceId: 24,
        timestamp: '2026-04-08T00:00:24.000Z',
        agentId: 'dev-1'
      },
      {
        tool: 'semantic_search',
        params: {
          task_id: 'task-auth',
          model: 'gpt-5-mini',
          usage: {
            promptTokens: 200,
            completionTokens: 100
          },
          durationMs: 350
        },
        sourceEventType: 'analysis',
        traceId: 'session-001',
        sequenceId: 25,
        timestamp: '2026-04-08T00:00:25.000Z',
        agentId: 'review-1'
      }
    ],
    recentWorkflowSteps: [
      ...state.recentWorkflowSteps,
      {
        step: 'Presentation opened',
        detail: 'Auth middleware pitch opened in the amphitheatre.',
        sourceEventType: 'challenge_presentation',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'presentation',
          challengeRole: 'presenter',
          linkedTaskIds: ['task-auth']
        },
        sequenceId: 26,
        timestamp: '2026-04-08T00:00:26.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Question asked',
        detail: 'What evidence proves replay stability?',
        sourceEventType: 'challenge_question',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'questions',
          challengeRole: 'reviewer',
          linkedTaskIds: ['task-auth']
        },
        sequenceId: 27,
        timestamp: '2026-04-08T00:00:27.000Z',
        agentId: 'review-1'
      },
      {
        step: 'Critical objection raised',
        detail: 'Add the replay proof before merge.',
        sourceEventType: 'challenge_critique',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'critiques',
          challengeRole: 'critic',
          objectionId: 'obj-replay',
          objectionSeverity: 'high',
          linkedTaskIds: ['task-auth'],
          linkedTraceIds: ['session-001']
        },
        sequenceId: 28,
        timestamp: '2026-04-08T00:00:28.000Z',
        agentId: 'review-1'
      },
      {
        step: 'Vote recorded',
        detail: 'Proceed after the replay proof is attached.',
        sourceEventType: 'challenge_vote',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'vote',
          challengeRole: 'voter',
          vote: 'approve',
          score: 91,
          challengeVerdict: 'approved',
          linkedTaskIds: ['task-auth'],
          linkedObjectionIds: ['obj-replay']
        },
        sequenceId: 29,
        timestamp: '2026-04-08T00:00:29.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Iteration closed',
        detail: 'Replay proof attached, objection resolved, challenge approved.',
        sourceEventType: 'challenge_iteration',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          challengeId: 'challenge-auth-1',
          challengePhase: 'iteration',
          challengeRole: 'moderator',
          challengeVerdict: 'approved',
          resolvedObjectionIds: ['obj-replay'],
          linkedTaskIds: ['task-auth']
        },
        sequenceId: 30,
        timestamp: '2026-04-08T00:00:30.000Z',
        agentId: 'orch-1'
      },
      ...(includeFinOpsExtract
        ? [
            {
              step: 'FinOps extract attached',
              detail: 'Cost, token and latency extract attached to the review proof.',
              sourceEventType: 'decision',
              traceId: 'session-001',
              taskId: 'task-auth',
              metadata: {
                finopsExtractRef: 'finops://task-auth/review-extract-001',
                evidenceRefs: ['finops://task-auth/review-extract-001'],
                tokensUsed: 100,
                latencyMs: 120,
                costUsd: 0.002,
                complexity: 'expert'
              },
              sequenceId: 31,
              timestamp: '2026-04-08T00:00:31.000Z',
              agentId: 'orch-1'
            }
          ]
        : [])
    ]
  };
}

function createCriticalTaskGovernanceState(driftScore: number): GameState {
  const state = createCriticalTaskReadyState(true);

  return {
    ...state,
    lastSequenceId: 33,
    recentToolCalls: [
      ...state.recentToolCalls,
      {
        tool: 'create_file',
        params: {
          task_id: 'task-auth',
          path: '.github/prompts/auth-runtime.prompt.md'
        },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 32,
        timestamp: '2026-04-08T00:00:32.000Z',
        agentId: 'dev-1'
      }
    ],
    recentWorkflowSteps: [
      ...state.recentWorkflowSteps,
      {
        step: 'Governance canary completed',
        detail: 'Prompt/policy candidate replayed against the auth canary suite.',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          governanceChangeDetected: true,
          governanceVersions: [
            {
              artifactType: 'prompt',
              targetRef: 'prompt://auth/runtime',
              baselineVersion: 'prompt/v1',
              candidateVersion: 'prompt/v2'
            },
            {
              artifactType: 'policy',
              targetRef: 'policy://auth/runtime',
              baselineVersion: 'policy/v4',
              candidateVersion: 'policy/v5'
            }
          ],
          canaryReportRef: 'canary://task-auth/governance-001',
          governanceDriftThreshold: 0.2,
          canaryScenarios: [
            {
              scenarioId: 'scenario-block-runtime-config',
              title: 'Block unsafe runtime_config mutation',
              baselineVerdict: 'BLOCK',
              candidateVerdict: 'BLOCK',
              driftScore: 0
            },
            {
              scenarioId: 'scenario-read-audit',
              title: 'Allow read-only audit import',
              baselineVerdict: 'PASS',
              candidateVerdict: driftScore === 0 ? 'PASS' : 'WARN',
              driftScore,
              diagnostic: 'Candidate prompt drift assessment.'
            }
          ]
        },
        sequenceId: 33,
        timestamp: '2026-04-08T00:00:33.000Z',
        agentId: 'orch-1'
      }
    ]
  };
}

function createCriticalTaskRecoveryState(includeProof: boolean): GameState {
  const state = createCriticalTaskReadyState(true);

  return {
    ...state,
    lastSequenceId: 33,
    recentWorkflowSteps: [
      ...state.recentWorkflowSteps,
      {
        step: 'Incident declared',
        detail: 'Websocket transport became unavailable during auth replay.',
        sourceEventType: 'incident',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          incidentType: 'ws_unavailable',
          runbookRef: 'runbook://incident/ws-unavailable/v1'
        },
        sequenceId: 32,
        timestamp: '2026-04-08T00:00:32.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Recovery exercise completed',
        detail: 'Recovery checklist executed and state resynchronized.',
        sourceEventType: 'recovery',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          incidentType: 'ws_unavailable',
          exerciseRef: 'exercise://task-auth/ws-unavailable-001',
          recoveryChecklist: includeProof
            ? ['detection', 'containment', 'recovery', 'verification']
            : ['detection', 'containment', 'recovery'],
          beforeStateRef: 'snapshot://task-auth/before/ws',
          afterStateRef: 'snapshot://task-auth/after/ws',
          ...(includeProof ? { resyncProofRef: 'resync://task-auth/ws-unavailable-001' } : {})
        },
        sequenceId: 33,
        timestamp: '2026-04-08T00:00:33.000Z',
        agentId: 'dev-1'
      }
    ]
  };
}

function createExperimentTaskState(includeDecision: boolean): GameState {
  const state = createBaseState();

  return {
    ...state,
    lastSequenceId: 25,
    recentWorkflowSteps: [
      ...state.recentWorkflowSteps,
      {
        step: 'Experiment closeout recorded',
        detail: 'The auth onboarding hypothesis was measured before closeout.',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'experiment',
          experimentId: 'exp-auth-001',
          experimentTheme: 'onboarding',
          hypothesis: 'Reducing auth friction should improve activation.',
          experimentMetric: 'activation_rate',
          experimentGuardrail: 'support_tickets <= baseline + 2',
          measurementRef: 'measure://task-auth/activation-001',
          ...(includeDecision ? { experimentDecision: 'iterate' } : {})
        },
        sequenceId: 25,
        timestamp: '2026-04-08T00:00:25.000Z',
        agentId: 'dev-1'
      }
    ]
  };
}

describe('verification gate view', () => {
  it('marks tasks with complete verification chain as ready for done transition', () => {
    const gate = evaluateTaskVerificationGate(createBaseState(), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate).toMatchObject({
      taskId: 'task-auth',
      taskStatus: 'review',
      isReadyForDone: true,
      traceCount: 1
    });
    expect(gate?.unmetRequirementCodes).toEqual([]);
    expect(gate?.verificationChain.linkedExternalReviews).toMatchObject([
      {
        reviewId: 'review-claude-001',
        hostId: 'host-claude',
        hostDisplayName: 'Claude Code',
        verdict: 'warn',
        openFindingCount: 1,
        traceId: 'session-001',
        taskId: 'task-auth'
      }
    ]);
  });

  it('blocks tasks that are missing assignment or evidence', () => {
    const ghostGate = evaluateTaskVerificationGate(createBaseState(), 'task-ghost');
    const silentGate = evaluateTaskVerificationGate(createBaseState(), 'task-done-no-proof');

    expect(ghostGate?.isReadyForDone).toBe(false);
    expect(ghostGate?.unmetRequirementCodes).toEqual(expect.arrayContaining([
      'TASK_ASSIGNED',
      'TASK_HAS_ACTION_ID',
      'TASK_HAS_VERIFICATION_REF',
      'TASK_HAS_CONTROLS_EXECUTED',
      'TASK_HAS_EVIDENCE_REFS',
      'TASK_HAS_ACTIONABLE_EVIDENCE'
    ]));

    expect(silentGate?.isReadyForDone).toBe(false);
    expect(silentGate?.unmetRequirementCodes).toEqual(expect.arrayContaining([
      'TASK_ASSIGNED',
      'TASK_HAS_ACTIVITY',
      'TASK_HAS_TRACE',
      'TASK_HAS_ACTION_ID',
      'TASK_HAS_VERIFICATION_REF',
      'TASK_HAS_CONTROLS_EXECUTED',
      'TASK_HAS_EVIDENCE_REFS',
      'TASK_HAS_ACTIONABLE_EVIDENCE'
    ]));
  });

  it('blocks done readiness when an experiment closes without an explicit decision', () => {
    const gate = evaluateTaskVerificationGate(createExperimentTaskState(false), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_EXPERIMENT_DECISION_COMPLETE');
  });

  it('allows done readiness when experiment measurement and decision evidence are complete', () => {
    const gate = evaluateTaskVerificationGate(createExperimentTaskState(true), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_EXPERIMENT_DECISION_COMPLETE');
  });

  it('blocks done readiness when verification chain metadata is incomplete', () => {
    const state = createBaseState();
    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        recentWorkflowSteps: state.recentWorkflowSteps.map((workflowStep) =>
          workflowStep.taskId === 'task-auth'
            ? {
                ...workflowStep,
                metadata: {
                  ...workflowStep.metadata,
                  verificationRef: '',
                  evidenceRefs: []
                }
              }
            : workflowStep
        )
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toEqual(expect.arrayContaining(['TASK_HAS_VERIFICATION_REF', 'TASK_HAS_EVIDENCE_REFS']));
  });

  it('summarizes ready versus blocked tasks for a future verification dashboard', () => {
    const verificationView = createVerificationView(createBaseState());

    expect(verificationView.metrics).toEqual({
      taskCount: 3,
      readyCount: 1,
      blockedCount: 2,
      activeReadyCount: 1
    });
    expect(verificationView.tasks.map((task) => task.taskId)).toEqual([
      'task-done-no-proof',
      'task-ghost',
      'task-auth'
    ]);
  });

  it('blocks done readiness when a critical review finding remains open', () => {
    const state = createBaseState();
    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Critical review finding',
            detail: 'Security regression found in auth flow',
            sourceEventType: 'review',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { severity: 'critical', status: 'open' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_NO_OPEN_CRITICAL_FINDINGS');
  });

  it('does not block done readiness for resolved critical findings', () => {
    const state = createBaseState();
    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Critical review finding resolved',
            detail: 'Security regression fixed',
            sourceEventType: 'review',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { severity: 'critical', status: 'resolved' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          }
        ],
        lastErrors: [
          createErrorEvent(
            25,
            'WS_TIMEOUT',
            'Transient network issue.',
            'req-timeout',
            true,
            '2026-04-08T00:00:25.000Z'
          )
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_NO_OPEN_CRITICAL_FINDINGS');
  });

  it('blocks done readiness when a published blocking security finding remains open', () => {
    const state = createBaseState();
    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Security finding recorded',
            detail: 'Missing policy on runtime_config',
            sourceEventType: 'security_finding',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: {
              findingId: 'SEC-200',
              severity: 'high',
              status: 'open',
              confidenceScore: 9.1,
              surfaceId: 'runtime_config',
              missingPolicy: true,
              exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.'
            },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS');
  });

  it('does not block done readiness for security findings below publish confidence threshold', () => {
    const state = createBaseState();
    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Security finding candidate',
            detail: 'Low confidence signal',
            sourceEventType: 'security_finding',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: {
              findingId: 'SEC-201',
              severity: 'critical',
              status: 'open',
              confidenceScore: 7.6,
              surfaceId: 'runtime_config',
              missingPolicy: true,
              exploitScenario: 'Signal is not yet validated.'
            },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS');
  });

  it('blocks review readiness when FIX_PROPOSED is logged before ROOT_CAUSE_IDENTIFIED', () => {
    const state = createBaseState();
    const gate = evaluateTaskReviewVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Fix proposed too early',
            detail: 'Candidate fix proposed without root cause',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'fix_proposed', topic: 'investigation' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReadyForReview).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED');
    expect(gate?.unmetRequirementCodes).toContain('TASK_DEBUG_PHASE_SEQUENCE_COMPLETE');
  });

  it('blocks review readiness when a critical review finding remains unresolved', () => {
    const state = createBaseState();
    const gate = evaluateTaskReviewVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Root cause identified',
            detail: 'Found the regression source.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'root_cause_identified', topic: 'investigation' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Pattern identified',
            detail: 'Only retried writes fail.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'pattern_identified', topic: 'investigation' },
            sequenceId: 25,
            timestamp: '2026-04-08T00:00:25.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Hypothesis validated',
            detail: 'Race reproduced in harness.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'hypothesis', topic: 'investigation' },
            sequenceId: 26,
            timestamp: '2026-04-08T00:00:26.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Implementation completed',
            detail: 'Candidate fix is ready.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'implementation_completed', topic: 'investigation' },
            sequenceId: 27,
            timestamp: '2026-04-08T00:00:27.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Critical review finding',
            detail: 'Regression still open.',
            sourceEventType: 'review',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { severity: 'critical', status: 'open' },
            sequenceId: 28,
            timestamp: '2026-04-08T00:00:28.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForReview).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_NO_OPEN_CRITICAL_FINDINGS');
  });

  it('blocks review readiness after three consecutive fix_failed events without architecture escalation', () => {
    const state = createBaseState();
    const gate = evaluateTaskReviewVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Root cause identified',
            detail: 'Found the regression source.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'root_cause_identified', topic: 'investigation' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Pattern identified',
            detail: 'Only retried writes fail.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'pattern_identified', topic: 'investigation' },
            sequenceId: 25,
            timestamp: '2026-04-08T00:00:25.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Hypothesis validated',
            detail: 'Race reproduced in harness.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'hypothesis', topic: 'investigation' },
            sequenceId: 26,
            timestamp: '2026-04-08T00:00:26.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Implementation completed',
            detail: 'Candidate fix is ready.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'implementation_completed', topic: 'investigation' },
            sequenceId: 27,
            timestamp: '2026-04-08T00:00:27.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Fix failed 1',
            detail: 'First retry guard failed.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { outcome: 'fix_failed', topic: 'investigation' },
            sequenceId: 28,
            timestamp: '2026-04-08T00:00:28.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Fix failed 2',
            detail: 'Second retry guard failed.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { outcome: 'fix_failed', topic: 'investigation' },
            sequenceId: 29,
            timestamp: '2026-04-08T00:00:29.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Fix failed 3',
            detail: 'Third retry guard failed.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { outcome: 'fix_failed', topic: 'investigation' },
            sequenceId: 30,
            timestamp: '2026-04-08T00:00:30.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForReview).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_ARCHITECTURE_REVIEW_TRIGGERED_AFTER_REPEAT_FIX_FAILURES');
  });

  it('marks review readiness when investigation phases are complete and ordered', () => {
    const state = createBaseState();
    const gate = evaluateTaskReviewVerificationGate(
      {
        ...state,
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Root cause identified',
            detail: 'Found cache invalidation defect',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'root_cause_identified', topic: 'investigation' },
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Pattern identified',
            detail: 'Defect appears on retried requests',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'pattern_identified', topic: 'investigation' },
            sequenceId: 25,
            timestamp: '2026-04-08T00:00:25.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Hypothesis validated',
            detail: 'Token race reproduced in harness',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'hypothesis', topic: 'investigation' },
            sequenceId: 26,
            timestamp: '2026-04-08T00:00:26.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Implementation completed',
            detail: 'Retry guard merged',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'implementation_completed', topic: 'investigation' },
            sequenceId: 27,
            timestamp: '2026-04-08T00:00:27.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Fix proposed',
            detail: 'Ready to move to review',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'fix_proposed', topic: 'investigation' },
            sequenceId: 28,
            timestamp: '2026-04-08T00:00:28.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReadyForReview).toBe(true);
    expect(gate?.unmetRequirementCodes).toEqual([]);
  });

  it('blocks review readiness when obsolete memory recall exceeds the configured threshold', () => {
    const state = createBaseState();
    const gate = evaluateTaskReviewVerificationGate(
      {
        ...state,
        recentHostContextEntries: [
          {
            sequenceId: 24,
            timestamp: '2026-04-08T00:00:24.000Z',
            entry: {
              entryId: 'ctx-auth-obsolete-1',
              hostId: 'host-claude',
              sourceType: 'memory',
              visibility: 'shared',
              confidence: 7.4,
              importedAt: '2026-04-08T00:00:00.000Z',
              ttlSeconds: 10,
              contentRef: 'memory://auth/obsolete-guidance',
              trustStatus: 'review'
            },
            meta: {
              traceId: 'session-001',
              taskId: 'task-auth',
              correlationId: 'corr-auth-001',
              hostId: 'host-claude'
            }
          }
        ],
        recentWorkflowSteps: [
          ...state.recentWorkflowSteps,
          {
            step: 'Root cause identified',
            detail: 'Found stale auth guidance leak',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'root_cause_identified', topic: 'investigation' },
            sequenceId: 25,
            timestamp: '2026-04-08T00:00:25.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Pattern identified',
            detail: 'Obsolete note reused during review.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'pattern_identified', topic: 'investigation' },
            sequenceId: 26,
            timestamp: '2026-04-08T00:00:26.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Hypothesis validated',
            detail: 'Trace confirms memory recall drift.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'hypothesis', topic: 'investigation' },
            sequenceId: 27,
            timestamp: '2026-04-08T00:00:27.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Implementation completed',
            detail: 'Recall instrumentation added.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'implementation_completed', topic: 'investigation' },
            sequenceId: 28,
            timestamp: '2026-04-08T00:00:28.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Fix proposed',
            detail: 'Ready to hand off with recall checks.',
            sourceEventType: 'decision',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: { phase: 'fix_proposed', topic: 'investigation' },
            sequenceId: 29,
            timestamp: '2026-04-08T00:00:29.000Z',
            agentId: 'dev-1'
          },
          {
            step: 'Review imported obsolete guidance',
            detail: 'Review still points at expired memory.',
            sourceEventType: 'review',
            traceId: 'session-001',
            taskId: 'task-auth',
            metadata: {
              memoryAccess: 'read',
              contentRefs: ['memory://auth/obsolete-guidance'],
              correlationId: 'corr-auth-001'
            },
            sequenceId: 30,
            timestamp: '2026-04-08T00:00:30.000Z',
            agentId: 'dev-1'
          }
        ]
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isApplicable).toBe(true);
    expect(gate?.isReadyForReview).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_OBSOLESCENCE_RATE_WITHIN_THRESHOLD');
  });

  it('blocks done readiness for critical tasks without a completed counter-review protocol', () => {
    const state = createBaseState();
    const authTask = state.tasks['task-auth'];
    if (authTask === undefined) {
      throw new Error('Expected task-auth fixture to exist.');
    }

    const gate = evaluateTaskVerificationGate(
      {
        ...state,
        tasks: {
          ...state.tasks,
          'task-auth': {
            ...authTask,
            priority: 'critical'
          }
        }
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_CRITICAL_COUNTER_REVIEW_COMPLETE');
  });

  it('blocks done readiness for critical tasks when the decision card schema is incomplete', () => {
    const criticalState = createCriticalTaskReadyState(true);
    const gate = evaluateTaskVerificationGate(
      {
        ...criticalState,
        recentWorkflowSteps: criticalState.recentWorkflowSteps.map((workflowStep) =>
          workflowStep.sequenceId === 23
            ? {
                ...workflowStep,
                metadata: {
                  ...workflowStep.metadata,
                  impact: ''
                }
              }
            : workflowStep
        )
      },
      'task-auth'
    );

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_CRITICAL_DECISION_CARD_COMPLETE');
  });

  it('blocks done readiness for critical tasks when the FinOps extract is missing', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskReadyState(false), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_CRITICAL_FINOPS_EXTRACT_PRESENT');
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_CRITICAL_COUNTER_REVIEW_COMPLETE');
  });

  it('blocks done readiness for critical tasks when prompt/policy drift exceeds the configured threshold', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskGovernanceState(0.35), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD');
  });

  it('blocks done readiness for critical tasks when incident recovery proof is incomplete', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskRecoveryState(false), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(false);
    expect(gate?.unmetRequirementCodes).toContain('TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE');
  });

  it('allows done readiness for critical tasks when the governance canary stays within threshold', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskGovernanceState(0.05), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD');
  });

  it('allows done readiness for critical tasks when the recovery exercise is complete', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskRecoveryState(true), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE');
  });

  it('allows done readiness for critical tasks once the counter-review protocol and FinOps extract are complete', () => {
    const gate = evaluateTaskVerificationGate(createCriticalTaskReadyState(true), 'task-auth');

    expect(gate).not.toBeNull();
    expect(gate?.isReadyForDone).toBe(true);
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_CRITICAL_COUNTER_REVIEW_COMPLETE');
    expect(gate?.unmetRequirementCodes).not.toContain('TASK_CRITICAL_FINOPS_EXTRACT_PRESENT');
  });
});