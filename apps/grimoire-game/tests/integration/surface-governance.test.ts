import { MockAgentAdapter } from '../../src/bridge/agent-adapter';
import { createConfigUpdate, RUNTIME_PROTOCOL_VERSION, type GameStateSnapshot } from '../../src/contracts/events';

const initialSnapshot: GameStateSnapshot = {
  protocolVersion: RUNTIME_PROTOCOL_VERSION,
  generatedAt: '2026-04-08T00:00:00.000Z',
  lastSequenceId: 0,
  agents: [],
  tasks: [],
  config: {},
  recentToolCalls: [],
  recentWorkflowSteps: []
};

describe('surface governance fail-closed runtime', () => {
  it('blocks governed mutations when trustStatus is blocked and records an auth rejection audit', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);
    const event = createConfigUpdate('req-surface-blocked', 'hud.theme', 'paper', 'cfg-surface-blocked', {
      trustLevel: 'blocked'
    });

    const response = await adapter.handleClientEvent(event, {
      principalId: 'orch-1',
      role: 'orchestrator'
    });

    expect(response).toHaveLength(1);
    expect(response[0]?.type).toBe('ERROR');
    if (response[0]?.type === 'ERROR') {
      expect(response[0].code).toBe('FORBIDDEN');
      expect(response[0].message).toBe('Governed surface runtime_config is blocked by trust status policy.');
      expect(response[0].correlationId).toBe('req-surface-blocked');
    }

    const rejection = adapter
      .getAuditLog()
      .find((entry) => entry.type === 'AUTH_REJECTED' && entry.requestId === 'req-surface-blocked');
    expect(rejection?.detail).toBe('Governed surface runtime_config is blocked by trust status policy.');
  });

  it('blocks governed mutations when required policy metadata is missing', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);

    const response = await adapter.handleClientEvent(
      {
        type: 'CONFIG_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-surface-missing-policy',
        key: 'hud.theme',
        value: 'paper',
        idempotencyKey: 'cfg-surface-missing-policy',
        guardrail: {
          surface: 'runtime_config',
          trustLevel: 'trusted',
          provenance: {
            source: 'runtime_ui',
            actorTag: 'config.update'
          }
        }
      } as never,
      {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    );

    expect(response).toHaveLength(1);
    expect(response[0]?.type).toBe('ERROR');
    if (response[0]?.type === 'ERROR') {
      expect(response[0].code).toBe('FORBIDDEN');
      expect(response[0].message).toBe('Mutation CONFIG_UPDATE is missing required policy metadata.');
      expect(response[0].correlationId).toBe('req-surface-missing-policy');
    }
  });

  it('blocks critical governed mutations when verification metadata is missing', async () => {
    const adapter = new MockAgentAdapter(initialSnapshot);

    const response = await adapter.handleClientEvent(
      {
        type: 'CONFIG_UPDATE',
        version: RUNTIME_PROTOCOL_VERSION,
        requestId: 'req-surface-missing-verification',
        key: 'hud.theme',
        value: 'paper',
        idempotencyKey: 'cfg-surface-missing-verification',
        guardrail: {
          surface: 'runtime_config',
          policy: 'elevated',
          trustLevel: 'trusted',
          provenance: {
            source: 'runtime_ui',
            actorTag: 'config.update'
          }
        }
      } as never,
      {
        principalId: 'orch-1',
        role: 'orchestrator'
      }
    );

    expect(response).toHaveLength(1);
    expect(response[0]?.type).toBe('ERROR');
    if (response[0]?.type === 'ERROR') {
      expect(response[0].code).toBe('FORBIDDEN');
      expect(response[0].message).toBe(
        'Critical mutation CONFIG_UPDATE is missing verification metadata required for critical governed config mutation.'
      );
      expect(response[0].correlationId).toBe('req-surface-missing-verification');
    }
  });
});
