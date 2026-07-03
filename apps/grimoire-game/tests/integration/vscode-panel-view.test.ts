import { createVsCodePanelBridge } from '../../src/bridge/vscode-webview-bridge';
import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';
import { createVsCodePanelView } from '../../src/state/vscode-panel-view';

describe('vscode-panel-view', () => {
  it('projects the same runtime dashboard as a bounded IDE panel', () => {
    const scenario = createRuntimeViewsDemoData('release-ready').scenarios.find((candidate) => candidate.id === 'release-ready');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected release-ready scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const panelView = createVsCodePanelView(dashboard, { transport: 'vscode-webview' });

    expect(panelView.connection).toMatchObject({
      transport: 'vscode-webview',
      degraded: false
    });
    expect(panelView.focus.taskId).toBe(dashboard.observability.focus.taskId ?? panelView.focus.taskId);
    expect(panelView.taskLanes.length).toBeGreaterThan(0);
    expect(panelView.verificationLanes.length).toBeGreaterThan(0);
    expect(panelView.hosts.length).toBeGreaterThan(0);
    expect(panelView.commands).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ commandId: 'focus.task' }),
        expect.objectContaining({ commandId: 'open.verification' }),
        expect.objectContaining({ commandId: 'sync', enabled: true })
      ])
    );
  });

  it('degrades cleanly outside VS Code and only posts messages when the host exists', () => {
    const postedMessages: unknown[] = [];
    let persistedState: unknown = {
      scenarioId: 'release-ready',
      filter: 'blocked',
      mode: 'host-bridge'
    };
    const hostBridge = createVsCodePanelBridge({
      postMessage(message) {
        postedMessages.push(message);
      },
      getState() {
        return persistedState;
      },
      setState(nextState) {
        persistedState = nextState;
      }
    });
    const browserBridge = createVsCodePanelBridge(null);

    expect(hostBridge.restoreState()).toEqual({
      scenarioId: 'release-ready',
      filter: 'blocked',
      mode: 'host-bridge'
    });
    expect(hostBridge.postCommand({ command: 'sync' })).toBe(true);
    expect(postedMessages).toHaveLength(1);
    expect(postedMessages[0]).toMatchObject({
      type: 'grimoire.vscode-panel.command',
      payload: { command: 'sync' }
    });

    expect(browserBridge.transport).toBe('browser-fallback');
    expect(browserBridge.degraded).toBe(true);
    expect(browserBridge.postCommand({ command: 'sync' })).toBe(false);
  });
});