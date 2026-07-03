import {
  HOST_BRIDGE_POLICY_REF,
  evaluateHostInvocationPolicy
} from '../../src/bridge/host-invocation-policy';
import type {
  CapabilityManifest,
  HostBinding,
  InvocationEnvelope
} from '../../src/contracts/events';

const HOST_BINDING: HostBinding = {
  hostId: 'host-copilot',
  hostType: 'copilot',
  displayName: 'GitHub Copilot',
  authMode: 'oauth',
  connectionState: 'online',
  trustStatus: 'trusted',
  scopes: ['fs', 'exec', 'config_write'],
  capabilityManifestRef: 'manifest-copilot',
  sourceOfTruth: 'secondary'
};

const HOST_MANIFEST: CapabilityManifest = {
  manifestId: 'manifest-copilot',
  hostId: 'host-copilot',
  routines: ['code-review'],
  toolProviders: ['github-mcp'],
  reviewChannels: ['pull-request-review'],
  contextSources: ['selection'],
  permissionMode: 'policy',
  supportsStreaming: true,
  supportsReviewImport: true,
  supportsContextImport: true,
  supportsPreviewCommit: true
};

const BASE_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-001',
  hostId: 'host-copilot',
  actionKind: 'tool_call',
  mode: 'preview',
  correlationId: 'corr-001',
  idempotencyKey: 'idem-001',
  traceId: 'session-host-001',
  taskId: 'task-auth',
  requestedScopes: ['fs'],
  payload: {
    tool: 'semantic_search'
  },
  evidencePolicy: 'basic'
};

describe('host invocation policy', () => {
  it('allows trusted online hosts when scopes and capabilities match', () => {
    const decision = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, BASE_ENVELOPE);

    expect(decision).toMatchObject({
      decision: 'ALLOW',
      meta: {
        hostId: 'host-copilot',
        policyRef: HOST_BRIDGE_POLICY_REF,
        traceId: 'session-host-001',
        taskId: 'task-auth'
      }
    });
  });

  it('prompts when the connector requires explicit permission', () => {
    const decision = evaluateHostInvocationPolicy(
      HOST_BINDING,
      {
        ...HOST_MANIFEST,
        permissionMode: 'prompt'
      },
      {
        ...BASE_ENVELOPE,
        mode: 'validate'
      }
    );

    expect(decision.decision).toBe('PROMPT');
    expect(decision.meta.promptRef).toBe('host-prompt:host-copilot:env-001');
  });

  it('denies unsupported scopes and direct commit writes', () => {
    const unsupportedScope = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, {
      ...BASE_ENVELOPE,
      requestedScopes: ['secrets']
    });
    const directCommit = evaluateHostInvocationPolicy(HOST_BINDING, HOST_MANIFEST, {
      ...BASE_ENVELOPE,
      mode: 'commit',
      requestedScopes: ['config_write'],
      evidencePolicy: 'strict'
    });

    expect(unsupportedScope.decision).toBe('DENY');
    expect(unsupportedScope.reason).toContain('unsupported scopes');
    expect(directCommit.decision).toBe('DENY');
    expect(directCommit.reason).toContain('cannot start directly in commit mode');
  });

  it('degrades stale or restricted hosts into read-only mode', () => {
    const staleDecision = evaluateHostInvocationPolicy(
      {
        ...HOST_BINDING,
        connectionState: 'stale'
      },
      HOST_MANIFEST,
      BASE_ENVELOPE
    );
    const restrictedDecision = evaluateHostInvocationPolicy(
      {
        ...HOST_BINDING,
        trustStatus: 'restricted'
      },
      HOST_MANIFEST,
      {
        ...BASE_ENVELOPE,
        mode: 'validate'
      }
    );

    expect(staleDecision.decision).toBe('DEGRADE');
    expect(staleDecision.reason).toContain('read-only degraded mode');
    expect(restrictedDecision.decision).toBe('DEGRADE');
    expect(restrictedDecision.reason).toContain('read-only');
  });
});