import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createRuntimeObservabilitySurfaceView } from '../../src/state/runtime-observability-surface-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

describe('runtime-observability-surface-view', () => {
  it('projects observability metrics, blockers and connection issues from the dashboard', () => {
    const scenario = createRuntimeViewsDemoData().scenarios.find((candidate) => candidate.id === 'blocked-guardrails');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected blocked-guardrails scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const view = createRuntimeObservabilitySurfaceView(dashboard);
    const expectedBlockedTaskCount = dashboard.observability.source.projections.verification.tasks.filter(
      (task) => !task.isReadyForDone
    ).length;

    expect(view.header.title).toBe('Observability deck');
    expect(view.metricCards).toHaveLength(dashboard.observability.metricCards.length);
    expect(view.timelineRows).toHaveLength(dashboard.observability.timelineRows.length);
    expect(view.attentionItems.length).toBeGreaterThan(0);
    expect(view.blockedTasks).toHaveLength(expectedBlockedTaskCount);
    expect(view.connectionIssues).toHaveLength(dashboard.observability.source.connection?.issues.length ?? 0);
    expect(view.sessions.length).toBeGreaterThan(0);
    expect(view.focus.taskId).toBe(scenario.webViews.observabilityView.focus.taskId);
  });
});