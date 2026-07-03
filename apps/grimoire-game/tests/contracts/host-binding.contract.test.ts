import {
  type CapabilityManifest,
  type HostBinding,
  CapabilityManifestSchema,
  HostBindingSchema,
  createHostBindingStateEvent,
  parseServerEvent
} from '../../src/contracts/events';

const HOST_BINDING: HostBinding = {
  hostId: 'host-copilot',
  hostType: 'copilot',
  displayName: 'GitHub Copilot',
  version: '1.0.0',
  authMode: 'oauth',
  connectionState: 'online',
  trustStatus: 'trusted',
  scopes: ['fs', 'exec'],
  capabilityManifestRef: 'manifest-copilot',
  sourceOfTruth: 'secondary',
  lastSeenAt: '2026-04-10T10:00:00.000Z'
};

const CAPABILITY_MANIFEST: CapabilityManifest = {
  manifestId: 'manifest-copilot',
  hostId: 'host-copilot',
  routines: ['code-review', 'branch-workflow'],
  toolProviders: ['github-mcp'],
  reviewChannels: ['pull-request-review'],
  contextSources: ['selection', 'session_context'],
  permissionMode: 'hybrid',
  supportsStreaming: true,
  supportsReviewImport: true,
  supportsContextImport: true,
  supportsPreviewCommit: true
};

describe('host binding contracts', () => {
  it('accepts canonical host binding and capability manifest payloads', () => {
    expect(HostBindingSchema.parse(HOST_BINDING)).toMatchObject({
      hostId: 'host-copilot',
      connectionState: 'online',
      trustStatus: 'trusted',
      sourceOfTruth: 'secondary'
    });
    expect(CapabilityManifestSchema.parse(CAPABILITY_MANIFEST)).toMatchObject({
      hostId: 'host-copilot',
      permissionMode: 'hybrid',
      supportsPreviewCommit: true
    });
  });

  it('emits a typed host binding state event for v1 replay', () => {
    const event = parseServerEvent(
      createHostBindingStateEvent(
        21,
        {
          binding: HOST_BINDING,
          manifest: CAPABILITY_MANIFEST,
          reason: 'Connector online and policy-approved.'
        },
        {
          timestamp: '2026-04-10T10:00:21.000Z'
        }
      )
    );

    expect(event.type).toBe('HOST_BINDING_STATE');
    if (event.type !== 'HOST_BINDING_STATE') {
      throw new Error('Expected a HOST_BINDING_STATE event.');
    }
    expect(event.binding.displayName).toBe('GitHub Copilot');
    expect(event.manifest.routines).toContain('code-review');
    expect(event.reason).toBe('Connector online and policy-approved.');
  });

  it('rejects bindings that lose manifest linkage or stop being secondary', () => {
    expect(() =>
      HostBindingSchema.parse({
        ...HOST_BINDING,
        capabilityManifestRef: ''
      })
    ).toThrow();

    expect(() =>
      HostBindingSchema.parse({
        ...HOST_BINDING,
        sourceOfTruth: 'primary'
      })
    ).toThrow();
  });
});