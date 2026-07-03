import type { TaskStatus } from '../contracts/events';

import {
  createBranchFinisherView,
  type BranchFinisherView
} from './branch-finisher-view';
import {
  createMissionLedgerView,
  MISSION_LEDGER_MISSION_STATUS_ORDER,
  type MissionLedgerMissionStatus,
  type MissionLedgerPriority
} from './mission-ledger-view';
import type { GameState } from './game-state';
import {
  createSessionLineageView,
  type SessionLineageAlertCode,
  type SessionLineageAlertSeverity
} from './session-lineage-view';
import {
  createVerificationQueueView,
  VERIFICATION_QUEUE_STATUS_ORDER,
  type VerificationQueueStatus
} from './verification-queue-view';
import type { VerificationVerdict } from './verification-view';

export interface SupervisionMissionCard {
  missionId: string;
  title: string;
  status: MissionLedgerMissionStatus;
  priority: MissionLedgerPriority;
  owner: string;
  updatedAt: string;
  blockedItemCount: number;
  verifyingItemCount: number;
  openEscalationCount: number;
  traceCount: number;
}

export interface SupervisionMissionLane {
  status: MissionLedgerMissionStatus;
  count: number;
  missions: readonly SupervisionMissionCard[];
}

export interface SupervisionVerificationCard {
  taskId: string;
  title: string;
  taskStatus: TaskStatus;
  queueStatus: VerificationQueueStatus;
  assigneeName: string | null;
  verificationRef: string | null;
  verdict: VerificationVerdict | null;
  unmetRequirementCount: number;
  evidenceCount: number;
}

export interface SupervisionVerificationLane {
  status: VerificationQueueStatus;
  count: number;
  items: readonly SupervisionVerificationCard[];
}

export interface SupervisionLineageAlertBucket {
  code: SessionLineageAlertCode;
  severity: SessionLineageAlertSeverity;
  count: number;
  traceIds: readonly string[];
}

export interface SupervisionReleaseGate {
  shipBlocked: boolean;
  releaseBlocked: boolean;
  blockedMissionCount: number;
  staleLineageAlertCount: number;
  securityBlockingCount: number;
  verificationBlockingCount: number;
  blockingTaskIds: readonly string[];
  blockingReasons: readonly string[];
}

export interface SupervisionViewSummary {
  missionCount: number;
  blockedMissionCount: number;
  verificationQueueCount: number;
  lineageAlertCount: number;
  releaseBlocked: boolean;
}

export interface SupervisionView {
  protocolVersion: string;
  lastSequenceId: number;
  missionLanes: readonly SupervisionMissionLane[];
  verificationLanes: readonly SupervisionVerificationLane[];
  lineageAlerts: readonly SupervisionLineageAlertBucket[];
  releaseGate: SupervisionReleaseGate;
  summary: SupervisionViewSummary;
}

const LINEAGE_ALERT_CODE_RANK: Record<SessionLineageAlertCode, number> = {
  MISSING_LINEAGE: 0,
  MISSING_EVIDENCE: 1,
  MISSING_RUN_ID: 2
};

export function createSupervisionView(state: GameState): SupervisionView {
  const missionLedger = createMissionLedgerView(state);
  const verificationQueue = createVerificationQueueView(state);
  const sessionLineage = createSessionLineageView(state);
  const branchFinisher = createBranchFinisherView(state);
  const escalationCountByMissionId = createMissionEscalationCountIndex(missionLedger);

  const missionLanes = MISSION_LEDGER_MISSION_STATUS_ORDER.map((status) => {
    const missions = missionLedger.missions
      .filter((mission) => mission.status === status)
      .map((mission) => ({
        missionId: mission.missionId,
        title: mission.title,
        status: mission.status,
        priority: mission.priority,
        owner: mission.owner,
        updatedAt: mission.updatedAt,
        blockedItemCount: mission.blockedItemIds.length,
        verifyingItemCount: mission.verifyingItemIds.length,
        openEscalationCount: escalationCountByMissionId[mission.missionId] ?? 0,
        traceCount: mission.traceRefs.length
      }))
      .sort(compareSupervisionMissionCards);

    return {
      status,
      count: missions.length,
      missions
    };
  });

  const verificationLanes = VERIFICATION_QUEUE_STATUS_ORDER.map((status) => {
    const items = verificationQueue.items
      .filter((item) => item.queueStatus === status)
      .map((item) => ({
        taskId: item.taskId,
        title: item.taskTitle,
        taskStatus: item.taskStatus,
        queueStatus: item.queueStatus,
        assigneeName: item.assigneeAgentName,
        verificationRef: item.verificationRef,
        verdict: item.verdict,
        unmetRequirementCount: item.unmetRequirementCodes.length,
        evidenceCount: item.evidenceCount
      }))
      .sort(compareSupervisionVerificationCards);

    return {
      status,
      count: items.length,
      items
    };
  });

  const lineageAlerts = createSupervisionLineageBuckets(sessionLineage);
  const releaseGate = createSupervisionReleaseGate(branchFinisher, missionLedger.summary.blockedMissionCount, sessionLineage);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    missionLanes,
    verificationLanes,
    lineageAlerts,
    releaseGate,
    summary: {
      missionCount: missionLedger.summary.missionCount,
      blockedMissionCount: missionLedger.summary.blockedMissionCount,
      verificationQueueCount: verificationQueue.metrics.itemCount,
      lineageAlertCount: sessionLineage.alerts.length,
      releaseBlocked: releaseGate.releaseBlocked
    }
  };
}

function createMissionEscalationCountIndex(
  missionLedger: ReturnType<typeof createMissionLedgerView>
): Record<string, number> {
  const counts: Record<string, number> = {};

  for (const mission of missionLedger.missions) {
    counts[mission.missionId] = missionLedger.escalationRecords.filter(
      (record) => record.status === 'open' && mission.itemIds.includes(record.itemId)
    ).length;
  }

  return counts;
}

function createSupervisionLineageBuckets(
  lineage: ReturnType<typeof createSessionLineageView>
): SupervisionLineageAlertBucket[] {
  const buckets = new Map<SessionLineageAlertCode, SupervisionLineageAlertBucket>();

  for (const alert of lineage.alerts) {
    const current = buckets.get(alert.code) ?? {
      code: alert.code,
      severity: alert.severity,
      count: 0,
      traceIds: []
    };

    buckets.set(alert.code, {
      code: current.code,
      severity: current.severity,
      count: current.count + 1,
      traceIds: uniqueStrings([...current.traceIds, alert.traceId])
    });
  }

  return [...buckets.values()].sort(compareSupervisionLineageBuckets);
}

function createSupervisionReleaseGate(
  branchFinisher: BranchFinisherView,
  blockedMissionCount: number,
  lineage: ReturnType<typeof createSessionLineageView>
): SupervisionReleaseGate {
  const securityBlockingCount = branchFinisher.securityCards.filter((card) => card.blocksShip).length;
  const blockingReasons = uniqueStrings([
    ...branchFinisher.blockingReasons,
    ...branchFinisher.verification.blockingReasons,
    ...lineage.alerts.map((alert) => alert.message),
    ...(blockedMissionCount > 0 ? ['One or more missions are blocked in the mission ledger.'] : [])
  ]);
  const releaseBlocked =
    branchFinisher.shipBlocked ||
    branchFinisher.verification.blockingItemCount > 0 ||
    blockedMissionCount > 0 ||
    lineage.metrics.staleAlertCount > 0;

  return {
    shipBlocked: branchFinisher.shipBlocked,
    releaseBlocked,
    blockedMissionCount,
    staleLineageAlertCount: lineage.metrics.staleAlertCount,
    securityBlockingCount,
    verificationBlockingCount: branchFinisher.verification.blockingItemCount,
    blockingTaskIds: branchFinisher.verification.blockingTaskIds,
    blockingReasons
  };
}

function compareSupervisionMissionCards(left: SupervisionMissionCard, right: SupervisionMissionCard): number {
  const priorityRank = { high: 0, medium: 1, low: 2 } as const;
  if (left.priority !== right.priority) {
    return priorityRank[left.priority] - priorityRank[right.priority];
  }

  if (left.openEscalationCount !== right.openEscalationCount) {
    return right.openEscalationCount - left.openEscalationCount;
  }

  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.title.localeCompare(right.title);
}

function compareSupervisionVerificationCards(left: SupervisionVerificationCard, right: SupervisionVerificationCard): number {
  if (left.unmetRequirementCount !== right.unmetRequirementCount) {
    return right.unmetRequirementCount - left.unmetRequirementCount;
  }

  if (left.evidenceCount !== right.evidenceCount) {
    return right.evidenceCount - left.evidenceCount;
  }

  return left.title.localeCompare(right.title);
}

function compareSupervisionLineageBuckets(
  left: SupervisionLineageAlertBucket,
  right: SupervisionLineageAlertBucket
): number {
  if (left.severity !== right.severity) {
    return left.severity === 'warning' ? -1 : 1;
  }

  if (left.count !== right.count) {
    return right.count - left.count;
  }

  return LINEAGE_ALERT_CODE_RANK[left.code] - LINEAGE_ALERT_CODE_RANK[right.code];
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}