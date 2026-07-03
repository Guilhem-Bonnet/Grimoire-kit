import type { JsonValue, RuntimeErrorEvent } from '../contracts/events';

import { createBoardView, type BoardDecisionCard } from './board-view';
import type {
  GameState,
  HostBindingRecord,
  HostContextLedgerRecord,
  HostInvocationDecisionRecord,
  HostReviewArtifactRecord,
  ToolCallLogEntry,
  WorkflowStepLogEntry
} from './game-state';

export const AUDIT_ENTRY_KIND_ORDER = [
  'runtime_error',
  'host_invocation',
  'host_review',
  'decision_card',
  'task_handoff',
  'workflow_step',
  'tool_call',
  'host_context',
  'host_binding'
] as const;
export const AUDIT_ENTRY_LEVEL_ORDER = ['error', 'warning', 'info'] as const;

export type AuditEntryKind = (typeof AUDIT_ENTRY_KIND_ORDER)[number];
export type AuditEntryLevel = (typeof AUDIT_ENTRY_LEVEL_ORDER)[number];

export interface AuditFilter {
  agentId?: string;
  taskId?: string;
  roomId?: string;
  traceId?: string;
  kinds?: readonly AuditEntryKind[];
  levels?: readonly AuditEntryLevel[];
  query?: string;
}

export interface AuditEntry {
  id: string;
  kind: AuditEntryKind;
  level: AuditEntryLevel;
  sequenceId: number;
  timestamp: string;
  title: string;
  detail: string;
  sourceEventType: string;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
  taskId: string | null;
  taskTitle: string | null;
  traceId: string | null;
  metadata: Record<string, JsonValue>;
}

export interface AuditFacetCount<T extends string = string> {
  value: T;
  count: number;
  label?: string;
}

export interface AuditFacets {
  agents: readonly AuditFacetCount[];
  rooms: readonly AuditFacetCount[];
  tasks: readonly AuditFacetCount[];
  traces: readonly AuditFacetCount[];
  kinds: readonly AuditFacetCount<AuditEntryKind>[];
  levels: readonly AuditFacetCount<AuditEntryLevel>[];
}

export interface AuditMetrics {
  totalCount: number;
  filteredCount: number;
  runtimeErrorCount: number;
  decisionCardCount: number;
  workflowStepCount: number;
  toolCallCount: number;
  errorCount: number;
  warningCount: number;
  infoCount: number;
}

export interface AuditView {
  protocolVersion: string;
  lastSequenceId: number;
  hasActiveFilters: boolean;
  filter: AuditFilter;
  entries: readonly AuditEntry[];
  decisionCards: readonly BoardDecisionCard[];
  metrics: AuditMetrics;
  facets: AuditFacets;
}

const AUDIT_ENTRY_KIND_RANK: Record<AuditEntryKind, number> = {
  runtime_error: 0,
  host_invocation: 1,
  host_review: 2,
  decision_card: 3,
  task_handoff: 4,
  workflow_step: 5,
  tool_call: 6,
  host_context: 7,
  host_binding: 8
};

const AUDIT_ENTRY_LEVEL_RANK: Record<AuditEntryLevel, number> = {
  error: 0,
  warning: 1,
  info: 2
};

interface DecisionCardRecord {
  card: BoardDecisionCard;
  entry: AuditEntry;
}

export function createAuditView(state: GameState, filter: AuditFilter = {}): AuditView {
  const normalizedFilter = normalizeAuditFilter(filter);
  const decisionCardRecords = createDecisionCardRecords(state);
  const allEntries = [
    ...Object.values(state.hostBindings ?? {}).map((binding) => createHostBindingEntry(binding)),
    ...(state.recentHostInvocationDecisions ?? []).map((decision) => createHostInvocationEntry(state, decision)),
    ...(state.recentHostReviews ?? []).map((review) => createHostReviewEntry(state, review)),
    ...(state.recentHostContextEntries ?? []).map((entry) => createHostContextEntry(state, entry)),
    ...decisionCardRecords.map((record) => record.entry),
    ...createTaskHandoffEntries(state),
    ...state.recentWorkflowSteps.map((workflowStep) => createWorkflowStepEntry(state, workflowStep)),
    ...state.recentToolCalls.map((toolCall) => createToolCallEntry(state, toolCall)),
    ...state.lastErrors.map(createRuntimeErrorEntry)
  ].sort(compareAuditEntries);
  const entries = allEntries.filter((entry) => matchesAuditFilter(entry, normalizedFilter));
  const decisionCards = decisionCardRecords
    .filter((record) => matchesAuditFilter(record.entry, normalizedFilter))
    .map((record) => record.card);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    hasActiveFilters: isAuditFilterActive(normalizedFilter),
    filter: normalizedFilter,
    entries,
    decisionCards,
    metrics: createAuditMetrics(allEntries, entries),
    facets: createAuditFacets(entries)
  };
}

function createTaskHandoffEntries(state: GameState): AuditEntry[] {
  const workflowStepsByConversation = new Map<string, WorkflowStepLogEntry[]>();

  for (const workflowStep of state.recentWorkflowSteps) {
    if (workflowStep.agentId === undefined) {
      continue;
    }

    const conversationKey = createTaskHandoffKey(workflowStep);
    if (conversationKey === null) {
      continue;
    }

    const conversationSteps = workflowStepsByConversation.get(conversationKey) ?? [];
    conversationSteps.push(workflowStep);
    workflowStepsByConversation.set(conversationKey, conversationSteps);
  }

  const handoffEntries: AuditEntry[] = [];

  for (const workflowSteps of workflowStepsByConversation.values()) {
    const sortedWorkflowSteps = [...workflowSteps].sort((left, right) => left.sequenceId - right.sequenceId);
    let previousWorkflowStep: WorkflowStepLogEntry | null = null;

    for (const workflowStep of sortedWorkflowSteps) {
      if (workflowStep.agentId === undefined) {
        continue;
      }

      if (
        previousWorkflowStep !== null &&
        previousWorkflowStep.agentId !== undefined &&
        previousWorkflowStep.agentId !== workflowStep.agentId
      ) {
        const handoffEntry = createTaskHandoffEntry(state, previousWorkflowStep, workflowStep);
        if (handoffEntry !== null) {
          handoffEntries.push(handoffEntry);
        }
      }

      previousWorkflowStep = workflowStep;
    }
  }

  return handoffEntries;
}

function createTaskHandoffKey(workflowStep: WorkflowStepLogEntry): string | null {
  const taskScope = workflowStep.taskId ?? '__taskless__';
  const traceScope = workflowStep.traceId ?? '__traceless__';

  if (taskScope === '__taskless__' && traceScope === '__traceless__') {
    return null;
  }

  return `${taskScope}::${traceScope}`;
}

function createTaskHandoffEntry(
  state: GameState,
  previousWorkflowStep: WorkflowStepLogEntry,
  workflowStep: WorkflowStepLogEntry
): AuditEntry | null {
  if (previousWorkflowStep.agentId === undefined || workflowStep.agentId === undefined) {
    return null;
  }

  const fromAgent = state.agents[previousWorkflowStep.agentId];
  const toAgent = state.agents[workflowStep.agentId];
  if (fromAgent === undefined || toAgent === undefined) {
    return null;
  }

  const taskId = workflowStep.taskId ?? previousWorkflowStep.taskId ?? null;
  const task = taskId === null ? undefined : state.tasks[taskId];
  const traceId = workflowStep.traceId ?? previousWorkflowStep.traceId ?? null;
  const scope = fromAgent.roomId === toAgent.roomId ? 'intra_room' : 'inter_room';

  return {
    id: `task-handoff-${workflowStep.sequenceId}`,
    kind: 'task_handoff',
    level: 'info',
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    title: `Task handoff: ${task?.title ?? taskId ?? workflowStep.step}`,
    detail: `${fromAgent.name} -> ${toAgent.name}: ${workflowStep.step}`,
    sourceEventType: 'task_handoff',
    agentId: fromAgent.id,
    agentName: fromAgent.name,
    roomId: fromAgent.roomId,
    taskId,
    taskTitle: task?.title ?? null,
    traceId,
    metadata: {
      fromAgentId: fromAgent.id,
      fromAgentName: fromAgent.name,
      fromRoomId: fromAgent.roomId,
      toAgentId: toAgent.id,
      toAgentName: toAgent.name,
      toRoomId: toAgent.roomId,
      scope,
      previousSequenceId: previousWorkflowStep.sequenceId,
      currentSequenceId: workflowStep.sequenceId,
      handoffStep: workflowStep.step,
      handoffDetail: workflowStep.detail,
      ...(traceId === null ? {} : { correlationId: `trace:${traceId}` })
    }
  };
}

function createDecisionCardRecords(state: GameState): DecisionCardRecord[] {
  const boardView = createBoardView(state);

  return boardView.decisionCards.map((card) => ({
    card,
    entry: createDecisionCardEntry(state, card)
  }));
}

function createDecisionCardEntry(state: GameState, card: BoardDecisionCard): AuditEntry {
  const agent = card.agentId === null ? undefined : state.agents[card.agentId];

  return {
    id: card.id,
    kind: 'decision_card',
    level: 'info',
    sequenceId: card.sequenceId,
    timestamp: card.timestamp,
    title: `Decision card: ${card.title}`,
    detail: card.detail,
    sourceEventType: card.sourceEventType,
    agentId: card.agentId,
    agentName: agent?.name ?? null,
    roomId: card.roomId,
    taskId: card.taskId,
    taskTitle: card.taskTitle,
    traceId: card.traceId,
    metadata: {
      ...(card.actionId === null ? {} : { action_id: card.actionId }),
      structured: card.isStructured,
      missing_fields: [...card.missingFields],
      evidence_refs: [...card.evidenceRefs],
      evidence_count: card.evidence.length,
      supporting_tools: card.supportingToolCalls.map((toolCall) => toolCall.tool)
    }
  };
}

function createWorkflowStepEntry(state: GameState, workflowStep: WorkflowStepLogEntry): AuditEntry {
  const agent = workflowStep.agentId === undefined ? undefined : state.agents[workflowStep.agentId];
  const task = workflowStep.taskId === undefined ? undefined : state.tasks[workflowStep.taskId];

  return {
    id: `workflow-step-${workflowStep.sequenceId}`,
    kind: 'workflow_step',
    level: 'info',
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    title: workflowStep.step,
    detail: workflowStep.detail,
    sourceEventType: workflowStep.sourceEventType,
    agentId: workflowStep.agentId ?? null,
    agentName: agent?.name ?? null,
    roomId: agent?.roomId ?? null,
    taskId: workflowStep.taskId ?? null,
    taskTitle: task?.title ?? null,
    traceId: workflowStep.traceId ?? null,
    metadata: cloneJsonRecord(workflowStep.metadata)
  };
}

function createToolCallEntry(state: GameState, toolCall: ToolCallLogEntry): AuditEntry {
  const agent = toolCall.agentId === undefined ? undefined : state.agents[toolCall.agentId];
  const taskId = readStringValue(toolCall.params.task_id) ?? null;
  const task = taskId === null ? undefined : state.tasks[taskId];

  return {
    id: `tool-call-${toolCall.sequenceId}`,
    kind: 'tool_call',
    level: 'info',
    sequenceId: toolCall.sequenceId,
    timestamp: toolCall.timestamp,
    title: `Tool call: ${toolCall.tool}`,
    detail: formatToolCallDetail(toolCall),
    sourceEventType: toolCall.sourceEventType,
    agentId: toolCall.agentId ?? null,
    agentName: agent?.name ?? null,
    roomId: agent?.roomId ?? null,
    taskId,
    taskTitle: task?.title ?? null,
    traceId: toolCall.traceId ?? null,
    metadata: cloneJsonRecord(toolCall.params)
  };
}

function createRuntimeErrorEntry(error: RuntimeErrorEvent): AuditEntry {
  return {
    id: `runtime-error-${error.sequenceId}`,
    kind: 'runtime_error',
    level: 'error',
    sequenceId: error.sequenceId,
    timestamp: error.timestamp,
    title: `Runtime error: ${error.code}`,
    detail: error.message,
    sourceEventType: error.type,
    agentId: null,
    agentName: null,
    roomId: null,
    taskId: null,
    taskTitle: null,
    traceId: null,
    metadata: {
      retryable: error.retryable,
      ...(error.correlationId === undefined ? {} : { correlation_id: error.correlationId })
    }
  };
}

function createHostBindingEntry(record: HostBindingRecord): AuditEntry {
  return {
    id: `host-binding-${record.binding.hostId}-${record.sequenceId}`,
    kind: 'host_binding',
    level: resolveHostBindingLevel(record),
    sequenceId: record.sequenceId,
    timestamp: record.timestamp,
    title: `Host binding: ${record.binding.displayName}`,
    detail:
      record.reason ??
      `${record.binding.connectionState} / ${record.binding.trustStatus} via ${record.binding.hostType}`,
    sourceEventType: 'HOST_BINDING_STATE',
    agentId: null,
    agentName: null,
    roomId: null,
    taskId: null,
    taskTitle: null,
    traceId: null,
    metadata: {
      hostId: record.binding.hostId,
      hostType: record.binding.hostType,
      authMode: record.binding.authMode,
      connectionState: record.binding.connectionState,
      trustStatus: record.binding.trustStatus,
      capabilityManifestRef: record.binding.capabilityManifestRef,
      manifestId: record.manifest.manifestId,
      permissionMode: record.manifest.permissionMode,
      supportsStreaming: record.manifest.supportsStreaming,
      supportsReviewImport: record.manifest.supportsReviewImport,
      supportsContextImport: record.manifest.supportsContextImport,
      supportsPreviewCommit: record.manifest.supportsPreviewCommit,
      scopes: [...record.binding.scopes],
      routines: [...record.manifest.routines],
      toolProviders: [...record.manifest.toolProviders],
      reviewChannels: [...record.manifest.reviewChannels],
      contextSources: [...record.manifest.contextSources],
      ...(record.binding.lastSeenAt === undefined ? {} : { lastSeenAt: record.binding.lastSeenAt }),
      ...(record.reason === undefined ? {} : { reason: record.reason })
    }
  };
}

function createHostInvocationEntry(state: GameState, record: HostInvocationDecisionRecord): AuditEntry {
  const hostLabel = resolveHostDisplayName(state, record.envelope.hostId);
  const task = record.envelope.taskId === undefined ? undefined : state.tasks[record.envelope.taskId];

  return {
    id: `host-invocation-${record.sequenceId}`,
    kind: 'host_invocation',
    level: resolveHostInvocationLevel(record.decision),
    sequenceId: record.sequenceId,
    timestamp: record.timestamp,
    title: `Host decision: ${record.decision} ${hostLabel}`,
    detail: record.reason,
    sourceEventType: 'HOST_INVOCATION_DECISION',
    agentId: null,
    agentName: null,
    roomId: null,
    taskId: record.envelope.taskId ?? record.meta.taskId ?? null,
    taskTitle: task?.title ?? null,
    traceId: record.envelope.traceId ?? record.meta.traceId ?? null,
    metadata: {
      hostId: record.envelope.hostId,
      actionKind: record.envelope.actionKind,
      mode: record.envelope.mode,
      evidencePolicy: record.envelope.evidencePolicy,
      requestedScopes: [...record.envelope.requestedScopes],
      correlationId: record.envelope.correlationId,
      decision: record.decision,
      ...(record.meta.policyRef === undefined ? {} : { policyRef: record.meta.policyRef }),
      ...(record.meta.promptRef === undefined ? {} : { promptRef: record.meta.promptRef }),
      ...(record.meta.degradedFrom === undefined ? {} : { degradedFrom: record.meta.degradedFrom }),
      ...cloneJsonRecord(record.meta.details ?? {})
    }
  };
}

function createHostReviewEntry(state: GameState, record: HostReviewArtifactRecord): AuditEntry {
  const hostLabel = resolveHostDisplayName(state, record.review.hostId);
  const task = record.review.taskId === undefined ? undefined : state.tasks[record.review.taskId];

  return {
    id: `host-review-${record.review.reviewId}`,
    kind: 'host_review',
    level: resolveHostReviewLevel(record),
    sequenceId: record.sequenceId,
    timestamp: record.timestamp,
    title: `Host review: ${hostLabel}`,
    detail: `${record.review.subjectRef} -> ${record.review.verdict}`,
    sourceEventType: 'HOST_REVIEW_ARTIFACT',
    agentId: null,
    agentName: null,
    roomId: null,
    taskId: record.review.taskId ?? record.meta.taskId ?? null,
    taskTitle: task?.title ?? null,
    traceId: record.review.traceId ?? record.meta.traceId ?? null,
    metadata: {
      hostId: record.review.hostId,
      reviewId: record.review.reviewId,
      sourceType: record.review.sourceType,
      subjectRef: record.review.subjectRef,
      verdict: record.review.verdict,
      findingCount: record.review.findings.length,
      findings: record.review.findings,
      severities: record.review.findings.map((finding) => finding.severity),
      linkedEvidenceRefs: [...record.review.linkedEvidenceRefs],
      ...(record.meta.correlationId === undefined ? {} : { correlationId: record.meta.correlationId }),
      ...cloneJsonRecord(record.meta.details ?? {})
    }
  };
}

function createHostContextEntry(state: GameState, record: HostContextLedgerRecord): AuditEntry {
  const hostLabel = resolveHostDisplayName(state, record.entry.hostId);
  const task = record.meta.taskId === undefined ? undefined : state.tasks[record.meta.taskId];

  return {
    id: `host-context-${record.entry.entryId}`,
    kind: 'host_context',
    level: record.entry.trustStatus === 'restricted' ? 'warning' : 'info',
    sequenceId: record.sequenceId,
    timestamp: record.timestamp,
    title: `Host context: ${hostLabel}`,
    detail: `${record.entry.sourceType} -> ${record.entry.contentRef}`,
    sourceEventType: 'HOST_CONTEXT_LEDGER_UPDATE',
    agentId: null,
    agentName: null,
    roomId: null,
    taskId: record.meta.taskId ?? null,
    taskTitle: task?.title ?? null,
    traceId: record.meta.traceId ?? null,
    metadata: {
      hostId: record.entry.hostId,
      entryId: record.entry.entryId,
      sourceType: record.entry.sourceType,
      visibility: record.entry.visibility,
      confidence: record.entry.confidence,
      ttlSeconds: record.entry.ttlSeconds,
      contentRef: record.entry.contentRef,
      trustStatus: record.entry.trustStatus,
      ...(record.entry.supersedes === undefined ? {} : { supersedes: record.entry.supersedes }),
      ...(record.meta.correlationId === undefined ? {} : { correlationId: record.meta.correlationId }),
      ...cloneJsonRecord(record.meta.details ?? {})
    }
  };
}

function normalizeAuditFilter(filter: AuditFilter): AuditFilter {
  return {
    ...(filter.agentId === undefined ? {} : { agentId: filter.agentId }),
    ...(filter.taskId === undefined ? {} : { taskId: filter.taskId }),
    ...(filter.roomId === undefined ? {} : { roomId: filter.roomId }),
    ...(filter.traceId === undefined ? {} : { traceId: filter.traceId }),
    ...(filter.kinds === undefined || filter.kinds.length === 0 ? {} : { kinds: [...new Set(filter.kinds)] }),
    ...(filter.levels === undefined || filter.levels.length === 0 ? {} : { levels: [...new Set(filter.levels)] }),
    ...(filter.query === undefined || filter.query.trim().length === 0 ? {} : { query: filter.query.trim() })
  };
}

function isAuditFilterActive(filter: AuditFilter): boolean {
  return Object.keys(filter).length > 0;
}

function matchesAuditFilter(entry: AuditEntry, filter: AuditFilter): boolean {
  if (filter.agentId !== undefined && entry.agentId !== filter.agentId) {
    return false;
  }

  if (filter.taskId !== undefined && entry.taskId !== filter.taskId) {
    return false;
  }

  if (filter.roomId !== undefined && entry.roomId !== filter.roomId) {
    return false;
  }

  if (filter.traceId !== undefined && entry.traceId !== filter.traceId) {
    return false;
  }

  if (filter.kinds !== undefined && !filter.kinds.includes(entry.kind)) {
    return false;
  }

  if (filter.levels !== undefined && !filter.levels.includes(entry.level)) {
    return false;
  }

  if (filter.query !== undefined && !buildAuditSearchText(entry).includes(filter.query.toLowerCase())) {
    return false;
  }

  return true;
}

function buildAuditSearchText(entry: AuditEntry): string {
  return [
    entry.id,
    entry.kind,
    entry.level,
    entry.title,
    entry.detail,
    entry.sourceEventType,
    entry.agentId,
    entry.agentName,
    entry.roomId,
    entry.taskId,
    entry.taskTitle,
    entry.traceId,
    JSON.stringify(entry.metadata)
  ]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .join(' ')
    .toLowerCase();
}

function createAuditMetrics(allEntries: readonly AuditEntry[], entries: readonly AuditEntry[]): AuditMetrics {
  return {
    totalCount: allEntries.length,
    filteredCount: entries.length,
    runtimeErrorCount: countEntriesByKind(entries, 'runtime_error'),
    decisionCardCount: countEntriesByKind(entries, 'decision_card'),
    workflowStepCount: countEntriesByKind(entries, 'workflow_step'),
    toolCallCount: countEntriesByKind(entries, 'tool_call'),
    errorCount: countEntriesByLevel(entries, 'error'),
    warningCount: countEntriesByLevel(entries, 'warning'),
    infoCount: countEntriesByLevel(entries, 'info')
  };
}

function createAuditFacets(entries: readonly AuditEntry[]): AuditFacets {
  return {
    agents: createFacetCounts(entries, (entry) => entry.agentId, (entry) => entry.agentName ?? undefined),
    rooms: createFacetCounts(entries, (entry) => entry.roomId),
    tasks: createFacetCounts(entries, (entry) => entry.taskId, (entry) => entry.taskTitle ?? undefined),
    traces: createFacetCounts(entries, (entry) => entry.traceId),
    kinds: createFixedFacetCounts(entries, AUDIT_ENTRY_KIND_ORDER, (entry) => entry.kind),
    levels: createFixedFacetCounts(entries, AUDIT_ENTRY_LEVEL_ORDER, (entry) => entry.level)
  };
}

function createFacetCounts<T extends string>(
  entries: readonly AuditEntry[],
  pickValue: (entry: AuditEntry) => T | null,
  pickLabel?: (entry: AuditEntry, value: T) => string | undefined
): AuditFacetCount<T>[] {
  const buckets = new Map<T, { count: number; label?: string }>();

  for (const entry of entries) {
    const value = pickValue(entry);
    if (value === null) {
      continue;
    }

    const current = buckets.get(value);
    const label = pickLabel?.(entry, value);
    const nextLabel = current?.label ?? label;
    buckets.set(value, {
      count: (current?.count ?? 0) + 1,
      ...(nextLabel === undefined ? {} : { label: nextLabel })
    });
  }

  return Array.from(buckets.entries())
    .map(([value, bucket]) => ({ value, count: bucket.count, ...(bucket.label === undefined ? {} : { label: bucket.label }) }))
    .sort(compareFacetCounts);
}

function createFixedFacetCounts<T extends string>(
  entries: readonly AuditEntry[],
  order: readonly T[],
  pickValue: (entry: AuditEntry) => T
): AuditFacetCount<T>[] {
  const counts = new Map<T, number>();

  for (const entry of entries) {
    const value = pickValue(entry);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }

  return order
    .filter((value) => (counts.get(value) ?? 0) > 0)
    .map((value) => ({ value, count: counts.get(value) ?? 0 }));
}

function compareFacetCounts(left: AuditFacetCount, right: AuditFacetCount): number {
  if (left.count !== right.count) {
    return right.count - left.count;
  }

  const leftLabel = left.label ?? left.value;
  const rightLabel = right.label ?? right.value;
  return leftLabel.localeCompare(rightLabel);
}

function compareAuditEntries(left: AuditEntry, right: AuditEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  if (left.kind !== right.kind) {
    return AUDIT_ENTRY_KIND_RANK[left.kind] - AUDIT_ENTRY_KIND_RANK[right.kind];
  }

  if (left.level !== right.level) {
    return AUDIT_ENTRY_LEVEL_RANK[left.level] - AUDIT_ENTRY_LEVEL_RANK[right.level];
  }

  return left.title.localeCompare(right.title);
}

function countEntriesByKind(entries: readonly AuditEntry[], kind: AuditEntryKind): number {
  return entries.filter((entry) => entry.kind === kind).length;
}

function countEntriesByLevel(entries: readonly AuditEntry[], level: AuditEntryLevel): number {
  return entries.filter((entry) => entry.level === level).length;
}

function cloneJsonRecord(record: Record<string, JsonValue>): Record<string, JsonValue> {
  return JSON.parse(JSON.stringify(record)) as Record<string, JsonValue>;
}

function readStringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function formatToolCallDetail(toolCall: ToolCallLogEntry): string {
  const path = readStringValue(toolCall.params.path);
  if (path !== undefined) {
    return `${toolCall.tool} on ${path}`;
  }

  const taskId = readStringValue(toolCall.params.task_id);
  if (taskId !== undefined) {
    return `${toolCall.tool} for ${taskId}`;
  }

  const query = readStringValue(toolCall.params.query);
  if (query !== undefined) {
    return `${toolCall.tool} for ${query}`;
  }

  return toolCall.tool;
}

function resolveHostDisplayName(state: GameState, hostId: string): string {
  return state.hostBindings?.[hostId]?.binding.displayName ?? hostId;
}

function resolveHostBindingLevel(record: HostBindingRecord): AuditEntryLevel {
  if (record.binding.connectionState === 'blocked' || record.binding.trustStatus === 'blocked') {
    return 'error';
  }

  if (
    record.binding.connectionState === 'degraded' ||
    record.binding.connectionState === 'offline' ||
    record.binding.connectionState === 'stale' ||
    record.binding.trustStatus === 'review' ||
    record.binding.trustStatus === 'restricted'
  ) {
    return 'warning';
  }

  return 'info';
}

function resolveHostInvocationLevel(decision: HostInvocationDecisionRecord['decision']): AuditEntryLevel {
  if (decision === 'DENY') {
    return 'error';
  }

  if (decision === 'PROMPT' || decision === 'DEGRADE') {
    return 'warning';
  }

  return 'info';
}

function resolveHostReviewLevel(record: HostReviewArtifactRecord): AuditEntryLevel {
  if (record.review.verdict === 'fail' || record.review.findings.some((finding) => finding.severity === 'critical')) {
    return 'error';
  }

  if (
    record.review.verdict === 'warn' ||
    record.review.findings.some((finding) => finding.severity === 'high' || finding.severity === 'medium')
  ) {
    return 'warning';
  }

  return 'info';
}