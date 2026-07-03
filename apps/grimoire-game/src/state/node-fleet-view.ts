import type { NodeRegistrySnapshot, NodeRegistryStatus } from '../contracts/events';

export interface NodeFleetAlert {
  code: 'node_stale' | 'node_offline';
  severity: 'warning' | 'critical';
  nodeId: string;
  message: string;
}

export interface NodeFleetNodeView {
  nodeId: string;
  status: NodeRegistryStatus;
  ageMs: number;
  lastSeenAt: string;
  lastSequenceId: number;
  workerIds: readonly string[];
  channels: readonly string[];
  messageTypes: readonly string[];
  capabilityTags: readonly string[];
}

export interface NodeFleetSummary {
  projectId: string | null;
  runId: string | null;
  nodeCount: number;
  liveNodeCount: number;
  staleNodeCount: number;
  offlineNodeCount: number;
  workerCount: number;
  alertCount: number;
}

export interface NodeFleetView {
  summary: NodeFleetSummary;
  nodes: readonly NodeFleetNodeView[];
  alerts: readonly NodeFleetAlert[];
}

export function createNodeFleetView(snapshot: NodeRegistrySnapshot | null): NodeFleetView {
  if (snapshot === null) {
    return createEmptyNodeFleetView();
  }

  const nodes = snapshot.nodes.map((node) => ({
    nodeId: node.nodeId,
    status: node.status,
    ageMs: node.ageMs,
    lastSeenAt: node.lastSeenAt,
    lastSequenceId: node.lastSequenceId,
    workerIds: [...node.workerIds],
    channels: [...node.channels],
    messageTypes: [...node.messageTypes],
    capabilityTags: [...node.capabilityTags]
  }));
  const alerts = nodes.flatMap((node) => createAlertsForNode(node));

  return {
    summary: {
      projectId: snapshot.projectId,
      runId: snapshot.runId,
      nodeCount: snapshot.summary.nodeCount,
      liveNodeCount: snapshot.summary.liveNodeCount,
      staleNodeCount: snapshot.summary.staleNodeCount,
      offlineNodeCount: snapshot.summary.offlineNodeCount,
      workerCount: snapshot.summary.workerCount,
      alertCount: alerts.length
    },
    nodes,
    alerts
  };
}

export function createEmptyNodeFleetView(): NodeFleetView {
  return {
    summary: {
      projectId: null,
      runId: null,
      nodeCount: 0,
      liveNodeCount: 0,
      staleNodeCount: 0,
      offlineNodeCount: 0,
      workerCount: 0,
      alertCount: 0
    },
    nodes: [],
    alerts: []
  };
}

function createAlertsForNode(node: NodeFleetNodeView): NodeFleetAlert[] {
  if (node.status === 'offline') {
    return [
      {
        code: 'node_offline',
        severity: 'critical',
        nodeId: node.nodeId,
        message: `Node ${node.nodeId} is offline.`
      }
    ];
  }

  if (node.status === 'stale') {
    return [
      {
        code: 'node_stale',
        severity: 'warning',
        nodeId: node.nodeId,
        message: `Node ${node.nodeId} heartbeat is stale.`
      }
    ];
  }

  return [];
}