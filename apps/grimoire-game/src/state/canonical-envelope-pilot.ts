import {
  createControlPlaneRunContext,
  createEventMeta,
  createCanonicalEnvelopePilot,
  type AgentPresence,
  type CanonicalEnvelopeChannel,
  type CanonicalEnvelopePilotInput,
  type CanonicalEnvelopePilot,
  type ControlPlaneRunContext,
  type ContextLedgerEntry,
  type EventMeta,
  type InvocationEnvelope,
  type JsonValue,
  type ReviewArtifact,
  type RuntimeErrorEvent,
  type ServerEvent,
  type TaskSnapshot,
  type VerificationEvidenceRef,
  type VerificationGateResult
} from '../contracts/events';

interface CanonicalEnvelopeAugmentation {
  sequenceId?: number;
  controlPlane?: ControlPlaneRunContext;
}

function buildCanonicalContext(
  context: CanonicalEnvelopePilotInput['context'],
  controlPlane?: ControlPlaneRunContext
): CanonicalEnvelopePilotInput['context'] {
  if (controlPlane === undefined) {
    return context;
  }

  const normalizedControlPlane = createControlPlaneRunContext(controlPlane);

  return {
    ...context,
    projectId: normalizedControlPlane.projectId,
    runId: normalizedControlPlane.runId,
    ...(normalizedControlPlane.nodeId === undefined ? {} : { nodeId: normalizedControlPlane.nodeId }),
    ...(normalizedControlPlane.workerId === undefined ? {} : { workerId: normalizedControlPlane.workerId }),
    ...(normalizedControlPlane.leaseId === undefined ? {} : { leaseId: normalizedControlPlane.leaseId }),
    ...(normalizedControlPlane.worktreeId === undefined ? {} : { worktreeId: normalizedControlPlane.worktreeId }),
    ...(normalizedControlPlane.branch === undefined ? {} : { branch: normalizedControlPlane.branch })
  };
}

export interface CanonicalTaskUpdateEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  task: TaskSnapshot;
  agent?: AgentPresence;
  traceId?: string;
  correlationId?: string;
}

export interface CanonicalWorkflowStepEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  step: {
    step: string;
    detail: string;
    sourceEventType: string;
    traceId?: string;
    taskId?: string;
    metadata: Record<string, JsonValue>;
  };
  correlationId?: string;
}

export interface CanonicalVerificationGateEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  gate: {
    result: VerificationGateResult;
    actionId: string;
    verificationRef: string;
    evidenceRefs: readonly VerificationEvidenceRef[];
    controlsExecuted: readonly string[];
    unmetControls?: readonly string[];
    traceId?: string;
    taskId?: string;
  };
  correlationId?: string;
}

export interface CanonicalRuntimeErrorEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  error: Pick<RuntimeErrorEvent, 'code' | 'message' | 'retryable' | 'correlationId'>;
  traceId?: string;
  taskId?: string;
  correlationId?: string;
}

export interface CanonicalSecurityFindingEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  finding: {
    findingId: string;
    title: string;
    severity: string;
    status: string;
    confidenceScore: number;
    exploitScenario: string;
    surfaceId: string;
    controls?: readonly string[];
    owaspCategory?: string;
    strideCategory?: string;
    agenticSkillCategory?: string;
    trustStatus?: string;
    requiredPolicy?: string;
    origin?: string;
    traceId?: string;
    taskId?: string;
  };
  correlationId?: string;
}

export interface CanonicalHostInvocationEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  envelope: InvocationEnvelope;
  decision: string;
  reason: string;
  meta?: EventMeta | undefined;
  correlationId?: string | undefined;
}

export interface CanonicalHostReviewEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  review: ReviewArtifact;
  meta?: EventMeta | undefined;
  correlationId?: string | undefined;
}

export interface CanonicalHostContextEnvelopeInput extends CanonicalEnvelopeAugmentation {
  messageId: string;
  emittedAt: string;
  channel: CanonicalEnvelopeChannel;
  entry: ContextLedgerEntry;
  meta?: EventMeta | undefined;
  correlationId?: string | undefined;
}

export function createCanonicalTaskUpdateEnvelope(input: CanonicalTaskUpdateEnvelopeInput): CanonicalEnvelopePilot {
  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'task.update',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        taskId: input.task.id,
        ...(input.traceId === undefined ? {} : { traceId: input.traceId }),
        ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId })
      },
      input.controlPlane
    ),
    body: toJsonValue({
      task: input.task,
      ...(input.agent === undefined ? {} : { agent: input.agent })
    })
  });
}

export function createCanonicalWorkflowStepEnvelope(input: CanonicalWorkflowStepEnvelopeInput): CanonicalEnvelopePilot {
  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'workflow.step',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(input.step.traceId === undefined ? {} : { traceId: input.step.traceId }),
        ...(input.step.taskId === undefined ? {} : { taskId: input.step.taskId }),
        ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId })
      },
      input.controlPlane
    ),
    body: toJsonValue({
      step: {
        step: input.step.step,
        detail: input.step.detail,
        sourceEventType: input.step.sourceEventType,
        ...(input.step.traceId === undefined ? {} : { traceId: input.step.traceId }),
        ...(input.step.taskId === undefined ? {} : { taskId: input.step.taskId }),
        metadata: input.step.metadata
      }
    })
  });
}

export function createCanonicalVerificationGateEnvelope(
  input: CanonicalVerificationGateEnvelopeInput
): CanonicalEnvelopePilot {
  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'verification.gate',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        verificationRef: input.gate.verificationRef,
        ...(input.gate.traceId === undefined ? {} : { traceId: input.gate.traceId }),
        ...(input.gate.taskId === undefined ? {} : { taskId: input.gate.taskId }),
        ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId })
      },
      input.controlPlane
    ),
    body: toJsonValue({
      result: input.gate.result,
      actionId: input.gate.actionId,
      verificationRef: input.gate.verificationRef,
      evidenceRefs: input.gate.evidenceRefs,
      controlsExecuted: [...input.gate.controlsExecuted],
      ...(input.gate.unmetControls === undefined ? {} : { unmetControls: [...input.gate.unmetControls] }),
      ...(input.gate.traceId === undefined ? {} : { traceId: input.gate.traceId }),
      ...(input.gate.taskId === undefined ? {} : { taskId: input.gate.taskId })
    })
  });
}

export function createCanonicalRuntimeErrorEnvelope(input: CanonicalRuntimeErrorEnvelopeInput): CanonicalEnvelopePilot {
  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'runtime.error',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(input.traceId === undefined ? {} : { traceId: input.traceId }),
        ...(input.taskId === undefined ? {} : { taskId: input.taskId }),
        ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId })
      },
      input.controlPlane
    ),
    body: toJsonValue({
      code: input.error.code,
      message: input.error.message,
      retryable: input.error.retryable,
      ...(input.error.correlationId === undefined ? {} : { correlationId: input.error.correlationId })
    })
  });
}

export function createCanonicalSecurityFindingEnvelope(
  input: CanonicalSecurityFindingEnvelopeInput
): CanonicalEnvelopePilot {
  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'security.finding',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(input.finding.traceId === undefined ? {} : { traceId: input.finding.traceId }),
        ...(input.finding.taskId === undefined ? {} : { taskId: input.finding.taskId }),
        ...(input.correlationId === undefined ? {} : { correlationId: input.correlationId })
      },
      input.controlPlane
    ),
    body: toJsonValue({
      findingId: input.finding.findingId,
      title: input.finding.title,
      severity: input.finding.severity,
      status: input.finding.status,
      confidenceScore: input.finding.confidenceScore,
      exploitScenario: input.finding.exploitScenario,
      surfaceId: input.finding.surfaceId,
      ...(input.finding.controls === undefined ? {} : { controls: [...input.finding.controls] }),
      ...(input.finding.owaspCategory === undefined ? {} : { owaspCategory: input.finding.owaspCategory }),
      ...(input.finding.strideCategory === undefined ? {} : { strideCategory: input.finding.strideCategory }),
      ...(input.finding.agenticSkillCategory === undefined
        ? {}
        : { agenticSkillCategory: input.finding.agenticSkillCategory }),
      ...(input.finding.trustStatus === undefined ? {} : { trustStatus: input.finding.trustStatus }),
      ...(input.finding.requiredPolicy === undefined ? {} : { requiredPolicy: input.finding.requiredPolicy }),
      ...(input.finding.origin === undefined ? {} : { origin: input.finding.origin }),
      ...(input.finding.traceId === undefined ? {} : { traceId: input.finding.traceId }),
      ...(input.finding.taskId === undefined ? {} : { taskId: input.finding.taskId })
    })
  });
}

export function createCanonicalHostInvocationEnvelope(
  input: CanonicalHostInvocationEnvelopeInput
): CanonicalEnvelopePilot {
  const meta = createEventMeta(input.meta ?? {});

  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'host.invocation',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(input.envelope.traceId === undefined ? {} : { traceId: input.envelope.traceId }),
        ...(input.envelope.taskId === undefined ? {} : { taskId: input.envelope.taskId }),
        correlationId: input.correlationId ?? meta.correlationId ?? input.envelope.correlationId
      },
      input.controlPlane
    ),
    body: toJsonValue({
      hostId: input.envelope.hostId,
      actionKind: input.envelope.actionKind,
      mode: input.envelope.mode,
      decision: input.decision,
      reason: input.reason,
      requestedScopes: [...input.envelope.requestedScopes],
      evidencePolicy: input.envelope.evidencePolicy,
      ...(meta.policyRef === undefined ? {} : { policyRef: meta.policyRef }),
      ...(meta.promptRef === undefined ? {} : { promptRef: meta.promptRef }),
      ...(meta.degradedFrom === undefined ? {} : { degradedFrom: meta.degradedFrom }),
      ...(meta.details === undefined ? {} : { meta: meta.details })
    })
  });
}

export function createCanonicalHostReviewEnvelope(input: CanonicalHostReviewEnvelopeInput): CanonicalEnvelopePilot {
  const meta = createEventMeta(input.meta ?? {});

  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'host.review',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(input.review.traceId === undefined ? {} : { traceId: input.review.traceId }),
        ...(input.review.taskId === undefined ? {} : { taskId: input.review.taskId }),
        correlationId: input.correlationId ?? meta.correlationId ?? `host.review:${input.review.reviewId}`
      },
      input.controlPlane
    ),
    body: toJsonValue({
      hostId: input.review.hostId,
      reviewId: input.review.reviewId,
      sourceType: input.review.sourceType,
      subjectRef: input.review.subjectRef,
      verdict: input.review.verdict,
      findings: input.review.findings,
      linkedEvidenceRefs: [...input.review.linkedEvidenceRefs],
      ...(meta.details === undefined ? {} : { meta: meta.details })
    })
  });
}

export function createCanonicalHostContextEnvelope(input: CanonicalHostContextEnvelopeInput): CanonicalEnvelopePilot {
  const meta = createEventMeta(input.meta ?? {});

  return createCanonicalEnvelopePilot({
    header: {
      messageType: 'host.context',
      messageId: input.messageId,
      emittedAt: input.emittedAt,
      channel: input.channel,
      ...(input.sequenceId === undefined ? {} : { sequenceId: input.sequenceId })
    },
    context: buildCanonicalContext(
      {
        ...(meta.traceId === undefined ? {} : { traceId: meta.traceId }),
        ...(meta.taskId === undefined ? {} : { taskId: meta.taskId }),
        correlationId: input.correlationId ?? meta.correlationId ?? `host.context:${input.entry.entryId}`
      },
      input.controlPlane
    ),
    body: toJsonValue({
      hostId: input.entry.hostId,
      entryId: input.entry.entryId,
      sourceType: input.entry.sourceType,
      visibility: input.entry.visibility,
      confidence: input.entry.confidence,
      ttlSeconds: input.entry.ttlSeconds,
      contentRef: input.entry.contentRef,
      trustStatus: input.entry.trustStatus,
      ...(input.entry.supersedes === undefined ? {} : { supersedes: input.entry.supersedes }),
      ...(meta.details === undefined ? {} : { meta: meta.details })
    })
  });
}

export function projectServerEventToCanonicalEnvelope(
  event: ServerEvent,
  channel: CanonicalEnvelopeChannel,
  controlPlane?: ControlPlaneRunContext
): CanonicalEnvelopePilot | null {
  const correlationId =
    event.type === 'VERIFICATION_GATE' && typeof event.meta.correlationId === 'string'
      ? event.meta.correlationId
      : `${event.type.toLowerCase()}:${event.sequenceId}`;

  if (event.type === 'TASK_UPDATE') {
    return createCanonicalTaskUpdateEnvelope({
      messageId: `task.update:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      task: event.task,
      ...(event.agent === undefined ? {} : { agent: event.agent }),
      ...(controlPlane === undefined ? {} : { controlPlane }),
      correlationId
    });
  }

  if (event.type === 'WORKFLOW_STEP') {
    return createCanonicalWorkflowStepEnvelope({
      messageId: `workflow.step:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      step: {
        step: event.step.step,
        detail: event.step.detail,
        sourceEventType: event.step.sourceEventType,
        ...(event.step.traceId === undefined ? {} : { traceId: event.step.traceId }),
        ...(event.step.taskId === undefined ? {} : { taskId: event.step.taskId }),
        metadata: event.step.metadata
      },
      ...(controlPlane === undefined ? {} : { controlPlane }),
      correlationId
    });
  }

  if (event.type === 'VERIFICATION_GATE') {
    return createCanonicalVerificationGateEnvelope({
      messageId: `verification.gate:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      gate: {
        result: event.result,
        actionId: event.actionId,
        verificationRef: event.verificationRef,
        evidenceRefs: event.evidenceRefs,
        controlsExecuted: event.controlsExecuted,
        unmetControls: event.unmetControls,
        ...(event.traceId === undefined ? {} : { traceId: event.traceId }),
        ...(event.taskId === undefined ? {} : { taskId: event.taskId })
      },
      ...(controlPlane === undefined ? {} : { controlPlane }),
      correlationId
    });
  }

  if (event.type === 'ERROR') {
    return createCanonicalRuntimeErrorEnvelope({
      messageId: `runtime.error:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      error: {
        code: event.code,
        message: event.message,
        retryable: event.retryable,
        ...(event.correlationId === undefined ? {} : { correlationId: event.correlationId })
      },
      ...(controlPlane === undefined ? {} : { controlPlane }),
      correlationId: event.correlationId ?? correlationId
    });
  }

  if (event.type === 'HOST_INVOCATION_DECISION') {
    return createCanonicalHostInvocationEnvelope({
      messageId: `host.invocation:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      envelope: event.envelope,
      decision: event.decision,
      reason: event.reason,
      meta: event.meta,
      ...(controlPlane === undefined ? {} : { controlPlane }),
      correlationId: event.meta.correlationId ?? event.envelope.correlationId
    });
  }

  if (event.type === 'HOST_REVIEW_ARTIFACT') {
    return createCanonicalHostReviewEnvelope({
      messageId: `host.review:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      review: event.review,
      meta: event.meta,
      ...(controlPlane === undefined ? {} : { controlPlane }),
      ...(event.meta.correlationId === undefined ? {} : { correlationId: event.meta.correlationId })
    });
  }

  if (event.type === 'HOST_CONTEXT_LEDGER_UPDATE') {
    return createCanonicalHostContextEnvelope({
      messageId: `host.context:${event.sequenceId}`,
      emittedAt: event.timestamp,
      channel,
      sequenceId: event.sequenceId,
      entry: event.entry,
      meta: event.meta,
      ...(controlPlane === undefined ? {} : { controlPlane }),
      ...(event.meta.correlationId === undefined ? {} : { correlationId: event.meta.correlationId })
    });
  }

  return null;
}

export function projectServerEventsToCanonicalEnvelopes(
  events: readonly ServerEvent[],
  channel: CanonicalEnvelopeChannel,
  controlPlane?: ControlPlaneRunContext
): CanonicalEnvelopePilot[] {
  return events
    .map((event) => projectServerEventToCanonicalEnvelope(event, channel, controlPlane))
    .filter((envelope): envelope is CanonicalEnvelopePilot => envelope !== null);
}

function toJsonValue(value: unknown): JsonValue {
  return JSON.parse(JSON.stringify(value)) as JsonValue;
}
