import {
  CapabilityManifestSchema,
  HostBindingSchema,
  createEventMeta,
  type CapabilityManifest,
  type CapabilityManifestInput,
  type EventMeta,
  type EventMetaInput,
  type HostBinding,
  type HostBindingInput,
  type HostInvocationDecision,
  type InvocationEnvelope,
  type InvocationEnvelopeInput,
  InvocationEnvelopeSchema
} from '../contracts/events';

export const HOST_BRIDGE_POLICY_REF = 'policy://host-bridge/default-v1';

export interface HostInvocationPolicyDecision {
  decision: HostInvocationDecision;
  reason: string;
  meta: EventMeta;
}

export function evaluateHostInvocationPolicy(
  bindingInput: HostBindingInput,
  manifestInput: CapabilityManifestInput,
  envelopeInput: InvocationEnvelopeInput,
  meta: EventMetaInput = {}
): HostInvocationPolicyDecision {
  const binding = HostBindingSchema.parse(bindingInput);
  const manifest = CapabilityManifestSchema.parse(manifestInput);
  const envelope = InvocationEnvelopeSchema.parse(envelopeInput);

  if (binding.hostId !== manifest.hostId || binding.hostId !== envelope.hostId) {
    return buildDecision('DENY', 'Host binding, manifest and envelope must target the same hostId.', binding, manifest, envelope, meta);
  }

  if (binding.capabilityManifestRef !== manifest.manifestId) {
    return buildDecision('DENY', 'Host binding capabilityManifestRef does not match the provided manifest.', binding, manifest, envelope, meta);
  }

  if (binding.connectionState === 'blocked' || binding.trustStatus === 'blocked') {
    return buildDecision('DENY', 'Host is blocked by connector policy.', binding, manifest, envelope, meta);
  }

  if (manifest.permissionMode === 'none' && envelope.actionKind === 'permission_prompt') {
    return buildDecision('DENY', 'Host does not support runtime permission prompts.', binding, manifest, envelope, meta);
  }

  if (envelope.actionKind === 'review_import' && !manifest.supportsReviewImport) {
    return buildDecision('DENY', 'Host manifest does not allow review imports.', binding, manifest, envelope, meta);
  }

  if (envelope.actionKind === 'context_import' && !manifest.supportsContextImport) {
    return buildDecision('DENY', 'Host manifest does not allow context imports.', binding, manifest, envelope, meta);
  }

  if (envelope.mode === 'commit' && !manifest.supportsPreviewCommit) {
    return buildDecision('DENY', 'Host manifest does not support preview to commit promotion.', binding, manifest, envelope, meta);
  }

  if ((envelope.actionKind === 'tool_call' || envelope.actionKind === 'routine') && envelope.mode === 'commit') {
    return buildDecision('DENY', 'External host mutations cannot start directly in commit mode.', binding, manifest, envelope, meta);
  }

  const unsupportedScopes = envelope.requestedScopes.filter((scope) => !binding.scopes.includes(scope));
  if (unsupportedScopes.length > 0) {
    return buildDecision(
      'DENY',
      `Host requested unsupported scopes: ${unsupportedScopes.join(', ')}.`,
      binding,
      manifest,
      envelope,
      meta
    );
  }

  if (requiresStrictEvidence(envelope) && envelope.evidencePolicy !== 'strict') {
    return buildDecision(
      'DENY',
      'Strict evidence policy is required for durable host mutations or imported review verdicts.',
      binding,
      manifest,
      envelope,
      meta
    );
  }

  if (binding.connectionState === 'offline') {
    return envelope.mode === 'read'
      ? buildDecision('DEGRADE', 'Host is offline; runtime degrades to stale read diagnostics only.', binding, manifest, envelope, meta)
      : buildDecision('DENY', 'Host is offline and cannot execute non-read invocations.', binding, manifest, envelope, meta);
  }

  if (binding.connectionState === 'stale' || binding.connectionState === 'degraded') {
    return buildDecision(
      'DEGRADE',
      `Host connection is ${binding.connectionState}; runtime keeps the host visible in read-only degraded mode.`,
      binding,
      manifest,
      envelope,
      meta,
      {
        degradedFrom: binding.connectionState
      }
    );
  }

  if (manifest.permissionMode === 'prompt' && envelope.mode !== 'read') {
    return buildDecision('PROMPT', 'Host invocation requires an explicit permission prompt before continuing.', binding, manifest, envelope, meta, {
      promptRef: `host-prompt:${binding.hostId}:${envelope.envelopeId}`
    });
  }

  if (
    manifest.permissionMode === 'hybrid' &&
    envelope.mode !== 'read' &&
    (binding.trustStatus !== 'trusted' || envelope.requestedScopes.length > 0)
  ) {
    return buildDecision('PROMPT', 'Host invocation requires a permission prompt because the connector is hybrid-governed.', binding, manifest, envelope, meta, {
      promptRef: `host-prompt:${binding.hostId}:${envelope.envelopeId}`
    });
  }

  if (binding.trustStatus === 'review' && envelope.mode !== 'read') {
    return buildDecision('PROMPT', 'Host is in review status; mutation requires user confirmation.', binding, manifest, envelope, meta, {
      promptRef: `host-prompt:${binding.hostId}:${envelope.envelopeId}`
    });
  }

  if (binding.trustStatus === 'restricted' && envelope.mode !== 'read') {
    return buildDecision('DEGRADE', 'Restricted hosts stay read-only until trust is upgraded.', binding, manifest, envelope, meta);
  }

  return buildDecision('ALLOW', 'Host invocation satisfies the current Host Bridge policy.', binding, manifest, envelope, meta);
}

function requiresStrictEvidence(envelope: InvocationEnvelope): boolean {
  if (envelope.mode !== 'commit') {
    return false;
  }

  return (
    envelope.requestedScopes.includes('config_write') ||
    envelope.requestedScopes.includes('write_budget') ||
    envelope.actionKind === 'review_import'
  );
}

function buildDecision(
  decision: HostInvocationDecision,
  reason: string,
  binding: HostBinding,
  manifest: CapabilityManifest,
  envelope: InvocationEnvelope,
  meta: EventMetaInput,
  overrides: Partial<EventMetaInput> = {}
): HostInvocationPolicyDecision {
  const details = {
    hostType: binding.hostType,
    connectionState: binding.connectionState,
    trustStatus: binding.trustStatus,
    permissionMode: manifest.permissionMode,
    actionKind: envelope.actionKind,
    mode: envelope.mode,
    evidencePolicy: envelope.evidencePolicy,
    requestedScopes: envelope.requestedScopes,
    manifestId: manifest.manifestId,
    decision,
    ...(meta.details ?? {}),
    ...(overrides.details ?? {})
  };

  return {
    decision,
    reason,
    meta: createEventMeta({
      ...(envelope.traceId === undefined ? {} : { traceId: envelope.traceId }),
      ...(envelope.taskId === undefined ? {} : { taskId: envelope.taskId }),
      correlationId: envelope.correlationId,
      hostId: binding.hostId,
      policyRef: HOST_BRIDGE_POLICY_REF,
      ...meta,
      ...overrides,
      details
    })
  };
}