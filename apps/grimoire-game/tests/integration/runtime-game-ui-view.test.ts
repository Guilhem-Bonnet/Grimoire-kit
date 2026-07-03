import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createRuntimeGameUiView } from '../../src/state/runtime-game-ui-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

describe('runtime-game-ui-view', () => {
  it('projects rooms, agents and lanes from the same runtime dashboard', () => {
    const scenario = createRuntimeViewsDemoData().scenarios.find((candidate) => candidate.id === 'release-ready');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected release-ready scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const view = createRuntimeGameUiView(dashboard);

    expect(view.header.title).toBe('Command Table');
    expect(view.rooms).toHaveLength(dashboard.board.rooms.length);
    expect(view.agents).toHaveLength(dashboard.board.agents.length);
    expect(view.taskLanes.reduce((count, lane) => count + lane.count, 0)).toBe(dashboard.board.metrics.taskCount);
    expect(view.verificationLanes.reduce((count, lane) => count + lane.count, 0)).toBe(
      dashboard.verificationQueue.metrics.itemCount
    );
    expect(view.securityCards).toHaveLength(dashboard.board.securityCards.length);
    expect(view.focus.taskId).toBe(scenario.webViews.gameUiView.focus.taskId);
    expect(scenario.webViews.gameUiView.rooms).toHaveLength(view.rooms.length);
  });
});