import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import { createBranchFinisherView, createSecurityAuditView } from '../../src/state/branch-finisher-view';

function createState(
  recentWorkflowSteps: readonly WorkflowStepLogEntry[],
  options: {
    agents?: GameState['agents'];
    tasks?: GameState['tasks'];
    recentToolCalls?: GameState['recentToolCalls'];
  } = {}
): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 220,
    hydratedAt: '2026-04-09T00:00:00.000Z',
    agents: options.agents ?? {},
    tasks: options.tasks ?? {},
    config: {},
    recentToolCalls: options.recentToolCalls ?? [],
    recentWorkflowSteps,
    lastErrors: []
  };
}

describe('branch finisher and security audit projection', () => {
  it('publishes high-confidence findings and blocks ship on critical or governance gaps', () => {
    const state = createState([
      {
        step: 'Security finding recorded',
        detail: 'Critical missing policy',
        sourceEventType: 'security_finding',
        metadata: {
          findingId: 'SEC-001',
          title: 'Missing required policy',
          severity: 'critical',
          status: 'open',
          confidenceScore: 9.4,
          exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.',
          surfaceId: 'runtime_config',
          missingPolicy: true,
          owaspCategory: 'LLM06:2025 Excessive Agency',
          origin: 'runtime_ui',
          controls: ['owasp:asvs-v4']
        },
        sequenceId: 210,
        timestamp: '2026-04-09T00:05:10.000Z',
        traceId: 'trace-sec-001'
      },
      {
        step: 'Security finding recorded',
        detail: 'Low confidence item',
        sourceEventType: 'security_finding',
        metadata: {
          findingId: 'SEC-002',
          title: 'Potential disclosure',
          severity: 'high',
          status: 'open',
          confidenceScore: 7.2,
          exploitScenario: 'Incomplete data sanitization could leak metadata.',
          surfaceId: 'task_lifecycle',
          requiredPolicy: 'surface_scoped'
        },
        sequenceId: 211,
        timestamp: '2026-04-09T00:05:11.000Z'
      },
      {
        step: 'Security finding recorded',
        detail: 'Resolved finding',
        sourceEventType: 'security_finding',
        metadata: {
          findingId: 'SEC-003',
          title: 'Resolved trust mismatch',
          severity: 'high',
          status: 'resolved',
          confidenceScore: 9.1,
          exploitScenario: 'Resolved after trust downgrade control was added.',
          surfaceId: 'agent_presence',
          requiredPolicy: 'surface_scoped',
          origin: 'runtime_adapter'
        },
        sequenceId: 212,
        timestamp: '2026-04-09T00:05:12.000Z'
      }
    ]);

    const securityAudit = createSecurityAuditView(state);

    expect(securityAudit.metrics.totalFindingCount).toBe(3);
    expect(securityAudit.metrics.publishedFindingCount).toBe(2);
    expect(securityAudit.metrics.openFindingCount).toBe(1);
    expect(securityAudit.shipBlocked).toBe(true);
    expect(securityAudit.blockingReasons).toContain('Critical security finding is still open.');
    expect(securityAudit.blockingReasons).toContain('Surface runtime_config is missing required policy.');
    expect(securityAudit.findings[0]?.owaspFocusAreas).toContain('excessive_agency');
    expect(securityAudit.owaspCategories).toContainEqual(
      expect.objectContaining({
        category: 'LLM06',
        label: 'LLM06 Excessive Agency',
        blockingFindingCount: 1
      })
    );
    expect(securityAudit.owaspFocusAreas).toContainEqual(
      expect.objectContaining({
        focusArea: 'excessive_agency',
        label: 'LLM06 Excessive Agency',
        explicitCategoryCount: 1,
        derivedSignalCount: 0,
        blockingFindingCount: 1
      })
    );
    expect(securityAudit.metrics).toMatchObject({
      policyGapCount: 1,
      provenanceGapCount: 0,
      trustBlockedCount: 0,
      owaspCategorizedFindingCount: 1,
      owaspFocusAreaCount: 1
    });
    expect(securityAudit.kanbanCards).toHaveLength(1);
    expect(securityAudit.kanbanCards[0]).toMatchObject({
      findingId: 'SEC-001',
      status: 'review',
      blocksShip: true,
      surfaceId: 'runtime_config'
    });
    expect(securityAudit.kanbanCards[0]?.detail).toContain('Untrusted actor can mutate runtime_config');
  });

  it('applies branch finisher option matrix and typed discard confirmation', () => {
    const state = createState([
      {
        step: 'Security finding recorded',
        detail: 'Critical missing policy',
        sourceEventType: 'security_finding',
        metadata: {
          findingId: 'SEC-001',
          title: 'Missing required policy',
          severity: 'critical',
          status: 'open',
          confidenceScore: 9.4,
          exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.',
          surfaceId: 'runtime_config',
          missingPolicy: true,
          owaspCategory: 'LLM06:2025 Excessive Agency',
          origin: 'runtime_ui'
        },
        sequenceId: 210,
        timestamp: '2026-04-09T00:05:10.000Z'
      },
      {
        step: 'Branch finisher options updated',
        detail: 'feature/security-audit',
        sourceEventType: 'branch_finish_options',
        metadata: {
          branch: 'feature/security-audit',
          testsPassed: true,
          allowedOptions: ['merge', 'pr', 'keep', 'discard'],
          typedDiscardConfirmation: 'DROP-BRANCH'
        },
        sequenceId: 215,
        timestamp: '2026-04-09T00:05:15.000Z'
      },
      {
        step: 'Branch finisher decision proposed',
        detail: 'feature/security-audit: discard',
        sourceEventType: 'branch_finish_decision',
        metadata: {
          branch: 'feature/security-audit',
          selectedOption: 'discard',
          typedConfirmation: 'DISCARD'
        },
        sequenceId: 216,
        timestamp: '2026-04-09T00:05:16.000Z'
      }
    ]);

    const branchFinisher = createBranchFinisherView(state);
    const optionByName = Object.fromEntries(branchFinisher.options.map((option) => [option.option, option]));

    expect(branchFinisher.branch).toBe('feature/security-audit');
    expect(branchFinisher.testsPassed).toBe(true);
    expect(branchFinisher.shipBlocked).toBe(true);
    expect(optionByName.merge?.allowed).toBe(false);
    expect(optionByName.merge?.blockedReasons).toContain('Security audit has unresolved blocking findings.');
    expect(optionByName.pr?.allowed).toBe(false);
    expect(optionByName.keep?.allowed).toBe(true);
    expect(optionByName.discard?.allowed).toBe(true);
    expect(optionByName.discard?.requiresTypedConfirmation).toBe(true);
    expect(optionByName.discard?.requiredTypedConfirmation).toBe('DROP-BRANCH');
    expect(branchFinisher.latestDecision?.allowed).toBe(false);
    expect(branchFinisher.latestDecision?.blockedReasons).toContain(
      'Discard option requires an exact typed confirmation.'
    );
  });

  it('blocks destructive options when tests did not pass', () => {
    const state = createState([
      {
        step: 'Branch finisher options updated',
        detail: 'feature/no-tests',
        sourceEventType: 'branch_finish_options',
        metadata: {
          branch: 'feature/no-tests',
          testsPassed: false,
          allowedOptions: ['merge', 'pr', 'keep', 'discard']
        },
        sequenceId: 300,
        timestamp: '2026-04-09T00:08:00.000Z'
      }
    ]);

    const branchFinisher = createBranchFinisherView(state);
    const optionByName = Object.fromEntries(branchFinisher.options.map((option) => [option.option, option]));

    expect(branchFinisher.shipBlocked).toBe(false);
    expect(optionByName.merge?.allowed).toBe(false);
    expect(optionByName.pr?.allowed).toBe(false);
    expect(optionByName.discard?.allowed).toBe(false);
    expect(optionByName.keep?.allowed).toBe(true);
    expect(optionByName.discard?.blockedReasons).toContain(
      'Tests must pass before destructive branch closure actions.'
    );
  });

  it('summarizes verification queue and evidence pack readiness for branch closure surfaces', () => {
    const state = createState(
      [
        {
          step: 'Decision recorded',
          detail: 'auth: JWT middleware ready',
          sourceEventType: 'decision',
          traceId: 'trace-auth',
          taskId: 'task-auth',
          metadata: {
            topic: 'auth',
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-auth/runtime-dashboard',
            controlsExecuted: ['tests:unit', 'review:critical-findings'],
            evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-auth'],
            verdict: 'PASS'
          },
          sequenceId: 410,
          timestamp: '2026-04-09T00:10:10.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Decision recorded',
          detail: 'security branch rejected',
          sourceEventType: 'decision',
          traceId: 'trace-rejected',
          taskId: 'task-rejected',
          metadata: {
            topic: 'security',
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-rejected/runtime-dashboard',
            controlsExecuted: ['tests:integration', 'review:critical-findings'],
            evidenceRefs: ['tests://grimoire-game/runtime-dashboard#task-rejected'],
            verdict: 'FAIL'
          },
          sequenceId: 411,
          timestamp: '2026-04-09T00:10:11.000Z',
          agentId: 'dev-1'
        },
        {
          step: 'Fix proposed too early',
          detail: 'Patched without root cause',
          sourceEventType: 'decision',
          traceId: 'trace-needs-work',
          taskId: 'task-needs-work',
          metadata: { phase: 'fix_proposed' },
          sequenceId: 412,
          timestamp: '2026-04-09T00:10:12.000Z',
          agentId: 'dev-1'
        }
      ],
      {
        agents: {
          'dev-1': {
            id: 'dev-1',
            name: 'Amelia',
            role: 'agent',
            status: 'working',
            roomId: 'build-room',
            position: { x: 8, y: 8 }
          }
        },
        tasks: {
          'task-auth': {
            id: 'task-auth',
            title: 'Implement auth',
            status: 'review',
            assigneeId: 'dev-1'
          },
          'task-rejected': {
            id: 'task-rejected',
            title: 'Close insecure branch',
            status: 'done',
            assigneeId: 'dev-1'
          },
          'task-needs-work': {
            id: 'task-needs-work',
            title: 'Patch verification gaps',
            status: 'review',
            assigneeId: 'dev-1'
          }
        }
      }
    );

    const branchFinisher = createBranchFinisherView(state);

    expect(branchFinisher.verification).toMatchObject({
      queueCount: 3,
      verifyingCount: 1,
      rejectedCount: 1,
      needsWorkCount: 1,
      blockingItemCount: 2,
      evidencePackCount: 2,
      attestedPackCount: 2,
      unattestedPackCount: 0,
      missingEvidencePackCount: 0
    });
    expect(branchFinisher.verification.blockingTaskIds).toEqual(['task-rejected', 'task-needs-work']);
    expect(branchFinisher.verification.blockingReasons).toContain(
      'Task Close insecure branch is rejected in verification.'
    );
    expect(branchFinisher.verification.blockingReasons).toContain(
      'Task Patch verification gaps still needs work before verification can complete.'
    );
  });
});