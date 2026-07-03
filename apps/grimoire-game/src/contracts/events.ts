import { z } from 'zod';

import {
  AgentPresenceSchema,
  AgentRoleSchema,
  CapabilityManifestSchema,
  BranchFinishDecisionPayloadSchema,
  BranchFinishOptionSchema,
  BranchFinishOptionsPayloadSchema,
  LEASE_STORE_VERSION,
  AgentStateEventSchema,
  AgentStatusSchema,
  ControlPlaneRunContextSchema,
  CONTROL_PLANE_REGISTRY_VERSION,
  NODE_REGISTRY_VERSION,
  MUTATION_POLICIES,
  MUTATION_PROVENANCE_SOURCES,
  MUTATION_SURFACES,
  MUTATION_TRUST_LEVELS,
  CLIENT_EVENT_TYPES,
  CanonicalEnvelopeChannelSchema,
  CanonicalEnvelopeContextSchema,
  CanonicalEnvelopeHeaderSchema,
  CanonicalEnvelopePilotSchema,
  ContextLedgerEntrySchema,
  ClientEventSchema,
  ConfigSnapshotSchema,
  ConfigUpdateEventSchema,
  EventMetaSchema,
  GameStateSnapshotSchema,
  HOST_ACTION_KINDS,
  HOST_ACTION_MODES,
  HOST_AUTH_MODES,
  HOST_CONNECTION_STATES,
  HOST_CONTEXT_SOURCE_TYPES,
  HOST_CONTEXT_TRUST_STATUSES,
  HOST_CONTEXT_VISIBILITIES,
  HOST_EVIDENCE_POLICIES,
  HOST_INVOCATION_DECISIONS,
  HOST_PERMISSION_MODES,
  HOST_REVIEW_FINDING_SEVERITIES,
  HOST_REVIEW_RESOLUTION_STATUSES,
  HOST_REVIEW_SOURCE_TYPES,
  HOST_REVIEW_VERDICTS,
  HOST_SCOPES,
  HOST_TRUST_STATUSES,
  HOST_TYPES,
  HostActionKindSchema,
  HostActionModeSchema,
  HostAuthModeSchema,
  HostBindingRecordSchema,
  HostBindingSchema,
  HostBindingStateEventSchema,
  HostConnectionStateSchema,
  HostContextLedgerRecordSchema,
  HostContextLedgerUpdateEventSchema,
  HostContextSourceTypeSchema,
  HostContextTrustStatusSchema,
  HostContextVisibilitySchema,
  HostEvidencePolicySchema,
  HostInvocationDecisionRecordSchema,
  HostInvocationDecisionEventSchema,
  HostInvocationDecisionSchema,
  HostPermissionModeSchema,
  HostReviewArtifactRecordSchema,
  HostReviewArtifactEventSchema,
  HostReviewFindingSeveritySchema,
  HostReviewResolutionStatusSchema,
  HostReviewSourceTypeSchema,
  HostReviewVerdictSchema,
  HostScopeSchema,
  HostTrustStatusSchema,
  HostTypeSchema,
  InvocationEnvelopeSchema,
  JsonValueSchema,
  LeaseRecordSchema,
  LeaseStatusSchema,
  LeaseStoreSnapshotSchema,
  LeaseStoreSummarySchema,
  MutationGuardrailSchema,
  MutationPolicySchema,
  MutationProvenanceSchema,
  MutationProvenanceSourceSchema,
  MutationSurfaceSchema,
  MutationTrustLevelSchema,
  NodeRegistryRecordSchema,
  NodeRegistrySnapshotSchema,
  NodeRegistryStatusSchema,
  NodeRegistrySummarySchema,
  NodeWorkerRecordSchema,
  PointSchema,
  ProjectRegistryRecordSchema,
  ProjectRegistrySnapshotSchema,
  RUNTIME_PROTOCOL_VERSION,
  AgentStatusUpdateEventSchema,
  ReconnectHandshakeEventSchema,
  ReviewArtifactFindingSchema,
  ReviewArtifactSchema,
  RuntimeErrorEventSchema,
  SERVER_EVENT_TYPES,
  SecurityFindingPayloadSchema,
  SecurityFindingSeveritySchema,
  SecurityFindingStatusSchema,
  TaskKindSchema,
  TaskPrioritySchema,
  VerificationChainMetadataSchema,
  VerificationEvidenceRefSchema,
  VerificationGateEventSchema,
  VerificationGateResultSchema,
  ServerEventSchema,
  SurfaceExecutionRecordSchema,
  SurfaceGovernanceRegistrySchema,
  StateSnapshotEventSchema,
  TaskAssignEventSchema,
  TaskTransitionEventSchema,
  ToolCallEventSchema,
  ToolCallLogEntrySchema,
  ToolCallPayloadSchema,
  TaskUpdateEventSchema,
  TaskSnapshotSchema,
  TaskStatusSchema,
  WorkflowStepEventSchema,
  WorkflowStepLogEntrySchema,
  WorkflowStepPayloadSchema
} from './schemas';

export {
  CLIENT_EVENT_TYPES,
  CapabilityManifestSchema,
  BranchFinishDecisionPayloadSchema,
  BranchFinishOptionSchema,
  BranchFinishOptionsPayloadSchema,
  LEASE_STORE_VERSION,
  CanonicalEnvelopeChannelSchema,
  CanonicalEnvelopeContextSchema,
  CanonicalEnvelopeHeaderSchema,
  CanonicalEnvelopePilotSchema,
  ControlPlaneRunContextSchema,
  CONTROL_PLANE_REGISTRY_VERSION,
  NODE_REGISTRY_VERSION,
  ContextLedgerEntrySchema,
  EventMetaSchema,
  HOST_ACTION_KINDS,
  HOST_ACTION_MODES,
  HOST_AUTH_MODES,
  HOST_CONNECTION_STATES,
  HOST_CONTEXT_SOURCE_TYPES,
  HOST_CONTEXT_TRUST_STATUSES,
  HOST_CONTEXT_VISIBILITIES,
  HOST_EVIDENCE_POLICIES,
  HOST_INVOCATION_DECISIONS,
  HOST_PERMISSION_MODES,
  HOST_REVIEW_FINDING_SEVERITIES,
  HOST_REVIEW_RESOLUTION_STATUSES,
  HOST_REVIEW_SOURCE_TYPES,
  HOST_REVIEW_VERDICTS,
  HOST_SCOPES,
  HOST_TRUST_STATUSES,
  HOST_TYPES,
  HostActionKindSchema,
  HostActionModeSchema,
  HostAuthModeSchema,
  HostBindingRecordSchema,
  HostBindingSchema,
  HostBindingStateEventSchema,
  HostConnectionStateSchema,
  HostContextLedgerRecordSchema,
  HostContextLedgerUpdateEventSchema,
  HostContextSourceTypeSchema,
  HostContextTrustStatusSchema,
  HostContextVisibilitySchema,
  HostEvidencePolicySchema,
  HostInvocationDecisionRecordSchema,
  HostInvocationDecisionEventSchema,
  HostInvocationDecisionSchema,
  HostPermissionModeSchema,
  HostReviewArtifactRecordSchema,
  HostReviewArtifactEventSchema,
  HostReviewFindingSeveritySchema,
  HostReviewResolutionStatusSchema,
  HostReviewSourceTypeSchema,
  HostReviewVerdictSchema,
  HostScopeSchema,
  HostTrustStatusSchema,
  HostTypeSchema,
  InvocationEnvelopeSchema,
  LeaseRecordSchema,
  LeaseStatusSchema,
  LeaseStoreSnapshotSchema,
  LeaseStoreSummarySchema,
  MUTATION_POLICIES,
  MUTATION_PROVENANCE_SOURCES,
  MUTATION_SURFACES,
  MUTATION_TRUST_LEVELS,
  NodeRegistryRecordSchema,
  NodeRegistrySnapshotSchema,
  NodeRegistryStatusSchema,
  NodeRegistrySummarySchema,
  NodeWorkerRecordSchema,
  ProjectRegistryRecordSchema,
  ProjectRegistrySnapshotSchema,
  RUNTIME_PROTOCOL_VERSION,
  ReviewArtifactFindingSchema,
  ReviewArtifactSchema,
  SERVER_EVENT_TYPES,
  SecurityFindingPayloadSchema,
  SecurityFindingSeveritySchema,
  SecurityFindingStatusSchema,
  TaskKindSchema,
  TaskPrioritySchema,
  VerificationChainMetadataSchema,
  VerificationEvidenceRefSchema,
  VerificationGateEventSchema,
  VerificationGateResultSchema,
  SurfaceExecutionRecordSchema,
  SurfaceGovernanceRegistrySchema
} from './schemas';

export type AgentPresence = z.infer<typeof AgentPresenceSchema>;
export type AgentRole = z.infer<typeof AgentRoleSchema>;
export type AgentStatus = z.infer<typeof AgentStatusSchema>;
export type TaskSnapshot = z.infer<typeof TaskSnapshotSchema>;
export type TaskStatus = z.infer<typeof TaskStatusSchema>;
export type TaskPriority = z.infer<typeof TaskPrioritySchema>;
export type TaskKind = z.infer<typeof TaskKindSchema>;
export type SecurityFindingSeverity = z.infer<typeof SecurityFindingSeveritySchema>;
export type SecurityFindingStatus = z.infer<typeof SecurityFindingStatusSchema>;
export type SecurityFindingPayload = z.infer<typeof SecurityFindingPayloadSchema>;
export type LeaseStatus = z.infer<typeof LeaseStatusSchema>;
export type LeaseRecord = z.infer<typeof LeaseRecordSchema>;
export type LeaseStoreSummary = z.infer<typeof LeaseStoreSummarySchema>;
export type LeaseStoreSnapshot = z.infer<typeof LeaseStoreSnapshotSchema>;
export type BranchFinishOption = z.infer<typeof BranchFinishOptionSchema>;
export type BranchFinishOptionsPayload = z.infer<typeof BranchFinishOptionsPayloadSchema>;
export type BranchFinishDecisionPayload = z.infer<typeof BranchFinishDecisionPayloadSchema>;
export interface BranchFinishOptionsPayloadInput {
  branch: string;
  testsPassed: boolean;
  allowedOptions?: readonly BranchFinishOption[];
  typedDiscardConfirmation?: string;
}

export interface BranchFinishDecisionPayloadInput {
  branch: string;
  selectedOption: BranchFinishOption;
  typedConfirmation?: string;
}
export type CanonicalEnvelopeChannel = z.infer<typeof CanonicalEnvelopeChannelSchema>;
export type CanonicalEnvelopeContext = z.infer<typeof CanonicalEnvelopeContextSchema>;
export type CanonicalEnvelopeHeader = z.infer<typeof CanonicalEnvelopeHeaderSchema>;
export type CanonicalEnvelopePilot = z.infer<typeof CanonicalEnvelopePilotSchema>;
export type ControlPlaneRunContext = z.infer<typeof ControlPlaneRunContextSchema>;
export type EventMeta = z.infer<typeof EventMetaSchema>;
export type NodeRegistryStatus = z.infer<typeof NodeRegistryStatusSchema>;
export type NodeWorkerRecord = z.infer<typeof NodeWorkerRecordSchema>;
export type NodeRegistryRecord = z.infer<typeof NodeRegistryRecordSchema>;
export type NodeRegistrySummary = z.infer<typeof NodeRegistrySummarySchema>;
export type NodeRegistrySnapshot = z.infer<typeof NodeRegistrySnapshotSchema>;
export type Point = z.infer<typeof PointSchema>;
export type ProjectRegistryRecord = z.infer<typeof ProjectRegistryRecordSchema>;
export type ProjectRegistrySnapshot = z.infer<typeof ProjectRegistrySnapshotSchema>;
export type JsonValue = z.infer<typeof JsonValueSchema>;
export type ConfigSnapshot = z.infer<typeof ConfigSnapshotSchema>;
export type GameStateSnapshot = z.infer<typeof GameStateSnapshotSchema>;
export type HostType = z.infer<typeof HostTypeSchema>;
export type HostAuthMode = z.infer<typeof HostAuthModeSchema>;
export type HostConnectionState = z.infer<typeof HostConnectionStateSchema>;
export type HostTrustStatus = z.infer<typeof HostTrustStatusSchema>;
export type HostScope = z.infer<typeof HostScopeSchema>;
export type HostPermissionMode = z.infer<typeof HostPermissionModeSchema>;
export type HostActionKind = z.infer<typeof HostActionKindSchema>;
export type HostActionMode = z.infer<typeof HostActionModeSchema>;
export type HostEvidencePolicy = z.infer<typeof HostEvidencePolicySchema>;
export type HostContextSourceType = z.infer<typeof HostContextSourceTypeSchema>;
export type HostContextVisibility = z.infer<typeof HostContextVisibilitySchema>;
export type HostContextTrustStatus = z.infer<typeof HostContextTrustStatusSchema>;
export type HostReviewSourceType = z.infer<typeof HostReviewSourceTypeSchema>;
export type HostReviewVerdict = z.infer<typeof HostReviewVerdictSchema>;
export type HostReviewFindingSeverity = z.infer<typeof HostReviewFindingSeveritySchema>;
export type HostReviewResolutionStatus = z.infer<typeof HostReviewResolutionStatusSchema>;
export type HostInvocationDecision = z.infer<typeof HostInvocationDecisionSchema>;
export type HostBinding = z.infer<typeof HostBindingSchema>;
export type CapabilityManifest = z.infer<typeof CapabilityManifestSchema>;
export type InvocationEnvelope = z.infer<typeof InvocationEnvelopeSchema>;
export type ContextLedgerEntry = z.infer<typeof ContextLedgerEntrySchema>;
export type ReviewArtifactFinding = z.infer<typeof ReviewArtifactFindingSchema>;
export type ReviewArtifact = z.infer<typeof ReviewArtifactSchema>;
export type HostBindingRecord = z.infer<typeof HostBindingRecordSchema>;
export type HostInvocationDecisionRecord = z.infer<typeof HostInvocationDecisionRecordSchema>;
export type HostReviewArtifactRecord = z.infer<typeof HostReviewArtifactRecordSchema>;
export type HostContextLedgerRecord = z.infer<typeof HostContextLedgerRecordSchema>;
export type MutationSurface = z.infer<typeof MutationSurfaceSchema>;
export type MutationPolicy = z.infer<typeof MutationPolicySchema>;
export type MutationTrustLevel = z.infer<typeof MutationTrustLevelSchema>;
export type MutationProvenanceSource = z.infer<typeof MutationProvenanceSourceSchema>;
export type MutationProvenance = z.infer<typeof MutationProvenanceSchema>;
export type MutationGuardrail = z.infer<typeof MutationGuardrailSchema>;
export type VerificationChainMetadata = z.infer<typeof VerificationChainMetadataSchema>;
export type VerificationEvidenceRef = z.infer<typeof VerificationEvidenceRefSchema>;
export type VerificationGateResult = z.infer<typeof VerificationGateResultSchema>;
export type SurfaceExecutionRecord = z.infer<typeof SurfaceExecutionRecordSchema>;
export type ToolCallLogEntry = z.infer<typeof ToolCallLogEntrySchema>;
export type ToolCallPayload = z.infer<typeof ToolCallPayloadSchema>;
export type WorkflowStepLogEntry = z.infer<typeof WorkflowStepLogEntrySchema>;
export type WorkflowStepPayload = z.infer<typeof WorkflowStepPayloadSchema>;

export type ReconnectHandshakeEvent = z.infer<typeof ReconnectHandshakeEventSchema>;
export type ConfigUpdateEvent = z.infer<typeof ConfigUpdateEventSchema>;
export type TaskTransitionEvent = z.infer<typeof TaskTransitionEventSchema>;
export type TaskAssignEvent = z.infer<typeof TaskAssignEventSchema>;
export type AgentStatusUpdateEvent = z.infer<typeof AgentStatusUpdateEventSchema>;
export type ClientEvent = z.infer<typeof ClientEventSchema>;

export type StateSnapshotEvent = z.infer<typeof StateSnapshotEventSchema>;
export type AgentStateEvent = z.infer<typeof AgentStateEventSchema>;
export type TaskUpdateEvent = z.infer<typeof TaskUpdateEventSchema>;
export type ToolCallEvent = z.infer<typeof ToolCallEventSchema>;
export type WorkflowStepEvent = z.infer<typeof WorkflowStepEventSchema>;
export type VerificationGateEvent = z.infer<typeof VerificationGateEventSchema>;
export type HostBindingStateEvent = z.infer<typeof HostBindingStateEventSchema>;
export type HostInvocationDecisionEvent = z.infer<typeof HostInvocationDecisionEventSchema>;
export type HostReviewArtifactEvent = z.infer<typeof HostReviewArtifactEventSchema>;
export type HostContextLedgerUpdateEvent = z.infer<typeof HostContextLedgerUpdateEventSchema>;
export type RuntimeErrorEvent = z.infer<typeof RuntimeErrorEventSchema>;
export type ServerEvent = z.infer<typeof ServerEventSchema>;

export const MUTATING_CLIENT_EVENT_TYPES = [
  'CONFIG_UPDATE',
  'TASK_TRANSITION',
  'TASK_ASSIGN',
  'AGENT_STATUS_UPDATE'
] as const;

export const SURFACE_EXECUTION_REGISTRY = SurfaceGovernanceRegistrySchema.parse([
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
] as const) as readonly SurfaceExecutionRecord[];

export function createSurfaceExecutionRegistry(): readonly SurfaceExecutionRecord[] {
  return SURFACE_EXECUTION_REGISTRY.map((record) => ({ ...record }));
}

export interface MutationGuardrailInput {
  policy?: MutationPolicy;
  trustLevel?: MutationTrustLevel;
  provenance?: Partial<MutationProvenance>;
}

export interface VerificationChainInput {
  actionId?: string;
  traceId?: string;
  verificationRef?: string;
  controlsExecuted?: readonly string[];
  evidenceRefs?: readonly string[];
  requestId?: string;
  idempotencyKey?: string;
  hostId?: string;
  verdict?: VerificationGateResult;
  unmetControls?: readonly string[];
}

export function createProtocolTimestamp(): string {
  return new Date().toISOString();
}

function createCanonicalVerificationMetadata(
  requestId: string,
  idempotencyKey: string,
  actionId: string,
  input: VerificationChainInput = {}
): VerificationChainMetadata {
  return VerificationChainMetadataSchema.parse({
    actionId: input.actionId ?? actionId,
    traceId: input.traceId ?? requestId,
    verificationRef: input.verificationRef ?? `verify://${actionId}/${requestId}`,
    controlsExecuted: input.controlsExecuted ?? [`policy:${actionId}`],
    evidenceRefs: input.evidenceRefs ?? [`mutation://${actionId}/${idempotencyKey}`],
    requestId: input.requestId ?? requestId,
    idempotencyKey: input.idempotencyKey ?? idempotencyKey,
    ...(input.hostId === undefined ? {} : { hostId: input.hostId }),
    ...(input.verdict === undefined ? {} : { verdict: input.verdict }),
    unmetControls: input.unmetControls === undefined ? [] : [...input.unmetControls]
  });
}

export function createMutationGuardrail(
  surface: MutationSurface,
  input: MutationGuardrailInput = {}
): MutationGuardrail {
  return MutationGuardrailSchema.parse({
    surface,
    policy: input.policy ?? 'surface_scoped',
    trustLevel: input.trustLevel ?? 'trusted',
    provenance: {
      source: input.provenance?.source ?? 'runtime_ui',
      actorTag: input.provenance?.actorTag ?? 'runtime-dashboard'
    }
  });
}

function resolveMutationGuardrail(
  surface: MutationSurface,
  defaultPolicy: MutationPolicy,
  defaultActorTag: string,
  input?: MutationGuardrailInput
): MutationGuardrail {
  const provenance = {
    ...(input?.provenance?.source === undefined ? {} : { source: input.provenance.source }),
    actorTag: input?.provenance?.actorTag ?? defaultActorTag
  };

  return createMutationGuardrail(surface, {
    policy: input?.policy ?? defaultPolicy,
    ...(input?.trustLevel === undefined ? {} : { trustLevel: input.trustLevel }),
    provenance
  });
}

export function parseClientEvent(input: unknown): ClientEvent {
  return ClientEventSchema.parse(input);
}

export function parseServerEvent(input: unknown): ServerEvent {
  return ServerEventSchema.parse(input);
}

export function safeParseClientEvent(input: unknown) {
  return ClientEventSchema.safeParse(input);
}

export function safeParseServerEvent(input: unknown) {
  return ServerEventSchema.safeParse(input);
}

export function createReconnectHandshake(requestId: string, lastSequenceId?: number): ReconnectHandshakeEvent {
  return ReconnectHandshakeEventSchema.parse({
    type: 'RECONNECT_HANDSHAKE',
    version: RUNTIME_PROTOCOL_VERSION,
    requestId,
    ...(lastSequenceId === undefined ? {} : { lastSequenceId })
  });
}

export function createConfigUpdate(
  requestId: string,
  key: string,
  value: unknown,
  idempotencyKey = requestId,
  guardrail?: MutationGuardrailInput,
  verification?: VerificationChainInput
): ConfigUpdateEvent {
  return ConfigUpdateEventSchema.parse({
    type: 'CONFIG_UPDATE',
    version: RUNTIME_PROTOCOL_VERSION,
    requestId,
    key,
    value,
    idempotencyKey,
    verification: createCanonicalVerificationMetadata(
      requestId,
      idempotencyKey,
      'config.update',
      verification
    ),
    guardrail: resolveMutationGuardrail('runtime_config', 'elevated', 'config.update', guardrail)
  });
}

export function createTaskTransition(
  requestId: string,
  taskId: string,
  status: TaskStatus,
  idempotencyKey = requestId,
  guardrail?: MutationGuardrailInput,
  verification?: VerificationChainInput
): TaskTransitionEvent {
  return TaskTransitionEventSchema.parse({
    type: 'TASK_TRANSITION',
    version: RUNTIME_PROTOCOL_VERSION,
    requestId,
    taskId,
    status,
    idempotencyKey,
    ...((verification === undefined && status !== 'done')
      ? {}
      : {
          verification: createCanonicalVerificationMetadata(
            requestId,
            idempotencyKey,
            `task.transition.${status}`,
            verification
          )
        }),
    guardrail: resolveMutationGuardrail(
      'task_lifecycle',
      status === 'done' ? 'elevated' : 'surface_scoped',
      'task.transition',
      guardrail
    )
  });
}

export function createTaskAssign(
  requestId: string,
  taskId: string,
  assigneeId: string,
  idempotencyKey = requestId,
  guardrail?: MutationGuardrailInput
): TaskAssignEvent {
  return TaskAssignEventSchema.parse({
    type: 'TASK_ASSIGN',
    version: RUNTIME_PROTOCOL_VERSION,
    requestId,
    taskId,
    assigneeId,
    idempotencyKey,
    guardrail: resolveMutationGuardrail('task_assignment', 'surface_scoped', 'task.assign', guardrail)
  });
}

export function createAgentStatusUpdate(
  requestId: string,
  agentId: string,
  status: AgentStatus,
  idempotencyKey = requestId,
  guardrail?: MutationGuardrailInput
): AgentStatusUpdateEvent {
  return AgentStatusUpdateEventSchema.parse({
    type: 'AGENT_STATUS_UPDATE',
    version: RUNTIME_PROTOCOL_VERSION,
    requestId,
    agentId,
    status,
    idempotencyKey,
    guardrail: resolveMutationGuardrail('agent_presence', 'surface_scoped', 'agent.status.update', guardrail)
  });
}

export function createStateSnapshotEvent(
  sequenceId: number,
  snapshot: GameStateSnapshot,
  timestamp = createProtocolTimestamp()
): StateSnapshotEvent {
  return parseServerEvent({
    type: 'STATE_SNAPSHOT',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp,
    snapshot: {
      ...snapshot,
      lastSequenceId: sequenceId
    }
  }) as StateSnapshotEvent;
}

export function createAgentStateEvent(
  sequenceId: number,
  agent: AgentPresence,
  timestamp = createProtocolTimestamp()
): AgentStateEvent {
  return parseServerEvent({
    type: 'AGENT_STATE',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp,
    agent
  }) as AgentStateEvent;
}

export function createTaskUpdateEvent(
  sequenceId: number,
  task: TaskSnapshot,
  options: { timestamp?: string; agent?: AgentPresence } = {}
): TaskUpdateEvent {
  return parseServerEvent({
    type: 'TASK_UPDATE',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    task,
    ...(options.agent === undefined ? {} : { agent: options.agent })
  }) as TaskUpdateEvent;
}

export function createToolCallEvent(
  sequenceId: number,
  call: ToolCallPayload,
  options: { timestamp?: string; agent?: AgentPresence } = {}
): ToolCallEvent {
  return parseServerEvent({
    type: 'TOOL_CALL',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    call,
    ...(options.agent === undefined ? {} : { agent: options.agent })
  }) as ToolCallEvent;
}

export function createWorkflowStepEvent(
  sequenceId: number,
  step: WorkflowStepPayload,
  options: { timestamp?: string; agent?: AgentPresence } = {}
): WorkflowStepEvent {
  return parseServerEvent({
    type: 'WORKFLOW_STEP',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    step,
    ...(options.agent === undefined ? {} : { agent: options.agent })
  }) as WorkflowStepEvent;
}

export interface VerificationGatePayload {
  result: VerificationGateResult;
  actionId: string;
  verificationRef: string;
  evidenceRefs: readonly VerificationEvidenceRef[];
  controlsExecuted: readonly string[];
  unmetControls?: readonly string[];
  traceId?: string;
  taskId?: string;
  meta?: Record<string, JsonValue>;
}

export interface CanonicalEnvelopePilotInput {
  header: {
    messageType: string;
    messageId: string;
    emittedAt: string;
    channel: CanonicalEnvelopeChannel;
    sequenceId?: number;
  };
  context: {
    protocolVersion?: string;
    traceId?: string;
    taskId?: string;
    correlationId?: string;
    verificationRef?: string;
    projectId?: string;
    runId?: string;
    nodeId?: string;
    workerId?: string;
    leaseId?: string;
    worktreeId?: string;
    branch?: string;
  };
  body: JsonValue;
}

export interface EventMetaInput {
  traceId?: string | undefined;
  taskId?: string | undefined;
  correlationId?: string | undefined;
  hostId?: string | undefined;
  policyRef?: string | undefined;
  promptRef?: string | undefined;
  degradedFrom?: string | undefined;
  details?: Record<string, JsonValue> | undefined;
}

export interface HostBindingInput {
  hostId: string;
  hostType: HostType;
  displayName: string;
  version?: string | undefined;
  authMode: HostAuthMode;
  connectionState: HostConnectionState;
  trustStatus: HostTrustStatus;
  scopes?: readonly HostScope[] | undefined;
  capabilityManifestRef: string;
  sourceOfTruth: 'secondary';
  lastSeenAt?: string | undefined;
  notes?: string | undefined;
}

export interface CapabilityManifestInput {
  manifestId: string;
  hostId: string;
  routines?: readonly string[] | undefined;
  toolProviders?: readonly string[] | undefined;
  reviewChannels?: readonly string[] | undefined;
  contextSources?: readonly string[] | undefined;
  permissionMode: HostPermissionMode;
  supportsStreaming: boolean;
  supportsReviewImport: boolean;
  supportsContextImport: boolean;
  supportsPreviewCommit: boolean;
}

export interface InvocationEnvelopeInput {
  envelopeId: string;
  hostId: string;
  actionKind: HostActionKind;
  mode: HostActionMode;
  correlationId: string;
  idempotencyKey: string;
  traceId?: string | undefined;
  taskId?: string | undefined;
  requestedScopes?: readonly HostScope[] | undefined;
  payload: JsonValue;
  evidencePolicy: HostEvidencePolicy;
}

export interface ContextLedgerEntryInput {
  entryId: string;
  hostId: string;
  sourceType: HostContextSourceType;
  visibility: HostContextVisibility;
  confidence: number;
  importedAt: string;
  ttlSeconds: number;
  contentRef: string;
  supersedes?: string | undefined;
  trustStatus: HostContextTrustStatus;
}

export interface ReviewArtifactFindingInput {
  id: string;
  severity: HostReviewFindingSeverity;
  message: string;
  resolutionStatus?: HostReviewResolutionStatus | undefined;
}

export interface ReviewArtifactInput {
  reviewId: string;
  hostId: string;
  sourceType: HostReviewSourceType;
  subjectRef: string;
  verdict: HostReviewVerdict;
  findings: readonly ReviewArtifactFindingInput[];
  linkedEvidenceRefs?: readonly string[] | undefined;
  importedAt: string;
  traceId?: string | undefined;
  taskId?: string | undefined;
}

export interface HostInvocationDecisionPayload {
  envelope: InvocationEnvelopeInput;
  decision: HostInvocationDecision;
  reason: string;
  meta?: EventMetaInput;
}

export interface HostBindingStatePayload {
  binding: HostBindingInput;
  manifest: CapabilityManifestInput;
  reason?: string;
}

export interface HostReviewArtifactPayload {
  review: ReviewArtifactInput;
  meta?: EventMetaInput;
}

export interface HostContextLedgerUpdatePayload {
  entry: ContextLedgerEntryInput;
  meta?: EventMetaInput;
}

export function createVerificationGateEvent(
  sequenceId: number,
  gate: VerificationGatePayload,
  options: { timestamp?: string } = {}
): VerificationGateEvent {
  return parseServerEvent({
    type: 'VERIFICATION_GATE',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    result: gate.result,
    actionId: gate.actionId,
    verificationRef: gate.verificationRef,
    evidenceRefs: gate.evidenceRefs,
    controlsExecuted: gate.controlsExecuted,
    ...(gate.unmetControls === undefined ? {} : { unmetControls: gate.unmetControls }),
    ...(gate.traceId === undefined ? {} : { traceId: gate.traceId }),
    ...(gate.taskId === undefined ? {} : { taskId: gate.taskId }),
    ...(gate.meta === undefined ? {} : { meta: gate.meta })
  }) as VerificationGateEvent;
}

export function createEventMeta(input: EventMetaInput = {}): EventMeta {
  return EventMetaSchema.parse({
    ...(input.traceId === undefined ? {} : { traceId: input.traceId }),
    ...(input.taskId === undefined ? {} : { taskId: input.taskId }),
    ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId }),
    ...(input.hostId === undefined ? {} : { hostId: input.hostId }),
    ...(input.policyRef === undefined ? {} : { policyRef: input.policyRef }),
    ...(input.promptRef === undefined ? {} : { promptRef: input.promptRef }),
    ...(input.degradedFrom === undefined ? {} : { degradedFrom: input.degradedFrom }),
    ...(input.details === undefined ? {} : { details: input.details })
  });
}

export function createHostBindingStateEvent(
  sequenceId: number,
  payload: HostBindingStatePayload,
  options: { timestamp?: string } = {}
): HostBindingStateEvent {
  return parseServerEvent({
    type: 'HOST_BINDING_STATE',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    binding: payload.binding,
    manifest: payload.manifest,
    ...(payload.reason === undefined ? {} : { reason: payload.reason })
  }) as HostBindingStateEvent;
}

export function createHostInvocationDecisionEvent(
  sequenceId: number,
  payload: HostInvocationDecisionPayload,
  options: { timestamp?: string } = {}
): HostInvocationDecisionEvent {
  return parseServerEvent({
    type: 'HOST_INVOCATION_DECISION',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    envelope: payload.envelope,
    decision: payload.decision,
    reason: payload.reason,
    meta: createEventMeta(payload.meta)
  }) as HostInvocationDecisionEvent;
}

export function createHostReviewArtifactEvent(
  sequenceId: number,
  payload: HostReviewArtifactPayload,
  options: { timestamp?: string } = {}
): HostReviewArtifactEvent {
  return parseServerEvent({
    type: 'HOST_REVIEW_ARTIFACT',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    review: payload.review,
    meta: createEventMeta(payload.meta)
  }) as HostReviewArtifactEvent;
}

export function createHostContextLedgerUpdateEvent(
  sequenceId: number,
  payload: HostContextLedgerUpdatePayload,
  options: { timestamp?: string } = {}
): HostContextLedgerUpdateEvent {
  return parseServerEvent({
    type: 'HOST_CONTEXT_LEDGER_UPDATE',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp: options.timestamp ?? createProtocolTimestamp(),
    entry: payload.entry,
    meta: createEventMeta(payload.meta)
  }) as HostContextLedgerUpdateEvent;
}

export function createControlPlaneRunContext(input: ControlPlaneRunContext): ControlPlaneRunContext {
  return ControlPlaneRunContextSchema.parse({
    projectId: input.projectId,
    runId: input.runId,
    ...(input.nodeId === undefined ? {} : { nodeId: input.nodeId }),
    ...(input.workerId === undefined ? {} : { workerId: input.workerId }),
    ...(input.leaseId === undefined ? {} : { leaseId: input.leaseId }),
    ...(input.worktreeId === undefined ? {} : { worktreeId: input.worktreeId }),
    ...(input.branch === undefined ? {} : { branch: input.branch })
  });
}

export function createCanonicalEnvelopePilot(input: CanonicalEnvelopePilotInput): CanonicalEnvelopePilot {
  return CanonicalEnvelopePilotSchema.parse({
    header: {
      messageType: input.header.messageType,
      messageVersion: 'pilot-v1',
      messageId: input.header.messageId,
      emittedAt: input.header.emittedAt,
      channel: input.header.channel,
      ...(input.header.sequenceId === undefined ? {} : { sequenceId: input.header.sequenceId })
    },
    context: {
      protocolVersion:
        input.context.protocolVersion === undefined ? RUNTIME_PROTOCOL_VERSION : input.context.protocolVersion,
      ...(input.context.traceId === undefined ? {} : { traceId: input.context.traceId }),
      ...(input.context.taskId === undefined ? {} : { taskId: input.context.taskId }),
      ...(input.context.correlationId === undefined ? {} : { correlationId: input.context.correlationId }),
      ...(input.context.verificationRef === undefined ? {} : { verificationRef: input.context.verificationRef }),
      ...(input.context.projectId === undefined ? {} : { projectId: input.context.projectId }),
      ...(input.context.runId === undefined ? {} : { runId: input.context.runId }),
      ...(input.context.nodeId === undefined ? {} : { nodeId: input.context.nodeId }),
      ...(input.context.workerId === undefined ? {} : { workerId: input.context.workerId }),
      ...(input.context.leaseId === undefined ? {} : { leaseId: input.context.leaseId }),
      ...(input.context.worktreeId === undefined ? {} : { worktreeId: input.context.worktreeId }),
      ...(input.context.branch === undefined ? {} : { branch: input.context.branch })
    },
    body: input.body
  });
}

export function createSecurityFindingPayload(payload: SecurityFindingPayload): SecurityFindingPayload {
  return SecurityFindingPayloadSchema.parse(payload);
}

export function createBranchFinishOptionsPayload(
  payload: BranchFinishOptionsPayloadInput
): BranchFinishOptionsPayload {
  return BranchFinishOptionsPayloadSchema.parse({
    branch: payload.branch,
    testsPassed: payload.testsPassed,
    ...(payload.allowedOptions === undefined ? {} : { allowedOptions: payload.allowedOptions }),
    ...(payload.typedDiscardConfirmation === undefined
      ? {}
      : { typedDiscardConfirmation: payload.typedDiscardConfirmation })
  });
}

export function createBranchFinishDecisionPayload(
  payload: BranchFinishDecisionPayloadInput
): BranchFinishDecisionPayload {
  return BranchFinishDecisionPayloadSchema.parse({
    branch: payload.branch,
    selectedOption: payload.selectedOption,
    ...(payload.typedConfirmation === undefined ? {} : { typedConfirmation: payload.typedConfirmation })
  });
}

export function createErrorEvent(
  sequenceId: number,
  code: string,
  message: string,
  correlationId?: string,
  retryable = false,
  timestamp = createProtocolTimestamp()
): RuntimeErrorEvent {
  return parseServerEvent({
    type: 'ERROR',
    version: RUNTIME_PROTOCOL_VERSION,
    sequenceId,
    timestamp,
    code,
    message,
    retryable,
    ...(correlationId === undefined ? {} : { correlationId })
  }) as RuntimeErrorEvent;
}

export function isMutatingClientEvent(event: ClientEvent): boolean {
  return (
    event.type === 'CONFIG_UPDATE' ||
    event.type === 'TASK_TRANSITION' ||
    event.type === 'TASK_ASSIGN' ||
    event.type === 'AGENT_STATUS_UPDATE'
  );
}