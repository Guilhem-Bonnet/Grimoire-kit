import type {
  ObservabilityAttentionItem,
  ObservabilityMetricCard,
  ObservabilityPanelView,
  ObservabilityTimelineRow
} from './observability-panel-view';
import type { ObservabilityConnectionIssue } from './observability-view';
import type { RuntimeDashboardUiTone } from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';
import type { SessionStatus } from './session-view';

export interface RuntimeObservabilitySurfaceHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  summary: string;
}

export interface RuntimeObservabilityBlockedTask {
  taskId: string;
  title: string;
  status: RuntimeDashboardView['verification']['tasks'][number]['taskStatus'];
  tone: RuntimeDashboardUiTone;
  unmetRequirementCount: number;
  evidenceCount: number;
  traceCount: number;
  detail: string;
}

export interface RuntimeObservabilityConnectionCard {
  agentId: string;
  agentName: string | null;
  status: ObservabilityConnectionIssue['status'];
  tone: RuntimeDashboardUiTone;
  detail: string;
}

export interface RuntimeObservabilityHotspotCard {
  id: string;
  label: string;
  tone: RuntimeDashboardUiTone;
  detail: string;
}

export interface RuntimeObservabilitySessionCard {
  traceId: string;
  title: string;
  status: SessionStatus;
  tone: RuntimeDashboardUiTone;
  entryCount: number;
  errorCount: number;
  activeTaskCount: number;
  lastEventTitle: string;
}

export interface RuntimeObservabilitySurfaceView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeObservabilitySurfaceHeader;
  focus: ObservabilityPanelView['focus'];
  metricCards: readonly ObservabilityMetricCard[];
  timelineRows: readonly ObservabilityTimelineRow[];
  attentionItems: readonly ObservabilityAttentionItem[];
  blockedTasks: readonly RuntimeObservabilityBlockedTask[];
  connectionIssues: readonly RuntimeObservabilityConnectionCard[];
  securityHotspots: readonly RuntimeObservabilityHotspotCard[];
  collaborationHotspots: readonly RuntimeObservabilityHotspotCard[];
  sessions: readonly RuntimeObservabilitySessionCard[];
  source: ObservabilityPanelView;
}

export function createRuntimeObservabilitySurfaceView(
  dashboard: RuntimeDashboardView
): RuntimeObservabilitySurfaceView {
  const panel = dashboard.observability;
  const source = panel.source;
  const blockedTasks = source.projections.verification.tasks
    .filter((task) => !task.isReadyForDone)
    .map((task) => ({
      taskId: task.taskId,
      title: task.taskTitle,
      status: task.taskStatus,
      tone: toneForBlockedTask(task.taskStatus, task.unmetRequirementCodes.length),
      unmetRequirementCount: task.unmetRequirementCodes.length,
      evidenceCount: task.evidenceCount,
      traceCount: task.traceCount,
      detail:
        task.unmetRequirementCodes.length === 0
          ? 'Verification gate incomplete.'
          : task.unmetRequirementCodes.join(', ')
    }));

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Observability deck',
      subtitle: 'Timeline, connection health, verification blockers and security hotspots stay explicit and queryable.',
      tone: toneForObservabilityHeader(dashboard),
      summary: `${panel.attentionItems.length} attention item(s), ${source.summary.connectionIssueCount} connection issue(s), ${source.summary.securityBlockingFindingCount} blocking security finding(s).`
    },
    focus: panel.focus,
    metricCards: panel.metricCards,
    timelineRows: panel.timelineRows,
    attentionItems: panel.attentionItems,
    blockedTasks,
    connectionIssues: (source.connection?.issues ?? []).map((issue) => ({
      agentId: issue.agentId,
      agentName: issue.agentName,
      status: issue.status,
      tone: toneForConnectionStatus(issue.status),
      detail: [
        issue.found ? 'JSONL found' : 'JSONL missing',
        `parsed ${issue.parsedLineCount} line(s)`,
        issue.lastDataAt === null ? 'last data n/a' : `last data ${issue.lastDataAt}`,
        issue.path === null ? null : issue.path
      ]
        .filter((part): part is string => part !== null)
        .join(' | ')
    })),
    securityHotspots: source.attention.owaspHotspots.map((hotspot) => ({
      id: `security-hotspot:${hotspot.focusArea}`,
      label: hotspot.label,
      tone: hotspot.blockingFindingCount > 0 ? 'critical' : 'warning',
      detail: `${hotspot.openFindingCount} open finding(s), ${hotspot.blockingFindingCount} blocking, ${hotspot.derivedSignalCount} derived signal(s).`
    })),
    collaborationHotspots: source.projections.collaboration.hotspots.map((hotspot) => ({
      id: `collaboration-hotspot:${hotspot.agentId}`,
      label: hotspot.agentId,
      tone: hotspot.handoffCount > 0 ? 'warning' : 'neutral',
      detail: `${hotspot.collaborationCount} link(s), ${hotspot.handoffCount} handoff(s), ${hotspot.traceCount} trace(s).`
    })),
    sessions: source.projections.sessions.sessions.map((session) => ({
      traceId: session.summary.traceId,
      title: session.summary.title,
      status: session.summary.status,
      tone: toneForSessionStatus(session.summary.status),
      entryCount: session.summary.entryCount,
      errorCount: session.summary.errorCount,
      activeTaskCount: session.summary.activeTaskCount,
      lastEventTitle: session.summary.lastEventTitle
    })),
    source: panel
  };
}

function toneForObservabilityHeader(dashboard: RuntimeDashboardView): RuntimeDashboardUiTone {
  if (dashboard.summary.criticalAttentionCount > 0 || dashboard.summary.securityBlockingFindingCount > 0) {
    return 'critical';
  }

  if (dashboard.summary.warningAttentionCount > 0 || dashboard.summary.blockedTaskCount > 0) {
    return 'warning';
  }

  return 'positive';
}

function toneForBlockedTask(
  status: RuntimeDashboardView['verification']['tasks'][number]['taskStatus'],
  unmetRequirementCount: number
): RuntimeDashboardUiTone {
  if (unmetRequirementCount > 1 || status === 'review') {
    return 'warning';
  }

  if (status === 'in_progress') {
    return 'positive';
  }

  return 'neutral';
}

function toneForConnectionStatus(status: ObservabilityConnectionIssue['status']): RuntimeDashboardUiTone {
  switch (status) {
    case 'disconnected':
      return 'critical';
    case 'stale':
      return 'warning';
    default:
      return 'positive';
  }
}

function toneForSessionStatus(status: SessionStatus): RuntimeDashboardUiTone {
  switch (status) {
    case 'attention':
      return 'warning';
    case 'active':
      return 'positive';
    default:
      return 'neutral';
  }
}