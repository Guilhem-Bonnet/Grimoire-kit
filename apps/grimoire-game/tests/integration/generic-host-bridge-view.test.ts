import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createGenericHostBridgeView } from '../../src/state/generic-host-bridge-view';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';

describe('generic-host-bridge-view', () => {
  it('projects browser, VS Code and external hosts on the same runtime truth', () => {
    const scenario = createRuntimeViewsDemoData('release-ready').scenarios.find((candidate) => candidate.id === 'release-ready');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected release-ready scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const hostBridgeView = createGenericHostBridgeView(dashboard);

    expect(hostBridgeView.header.tone).toBe('critical');
    expect(hostBridgeView.focus).toMatchObject({
      projectId: 'grimoire-game-web',
      taskId: 'task-power',
      traceId: 'trace-runtime-web'
    });
    expect(hostBridgeView.channels).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ channelId: 'browser', status: 'ready' }),
        expect.objectContaining({ channelId: 'vscode', status: 'ready' }),
        expect.objectContaining({ channelId: 'external', status: 'ready' })
      ])
    );
    expect(hostBridgeView.dispatchHosts).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ hostType: 'copilot' }),
        expect.objectContaining({ hostType: 'claude' }),
        expect.objectContaining({ hostType: 'mcp' })
      ])
    );
    expect(hostBridgeView.packets.length).toBeGreaterThan(0);
    expect(hostBridgeView.packets[0]).toMatchObject({
      taskId: 'task-power'
    });
  });
});