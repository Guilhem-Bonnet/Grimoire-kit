import {
  createRuntimeDashboardUiView,
  type RuntimeDashboardUiTone,
  type RuntimeDashboardUiViewOptions
} from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface RuntimeKernelHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  projectId: string | null;
  runId: string | null;
  protocolVersion: string;
  focusTraceId: string | null;
  focusTaskId: string | null;
}

export interface RuntimeKernelStatCard {
  id: string;
  label: string;
  value: string | number;
  hint: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeKernelTriadItem {
  id: string;
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  focus: boolean;
  details: readonly string[];
}

export interface RuntimeKernelTriadPanel {
  id: 'nodes' | 'leases' | 'hosts';
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  items: readonly RuntimeKernelTriadItem[];
}

export interface RuntimeKernelContractCard {
  id: string;
  label: string;
  version: string | null;
  detail: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeKernelInvariant {
  id: string;
  label: string;
  status: 'clear' | 'attention' | 'blocked';
  tone: RuntimeDashboardUiTone;
  detail: string;
}

export interface RuntimeKernelCausalityStep {
  id: string;
  label: string;
  value: string;
  detail: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeKernelView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeKernelHeader;
  statCards: readonly RuntimeKernelStatCard[];
  triad: readonly RuntimeKernelTriadPanel[];
  contracts: readonly RuntimeKernelContractCard[];
  invariants: readonly RuntimeKernelInvariant[];
  causality: readonly RuntimeKernelCausalityStep[];
}

export function createRuntimeKernelView(
  dashboard: RuntimeDashboardView,
  options: RuntimeDashboardUiViewOptions = {}
): RuntimeKernelView {
  const ui = createRuntimeDashboardUiView(dashboard, options);
  const project = dashboard.projectRegistry?.activeProject ?? null;
  const blockingReasons = dashboard.supervision.releaseGate.blockingReasons;
  const primaryPack = dashboard.verificationEvidencePacks.packs[0] ?? null;

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Noyau Forge',
      subtitle:
        dashboard.supervision.releaseGate.releaseBlocked
          ? 'Control plane, contrats et causalite partages sous surveillance active.'
          : 'Control plane, contrats et causalite alignes sur le meme run operatoire.',
      tone: ui.header.tone,
      projectId: project?.projectId ?? dashboard.nodeFleet.summary.projectId,
      runId: ui.focus.runId,
      protocolVersion: dashboard.protocolVersion,
      focusTraceId: ui.focus.traceId,
      focusTaskId: ui.focus.taskId
    },
    statCards: [
      {
        id: 'project',
        label: 'Projet actif',
        value: project?.projectId ?? 'indisponible',
        hint: project?.runId ?? 'aucun run relie',
        tone: project === null ? 'warning' : 'positive'
      },
      {
        id: 'nodes',
        label: 'Nodes live',
        value: `${dashboard.summary.liveNodeCount}/${dashboard.summary.nodeCount}`,
        hint: `${dashboard.summary.nodeWorkerCount} worker(s)` ,
        tone: dashboard.summary.staleNodeCount > 0 || dashboard.summary.offlineNodeCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'leases',
        label: 'Leases actives',
        value: `${dashboard.summary.activeLeaseCount}/${dashboard.summary.leaseCount}`,
        hint: `${dashboard.summary.expiredLeaseCount} expiree(s)`,
        tone: dashboard.summary.expiredLeaseCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'hosts',
        label: 'Hosts relies',
        value: dashboard.summary.hostCount,
        hint: `${dashboard.summary.deniedHostDecisionCount + dashboard.summary.promptedHostDecisionCount} decision(s) auditees`,
        tone: dashboard.summary.degradedHostCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'lineage',
        label: 'Lineage',
        value: dashboard.summary.lineageEdgeCount,
        hint: `${dashboard.summary.staleLineageAlertCount} alerte(s)`,
        tone: dashboard.summary.staleLineageAlertCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'proofs',
        label: 'Proof packs',
        value: dashboard.summary.verificationEvidencePackCount,
        hint: `${dashboard.summary.missingExpectedProofCount} preuve(s) manquante(s)`,
        tone:
          dashboard.summary.verificationEvidencePackCount === 0
            ? 'critical'
            : dashboard.summary.missingExpectedProofCount > 0
              ? 'warning'
              : 'positive'
      }
    ],
    triad: [
      createNodeTriadPanel(dashboard, ui),
      createLeaseTriadPanel(dashboard, ui),
      createHostTriadPanel(dashboard, ui)
    ],
    contracts: [
      {
        id: 'runtime-protocol',
        label: 'Runtime protocol',
        version: dashboard.protocolVersion,
        detail: 'GameState reste la source unique de verite pour les projections web.',
        tone: 'positive'
      },
      {
        id: 'project-registry',
        label: 'Project registry',
        version: dashboard.projectRegistry?.registryVersion ?? null,
        detail:
          project === null
            ? 'Aucun registre projet relie au run courant.'
            : `${project.channels.length} channel(s), ${project.messageTypes.length} type(s) de message, ${project.nodeIds.length} node(s).`,
        tone: project === null ? 'warning' : 'positive'
      },
      {
        id: 'node-registry',
        label: 'Node registry',
        version: null,
        detail: `${dashboard.nodeFleet.summary.nodeCount} node(s), ${dashboard.nodeFleet.summary.workerCount} worker(s), ${dashboard.nodeFleet.summary.alertCount} alerte(s).`,
        tone: dashboard.nodeFleet.summary.alertCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'lease-store',
        label: 'Lease store',
        version: null,
        detail: `${dashboard.leaseView.summary.leaseCount} lease(s), ${dashboard.leaseView.summary.activeLeaseCount} active(s), ${dashboard.leaseView.summary.alertCount} alerte(s).`,
        tone: dashboard.leaseView.summary.alertCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'host-bridge',
        label: 'Host bridge',
        version: null,
        detail: `${dashboard.hostBridge.metrics.hostCount} host(s), ${dashboard.hostBridge.metrics.reviewArtifactCount} review(s), ${dashboard.hostBridge.metrics.contextEntryCount} contexte(s) importes.`,
        tone: dashboard.hostBridge.metrics.hostCount === 0 ? 'warning' : 'positive'
      },
      {
        id: 'verification-chain',
        label: 'Verification chain',
        version: null,
        detail: `${dashboard.summary.verificationEvidencePackCount} pack(s), ${dashboard.summary.verificationAttestationCount} attestation(s), ${dashboard.summary.missingExpectedProofCount} preuve(s) manquante(s).`,
        tone:
          dashboard.summary.verificationEvidencePackCount === 0
            ? 'critical'
            : dashboard.summary.missingExpectedProofCount > 0
              ? 'warning'
              : 'positive'
      }
    ],
    invariants: [
      createInvariant(
        'control-plane-linked',
        'Control plane relie',
        project !== null && dashboard.nodeFleet.summary.nodeCount > 0 && dashboard.leaseView.summary.leaseCount > 0
          ? 'clear'
          : 'attention',
        project !== null && dashboard.nodeFleet.summary.nodeCount > 0 && dashboard.leaseView.summary.leaseCount > 0
          ? 'Project registry, node fleet et lease store sont relies au meme run.'
          : 'Le shell reste lisible, mais le control plane est partiel pour ce run.'
      ),
      createInvariant(
        'single-runtime-truth',
        'Verite runtime partagee',
        'clear',
        'Les surfaces cockpit, observer, Game UI et VS Code restent derivees du meme GameState.'
      ),
      createInvariant(
        'ownership-coherence',
        'Ownership sans conflit',
        dashboard.leaseView.alerts.some((alert) => alert.code === 'ownership_conflict')
          ? 'blocked'
          : dashboard.leaseView.alerts.length > 0
            ? 'attention'
            : 'clear',
        dashboard.leaseView.alerts.some((alert) => alert.code === 'ownership_conflict')
          ? 'Un conflit de lease actif existe sur le perimetre Git en cours.'
          : dashboard.leaseView.alerts.length > 0
            ? 'Leases ou ownership a surveiller avant toute mutation.'
            : 'Aucun conflit de lease ou d ownership detecte sur le run courant.'
      ),
      createInvariant(
        'host-guards-audited',
        'Host guards audites',
        dashboard.hostBridge.metrics.allowDecisionCount +
          dashboard.hostBridge.metrics.promptDecisionCount +
          dashboard.hostBridge.metrics.denyDecisionCount +
          dashboard.hostBridge.metrics.degradeDecisionCount > 0
          ? 'clear'
          : 'attention',
        dashboard.hostBridge.metrics.allowDecisionCount +
          dashboard.hostBridge.metrics.promptDecisionCount +
          dashboard.hostBridge.metrics.denyDecisionCount +
          dashboard.hostBridge.metrics.degradeDecisionCount > 0
          ? 'Les decisions host sont visibles et auditees dans le run.'
          : 'Aucune decision host recente n a ete reliee au run courant.'
      ),
      createInvariant(
        'proof-linkage',
        'Preuves reliees au verdict',
        dashboard.summary.verificationEvidencePackCount === 0
          ? 'blocked'
          : dashboard.summary.missingExpectedProofCount > 0
            ? 'attention'
            : 'clear',
        dashboard.summary.verificationEvidencePackCount === 0
          ? 'Aucun evidence pack n est relie au verdict courant.'
          : dashboard.summary.missingExpectedProofCount > 0
            ? 'Des preuves attendues manquent encore dans les evidence packs.'
            : 'Les evidence packs couverts restent relies au verdict courant.'
      ),
      createInvariant(
        'lineage-connected',
        'Lineage exploitable',
        dashboard.summary.staleLineageAlertCount > 0 ? 'attention' : 'clear',
        dashboard.summary.staleLineageAlertCount > 0
          ? 'Des alertes de lineage degradent encore la causalite partagee.'
          : 'La chaine de lineage reste exploitable pour suivre le run et ses preuves.'
      )
    ],
    causality: [
      {
        id: 'focus-run',
        label: 'Run actif',
        value: ui.focus.runId ?? project?.runId ?? 'indisponible',
        detail: project?.projectId ?? 'Aucun projet actif relie',
        tone: ui.focus.runId === null ? 'warning' : 'positive'
      },
      {
        id: 'focus-trace',
        label: 'Trace en focus',
        value: ui.focus.traceTitle ?? ui.focus.traceId ?? 'aucune trace',
        detail: ui.focus.traceId ?? 'Aucune trace dominante pour ce scenario.',
        tone: ui.focus.traceId === null ? 'neutral' : 'positive'
      },
      {
        id: 'focus-proof',
        label: 'Verification reliee',
        value: primaryPack?.verificationRef ?? 'aucune verification',
        detail:
          primaryPack === null
            ? 'Aucun evidence pack n a encore ete relie a la vue courante.'
            : `${primaryPack.evidence.length} evidence(s), ${primaryPack.controlRefs.length} control(s), verdict ${primaryPack.verdict}.`,
        tone: primaryPack === null ? 'warning' : primaryPack.proofCoverage?.missingExpectedProofRefs.length ? 'warning' : 'positive'
      },
      {
        id: 'focus-release',
        label: 'Effet release',
        value: dashboard.supervision.releaseGate.releaseBlocked ? 'NO-GO' : 'GO',
        detail: blockingReasons[0] ?? 'Aucun blocage release visible pour ce run.',
        tone: dashboard.supervision.releaseGate.releaseBlocked ? 'critical' : 'positive'
      }
    ]
  };
}

function createNodeTriadPanel(
  dashboard: RuntimeDashboardView,
  ui: ReturnType<typeof createRuntimeDashboardUiView>
): RuntimeKernelTriadPanel {
  const items: RuntimeKernelTriadItem[] = dashboard.nodeFleet.nodes.slice(0, 3).map((node) => ({
    id: node.nodeId,
    title: node.nodeId,
    subtitle: `${node.status} · seq ${node.lastSequenceId}`,
    tone: node.status === 'offline' ? 'critical' : node.status === 'stale' ? 'warning' : 'positive',
    focus: ui.focus.nodeId === node.nodeId,
    details: [
      `${node.workerIds.length} worker(s)`,
      `${node.channels.length} channel(s)`,
      `${node.messageTypes.length} type(s) de message`,
      `${node.capabilityTags.join(', ') || 'aucune capacite declaree'}`
    ]
  }));

  return {
    id: 'nodes',
    title: 'Triade runtime · Nodes',
    subtitle: 'Heartbeat, capacites et couverture du run',
    tone: aggregatePanelTone(items),
    items
  };
}

function createLeaseTriadPanel(
  dashboard: RuntimeDashboardView,
  ui: ReturnType<typeof createRuntimeDashboardUiView>
): RuntimeKernelTriadPanel {
  const items: RuntimeKernelTriadItem[] = dashboard.leaseView.leases.slice(0, 3).map((lease) => ({
    id: lease.leaseId,
    title: lease.taskId,
    subtitle: `${lease.ownershipStatus} · ${lease.status}`,
    tone:
      lease.ownershipStatus === 'conflicted'
        ? 'critical'
        : lease.status === 'expired' || lease.ownershipStatus === 'unresolved'
          ? 'warning'
          : 'positive',
    focus: ui.focus.taskId === lease.taskId,
    details: [
      `node ${lease.nodeId}`,
      `branch ${lease.branch ?? 'indisponible'}`,
      `worktree ${lease.worktreeId ?? 'indisponible'}`,
      `dirty ${lease.dirtyStatus}`
    ]
  }));

  return {
    id: 'leases',
    title: 'Triade runtime · Leases',
    subtitle: 'Ownership, worktree et etat de mutation',
    tone: aggregatePanelTone(items),
    items
  };
}

function createHostTriadPanel(
  dashboard: RuntimeDashboardView,
  ui: ReturnType<typeof createRuntimeDashboardUiView>
): RuntimeKernelTriadPanel {
  const items: RuntimeKernelTriadItem[] = dashboard.hostBridge.hosts.slice(0, 3).map((host) => ({
    id: host.hostId,
    title: host.displayName,
    subtitle: `${host.connectionState} · trust ${host.trustStatus}`,
    tone: toneForHost(host.connectionState, host.trustStatus),
    focus: ui.focus.taskId !== null && dashboard.hostBridge.reviews.some((review) => review.review.hostId === host.hostId),
    details: [
      `${host.toolProviderCount} provider(s)`,
      `${host.reviewChannelCount} review channel(s)`,
      `${host.contextSourceCount} contexte(s)`,
      `${host.permissionMode}`
    ]
  }));

  return {
    id: 'hosts',
    title: 'Triade runtime · Hosts',
    subtitle: 'Canaux, trust et capacites branchees sur le run',
    tone: aggregatePanelTone(items),
    items
  };
}

function aggregatePanelTone(items: readonly RuntimeKernelTriadItem[]): RuntimeDashboardUiTone {
  if (items.some((item) => item.tone === 'critical')) {
    return 'critical';
  }

  if (items.some((item) => item.tone === 'warning')) {
    return 'warning';
  }

  if (items.some((item) => item.tone === 'positive')) {
    return 'positive';
  }

  return 'neutral';
}

function createInvariant(
  id: string,
  label: string,
  status: RuntimeKernelInvariant['status'],
  detail: string
): RuntimeKernelInvariant {
  return {
    id,
    label,
    status,
    tone: status === 'blocked' ? 'critical' : status === 'attention' ? 'warning' : 'positive',
    detail
  };
}

function toneForHost(
  connectionState: RuntimeDashboardView['hostBridge']['hosts'][number]['connectionState'],
  trustStatus: RuntimeDashboardView['hostBridge']['hosts'][number]['trustStatus']
): RuntimeDashboardUiTone {
  if (connectionState === 'blocked' || trustStatus === 'blocked') {
    return 'critical';
  }

  if (connectionState === 'stale' || connectionState === 'degraded' || trustStatus === 'review' || trustStatus === 'restricted') {
    return 'warning';
  }

  if (connectionState === 'online' && trustStatus === 'trusted') {
    return 'positive';
  }

  return 'neutral';
}