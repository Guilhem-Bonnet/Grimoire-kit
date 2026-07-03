import { createRuntimeViewsDemoData } from '../../examples/runtime-views-demo-data';
import { createRuntimeDashboardView } from '../../src/state/runtime-dashboard-view';
import { createRuntimeProofDossierView } from '../../src/state/runtime-proof-dossier-view';

describe('runtime-proof-dossier-view', () => {
  it('projects release gates, evidence packs and blocking reasons into an operator dossier', () => {
    const scenario = createRuntimeViewsDemoData().scenarios.find((candidate) => candidate.id === 'blocked-guardrails');

    expect(scenario).toBeDefined();
    if (scenario === undefined) {
      throw new Error('Expected blocked-guardrails scenario.');
    }

    const dashboard = createRuntimeDashboardView(scenario.state, { observability: {} }, scenario.controlPlane);
    const view = createRuntimeProofDossierView(dashboard);

    expect(view.header.title).toBe('Dossier de preuve');
    expect(view.header.releaseBlocked).toBe(true);
    expect(view.statCards.length).toBeGreaterThanOrEqual(5);
    expect(view.gates.some((gate) => gate.id === 'release-gate')).toBe(true);
    expect(view.gates.some((gate) => gate.id === 'verification-blockers')).toBe(true);
    expect(view.blockingReasons.length).toBeGreaterThan(0);
    expect(view.packs.length).toBeGreaterThan(0);
    expect(view.packs.some((pack) => pack.verificationRef.length > 0)).toBe(true);
    expect(scenario.webViews.proofDossierView.packs).toHaveLength(view.packs.length);
  });
});