import type {
  AgentRole,
  ClientEvent,
  MutationGuardrail,
  MutationPolicy,
  MutationProvenanceSource,
  MutationSurface,
  MutationTrustLevel,
  VerificationChainMetadata
} from '../../contracts/events';
import {
  MUTATION_POLICIES,
  MUTATION_PROVENANCE_SOURCES,
  MUTATION_SURFACES,
  MUTATION_TRUST_LEVELS
} from '../../contracts/events';

export interface AuthContext {
  principalId: string;
  role: AgentRole;
  tokenId?: string;
  trustLevel?: MutationTrustLevel;
  authorizedMutationSurfaces?: readonly MutationSurface[];
}

export interface AuthDecision {
  allowed: boolean;
  reason?: string;
  surface?: MutationSurface;
  policy?: MutationPolicy;
  trustLevel?: MutationTrustLevel;
  provenanceSource?: MutationProvenanceSource;
  provenanceActorTag?: string;
}

export interface AuthorizationAuditEntry {
  at: string;
  principalId: string;
  role: AgentRole;
  tokenId?: string;
  eventType: ClientEvent['type'];
  allowed: boolean;
  reason?: string;
  surface?: MutationSurface;
  policy?: MutationPolicy;
  trustLevel?: MutationTrustLevel;
  provenanceSource?: MutationProvenanceSource;
  provenanceActorTag?: string;
}

export class AuthorizationError extends Error {
  readonly code = 'FORBIDDEN';

  constructor(message: string) {
    super(message);
    this.name = 'AuthorizationError';
  }
}

const EVENT_ROLE_MATRIX: Record<ClientEvent['type'], readonly AgentRole[]> = {
  RECONNECT_HANDSHAKE: ['orchestrator', 'agent', 'spectator'],
  CONFIG_UPDATE: ['orchestrator'],
  TASK_TRANSITION: ['orchestrator'],
  TASK_ASSIGN: ['orchestrator'],
  AGENT_STATUS_UPDATE: ['orchestrator']
};

export const CLIENT_EVENT_PREVIEW_ORDER = [
  'RECONNECT_HANDSHAKE',
  'CONFIG_UPDATE',
  'TASK_TRANSITION',
  'TASK_ASSIGN',
  'AGENT_STATUS_UPDATE'
] as const;

export interface ClientEventAccessPreview {
  eventType: ClientEvent['type'];
  mutation: boolean;
  allowed: boolean;
  reason: string | null;
}

function isGovernedMutationEvent(event: ClientEvent): event is Exclude<ClientEvent, { type: 'RECONNECT_HANDSHAKE' }> {
  return event.type !== 'RECONNECT_HANDSHAKE';
}

type GovernedMutationEvent = Exclude<ClientEvent, { type: 'RECONNECT_HANDSHAKE' }>;

const MUTATION_POLICY_RANK: Record<MutationPolicy, number> = {
  read_only: 0,
  surface_scoped: 1,
  elevated: 2
};

function isMutationSurface(value: unknown): value is MutationSurface {
  return typeof value === 'string' && MUTATION_SURFACES.includes(value as MutationSurface);
}

function isMutationPolicy(value: unknown): value is MutationPolicy {
  return typeof value === 'string' && MUTATION_POLICIES.includes(value as MutationPolicy);
}

function isMutationTrustLevel(value: unknown): value is MutationTrustLevel {
  return typeof value === 'string' && MUTATION_TRUST_LEVELS.includes(value as MutationTrustLevel);
}

function isMutationProvenanceSource(value: unknown): value is MutationProvenanceSource {
  return typeof value === 'string' && MUTATION_PROVENANCE_SOURCES.includes(value as MutationProvenanceSource);
}

function normalizeNonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }

  const normalized = value.trim();
  return normalized.length === 0 ? undefined : normalized;
}

function resolveRequiredGuardrailSurface(event: GovernedMutationEvent): MutationSurface {
  switch (event.type) {
    case 'CONFIG_UPDATE':
      return 'runtime_config';
    case 'TASK_TRANSITION':
      return 'task_lifecycle';
    case 'TASK_ASSIGN':
      return 'task_assignment';
    case 'AGENT_STATUS_UPDATE':
      return 'agent_presence';
  }
}

function resolveRequiredGuardrailPolicy(event: GovernedMutationEvent): MutationPolicy {
  switch (event.type) {
    case 'CONFIG_UPDATE':
      return 'elevated';
    case 'TASK_TRANSITION':
      return event.status === 'done' ? 'elevated' : 'surface_scoped';
    case 'TASK_ASSIGN':
    case 'AGENT_STATUS_UPDATE':
      return 'surface_scoped';
  }
}

function resolveRequiredVerificationReason(event: GovernedMutationEvent): string | null {
  switch (event.type) {
    case 'CONFIG_UPDATE':
      return 'critical governed config mutation';
    case 'TASK_TRANSITION':
      return event.status === 'done' ? 'verification-gated task completion' : null;
    case 'TASK_ASSIGN':
    case 'AGENT_STATUS_UPDATE':
      return null;
  }
}

function extractVerificationMetadata(event: GovernedMutationEvent): VerificationChainMetadata | undefined {
  if ('verification' in event) {
    return event.verification;
  }

  return undefined;
}

function toGuardrailMetadata(guardrail: MutationGuardrail | undefined): Omit<AuthDecision, 'allowed' | 'reason'> {
  if (guardrail === undefined) {
    return {};
  }

  const surface = isMutationSurface((guardrail as { surface?: unknown }).surface)
    ? (guardrail as { surface: MutationSurface }).surface
    : undefined;
  const policy = isMutationPolicy((guardrail as { policy?: unknown }).policy)
    ? (guardrail as { policy: MutationPolicy }).policy
    : undefined;
  const trustLevel = isMutationTrustLevel((guardrail as { trustLevel?: unknown }).trustLevel)
    ? (guardrail as { trustLevel: MutationTrustLevel }).trustLevel
    : undefined;
  const provenance = (guardrail as { provenance?: { source?: unknown; actorTag?: unknown } }).provenance;
  const provenanceSource = isMutationProvenanceSource(provenance?.source) ? provenance.source : undefined;
  const provenanceActorTag = normalizeNonEmptyString(provenance?.actorTag);

  return {
    ...(surface === undefined ? {} : { surface }),
    ...(policy === undefined ? {} : { policy }),
    ...(trustLevel === undefined ? {} : { trustLevel }),
    ...(provenanceSource === undefined ? {} : { provenanceSource }),
    ...(provenanceActorTag === undefined ? {} : { provenanceActorTag })
  };
}

function resolveContextTrustLevel(context: AuthContext): MutationTrustLevel {
  if (context.trustLevel !== undefined) {
    return context.trustLevel;
  }

  return context.role === 'orchestrator' ? 'trusted' : 'restricted';
}

function resolveAuthorizedMutationSurfaces(context: AuthContext): readonly MutationSurface[] {
  if (context.authorizedMutationSurfaces !== undefined) {
    return context.authorizedMutationSurfaces;
  }

  return context.role === 'orchestrator' ? MUTATION_SURFACES : [];
}

export function isReadOnlyRole(role: AgentRole): boolean {
  return role === 'spectator';
}

export function previewClientEventAccess(context: Pick<AuthContext, 'role'>): ClientEventAccessPreview[] {
  return CLIENT_EVENT_PREVIEW_ORDER.map((eventType) => {
    const mutation = eventType !== 'RECONNECT_HANDSHAKE';

    if (mutation && isReadOnlyRole(context.role)) {
      return {
        eventType,
        mutation,
        allowed: false,
        reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
      };
    }

    if (!EVENT_ROLE_MATRIX[eventType].includes(context.role)) {
      return {
        eventType,
        mutation,
        allowed: false,
        reason: `Role ${context.role} cannot execute ${eventType}.`
      };
    }

    return {
      eventType,
      mutation,
      allowed: true,
      reason: null
    };
  });
}

export function authorizeClientEvent(context: AuthContext, event: ClientEvent): AuthDecision {
  const allowedRoles = EVENT_ROLE_MATRIX[event.type];

  if (!isGovernedMutationEvent(event)) {
    return allowedRoles.includes(context.role)
      ? { allowed: true }
      : {
          allowed: false,
          reason: `Role ${context.role} cannot execute ${event.type}.`
        };
  }

  if (isReadOnlyRole(context.role)) {
    return {
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot mutate runtime state.'
    };
  }

  if (!allowedRoles.includes(context.role)) {
    return {
      allowed: false,
      reason: `Role ${context.role} cannot execute ${event.type}.`
    };
  }

  const guardrail = event.guardrail;
  const metadata = toGuardrailMetadata(guardrail);

  if (guardrail === undefined) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} is missing runtime guardrail metadata.`
    };
  }

  if (metadata.provenanceSource === undefined || metadata.provenanceActorTag === undefined) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} is missing runtime guardrail origin metadata.`,
      ...metadata
    };
  }

  if (metadata.policy === undefined) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} is missing required policy metadata.`,
      ...metadata
    };
  }

  if (metadata.trustLevel === undefined) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} is missing runtime guardrail trust status metadata.`,
      ...metadata
    };
  }

  if (metadata.trustLevel === 'blocked') {
    return {
      allowed: false,
      reason: `Governed surface ${metadata.surface ?? 'unknown'} is blocked by trust status policy.`,
      ...metadata
    };
  }

  if (metadata.surface === undefined) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} is missing governed surface metadata.`,
      ...metadata
    };
  }

  const requiredSurface = resolveRequiredGuardrailSurface(event);
  if (metadata.surface !== requiredSurface) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} targets governed surface ${metadata.surface} but expected ${requiredSurface}.`,
      ...metadata
    };
  }

  if (metadata.policy === 'read_only') {
    return {
      allowed: false,
      reason: `Policy read_only forbids mutation on governed surface ${metadata.surface}.`,
      ...metadata
    };
  }

  const requiredPolicy = resolveRequiredGuardrailPolicy(event);
  if (MUTATION_POLICY_RANK[metadata.policy] < MUTATION_POLICY_RANK[requiredPolicy]) {
    return {
      allowed: false,
      reason: `Mutation ${event.type} does not satisfy required policy ${requiredPolicy} on governed surface ${metadata.surface}.`,
      ...metadata
    };
  }

  const requiredVerificationReason = resolveRequiredVerificationReason(event);
  if (requiredVerificationReason !== null) {
    const verification = extractVerificationMetadata(event);

    if (verification === undefined) {
      return {
        allowed: false,
        reason: `Critical mutation ${event.type} is missing verification metadata required for ${requiredVerificationReason}.`,
        ...metadata
      };
    }

    if (verification.requestId === undefined || verification.idempotencyKey === undefined) {
      return {
        allowed: false,
        reason: `Critical mutation ${event.type} is missing canonical proof identities in verification metadata.`,
        ...metadata
      };
    }

    if (verification.requestId !== event.requestId || verification.idempotencyKey !== event.idempotencyKey) {
      return {
        allowed: false,
        reason: `Critical mutation ${event.type} verification metadata does not align with requestId and idempotencyKey.`,
        ...metadata
      };
    }
  }

  const authorizedMutationSurfaces = resolveAuthorizedMutationSurfaces(context);
  if (!authorizedMutationSurfaces.includes(metadata.surface)) {
    return {
      allowed: false,
      reason: `Role ${context.role} is not authorized for governed surface ${metadata.surface}.`,
      ...metadata
    };
  }

  const contextTrustLevel = resolveContextTrustLevel(context);
  if (metadata.trustLevel === 'trusted' && contextTrustLevel !== 'trusted') {
    return {
      allowed: false,
      reason: `Governed surface ${metadata.surface} requires a trusted runtime context.`,
      ...metadata
    };
  }

  return {
    allowed: true,
    ...metadata
  };
}

export function requireAuthorizedClientEvent(context: AuthContext, event: ClientEvent): void {
  const decision = authorizeClientEvent(context, event);

  if (!decision.allowed) {
    throw new AuthorizationError(decision.reason ?? 'Forbidden.');
  }
}

export function createAuthorizationAuditEntry(
  context: AuthContext,
  event: ClientEvent,
  decision: AuthDecision,
  at = new Date().toISOString()
): AuthorizationAuditEntry {
  const metadata = isGovernedMutationEvent(event) ? toGuardrailMetadata(event.guardrail) : {};

  return {
    at,
    principalId: context.principalId,
    role: context.role,
    ...(context.tokenId === undefined ? {} : { tokenId: context.tokenId }),
    eventType: event.type,
    allowed: decision.allowed,
    ...(decision.reason === undefined ? {} : { reason: decision.reason }),
    ...(metadata.surface === undefined ? {} : { surface: metadata.surface }),
    ...(metadata.policy === undefined ? {} : { policy: metadata.policy }),
    ...(metadata.trustLevel === undefined ? {} : { trustLevel: metadata.trustLevel }),
    ...(metadata.provenanceSource === undefined ? {} : { provenanceSource: metadata.provenanceSource }),
    ...(metadata.provenanceActorTag === undefined ? {} : { provenanceActorTag: metadata.provenanceActorTag })
  };
}