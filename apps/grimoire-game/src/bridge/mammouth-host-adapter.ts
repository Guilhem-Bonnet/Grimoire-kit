import {
  ContextLedgerEntrySchema,
  InvocationEnvelopeSchema,
  ReviewArtifactSchema,
  type ContextLedgerEntry,
  type HostBindingInput,
  type InvocationEnvelope,
  type ReviewArtifact
} from '../contracts/events';
import {
  parseMammouthHandoffRequest,
  parseMammouthReviewResponse,
  type MammouthHandoffRequest,
  type MammouthReviewResponse
} from '../contracts/mammouth-host';
import type { HostBridgeHostCard } from '../state/host-bridge-view';
import type { HostHandoffPacket } from '../state/host-handoff-view';

import { evaluateHostInvocationPolicy, type HostInvocationPolicyDecision } from './host-invocation-policy';

export interface MammouthHostAdapterOptions {
  endpoint: string;
  apiKey?: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  allowPrompted?: boolean;
  extraHeaders?: Record<string, string>;
}

export interface PreparedMammouthReviewImport {
  envelope: InvocationEnvelope;
  policy: HostInvocationPolicyDecision;
  request: MammouthHandoffRequest;
}

export interface MammouthReviewImportResult extends PreparedMammouthReviewImport {
  response: MammouthReviewResponse | null;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export function createMammouthReviewImportEnvelope(
  packet: HostHandoffPacket,
  host: HostBridgeHostCard
): InvocationEnvelope {
  return InvocationEnvelopeSchema.parse({
    envelopeId: `env://${host.hostId}/${packet.taskId}/review-import`,
    hostId: host.hostId,
    actionKind: 'review_import',
    mode: 'validate',
    correlationId: packet.traceId ?? packet.packetId,
    idempotencyKey: `mammouth-review:${packet.packetId}`,
    ...(packet.traceId === null ? {} : { traceId: packet.traceId }),
    taskId: packet.taskId,
    requestedScopes: ['network'],
    payload: {
      packetId: packet.packetId,
      taskId: packet.taskId,
      expectedProofRefs: packet.missionPack?.expectedProofRefs ?? [],
      canonicalEnvelopeCount: packet.canonicalEnvelopeCount
    },
    evidencePolicy: 'strict'
  });
}

export function buildMammouthHandoffRequest(
  packet: HostHandoffPacket,
  host: HostBridgeHostCard
): MammouthHandoffRequest {
  assertDispatchablePacket(packet, host);

  const priorReviews = packet.canonicalEnvelopes
    .filter((envelope) => envelope.header.messageType === 'host.review')
    .map(projectReviewArtifactFromEnvelope);
  const importedContext = packet.canonicalEnvelopes
    .filter((envelope) => envelope.header.messageType === 'host.context')
    .map(projectContextLedgerEntryFromEnvelope);

  return parseMammouthHandoffRequest({
    version: 'mammouth-host-v1',
    packetId: packet.packetId,
    taskId: packet.taskId,
    taskTitle: packet.taskTitle,
    traceId: packet.traceId,
    sessionTitle: packet.sessionTitle,
    targetHost: {
      hostId: host.hostId,
      displayName: host.displayName,
      permissionMode: host.permissionMode,
      reviewChannels: [...host.reviewChannels],
      contextSources: [...host.contextSources],
      toolProviders: [...host.toolProviders]
    },
    missionPack: {
      objective: packet.missionPack?.objective,
      scope: [...(packet.missionPack?.scope ?? [])],
      canonicalSourceRefs: [...(packet.missionPack?.canonicalSourceRefs ?? [])],
      constraints: [...(packet.missionPack?.constraints ?? [])],
      expectedOutput: packet.missionPack?.expectedOutput ?? null,
      expectedProofRefs: [...(packet.missionPack?.expectedProofRefs ?? [])],
      mode: packet.missionPack?.mode ?? null
    },
    canonicalEnvelopes: packet.canonicalEnvelopes,
    priorReviews,
    importedContext,
    instructions: {
      requireRepoTruth: true,
      requireEvidence: true,
      responseFormat: 'review_artifact'
    }
  });
}

export function prepareMammouthReviewImport(
  packet: HostHandoffPacket,
  host: HostBridgeHostCard
): PreparedMammouthReviewImport {
  const envelope = createMammouthReviewImportEnvelope(packet, host);
  const policy = evaluateHostInvocationPolicy(
    toHostBindingInput(host),
    toCapabilityManifestInput(host),
    envelope,
    {
      details: {
        adapter: 'mammouth-http',
        packetId: packet.packetId,
        missingRequirements: [...packet.missingRequirements],
        canonicalEnvelopeCount: packet.canonicalEnvelopeCount
      }
    }
  );
  const request = buildMammouthHandoffRequest(packet, host);

  return {
    envelope,
    policy,
    request
  };
}

export async function invokeMammouthReviewImport(
  packet: HostHandoffPacket,
  host: HostBridgeHostCard,
  options: MammouthHostAdapterOptions
): Promise<MammouthReviewImportResult> {
  const prepared = prepareMammouthReviewImport(packet, host);
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;

  if (fetchImpl === undefined) {
    throw new Error('Fetch API is not available for the Mammouth HTTP adapter.');
  }

  if (prepared.policy.decision === 'DENY') {
    throw new Error(prepared.policy.reason);
  }

  if (!shouldExecutePolicyDecision(prepared.policy.decision, options.allowPrompted === true)) {
    return {
      ...prepared,
      response: null
    };
  }

  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT_MS);

  try {
    const response = await fetchImpl(options.endpoint, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(options.apiKey === undefined ? {} : { authorization: `Bearer ${options.apiKey}` }),
        ...(options.extraHeaders ?? {})
      },
      body: JSON.stringify(prepared.request),
      signal: controller.signal
    });

    if (!response.ok) {
      throw new Error(`Mammouth review import failed with status ${response.status}.`);
    }

    const payload = parseMammouthReviewResponse(await response.json());

    if (payload.packetId !== prepared.request.packetId) {
      throw new Error('Mammouth review response packetId does not match the dispatched handoff packet.');
    }

    if (payload.review.hostId !== host.hostId) {
      throw new Error('Mammouth review response hostId does not match the targeted host.');
    }

    return {
      ...prepared,
      response: payload
    };
  } finally {
    clearTimeout(timeoutHandle);
  }
}

function shouldExecutePolicyDecision(decision: HostInvocationPolicyDecision['decision'], allowPrompted: boolean): boolean {
  if (decision === 'ALLOW') {
    return true;
  }

  if (decision === 'PROMPT') {
    return allowPrompted;
  }

  return false;
}

function assertDispatchablePacket(packet: HostHandoffPacket, host: HostBridgeHostCard): void {
  if (packet.status === 'blocked') {
    throw new Error(`Host handoff packet ${packet.packetId} is blocked: ${packet.missingRequirements.join(', ')}.`);
  }

  if (!packet.reviewCapableHostIds.includes(host.hostId)) {
    throw new Error(`Host ${host.hostId} is not review-capable for handoff packet ${packet.packetId}.`);
  }

  if (packet.missionPack === null) {
    throw new Error(`Host handoff packet ${packet.packetId} is missing a mission pack.`);
  }

  if (packet.canonicalEnvelopes.length === 0) {
    throw new Error(`Host handoff packet ${packet.packetId} has no canonical envelopes to dispatch.`);
  }
}

function toHostBindingInput(host: HostBridgeHostCard): HostBindingInput {
  return {
    hostId: host.hostId,
    hostType: host.hostType,
    displayName: host.displayName,
    authMode: host.authMode,
    connectionState: host.connectionState,
    trustStatus: host.trustStatus,
    scopes: [...host.scopes],
    capabilityManifestRef: host.manifestId,
    sourceOfTruth: 'secondary',
    ...(host.lastSeenAt === null ? {} : { lastSeenAt: host.lastSeenAt }),
    ...(host.reason === null ? {} : { notes: host.reason })
  };
}

function toCapabilityManifestInput(host: HostBridgeHostCard) {
  return {
    manifestId: host.manifestId,
    hostId: host.hostId,
    routines: [...host.routines],
    toolProviders: [...host.toolProviders],
    reviewChannels: [...host.reviewChannels],
    contextSources: [...host.contextSources],
    permissionMode: host.permissionMode,
    supportsStreaming: host.supportsStreaming,
    supportsReviewImport: host.supportsReviewImport,
    supportsContextImport: host.supportsContextImport,
    supportsPreviewCommit: host.supportsPreviewCommit
  };
}

function projectReviewArtifactFromEnvelope(envelope: HostHandoffPacket['canonicalEnvelopes'][number]): ReviewArtifact {
  const body = asRecord(envelope.body);

  return ReviewArtifactSchema.parse({
    reviewId: body.reviewId,
    hostId: body.hostId,
    sourceType: body.sourceType,
    subjectRef: body.subjectRef,
    verdict: body.verdict,
    findings: body.findings,
    linkedEvidenceRefs: body.linkedEvidenceRefs,
    importedAt: envelope.header.emittedAt,
    ...(envelope.context.traceId === undefined ? {} : { traceId: envelope.context.traceId }),
    ...(envelope.context.taskId === undefined ? {} : { taskId: envelope.context.taskId })
  });
}

function projectContextLedgerEntryFromEnvelope(envelope: HostHandoffPacket['canonicalEnvelopes'][number]): ContextLedgerEntry {
  const body = asRecord(envelope.body);

  return ContextLedgerEntrySchema.parse({
    entryId: body.entryId,
    hostId: body.hostId,
    sourceType: body.sourceType,
    visibility: body.visibility,
    confidence: body.confidence,
    importedAt: envelope.header.emittedAt,
    ttlSeconds: body.ttlSeconds,
    contentRef: body.contentRef,
    trustStatus: body.trustStatus,
    ...(body.supersedes === undefined ? {} : { supersedes: body.supersedes })
  });
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}