import {
  createSurfaceExecutionRegistry,
  SurfaceExecutionRecordSchema,
  SurfaceGovernanceRegistrySchema
} from '../../src/contracts/events';

describe('surface governance contracts', () => {
  it('exposes a canonical governed surface registry for runtime scope', () => {
    const registry = createSurfaceExecutionRegistry();

    expect(registry).toEqual([
      {
        surface: 'runtime_config',
        origin: 'runtime_ui',
        requiredPolicy: 'elevated',
        trustStatus: 'trusted'
      },
      {
        surface: 'task_lifecycle',
        origin: 'runtime_ui',
        requiredPolicy: 'surface_scoped',
        trustStatus: 'trusted'
      },
      {
        surface: 'task_assignment',
        origin: 'runtime_ui',
        requiredPolicy: 'surface_scoped',
        trustStatus: 'trusted'
      },
      {
        surface: 'agent_presence',
        origin: 'runtime_ui',
        requiredPolicy: 'surface_scoped',
        trustStatus: 'trusted'
      }
    ]);
    expect(() => SurfaceGovernanceRegistrySchema.parse(registry)).not.toThrow();
  });

  it('accepts blocked trust status records for fail-closed governance decisions', () => {
    expect(() =>
      SurfaceExecutionRecordSchema.parse({
        surface: 'runtime_config',
        origin: 'runtime_ui',
        requiredPolicy: 'elevated',
        trustStatus: 'blocked'
      })
    ).not.toThrow();
  });

  it('rejects malformed records missing origin or requiredPolicy', () => {
    expect(() =>
      SurfaceExecutionRecordSchema.parse({
        surface: 'runtime_config',
        requiredPolicy: 'elevated',
        trustStatus: 'trusted'
      })
    ).toThrow();

    expect(() =>
      SurfaceExecutionRecordSchema.parse({
        surface: 'runtime_config',
        origin: 'runtime_ui',
        trustStatus: 'trusted'
      })
    ).toThrow();
  });
});
