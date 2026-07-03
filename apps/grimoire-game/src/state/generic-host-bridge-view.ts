import type {
  HostContextLedgerRecord,
  HostInvocationDecision,
  HostReviewArtifactRecord,
  HostReviewVerdict
} from '../contracts/events';

import type { HostHandoffPacket, HostHandoffStatus } from './host-handoff-view';
import type { HostBridgeHostCard } from './host-bridge-view';
import type { RuntimeDashboardUiTone } from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export const GENERIC_HOST_BRIDGE_CHANNELS = ['browser', 'vscode', 'external'] as const;

export type GenericHostBridgeChannelId = (typeof GENERIC_HOST_BRIDGE_CHANNELS)[number];

export interface GenericHostBridgeHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
}

export interface GenericHostBridgeFocus {
  projectId: string | null;
  runId: string | null;
  taskId: string | null;
  traceId: string | null;
}

export interface GenericHostBridgeSummary {
  hostCount: number;
  readyChannelCount: number;
  degradedChannelCount: number;
  blockedChannelCount: number;
  readyPacketCount: number;
  reviewPendingPacketCount: number;
  blockedPacketCount: number;
  importedReviewCount: number;
  importedContextCount: number;
  deniedDecisionCount: number;
  promptedDecisionCount: number;
}

export interface GenericHostBridgeChannelCard {
  channelId: GenericHostBridgeChannelId;
  label: string;
  status: 'ready' | 'degraded' | 'blocked';
  tone: RuntimeDashboardUiTone;
  detail: string;
  hostCount: number;
  packetCount: number;
}

export interface GenericHostBridgeDispatchHost {
  hostId: string;
  hostType: HostBridgeHostCard['hostType'];
  displayName: string;
  connectionState: HostBridgeHostCard['connectionState'];
  trustStatus: HostBridgeHostCard['trustStatus'];
  permissionMode: HostBridgeHostCard['permissionMode'];
  tone: RuntimeDashboardUiTone;
  routines: readonly string[];
  toolProviders: readonly string[];
  reviewChannels: readonly string[];
  contextSources: readonly string[];
  packetCount: number;
  readyPacketCount: number;
  reviewPendingPacketCount: number;
  blockedPacketCount: number;
  latestDecision: HostInvocationDecision | null;
  latestReviewVerdict: HostReviewVerdict | null;
  openReviewFindingCount: number;
  latestContextEntryId: string | null;
  reason: string | null;
}

export interface GenericHostBridgePacketCard {
  packetId: string;
  taskId: string;
  taskTitle: string | null;
  traceId: string | null;
  status: HostHandoffStatus;
  tone: RuntimeDashboardUiTone;
  readyForDispatch: boolean;
  hostIds: readonly string[];
  readyHostIds: readonly string[];
  latestDecision: HostInvocationDecision | null;
  latestReviewVerdict: HostReviewVerdict | null;
  openReviewFindingCount: number;
  missingRequirements: readonly string[];
}

export interface GenericHostBridgeView {
  protocolVersion: string;
  lastSequenceId: number;
  header: GenericHostBridgeHeader;
  focus: GenericHostBridgeFocus;
  summary: GenericHostBridgeSummary;
  channels: readonly GenericHostBridgeChannelCard[];
  dispatchHosts: readonly GenericHostBridgeDispatchHost[];
  packets: readonly GenericHostBridgePacketCard[];
  recentInvocations: readonly RuntimeDashboardView['hostBridge']['invocations'][number][];
  recentReviews: readonly HostReviewArtifactRecord[];
  recentContextEntries: readonly HostContextLedgerRecord[];
}

export function createGenericHostBridgeView(dashboard: RuntimeDashboardView): GenericHostBridgeView {
  const channels = createChannelCards(dashboard);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Host Bridge generique',
      subtitle:
        'Le meme run reste lisible depuis le navigateur, le panel VS Code et les hôtes externes via les primitives Host Binding, Review Artifact et Context Ledger.',
      tone: deriveHeaderTone(dashboard)
    },
    focus: {
      projectId: dashboard.projectRegistry?.activeProject?.projectId ?? null,
      runId: dashboard.projectRegistry?.activeProject?.runId ?? null,
      taskId: dashboard.projectRegistry?.activeProject?.taskId ?? dashboard.observability.focus.taskId ?? null,
      traceId: dashboard.projectRegistry?.activeProject?.traceId ?? dashboard.observability.focus.traceId ?? null
    },
    summary: {
      hostCount: dashboard.summary.hostCount,
      readyChannelCount: channels.filter((channel) => channel.status === 'ready').length,
      degradedChannelCount: channels.filter((channel) => channel.status === 'degraded').length,
      blockedChannelCount: channels.filter((channel) => channel.status === 'blocked').length,
      readyPacketCount: dashboard.summary.readyHostHandoffCount,
      reviewPendingPacketCount: dashboard.summary.reviewPendingHostHandoffCount,
      blockedPacketCount: dashboard.summary.blockedHostHandoffCount,
      importedReviewCount: dashboard.summary.importedHostReviewCount,
      importedContextCount: dashboard.summary.importedHostContextCount,
      deniedDecisionCount: dashboard.summary.deniedHostDecisionCount,
      promptedDecisionCount: dashboard.summary.promptedHostDecisionCount
    },
    channels,
    dispatchHosts: dashboard.hostBridge.hosts.map((host) =>
      createDispatchHost(host, dashboard.hostHandoffs.packets, dashboard.hostBridge.reviews, dashboard.hostBridge.contextEntries, dashboard.hostBridge.invocations)
    ),
    packets: dashboard.hostHandoffs.packets.map(createPacketCard),
    recentInvocations: dashboard.hostBridge.invocations.slice(0, 6),
    recentReviews: dashboard.hostBridge.reviews.slice(0, 6),
    recentContextEntries: dashboard.hostBridge.contextEntries.slice(0, 6)
  };
}

function createChannelCards(dashboard: RuntimeDashboardView): GenericHostBridgeChannelCard[] {
  const ideHosts = dashboard.hostBridge.hosts.filter((host) => host.hostType === 'ide');
  const externalHosts = dashboard.hostBridge.hosts.filter((host) => host.hostType !== 'ide');

  return [
    {
      channelId: 'browser',
      label: 'Navigateur',
      status: dashboard.summary.canonicalEnvelopeCount > 0 ? 'ready' : 'blocked',
      tone: dashboard.summary.canonicalEnvelopeCount > 0 ? 'positive' : 'critical',
      detail:
        dashboard.summary.canonicalEnvelopeCount > 0
          ? `Le shell web relit ${dashboard.summary.canonicalEnvelopeCount} enveloppe(s) canoniques sans source de verite parallele.`
          : 'Aucune enveloppe canonique n est disponible pour rejouer le run dans le navigateur.',
      hostCount: 0,
      packetCount: dashboard.hostHandoffs.summary.packetCount
    },
    {
      channelId: 'vscode',
      label: 'VS Code',
      status: deriveVsCodeChannelStatus(ideHosts),
      tone: deriveVsCodeChannelTone(ideHosts),
      detail: describeVsCodeChannel(ideHosts),
      hostCount: ideHosts.length,
      packetCount: dashboard.hostHandoffs.summary.packetCount
    },
    {
      channelId: 'external',
      label: 'Hôtes externes',
      status: deriveExternalChannelStatus(dashboard),
      tone: deriveExternalChannelTone(dashboard),
      detail: describeExternalChannel(dashboard, externalHosts.length),
      hostCount: externalHosts.length,
      packetCount: dashboard.hostHandoffs.summary.packetCount
    }
  ];
}

function createDispatchHost(
  host: HostBridgeHostCard,
  packets: readonly HostHandoffPacket[],
  reviews: readonly HostReviewArtifactRecord[],
  contextEntries: readonly HostContextLedgerRecord[],
  invocations: readonly RuntimeDashboardView['hostBridge']['invocations'][number][]
): GenericHostBridgeDispatchHost {
  const linkedPackets = packets.filter((packet) => packet.hostIds.includes(host.hostId));
  const linkedReviews = reviews.filter((record) => record.review.hostId === host.hostId);
  const linkedContextEntries = contextEntries.filter((record) => record.entry.hostId === host.hostId);
  const latestInvocation = invocations.find((record) => record.envelope.hostId === host.hostId) ?? null;
  const latestReview = linkedReviews[0] ?? null;

  return {
    hostId: host.hostId,
    hostType: host.hostType,
    displayName: host.displayName,
    connectionState: host.connectionState,
    trustStatus: host.trustStatus,
    permissionMode: host.permissionMode,
    tone: deriveDispatchHostTone(host, linkedPackets, latestInvocation?.decision ?? null),
    routines: host.routines,
    toolProviders: host.toolProviders,
    reviewChannels: host.reviewChannels,
    contextSources: host.contextSources,
    packetCount: linkedPackets.length,
    readyPacketCount: linkedPackets.filter((packet) => packet.readyHostIds.includes(host.hostId)).length,
    reviewPendingPacketCount: linkedPackets.filter((packet) => packet.hostIds.includes(host.hostId) && packet.status === 'review_pending').length,
    blockedPacketCount: linkedPackets.filter((packet) => packet.hostIds.includes(host.hostId) && packet.status === 'blocked').length,
    latestDecision: latestInvocation?.decision ?? null,
    latestReviewVerdict: latestReview?.review.verdict ?? null,
    openReviewFindingCount: linkedReviews.reduce(
      (count, record) => count + record.review.findings.filter((finding) => finding.resolutionStatus !== 'resolved').length,
      0
    ),
    latestContextEntryId: linkedContextEntries[0]?.entry.entryId ?? null,
    reason: host.reason
  };
}

function createPacketCard(packet: HostHandoffPacket): GenericHostBridgePacketCard {
  return {
    packetId: packet.packetId,
    taskId: packet.taskId,
    taskTitle: packet.taskTitle,
    traceId: packet.traceId,
    status: packet.status,
    tone: packet.status === 'ready' ? 'positive' : packet.status === 'review_pending' ? 'warning' : 'critical',
    readyForDispatch: packet.readyForDispatch,
    hostIds: packet.hostIds,
    readyHostIds: packet.readyHostIds,
    latestDecision: packet.latestDecision,
    latestReviewVerdict: packet.latestReviewVerdict,
    openReviewFindingCount: packet.openReviewFindingCount,
    missingRequirements: packet.missingRequirements
  };
}

function deriveHeaderTone(dashboard: RuntimeDashboardView): RuntimeDashboardUiTone {
  if (dashboard.summary.blockedHostHandoffCount > 0 || dashboard.summary.deniedHostDecisionCount > 0) {
    return 'critical';
  }

  if (dashboard.summary.reviewPendingHostHandoffCount > 0 || dashboard.summary.degradedHostCount > 0) {
    return 'warning';
  }

  return 'positive';
}

function deriveVsCodeChannelStatus(ideHosts: readonly HostBridgeHostCard[]): 'ready' | 'degraded' | 'blocked' {
  if (ideHosts.length === 0) {
    return 'blocked';
  }

  return ideHosts.some((host) => host.connectionState === 'degraded' || host.connectionState === 'stale' || host.connectionState === 'offline')
    ? 'degraded'
    : 'ready';
}

function deriveVsCodeChannelTone(ideHosts: readonly HostBridgeHostCard[]): RuntimeDashboardUiTone {
  const status = deriveVsCodeChannelStatus(ideHosts);
  return status === 'ready' ? 'positive' : status === 'degraded' ? 'warning' : 'critical';
}

function describeVsCodeChannel(ideHosts: readonly HostBridgeHostCard[]): string {
  if (ideHosts.length === 0) {
    return 'Aucun host IDE n expose encore un panel stable sur ce run.';
  }

  return ideHosts.some((host) => host.connectionState === 'degraded' || host.connectionState === 'stale' || host.connectionState === 'offline')
    ? 'Le panel VS Code reste visible, mais au moins un binding IDE est degrade et doit rester borne en lecture.'
    : 'Le panel VS Code lit le meme run et remonte seulement des commandes bornees.';
}

function deriveExternalChannelStatus(dashboard: RuntimeDashboardView): 'ready' | 'degraded' | 'blocked' {
  if (dashboard.summary.readyHostHandoffCount > 0) {
    return 'ready';
  }

  if (dashboard.summary.reviewPendingHostHandoffCount > 0 || dashboard.summary.hostCount > 0) {
    return 'degraded';
  }

  return 'blocked';
}

function deriveExternalChannelTone(dashboard: RuntimeDashboardView): RuntimeDashboardUiTone {
  const status = deriveExternalChannelStatus(dashboard);
  return status === 'ready' ? 'positive' : status === 'degraded' ? 'warning' : 'critical';
}

function describeExternalChannel(dashboard: RuntimeDashboardView, externalHostCount: number): string {
  if (externalHostCount === 0) {
    return 'Aucun hôte externe n est encore raccorde au contrat generique.';
  }

  if (dashboard.summary.readyHostHandoffCount > 0) {
    return 'Au moins un packet est dispatchable vers un hôte externe sans casser la preuve ni la policy.';
  }

  if (dashboard.summary.reviewPendingHostHandoffCount > 0) {
    return 'Les hôtes externes voient le run, mais la promotion vers dispatch attend une revue ou une permission explicite.';
  }

  return 'Les hôtes externes restent visibles, mais aucun packet n est encore eligible au dispatch.';
}

function deriveDispatchHostTone(
  host: HostBridgeHostCard,
  packets: readonly HostHandoffPacket[],
  latestDecision: HostInvocationDecision | null
): RuntimeDashboardUiTone {
  if (host.connectionState === 'blocked' || host.trustStatus === 'blocked' || latestDecision === 'DENY') {
    return 'critical';
  }

  if (
    host.connectionState === 'degraded' ||
    host.connectionState === 'stale' ||
    host.connectionState === 'offline' ||
    host.trustStatus === 'review' ||
    host.trustStatus === 'restricted' ||
    latestDecision === 'PROMPT' ||
    packets.some((packet) => packet.status === 'review_pending')
  ) {
    return 'warning';
  }

  return 'positive';
}