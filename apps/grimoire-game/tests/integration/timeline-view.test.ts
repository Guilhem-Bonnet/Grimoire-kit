import { createErrorEvent } from '../../src/contracts/events';
import type { GameState } from '../../src/state/game-state';
import { createTimelineView } from '../../src/state/timeline-view';

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
      }
    },
    config: {},
    recentToolCalls: [
      {
        tool: 'runTests',
        params: { task_id: 'task-qa', query: 'qa pass' },
        sourceEventType: 'verification',
        traceId: 'session-002',
        sequenceId: 160,
        timestamp: '2026-04-09T00:01:40.000Z',
        agentId: 'qa-1'
      },
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
        sequenceId: 100,
        timestamp: '2026-04-09T00:00:40.000Z',
        agentId: 'orch-1'
      },
      {
        step: 'Routing dispatch',
        detail: 'Intent routed: QA pass',
        sourceEventType: 'routing',
        traceId: 'session-002',
        taskId: 'task-qa',
        metadata: { intent: 'QA pass' },
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
        metadata: { topic: 'auth' },
        sequenceId: 140,
        timestamp: '2026-04-09T00:01:20.000Z',
        agentId: 'dev-1'
      }
    ],
    lastErrors: [createErrorEvent(220, 'WS_TIMEOUT', 'Lost connection to runtime.', 'req-17', true, '2026-04-09T00:03:40.000Z')]
  };
}

describe('createTimelineView', () => {
  it('builds a chronological timeline with stable metrics', () => {
    const view = createTimelineView(createBaseState());
    const sequenceIds = view.entries.map((entry) => entry.sequenceId);

    expect(sequenceIds).toEqual([...sequenceIds].sort((left, right) => left - right));
    expect(view.metrics.totalCount).toBe(view.metrics.filteredCount);
    expect(view.metrics.earliestSequenceId).toBe(100);
    expect(view.metrics.latestSequenceId).toBe(220);
    expect(view.metrics.errorCount).toBeGreaterThanOrEqual(1);
    expect(view.metrics.infoCount).toBeGreaterThanOrEqual(1);
  });

  it('detects sequence gaps for timeline scrubbers', () => {
    const view = createTimelineView(createBaseState());

    expect(view.gaps.length).toBeGreaterThan(0);
    expect(view.gaps.some((gap) => gap.missingCount >= 10)).toBe(true);
    expect(view.metrics.missingSequenceCount).toBe(view.gaps.reduce((sum, gap) => sum + gap.missingCount, 0));
    expect(view.metrics.gapCount).toBe(view.gaps.length);
  });

  it('filters by trace, kind and sequence range', () => {
    const view = createTimelineView(createBaseState(), {
      traceId: 'session-001',
      kinds: ['tool_call'],
      fromSequenceId: 150,
      toSequenceId: 200,
      query: 'auth.ts'
    });

    expect(view.hasActiveFilters).toBe(true);
    expect(view.entries).toHaveLength(1);
    expect(view.entries[0]).toMatchObject({
      kind: 'tool_call',
      traceId: 'session-001',
      sequenceId: 180
    });
  });

  it('supports capped windows while preserving chronology', () => {
    const view = createTimelineView(createBaseState(), {}, { maxEntries: 2 });
    const sequenceIds = view.entries.map((entry) => entry.sequenceId);

    expect(view.entries).toHaveLength(2);
    expect(sequenceIds).toEqual([...sequenceIds].sort((left, right) => left - right));
    expect(view.entries.at(-1)?.sequenceId).toBe(220);
    expect(view.metrics.filteredCount).toBe(2);
    expect(view.metrics.totalCount).toBeGreaterThanOrEqual(view.metrics.filteredCount);
  });
});
