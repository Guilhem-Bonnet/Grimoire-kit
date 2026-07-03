import type {
  CanonicalEnvelopePilot,
  HostInvocationDecision,
  HostReviewVerdict
} from '../contracts/events';

import type { GameState } from './game-state';
import { countOpenHostFindings, createHostBridgeView, type HostBridgeHostCard } from './host-bridge-view';
import { createMissionPackByTaskId, type MissionPackSnapshot } from './mission-pack';
import { createSessionView, type SessionViewSession } from './session-view';

export const HOST_HANDOFF_STATUS_ORDER = ['blocked', 'review_pending', 'ready'] as const;

export type HostHandoffStatus = (typeof HOST_HANDOFF_STATUS_ORDER)[number];

export interface HostHandoffPacket {
  packetId: string;
  taskId: string;
  taskTitle: string | null;
  traceId: string | null;
  sessionTitle: string | null;
  lastUpdatedAt: string;
  lastSequenceId: number;
  agentIds: readonly string[];
  roomIds: readonly string[];
  missionPack: MissionPackSnapshot | null;
  hostIds: readonly string[];
  readyHostIds: readonly string[];
  reviewCapableHostIds: readonly string[];
  contextCapableHostIds: readonly string[];
  latestDecision: HostInvocationDecision | null;
  latestDecisionHostId: string | null;
  latestDecisionReason: string | null;
  latestReviewVerdict: HostReviewVerdict | null;
  reviewCount: number;
  openReviewFindingCount: number;
  linkedReviewIds: readonly string[];
  contextEntryCount: number;
  linkedContextEntryIds: readonly string[];
  canonicalEnvelopeCount: number;
  canonicalMessageTypes: readonly string[];
  canonicalEnvelopes: readonly CanonicalEnvelopePilot[];
  missingRequirements: readonly string[];
  readyForDispatch: boolean;
  status: HostHandoffStatus;
}

export interface HostHandoffViewSummary {
  packetCount: number;
  readyCount: number;
  reviewPendingCount: number;
  blockedCount: number;
  missionPackCount: number;
  missingMissionPackCount: number;
  missingCanonicalEnvelopeCount: number;
  openReviewFindingCount: number;
}

export interface HostHandoffView {
  protocolVersion: string;
  lastSequenceId: number;
  packets: readonly HostHandoffPacket[];
  summary: HostHandoffViewSummary;
}

export interface HostHandoffQuery {
  taskId?: string;
  hostId?: string;
  traceId?: string;
  status?: HostHandoffStatus;
  readyForDispatch?: boolean;
}

export interface HostHandoffQueryResult {
  packets: readonly HostHandoffPacket[];
  totalCount: number;
}

export function createHostHandoffView(state: GameState): HostHandoffView {
  const hostBridge = createHostBridgeView(state);
  const sessionView = createSessionView(state);
  const missionPackByTaskId = createMissionPackByTaskId(state);
  const latestTraceByTaskId = createLatestTraceByTaskId(state, hostBridge, sessionView.sessions);
  const taskIds = collectRelevantTaskIds(missionPackByTaskId, hostBridge, sessionView.sessions);
  const packets = taskIds
    .map((taskId) =>
      createHostHandoffPacket(taskId, state, missionPackByTaskId[taskId] ?? null, latestTraceByTaskId, hostBridge, sessionView.sessions)
    )
    .sort(compareHostHandoffPackets);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    packets,
    summary: {
      packetCount: packets.length,
      readyCount: packets.filter((packet) => packet.status === 'ready').length,
      reviewPendingCount: packets.filter((packet) => packet.status === 'review_pending').length,
      blockedCount: packets.filter((packet) => packet.status === 'blocked').length,
      missionPackCount: packets.filter((packet) => packet.missionPack !== null).length,
      missingMissionPackCount: packets.filter((packet) => packet.missionPack === null).length,
      missingCanonicalEnvelopeCount: packets.filter((packet) => packet.canonicalEnvelopeCount === 0).length,
      openReviewFindingCount: packets.reduce((count, packet) => count + packet.openReviewFindingCount, 0)
    }
  };
}

export function queryHostHandoffView(
  view: HostHandoffView,
  query: HostHandoffQuery = {}
): HostHandoffQueryResult {
  const packets = view.packets.filter((packet) => matchesHostHandoffPacket(packet, query));

  return {
    packets,
    totalCount: packets.length
  };
}

function createHostHandoffPacket(
  taskId: string,
  state: GameState,
  missionPack: MissionPackSnapshot | null,
  latestTraceByTaskId: Record<string, string>,
  hostBridge: ReturnType<typeof createHostBridgeView>,
  sessions: readonly SessionViewSession[]
): HostHandoffPacket {
  const traceId = latestTraceByTaskId[taskId] ?? null;
  const session = selectSessionForTask(taskId, traceId, sessions);
  const canonicalEnvelopes =
    session === null ? [] : filterSessionCanonicalEnvelopes(session, taskId, traceId);
  const invocations = hostBridge.invocations.filter((record) => matchesTaskOrTrace(taskId, traceId, record.envelope.taskId, record.envelope.traceId));
  const reviews = hostBridge.reviews.filter((record) =>
    matchesTaskOrTrace(taskId, traceId, record.review.taskId ?? record.meta.taskId, record.review.traceId ?? record.meta.traceId)
  );
  const contextEntries = hostBridge.contextEntries.filter((record) =>
    matchesTaskOrTrace(taskId, traceId, record.meta.taskId, record.meta.traceId)
  );
  const eligibleHosts = hostBridge.hosts.filter(isEligibleHandoffHost);
  const readyHosts = eligibleHosts.filter((host) => host.supportsContextImport || host.supportsReviewImport);
  const reviewCapableHosts = readyHosts.filter((host) => host.supportsReviewImport);
  const contextCapableHosts = readyHosts.filter((host) => host.supportsContextImport);
  const latestInvocation = invocations[0] ?? null;
  const latestReview = reviews[0] ?? null;
  const openReviewFindingCount = reviews.reduce((count, record) => count + countOpenHostFindings(record.review), 0);
  const missingRequirements = createMissingRequirements(missionPack, canonicalEnvelopes, readyHosts, latestInvocation?.decision ?? null);
  const status = deriveHostHandoffStatus(missingRequirements, openReviewFindingCount, latestInvocation?.decision ?? null);
  const taskTitle = state.tasks[taskId]?.title ?? null;
  const lastSequenceId = Math.max(
    session?.summary.lastSequenceId ?? -1,
    latestInvocation?.sequenceId ?? -1,
    latestReview?.sequenceId ?? -1,
    contextEntries[0]?.sequenceId ?? -1
  );
  const lastUpdatedAt = [
    session?.summary.updatedAt,
    latestInvocation?.timestamp,
    latestReview?.timestamp,
    contextEntries[0]?.timestamp,
    missionPack?.recordedAt,
    state.hydratedAt,
    new Date(0).toISOString()
  ].find((value): value is string => value !== null && value !== undefined) ?? new Date(0).toISOString();

  return {
    packetId: `host-handoff:${taskId}`,
    taskId,
    taskTitle,
    traceId,
    sessionTitle: session?.summary.title ?? null,
    lastUpdatedAt,
    lastSequenceId,
    agentIds: session?.summary.agentIds ?? [],
    roomIds: session?.summary.roomIds ?? [],
    missionPack,
    hostIds: sortStrings(eligibleHosts.map((host) => host.hostId)),
    readyHostIds: sortStrings(readyHosts.map((host) => host.hostId)),
    reviewCapableHostIds: sortStrings(reviewCapableHosts.map((host) => host.hostId)),
    contextCapableHostIds: sortStrings(contextCapableHosts.map((host) => host.hostId)),
    latestDecision: latestInvocation?.decision ?? null,
    latestDecisionHostId: latestInvocation?.envelope.hostId ?? null,
    latestDecisionReason: latestInvocation?.reason ?? null,
    latestReviewVerdict: latestReview?.review.verdict ?? null,
    reviewCount: reviews.length,
    openReviewFindingCount,
    linkedReviewIds: sortStrings(reviews.map((record) => record.review.reviewId)),
    contextEntryCount: contextEntries.length,
    linkedContextEntryIds: sortStrings(contextEntries.map((record) => record.entry.entryId)),
    canonicalEnvelopeCount: canonicalEnvelopes.length,
    canonicalMessageTypes: uniqueStrings(canonicalEnvelopes.map((envelope) => envelope.header.messageType)),
    canonicalEnvelopes,
    missingRequirements,
    readyForDispatch: status === 'ready',
    status
  };
}

function collectRelevantTaskIds(
  missionPackByTaskId: Record<string, MissionPackSnapshot>,
  hostBridge: ReturnType<typeof createHostBridgeView>,
  sessions: readonly SessionViewSession[]
): string[] {
  const taskIds = new Set<string>(Object.keys(missionPackByTaskId));

  for (const invocation of hostBridge.invocations) {
    if (invocation.envelope.taskId !== undefined) {
      taskIds.add(invocation.envelope.taskId);
    }
  }

  for (const review of hostBridge.reviews) {
    const taskId = review.review.taskId ?? review.meta.taskId;
    if (taskId !== undefined) {
      taskIds.add(taskId);
    }
  }

  for (const contextEntry of hostBridge.contextEntries) {
    const taskId = contextEntry.meta.taskId;
    if (taskId !== undefined) {
      taskIds.add(taskId);
    }
  }

  for (const session of sessions) {
    for (const taskId of session.summary.taskIds) {
      taskIds.add(taskId);
    }
  }

  return [...taskIds].sort((left, right) => left.localeCompare(right));
}

function createLatestTraceByTaskId(
  state: GameState,
  hostBridge: ReturnType<typeof createHostBridgeView>,
  sessions: readonly SessionViewSession[]
): Record<string, string> {
  const latestTraceByTaskId: Record<string, string> = {};

  for (const workflowStep of [...state.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    if (workflowStep.taskId !== undefined && workflowStep.traceId !== undefined && latestTraceByTaskId[workflowStep.taskId] === undefined) {
      latestTraceByTaskId[workflowStep.taskId] = workflowStep.traceId;
    }
  }

  for (const invocation of hostBridge.invocations) {
    if (
      invocation.envelope.taskId !== undefined &&
      invocation.envelope.traceId !== undefined &&
      latestTraceByTaskId[invocation.envelope.taskId] === undefined
    ) {
      latestTraceByTaskId[invocation.envelope.taskId] = invocation.envelope.traceId;
    }
  }

  for (const review of hostBridge.reviews) {
    const taskId = review.review.taskId ?? review.meta.taskId;
    const traceId = review.review.traceId ?? review.meta.traceId;
    if (taskId !== undefined && traceId !== undefined && latestTraceByTaskId[taskId] === undefined) {
      latestTraceByTaskId[taskId] = traceId;
    }
  }

  for (const contextEntry of hostBridge.contextEntries) {
    const taskId = contextEntry.meta.taskId;
    const traceId = contextEntry.meta.traceId;
    if (taskId !== undefined && traceId !== undefined && latestTraceByTaskId[taskId] === undefined) {
      latestTraceByTaskId[taskId] = traceId;
    }
  }

  for (const session of [...sessions].sort((left, right) => right.summary.lastSequenceId - left.summary.lastSequenceId)) {
    for (const taskId of session.summary.taskIds) {
      if (latestTraceByTaskId[taskId] === undefined) {
        latestTraceByTaskId[taskId] = session.summary.traceId;
      }
    }
  }

  return latestTraceByTaskId;
}

function selectSessionForTask(
  taskId: string,
  traceId: string | null,
  sessions: readonly SessionViewSession[]
): SessionViewSession | null {
  if (traceId !== null) {
    const matchedByTrace = sessions.find((session) => session.summary.traceId === traceId);
    if (matchedByTrace !== undefined) {
      return matchedByTrace;
    }
  }

  return sessions.find((session) => session.summary.taskIds.includes(taskId)) ?? null;
}

function filterSessionCanonicalEnvelopes(
  session: SessionViewSession,
  taskId: string,
  traceId: string | null
): CanonicalEnvelopePilot[] {
  return session.canonicalEnvelopes.filter((envelope) => {
    if (envelope.context.taskId === taskId) {
      return true;
    }

    if (envelope.context.taskId === undefined && traceId !== null && envelope.context.traceId === traceId) {
      return true;
    }

    return envelope.context.taskId === undefined && traceId === null && session.summary.taskIds.length === 1;
  });
}

function matchesTaskOrTrace(
  taskId: string,
  traceId: string | null,
  candidateTaskId: string | undefined,
  candidateTraceId: string | undefined
): boolean {
  if (candidateTaskId === taskId) {
    return true;
  }

  return traceId !== null && candidateTraceId === traceId;
}

function isEligibleHandoffHost(host: HostBridgeHostCard): boolean {
  return host.connectionState !== 'offline' && host.connectionState !== 'blocked' && host.trustStatus !== 'blocked';
}

function createMissingRequirements(
  missionPack: MissionPackSnapshot | null,
  canonicalEnvelopes: readonly CanonicalEnvelopePilot[],
  readyHosts: readonly HostBridgeHostCard[],
  latestDecision: HostInvocationDecision | null
): string[] {
  const requirements: string[] = [];

  if (missionPack === null) {
    requirements.push('mission_pack');
  } else if (missionPack.canonicalSourceRefs.length === 0) {
    requirements.push('canonical_sources');
  }

  if (canonicalEnvelopes.length === 0) {
    requirements.push('canonical_envelopes');
  }

  if (readyHosts.length === 0) {
    requirements.push('dispatchable_host');
  }

  if (latestDecision === 'DENY') {
    requirements.push('latest_decision_denied');
  }

  return requirements;
}

function deriveHostHandoffStatus(
  missingRequirements: readonly string[],
  openReviewFindingCount: number,
  latestDecision: HostInvocationDecision | null
): HostHandoffStatus {
  if (missingRequirements.length > 0) {
    return 'blocked';
  }

  if (openReviewFindingCount > 0 || latestDecision === 'PROMPT' || latestDecision === 'DEGRADE') {
    return 'review_pending';
  }

  return 'ready';
}

function matchesHostHandoffPacket(packet: HostHandoffPacket, query: HostHandoffQuery): boolean {
  if (query.taskId !== undefined && packet.taskId !== query.taskId) {
    return false;
  }

  if (query.hostId !== undefined && !packet.hostIds.includes(query.hostId)) {
    return false;
  }

  if (query.traceId !== undefined && packet.traceId !== query.traceId) {
    return false;
  }

  if (query.status !== undefined && packet.status !== query.status) {
    return false;
  }

  if (query.readyForDispatch !== undefined && packet.readyForDispatch !== query.readyForDispatch) {
    return false;
  }

  return true;
}

function compareHostHandoffPackets(left: HostHandoffPacket, right: HostHandoffPacket): number {
  if (left.lastSequenceId !== right.lastSequenceId) {
    return right.lastSequenceId - left.lastSequenceId;
  }

  return left.taskId.localeCompare(right.taskId);
}

function uniqueStrings(values: readonly string[]): string[] {
  return sortStrings([...new Set(values.filter((value) => value.length > 0))]);
}

function sortStrings(values: readonly string[]): string[] {
  return [...values].sort((left, right) => left.localeCompare(right));
}