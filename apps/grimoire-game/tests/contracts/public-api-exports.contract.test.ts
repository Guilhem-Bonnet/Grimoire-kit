import * as gamePackage from '../../src/index';

describe('public package exports', () => {
  it('re-exports advanced runtime projection factories from the package entrypoint', () => {
    expect(typeof gamePackage.createDeepInspectionView).toBe('function');
    expect(typeof gamePackage.createExpertCockpitView).toBe('function');
    expect(typeof gamePackage.createGenericHostBridgeView).toBe('function');
    expect(typeof gamePackage.createRuntimeKernelView).toBe('function');
    expect(typeof gamePackage.createRuntimeObserverView).toBe('function');
    expect(typeof gamePackage.createRuntimeProofDossierView).toBe('function');
    expect(typeof gamePackage.createWorkflowVisualizationView).toBe('function');
  });
});