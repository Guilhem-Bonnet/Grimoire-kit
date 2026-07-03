import {
  BranchFinishDecisionPayloadSchema,
  createBranchFinishDecisionPayload,
  createBranchFinishOptionsPayload,
  createSecurityFindingPayload,
  SecurityFindingPayloadSchema
} from '../../src/contracts/events';

describe('branch finisher and security audit contracts', () => {
  it('accepts published security findings with exploit scenario and governance metadata', () => {
    const payload = createSecurityFindingPayload({
      findingId: 'SEC-001',
      title: 'Missing policy on runtime_config surface',
      severity: 'critical',
      status: 'open',
      confidenceScore: 9.2,
      exploitScenario: 'An unqualified mutation can bypass policy checks and alter runtime config.',
      surfaceId: 'runtime_config',
      origin: 'runtime_ui',
      requiredPolicy: 'elevated',
      trustStatus: 'trusted',
      controls: ['owasp:asvs-v4', 'stride:tampering']
    });

    expect(payload).toMatchObject({
      findingId: 'SEC-001',
      severity: 'critical',
      confidenceScore: 9.2,
      requiredPolicy: 'elevated'
    });
  });

  it('rejects security findings without exploit scenario', () => {
    expect(() =>
      SecurityFindingPayloadSchema.parse({
        findingId: 'SEC-002',
        title: 'Policy gap',
        severity: 'high',
        status: 'open',
        confidenceScore: 8.5,
        exploitScenario: '',
        surfaceId: 'task_lifecycle'
      })
    ).toThrow();
  });

  it('defaults branch finisher options and typed discard confirmation', () => {
    const payload = createBranchFinishOptionsPayload({
      branch: 'feature/security-audit',
      testsPassed: true
    });

    expect(payload.allowedOptions).toEqual(['merge', 'pr', 'keep', 'discard']);
    expect(payload.typedDiscardConfirmation).toBe('DISCARD');
  });

  it('rejects branch finisher decisions with unsupported option', () => {
    expect(() =>
      BranchFinishDecisionPayloadSchema.parse({
        branch: 'feature/security-audit',
        selectedOption: 'ship',
        typedConfirmation: 'DISCARD'
      })
    ).toThrow();
  });

  it('accepts discard decisions with typed confirmation payload', () => {
    const payload = createBranchFinishDecisionPayload({
      branch: 'feature/security-audit',
      selectedOption: 'discard',
      typedConfirmation: 'DISCARD'
    });

    expect(payload).toEqual({
      branch: 'feature/security-audit',
      selectedOption: 'discard',
      typedConfirmation: 'DISCARD'
    });
  });
});