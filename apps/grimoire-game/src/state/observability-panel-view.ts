import type { AuditEntryKind, AuditEntryLevel } from './audit-view';
import {
  describeSecurityFindingOwaspRisk,
  type SecurityAuditFinding,
  type SecurityOwaspFocusSummary
} from './branch-finisher-view';
import type { GameState } from './game-state';
import {
  type ObservabilityArchitectureEscalation,
  createObservabilityView,
  type ObservabilityAttention,
  type ObservabilityConnectionIssue,
  type ObservabilityTaskAlert,
  type ObservabilityView,
  type ObservabilityViewOptions
} from './observability-view';

export type ObservabilityMetricStatus = 'ok' | 'warning' | 'critical';
export type ObservabilityAttentionSeverity = 'critical' | 'warning' | 'info';
export type ObservabilityAttentionKind =
  | 'runtime_error'
  | 'security_finding'
  | 'owasp_hotspot'
  | 'timeline_gap'
  | 'task_alert'
  | 'connection_status'
  | 'architecture_escalation';

export interface ObservabilityPanelOptions extends ObservabilityViewOptions {
  maxTimelineRows?: number;
  maxAttentionItems?: number;
}

export interface ObservabilityMetricCard {
  id: string;
  label: string;
  value: number;
  status: ObservabilityMetricStatus;
  hint: string;
}

export interface ObservabilityTimelineRow {
  id: string;
  sequenceId: number;
  timestamp: string;
  title: string;
  detail: string;
  kind: AuditEntryKind;
  level: AuditEntryLevel;
  agentId: string | null;
  taskId: string | null;
  traceId: string | null;
}

export interface ObservabilityAttentionItem {
  id: string;
  kind: ObservabilityAttentionKind;
  severity: ObservabilityAttentionSeverity;
  label: string;
  detail: string;
  sequenceId: number | null;
  taskId: string | null;
  traceId: string | null;
}

export interface ObservabilityPanelView {
  protocolVersion: string;
  lastSequenceId: number;
  focus: ObservabilityView['focus'];
  metricCards: readonly ObservabilityMetricCard[];
  timelineRows: readonly ObservabilityTimelineRow[];
  attentionItems: readonly ObservabilityAttentionItem[];
  source: ObservabilityView;
}

const ATTENTION_SEVERITY_RANK: Record<ObservabilityAttentionSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2
};

export function createObservabilityPanelView(state: GameState, options: ObservabilityPanelOptions = {}): ObservabilityPanelView {
  const source = createObservabilityView(state, toObservabilityOptions(options));
  const metricCards = createMetricCards(source);
  const timelineRows = createTimelineRows(source, options.maxTimelineRows);
  const attentionItems = createAttentionItems(source.attention, options.maxAttentionItems);

  return {
    protocolVersion: source.protocolVersion,
    lastSequenceId: source.lastSequenceId,
    focus: source.focus,
    metricCards,
    timelineRows,
    attentionItems,
    source
  };
}

function toObservabilityOptions(options: ObservabilityPanelOptions): ObservabilityViewOptions {
  return {
    ...(options.focus === undefined ? {} : { focus: options.focus }),
    ...(options.timelineFilter === undefined ? {} : { timelineFilter: options.timelineFilter }),
    ...(options.maxTimelineEntries === undefined ? {} : { maxTimelineEntries: options.maxTimelineEntries }),
    ...(options.maxErrorEntries === undefined ? {} : { maxErrorEntries: options.maxErrorEntries })
  };
}

function createMetricCards(view: ObservabilityView): ObservabilityMetricCard[] {
  const { summary } = view;

  return [
    {
      id: 'sessions',
      label: 'Sessions',
      value: summary.sessionCount,
      status: summary.attentionSessionCount > 0 ? 'warning' : 'ok',
      hint: `${summary.activeSessionCount} active(s)`
    },
    {
      id: 'tasks-blocked',
      label: 'Tasks blocked',
      value: summary.blockedTaskCount,
      status: summary.blockedTaskCount > 0 ? 'critical' : 'ok',
      hint: `${summary.readyTaskCount} ready`
    },
    {
      id: 'security-findings',
      label: 'Security findings',
      value: summary.securityBlockingFindingCount,
      status:
        summary.securityBlockingFindingCount > 0
          ? 'critical'
          : summary.securityOpenFindingCount > 0
            ? 'warning'
            : 'ok',
      hint: `${summary.securityOpenFindingCount} open / ${summary.securityOwaspHotspotCount} hotspot(s)`
    },
    {
      id: 'timeline-errors',
      label: 'Timeline errors',
      value: summary.timelineErrorCount,
      status: summary.timelineErrorCount > 0 ? 'critical' : summary.timelineWarningCount > 0 ? 'warning' : 'ok',
      hint: `${summary.timelineWarningCount} warning(s)`
    },
    {
      id: 'timeline-gaps',
      label: 'Timeline gaps',
      value: summary.timelineGapCount,
      status: summary.timelineGapCount > 0 ? 'warning' : 'ok',
      hint: `${summary.timelineEntryCount} entries`
    },
    {
      id: 'connection-status',
      label: 'Connection issues',
      value: summary.connectionIssueCount,
      status:
        summary.connectionDisconnectedCount > 0
          ? 'critical'
          : summary.connectionStaleCount > 0
            ? 'warning'
            : 'ok',
      hint: `${summary.connectionDisconnectedCount} disconnected / ${summary.connectionStaleCount} stale`
    },
    {
      id: 'collaboration-hotspots',
      label: 'Collaboration hotspots',
      value: summary.hotspotCount,
      status: summary.hotspotCount > 0 ? 'ok' : 'warning',
      hint: 'Agent interaction graph'
    }
  ];
}

function createTimelineRows(view: ObservabilityView, maxTimelineRows: number | undefined): ObservabilityTimelineRow[] {
  const rowLimit = normalizePositiveLimit(maxTimelineRows, 120);
  const entries = view.projections.timeline.entries;
  const slicedEntries = entries.length <= rowLimit ? [...entries] : [...entries.slice(-rowLimit)];

  return slicedEntries.map((entry) => ({
    id: entry.id,
    sequenceId: entry.sequenceId,
    timestamp: entry.timestamp,
    title: entry.title,
    detail: entry.detail,
    kind: entry.kind,
    level: entry.level,
    agentId: entry.agentId,
    taskId: entry.taskId,
    traceId: entry.traceId
  }));
}

function createAttentionItems(attention: ObservabilityAttention, maxAttentionItems: number | undefined): ObservabilityAttentionItem[] {
  const errorItems = attention.recentErrors.map((entry) => ({
    id: `runtime-error-${entry.sequenceId}`,
    kind: 'runtime_error' as const,
    severity: 'critical' as const,
    label: entry.title,
    detail: entry.detail,
    sequenceId: entry.sequenceId,
    taskId: entry.taskId,
    traceId: entry.traceId
  }));
  const securityFindingItems = attention.securityFindings.map(toSecurityFindingAttentionItem);
  const owaspHotspotItems = attention.owaspHotspots.map(toOwaspHotspotAttentionItem);
  const gapItems = attention.timelineGaps.map((gap) => ({
    id: `timeline-gap-${gap.fromSequenceId}-${gap.toSequenceId}`,
    kind: 'timeline_gap' as const,
    severity: 'warning' as const,
    label: `Missing sequence window ${gap.fromSequenceId}-${gap.toSequenceId}`,
    detail: `${gap.missingCount} sequence(s) missing in timeline window.`,
    sequenceId: gap.toSequenceId,
    taskId: null,
    traceId: null
  }));
  const architectureEscalationItems = attention.architectureEscalations.map(toArchitectureEscalationAttentionItem);
  const connectionItems = attention.connectionIssues.map(toConnectionAttentionItem);
  const taskAlertItems = attention.taskAlerts.map(toTaskAlertAttentionItem);
  const mergedItems = [
    ...errorItems,
    ...securityFindingItems,
    ...architectureEscalationItems,
    ...connectionItems,
    ...owaspHotspotItems,
    ...gapItems,
    ...taskAlertItems
  ].sort(compareAttentionItems);
  const itemLimit = normalizePositiveLimit(maxAttentionItems, 24);

  return mergedItems.length <= itemLimit ? mergedItems : mergedItems.slice(0, itemLimit);
}

function toSecurityFindingAttentionItem(finding: SecurityAuditFinding): ObservabilityAttentionItem {
  const owaspRiskLabel = describeSecurityFindingOwaspRisk(finding);

  return {
    id: `security-finding-${finding.findingId}`,
    kind: 'security_finding',
    severity: finding.blocksShip || finding.severity === 'critical' ? 'critical' : 'warning',
    label:
      owaspRiskLabel === null
        ? `Security finding: ${finding.title}`
        : `Security finding: ${owaspRiskLabel} - ${finding.title}`,
    detail: `${finding.findingId} on ${finding.surfaceId}: ${finding.exploitScenario}`,
    sequenceId: finding.sequenceId,
    taskId: finding.taskId,
    traceId: finding.traceId
  };
}

function toOwaspHotspotAttentionItem(focusArea: SecurityOwaspFocusSummary): ObservabilityAttentionItem {
  const severity: ObservabilityAttentionSeverity = focusArea.blockingFindingCount > 0 ? 'critical' : 'warning';

  return {
    id: `owasp-hotspot-${focusArea.focusArea}`,
    kind: 'owasp_hotspot',
    severity,
    label: `OWASP hotspot: ${focusArea.label}`,
    detail: `${focusArea.openFindingCount} open finding(s), ${focusArea.blockingFindingCount} blocking, ${focusArea.derivedSignalCount} derived signal(s).`,
    sequenceId: null,
    taskId: null,
    traceId: null
  };
}

function toArchitectureEscalationAttentionItem(
  escalation: ObservabilityArchitectureEscalation
): ObservabilityAttentionItem {
  return {
    id: `architecture-escalation-${escalation.taskId}`,
    kind: 'architecture_escalation',
    severity: 'critical',
    label: 'Architecture review required',
    detail: `Task ${escalation.taskTitle} reached ${escalation.consecutiveFixFailures} consecutive fix_failed outcomes (latest sequence ${escalation.latestFailureSequenceId}).`,
    sequenceId: escalation.latestFailureSequenceId,
    taskId: escalation.taskId,
    traceId: escalation.latestFailureTraceId
  };
}

function toConnectionAttentionItem(issue: ObservabilityConnectionIssue): ObservabilityAttentionItem {
  const severity: ObservabilityAttentionSeverity = issue.status === 'disconnected' ? 'critical' : 'warning';

  return {
    id: `connection-status-${issue.agentId}`,
    kind: 'connection_status',
    severity,
    label: `Connection ${issue.status}: ${issue.agentName ?? issue.agentId}`,
    detail: [
      issue.found ? 'JSONL found' : 'JSONL not found',
      `parsed ${issue.parsedLineCount} line(s)`,
      issue.lastDataAt === null ? 'last data n/a' : `last data ${issue.lastDataAt}`,
      issue.path === null ? null : issue.path
    ]
      .filter((part): part is string => part !== null)
      .join(' | '),
    sequenceId: null,
    taskId: null,
    traceId: null
  };
}

function toTaskAlertAttentionItem(alert: ObservabilityTaskAlert): ObservabilityAttentionItem {
  const severity: ObservabilityAttentionSeverity = alert.warningCount > 0 ? 'warning' : 'info';

  return {
    id: `task-alert-${alert.taskId}`,
    kind: 'task_alert',
    severity,
    label: `Task alert: ${alert.taskTitle}`,
    detail: `${alert.codes.join(', ')} (${alert.warningCount} warning / ${alert.infoCount} info).`,
    sequenceId: null,
    taskId: alert.taskId,
    traceId: null
  };
}

function compareAttentionItems(left: ObservabilityAttentionItem, right: ObservabilityAttentionItem): number {
  if (left.severity !== right.severity) {
    return ATTENTION_SEVERITY_RANK[left.severity] - ATTENTION_SEVERITY_RANK[right.severity];
  }

  const leftSequenceId = left.sequenceId ?? -1;
  const rightSequenceId = right.sequenceId ?? -1;
  if (leftSequenceId !== rightSequenceId) {
    return rightSequenceId - leftSequenceId;
  }

  return left.label.localeCompare(right.label);
}

function normalizePositiveLimit(value: number | undefined, defaultLimit: number): number {
  if (value === undefined || !Number.isFinite(value)) {
    return defaultLimit;
  }

  const normalized = Math.trunc(value);
  return normalized > 0 ? normalized : defaultLimit;
}