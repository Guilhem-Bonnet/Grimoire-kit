import type { TaskStatus } from '../contracts/events';

import type { MissionLedgerVerificationStatus, MissionLedgerVerificationVerdict } from './mission-ledger-view';
import type { ObservabilityAttentionSeverity, ObservabilityAttentionKind } from './observability-panel-view';
import type { RuntimeDashboardSummary, RuntimeDashboardView } from './runtime-dashboard-view';
import { VERIFICATION_QUEUE_STATUS_ORDER, type VerificationQueueStatus } from './verification-queue-view';
import type { VerificationVerdict } from './verification-view';

export type RuntimeDashboardUiTone = 'positive' | 'neutral' | 'warning' | 'critical';

export interface RuntimeDashboardUiHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeDashboardUiStatCard {
  id: string;
  label: string;
  value: number;
  tone: RuntimeDashboardUiTone;
  hint: string;
}

export interface RuntimeDashboardUiFleetCard {
  nodeId: string;
  status: RuntimeDashboardView['nodeFleet']['nodes'][number]['status'];
  tone: RuntimeDashboardUiTone;
  workerCount: number;
  activeLeaseCount: number;
  ageMs: number;
  lastSeenAt: string;
  capabilityTags: readonly string[];
}

export interface RuntimeDashboardUiOwnershipCard {
  leaseId: string;
  taskId: string;
  taskTitle: string | null;
  ownerId: string | null;
  nodeId: string;
  branch: string | null;
  worktreeId: string | null;
  status: RuntimeDashboardView['leaseView']['leases'][number]['status'];
  ownershipStatus: RuntimeDashboardView['leaseView']['leases'][number]['ownershipStatus'];
  dirtyStatus: RuntimeDashboardView['leaseView']['leases'][number]['dirtyStatus'];
  tone: RuntimeDashboardUiTone;
  detail: string;
}

export interface RuntimeDashboardUiHostCard {
  hostId: string;
  displayName: string;
  hostType: RuntimeDashboardView['hostBridge']['hosts'][number]['hostType'];
  connectionState: RuntimeDashboardView['hostBridge']['hosts'][number]['connectionState'];
  trustStatus: RuntimeDashboardView['hostBridge']['hosts'][number]['trustStatus'];
  permissionMode: RuntimeDashboardView['hostBridge']['hosts'][number]['permissionMode'];
  tone: RuntimeDashboardUiTone;
  scopeCount: number;
  reviewChannelCount: number;
  contextSourceCount: number;
  detail: string;
}

export interface RuntimeDashboardUiLaneTask {
  id: string;
  title: string;
  status: TaskStatus;
  assigneeId: string | null;
  assigneeName: string | null;
}

export interface RuntimeDashboardUiTaskLane {
  status: TaskStatus;
  label: string;
  count: number;
  tone: RuntimeDashboardUiTone;
  tasks: readonly RuntimeDashboardUiLaneTask[];
}

export interface RuntimeDashboardUiAttentionItem {
  id: string;
  kind: ObservabilityAttentionKind;
  severity: ObservabilityAttentionSeverity;
  tone: RuntimeDashboardUiTone;
  label: string;
  detail: string;
  taskId: string | null;
  traceId: string | null;
  context: string | null;
}

export interface RuntimeDashboardUiVerificationQueueItem {
  id: string;
  taskId: string;
  title: string;
  taskStatus: TaskStatus;
  queueStatus: VerificationQueueStatus;
  tone: RuntimeDashboardUiTone;
  assigneeId: string | null;
  assigneeName: string | null;
  verificationRef: string | null;
  verdict: VerificationVerdict | null;
  unmetRequirementCount: number;
  unmetRequirementCodes: readonly string[];
  detail: string;
}

export interface RuntimeDashboardUiVerificationLane {
  status: VerificationQueueStatus;
  label: string;
  count: number;
  tone: RuntimeDashboardUiTone;
  items: readonly RuntimeDashboardUiVerificationQueueItem[];
}

export interface RuntimeDashboardUiEvidencePackCard {
  id: string;
  missionId: string;
  missionTitle: string;
  taskRef: string | null;
  verificationRef: string;
  status: MissionLedgerVerificationStatus;
  verdict: MissionLedgerVerificationVerdict;
  tone: RuntimeDashboardUiTone;
  checkedBy: string;
  checkedAt: string;
  evidenceCount: number;
  controlCount: number;
  attested: boolean;
  missionMode: string | null;
  expectedProofCount: number;
  missingExpectedProofCount: number;
  externalReviews: readonly RuntimeDashboardUiExternalReviewCard[];
  detail: string;
}

export interface RuntimeDashboardUiExternalReviewCard {
  reviewId: string;
  hostId: string;
  hostDisplayName: string | null;
  verdict: string;
  tone: RuntimeDashboardUiTone;
  taskId: string | null;
  traceId: string | null;
  detail: string;
}

export interface RuntimeDashboardUiTimelinePoint {
  id: string;
  sequenceId: number;
  timestamp: string;
  level: 'error' | 'warning' | 'info';
  title: string;
}

export interface RuntimeDashboardUiFocus {
  runId: string | null;
  traceId: string | null;
  taskId: string | null;
  nodeId: string | null;
  agentId: string | null;
  traceTitle: string | null;
  taskTitle: string | null;
  agentName: string | null;
}

export interface RuntimeDashboardUiView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeDashboardUiHeader;
  statCards: readonly RuntimeDashboardUiStatCard[];
  fleet: readonly RuntimeDashboardUiFleetCard[];
  hosts: readonly RuntimeDashboardUiHostCard[];
  ownership: readonly RuntimeDashboardUiOwnershipCard[];
  lanes: readonly RuntimeDashboardUiTaskLane[];
  attention: readonly RuntimeDashboardUiAttentionItem[];
  verificationQueue: readonly RuntimeDashboardUiVerificationLane[];
  evidencePacks: readonly RuntimeDashboardUiEvidencePackCard[];
  timeline: readonly RuntimeDashboardUiTimelinePoint[];
  focus: RuntimeDashboardUiFocus;
}

export interface RuntimeDashboardUiViewOptions {
  maxTasksPerLane?: number;
  maxAttentionItems?: number;
  maxVerificationItemsPerLane?: number;
  maxEvidencePacks?: number;
  maxTimelinePoints?: number;
}

const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  backlog: 'Backlog',
  todo: 'Todo',
  in_progress: 'In progress',
  review: 'Review',
  done: 'Done'
};

const TASK_STATUS_TONES: Record<TaskStatus, RuntimeDashboardUiTone> = {
  backlog: 'neutral',
  todo: 'neutral',
  in_progress: 'positive',
  review: 'warning',
  done: 'positive'
};

const VERIFICATION_QUEUE_STATUS_LABELS: Record<VerificationQueueStatus, string> = {
  rejected: 'Rejected',
  needs_work: 'Needs work',
  verifying: 'Verifying',
  queued: 'Queued',
  accepted: 'Accepted'
};

const VERIFICATION_QUEUE_STATUS_TONES: Record<VerificationQueueStatus, RuntimeDashboardUiTone> = {
  rejected: 'critical',
  needs_work: 'warning',
  verifying: 'positive',
  queued: 'neutral',
  accepted: 'positive'
};

export function createRuntimeDashboardUiView(
  dashboard: RuntimeDashboardView,
  options: RuntimeDashboardUiViewOptions = {}
): RuntimeDashboardUiView {
  const maxTasksPerLane = normalizePositiveLimit(options.maxTasksPerLane, 6);
  const maxAttentionItems = normalizePositiveLimit(options.maxAttentionItems, 12);
  const maxVerificationItemsPerLane = normalizePositiveLimit(options.maxVerificationItemsPerLane, 4);
  const maxEvidencePacks = normalizePositiveLimit(options.maxEvidencePacks, 6);
  const maxTimelinePoints = normalizePositiveLimit(options.maxTimelinePoints, 120);
  const agentsById = Object.fromEntries(dashboard.board.agents.map((agent) => [agent.id, agent.name]));
  const hostDisplayNamesById = Object.fromEntries(dashboard.hostBridge.hosts.map((host) => [host.hostId, host.displayName]));
  const taskTitlesById = Object.fromEntries(
    dashboard.board.taskColumns.flatMap((column) => column.tasks.map((task) => [task.id, task.title]))
  );
  const activeLeaseCountByNodeId = dashboard.leaseView.leases.reduce((counts, lease) => {
    if (lease.status !== 'active') {
      return counts;
    }

    counts.set(lease.nodeId, (counts.get(lease.nodeId) ?? 0) + 1);
    return counts;
  }, new Map<string, number>());
  const lanes = dashboard.board.taskColumns.map((column) => ({
    status: column.status,
    label: TASK_STATUS_LABELS[column.status],
    count: column.count,
    tone: TASK_STATUS_TONES[column.status],
    tasks: column.tasks.slice(0, maxTasksPerLane).map((task) => ({
      id: task.id,
      title: task.title,
      status: task.status,
      assigneeId: task.assigneeId ?? null,
      assigneeName: task.assigneeId === undefined || task.assigneeId === null ? null : (agentsById[task.assigneeId] ?? null)
    }))
  }));

  const fleet = dashboard.nodeFleet.nodes.map((node) => ({
    nodeId: node.nodeId,
    status: node.status,
    tone: toneForNodeStatus(node.status),
    workerCount: node.workerIds.length,
    activeLeaseCount: activeLeaseCountByNodeId.get(node.nodeId) ?? 0,
    ageMs: node.ageMs,
    lastSeenAt: node.lastSeenAt,
    capabilityTags: [...node.capabilityTags]
  }));

  const ownership = dashboard.leaseView.leases.map((lease) => ({
    leaseId: lease.leaseId,
    taskId: lease.taskId,
    taskTitle: taskTitlesById[lease.taskId] ?? null,
    ownerId: lease.ownerId,
    nodeId: lease.nodeId,
    branch: lease.branch,
    worktreeId: lease.worktreeId,
    status: lease.status,
    ownershipStatus: lease.ownershipStatus,
    dirtyStatus: lease.dirtyStatus,
    tone: toneForOwnership(lease),
    detail: createOwnershipDetail(lease)
  }));

  const hosts = dashboard.hostBridge.hosts.map((host) => ({
    hostId: host.hostId,
    displayName: host.displayName,
    hostType: host.hostType,
    connectionState: host.connectionState,
    trustStatus: host.trustStatus,
    permissionMode: host.permissionMode,
    tone: toneForHost(host.connectionState, host.trustStatus),
    scopeCount: host.scopes.length,
    reviewChannelCount: host.reviewChannels.length,
    contextSourceCount: host.contextSources.length,
    detail: `${host.connectionState} | ${host.permissionMode} | ${host.toolProviderCount} provider(s)`
  }));

  const attention = dashboard.observability.attentionItems.slice(0, maxAttentionItems).map((item) => ({
    id: item.id,
    kind: item.kind,
    severity: item.severity,
    tone: toneForAttentionSeverity(item.severity),
    label: item.label,
    detail: item.detail,
    taskId: item.taskId,
    traceId: item.traceId,
    context: createAttentionContext(item.taskId, item.traceId)
  }));

  const verificationQueue = VERIFICATION_QUEUE_STATUS_ORDER.map((status) => {
    const queueItems = dashboard.verificationQueue.items
      .filter((item) => item.queueStatus === status)
      .slice(0, maxVerificationItemsPerLane)
      .map((item) => ({
        id: item.queueId,
        taskId: item.taskId,
        title: item.taskTitle,
        taskStatus: item.taskStatus,
        queueStatus: item.queueStatus,
        tone: VERIFICATION_QUEUE_STATUS_TONES[item.queueStatus],
        assigneeId: item.assigneeAgentId,
        assigneeName: item.assigneeAgentName,
        verificationRef: item.verificationRef,
        verdict: item.verdict,
        unmetRequirementCount: item.unmetRequirementCodes.length,
        unmetRequirementCodes: item.unmetRequirementCodes,
        detail: createVerificationQueueDetail(item)
      }));

    return {
      status,
      label: VERIFICATION_QUEUE_STATUS_LABELS[status],
      count: dashboard.verificationQueue.items.filter((item) => item.queueStatus === status).length,
      tone: VERIFICATION_QUEUE_STATUS_TONES[status],
      items: queueItems
    };
  });

  const evidencePacks = dashboard.verificationEvidencePacks.packs.slice(0, maxEvidencePacks).map((pack) => ({
    id: pack.packId,
    missionId: pack.missionId,
    missionTitle: pack.missionTitle,
    taskRef: pack.taskRef,
    verificationRef: pack.verificationRef,
    status: pack.status,
    verdict: pack.verdict,
    tone: toneForEvidencePack(
      pack.status,
      pack.verdict,
      pack.attestation !== null,
      pack.evidence.length,
      pack.missionPack !== null,
      pack.proofCoverage?.fullyCovered ?? false
    ),
    checkedBy: pack.checkedBy,
    checkedAt: pack.checkedAt,
    evidenceCount: pack.evidence.length,
    controlCount: pack.controlRefs.length,
    attested: pack.attestation !== null,
    missionMode: pack.missionPack?.mode ?? null,
    expectedProofCount: pack.proofCoverage?.expectedProofCount ?? 0,
    missingExpectedProofCount: pack.proofCoverage?.missingExpectedProofRefs.length ?? 0,
    externalReviews: pack.externalReviews.map((review) => ({
      reviewId: review.reviewId,
      hostId: review.hostId,
      hostDisplayName: hostDisplayNamesById[review.hostId] ?? null,
      verdict: review.verdict,
      tone: toneForExternalReview(review.verdict, review.openFindingCount),
      taskId: review.taskId,
      traceId: review.traceId,
      detail: `${review.findingCount} finding(s), ${review.openFindingCount} open.`
    })),
    detail: createEvidencePackDetail(
      pack.attestation !== null,
      pack.evidence.length,
      pack.controlRefs.length,
      pack.proofCoverage?.expectedProofCount ?? 0,
      pack.proofCoverage?.satisfiedExpectedProofRefs.length ?? 0
    )
  }));

  const timelineRows = dashboard.observability.timelineRows;
  const timelineSource = timelineRows.length <= maxTimelinePoints ? timelineRows : timelineRows.slice(-maxTimelinePoints);
  const timeline = timelineSource.map((row) => ({
    id: row.id,
    sequenceId: row.sequenceId,
    timestamp: row.timestamp,
    level: row.level,
    title: row.title
  }));
  const focusOwnership = selectFocusOwnership(dashboard, dashboard.observability.focus.taskId);
  const focusTaskId = dashboard.observability.focus.taskId ?? focusOwnership?.taskId ?? null;

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: createHeader(dashboard),
    statCards: createStatCards(dashboard),
    fleet,
    hosts,
    ownership,
    lanes,
    attention,
    verificationQueue,
    evidencePacks,
    timeline,
    focus: {
      runId: dashboard.projectRegistry?.activeProject.runId ?? dashboard.nodeFleet.summary.runId ?? dashboard.leaseView.summary.runId,
      traceId: dashboard.observability.focus.traceId,
      taskId: focusTaskId,
      nodeId: focusOwnership?.nodeId ?? selectFocusNodeId(dashboard),
      agentId: dashboard.observability.focus.agentId,
      traceTitle: dashboard.observability.focus.traceTitle,
      taskTitle: dashboard.observability.focus.taskTitle ?? (focusTaskId === null ? null : (taskTitlesById[focusTaskId] ?? null)),
      agentName: dashboard.observability.focus.agentName
    }
  };
}

function createHeader(dashboard: RuntimeDashboardView): RuntimeDashboardUiHeader {
  const { summary } = dashboard;
  const tone =
    summary.criticalAttentionCount > 0
      ? 'critical'
      : summary.warningAttentionCount > 0 || summary.blockedTaskCount > 0
        ? 'warning'
        : summary.activeTaskCount > 0 || summary.workingAgentCount > 0
          ? 'positive'
          : 'neutral';

  const title =
    tone === 'critical'
      ? 'Immediate runtime attention required'
      : tone === 'warning'
        ? 'Runtime under observation'
        : tone === 'positive'
          ? 'Runtime flow is stable'
          : 'Runtime is idle';

  return {
    title,
    subtitle: `${summary.activeTaskCount} active task(s), ${summary.liveNodeCount}/${summary.nodeCount} live node(s), ${summary.activeLeaseCount} active lease(s), ${summary.totalAttentionCount} attention item(s).`,
    tone
  };
}

function createStatCards(dashboard: RuntimeDashboardView): RuntimeDashboardUiStatCard[] {
  const { summary } = dashboard;
  const sourceSummary = dashboard.observability.source.summary;

  return [
    {
      id: 'active-tasks',
      label: 'Active tasks',
      value: summary.activeTaskCount,
      tone: summary.activeTaskCount > 0 ? 'positive' : 'neutral',
      hint: `${sourceSummary.taskCount} task(s) tracked`
    },
    {
      id: 'blocked-tasks',
      label: 'Blocked tasks',
      value: summary.blockedTaskCount,
      tone: summary.blockedTaskCount > 0 ? 'warning' : 'positive',
      hint: `${sourceSummary.readyTaskCount} task(s) ready`
    },
    {
      id: 'working-agents',
      label: 'Working agents',
      value: summary.workingAgentCount,
      tone: summary.workingAgentCount > 0 ? 'positive' : 'neutral',
      hint: `${dashboard.board.metrics.agentCount} agent(s) visible`
    },
    {
      id: 'live-nodes',
      label: 'Live nodes',
      value: summary.liveNodeCount,
      tone: summary.offlineNodeCount > 0 ? 'warning' : summary.liveNodeCount > 0 ? 'positive' : 'neutral',
      hint: `${summary.nodeCount} node(s), ${summary.nodeWorkerCount} worker(s)`
    },
    {
      id: 'active-leases',
      label: 'Active leases',
      value: summary.activeLeaseCount,
      tone: summary.expiredLeaseCount > 0 || summary.leaseAlertCount > 0 ? 'warning' : summary.activeLeaseCount > 0 ? 'positive' : 'neutral',
      hint: `${summary.expiredLeaseCount} expired, ${summary.leaseAlertCount} alert(s)`
    },
    {
      id: 'critical-attention',
      label: 'Critical attention',
      value: summary.criticalAttentionCount,
      tone: summary.criticalAttentionCount > 0 ? 'critical' : 'positive',
      hint: `${summary.warningAttentionCount} warning item(s)`
    },
    {
      id: 'board-alerts',
      label: 'Board alerts',
      value: summary.boardAlertCount,
      tone: summary.boardAlertCount > 0 ? 'warning' : 'positive',
      hint: `${dashboard.board.metrics.roomCount} room(s) tracked`
    },
    {
      id: 'timeline-gaps',
      label: 'Timeline gaps',
      value: sourceSummary.timelineGapCount,
      tone: sourceSummary.timelineGapCount > 0 ? 'warning' : 'positive',
      hint: `${sourceSummary.timelineEntryCount} timeline row(s)`
    },
    {
      id: 'verification-queue',
      label: 'Verification queue',
      value: summary.verificationQueueCount,
      tone: toneForVerificationQueueSummary(summary),
      hint: `${summary.verificationNeedsWorkCount} needs work, ${summary.verificationRejectedCount} rejected`
    },
    {
      id: 'host-handoffs',
      label: 'Host handoffs',
      value: summary.hostHandoffPacketCount,
      tone: toneForHostHandoffSummary(summary),
      hint: `${summary.readyHostHandoffCount} ready, ${summary.reviewPendingHostHandoffCount} review, ${summary.blockedHostHandoffCount} blocked`
    },
    {
      id: 'evidence-packs',
      label: 'Evidence attestations',
      value: summary.verificationAttestationCount,
      tone: toneForEvidencePackSummary(summary, dashboard),
      hint:
        `${summary.verificationEvidencePackCount} pack(s), ` +
        `${dashboard.verificationEvidencePacks.summary.missingEvidenceCount} missing evidence, ` +
        `${summary.missingExpectedProofCount} missing proof ref(s)`
    }
  ];
}

function toneForVerificationQueueSummary(summary: RuntimeDashboardSummary): RuntimeDashboardUiTone {
  if (summary.verificationRejectedCount > 0) {
    return 'critical';
  }

  if (summary.verificationNeedsWorkCount > 0) {
    return 'warning';
  }

  if (summary.verificationQueueCount > 0) {
    return 'positive';
  }

  return 'neutral';
}

function toneForHostHandoffSummary(summary: RuntimeDashboardSummary): RuntimeDashboardUiTone {
  if (summary.blockedHostHandoffCount > 0) {
    return 'critical';
  }

  if (summary.reviewPendingHostHandoffCount > 0) {
    return 'warning';
  }

  if (summary.readyHostHandoffCount > 0) {
    return 'positive';
  }

  return 'neutral';
}

function toneForEvidencePackSummary(
  summary: RuntimeDashboardSummary,
  dashboard: RuntimeDashboardView
): RuntimeDashboardUiTone {
  if (summary.verificationEvidencePackCount === 0) {
    return 'neutral';
  }

  if (
    dashboard.verificationEvidencePacks.summary.missingEvidenceCount > 0 ||
    summary.verificationAttestationCount < summary.verificationEvidencePackCount ||
    summary.missionPackCoveredCount < summary.missionPackLinkedCount ||
    summary.missingExpectedProofCount > 0
  ) {
    return 'warning';
  }

  return 'positive';
}

function toneForAttentionSeverity(severity: ObservabilityAttentionSeverity): RuntimeDashboardUiTone {
  if (severity === 'critical') {
    return 'critical';
  }

  if (severity === 'warning') {
    return 'warning';
  }

  return 'neutral';
}

function createAttentionContext(taskId: string | null, traceId: string | null): string | null {
  const parts: string[] = [];

  if (taskId !== null) {
    parts.push(`Task ${taskId}`);
  }

  if (traceId !== null) {
    parts.push(`Trace ${traceId}`);
  }

  return parts.length === 0 ? null : parts.join(' | ');
}

function toneForEvidencePack(
  status: MissionLedgerVerificationStatus,
  verdict: MissionLedgerVerificationVerdict,
  attested: boolean,
  evidenceCount: number,
  hasMissionPack: boolean,
  fullyCoveredMissionPack: boolean
): RuntimeDashboardUiTone {
  if (status === 'rejected' || verdict === 'fail') {
    return 'critical';
  }

  if (!attested || evidenceCount === 0 || (hasMissionPack && !fullyCoveredMissionPack)) {
    return 'warning';
  }

  return 'positive';
}

function createVerificationQueueDetail(item: RuntimeDashboardView['verificationQueue']['items'][number]): string {
  return `${item.evidenceCount} evidence item(s), ${item.controlsExecuted.length} control(s), ${item.unmetRequirementCodes.length} unmet requirement(s).`;
}

function createEvidencePackDetail(
  attested: boolean,
  evidenceCount: number,
  controlCount: number,
  expectedProofCount: number,
  satisfiedProofCount: number
): string {
  const attestationLabel = attested ? 'attested' : 'attestation pending';
  const proofLabel =
    expectedProofCount > 0
      ? `${satisfiedProofCount}/${expectedProofCount} mission proof(s) satisfied`
      : 'mission proof contract missing';
  return `${evidenceCount} evidence record(s), ${controlCount} control(s), ${proofLabel}, ${attestationLabel}.`;
}

function normalizePositiveLimit(value: number | undefined, fallback: number): number {
  if (value === undefined || !Number.isFinite(value)) {
    return fallback;
  }

  const normalized = Math.trunc(value);
  return normalized > 0 ? normalized : fallback;
}

function toneForNodeStatus(status: RuntimeDashboardView['nodeFleet']['nodes'][number]['status']): RuntimeDashboardUiTone {
  if (status === 'offline') {
    return 'critical';
  }

  if (status === 'stale') {
    return 'warning';
  }

  return 'positive';
}

function toneForOwnership(lease: RuntimeDashboardView['leaseView']['leases'][number]): RuntimeDashboardUiTone {
  if (lease.ownershipStatus === 'conflicted') {
    return 'critical';
  }

  if (lease.ownershipStatus === 'unresolved' || lease.status === 'expired') {
    return 'warning';
  }

  if (lease.dirtyStatus === 'dirty') {
    return 'positive';
  }

  return 'neutral';
}

function toneForHost(
  connectionState: RuntimeDashboardView['hostBridge']['hosts'][number]['connectionState'],
  trustStatus: RuntimeDashboardView['hostBridge']['hosts'][number]['trustStatus']
): RuntimeDashboardUiTone {
  if (connectionState === 'blocked' || trustStatus === 'blocked') {
    return 'critical';
  }

  if (connectionState === 'degraded' || connectionState === 'stale' || trustStatus === 'review' || trustStatus === 'restricted') {
    return 'warning';
  }

  return 'positive';
}

function toneForExternalReview(verdict: string, openFindingCount: number): RuntimeDashboardUiTone {
  if (verdict === 'fail') {
    return 'critical';
  }

  if (verdict === 'warn' || openFindingCount > 0) {
    return 'warning';
  }

  return 'positive';
}

function createOwnershipDetail(lease: RuntimeDashboardView['leaseView']['leases'][number]): string {
  const branch = lease.branch ?? 'branch unresolved';
  const worktree = lease.worktreeId ?? 'worktree unresolved';
  return `${branch} | ${worktree} | ${lease.dirtyStatus}`;
}

function selectFocusNodeId(dashboard: RuntimeDashboardView): string | null {
  const taskId = dashboard.observability.focus.taskId;

  if (taskId !== null) {
    const matchingLease = dashboard.leaseView.leases.find((lease) => lease.taskId === taskId && lease.status === 'active');
    if (matchingLease !== undefined) {
      return matchingLease.nodeId;
    }
  }

  const criticalNodeAlert = dashboard.nodeFleet.alerts.find((alert) => alert.severity === 'critical');
  if (criticalNodeAlert !== undefined) {
    return criticalNodeAlert.nodeId;
  }

  const activeLease = dashboard.leaseView.leases.find((lease) => lease.status === 'active');
  if (activeLease !== undefined) {
    return activeLease.nodeId;
  }

  return dashboard.nodeFleet.nodes[0]?.nodeId ?? null;
}

function selectFocusOwnership(
  dashboard: RuntimeDashboardView,
  taskId: string | null
): RuntimeDashboardView['leaseView']['leases'][number] | undefined {
  if (taskId !== null) {
    const taskLease = dashboard.leaseView.leases.find((lease) => lease.taskId === taskId && lease.status === 'active');
    if (taskLease !== undefined) {
      return taskLease;
    }
  }

  return dashboard.leaseView.leases.find((lease) => lease.status === 'active') ?? dashboard.leaseView.leases[0];
}