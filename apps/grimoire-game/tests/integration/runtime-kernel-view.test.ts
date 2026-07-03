import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';
import { createRuntimeKernelView } from '../../src/state/runtime-kernel-view';

describe('runtime-kernel-view', () => {
  it('projects the control plane, shared contracts and runtime invariants from the same dashboard', () => {
    const scenario = createRuntimeViewsDemoData().scenarios.find((candidate) => candidate.id === 'release-ready');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected release-ready scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const view = createRuntimeKernelView(dashboard);

    expect(view.header.title).toBe('Noyau Forge');
    expect(view.header.projectId).toBe('grimoire-game-web');
    expect(view.statCards.length).toBeGreaterThanOrEqual(5);
    expect(view.contracts.some((contract) => contract.id === 'runtime-protocol')).toBe(true);
    expect(view.contracts.some((contract) => contract.id === 'project-registry')).toBe(true);
    expect(view.triad.some((panel) => panel.id === 'nodes' && panel.items.length > 0)).toBe(true);
    expect(view.triad.some((panel) => panel.id === 'leases' && panel.items.length > 0)).toBe(true);
    expect(view.triad.some((panel) => panel.id === 'hosts' && panel.items.length > 0)).toBe(true);
    expect(view.invariants.some((invariant) => invariant.id === 'control-plane-linked')).toBe(true);
    expect(view.invariants.some((invariant) => invariant.id === 'proof-linkage')).toBe(true);
    expect(view.causality.length).toBeGreaterThanOrEqual(4);
    expect(scenario.webViews.kernelView.contracts).toHaveLength(view.contracts.length);
  });
});