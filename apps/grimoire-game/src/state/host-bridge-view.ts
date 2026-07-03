import type {
  CapabilityManifest,
  HostBinding,
  HostBindingRecord,
  HostConnectionState,
  HostContextLedgerRecord,
  HostInvocationDecision,
  HostInvocationDecisionRecord,
  HostReviewArtifactRecord,
  HostTrustStatus,
  ReviewArtifact
} from '../contracts/events';

import type { GameState } from './game-state';

export interface HostBridgeHostCard {
  hostId: string;
  hostType: HostBinding['hostType'];
  displayName: string;
  authMode: HostBinding['authMode'];
  connectionState: HostConnectionState;
  trustStatus: HostTrustStatus;
  scopes: readonly HostBinding['scopes'][number][];
  routines: readonly string[];
  toolProviders: readonly string[];
  reviewChannels: readonly string[];
  contextSources: readonly string[];
  permissionMode: CapabilityManifest['permissionMode'];
  supportsStreaming: boolean;
  supportsReviewImport: boolean;
  supportsContextImport: boolean;
  supportsPreviewCommit: boolean;
  routineCount: number;
  toolProviderCount: number;
  reviewChannelCount: number;
  contextSourceCount: number;
  manifestId: string;
  lastSeenAt: string | null;
  lastUpdatedAt: string;
  lastSequenceId: number;
  reason: string | null;
}

export interface HostBridgeViewMetrics {
  hostCount: number;
  onlineCount: number;
  staleCount: number;
  degradedCount: number;
  offlineCount: number;
  blockedCount: number;
  trustedCount: number;
  reviewCount: number;
  restrictedCount: number;
  blockedTrustCount: number;
  allowDecisionCount: number;
  promptDecisionCount: number;
  denyDecisionCount: number;
  degradeDecisionCount: number;
  reviewArtifactCount: number;
  contextEntryCount: number;
}

export interface HostBridgeView {
  protocolVersion: string;
  lastSequenceId: number;
  hosts: readonly HostBridgeHostCard[];
  invocations: readonly HostInvocationDecisionRecord[];
  reviews: readonly HostReviewArtifactRecord[];
  contextEntries: readonly HostContextLedgerRecord[];
  metrics: HostBridgeViewMetrics;
}

export function createHostBridgeView(state: GameState): HostBridgeView {
  const hosts = Object.values(state.hostBindings ?? {})
    .sort(compareHostBindingRecords)
    .map(createHostBridgeHostCard);
  const invocations = [...(state.recentHostInvocationDecisions ?? [])].sort(compareSequenceRecords);
  const reviews = [...(state.recentHostReviews ?? [])].sort(compareSequenceRecords);
  const contextEntries = [...(state.recentHostContextEntries ?? [])].sort(compareSequenceRecords);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    hosts,
    invocations,
    reviews,
    contextEntries,
    metrics: {
      hostCount: hosts.length,
      onlineCount: countHostsByConnectionState(hosts, 'online'),
      staleCount: countHostsByConnectionState(hosts, 'stale'),
      degradedCount: countHostsByConnectionState(hosts, 'degraded'),
      offlineCount: countHostsByConnectionState(hosts, 'offline'),
      blockedCount: countHostsByConnectionState(hosts, 'blocked'),
      trustedCount: countHostsByTrustStatus(hosts, 'trusted'),
      reviewCount: countHostsByTrustStatus(hosts, 'review'),
      restrictedCount: countHostsByTrustStatus(hosts, 'restricted'),
      blockedTrustCount: countHostsByTrustStatus(hosts, 'blocked'),
      allowDecisionCount: countInvocationsByDecision(invocations, 'ALLOW'),
      promptDecisionCount: countInvocationsByDecision(invocations, 'PROMPT'),
      denyDecisionCount: countInvocationsByDecision(invocations, 'DENY'),
      degradeDecisionCount: countInvocationsByDecision(invocations, 'DEGRADE'),
      reviewArtifactCount: reviews.length,
      contextEntryCount: contextEntries.length
    }
  };
}

function createHostBridgeHostCard(record: HostBindingRecord): HostBridgeHostCard {
  return {
    hostId: record.binding.hostId,
    hostType: record.binding.hostType,
    displayName: record.binding.displayName,
    authMode: record.binding.authMode,
    connectionState: record.binding.connectionState,
    trustStatus: record.binding.trustStatus,
    scopes: [...record.binding.scopes],
    routines: [...record.manifest.routines],
    toolProviders: [...record.manifest.toolProviders],
    reviewChannels: [...record.manifest.reviewChannels],
    contextSources: [...record.manifest.contextSources],
    permissionMode: record.manifest.permissionMode,
    supportsStreaming: record.manifest.supportsStreaming,
    supportsReviewImport: record.manifest.supportsReviewImport,
    supportsContextImport: record.manifest.supportsContextImport,
    supportsPreviewCommit: record.manifest.supportsPreviewCommit,
    routineCount: record.manifest.routines.length,
    toolProviderCount: record.manifest.toolProviders.length,
    reviewChannelCount: record.manifest.reviewChannels.length,
    contextSourceCount: record.manifest.contextSources.length,
    manifestId: record.manifest.manifestId,
    lastSeenAt: record.binding.lastSeenAt ?? null,
    lastUpdatedAt: record.timestamp,
    lastSequenceId: record.sequenceId,
    reason: record.reason ?? null
  };
}

function countHostsByConnectionState(hosts: readonly HostBridgeHostCard[], connectionState: HostConnectionState): number {
  return hosts.filter((host) => host.connectionState === connectionState).length;
}

function countHostsByTrustStatus(hosts: readonly HostBridgeHostCard[], trustStatus: HostTrustStatus): number {
  return hosts.filter((host) => host.trustStatus === trustStatus).length;
}

function countInvocationsByDecision(
  invocations: readonly HostInvocationDecisionRecord[],
  decision: HostInvocationDecision
): number {
  return invocations.filter((invocation) => invocation.decision === decision).length;
}

function compareHostBindingRecords(left: HostBindingRecord, right: HostBindingRecord): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return left.binding.hostId.localeCompare(right.binding.hostId);
}

function compareSequenceRecords(
  left: HostInvocationDecisionRecord | HostReviewArtifactRecord | HostContextLedgerRecord,
  right: HostInvocationDecisionRecord | HostReviewArtifactRecord | HostContextLedgerRecord
): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return right.timestamp.localeCompare(left.timestamp);
}

export function countOpenHostFindings(review: ReviewArtifact): number {
  return review.findings.filter((finding) => finding.resolutionStatus !== 'resolved').length;
}