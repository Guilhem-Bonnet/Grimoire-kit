import type {
  CanonicalEnvelopePilot,
  GameStateSnapshot,
  LeaseStoreSnapshot,
  NodeRegistrySnapshot,
  ProjectRegistrySnapshot,
  ServerEvent
} from '../contracts/events';

import {
  createBranchFinisherView,
  createSecurityAuditView,
  type BranchFinisherView,
  type SecurityAuditView
} from './branch-finisher-view';
import { createBoardView, type BoardView } from './board-view';
import {
  createConfigurationSkillTreeView,
  type ConfigurationSkillTreeView
} from './configuration-skill-tree-view';
import { applyServerEvents, createEmptyGameState, hydrateGameState, type GameState } from './game-state';
import {
  createHostHandoffView,
  type HostHandoffView
} from './host-handoff-view';
import {
  createHostBridgeView,
  type HostBridgeView
} from './host-bridge-view';
import {
  createLeaseView,
  type LeaseView
} from './lease-view';
import {
  createLibraryView,
  type LibraryView
} from './library-view';
import {
  createMissionLedgerView,
  type MissionLedgerView
} from './mission-ledger-view';
import {
  createNodeFleetView,
  type NodeFleetView
} from './node-fleet-view';
import {
  createObservabilityPanelView,
  type ObservabilityAttentionSeverity,
  type ObservabilityPanelOptions,
  type ObservabilityPanelView
} from './observability-panel-view';
import {
  createSessionLineageView,
  type SessionLineageView
} from './session-lineage-view';
import { createSessionDiff, createSessionView, type SessionDiff, type SessionView } from './session-view';
import {
  createSupervisionView,
  type SupervisionView
} from './supervision-view';
import { createTaskView, type TaskView } from './task-view';
import {
  createVerificationEvidencePackView,
  type VerificationEvidencePackView
} from './verification-evidence-pack-view';
import {
  createVerificationQueueView,
  type VerificationQueueView
} from './verification-queue-view';
import { createVerificationView, type VerificationView } from './verification-view';

export interface RuntimeDashboardViewOptions {
  observability?: ObservabilityPanelOptions;
}

export interface RuntimeDashboardControlPlaneState {
  projectRegistry: ProjectRegistrySnapshot | null;
  nodeRegistry: NodeRegistrySnapshot | null;
  leaseStore: LeaseStoreSnapshot | null;
}

export interface RuntimeDashboardSummary {
  boardAlertCount: number;
  blockedTaskCount: number;
  activeTaskCount: number;
  workingAgentCount: number;
  missionCount: number;
  blockedMissionCount: number;
  verificationCount: number;
  verificationQueueCount: number;
  verificationQueuedCount: number;
  verificationVerifyingCount: number;
  verificationAcceptedCount: number;
  verificationRejectedCount: number;
  verificationNeedsWorkCount: number;
  verificationEvidencePackCount: number;
  verificationAttestationCount: number;
  missionPackLinkedCount: number;
  missionPackCoveredCount: number;
  missingExpectedProofCount: number;
  lineageEdgeCount: number;
  staleLineageAlertCount: number;
  canonicalEnvelopeCount: number;
  securityCardCount: number;
  securityBlockingFindingCount: number;
  shipBlocked: boolean;
  criticalAttentionCount: number;
  warningAttentionCount: number;
  infoAttentionCount: number;
  totalAttentionCount: number;
  hostCount: number;
  degradedHostCount: number;
  deniedHostDecisionCount: number;
  promptedHostDecisionCount: number;
  importedHostReviewCount: number;
  importedHostContextCount: number;
  hostHandoffPacketCount: number;
  readyHostHandoffCount: number;
  reviewPendingHostHandoffCount: number;
  blockedHostHandoffCount: number;
  libraryContextCount: number;
  libraryStaleContextCount: number;
  libraryOpenReviewFindingCount: number;
  leaseCount: number;
  activeLeaseCount: number;
  expiredLeaseCount: number;
  leaseAlertCount: number;
  nodeCount: number;
  liveNodeCount: number;
  staleNodeCount: number;
  offlineNodeCount: number;
  nodeWorkerCount: number;
  nodeAlertCount: number;
  releaseBlocked: boolean;
}

export interface RuntimeDashboardView {
  protocolVersion: string;
  lastSequenceId: number;
  board: BoardView;
  branchFinisher: BranchFinisherView;
  configurationSkillTree: ConfigurationSkillTreeView;
  hostHandoffs: HostHandoffView;
  hostBridge: HostBridgeView;
  leaseView: LeaseView;
  library: LibraryView;
  missionLedger: MissionLedgerView;
  nodeFleet: NodeFleetView;
  projectRegistry: ProjectRegistrySnapshot | null;
  securityAudit: SecurityAuditView;
  observability: ObservabilityPanelView;
  session: SessionView;
  sessionLineage: SessionLineageView;
  supervision: SupervisionView;
  canonicalEnvelopes: readonly CanonicalEnvelopePilot[];
  tasks: TaskView;
  verification: VerificationView;
  verificationEvidencePacks: VerificationEvidencePackView;
  verificationQueue: VerificationQueueView;
  sessionDiff: SessionDiff | null;
  summary: RuntimeDashboardSummary;
}

export function createRuntimeDashboardView(
  state: GameState,
  options: RuntimeDashboardViewOptions = {},
  controlPlane: RuntimeDashboardControlPlaneState = {
    projectRegistry: null,
    nodeRegistry: null,
    leaseStore: null
  }
): RuntimeDashboardView {
  const board = createBoardView(state);
  const securityAudit = createSecurityAuditView(state);
  const branchFinisher = createBranchFinisherView(state);
  const configurationSkillTree = createConfigurationSkillTreeView(state);
  const hostHandoffs = createHostHandoffView(state);
  const hostBridge = createHostBridgeView(state);
  const leaseView = createLeaseView(controlPlane.leaseStore, Object.values(state.tasks));
  const library = createLibraryView(state);
  const missionLedger = createMissionLedgerView(state);
  const nodeFleet = createNodeFleetView(controlPlane.nodeRegistry);
  const observability = createObservabilityPanelView(state, options.observability ?? {});
  const session = createSessionView(state);
  const sessionLineage = createSessionLineageView(state);
  const supervision = createSupervisionView(state);
  const tasks = createTaskView(state);
  const verification = createVerificationView(state);
  const verificationEvidencePacks = createVerificationEvidencePackView(state);
  const verificationQueue = createVerificationQueueView(state);
  const canonicalEnvelopes = session.sessions.flatMap((record) => record.canonicalEnvelopes);
  const sessionDiff = createLatestSessionDiff(state, session);
  const criticalAttentionCount = countAttentionBySeverity(observability, 'critical');
  const warningAttentionCount = countAttentionBySeverity(observability, 'warning');
  const infoAttentionCount = countAttentionBySeverity(observability, 'info');

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    board,
    branchFinisher,
    configurationSkillTree,
    hostHandoffs,
    hostBridge,
    leaseView,
    library,
    missionLedger,
    nodeFleet,
    projectRegistry: controlPlane.projectRegistry,
    securityAudit,
    observability,
    session,
    sessionLineage,
    supervision,
    canonicalEnvelopes,
    tasks,
    verification,
    verificationEvidencePacks,
    verificationQueue,
    sessionDiff,
    summary: {
      boardAlertCount: board.alerts.length,
      blockedTaskCount: observability.source.summary.blockedTaskCount,
      activeTaskCount: board.metrics.activeTaskCount,
      workingAgentCount: board.metrics.workingAgentCount,
      missionCount: missionLedger.summary.missionCount,
      blockedMissionCount: missionLedger.summary.blockedMissionCount,
      verificationCount: missionLedger.summary.verificationCount,
      verificationQueueCount: verificationQueue.metrics.itemCount,
      verificationQueuedCount: verificationQueue.metrics.queuedCount,
      verificationVerifyingCount: verificationQueue.metrics.verifyingCount,
      verificationAcceptedCount: verificationQueue.metrics.acceptedCount,
      verificationRejectedCount: verificationQueue.metrics.rejectedCount,
      verificationNeedsWorkCount: verificationQueue.metrics.needsWorkCount,
      verificationEvidencePackCount: verificationEvidencePacks.summary.packCount,
      verificationAttestationCount: verificationEvidencePacks.summary.attestedCount,
      missionPackLinkedCount: verificationEvidencePacks.summary.missionPackLinkedCount,
      missionPackCoveredCount: verificationEvidencePacks.summary.missionPackCoveredCount,
      missingExpectedProofCount: verificationEvidencePacks.summary.missingExpectedProofCount,
      lineageEdgeCount: sessionLineage.metrics.edgeCount,
      staleLineageAlertCount: sessionLineage.metrics.staleAlertCount,
      canonicalEnvelopeCount: canonicalEnvelopes.length,
      securityCardCount: board.metrics.securityCardCount,
      securityBlockingFindingCount: securityAudit.metrics.blockingFindingCount,
      shipBlocked: branchFinisher.shipBlocked,
      criticalAttentionCount,
      warningAttentionCount,
      infoAttentionCount,
      totalAttentionCount: observability.attentionItems.length,
      hostCount: hostBridge.metrics.hostCount,
      degradedHostCount: hostBridge.metrics.degradedCount + hostBridge.metrics.staleCount,
      deniedHostDecisionCount: hostBridge.metrics.denyDecisionCount,
      promptedHostDecisionCount: hostBridge.metrics.promptDecisionCount,
      importedHostReviewCount: hostBridge.metrics.reviewArtifactCount,
      importedHostContextCount: hostBridge.metrics.contextEntryCount,
      hostHandoffPacketCount: hostHandoffs.summary.packetCount,
      readyHostHandoffCount: hostHandoffs.summary.readyCount,
      reviewPendingHostHandoffCount: hostHandoffs.summary.reviewPendingCount,
      blockedHostHandoffCount: hostHandoffs.summary.blockedCount,
      libraryContextCount: library.summary.contextEntryCount,
      libraryStaleContextCount: library.summary.staleContextCount,
      libraryOpenReviewFindingCount: library.summary.openReviewFindingCount,
      leaseCount: leaseView.summary.leaseCount,
      activeLeaseCount: leaseView.summary.activeLeaseCount,
      expiredLeaseCount: leaseView.summary.expiredLeaseCount,
      leaseAlertCount: leaseView.summary.alertCount,
      nodeCount: nodeFleet.summary.nodeCount,
      liveNodeCount: nodeFleet.summary.liveNodeCount,
      staleNodeCount: nodeFleet.summary.staleNodeCount,
      offlineNodeCount: nodeFleet.summary.offlineNodeCount,
      nodeWorkerCount: nodeFleet.summary.workerCount,
      nodeAlertCount: nodeFleet.summary.alertCount,
      releaseBlocked: supervision.summary.releaseBlocked
    }
  };
}

export function createRuntimeDashboardViewFromSnapshot(
  snapshot: GameStateSnapshot,
  options: RuntimeDashboardViewOptions = {},
  hydratedAt: string | null = null,
  controlPlane: RuntimeDashboardControlPlaneState = {
    projectRegistry: null,
    nodeRegistry: null,
    leaseStore: null
  }
): RuntimeDashboardView {
  const state = hydrateGameState(snapshot, hydratedAt);
  return createRuntimeDashboardView(state, options, controlPlane);
}

export function createRuntimeDashboardViewFromEvents(
  events: readonly ServerEvent[],
  options: RuntimeDashboardViewOptions = {},
  initialState: GameState = createEmptyGameState(),
  controlPlane: RuntimeDashboardControlPlaneState = {
    projectRegistry: null,
    nodeRegistry: null,
    leaseStore: null
  }
): RuntimeDashboardView {
  const orderedEvents = [...events].sort((left, right) => left.sequenceId - right.sequenceId);
  const state = applyServerEvents(initialState, orderedEvents);
  return createRuntimeDashboardView(state, options, controlPlane);
}

function countAttentionBySeverity(
  panel: ObservabilityPanelView,
  severity: ObservabilityAttentionSeverity
): number {
  return panel.attentionItems.filter((item) => item.severity === severity).length;
}

function createLatestSessionDiff(state: GameState, sessionView: SessionView): SessionDiff | null {
  if (sessionView.sessions.length < 2) {
    return null;
  }

  const ordered = [...sessionView.sessions].sort(
    (left, right) => right.summary.lastSequenceId - left.summary.lastSequenceId
  );
  const left = ordered[0];
  const right = ordered[1];

  if (left === undefined || right === undefined) {
    return null;
  }

  return createSessionDiff(state, left.summary.traceId, right.summary.traceId);
}