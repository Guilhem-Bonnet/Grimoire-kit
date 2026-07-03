import { createErrorEvent } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createObservabilityPanelView } from '../../src/state/observability-panel-view';

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
          verificationRef: 'verify://task-auth/observability-panel',
          controlsExecuted: ['tests:unit', 'review:critical-findings'],
          evidenceRefs: ['tests://grimoire-game/observability-panel#task-auth'],
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

describe('createObservabilityPanelView', () => {
  it('builds KPI cards, bounded timeline rows and prioritized attention items', () => {
    const panel = createObservabilityPanelView(createBaseState(), {
      maxTimelineRows: 2,
      maxAttentionItems: 3
    });

    expect(panel.metricCards.some((card) => card.id === 'tasks-blocked' && card.status === 'critical' && card.value === 2)).toBe(true);
    expect(panel.timelineRows).toHaveLength(2);
    expect(panel.timelineRows.map((row) => row.sequenceId)).toEqual([...panel.timelineRows.map((row) => row.sequenceId)].sort((left, right) => left - right));
    expect(panel.attentionItems).toHaveLength(3);
    expect(panel.attentionItems[0]).toMatchObject({
      kind: 'runtime_error',
      severity: 'critical'
    });
    expect(panel.source.summary.taskCount).toBe(3);
  });

  it('propagates focus and timeline filters to panel rows and source view', () => {
    const panel = createObservabilityPanelView(createBaseState(), {
      focus: {
        traceId: 'session-001',
        taskId: 'task-auth',
        agentId: 'dev-1'
      },
      timelineFilter: {
        kinds: ['tool_call'],
        query: 'auth.ts'
      },
      maxTimelineRows: 10
    });

    expect(panel.focus).toMatchObject({
      traceId: 'session-001',
      taskId: 'task-auth',
      agentId: 'dev-1'
    });
    expect(panel.timelineRows).toHaveLength(1);
    expect(panel.timelineRows[0]).toMatchObject({
      traceId: 'session-001',
      taskId: 'task-auth',
      agentId: 'dev-1',
      kind: 'tool_call',
      sequenceId: 180
    });
    expect(panel.source.projections.timeline.filter).toMatchObject({
      traceId: 'session-001',
      taskId: 'task-auth',
      agentId: 'dev-1',
      kinds: ['tool_call'],
      query: 'auth.ts'
    });
  });

  it('surfaces connection diagnostics in metric cards and prioritized attention', () => {
    const state = createBaseState();
    state.config = {
      'live.connection.status': 'stale',
      'live.connection.byAgent': {
        'dev-1': {
          status: 'stale',
          found: true,
          path: '/tmp/runtime/.event-log.jsonl',
          parsedLineCount: 9,
          lastDataAt: '2026-04-09T00:03:34.000Z',
          scannedAt: '2026-04-09T00:03:40.000Z',
          staleAfterMs: 5000,
          ageMs: 6000
        },
        'qa-1': {
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

    const panel = createObservabilityPanelView(state, {
      maxAttentionItems: 8
    });

    expect(panel.metricCards.find((card) => card.id === 'connection-status')).toMatchObject({
      value: 2,
      status: 'critical'
    });

    const connectionAttention = panel.attentionItems.filter((item) => item.kind === 'connection_status');
    expect(connectionAttention).toHaveLength(2);
    expect(connectionAttention[0]).toMatchObject({
      severity: 'critical',
      label: 'Connection disconnected: Quinn'
    });
    expect(connectionAttention[1]).toMatchObject({
      severity: 'warning',
      label: 'Connection stale: Amelia'
    });
  });

  it('surfaces architecture escalation as critical attention after three fix_failed outcomes', () => {
    const state = createBaseState();
    state.lastErrors = [];
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

    const panel = createObservabilityPanelView(state, {
      maxAttentionItems: 8
    });

    const escalation = panel.attentionItems.find((item) => item.kind === 'architecture_escalation');
    expect(escalation).toMatchObject({
      severity: 'critical',
      label: 'Architecture review required',
      taskId: 'task-auth',
      traceId: 'session-001',
      sequenceId: 212
    });
    expect(escalation?.detail).toContain('3 consecutive fix_failed');
  });

  it('surfaces OWASP security findings and hotspots as dedicated metric and attention items', () => {
    const state = createBaseState();
    state.lastErrors = [];
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

    const panel = createObservabilityPanelView(state, {
      maxAttentionItems: 8
    });

    expect(panel.metricCards.find((card) => card.id === 'security-findings')).toMatchObject({
      value: 1,
      status: 'critical'
    });

    const securityFinding = panel.attentionItems.find((item) => item.kind === 'security_finding');
    expect(securityFinding).toMatchObject({
      severity: 'critical',
      taskId: 'task-auth',
      traceId: 'session-001'
    });
    expect(securityFinding?.label).toContain('LLM06 Excessive Agency');

    const hotspot = panel.attentionItems.find((item) => item.kind === 'owasp_hotspot');
    expect(hotspot).toMatchObject({
      severity: 'critical',
      label: 'OWASP hotspot: LLM06 Excessive Agency'
    });
  });
});