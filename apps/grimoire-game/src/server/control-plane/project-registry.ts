import {
  CONTROL_PLANE_REGISTRY_VERSION,
  ProjectRegistryRecordSchema,
  ProjectRegistrySnapshotSchema,
  type CanonicalEnvelopePilot,
  type ProjectRegistrySnapshot
} from '../../contracts/events';

export class ProjectRegistryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ProjectRegistryError';
  }
}

export function buildActiveProjectRegistry(
  envelopes: readonly CanonicalEnvelopePilot[]
): ProjectRegistrySnapshot {
  if (envelopes.length === 0) {
    throw new ProjectRegistryError('Project registry requires at least one canonical envelope.');
  }

  const ordered = [...envelopes].sort(compareCanonicalEnvelopes);
  const firstEnvelope = ordered[0];

  if (firstEnvelope === undefined) {
    throw new ProjectRegistryError('Project registry requires at least one canonical envelope.');
  }

  const projectId = firstEnvelope.context.projectId;
  const runId = firstEnvelope.context.runId;

  if (projectId === undefined || runId === undefined) {
    throw new ProjectRegistryError('Project registry requires projectId and runId on every canonical envelope.');
  }

  const channels: string[] = [];
  const messageTypes: string[] = [];
  const nodeIds: string[] = [];
  const workerIds: string[] = [];
  const leaseIds: string[] = [];
  const worktreeIds: string[] = [];

  let lastCorrelationId: string | undefined;
  let traceId: string | undefined;
  let taskId: string | undefined;
  let nodeId: string | undefined;
  let workerId: string | undefined;
  let leaseId: string | undefined;
  let worktreeId: string | undefined;

  for (const envelope of ordered) {
    if (envelope.context.projectId !== projectId || envelope.context.runId !== runId) {
      throw new ProjectRegistryError(
        'Project registry fail-closed: canonical envelopes span multiple project or run identifiers.'
      );
    }

    pushUnique(channels, envelope.header.channel);
    pushUnique(messageTypes, envelope.header.messageType);

    if (envelope.context.nodeId !== undefined) {
      nodeId = envelope.context.nodeId;
      pushUnique(nodeIds, envelope.context.nodeId);
    }

    if (envelope.context.workerId !== undefined) {
      workerId = envelope.context.workerId;
      pushUnique(workerIds, envelope.context.workerId);
    }

    if (envelope.context.leaseId !== undefined) {
      leaseId = envelope.context.leaseId;
      pushUnique(leaseIds, envelope.context.leaseId);
    }

    if (envelope.context.worktreeId !== undefined) {
      worktreeId = envelope.context.worktreeId;
      pushUnique(worktreeIds, envelope.context.worktreeId);
    }

    if (envelope.context.correlationId !== undefined) {
      lastCorrelationId = envelope.context.correlationId;
    }

    if (envelope.context.traceId !== undefined) {
      traceId = envelope.context.traceId;
    }

    if (envelope.context.taskId !== undefined) {
      taskId = envelope.context.taskId;
    }
  }

  const lastEnvelope = ordered[ordered.length - 1] ?? firstEnvelope;
  const activeProject = ProjectRegistryRecordSchema.parse({
    protocolVersion: firstEnvelope.context.protocolVersion,
    projectId,
    runId,
    firstEventAt: firstEnvelope.header.emittedAt,
    lastEventAt: lastEnvelope.header.emittedAt,
    firstSequenceId: resolveSequenceId(firstEnvelope),
    lastSequenceId: resolveSequenceId(lastEnvelope),
    eventCount: ordered.length,
    lastMessageId: lastEnvelope.header.messageId,
    ...(lastCorrelationId === undefined ? {} : { lastCorrelationId }),
    ...(traceId === undefined ? {} : { traceId }),
    ...(taskId === undefined ? {} : { taskId }),
    ...(nodeId === undefined ? {} : { nodeId }),
    ...(workerId === undefined ? {} : { workerId }),
    ...(leaseId === undefined ? {} : { leaseId }),
    ...(worktreeId === undefined ? {} : { worktreeId }),
    nodeIds,
    workerIds,
    leaseIds,
    worktreeIds,
    channels,
    messageTypes
  });

  return ProjectRegistrySnapshotSchema.parse({
    registryVersion: CONTROL_PLANE_REGISTRY_VERSION,
    generatedAt: lastEnvelope.header.emittedAt,
    activeProject
  });
}

export class ProjectRegistry {
  private readonly envelopes: CanonicalEnvelopePilot[] = [];

  applyEnvelope(envelope: CanonicalEnvelopePilot): ProjectRegistrySnapshot {
    this.envelopes.push(envelope);
    return buildActiveProjectRegistry(this.envelopes);
  }

  applyEnvelopes(envelopes: readonly CanonicalEnvelopePilot[]): ProjectRegistrySnapshot {
    this.envelopes.push(...envelopes);
    return buildActiveProjectRegistry(this.envelopes);
  }

  getSnapshot(): ProjectRegistrySnapshot | null {
    return this.envelopes.length === 0 ? null : buildActiveProjectRegistry(this.envelopes);
  }
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
    const suffix = suffixMatch[1];
    return Number.parseInt(suffix, 10);
  }

  throw new ProjectRegistryError(
    `Canonical envelope ${envelope.header.messageId} is missing a resolvable sequence identifier.`
  );
}

function pushUnique(target: string[], value: string): void {
  if (!target.includes(value)) {
    target.push(value);
  }
}