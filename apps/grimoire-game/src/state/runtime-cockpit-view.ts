import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiTone,
  type RuntimeDashboardUiView,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import { countOpenHostFindings } from './host-bridge-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface RuntimeCockpitHeader {
  projectId: string | null;
  runId: string | null;
  registryVersion: string | null;
  tone: RuntimeDashboardUiTone;
  summary: string;
}

export interface RuntimeCockpitProofItem {
  id: string;
  source: 'attention' | 'verification' | 'evidence' | 'external_review';
  tone: RuntimeDashboardUiTone;
  taskId: string | null;
  traceId: string | null;
  nodeId: string | null;
  hostId: string | null;
  verificationRef: string | null;
  detail: string;
}

export interface RuntimeCockpitHost {
  hostId: string;
  displayName: string;
  hostType: RuntimeDashboardView['hostBridge']['hosts'][number]['hostType'];
  connectionState: RuntimeDashboardView['hostBridge']['hosts'][number]['connectionState'];
  trustStatus: RuntimeDashboardView['hostBridge']['hosts'][number]['trustStatus'];
  tone: RuntimeDashboardUiTone;
  routines: readonly string[];
  toolProviders: readonly string[];
  reviewChannels: readonly string[];
  contextSources: readonly string[];
  reviewArtifactCount: number;
  openReviewFindingCount: number;
}

export interface RuntimeCockpitView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeCockpitHeader;
  focus: RuntimeDashboardUiView['focus'];
  fleet: RuntimeDashboardUiView['fleet'];
  hosts: readonly RuntimeCockpitHost[];
  ownership: RuntimeDashboardUiView['ownership'];
  proofs: readonly RuntimeCockpitProofItem[];
  ui: RuntimeDashboardUiView;
}

export function createRuntimeCockpitView(
  dashboard: RuntimeDashboardView,
  options: RuntimeDashboardUiViewOptions = {}
): RuntimeCockpitView {
  const ui = createRuntimeDashboardUiView(dashboard, options);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      projectId: dashboard.projectRegistry?.activeProject.projectId ?? dashboard.nodeFleet.summary.projectId ?? null,
      runId: ui.focus.runId,
      registryVersion: dashboard.projectRegistry?.registryVersion ?? null,
      tone: ui.header.tone,
      summary: `${dashboard.summary.liveNodeCount}/${dashboard.summary.nodeCount} live node(s), ${dashboard.summary.hostCount} host(s), ${dashboard.summary.activeLeaseCount} active lease(s), ${dashboard.summary.verificationQueueCount} verification item(s).`
    },
    focus: ui.focus,
    fleet: ui.fleet,
    hosts: createRuntimeCockpitHosts(dashboard, ui),
    ownership: ui.ownership,
    proofs: createRuntimeCockpitProofs(dashboard, ui),
    ui
  };
}

function createRuntimeCockpitHosts(
  dashboard: RuntimeDashboardView,
  ui: RuntimeDashboardUiView
): RuntimeCockpitHost[] {
  const toneByHostId = Object.fromEntries(ui.hosts.map((host) => [host.hostId, host.tone]));

  return dashboard.hostBridge.hosts.map((host) => {
    const relatedReviews = dashboard.hostBridge.reviews.filter((review) => review.review.hostId === host.hostId);

    return {
      hostId: host.hostId,
      displayName: host.displayName,
      hostType: host.hostType,
      connectionState: host.connectionState,
      trustStatus: host.trustStatus,
      tone: toneByHostId[host.hostId] ?? 'neutral',
      routines: [...host.routines],
      toolProviders: [...host.toolProviders],
      reviewChannels: [...host.reviewChannels],
      contextSources: [...host.contextSources],
      reviewArtifactCount: relatedReviews.length,
      openReviewFindingCount: relatedReviews.reduce((count, review) => count + countOpenHostFindings(review.review), 0)
    };
  });
}

function createRuntimeCockpitProofs(
  dashboard: RuntimeDashboardView,
  ui: RuntimeDashboardUiView
): RuntimeCockpitProofItem[] {
  const ownershipByTaskId = new Map(ui.ownership.map((entry) => [entry.taskId, entry.nodeId]));

  const attentionProofs: RuntimeCockpitProofItem[] = ui.attention.slice(0, 3).map((item) => ({
    id: `attention:${item.id}`,
    source: 'attention' as const,
    tone: item.tone,
    taskId: item.taskId,
    traceId: item.traceId,
    nodeId: item.taskId === null ? null : (ownershipByTaskId.get(item.taskId) ?? null),
    hostId: null,
    verificationRef: null,
    detail: item.detail
  }));

  const verificationProofs: RuntimeCockpitProofItem[] = dashboard.verificationQueue.items.slice(0, 3).map((item) => ({
    id: `verification:${item.queueId}`,
    source: 'verification' as const,
    tone:
      item.queueStatus === 'rejected'
        ? 'critical'
        : item.queueStatus === 'needs_work'
          ? 'warning'
          : item.queueStatus === 'accepted'
            ? 'positive'
            : 'neutral',
    taskId: item.taskId,
    traceId: null,
    nodeId: ownershipByTaskId.get(item.taskId) ?? null,
              hostId: null,
    verificationRef: item.verificationRef,
    detail: `${item.unmetRequirementCodes.length} unmet requirement(s); verdict ${item.verdict ?? 'pending'}.`
  }));

  const evidenceProofs: RuntimeCockpitProofItem[] = ui.evidencePacks.slice(0, 2).map((pack) => ({
    id: `evidence:${pack.id}`,
    source: 'evidence' as const,
    tone: pack.tone,
    taskId: pack.taskRef,
    traceId: null,
    nodeId: pack.taskRef === null ? null : (ownershipByTaskId.get(pack.taskRef) ?? null),
    hostId: null,
    verificationRef: pack.verificationRef,
    detail: pack.detail
  }));

  const externalReviewProofs: RuntimeCockpitProofItem[] = ui.evidencePacks
    .flatMap((pack) =>
      pack.externalReviews.map((review) => ({
        id: `external-review:${review.reviewId}`,
        source: 'external_review' as const,
        tone: review.tone,
        taskId: review.taskId ?? pack.taskRef,
        traceId: review.traceId,
        nodeId:
          (review.taskId ?? pack.taskRef) === null ? null : (ownershipByTaskId.get(review.taskId ?? pack.taskRef ?? '') ?? null),
        hostId: review.hostId,
        verificationRef: pack.verificationRef,
        detail: review.detail
      }))
    )
    .slice(0, 3);

  return [...attentionProofs, ...verificationProofs, ...evidenceProofs, ...externalReviewProofs];
}