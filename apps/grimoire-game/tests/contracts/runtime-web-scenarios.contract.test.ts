import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';

describe('runtime web scenarios', () => {
  it('ships fully populated web-facing projections for every demo scenario', () => {
    const data = createRuntimeViewsDemoData();

    expect(data.scenarios.length).toBeGreaterThan(0);

    for (const scenario of data.scenarios) {
      expect(scenario.controlPlane.projectRegistry?.activeProject.projectId).toBe('grimoire-game-web');
      expect(scenario.controlPlane.nodeRegistry?.summary.nodeCount).toBeGreaterThan(0);
      expect(scenario.controlPlane.leaseStore?.summary.leaseCount).toBeGreaterThan(0);
      expect(scenario.spectatorShare.tokenId).toBeTruthy();
      expect(scenario.spectatorShare.shareQuery).toContain(`scenario=${encodeURIComponent(scenario.id)}`);
      expect(scenario.webViews.cockpitView.hosts.length).toBeGreaterThan(0);
      expect(scenario.webViews.cockpitView.proofs.length).toBeGreaterThan(0);
      expect(scenario.webViews.gameUiView.rooms.length).toBeGreaterThan(0);
      expect(scenario.webViews.gameUiView.agents.length).toBeGreaterThan(0);
      expect(scenario.webViews.gameUiView.taskLanes.length).toBeGreaterThan(0);
      expect(scenario.webViews.kernelView.contracts.length).toBeGreaterThan(0);
      expect(scenario.webViews.kernelView.triad.some((panel) => panel.id === 'nodes')).toBe(true);
      expect(scenario.webViews.kernelView.invariants.length).toBeGreaterThan(0);
      expect(scenario.webViews.observabilityView.metricCards.length).toBeGreaterThan(0);
      expect(scenario.webViews.observabilityView.timelineRows.length).toBeGreaterThan(0);
      expect(scenario.webViews.observabilityView.sessions.length).toBeGreaterThan(0);
      expect(scenario.webViews.proofDossierView.gates.length).toBeGreaterThan(0);
      expect(scenario.webViews.proofDossierView.packs.length).toBeGreaterThan(0);
      expect(scenario.webViews.proofDossierView.blockingReasons.length).toBeGreaterThanOrEqual(0);
      expect(scenario.webViews.spectatorView.banner.readOnly).toBe(true);
      expect(scenario.webViews.spectatorView.banner.tokenId).toBe(scenario.spectatorShare.tokenId);
      expect(scenario.webViews.spectatorView.channels.every((channel) => channel.readOnly)).toBe(true);
      expect(scenario.webViews.spectatorView.channels.some((channel) => channel.channel === 'web')).toBe(true);
      expect(scenario.webViews.spectatorView.channels.some((channel) => channel.channel === 'vscode')).toBe(true);
      expect(scenario.webViews.spectatorView.blockedMutations.length).toBeGreaterThan(0);
      expect(scenario.webViews.spectatorView.auditTrail.length).toBeGreaterThan(0);
      expect(scenario.webViews.genericHostBridgeView.channels.some((channel) => channel.channelId === 'browser')).toBe(true);
      expect(scenario.webViews.genericHostBridgeView.channels.some((channel) => channel.channelId === 'vscode')).toBe(true);
      expect(scenario.webViews.genericHostBridgeView.channels.some((channel) => channel.channelId === 'external')).toBe(true);
      expect(scenario.webViews.genericHostBridgeView.dispatchHosts.some((host) => host.hostType === 'claude')).toBe(true);
      expect(scenario.webViews.genericHostBridgeView.dispatchHosts.some((host) => host.hostType === 'mcp')).toBe(true);
      expect(scenario.webViews.genericHostBridgeView.packets.length).toBeGreaterThan(0);
      expect(scenario.webViews.vscodePanelView.connection.transport).toBe('browser-fallback');
      expect(scenario.webViews.vscodePanelView.connection.degraded).toBe(true);
      expect(scenario.webViews.vscodePanelView.commands.some((command) => command.commandId === 'sync')).toBe(true);
      expect(scenario.webViews.vscodePanelView.commands.some((command) => command.commandId === 'focus.task')).toBe(true);
      expect(scenario.webViews.vscodePanelView.taskLanes.length).toBeGreaterThan(0);
      expect(scenario.webViews.vscodePanelView.verificationLanes.length).toBeGreaterThan(0);
      expect(scenario.webViews.observerView.parity.sameTaskCount).toBe(true);
      expect(scenario.webViews.observerView.parity.sameAttentionCount).toBe(true);
      expect(scenario.webViews.workflowView.paths.length).toBeGreaterThan(0);
      expect(scenario.webViews.expertView.cockpit.header.projectId).toBe('grimoire-game-web');
      expect(scenario.webViews.expertView.proof.verificationRef).toBeTruthy();
    }
  });
});
