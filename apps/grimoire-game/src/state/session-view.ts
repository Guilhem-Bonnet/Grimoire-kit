import { createAuditView, type AuditEntry } from './audit-view';
import type { BoardDecisionCard } from './board-view';
import {
  HostActionKindSchema,
  HostActionModeSchema,
  HostContextSourceTypeSchema,
  HostContextTrustStatusSchema,
  HostContextVisibilitySchema,
  HostEvidencePolicySchema,
  HostReviewFindingSeveritySchema,
  HostReviewResolutionStatusSchema,
  HostReviewSourceTypeSchema,
  HostReviewVerdictSchema,
  HostScopeSchema,
  type CanonicalEnvelopePilot,
  type HostReviewFindingSeverity,
  type HostReviewResolutionStatus,
  type JsonValue,
  type VerificationEvidenceRef,
  type VerificationGateResult
} from '../contracts/events';
import type { GameState } from './game-state';
import {
  createCanonicalHostContextEnvelope,
  createCanonicalHostInvocationEnvelope,
  createCanonicalHostReviewEnvelope,
  createCanonicalRuntimeErrorEnvelope,
  createCanonicalSecurityFindingEnvelope,
  createCanonicalTaskUpdateEnvelope,
  createCanonicalVerificationGateEnvelope,
  createCanonicalWorkflowStepEnvelope
} from './canonical-envelope-pilot';

export const SESSION_STATUS_ORDER = ['attention', 'active', 'completed'] as const;

export type SessionStatus = (typeof SESSION_STATUS_ORDER)[number];

export interface SessionSummary {
  traceId: string;
  title: string;
  status: SessionStatus;
  startedAt: string;
  updatedAt: string;
  firstSequenceId: number;
  lastSequenceId: number;
  agentIds: readonly string[];
  roomIds: readonly string[];
  taskIds: readonly string[];
  entryCount: number;
  decisionCount: number;
  workflowStepCount: number;
  toolCallCount: number;
  errorCount: number;
  activeTaskCount: number;
  completedTaskCount: number;
  lastEventTitle: string;
  lastEventType: string;
}

export interface SessionViewSession {
  summary: SessionSummary;
  entries: readonly AuditEntry[];
  decisionCards: readonly BoardDecisionCard[];
  canonicalEnvelopes: readonly CanonicalEnvelopePilot[];
}

export interface SessionViewMetrics {
  sessionCount: number;
  activeCount: number;
  completedCount: number;
  attentionCount: number;
  unscopedEntryCount: number;
  canonicalEnvelopeCount: number;
}

export interface SessionView {
  protocolVersion: string;
  lastSequenceId: number;
  sessions: readonly SessionViewSession[];
  unscopedEntries: readonly AuditEntry[];
  metrics: SessionViewMetrics;
}

export interface SessionDiffSide {
  traceId: string;
  status: SessionStatus;
  title: string;
  entryCount: number;
  lastSequenceId: number;
}

export interface SessionDiff {
  left: SessionDiffSide;
  right: SessionDiffSide;
  newerTraceId: string;
  sequenceGap: number;
  sharedAgentIds: readonly string[];
  onlyLeftAgentIds: readonly string[];
  onlyRightAgentIds: readonly string[];
  sharedTaskIds: readonly string[];
  onlyLeftTaskIds: readonly string[];
  onlyRightTaskIds: readonly string[];
  sharedToolNames: readonly string[];
  onlyLeftToolNames: readonly string[];
  onlyRightToolNames: readonly string[];
  sharedDecisionTitles: readonly string[];
  onlyLeftDecisionTitles: readonly string[];
  onlyRightDecisionTitles: readonly string[];
}

const SESSION_STATUS_RANK: Record<SessionStatus, number> = {
  attention: 0,
  active: 1,
  completed: 2
};

export function createSessionView(state: GameState): SessionView {
  const auditView = createAuditView(state);
  const entriesByTraceId = new Map<string, AuditEntry[]>();
  const decisionCardsByTraceId = new Map<string, BoardDecisionCard[]>();
  const unscopedEntries: AuditEntry[] = [];

  for (const entry of auditView.entries) {
    if (entry.traceId === null) {
      unscopedEntries.push(entry);
      continue;
    }

    const currentEntries = entriesByTraceId.get(entry.traceId) ?? [];
    currentEntries.push(entry);
    entriesByTraceId.set(entry.traceId, currentEntries);
  }

  for (const decisionCard of auditView.decisionCards) {
    if (decisionCard.traceId === null) {
      continue;
    }

    const currentCards = decisionCardsByTraceId.get(decisionCard.traceId) ?? [];
    currentCards.push(decisionCard);
    decisionCardsByTraceId.set(decisionCard.traceId, currentCards);
  }

  const sessions = Array.from(entriesByTraceId.entries())
    .map(([traceId, traceEntries]) => createSessionRecord(state, traceId, traceEntries, decisionCardsByTraceId.get(traceId) ?? []))
    .sort(compareSessionRecords);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    sessions,
    unscopedEntries,
    metrics: {
      sessionCount: sessions.length,
      activeCount: sessions.filter((session) => session.summary.status === 'active').length,
      completedCount: sessions.filter((session) => session.summary.status === 'completed').length,
      attentionCount: sessions.filter((session) => session.summary.status === 'attention').length,
      unscopedEntryCount: unscopedEntries.length,
      canonicalEnvelopeCount: sessions.reduce((count, session) => count + session.canonicalEnvelopes.length, 0)
    }
  };
}

export function createSessionDiff(state: GameState, leftTraceId: string, rightTraceId: string): SessionDiff | null {
  const sessionView = createSessionView(state);
  const left = sessionView.sessions.find((session) => session.summary.traceId === leftTraceId);
  const right = sessionView.sessions.find((session) => session.summary.traceId === rightTraceId);

  if (left === undefined || right === undefined) {
    return null;
  }

  return {
    left: toSessionDiffSide(left),
    right: toSessionDiffSide(right),
    newerTraceId: left.summary.lastSequenceId >= right.summary.lastSequenceId ? left.summary.traceId : right.summary.traceId,
    sequenceGap: Math.abs(left.summary.lastSequenceId - right.summary.lastSequenceId),
    sharedAgentIds: intersect(left.summary.agentIds, right.summary.agentIds),
    onlyLeftAgentIds: subtract(left.summary.agentIds, right.summary.agentIds),
    onlyRightAgentIds: subtract(right.summary.agentIds, left.summary.agentIds),
    sharedTaskIds: intersect(left.summary.taskIds, right.summary.taskIds),
    onlyLeftTaskIds: subtract(left.summary.taskIds, right.summary.taskIds),
    onlyRightTaskIds: subtract(right.summary.taskIds, left.summary.taskIds),
    sharedToolNames: intersect(listToolNames(left.entries), listToolNames(right.entries)),
    onlyLeftToolNames: subtract(listToolNames(left.entries), listToolNames(right.entries)),
    onlyRightToolNames: subtract(listToolNames(right.entries), listToolNames(left.entries)),
    sharedDecisionTitles: intersect(listDecisionTitles(left.decisionCards), listDecisionTitles(right.decisionCards)),
    onlyLeftDecisionTitles: subtract(listDecisionTitles(left.decisionCards), listDecisionTitles(right.decisionCards)),
    onlyRightDecisionTitles: subtract(listDecisionTitles(right.decisionCards), listDecisionTitles(left.decisionCards))
  };
}

function createSessionRecord(
  state: GameState,
  traceId: string,
  entries: readonly AuditEntry[],
  decisionCards: readonly BoardDecisionCard[]
): SessionViewSession {
  const sortedEntries = [...entries].sort(compareSessionEntriesDescending);
  const latestEntry = sortedEntries[0] ?? null;
  const earliestEntry = sortedEntries[sortedEntries.length - 1] ?? null;
  const taskIds = uniqueStrings(sortedEntries.map((entry) => entry.taskId));
  const activeTaskCount = taskIds.reduce((count, taskId) => {
    const task = state.tasks[taskId];
    return task !== undefined && task.status !== 'done' ? count + 1 : count;
  }, 0);
  const completedTaskCount = taskIds.reduce((count, taskId) => {
    const task = state.tasks[taskId];
    return task !== undefined && task.status === 'done' ? count + 1 : count;
  }, 0);
  const summary: SessionSummary = {
    traceId,
    title: deriveSessionTitle(traceId, sortedEntries, decisionCards),
    status: deriveSessionStatus(sortedEntries, activeTaskCount),
    startedAt: earliestEntry?.timestamp ?? new Date(0).toISOString(),
    updatedAt: latestEntry?.timestamp ?? new Date(0).toISOString(),
    firstSequenceId: earliestEntry?.sequenceId ?? -1,
    lastSequenceId: latestEntry?.sequenceId ?? -1,
    agentIds: uniqueStrings(sortedEntries.map((entry) => entry.agentId)),
    roomIds: uniqueStrings(sortedEntries.map((entry) => entry.roomId)),
    taskIds,
    entryCount: sortedEntries.length,
    decisionCount: sortedEntries.filter((entry) => entry.kind === 'decision_card').length,
    workflowStepCount: sortedEntries.filter((entry) => entry.kind === 'workflow_step').length,
    toolCallCount: sortedEntries.filter((entry) => entry.kind === 'tool_call').length,
    errorCount: sortedEntries.filter((entry) => entry.level === 'error').length,
    activeTaskCount,
    completedTaskCount,
    lastEventTitle: latestEntry?.title ?? traceId,
    lastEventType: latestEntry?.sourceEventType ?? 'unknown'
  };

  const canonicalEnvelopes = createSessionCanonicalEnvelopes(state, summary, sortedEntries);

  return {
    summary,
    entries: sortedEntries,
    decisionCards: [...decisionCards].sort((left, right) => right.sequenceId - left.sequenceId),
    canonicalEnvelopes
  };
}

function createSessionCanonicalEnvelopes(
  state: GameState,
  summary: SessionSummary,
  entries: readonly AuditEntry[]
): CanonicalEnvelopePilot[] {
  const envelopes: CanonicalEnvelopePilot[] = [];

  summary.taskIds.forEach((taskId, index) => {
    const task = state.tasks[taskId];
    if (task === undefined) {
      return;
    }

    const assignee = task.assigneeId === undefined || task.assigneeId === null ? undefined : state.agents[task.assigneeId];

    envelopes.push(
      createCanonicalTaskUpdateEnvelope({
        messageId: `task.update:${summary.traceId}:${task.id}:${summary.lastSequenceId}:${index}`,
        emittedAt: summary.updatedAt,
        channel: 'session',
        task,
        ...(assignee === undefined ? {} : { agent: assignee }),
        traceId: summary.traceId,
        correlationId: `trace:${summary.traceId}`
      })
    );
  });

  for (const entry of [...entries].sort((left, right) => left.sequenceId - right.sequenceId)) {
    if (entry.kind === 'host_invocation') {
      const envelope = createHostInvocationEnvelopeFromAuditEntry(entry);
      if (envelope !== null) {
        envelopes.push(envelope);
      }
      continue;
    }

    if (entry.kind === 'host_review') {
      const envelope = createHostReviewEnvelopeFromAuditEntry(entry);
      if (envelope !== null) {
        envelopes.push(envelope);
      }
      continue;
    }

    if (entry.kind === 'host_context') {
      const envelope = createHostContextEnvelopeFromAuditEntry(entry);
      if (envelope !== null) {
        envelopes.push(envelope);
      }
      continue;
    }

    if (entry.kind === 'workflow_step') {
      const securityFindingEnvelope = createSecurityFindingEnvelopeFromAuditEntry(entry);
      if (securityFindingEnvelope !== null) {
        envelopes.push(securityFindingEnvelope);
        continue;
      }

      const verificationEnvelope = createVerificationGateEnvelopeFromAuditEntry(entry);

      if (verificationEnvelope !== null) {
        envelopes.push(verificationEnvelope);
      } else {
        envelopes.push(
          createCanonicalWorkflowStepEnvelope({
            messageId: `workflow.step:${entry.sequenceId}`,
            emittedAt: entry.timestamp,
            channel: 'session',
            step: {
              step: entry.title,
              detail: entry.detail,
              sourceEventType: entry.sourceEventType,
              ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
              ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
              metadata: entry.metadata
            },
            correlationId: `${entry.sourceEventType}:${entry.sequenceId}`
          })
        );
      }
      continue;
    }

    if (entry.kind === 'runtime_error') {
      envelopes.push(
        createCanonicalRuntimeErrorEnvelope({
          messageId: `runtime.error:${entry.sequenceId}`,
          emittedAt: entry.timestamp,
          channel: 'session',
          error: {
            code: entry.title.replace(/^Runtime error:\s*/u, ''),
            message: entry.detail,
            retryable: readJsonBoolean(entry.metadata.retryable) ?? false,
            correlationId: readJsonString(entry.metadata.correlation_id) ?? undefined
          },
          ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
          ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
          correlationId: readJsonString(entry.metadata.correlation_id) ?? `runtime.error:${entry.sequenceId}`
        })
      );
    }
  }

  return envelopes;
}

function createSecurityFindingEnvelopeFromAuditEntry(entry: AuditEntry): CanonicalEnvelopePilot | null {
  if (!isSecurityFindingEntry(entry)) {
    return null;
  }

  const findingId = readJsonString(entry.metadata.findingId) ?? `finding-${entry.sequenceId}`;
  const title = readJsonString(entry.metadata.title) ?? entry.title;
  const severity = readJsonString(entry.metadata.severity) ?? 'medium';
  const status = readJsonString(entry.metadata.status) ?? 'open';
  const confidenceScore =
    readJsonNumber(entry.metadata.confidenceScore) ??
    readJsonNumber(entry.metadata.confidence) ??
    readJsonNumber(entry.metadata.trustScore) ??
    0;
  const exploitScenario = readJsonString(entry.metadata.exploitScenario) ?? entry.detail;
  const surfaceId = readJsonString(entry.metadata.surfaceId) ?? 'unknown-surface';
  const owaspCategory = readJsonString(entry.metadata.owaspCategory);
  const strideCategory = readJsonString(entry.metadata.strideCategory);
  const agenticSkillCategory = readJsonString(entry.metadata.agenticSkillCategory);
  const trustStatus = readJsonString(entry.metadata.trustStatus);
  const requiredPolicy = readJsonString(entry.metadata.requiredPolicy);
  const origin = readJsonString(entry.metadata.origin);
  const controls = readJsonStringArray(entry.metadata.controls);

  return createCanonicalSecurityFindingEnvelope({
    messageId: `security.finding:${entry.sequenceId}`,
    emittedAt: entry.timestamp,
    channel: 'session',
    finding: {
      findingId,
      title,
      severity,
      status,
      confidenceScore,
      exploitScenario,
      surfaceId,
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
      ...(owaspCategory === null ? {} : { owaspCategory }),
      ...(strideCategory === null ? {} : { strideCategory }),
      ...(agenticSkillCategory === null ? {} : { agenticSkillCategory }),
      ...(trustStatus === null ? {} : { trustStatus }),
      ...(requiredPolicy === null ? {} : { requiredPolicy }),
      ...(origin === null ? {} : { origin }),
      ...(controls.length === 0 ? {} : { controls })
    },
    correlationId: `${entry.sourceEventType}:${entry.sequenceId}`
  });
}

function createVerificationGateEnvelopeFromAuditEntry(entry: AuditEntry): CanonicalEnvelopePilot | null {
  if (entry.sourceEventType !== 'verification_gate') {
    return null;
  }

  const actionId = readJsonString(entry.metadata.actionId);
  const verificationRef = readJsonString(entry.metadata.verificationRef);
  const controlsExecuted = readJsonStringArray(entry.metadata.controlsExecuted);
  const evidenceRefs = readVerificationEvidenceRefs(entry.metadata);
  const verdict = readVerificationGateResult(entry.metadata.verdict ?? entry.metadata.result);
  const correlationId = readJsonString(entry.metadata.correlationId) ?? `${entry.sourceEventType}:${entry.sequenceId}`;

  if (
    actionId === null ||
    verificationRef === null ||
    verdict === null ||
    controlsExecuted.length === 0 ||
    evidenceRefs.length === 0
  ) {
    return null;
  }

  return createCanonicalVerificationGateEnvelope({
    messageId: `verification.gate:${entry.sequenceId}`,
    emittedAt: entry.timestamp,
    channel: 'session',
    gate: {
      result: verdict,
      actionId,
      verificationRef,
      controlsExecuted,
      evidenceRefs,
      unmetControls: readJsonStringArray(entry.metadata.unmetControls),
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId })
    },
    correlationId
  });
}

function createHostInvocationEnvelopeFromAuditEntry(entry: AuditEntry): CanonicalEnvelopePilot | null {
  const hostId = readJsonString(entry.metadata.hostId);
  const actionKind = readJsonString(entry.metadata.actionKind);
  const mode = readJsonString(entry.metadata.mode);
  const evidencePolicy = readJsonString(entry.metadata.evidencePolicy);
  const decision = readJsonString(entry.metadata.decision);
  const correlationId = readJsonString(entry.metadata.correlationId) ?? `host.invocation:${entry.sequenceId}`;
  const parsedActionKind = actionKind === null ? null : HostActionKindSchema.safeParse(actionKind);
  const parsedMode = mode === null ? null : HostActionModeSchema.safeParse(mode);
  const parsedEvidencePolicy = evidencePolicy === null ? null : HostEvidencePolicySchema.safeParse(evidencePolicy);

  if (
    hostId === null ||
    parsedActionKind === null ||
    !parsedActionKind.success ||
    parsedMode === null ||
    !parsedMode.success ||
    parsedEvidencePolicy === null ||
    !parsedEvidencePolicy.success ||
    decision === null
  ) {
    return null;
  }

  return createCanonicalHostInvocationEnvelope({
    messageId: `host.invocation:${entry.sequenceId}`,
    emittedAt: entry.timestamp,
    channel: 'session',
    envelope: {
      envelopeId: `audit:${entry.sequenceId}`,
      hostId,
      actionKind: parsedActionKind.data,
      mode: parsedMode.data,
      correlationId,
      idempotencyKey: `audit:${entry.sequenceId}`,
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
      requestedScopes: readHostScopes(entry.metadata.requestedScopes),
      payload: {
        auditEntryId: entry.id,
        sourceEventType: entry.sourceEventType
      },
      evidencePolicy: parsedEvidencePolicy.data
    },
    decision,
    reason: entry.detail,
    meta: {
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
      correlationId,
      hostId,
      ...(readJsonString(entry.metadata.policyRef) === null
        ? {}
        : { policyRef: readJsonString(entry.metadata.policyRef) ?? undefined }),
      ...(readJsonString(entry.metadata.promptRef) === null
        ? {}
        : { promptRef: readJsonString(entry.metadata.promptRef) ?? undefined }),
      ...(readJsonString(entry.metadata.degradedFrom) === null
        ? {}
        : { degradedFrom: readJsonString(entry.metadata.degradedFrom) ?? undefined })
    },
    correlationId
  });
}

function createHostReviewEnvelopeFromAuditEntry(entry: AuditEntry): CanonicalEnvelopePilot | null {
  const hostId = readJsonString(entry.metadata.hostId);
  const reviewId = readJsonString(entry.metadata.reviewId);
  const sourceType = readJsonString(entry.metadata.sourceType);
  const subjectRef = readJsonString(entry.metadata.subjectRef);
  const verdict = readJsonString(entry.metadata.verdict);
  const findings = readHostReviewFindings(entry.metadata.findings);
  const correlationId = readJsonString(entry.metadata.correlationId) ?? `host.review:${entry.sequenceId}`;
  const parsedSourceType = sourceType === null ? null : HostReviewSourceTypeSchema.safeParse(sourceType);
  const parsedVerdict = verdict === null ? null : HostReviewVerdictSchema.safeParse(verdict);

  if (
    hostId === null ||
    reviewId === null ||
    parsedSourceType === null ||
    !parsedSourceType.success ||
    subjectRef === null ||
    parsedVerdict === null ||
    !parsedVerdict.success ||
    findings.length === 0
  ) {
    return null;
  }

  return createCanonicalHostReviewEnvelope({
    messageId: `host.review:${entry.sequenceId}`,
    emittedAt: entry.timestamp,
    channel: 'session',
    review: {
      hostId,
      reviewId,
      sourceType: parsedSourceType.data,
      subjectRef,
      verdict: parsedVerdict.data,
      findings,
      linkedEvidenceRefs: readJsonStringArray(entry.metadata.linkedEvidenceRefs),
      importedAt: entry.timestamp,
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId })
    },
    meta: {
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
      correlationId,
      hostId
    },
    correlationId
  });
}

function createHostContextEnvelopeFromAuditEntry(entry: AuditEntry): CanonicalEnvelopePilot | null {
  const hostId = readJsonString(entry.metadata.hostId);
  const entryId = readJsonString(entry.metadata.entryId);
  const sourceType = readJsonString(entry.metadata.sourceType);
  const visibility = readJsonString(entry.metadata.visibility);
  const confidence = readJsonNumber(entry.metadata.confidence);
  const ttlSeconds = readJsonNumber(entry.metadata.ttlSeconds);
  const contentRef = readJsonString(entry.metadata.contentRef);
  const trustStatus = readJsonString(entry.metadata.trustStatus);
  const correlationId = readJsonString(entry.metadata.correlationId) ?? `host.context:${entry.sequenceId}`;
  const parsedSourceType = sourceType === null ? null : HostContextSourceTypeSchema.safeParse(sourceType);
  const parsedVisibility = visibility === null ? null : HostContextVisibilitySchema.safeParse(visibility);
  const parsedTrustStatus = trustStatus === null ? null : HostContextTrustStatusSchema.safeParse(trustStatus);

  if (
    hostId === null ||
    entryId === null ||
    parsedSourceType === null ||
    !parsedSourceType.success ||
    parsedVisibility === null ||
    !parsedVisibility.success ||
    confidence === null ||
    ttlSeconds === null ||
    contentRef === null ||
    parsedTrustStatus === null ||
    !parsedTrustStatus.success
  ) {
    return null;
  }

  return createCanonicalHostContextEnvelope({
    messageId: `host.context:${entry.sequenceId}`,
    emittedAt: entry.timestamp,
    channel: 'session',
    entry: {
      hostId,
      entryId,
      sourceType: parsedSourceType.data,
      visibility: parsedVisibility.data,
      confidence,
      importedAt: entry.timestamp,
      ttlSeconds,
      contentRef,
      trustStatus: parsedTrustStatus.data,
      ...(readJsonString(entry.metadata.supersedes) === null
        ? {}
        : { supersedes: readJsonString(entry.metadata.supersedes) ?? undefined })
    },
    meta: {
      ...(entry.traceId === null ? {} : { traceId: entry.traceId }),
      ...(entry.taskId === null ? {} : { taskId: entry.taskId }),
      correlationId,
      hostId
    },
    correlationId
  });
}

function isSecurityFindingEntry(entry: AuditEntry): boolean {
  const sourceEventType = normalizeToken(entry.sourceEventType);
  if (sourceEventType === 'security_finding' || sourceEventType === 'security_audit_finding') {
    return true;
  }

  return normalizeToken(readJsonString(entry.metadata.topic)) === 'security_finding';
}

function deriveSessionTitle(
  traceId: string,
  entries: readonly AuditEntry[],
  decisionCards: readonly BoardDecisionCard[]
): string {
  const routingEntry = entries.find((entry) => entry.sourceEventType === 'routing');
  if (routingEntry !== undefined) {
    return routingEntry.detail.replace(/^Intent routed:\s*/u, '') || routingEntry.title;
  }

  const taskTitles = uniqueStrings(entries.map((entry) => entry.taskTitle));
  if (taskTitles.length === 1) {
    const taskTitle = taskTitles[0];
    if (taskTitle !== undefined) {
      return taskTitle;
    }
  }

  const firstDecisionCard = decisionCards[0];
  if (firstDecisionCard !== undefined) {
    return firstDecisionCard.title;
  }

  return traceId;
}

function deriveSessionStatus(entries: readonly AuditEntry[], activeTaskCount: number): SessionStatus {
  if (entries.some((entry) => entry.level === 'error')) {
    return 'attention';
  }

  if (activeTaskCount > 0) {
    return 'active';
  }

  if (entries.some((entry) => entry.sourceEventType === 'aggregation')) {
    return 'completed';
  }

  return entries.some(
    (entry) =>
      entry.kind === 'tool_call' ||
      entry.kind === 'workflow_step' ||
      entry.kind === 'host_invocation' ||
      entry.kind === 'host_review' ||
      entry.kind === 'host_context'
  )
    ? 'completed'
    : 'active';
}

function compareSessionRecords(left: SessionViewSession, right: SessionViewSession): number {
  if (left.summary.status !== right.summary.status) {
    return SESSION_STATUS_RANK[left.summary.status] - SESSION_STATUS_RANK[right.summary.status];
  }

  if (left.summary.lastSequenceId !== right.summary.lastSequenceId) {
    return right.summary.lastSequenceId - left.summary.lastSequenceId;
  }

  return left.summary.traceId.localeCompare(right.summary.traceId);
}

function compareSessionEntriesDescending(left: AuditEntry, right: AuditEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return left.kind.localeCompare(right.kind);
}

function toSessionDiffSide(session: SessionViewSession): SessionDiffSide {
  return {
    traceId: session.summary.traceId,
    status: session.summary.status,
    title: session.summary.title,
    entryCount: session.summary.entryCount,
    lastSequenceId: session.summary.lastSequenceId
  };
}

function listToolNames(entries: readonly AuditEntry[]): string[] {
  return uniqueStrings(
    entries
      .filter((entry) => entry.kind === 'tool_call')
      .map((entry) => entry.title.replace(/^Tool call:\s*/u, ''))
  );
}

function listDecisionTitles(cards: readonly BoardDecisionCard[]): string[] {
  return uniqueStrings(cards.map((card) => card.title));
}

function uniqueStrings(values: readonly (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.length > 0))].sort((left, right) =>
    left.localeCompare(right)
  );
}

function readJsonString(value: JsonValue | undefined): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return normalized.length > 0 ? normalized : null;
}

function readJsonBoolean(value: JsonValue | undefined): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function readJsonNumber(value: JsonValue | undefined): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value !== 'string') {
    return null;
  }

  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readJsonStringArray(value: JsonValue | undefined): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function readVerificationEvidenceRefs(metadata: Record<string, JsonValue>): VerificationEvidenceRef[] {
  const typedEvidenceRefs = metadata.typedEvidenceRefs;

  if (Array.isArray(typedEvidenceRefs)) {
    const parsedEvidenceRefs = typedEvidenceRefs
      .map((item) => {
        if (typeof item !== 'object' || item === null || Array.isArray(item)) {
          return null;
        }

        const kind = readJsonString(item.kind);
        const ref = readJsonString(item.ref);

        if (
          ref === null ||
          (kind !== 'artifact' && kind !== 'coverage' && kind !== 'log' && kind !== 'screenshot' && kind !== 'test')
        ) {
          return null;
        }

        return {
          kind,
          ref
        } as VerificationEvidenceRef;
      })
      .filter((item): item is VerificationEvidenceRef => item !== null);

    if (parsedEvidenceRefs.length > 0) {
      return parsedEvidenceRefs;
    }
  }

  return readJsonStringArray(metadata.evidenceRefs).map<VerificationEvidenceRef>((ref) => ({
    kind: inferVerificationEvidenceKind(ref),
    ref
  }));
}

function inferVerificationEvidenceKind(ref: string): VerificationEvidenceRef['kind'] {
  const normalized = ref.trim().toLowerCase();

  if (normalized.startsWith('tests://')) {
    return 'test';
  }

  if (normalized.startsWith('log://')) {
    return 'log';
  }

  if (normalized.startsWith('coverage://')) {
    return 'coverage';
  }

  if (normalized.startsWith('screenshot://')) {
    return 'screenshot';
  }

  return 'artifact';
}

function readHostScopes(value: JsonValue | undefined): Array<(typeof HostScopeSchema)['_type']> {
  return readJsonStringArray(value)
    .map((item) => HostScopeSchema.safeParse(item))
    .filter((result): result is { success: true; data: (typeof HostScopeSchema)['_type'] } => result.success)
    .map((result) => result.data);
}

function readHostReviewFindings(value: JsonValue | undefined): Array<{
  id: string;
  severity: HostReviewFindingSeverity;
  message: string;
  resolutionStatus: HostReviewResolutionStatus;
}> {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .filter(
      (
        item
      ): item is {
        id?: JsonValue;
        severity?: JsonValue;
        message?: JsonValue;
        resolutionStatus?: JsonValue;
      } => typeof item === 'object' && item !== null && !Array.isArray(item)
    )
    .map((item) => {
      const id = readJsonString(item.id);
      const severity = readJsonString(item.severity);
      const message = readJsonString(item.message);
      const resolutionStatus = readJsonString(item.resolutionStatus);
      const parsedSeverity = severity === null ? null : HostReviewFindingSeveritySchema.safeParse(severity);
      const parsedResolutionStatus =
        resolutionStatus === null ? null : HostReviewResolutionStatusSchema.safeParse(resolutionStatus);

      if (
        id === null ||
        parsedSeverity === null ||
        !parsedSeverity.success ||
        message === null ||
        parsedResolutionStatus === null ||
        !parsedResolutionStatus.success
      ) {
        return null;
      }

      return {
        id,
        severity: parsedSeverity.data,
        message,
        resolutionStatus: parsedResolutionStatus.data
      };
    })
    .filter(
      (item): item is {
        id: string;
        severity: HostReviewFindingSeverity;
        message: string;
        resolutionStatus: HostReviewResolutionStatus;
      } => item !== null
    );
}

function readVerificationGateResult(value: JsonValue | undefined): VerificationGateResult | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toUpperCase();
  if (normalized === 'PASS' || normalized === 'FAIL') {
    return normalized;
  }

  return null;
}

function normalizeToken(value: string | null): string | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized.length === 0) {
    return null;
  }

  return normalized.replace(/[\s-]+/g, '_');
}

function intersect(left: readonly string[], right: readonly string[]): string[] {
  const rightSet = new Set(right);
  return [...left.filter((value) => rightSet.has(value))].sort((first, second) => first.localeCompare(second));
}

function subtract(left: readonly string[], right: readonly string[]): string[] {
  const rightSet = new Set(right);
  return [...left.filter((value) => !rightSet.has(value))].sort((first, second) => first.localeCompare(second));
}