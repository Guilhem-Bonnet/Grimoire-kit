import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import { createTimelineView } from '../../src/state/timeline-view';

function createLargeState(entryCount = 4000): GameState {
  const recentWorkflowSteps: WorkflowStepLogEntry[] = Array.from({ length: entryCount }, (_, index) => {
    const sequenceId = index * 2 + 2;

    return {
      step: 'Stream tick',
      detail: `Timeline frame ${index}`,
      sourceEventType: 'stream_tick',
      traceId: 'session-load',
      taskId: 'task-load',
      metadata: { frame: index },
      sequenceId,
      timestamp: new Date(Date.UTC(2026, 3, 9, 0, 0, index)).toISOString(),
      agentId: 'dev-1'
    };
  });

  return {
    protocolVersion: 'v1',
    lastSequenceId: recentWorkflowSteps[recentWorkflowSteps.length - 1]?.sequenceId ?? -1,
    hydratedAt: '2026-04-09T00:00:00.000Z',
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
      'task-load': {
        id: 'task-load',
        title: 'Load test stream',
        status: 'in_progress',
        assigneeId: 'dev-1'
      }
    },
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps,
    lastErrors: []
  };
}

describe('createTimelineView windowing', () => {
  it('keeps bounded windows and stable metrics on large streams', () => {
    const state = createLargeState();
    const view = createTimelineView(state, {}, { maxEntries: 120 });

    expect(view.entries).toHaveLength(120);
    expect(view.metrics.totalCount).toBeGreaterThanOrEqual(4000);
    expect(view.metrics.filteredCount).toBe(120);
    expect(view.metrics.latestSequenceId).toBe(state.lastSequenceId);
    expect(view.metrics.earliestSequenceId).toBe(view.entries[0]?.sequenceId ?? null);
    expect(view.gaps.length).toBeGreaterThan(0);
    expect(view.metrics.missingSequenceCount).toBe(view.gaps.reduce((sum, gap) => sum + gap.missingCount, 0));
    expect(view.entries.map((entry) => entry.sequenceId)).toEqual([...view.entries.map((entry) => entry.sequenceId)].sort((left, right) => left - right));
  });
});