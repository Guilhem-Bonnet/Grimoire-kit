import { createErrorEvent } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createObservabilityView } from '../../src/state/observability-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 220,
    hydratedAt: '2026-04-09T00:00:00.000Z',
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
        status: 'idle',
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
        status: 'review',
        assigneeId: 'dev-1'
      },
      'task-qa': {
        id: 'task-qa',
        title: 'QA pass',
        status: 'in_progress',
        assigneeId: 'qa-1'
      },
      'task-docs': {
        id: 'task-docs',
        title: 'Update docs',
        status: 'review',
        assigneeId: 'ghost-1'
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'create_file',
        params: { path: 'src/auth.ts', task_id: 'task-auth' },
        sourceEventType: 'artifact_created',
        traceId: 'session-001',
        sequenceId: 180,
        timestamp: '2026-04-09T00:03:00.000Z',
        agentId: 'dev-1'
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
        sequenceId: 120,
        timestamp: '2026-04-09T00:01:00.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Decision recorded',
        detail: 'auth: JWT middleware ready',
        sourceEventType: 'decision',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/observability',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/observability-view#task-auth'],
          verdict: 'PASS'
        },
        sequenceId: 140,
        timestamp: '2026-04-09T00:01:20.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: QA pass',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-qa',
        metadata: { intent: 'QA pass' },
        sequenceId: 200,
        timestamp: '2026-04-09T00:03:20.000Z',
        agentId: 'orch-1'
      }
    ],
    lastErrors: [createErrorEvent(220, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-17', true, '2026-04-09T00:03:40.000Z')]
  };
}

describe('createObservabilityView', () => {
  it('aggregates the runtime projections into a dashboard-ready view', () => {
    const view = createObservabilityView(createBaseState());

    expect(view.protocolVersion).toBe('v1');
    expect(view.summary).toMatchObject({
      sessionCount: 2,
      taskCount: 3,
      blockedTaskCount: 2,
      readyTaskCount: 1
    });
    expect(view.summary.timelineGapCount).toBeGreaterThan(0);
    expect(view.summary.timelineEntryCount).toBe(view.projections.timeline.entries.length);
    expect(view.attention.blockedTaskIds).toEqual(['task-docs', 'task-qa']);
    expect(view.attention.recentErrors).toHaveLength(1);
    expect(view.attention.recentErrors[0]).toMatchObject({ kind: 'runtime_error', level: 'error' });
    expect(view.attention.taskAlerts.find((alert) => alert.taskId === 'task-docs')).toMatchObject({
      codes: ['TASK_ASSIGNEE_MISSING', 'TASK_WITHOUT_ACTIVITY'],
      warningCount: 2
    });
  });

  it('applies focus context to timeline filtering and focus snapshot', () => {
    const view = createObservabilityView(createBaseState(), {
      focus: {
        traceId: 'session-001',
        taskId: 'task-auth',
        agentId: 'dev-1'
      }
    });

    expect(view.projections.timeline.hasActiveFilters).toBe(true);
    expect(view.projections.timeline.filter).toMatchObject({
      traceId: 'session-001',
      taskId: 'task-auth',
      agentId: 'dev-1'
    });
    expect(view.projections.timeline.entries.every((entry) => entry.traceId === 'session-001')).toBe(true);
    expect(view.projections.timeline.entries.every((entry) => entry.taskId === 'task-auth')).toBe(true);
    expect(view.projections.timeline.entries.every((entry) => entry.agentId === 'dev-1')).toBe(true);
    expect(view.focus).toMatchObject({
      traceId: 'session-001',
      taskId: 'task-auth',
      agentId: 'dev-1',
      traceTitle: 'Implement auth',
      taskTitle: 'Implement auth',
      agentName: 'Amelia'
    });
    expect(view.focus.matchingEntryCount).toBe(view.projections.timeline.entries.length);
  });

  it('supports explicit timeline filters, capping and error limits', () => {
    const view = createObservabilityView(createBaseState(), {
      focus: {
        traceId: 'session-001'
      },
      timelineFilter: {
        kinds: ['tool_call'],
        fromSequenceId: 130,
        toSequenceId: 200,
        query: 'auth.ts'
      },
      maxTimelineEntries: 1,
      maxErrorEntries: 1
    });

    expect(view.projections.timeline.filter).toMatchObject({
      traceId: 'session-001',
      kinds: ['tool_call'],
      fromSequenceId: 130,
      toSequenceId: 200,
      query: 'auth.ts'
    });
    expect(view.projections.timeline.entries).toHaveLength(1);
    expect(view.projections.timeline.entries[0]).toMatchObject({
      kind: 'tool_call',
      sequenceId: 180
    });
    expect(view.attention.recentErrors).toHaveLength(1);
  });

  it('projects connection diagnostics from runtime config into summary and attention', () => {
    const state = createBaseState();
    state.config = {
      'live.connection.path': '/tmp/runtime/.event-log.jsonl',
      'live.connection.found': true,
      'live.connection.parsedLineCount': 24,
      'live.connection.lastDataAt': '2026-04-09T00:03:39.000Z',
      'live.connection.scannedAt': '2026-04-09T00:03:40.000Z',
      'live.connection.staleAfterMs': 5000,
      'live.connection.ageMs': 1000,
      'live.connection.status': 'stale',
      'live.connection.byAgent': {
        'dev-1': {
          status: 'stale',
          found: true,
          path: '/tmp/runtime/.event-log.jsonl',
          parsedLineCount: 13,
          lastDataAt: '2026-04-09T00:03:34.000Z',
          scannedAt: '2026-04-09T00:03:40.000Z',
          staleAfterMs: 5000,
          ageMs: 6000
        },
        'qa-1': {
          status: 'live',
          found: true,
          path: '/tmp/runtime/.event-log.jsonl',
          parsedLineCount: 11,
          lastDataAt: '2026-04-09T00:03:39.500Z',
          scannedAt: '2026-04-09T00:03:40.000Z',
          staleAfterMs: 5000,
          ageMs: 500
        },
        'pm-1': {
          status: 'disconnected',
          found: false,
          path: '/tmp/runtime/.event-log.jsonl',
          parsedLineCount: 0,
          lastDataAt: null,
          scannedAt: '2026-04-09T00:03:40.000Z',
          staleAfterMs: 5000,
          ageMs: null
        }
      }
    };

    const view = createObservabilityView(state);

    expect(view.connection).not.toBeNull();
    expect(view.connection).toMatchObject({
      status: 'stale',
      issueCount: 2,
      staleCount: 1,
      disconnectedCount: 1
    });
    expect(view.summary).toMatchObject({
      connectionIssueCount: 2,
      connectionStaleCount: 1,
      connectionDisconnectedCount: 1
    });
    expect(view.attention.connectionIssues).toHaveLength(2);
    expect(view.attention.connectionIssues[0]).toMatchObject({
      agentId: 'pm-1',
      status: 'disconnected'
    });
    expect(view.attention.connectionIssues[1]).toMatchObject({
      agentId: 'dev-1',
      status: 'stale'
    });
  });

  it('flags architecture escalation when three consecutive fix_failed outcomes are detected', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Fix failed #1',
        detail: 'First attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 210,
        timestamp: '2026-04-09T00:03:25.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix failed #2',
        detail: 'Second attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 211,
        timestamp: '2026-04-09T00:03:30.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix failed #3',
        detail: 'Third attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 212,
        timestamp: '2026-04-09T00:03:35.000Z',
        agentId: 'dev-1'
      }
    ];

    const view = createObservabilityView(state);

    expect(view.attention.architectureEscalations).toHaveLength(1);
    expect(view.attention.architectureEscalations[0]).toMatchObject({
      taskId: 'task-auth',
      taskTitle: 'Implement auth',
      consecutiveFixFailures: 3,
      latestFailureSequenceId: 212,
      latestFailureTraceId: 'session-001'
    });
  });

  it('surfaces OWASP-aligned security findings and hotspots in summary and attention', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Security finding recorded',
        detail: 'Missing policy on runtime_config surface',
        sourceEventType: 'security_finding',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: {
          findingId: 'SEC-001',
          title: 'Missing required policy',
          severity: 'critical',
          status: 'open',
          confidenceScore: 9.3,
          exploitScenario: 'Untrusted actor can mutate runtime_config without elevated policy.',
          surfaceId: 'runtime_config',
          missingPolicy: true,
          owaspCategory: 'LLM06:2025 Excessive Agency',
          origin: 'runtime_ui'
        },
        sequenceId: 210,
        timestamp: '2026-04-09T00:03:25.000Z',
        agentId: 'dev-1'
      }
    ];
    state.lastErrors = [];

    const view = createObservabilityView(state);

    expect(view.summary).toMatchObject({
      securityFindingCount: 1,
      securityOpenFindingCount: 1,
      securityBlockingFindingCount: 1,
      securityOwaspHotspotCount: 1
    });
    expect(view.attention.securityFindings).toHaveLength(1);
    expect(view.attention.securityFindings[0]).toMatchObject({
      findingId: 'SEC-001',
      normalizedOwaspCategory: 'LLM06',
      blocksShip: true
    });
    expect(view.attention.securityFindings[0]?.owaspFocusAreas).toContain('excessive_agency');
    expect(view.attention.owaspHotspots).toContainEqual(
      expect.objectContaining({
        focusArea: 'excessive_agency',
        label: 'LLM06 Excessive Agency',
        blockingFindingCount: 1
      })
    );
    expect(view.projections.securityAudit.shipBlocked).toBe(true);
  });

  it('clears architecture escalation when a fix success occurs after previous failures', () => {
    const state = createBaseState();
    state.recentWorkflowSteps = [
      ...state.recentWorkflowSteps,
      {
        step: 'Fix failed #1',
        detail: 'First attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 210,
        timestamp: '2026-04-09T00:03:25.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix failed #2',
        detail: 'Second attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 211,
        timestamp: '2026-04-09T00:03:30.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix failed #3',
        detail: 'Third attempt failed',
        sourceEventType: 'fix_failed',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_failed' },
        sequenceId: 212,
        timestamp: '2026-04-09T00:03:35.000Z',
        agentId: 'dev-1'
      },
      {
        step: 'Fix succeeded',
        detail: 'Patch verified',
        sourceEventType: 'fix_succeeded',
        traceId: 'session-001',
        taskId: 'task-auth',
        metadata: { outcome: 'fix_succeeded' },
        sequenceId: 213,
        timestamp: '2026-04-09T00:03:40.000Z',
        agentId: 'dev-1'
      }
    ];

    const view = createObservabilityView(state);

    expect(view.attention.architectureEscalations).toHaveLength(0);
  });
});