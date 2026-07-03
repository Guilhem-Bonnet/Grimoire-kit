import { createAuditView, type AuditEntry } from './audit-view';
import {
  createSecurityAuditView,
  type SecurityAuditFinding,
  type SecurityAuditView,
  type SecurityOwaspFocusSummary
} from './branch-finisher-view';
import { createCollaborationView, type CollaborationView } from './collaboration-view';
import type { GameState } from './game-state';
import { createSessionView, type SessionView } from './session-view';
import { createTaskView, type TaskAlertCode, type TaskInspectionView, type TaskView } from './task-view';
import { createTimelineView, type TimelineFilter, type TimelineGap, type TimelineView } from './timeline-view';
import { createVerificationView, type VerificationView } from './verification-view';

export interface ObservabilityFocus {
  traceId?: string;
  taskId?: string;
  agentId?: string;
}

export interface ObservabilityViewOptions {
  focus?: ObservabilityFocus;
  timelineFilter?: TimelineFilter;
  maxTimelineEntries?: number;
  maxErrorEntries?: number;
}

export interface ObservabilitySummary {
  sessionCount: number;
  activeSessionCount: number;
  attentionSessionCount: number;
  taskCount: number;
  blockedTaskCount: number;
  readyTaskCount: number;
  hotspotCount: number;
  timelineEntryCount: number;
  timelineErrorCount: number;
  timelineWarningCount: number;
  timelineGapCount: number;
  connectionIssueCount: number;
  connectionStaleCount: number;
  connectionDisconnectedCount: number;
  securityFindingCount: number;
  securityOpenFindingCount: number;
  securityBlockingFindingCount: number;
  securityOwaspHotspotCount: number;
}

export type ObservabilityConnectionStatus = 'live' | 'stale' | 'disconnected';

export interface ObservabilityConnectionIssue {
  agentId: string;
  agentName: string | null;
  status: ObservabilityConnectionStatus;
  found: boolean;
  path: string | null;
  parsedLineCount: number;
  lastDataAt: string | null;
  scannedAt: string | null;
  staleAfterMs: number | null;
  ageMs: number | null;
}

export interface ObservabilityConnectionDiagnostics {
  status: ObservabilityConnectionStatus;
  found: boolean | null;
  path: string | null;
  parsedLineCount: number | null;
  lastDataAt: string | null;
  scannedAt: string | null;
  staleAfterMs: number | null;
  ageMs: number | null;
  issues: readonly ObservabilityConnectionIssue[];
  staleCount: number;
  disconnectedCount: number;
  issueCount: number;
}

export interface ObservabilityTaskAlert {
  taskId: string;
  taskTitle: string;
  codes: readonly TaskAlertCode[];
  warningCount: number;
  infoCount: number;
}

export interface ObservabilityArchitectureEscalation {
  taskId: string;
  taskTitle: string;
  consecutiveFixFailures: number;
  latestFailureSequenceId: number;
  latestFailureTimestamp: string;
  latestFailureTraceId: string | null;
}

export interface ObservabilityFocusSnapshot {
  traceId: string | null;
  taskId: string | null;
  agentId: string | null;
  traceTitle: string | null;
  taskTitle: string | null;
  agentName: string | null;
  matchingEntryCount: number;
}

export interface ObservabilityAttention {
  recentErrors: readonly AuditEntry[];
  timelineGaps: readonly TimelineGap[];
  blockedTaskIds: readonly string[];
  securityFindings: readonly SecurityAuditFinding[];
  owaspHotspots: readonly SecurityOwaspFocusSummary[];
  taskAlerts: readonly ObservabilityTaskAlert[];
  architectureEscalations: readonly ObservabilityArchitectureEscalation[];
  connectionIssues: readonly ObservabilityConnectionIssue[];
}

export interface ObservabilityProjections {
  timeline: TimelineView;
  sessions: SessionView;
  tasks: TaskView;
  verification: VerificationView;
  collaboration: CollaborationView;
  securityAudit: SecurityAuditView;
}

export interface ObservabilityView {
  protocolVersion: string;
  lastSequenceId: number;
  connection: ObservabilityConnectionDiagnostics | null;
  summary: ObservabilitySummary;
  focus: ObservabilityFocusSnapshot;
  attention: ObservabilityAttention;
  projections: ObservabilityProjections;
}

const FIX_FAILURE_STREAK_THRESHOLD = 3;
const FIX_FAILURE_EVENT_TOKEN = 'fix_failed';
const FIX_SUCCESS_EVENT_TOKENS = new Set([
  'fix_succeeded',
  'fix_success',
  'fix_successful',
  'fix_passed',
  'fix_verified',
  'fix_resolved'
]);

export function createObservabilityView(state: GameState, options: ObservabilityViewOptions = {}): ObservabilityView {
  const focus = normalizeFocus(options.focus);
  const timelineFilter = mergeTimelineFilters(focus, options.timelineFilter);
  const timelineOptions =
    options.maxTimelineEntries === undefined
      ? {}
      : {
          maxEntries: options.maxTimelineEntries
        };
  const projections: ObservabilityProjections = {
    timeline: createTimelineView(state, timelineFilter, timelineOptions),
    sessions: createSessionView(state),
    tasks: createTaskView(state),
    verification: createVerificationView(state),
    collaboration: createCollaborationView(state),
    securityAudit: createSecurityAuditView(state)
  };
  const errorLimit = normalizePositiveLimit(options.maxErrorEntries, 10);
  const recentErrors = createAuditView(state, { levels: ['error'] }).entries.slice(0, errorLimit);
  const securityFindings = projections.securityAudit.publishedFindings.filter((finding) => finding.status === 'open');
  const owaspHotspots = createOwaspHotspots(projections.securityAudit.owaspFocusAreas);
  const blockedTaskIds = projections.verification.tasks
    .filter((task) => !task.isReadyForDone)
    .map((task) => task.taskId)
    .sort((left, right) => left.localeCompare(right));
  const taskAlerts = projections.tasks.tasks
    .filter((task) => task.alerts.length > 0)
    .map((task) => ({
      taskId: task.task.id,
      taskTitle: task.task.title,
      codes: [...new Set(task.alerts.map((alert) => alert.code))].sort((left, right) => left.localeCompare(right)),
      warningCount: task.alerts.filter((alert) => alert.level === 'warning').length,
      infoCount: task.alerts.filter((alert) => alert.level === 'info').length
    }))
    .sort(compareTaskAlerts);
  const architectureEscalations = createArchitectureEscalations(projections.tasks.tasks);
  const connection = createConnectionDiagnostics(state);
  const connectionIssues = connection?.issues ?? [];
  const focusSnapshot = createFocusSnapshot(state, projections, focus);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    connection,
    summary: {
      sessionCount: projections.sessions.metrics.sessionCount,
      activeSessionCount: projections.sessions.metrics.activeCount,
      attentionSessionCount: projections.sessions.metrics.attentionCount,
      taskCount: projections.tasks.metrics.taskCount,
      blockedTaskCount: projections.verification.metrics.blockedCount,
      readyTaskCount: projections.verification.metrics.readyCount,
      hotspotCount: projections.collaboration.metrics.hotspotCount,
      timelineEntryCount: projections.timeline.metrics.filteredCount,
      timelineErrorCount: projections.timeline.metrics.errorCount,
      timelineWarningCount: projections.timeline.metrics.warningCount,
      timelineGapCount: projections.timeline.metrics.gapCount,
      connectionIssueCount: connection?.issueCount ?? 0,
      connectionStaleCount: connection?.staleCount ?? 0,
      connectionDisconnectedCount: connection?.disconnectedCount ?? 0,
      securityFindingCount: projections.securityAudit.metrics.publishedFindingCount,
      securityOpenFindingCount: projections.securityAudit.metrics.openFindingCount,
      securityBlockingFindingCount: projections.securityAudit.metrics.blockingFindingCount,
      securityOwaspHotspotCount: owaspHotspots.length
    },
    focus: focusSnapshot,
    attention: {
      recentErrors,
      timelineGaps: projections.timeline.gaps,
      blockedTaskIds,
      securityFindings,
      owaspHotspots,
      taskAlerts,
      architectureEscalations,
      connectionIssues
    },
    projections
  };
}

function createOwaspHotspots(owaspFocusAreas: readonly SecurityOwaspFocusSummary[]): SecurityOwaspFocusSummary[] {
  return owaspFocusAreas
    .filter((focusArea) => focusArea.blockingFindingCount > 0 || focusArea.openFindingCount > 1)
    .sort(compareOwaspHotspots);
}

function compareOwaspHotspots(left: SecurityOwaspFocusSummary, right: SecurityOwaspFocusSummary): number {
  if (left.blockingFindingCount !== right.blockingFindingCount) {
    return right.blockingFindingCount - left.blockingFindingCount;
  }

  if (left.openFindingCount !== right.openFindingCount) {
    return right.openFindingCount - left.openFindingCount;
  }

  if (left.findingCount !== right.findingCount) {
    return right.findingCount - left.findingCount;
  }

  return left.label.localeCompare(right.label);
}

function createArchitectureEscalations(
  tasks: readonly TaskInspectionView[]
): ObservabilityArchitectureEscalation[] {
  return tasks
    .map(createTaskArchitectureEscalation)
    .filter((escalation): escalation is ObservabilityArchitectureEscalation => escalation !== null)
    .sort(compareArchitectureEscalations);
}

function createTaskArchitectureEscalation(task: TaskInspectionView): ObservabilityArchitectureEscalation | null {
  let consecutiveFixFailures = 0;
  let latestFailure: TaskInspectionView['recentWorkflowSteps'][number] | null = null;

  for (const workflowStep of task.recentWorkflowSteps) {
    const outcome = classifyFixOutcome(workflowStep);
    if (outcome === 'failure') {
      consecutiveFixFailures += 1;
      latestFailure ??= workflowStep;
      continue;
    }

    if (outcome === 'success') {
      break;
    }
  }

  if (consecutiveFixFailures < FIX_FAILURE_STREAK_THRESHOLD || latestFailure === null) {
    return null;
  }

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    consecutiveFixFailures,
    latestFailureSequenceId: latestFailure.sequenceId,
    latestFailureTimestamp: latestFailure.timestamp,
    latestFailureTraceId: latestFailure.traceId ?? null
  };
}

function classifyFixOutcome(
  workflowStep: TaskInspectionView['recentWorkflowSteps'][number]
): 'failure' | 'success' | 'other' {
  const sourceEventType = normalizeToken(workflowStep.sourceEventType);
  if (isFixFailureToken(sourceEventType)) {
    return 'failure';
  }

  if (isFixSuccessToken(sourceEventType)) {
    return 'success';
  }

  const metadataStatus = normalizeToken(workflowStep.metadata.status);
  if (isFixFailureToken(metadataStatus)) {
    return 'failure';
  }

  if (isFixSuccessToken(metadataStatus)) {
    return 'success';
  }

  const metadataOutcome = normalizeToken(workflowStep.metadata.outcome);
  if (isFixFailureToken(metadataOutcome)) {
    return 'failure';
  }

  if (isFixSuccessToken(metadataOutcome)) {
    return 'success';
  }

  const metadataResult = normalizeToken(workflowStep.metadata.result);
  if (isFixFailureToken(metadataResult)) {
    return 'failure';
  }

  if (isFixSuccessToken(metadataResult)) {
    return 'success';
  }

  const stepToken = normalizeToken(workflowStep.step);
  if (isFixFailureToken(stepToken)) {
    return 'failure';
  }

  if (isFixSuccessToken(stepToken)) {
    return 'success';
  }

  const detailToken = normalizeToken(workflowStep.detail);
  if (isFixFailureToken(detailToken)) {
    return 'failure';
  }

  if (isFixSuccessToken(detailToken)) {
    return 'success';
  }

  return 'other';
}

function isFixFailureToken(value: string | null): boolean {
  return value !== null && value.includes(FIX_FAILURE_EVENT_TOKEN);
}

function isFixSuccessToken(value: string | null): boolean {
  return value !== null && FIX_SUCCESS_EVENT_TOKENS.has(value);
}

function normalizeToken(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized.length === 0) {
    return null;
  }

  return normalized.replace(/[\s-]+/g, '_');
}

function compareArchitectureEscalations(
  left: ObservabilityArchitectureEscalation,
  right: ObservabilityArchitectureEscalation
): number {
  if (left.consecutiveFixFailures !== right.consecutiveFixFailures) {
    return right.consecutiveFixFailures - left.consecutiveFixFailures;
  }

  if (left.latestFailureSequenceId !== right.latestFailureSequenceId) {
    return right.latestFailureSequenceId - left.latestFailureSequenceId;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function createConnectionDiagnostics(state: GameState): ObservabilityConnectionDiagnostics | null {
  const path = readOptionalString(state.config['live.connection.path']);
  const found = readOptionalBoolean(state.config['live.connection.found']);
  const parsedLineCount = readOptionalNumber(state.config['live.connection.parsedLineCount']);
  const lastDataAt = readOptionalString(state.config['live.connection.lastDataAt']);
  const scannedAt = readOptionalString(state.config['live.connection.scannedAt']);
  const staleAfterMs = readOptionalNumber(state.config['live.connection.staleAfterMs']);
  const ageMs = readOptionalNumber(state.config['live.connection.ageMs']);
  const status = readConnectionStatus(state.config['live.connection.status']);
  const byAgent = readConnectionIssuesByAgent(state);

  if (
    status === null &&
    path === null &&
    found === null &&
    parsedLineCount === null &&
    lastDataAt === null &&
    scannedAt === null &&
    staleAfterMs === null &&
    ageMs === null &&
    byAgent.length === 0
  ) {
    return null;
  }

  const disconnectedCount = byAgent.filter((entry) => entry.status === 'disconnected').length;
  const staleCount = byAgent.filter((entry) => entry.status === 'stale').length;
  const issues = byAgent.filter((entry) => entry.status !== 'live');
  const resolvedStatus =
    status ??
    (disconnectedCount > 0
      ? 'disconnected'
      : staleCount > 0
        ? 'stale'
        : found === false
          ? 'disconnected'
          : 'live');

  return {
    status: resolvedStatus,
    found,
    path,
    parsedLineCount,
    lastDataAt,
    scannedAt,
    staleAfterMs,
    ageMs,
    issues,
    staleCount,
    disconnectedCount,
    issueCount: issues.length
  };
}

function readConnectionIssuesByAgent(state: GameState): ObservabilityConnectionIssue[] {
  const byAgent = state.config['live.connection.byAgent'];
  if (!isRecord(byAgent)) {
    return [];
  }

  return Object.entries(byAgent)
    .flatMap(([agentId, rawValue]) => {
      if (!isRecord(rawValue)) {
        return [];
      }

      const status = readConnectionStatus(rawValue.status);
      if (status === null) {
        return [];
      }

      const found = readOptionalBoolean(rawValue.found) ?? false;
      const parsedLineCount = readOptionalNumber(rawValue.parsedLineCount) ?? 0;

      return [
        {
          agentId,
          agentName: state.agents[agentId]?.name ?? null,
          status,
          found,
          path: readOptionalString(rawValue.path),
          parsedLineCount,
          lastDataAt: readOptionalString(rawValue.lastDataAt),
          scannedAt: readOptionalString(rawValue.scannedAt),
          staleAfterMs: readOptionalNumber(rawValue.staleAfterMs),
          ageMs: readOptionalNumber(rawValue.ageMs)
        }
      ];
    })
    .sort(compareConnectionIssues);
}

function compareConnectionIssues(left: ObservabilityConnectionIssue, right: ObservabilityConnectionIssue): number {
  const leftPriority = connectionIssuePriority(left.status);
  const rightPriority = connectionIssuePriority(right.status);
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority;
  }

  return left.agentId.localeCompare(right.agentId);
}

function connectionIssuePriority(status: ObservabilityConnectionStatus): number {
  switch (status) {
    case 'disconnected':
      return 0;
    case 'stale':
      return 1;
    case 'live':
      return 2;
  }
}

function createFocusSnapshot(
  state: GameState,
  projections: ObservabilityProjections,
  focus: ObservabilityFocus
): ObservabilityFocusSnapshot {
  const traceId = normalizeString(focus.traceId);
  const taskId = normalizeString(focus.taskId);
  const agentId = normalizeString(focus.agentId);
  const hasActiveFocus = traceId !== null || taskId !== null || agentId !== null;

  return {
    traceId,
    taskId,
    agentId,
    traceTitle:
      traceId === null
        ? null
        : (projections.sessions.sessions.find((session) => session.summary.traceId === traceId)?.summary.title ?? null),
    taskTitle: taskId === null ? null : (state.tasks[taskId]?.title ?? null),
    agentName: agentId === null ? null : (state.agents[agentId]?.name ?? null),
    matchingEntryCount: hasActiveFocus
      ? projections.timeline.entries.filter((entry) => matchesFocus(entry, { traceId, taskId, agentId })).length
      : 0
  };
}

function matchesFocus(entry: AuditEntry, focus: { traceId: string | null; taskId: string | null; agentId: string | null }): boolean {
  if (focus.traceId !== null && entry.traceId !== focus.traceId) {
    return false;
  }

  if (focus.taskId !== null && entry.taskId !== focus.taskId) {
    return false;
  }

  if (focus.agentId !== null && entry.agentId !== focus.agentId) {
    return false;
  }

  return true;
}

function compareTaskAlerts(left: ObservabilityTaskAlert, right: ObservabilityTaskAlert): number {
  if (left.warningCount !== right.warningCount) {
    return right.warningCount - left.warningCount;
  }

  if (left.infoCount !== right.infoCount) {
    return right.infoCount - left.infoCount;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function normalizeFocus(focus: ObservabilityFocus | undefined): ObservabilityFocus {
  if (focus === undefined) {
    return {};
  }

  const traceId = normalizeString(focus.traceId);
  const taskId = normalizeString(focus.taskId);
  const agentId = normalizeString(focus.agentId);

  return {
    ...(traceId === null ? {} : { traceId }),
    ...(taskId === null ? {} : { taskId }),
    ...(agentId === null ? {} : { agentId })
  };
}

function mergeTimelineFilters(focus: ObservabilityFocus, timelineFilter: TimelineFilter | undefined): TimelineFilter {
  const fromFocus: TimelineFilter = {
    ...(focus.traceId === undefined ? {} : { traceId: focus.traceId }),
    ...(focus.taskId === undefined ? {} : { taskId: focus.taskId }),
    ...(focus.agentId === undefined ? {} : { agentId: focus.agentId })
  };

  return {
    ...fromFocus,
    ...compactTimelineFilter(timelineFilter)
  };
}

function compactTimelineFilter(filter: TimelineFilter | undefined): TimelineFilter {
  if (filter === undefined) {
    return {};
  }

  return {
    ...(filter.agentId === undefined ? {} : { agentId: filter.agentId }),
    ...(filter.taskId === undefined ? {} : { taskId: filter.taskId }),
    ...(filter.roomId === undefined ? {} : { roomId: filter.roomId }),
    ...(filter.traceId === undefined ? {} : { traceId: filter.traceId }),
    ...(filter.kinds === undefined ? {} : { kinds: filter.kinds }),
    ...(filter.levels === undefined ? {} : { levels: filter.levels }),
    ...(filter.query === undefined ? {} : { query: filter.query }),
    ...(filter.fromSequenceId === undefined ? {} : { fromSequenceId: filter.fromSequenceId }),
    ...(filter.toSequenceId === undefined ? {} : { toSequenceId: filter.toSequenceId })
  };
}

function normalizeString(value: string | undefined): string | null {
  if (value === undefined) {
    return null;
  }

  const normalized = value.trim();
  return normalized.length === 0 ? null : normalized;
}

function readConnectionStatus(value: unknown): ObservabilityConnectionStatus | null {
  if (value !== 'live' && value !== 'stale' && value !== 'disconnected') {
    return null;
  }

  return value;
}

function readOptionalString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim();
  return normalized.length === 0 ? null : normalized;
}

function readOptionalBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function readOptionalNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizePositiveLimit(value: number | undefined, defaultLimit: number): number {
  if (value === undefined || !Number.isFinite(value)) {
    return defaultLimit;
  }

  const normalized = Math.trunc(value);
  return normalized > 0 ? normalized : defaultLimit;
}