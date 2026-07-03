import { z } from 'zod';

export const RUNTIME_PROTOCOL_VERSION = 'v1';
export const CONTROL_PLANE_REGISTRY_VERSION = 'control-plane-v1';
export const NODE_REGISTRY_VERSION = 'node-registry-v1';
export const LEASE_STORE_VERSION = 'lease-store-v1';

export const MUTATION_SURFACES = [
  'runtime_config',
  'task_lifecycle',
  'task_assignment',
  'agent_presence'
] as const;
export const MUTATION_POLICIES = ['surface_scoped', 'elevated', 'read_only'] as const;
export const MUTATION_TRUST_LEVELS = ['trusted', 'restricted', 'blocked'] as const;
export const MUTATION_PROVENANCE_SOURCES = [
  'runtime_ui',
  'runtime_adapter',
  'runtime_replay',
  'runtime_api'
] as const;
export const HOST_TYPES = ['copilot', 'claude', 'mcp', 'ide', 'other'] as const;
export const HOST_AUTH_MODES = ['none', 'session', 'token', 'oauth', 'delegated'] as const;
export const HOST_CONNECTION_STATES = ['online', 'stale', 'degraded', 'offline', 'blocked'] as const;
export const HOST_TRUST_STATUSES = ['trusted', 'review', 'restricted', 'blocked'] as const;
export const HOST_SCOPES = ['fs', 'network', 'secrets', 'exec', 'config_write', 'write_budget'] as const;
export const HOST_PERMISSION_MODES = ['none', 'prompt', 'policy', 'hybrid'] as const;
export const HOST_ACTION_KINDS = [
  'tool_call',
  'routine',
  'review_import',
  'context_import',
  'permission_prompt'
] as const;
export const HOST_ACTION_MODES = ['read', 'preview', 'validate', 'commit'] as const;
export const HOST_EVIDENCE_POLICIES = ['none', 'basic', 'strict'] as const;
export const HOST_CONTEXT_SOURCE_TYPES = [
  'instructions',
  'memory',
  'selection',
  'session_context',
  'review_summary'
] as const;
export const HOST_CONTEXT_VISIBILITIES = ['private', 'shared', 'audit_only'] as const;
export const HOST_CONTEXT_TRUST_STATUSES = ['trusted', 'review', 'restricted'] as const;
export const HOST_REVIEW_SOURCE_TYPES = [
  'copilot_review',
  'claude_review',
  'github_check',
  'github_pr_comment',
  'mcp_review',
  'other'
] as const;
export const HOST_REVIEW_VERDICTS = ['pass', 'warn', 'fail', 'comment'] as const;
export const HOST_REVIEW_FINDING_SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;
export const HOST_REVIEW_RESOLUTION_STATUSES = ['open', 'acknowledged', 'resolved', 'wont_fix'] as const;
export const HOST_INVOCATION_DECISIONS = ['ALLOW', 'PROMPT', 'DENY', 'DEGRADE'] as const;

export const CLIENT_EVENT_TYPES = [
  'RECONNECT_HANDSHAKE',
  'CONFIG_UPDATE',
  'TASK_TRANSITION',
  'TASK_ASSIGN',
  'AGENT_STATUS_UPDATE'
] as const;
export const SERVER_EVENT_TYPES = [
  'STATE_SNAPSHOT',
  'AGENT_STATE',
  'TASK_UPDATE',
  'TOOL_CALL',
  'WORKFLOW_STEP',
  'VERIFICATION_GATE',
  'HOST_BINDING_STATE',
  'HOST_INVOCATION_DECISION',
  'HOST_REVIEW_ARTIFACT',
  'HOST_CONTEXT_LEDGER_UPDATE',
  'ERROR'
] as const;

export type ClientEventType = (typeof CLIENT_EVENT_TYPES)[number];
export type ServerEventType = (typeof SERVER_EVENT_TYPES)[number];

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

const NonEmptyStringSchema = z.string().min(1);
const NullableNonEmptyStringSchema = NonEmptyStringSchema.nullable();
const MutationIdentitySchema = z
  .string()
  .min(1)
  .max(128)
  .regex(/^[A-Za-z0-9._:-]+$/)
  .refine((value) => value.trim() === value, {
    message: 'Mutation identity must not contain leading or trailing spaces.'
  });

export const JsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.string(),
    z.number(),
    z.boolean(),
    z.null(),
    z.array(JsonValueSchema),
    z.record(JsonValueSchema)
  ])
);

export const ConfigSnapshotSchema = z.record(JsonValueSchema);

export const PointSchema = z
  .object({
    x: z.number().finite(),
    y: z.number().finite()
  })
  .strict();

export const AgentRoleSchema = z.enum(['orchestrator', 'agent', 'spectator']);
export const AgentStatusSchema = z.enum(['idle', 'working', 'paused', 'offline']);
export const TaskStatusSchema = z.enum(['backlog', 'todo', 'in_progress', 'review', 'done']);
export const TaskPrioritySchema = z.enum(['critical', 'high', 'medium', 'low']);
export const TaskKindSchema = z.enum(['feature', 'bug', 'research', 'ops', 'security']);
export const VerificationGateResultSchema = z.enum(['PASS', 'FAIL']);
export const SecurityFindingSeveritySchema = z.enum(['critical', 'high', 'medium', 'info']);
export const SecurityFindingStatusSchema = z.enum(['open', 'resolved']);
export const BranchFinishOptionSchema = z.enum(['merge', 'pr', 'keep', 'discard']);
export const CanonicalEnvelopeChannelSchema = z.enum(['runtime', 'replay', 'spectator', 'session']);
export const MutationSurfaceSchema = z.enum(MUTATION_SURFACES);
export const MutationPolicySchema = z.enum(MUTATION_POLICIES);
export const MutationTrustLevelSchema = z.enum(MUTATION_TRUST_LEVELS);
export const MutationProvenanceSourceSchema = z.enum(MUTATION_PROVENANCE_SOURCES);
export const HostTypeSchema = z.enum(HOST_TYPES);
export const HostAuthModeSchema = z.enum(HOST_AUTH_MODES);
export const HostConnectionStateSchema = z.enum(HOST_CONNECTION_STATES);
export const HostTrustStatusSchema = z.enum(HOST_TRUST_STATUSES);
export const HostScopeSchema = z.enum(HOST_SCOPES);
export const HostPermissionModeSchema = z.enum(HOST_PERMISSION_MODES);
export const HostActionKindSchema = z.enum(HOST_ACTION_KINDS);
export const HostActionModeSchema = z.enum(HOST_ACTION_MODES);
export const HostEvidencePolicySchema = z.enum(HOST_EVIDENCE_POLICIES);
export const HostContextSourceTypeSchema = z.enum(HOST_CONTEXT_SOURCE_TYPES);
export const HostContextVisibilitySchema = z.enum(HOST_CONTEXT_VISIBILITIES);
export const HostContextTrustStatusSchema = z.enum(HOST_CONTEXT_TRUST_STATUSES);
export const HostReviewSourceTypeSchema = z.enum(HOST_REVIEW_SOURCE_TYPES);
export const HostReviewVerdictSchema = z.enum(HOST_REVIEW_VERDICTS);
export const HostReviewFindingSeveritySchema = z.enum(HOST_REVIEW_FINDING_SEVERITIES);
export const HostReviewResolutionStatusSchema = z.enum(HOST_REVIEW_RESOLUTION_STATUSES);
export const HostInvocationDecisionSchema = z.enum(HOST_INVOCATION_DECISIONS);

export const VerificationEvidenceRefSchema = z
  .object({
    kind: z.enum(['test', 'log', 'coverage', 'artifact', 'screenshot']),
    ref: NonEmptyStringSchema
  })
  .strict();

export const VerificationChainMetadataSchema = z
  .object({
    actionId: NonEmptyStringSchema,
    traceId: NonEmptyStringSchema,
    verificationRef: NonEmptyStringSchema,
    controlsExecuted: z.array(NonEmptyStringSchema).min(1),
    evidenceRefs: z.array(NonEmptyStringSchema).min(1),
    requestId: MutationIdentitySchema.optional(),
    idempotencyKey: MutationIdentitySchema.optional(),
    hostId: NonEmptyStringSchema.optional(),
    verdict: VerificationGateResultSchema.optional(),
    unmetControls: z.array(NonEmptyStringSchema).default([])
  })
  .strict();

export const SecurityFindingPayloadSchema = z
  .object({
    findingId: NonEmptyStringSchema,
    title: NonEmptyStringSchema,
    severity: SecurityFindingSeveritySchema,
    status: SecurityFindingStatusSchema,
    confidenceScore: z.number().min(0).max(10),
    exploitScenario: NonEmptyStringSchema,
    surfaceId: NonEmptyStringSchema,
    origin: NonEmptyStringSchema.optional(),
    requiredPolicy: NonEmptyStringSchema.optional(),
    trustStatus: MutationTrustLevelSchema.optional(),
    owaspCategory: NonEmptyStringSchema.optional(),
    strideCategory: NonEmptyStringSchema.optional(),
    agenticSkillCategory: NonEmptyStringSchema.optional(),
    controls: z.array(NonEmptyStringSchema).default([])
  })
  .strict();

export const BranchFinishOptionsPayloadSchema = z
  .object({
    branch: NonEmptyStringSchema,
    testsPassed: z.boolean(),
    allowedOptions: z.array(BranchFinishOptionSchema).default(['merge', 'pr', 'keep', 'discard']),
    typedDiscardConfirmation: NonEmptyStringSchema.default('DISCARD')
  })
  .strict();

export const BranchFinishDecisionPayloadSchema = z
  .object({
    branch: NonEmptyStringSchema,
    selectedOption: BranchFinishOptionSchema,
    typedConfirmation: z.string().default('')
  })
  .strict();

export const ControlPlaneRunContextSchema = z
  .object({
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    nodeId: NonEmptyStringSchema.optional(),
    workerId: NonEmptyStringSchema.optional(),
    leaseId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional(),
    branch: NonEmptyStringSchema.optional()
  })
  .strict()
  .refine((value) => value.workerId === undefined || value.nodeId !== undefined, {
    message: 'nodeId is required when workerId is provided.'
  });

export const CanonicalEnvelopeContextSchema = z
  .object({
    protocolVersion: z.literal(RUNTIME_PROTOCOL_VERSION),
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    correlationId: NonEmptyStringSchema.optional(),
    verificationRef: NonEmptyStringSchema.optional(),
    projectId: NonEmptyStringSchema.optional(),
    runId: NonEmptyStringSchema.optional(),
    nodeId: NonEmptyStringSchema.optional(),
    workerId: NonEmptyStringSchema.optional(),
    leaseId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional(),
    branch: NonEmptyStringSchema.optional()
  })
  .strict()
  .refine((value) => (value.projectId === undefined) === (value.runId === undefined), {
    message: 'projectId and runId must be provided together.'
  })
  .refine(
    (value) =>
      value.workerId === undefined ||
      (value.projectId !== undefined && value.runId !== undefined && value.nodeId !== undefined),
    {
      message: 'projectId, runId and nodeId are required when workerId is provided.'
    }
  )
  .refine(
    (value) =>
      value.leaseId === undefined ||
      (value.projectId !== undefined && value.runId !== undefined),
    {
      message: 'projectId and runId are required when leaseId is provided.'
    }
  )
  .refine(
    (value) =>
      value.worktreeId === undefined ||
      (value.projectId !== undefined && value.runId !== undefined),
    {
      message: 'projectId and runId are required when worktreeId is provided.'
    }
  )
  .refine(
    (value) => value.branch === undefined || (value.projectId !== undefined && value.runId !== undefined),
    {
      message: 'projectId and runId are required when branch is provided.'
    }
  );

export const CanonicalEnvelopeHeaderSchema = z
  .object({
    messageType: NonEmptyStringSchema,
    messageVersion: z.literal('pilot-v1'),
    messageId: NonEmptyStringSchema,
    emittedAt: NonEmptyStringSchema,
    channel: CanonicalEnvelopeChannelSchema,
    sequenceId: z.number().int().min(0).optional()
  })
  .strict();

export const CanonicalEnvelopePilotSchema = z
  .object({
    header: CanonicalEnvelopeHeaderSchema,
    context: CanonicalEnvelopeContextSchema,
    body: JsonValueSchema
  })
  .strict();

export const ProjectRegistryRecordSchema = z
  .object({
    protocolVersion: z.literal(RUNTIME_PROTOCOL_VERSION),
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    firstEventAt: NonEmptyStringSchema,
    lastEventAt: NonEmptyStringSchema,
    firstSequenceId: z.number().int().min(0),
    lastSequenceId: z.number().int().min(0),
    eventCount: z.number().int().positive(),
    lastMessageId: NonEmptyStringSchema,
    lastCorrelationId: NonEmptyStringSchema.optional(),
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    nodeId: NonEmptyStringSchema.optional(),
    workerId: NonEmptyStringSchema.optional(),
    leaseId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional(),
    nodeIds: z.array(NonEmptyStringSchema),
    workerIds: z.array(NonEmptyStringSchema),
    leaseIds: z.array(NonEmptyStringSchema),
    worktreeIds: z.array(NonEmptyStringSchema),
    channels: z.array(CanonicalEnvelopeChannelSchema).min(1),
    messageTypes: z.array(NonEmptyStringSchema).min(1)
  })
  .strict()
  .refine((value) => value.firstSequenceId <= value.lastSequenceId, {
    message: 'firstSequenceId must be less than or equal to lastSequenceId.'
  })
  .refine((value) => value.nodeId === undefined || value.nodeIds.includes(value.nodeId), {
    message: 'nodeId must be included in nodeIds.'
  })
  .refine((value) => value.workerId === undefined || value.workerIds.includes(value.workerId), {
    message: 'workerId must be included in workerIds.'
  })
  .refine((value) => value.leaseId === undefined || value.leaseIds.includes(value.leaseId), {
    message: 'leaseId must be included in leaseIds.'
  })
  .refine((value) => value.worktreeId === undefined || value.worktreeIds.includes(value.worktreeId), {
    message: 'worktreeId must be included in worktreeIds.'
  });

export const ProjectRegistrySnapshotSchema = z
  .object({
    registryVersion: z.literal(CONTROL_PLANE_REGISTRY_VERSION),
    generatedAt: NonEmptyStringSchema,
    activeProject: ProjectRegistryRecordSchema
  })
  .strict();
export const NodeRegistryStatusSchema = z.enum(['live', 'stale', 'offline']);
export const NodeWorkerRecordSchema = z
  .object({
    workerId: NonEmptyStringSchema,
    firstSeenAt: NonEmptyStringSchema,
    lastSeenAt: NonEmptyStringSchema,
    firstSequenceId: z.number().int().min(0),
    lastSequenceId: z.number().int().min(0),
    messageCount: z.number().int().positive(),
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    leaseId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional()
  })
  .strict()
  .refine((value) => value.firstSequenceId <= value.lastSequenceId, {
    message: 'firstSequenceId must be less than or equal to lastSequenceId.'
  });
export const NodeRegistryRecordSchema = z
  .object({
    protocolVersion: z.literal(RUNTIME_PROTOCOL_VERSION),
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    nodeId: NonEmptyStringSchema,
    firstSeenAt: NonEmptyStringSchema,
    lastSeenAt: NonEmptyStringSchema,
    firstSequenceId: z.number().int().min(0),
    lastSequenceId: z.number().int().min(0),
    messageCount: z.number().int().positive(),
    staleAfterMs: z.number().int().positive(),
    offlineAfterMs: z.number().int().positive(),
    ageMs: z.number().int().min(0),
    status: NodeRegistryStatusSchema,
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    leaseId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional(),
    capabilityTags: z.array(NonEmptyStringSchema),
    workerIds: z.array(NonEmptyStringSchema),
    workers: z.array(NodeWorkerRecordSchema),
    channels: z.array(CanonicalEnvelopeChannelSchema).min(1),
    messageTypes: z.array(NonEmptyStringSchema).min(1)
  })
  .strict()
  .refine((value) => value.firstSequenceId <= value.lastSequenceId, {
    message: 'firstSequenceId must be less than or equal to lastSequenceId.'
  })
  .refine((value) => value.offlineAfterMs > value.staleAfterMs, {
    message: 'offlineAfterMs must be greater than staleAfterMs.'
  })
  .refine((value) => value.workerIds.length === value.workers.length, {
    message: 'workerIds must contain one entry per worker.'
  })
  .refine(
    (value) => value.workers.every((worker) => value.workerIds.includes(worker.workerId)),
    {
      message: 'workers must all be listed in workerIds.'
    }
  );
export const NodeRegistrySummarySchema = z
  .object({
    nodeCount: z.number().int().min(0),
    liveNodeCount: z.number().int().min(0),
    staleNodeCount: z.number().int().min(0),
    offlineNodeCount: z.number().int().min(0),
    workerCount: z.number().int().min(0)
  })
  .strict()
  .refine((value) => value.nodeCount === value.liveNodeCount + value.staleNodeCount + value.offlineNodeCount, {
    message: 'nodeCount must equal the sum of live, stale and offline nodes.'
  });
export const NodeRegistrySnapshotSchema = z
  .object({
    registryVersion: z.literal(NODE_REGISTRY_VERSION),
    generatedAt: NonEmptyStringSchema,
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    nodes: z.array(NodeRegistryRecordSchema),
    summary: NodeRegistrySummarySchema
  })
  .strict()
  .refine((value) => value.summary.nodeCount === value.nodes.length, {
    message: 'summary.nodeCount must match the number of nodes.'
  })
  .refine(
    (value) =>
      value.summary.workerCount === value.nodes.reduce((count, node) => count + node.workerIds.length, 0),
    {
      message: 'summary.workerCount must match the total number of workers.'
    }
  );

export const LeaseStatusSchema = z.enum(['active', 'expired']);

export const LeaseRecordSchema = z
  .object({
    protocolVersion: z.literal(RUNTIME_PROTOCOL_VERSION),
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    leaseId: NonEmptyStringSchema,
    taskId: NonEmptyStringSchema,
    nodeId: NonEmptyStringSchema,
    workerId: NonEmptyStringSchema.optional(),
    worktreeId: NonEmptyStringSchema.optional(),
    branch: NonEmptyStringSchema.optional(),
    claimedAt: NonEmptyStringSchema,
    lastRenewedAt: NonEmptyStringSchema,
    expiresAt: NonEmptyStringSchema,
    ttlMs: z.number().int().positive(),
    ageMs: z.number().int().min(0),
    status: LeaseStatusSchema,
    messageCount: z.number().int().positive(),
    lastSequenceId: z.number().int().min(0),
    traceId: NonEmptyStringSchema.optional(),
    channels: z.array(CanonicalEnvelopeChannelSchema).min(1),
    messageTypes: z.array(NonEmptyStringSchema).min(1)
  })
  .strict();

export const LeaseStoreSummarySchema = z
  .object({
    leaseCount: z.number().int().min(0),
    activeLeaseCount: z.number().int().min(0),
    expiredLeaseCount: z.number().int().min(0)
  })
  .strict()
  .refine((value) => value.leaseCount === value.activeLeaseCount + value.expiredLeaseCount, {
    message: 'leaseCount must equal the sum of active and expired leases.'
  });

export const LeaseStoreSnapshotSchema = z
  .object({
    registryVersion: z.literal(LEASE_STORE_VERSION),
    generatedAt: NonEmptyStringSchema,
    projectId: NonEmptyStringSchema,
    runId: NonEmptyStringSchema,
    leases: z.array(LeaseRecordSchema),
    summary: LeaseStoreSummarySchema
  })
  .strict()
  .refine((value) => value.summary.leaseCount === value.leases.length, {
    message: 'summary.leaseCount must match the number of leases.'
  });

export const MutationProvenanceSchema = z
  .object({
    source: MutationProvenanceSourceSchema,
    actorTag: NonEmptyStringSchema
  })
  .strict();

export const MutationGuardrailSchema = z
  .object({
    surface: MutationSurfaceSchema,
    policy: MutationPolicySchema,
    trustLevel: MutationTrustLevelSchema,
    provenance: MutationProvenanceSchema
  })
  .strict();

export const SurfaceExecutionRecordSchema = z
  .object({
    surface: MutationSurfaceSchema,
    origin: MutationProvenanceSourceSchema,
    requiredPolicy: MutationPolicySchema,
    trustStatus: MutationTrustLevelSchema
  })
  .strict();

export const SurfaceGovernanceRegistrySchema = z.array(SurfaceExecutionRecordSchema);

export const EventMetaSchema = z
  .object({
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    correlationId: NonEmptyStringSchema.optional(),
    hostId: NonEmptyStringSchema.optional(),
    policyRef: NonEmptyStringSchema.optional(),
    promptRef: NonEmptyStringSchema.optional(),
    degradedFrom: NonEmptyStringSchema.optional(),
    details: z.record(JsonValueSchema).optional()
  })
  .strict();

export const HostBindingSchema = z
  .object({
    hostId: NonEmptyStringSchema,
    hostType: HostTypeSchema,
    displayName: NonEmptyStringSchema,
    version: NonEmptyStringSchema.optional(),
    authMode: HostAuthModeSchema,
    connectionState: HostConnectionStateSchema,
    trustStatus: HostTrustStatusSchema,
    scopes: z.array(HostScopeSchema).default([]),
    capabilityManifestRef: NonEmptyStringSchema,
    sourceOfTruth: z.literal('secondary'),
    lastSeenAt: NonEmptyStringSchema.optional(),
    notes: NonEmptyStringSchema.optional()
  })
  .strict();

export const CapabilityManifestSchema = z
  .object({
    manifestId: NonEmptyStringSchema,
    hostId: NonEmptyStringSchema,
    routines: z.array(NonEmptyStringSchema).default([]),
    toolProviders: z.array(NonEmptyStringSchema).default([]),
    reviewChannels: z.array(NonEmptyStringSchema).default([]),
    contextSources: z.array(NonEmptyStringSchema).default([]),
    permissionMode: HostPermissionModeSchema,
    supportsStreaming: z.boolean(),
    supportsReviewImport: z.boolean(),
    supportsContextImport: z.boolean(),
    supportsPreviewCommit: z.boolean()
  })
  .strict();

export const InvocationEnvelopeSchema = z
  .object({
    envelopeId: NonEmptyStringSchema,
    hostId: NonEmptyStringSchema,
    actionKind: HostActionKindSchema,
    mode: HostActionModeSchema,
    correlationId: NonEmptyStringSchema,
    idempotencyKey: NonEmptyStringSchema,
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    requestedScopes: z.array(HostScopeSchema).default([]),
    payload: JsonValueSchema,
    evidencePolicy: HostEvidencePolicySchema
  })
  .strict();

export const ContextLedgerEntrySchema = z
  .object({
    entryId: NonEmptyStringSchema,
    hostId: NonEmptyStringSchema,
    sourceType: HostContextSourceTypeSchema,
    visibility: HostContextVisibilitySchema,
    confidence: z.number().min(0).max(10),
    importedAt: NonEmptyStringSchema,
    ttlSeconds: z.number().int().positive(),
    contentRef: NonEmptyStringSchema,
    supersedes: NonEmptyStringSchema.optional(),
    trustStatus: HostContextTrustStatusSchema
  })
  .strict();

export const ReviewArtifactFindingSchema = z
  .object({
    id: NonEmptyStringSchema,
    severity: HostReviewFindingSeveritySchema,
    message: NonEmptyStringSchema,
    resolutionStatus: HostReviewResolutionStatusSchema.default('open')
  })
  .strict();

export const ReviewArtifactSchema = z
  .object({
    reviewId: NonEmptyStringSchema,
    hostId: NonEmptyStringSchema,
    sourceType: HostReviewSourceTypeSchema,
    subjectRef: NonEmptyStringSchema,
    verdict: HostReviewVerdictSchema,
    findings: z.array(ReviewArtifactFindingSchema).min(1),
    linkedEvidenceRefs: z.array(NonEmptyStringSchema).default([]),
    importedAt: NonEmptyStringSchema,
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional()
  })
  .strict();

export const HostBindingRecordSchema = z
  .object({
    sequenceId: z.number().int().min(0),
    timestamp: NonEmptyStringSchema,
    binding: HostBindingSchema,
    manifest: CapabilityManifestSchema,
    reason: NonEmptyStringSchema.optional()
  })
  .strict();

export const HostInvocationDecisionRecordSchema = z
  .object({
    sequenceId: z.number().int().min(0),
    timestamp: NonEmptyStringSchema,
    envelope: InvocationEnvelopeSchema,
    decision: HostInvocationDecisionSchema,
    reason: NonEmptyStringSchema,
    meta: EventMetaSchema
  })
  .strict();

export const HostReviewArtifactRecordSchema = z
  .object({
    sequenceId: z.number().int().min(0),
    timestamp: NonEmptyStringSchema,
    review: ReviewArtifactSchema,
    meta: EventMetaSchema
  })
  .strict();

export const HostContextLedgerRecordSchema = z
  .object({
    sequenceId: z.number().int().min(0),
    timestamp: NonEmptyStringSchema,
    entry: ContextLedgerEntrySchema,
    meta: EventMetaSchema
  })
  .strict();

export const AgentPresenceSchema = z
  .object({
    id: NonEmptyStringSchema,
    name: NonEmptyStringSchema,
    role: AgentRoleSchema,
    status: AgentStatusSchema,
    roomId: NonEmptyStringSchema,
    position: PointSchema,
    parentId: NullableNonEmptyStringSchema.optional(),
    lastTool: NullableNonEmptyStringSchema.optional()
  })
  .strict();

export const TaskSnapshotSchema = z
  .object({
    id: NonEmptyStringSchema,
    title: NonEmptyStringSchema,
    status: TaskStatusSchema,
    priority: TaskPrioritySchema.optional(),
    kind: TaskKindSchema.optional(),
    dependencyIds: z.array(NonEmptyStringSchema).optional(),
    blockedReason: NullableNonEmptyStringSchema.optional(),
    assigneeId: NullableNonEmptyStringSchema.optional()
  })
  .strict();

export const ToolCallPayloadSchema = z
  .object({
    tool: NonEmptyStringSchema,
    params: z.record(JsonValueSchema).default({}),
    sourceEventType: NonEmptyStringSchema,
    traceId: NonEmptyStringSchema.optional()
  })
  .strict();

export const ToolCallLogEntrySchema = ToolCallPayloadSchema.extend({
  sequenceId: z.number().int().min(0),
  timestamp: NonEmptyStringSchema,
  agentId: NonEmptyStringSchema.optional()
}).strict();

export const WorkflowStepPayloadSchema = z
  .object({
    step: NonEmptyStringSchema,
    detail: NonEmptyStringSchema,
    sourceEventType: NonEmptyStringSchema,
    traceId: NonEmptyStringSchema.optional(),
    taskId: NonEmptyStringSchema.optional(),
    metadata: z.record(JsonValueSchema).default({})
  })
  .strict();

export const WorkflowStepLogEntrySchema = WorkflowStepPayloadSchema.extend({
  sequenceId: z.number().int().min(0),
  timestamp: NonEmptyStringSchema,
  agentId: NonEmptyStringSchema.optional()
}).strict();

export const GameStateSnapshotSchema = z
  .object({
    protocolVersion: z.literal(RUNTIME_PROTOCOL_VERSION),
    generatedAt: NonEmptyStringSchema,
    lastSequenceId: z.number().int().min(0),
    agents: z.array(AgentPresenceSchema),
    tasks: z.array(TaskSnapshotSchema),
    config: ConfigSnapshotSchema.default({}),
    recentToolCalls: z.array(ToolCallLogEntrySchema).default([]),
    recentWorkflowSteps: z.array(WorkflowStepLogEntrySchema).default([]),
    hostBindings: z.record(HostBindingRecordSchema).optional(),
    recentHostInvocationDecisions: z.array(HostInvocationDecisionRecordSchema).optional(),
    recentHostContextEntries: z.array(HostContextLedgerRecordSchema).optional(),
    recentHostReviews: z.array(HostReviewArtifactRecordSchema).optional()
  })
  .strict();

export const ClientEventBaseSchema = z
  .object({
    version: z.literal(RUNTIME_PROTOCOL_VERSION),
    requestId: MutationIdentitySchema
  })
  .strict();

export const ReconnectHandshakeEventSchema = ClientEventBaseSchema.extend({
  type: z.literal('RECONNECT_HANDSHAKE'),
  lastSequenceId: z.number().int().min(0).optional()
}).strict();

export const ConfigUpdateEventSchema = ClientEventBaseSchema.extend({
  type: z.literal('CONFIG_UPDATE'),
  key: NonEmptyStringSchema,
  value: JsonValueSchema,
  idempotencyKey: MutationIdentitySchema,
  verification: VerificationChainMetadataSchema.optional(),
  guardrail: MutationGuardrailSchema.optional()
}).strict();

export const TaskTransitionEventSchema = ClientEventBaseSchema.extend({
  type: z.literal('TASK_TRANSITION'),
  taskId: NonEmptyStringSchema,
  status: TaskStatusSchema,
  idempotencyKey: MutationIdentitySchema,
  verification: VerificationChainMetadataSchema.optional(),
  guardrail: MutationGuardrailSchema.optional()
}).strict();

export const TaskAssignEventSchema = ClientEventBaseSchema.extend({
  type: z.literal('TASK_ASSIGN'),
  taskId: NonEmptyStringSchema,
  assigneeId: NonEmptyStringSchema,
  idempotencyKey: MutationIdentitySchema,
  guardrail: MutationGuardrailSchema.optional()
}).strict();

export const AgentStatusUpdateEventSchema = ClientEventBaseSchema.extend({
  type: z.literal('AGENT_STATUS_UPDATE'),
  agentId: NonEmptyStringSchema,
  status: AgentStatusSchema,
  idempotencyKey: MutationIdentitySchema,
  guardrail: MutationGuardrailSchema.optional()
}).strict();

export const ClientEventSchema = z.discriminatedUnion('type', [
  ReconnectHandshakeEventSchema,
  ConfigUpdateEventSchema,
  TaskTransitionEventSchema,
  TaskAssignEventSchema,
  AgentStatusUpdateEventSchema
]);

export const ServerEventBaseSchema = z
  .object({
    version: z.literal(RUNTIME_PROTOCOL_VERSION),
    sequenceId: z.number().int().min(0),
    timestamp: NonEmptyStringSchema
  })
  .strict();

export const StateSnapshotEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('STATE_SNAPSHOT'),
  snapshot: GameStateSnapshotSchema
}).strict();

export const AgentStateEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('AGENT_STATE'),
  agent: AgentPresenceSchema
}).strict();

export const TaskUpdateEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('TASK_UPDATE'),
  task: TaskSnapshotSchema,
  agent: AgentPresenceSchema.optional()
}).strict();

export const ToolCallEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('TOOL_CALL'),
  call: ToolCallPayloadSchema,
  agent: AgentPresenceSchema.optional()
}).strict();

export const WorkflowStepEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('WORKFLOW_STEP'),
  step: WorkflowStepPayloadSchema,
  agent: AgentPresenceSchema.optional()
}).strict();

export const VerificationGateEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('VERIFICATION_GATE'),
  result: VerificationGateResultSchema,
  actionId: NonEmptyStringSchema,
  verificationRef: NonEmptyStringSchema,
  evidenceRefs: z.array(VerificationEvidenceRefSchema).min(1),
  controlsExecuted: z.array(NonEmptyStringSchema).min(1),
  unmetControls: z.array(NonEmptyStringSchema).default([]),
  traceId: NonEmptyStringSchema.optional(),
  taskId: NonEmptyStringSchema.optional(),
  meta: z.record(JsonValueSchema).default({})
}).strict();

export const RuntimeErrorEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('ERROR'),
  code: NonEmptyStringSchema,
  message: NonEmptyStringSchema,
  correlationId: NonEmptyStringSchema.optional(),
  retryable: z.boolean()
}).strict();

export const HostBindingStateEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('HOST_BINDING_STATE'),
  binding: HostBindingSchema,
  manifest: CapabilityManifestSchema,
  reason: NonEmptyStringSchema.optional()
}).strict();

export const HostInvocationDecisionEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('HOST_INVOCATION_DECISION'),
  envelope: InvocationEnvelopeSchema,
  decision: HostInvocationDecisionSchema,
  reason: NonEmptyStringSchema,
  meta: EventMetaSchema
}).strict();

export const HostReviewArtifactEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('HOST_REVIEW_ARTIFACT'),
  review: ReviewArtifactSchema,
  meta: EventMetaSchema
}).strict();

export const HostContextLedgerUpdateEventSchema = ServerEventBaseSchema.extend({
  type: z.literal('HOST_CONTEXT_LEDGER_UPDATE'),
  entry: ContextLedgerEntrySchema,
  meta: EventMetaSchema
}).strict();

export const ServerEventSchema = z.discriminatedUnion('type', [
  StateSnapshotEventSchema,
  AgentStateEventSchema,
  TaskUpdateEventSchema,
  ToolCallEventSchema,
  WorkflowStepEventSchema,
  VerificationGateEventSchema,
  HostBindingStateEventSchema,
  HostInvocationDecisionEventSchema,
  HostReviewArtifactEventSchema,
  HostContextLedgerUpdateEventSchema,
  RuntimeErrorEventSchema
]);