import {
  type ContextLedgerEntry,
  type InvocationEnvelope,
  ContextLedgerEntrySchema,
  InvocationEnvelopeSchema,
  createHostContextLedgerUpdateEvent,
  createHostInvocationDecisionEvent,
  parseServerEvent
} from '../../src/contracts/events';

const INVOCATION_ENVELOPE: InvocationEnvelope = {
  envelopeId: 'env-001',
  hostId: 'host-claude',
  actionKind: 'tool_call',
  mode: 'preview',
  correlationId: 'corr-host-001',
  idempotencyKey: 'idem-host-001',
  traceId: 'session-host-001',
  taskId: 'task-auth',
  requestedScopes: ['fs'],
  payload: {
    tool: 'semantic_search',
    query: 'host bridge policy'
  },
  evidencePolicy: 'basic'
};

const CONTEXT_ENTRY: ContextLedgerEntry = {
  entryId: 'ctx-001',
  hostId: 'host-claude',
  sourceType: 'selection',
  visibility: 'shared',
  confidence: 8.1,
  importedAt: '2026-04-10T10:10:00.000Z',
  ttlSeconds: 900,
  contentRef: 'selection://runtime-dashboard#host-bridge',
  trustStatus: 'review'
};

describe('host invocation contracts', () => {
  it('accepts invocation envelope and context ledger payloads', () => {
    expect(InvocationEnvelopeSchema.parse(INVOCATION_ENVELOPE)).toMatchObject({
      hostId: 'host-claude',
      actionKind: 'tool_call',
      mode: 'preview'
    });
    expect(ContextLedgerEntrySchema.parse(CONTEXT_ENTRY)).toMatchObject({
      entryId: 'ctx-001',
      visibility: 'shared',
      ttlSeconds: 900
    });
  });

  it('emits replay-safe invocation and context events with host meta', () => {
    const invocationEvent = parseServerEvent(
      createHostInvocationDecisionEvent(
        31,
        {
          envelope: INVOCATION_ENVELOPE,
          decision: 'PROMPT',
          reason: 'Preview requires a permission prompt.',
          meta: {
            traceId: 'session-host-001',
            taskId: 'task-auth',
            correlationId: 'corr-host-001',
            hostId: 'host-claude',
            promptRef: 'host-prompt:host-claude:env-001',
            policyRef: 'policy://host-bridge/default-v1'
          }
        },
        {
          timestamp: '2026-04-10T10:10:31.000Z'
        }
      )
    );
    const contextEvent = parseServerEvent(
      createHostContextLedgerUpdateEvent(
        32,
        {
          entry: CONTEXT_ENTRY,
          meta: {
            traceId: 'session-host-001',
            taskId: 'task-auth',
            correlationId: 'corr-host-001',
            hostId: 'host-claude'
          }
        },
        {
          timestamp: '2026-04-10T10:10:32.000Z'
        }
      )
    );

    expect(invocationEvent.type).toBe('HOST_INVOCATION_DECISION');
    expect(contextEvent.type).toBe('HOST_CONTEXT_LEDGER_UPDATE');

    if (invocationEvent.type !== 'HOST_INVOCATION_DECISION' || contextEvent.type !== 'HOST_CONTEXT_LEDGER_UPDATE') {
      throw new Error('Expected Host Bridge server events.');
    }

    expect(invocationEvent.meta.promptRef).toBe('host-prompt:host-claude:env-001');
    expect(contextEvent.entry.contentRef).toBe('selection://runtime-dashboard#host-bridge');
  });

  it('rejects malformed envelopes or context entries missing bounded metadata', () => {
    expect(() =>
      InvocationEnvelopeSchema.parse({
        ...INVOCATION_ENVELOPE,
        correlationId: ''
      })
    ).toThrow();

    expect(() =>
      ContextLedgerEntrySchema.parse({
        ...CONTEXT_ENTRY,
        ttlSeconds: 0
      })
    ).toThrow();
  });
});