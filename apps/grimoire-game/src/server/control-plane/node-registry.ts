import {
  NODE_REGISTRY_VERSION,
  NodeRegistryRecordSchema,
  NodeRegistrySnapshotSchema,
  type CanonicalEnvelopePilot,
  type NodeRegistrySnapshot,
  type NodeWorkerRecord
} from '../../contracts/events';

export interface NodeRegistryBuildOptions {
  scannedAt?: string;
  staleAfterMs?: number;
  offlineAfterMs?: number;
}

export const DEFAULT_NODE_STALE_AFTER_MS = 5_000;
export const DEFAULT_NODE_OFFLINE_AFTER_MS = 30_000;

export class NodeRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'NodeRegistryError';
  }
}

interface ResolvedNodeRegistryOptions {
  scannedAt: string;
  staleAfterMs: number;
  offlineAfterMs: number;
}

interface MutableNodeWorkerRecord {
  workerId: string;
  firstSeenAt: string;
  lastSeenAt: string;
  firstSequenceId: number;
  lastSequenceId: number;
  messageCount: number;
  traceId?: string;
  taskId?: string;
  leaseId?: string;
  worktreeId?: string;
}

interface MutableNodeRecord {
  protocolVersion: string;
  projectId: string;
  runId: string;
  nodeId: string;
  firstSeenAt: string;
  lastSeenAt: string;
  firstSequenceId: number;
  lastSequenceId: number;
  messageCount: number;
  traceId?: string;
  taskId?: string;
  leaseId?: string;
  worktreeId?: string;
  capabilityTags: string[];
  workerIds: string[];
  workers: Map<string, MutableNodeWorkerRecord>;
  channels: string[];
  messageTypes: string[];
}

export function buildNodeRegistry(
  envelopes: readonly CanonicalEnvelopePilot[],
  options: NodeRegistryBuildOptions = {}
): NodeRegistrySnapshot {
  if (envelopes.length === 0) {
    throw new NodeRegistryError('Node registry requires at least one canonical envelope.');
  }

  const ordered = [...envelopes].sort(compareCanonicalEnvelopes);
  const nodeEnvelopes = ordered.filter((envelope) => envelope.context.nodeId !== undefined);

  if (nodeEnvelopes.length === 0) {
    throw new NodeRegistryError('Node registry requires at least one canonical envelope with nodeId.');
  }

  const firstNodeEnvelope = nodeEnvelopes[0];

  if (firstNodeEnvelope === undefined) {
    throw new NodeRegistryError('Node registry requires at least one canonical envelope with nodeId.');
  }

  const projectId = firstNodeEnvelope.context.projectId;
  const runId = firstNodeEnvelope.context.runId;

  if (projectId === undefined || runId === undefined) {
    throw new NodeRegistryError('Node registry requires projectId and runId on every node envelope.');
  }

  const resolvedOptions = resolveNodeRegistryOptions(options, ordered[ordered.length - 1]?.header.emittedAt);
  const nodeMap = new Map<string, MutableNodeRecord>();

  for (const envelope of nodeEnvelopes) {
    const nodeId = envelope.context.nodeId;
    if (nodeId === undefined) {
      continue;
    }

    if (envelope.context.projectId !== projectId || envelope.context.runId !== runId) {
      throw new NodeRegistryError('Node registry fail-closed: canonical envelopes span multiple project or run identifiers.');
    }

    const sequenceId = resolveSequenceId(envelope);
    const currentNode = nodeMap.get(nodeId) ?? {
      protocolVersion: envelope.context.protocolVersion,
      projectId,
      runId,
      nodeId,
      firstSeenAt: envelope.header.emittedAt,
      lastSeenAt: envelope.header.emittedAt,
      firstSequenceId: sequenceId,
      lastSequenceId: sequenceId,
      messageCount: 0,
      capabilityTags: [],
      workerIds: [],
      workers: new Map<string, MutableNodeWorkerRecord>(),
      channels: [],
      messageTypes: []
    } satisfies MutableNodeRecord;

    currentNode.firstSeenAt = minIsoTimestamp(currentNode.firstSeenAt, envelope.header.emittedAt);
    currentNode.lastSeenAt = maxIsoTimestamp(currentNode.lastSeenAt, envelope.header.emittedAt);
    currentNode.firstSequenceId = Math.min(currentNode.firstSequenceId, sequenceId);
    currentNode.lastSequenceId = Math.max(currentNode.lastSequenceId, sequenceId);
    currentNode.messageCount += 1;
    pushUnique(currentNode.channels, envelope.header.channel);
    pushUnique(currentNode.messageTypes, envelope.header.messageType);

    if (envelope.context.traceId !== undefined) {
      currentNode.traceId = envelope.context.traceId;
    }

    if (envelope.context.taskId !== undefined) {
      currentNode.taskId = envelope.context.taskId;
    }

    if (envelope.context.leaseId !== undefined) {
      currentNode.leaseId = envelope.context.leaseId;
    }

    if (envelope.context.worktreeId !== undefined) {
      currentNode.worktreeId = envelope.context.worktreeId;
    }

    if (envelope.context.workerId !== undefined) {
      pushUnique(currentNode.workerIds, envelope.context.workerId);
      const currentWorker = currentNode.workers.get(envelope.context.workerId) ?? {
        workerId: envelope.context.workerId,
        firstSeenAt: envelope.header.emittedAt,
        lastSeenAt: envelope.header.emittedAt,
        firstSequenceId: sequenceId,
        lastSequenceId: sequenceId,
        messageCount: 0
      };

      currentWorker.firstSeenAt = minIsoTimestamp(currentWorker.firstSeenAt, envelope.header.emittedAt);
      currentWorker.lastSeenAt = maxIsoTimestamp(currentWorker.lastSeenAt, envelope.header.emittedAt);
      currentWorker.firstSequenceId = Math.min(currentWorker.firstSequenceId, sequenceId);
      currentWorker.lastSequenceId = Math.max(currentWorker.lastSequenceId, sequenceId);
      currentWorker.messageCount += 1;

      if (envelope.context.traceId !== undefined) {
        currentWorker.traceId = envelope.context.traceId;
      }

      if (envelope.context.taskId !== undefined) {
        currentWorker.taskId = envelope.context.taskId;
      }

      if (envelope.context.leaseId !== undefined) {
        currentWorker.leaseId = envelope.context.leaseId;
      }

      if (envelope.context.worktreeId !== undefined) {
        currentWorker.worktreeId = envelope.context.worktreeId;
      }

      currentNode.workers.set(envelope.context.workerId, currentWorker);
    }

    nodeMap.set(nodeId, currentNode);
  }

  const nodes = Array.from(nodeMap.values())
    .sort((left, right) => left.nodeId.localeCompare(right.nodeId))
    .map((node) => {
      const ageMs = computeAgeMs(node.lastSeenAt, resolvedOptions.scannedAt);
      const status = inferNodeStatus(ageMs, resolvedOptions.staleAfterMs, resolvedOptions.offlineAfterMs);
      const workers = Array.from(node.workers.values())
        .sort((left, right) => left.workerId.localeCompare(right.workerId))
        .map((worker) => toNodeWorkerRecord(worker));

      return NodeRegistryRecordSchema.parse({
        protocolVersion: node.protocolVersion,
        projectId: node.projectId,
        runId: node.runId,
        nodeId: node.nodeId,
        firstSeenAt: node.firstSeenAt,
        lastSeenAt: node.lastSeenAt,
        firstSequenceId: node.firstSequenceId,
        lastSequenceId: node.lastSequenceId,
        messageCount: node.messageCount,
        staleAfterMs: resolvedOptions.staleAfterMs,
        offlineAfterMs: resolvedOptions.offlineAfterMs,
        ageMs,
        status,
        ...(node.traceId === undefined ? {} : { traceId: node.traceId }),
        ...(node.taskId === undefined ? {} : { taskId: node.taskId }),
        ...(node.leaseId === undefined ? {} : { leaseId: node.leaseId }),
        ...(node.worktreeId === undefined ? {} : { worktreeId: node.worktreeId }),
        capabilityTags: [...node.capabilityTags],
        workerIds: [...node.workerIds],
        workers,
        channels: [...node.channels],
        messageTypes: [...node.messageTypes]
      });
    });

  const summary = {
    nodeCount: nodes.length,
    liveNodeCount: nodes.filter((node) => node.status === 'live').length,
    staleNodeCount: nodes.filter((node) => node.status === 'stale').length,
    offlineNodeCount: nodes.filter((node) => node.status === 'offline').length,
    workerCount: nodes.reduce((count, node) => count + node.workerIds.length, 0)
  };

  return NodeRegistrySnapshotSchema.parse({
    registryVersion: NODE_REGISTRY_VERSION,
    generatedAt: resolvedOptions.scannedAt,
    projectId,
    runId,
    nodes,
    summary
  });
}

export class NodeRegistry {
  private readonly envelopes: CanonicalEnvelopePilot[] = [];

  constructor(private readonly options: NodeRegistryBuildOptions = {}) {}

  applyEnvelope(envelope: CanonicalEnvelopePilot): NodeRegistrySnapshot {
    this.envelopes.push(envelope);
    return buildNodeRegistry(this.envelopes, this.options);
  }

  applyEnvelopes(envelopes: readonly CanonicalEnvelopePilot[]): NodeRegistrySnapshot {
    this.envelopes.push(...envelopes);
    return buildNodeRegistry(this.envelopes, this.options);
  }

  getSnapshot(): NodeRegistrySnapshot | null {
    return this.envelopes.length === 0 ? null : buildNodeRegistry(this.envelopes, this.options);
  }
}

function resolveNodeRegistryOptions(
  options: NodeRegistryBuildOptions,
  fallbackScannedAt: string | undefined
): ResolvedNodeRegistryOptions {
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_NODE_STALE_AFTER_MS;
  const offlineAfterMs = options.offlineAfterMs ?? DEFAULT_NODE_OFFLINE_AFTER_MS;

  if (offlineAfterMs <= staleAfterMs) {
    throw new NodeRegistryError('Node registry requires offlineAfterMs to be greater than staleAfterMs.');
  }

  return {
    scannedAt: options.scannedAt ?? fallbackScannedAt ?? new Date().toISOString(),
    staleAfterMs,
    offlineAfterMs
  };
}

function toNodeWorkerRecord(worker: MutableNodeWorkerRecord): NodeWorkerRecord {
  return {
    workerId: worker.workerId,
    firstSeenAt: worker.firstSeenAt,
    lastSeenAt: worker.lastSeenAt,
    firstSequenceId: worker.firstSequenceId,
    lastSequenceId: worker.lastSequenceId,
    messageCount: worker.messageCount,
    ...(worker.traceId === undefined ? {} : { traceId: worker.traceId }),
    ...(worker.taskId === undefined ? {} : { taskId: worker.taskId }),
    ...(worker.leaseId === undefined ? {} : { leaseId: worker.leaseId }),
    ...(worker.worktreeId === undefined ? {} : { worktreeId: worker.worktreeId })
  };
}

function inferNodeStatus(ageMs: number, staleAfterMs: number, offlineAfterMs: number): 'live' | 'stale' | 'offline' {
  if (ageMs > offlineAfterMs) {
    return 'offline';
  }

  if (ageMs > staleAfterMs) {
    return 'stale';
  }

  return 'live';
}

function computeAgeMs(lastSeenAt: string, scannedAt: string): number {
  const lastSeenMs = Date.parse(lastSeenAt);
  const scannedAtMs = Date.parse(scannedAt);

  if (!Number.isFinite(lastSeenMs) || !Number.isFinite(scannedAtMs)) {
    return 0;
  }

  return Math.max(0, scannedAtMs - lastSeenMs);
}

function compareCanonicalEnvelopes(left: CanonicalEnvelopePilot, right: CanonicalEnvelopePilot): number {
  const sequenceDelta = resolveSequenceId(left) - resolveSequenceId(right);
  if (sequenceDelta !== 0) {
    return sequenceDelta;
  }

  return left.header.emittedAt.localeCompare(right.header.emittedAt);
}

function resolveSequenceId(envelope: CanonicalEnvelopePilot): number {
  if (envelope.header.sequenceId !== undefined) {
    return envelope.header.sequenceId;
  }

  const suffixMatch = envelope.header.messageId.match(/:(\d+)$/);
  if (suffixMatch !== null && suffixMatch[1] !== undefined) {
    return Number.parseInt(suffixMatch[1], 10);
  }

  throw new NodeRegistryError(
    `Canonical envelope ${envelope.header.messageId} is missing a resolvable sequence identifier.`
  );
}

function minIsoTimestamp(left: string, right: string): string {
  return compareIsoTimestamp(left, right) <= 0 ? left : right;
}

function maxIsoTimestamp(left: string, right: string): string {
  return compareIsoTimestamp(left, right) >= 0 ? left : right;
}

function compareIsoTimestamp(left: string, right: string): number {
  const leftMs = Date.parse(left);
  const rightMs = Date.parse(right);

  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) {
    return leftMs - rightMs;
  }

  return left.localeCompare(right);
}

function pushUnique(target: string[], value: string): void {
  if (!target.includes(value)) {
    target.push(value);
  }
}