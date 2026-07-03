import {
  LEASE_STORE_VERSION,
  LeaseRecordSchema,
  LeaseStoreSnapshotSchema,
  type CanonicalEnvelopePilot,
  type LeaseRecord,
  type LeaseStoreSnapshot
} from '../../contracts/events';

export interface LeaseStoreBuildOptions {
  scannedAt?: string;
  ttlMs?: number;
}

export interface LeaseClaimInput {
  projectId: string;
  runId: string;
  leaseId: string;
  taskId: string;
  nodeId: string;
  workerId?: string;
  worktreeId?: string;
  branch?: string;
  claimedAt?: string;
  ttlMs?: number;
  traceId?: string;
}

export interface LeaseRenewInput {
  leaseId: string;
  renewedAt?: string;
  ttlMs?: number;
  traceId?: string;
}

export interface LeaseMutationContext {
  projectId: string;
  runId: string;
  leaseId: string;
  nodeId: string;
  workerId?: string;
  worktreeId?: string;
  branch?: string;
}

interface MutableLeaseRecord {
  protocolVersion: string;
  projectId: string;
  runId: string;
  leaseId: string;
  taskId: string;
  nodeId: string;
  workerId?: string;
  worktreeId?: string;
  branch?: string;
  claimedAt: string;
  lastRenewedAt: string;
  ttlMs: number;
  traceId?: string;
  channels: string[];
  messageTypes: string[];
  messageCount: number;
  lastSequenceId: number;
}

interface ResolvedLeaseOptions {
  scannedAt: string;
  ttlMs: number;
}

export const DEFAULT_LEASE_TTL_MS = 30_000;

export class LeaseStoreError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'LeaseStoreError';
  }
}

export function buildLeaseStore(
  envelopes: readonly CanonicalEnvelopePilot[],
  options: LeaseStoreBuildOptions = {}
): LeaseStoreSnapshot {
  if (envelopes.length === 0) {
    throw new LeaseStoreError('Lease store requires at least one canonical envelope.');
  }

  const ordered = [...envelopes].sort(compareCanonicalEnvelopes);
  const leaseEnvelopes = ordered.filter(
    (envelope) =>
      envelope.context.leaseId !== undefined &&
      envelope.context.taskId !== undefined &&
      envelope.context.nodeId !== undefined
  );

  if (leaseEnvelopes.length === 0) {
    throw new LeaseStoreError('Lease store requires at least one canonical envelope with leaseId, taskId and nodeId.');
  }

  const firstEnvelope = leaseEnvelopes[0];
  if (firstEnvelope === undefined || firstEnvelope.context.projectId === undefined || firstEnvelope.context.runId === undefined) {
    throw new LeaseStoreError('Lease store requires projectId and runId on every lease envelope.');
  }

  const projectId = firstEnvelope.context.projectId;
  const runId = firstEnvelope.context.runId;
  const resolvedOptions = resolveLeaseOptions(options, ordered[ordered.length - 1]?.header.emittedAt);
  const leaseMap = new Map<string, MutableLeaseRecord>();

  for (const envelope of leaseEnvelopes) {
    const leaseId = envelope.context.leaseId;
    const taskId = envelope.context.taskId;
    const nodeId = envelope.context.nodeId;
    if (leaseId === undefined || taskId === undefined || nodeId === undefined) {
      continue;
    }

    if (envelope.context.projectId !== projectId || envelope.context.runId !== runId) {
      throw new LeaseStoreError('Lease store fail-closed: canonical envelopes span multiple project or run identifiers.');
    }

    const sequenceId = resolveSequenceId(envelope);
    const current = leaseMap.get(leaseId) ?? {
      protocolVersion: envelope.context.protocolVersion,
      projectId,
      runId,
      leaseId,
      taskId,
      nodeId,
      claimedAt: envelope.header.emittedAt,
      lastRenewedAt: envelope.header.emittedAt,
      ttlMs: resolvedOptions.ttlMs,
      channels: [],
      messageTypes: [],
      messageCount: 0,
      lastSequenceId: sequenceId
    } satisfies MutableLeaseRecord;

    if (current.taskId !== taskId) {
      throw new LeaseStoreError(`Lease ${leaseId} cannot span multiple task identifiers.`);
    }

    current.lastRenewedAt = maxIsoTimestamp(current.lastRenewedAt, envelope.header.emittedAt);
    current.messageCount += 1;
    current.lastSequenceId = Math.max(current.lastSequenceId, sequenceId);
    pushUnique(current.channels, envelope.header.channel);
    pushUnique(current.messageTypes, envelope.header.messageType);

    current.nodeId = nodeId;

    if (envelope.context.workerId !== undefined) {
      current.workerId = envelope.context.workerId;
    }

    if (envelope.context.worktreeId !== undefined) {
      current.worktreeId = envelope.context.worktreeId;
    }

    if (envelope.context.branch !== undefined) {
      current.branch = envelope.context.branch;
    }

    if (envelope.context.traceId !== undefined) {
      current.traceId = envelope.context.traceId;
    }

    leaseMap.set(leaseId, current);
  }

  const leases = Array.from(leaseMap.values())
    .sort((left, right) => left.leaseId.localeCompare(right.leaseId))
    .map((record) => materializeLeaseRecord(record, resolvedOptions.scannedAt));

  return LeaseStoreSnapshotSchema.parse({
    registryVersion: LEASE_STORE_VERSION,
    generatedAt: resolvedOptions.scannedAt,
    projectId,
    runId,
    leases,
    summary: {
      leaseCount: leases.length,
      activeLeaseCount: leases.filter((lease) => lease.status === 'active').length,
      expiredLeaseCount: leases.filter((lease) => lease.status === 'expired').length
    }
  });
}

export class LeaseStore {
  private readonly leases = new Map<string, MutableLeaseRecord>();
  private readonly envelopes: CanonicalEnvelopePilot[] = [];

  constructor(private readonly options: LeaseStoreBuildOptions = {}) {}

  claim(input: LeaseClaimInput): LeaseStoreSnapshot {
    const claimedAt = input.claimedAt ?? new Date().toISOString();
    const ttlMs = input.ttlMs ?? this.options.ttlMs ?? DEFAULT_LEASE_TTL_MS;
    this.expire(claimedAt);
    assertResolvedOwnershipInput(input);

    const activeConflict = Array.from(this.leases.values()).find(
      (lease) =>
        lease.taskId === input.taskId &&
        lease.leaseId !== input.leaseId &&
        materializeLeaseRecord(lease, claimedAt).status === 'active'
    );
    if (activeConflict !== undefined) {
      throw new LeaseStoreError(`Task ${input.taskId} already has an active lease ${activeConflict.leaseId}.`);
    }

    const unresolvedOwnership = Array.from(this.leases.values()).find(
      (lease) =>
        lease.projectId === input.projectId &&
        lease.runId === input.runId &&
        lease.leaseId !== input.leaseId &&
        materializeLeaseRecord(lease, claimedAt).status === 'active' &&
        (lease.branch === undefined || lease.worktreeId === undefined)
    );
    if (unresolvedOwnership !== undefined) {
      throw new LeaseStoreError(
        `Active lease ${unresolvedOwnership.leaseId} is missing resolved ownership metadata and blocks new claims.`
      );
    }

    const ownershipConflict = findActiveOwnershipConflict(this.leases.values(), input, claimedAt);
    if (ownershipConflict !== null) {
      if (ownershipConflict.kind === 'branch') {
        throw new LeaseStoreError(
          `Branch ${input.branch} is already owned by active lease ${ownershipConflict.lease.leaseId}.`
        );
      }

      throw new LeaseStoreError(
        `Worktree ${input.worktreeId} is already owned by active lease ${ownershipConflict.lease.leaseId}.`
      );
    }

    this.leases.set(input.leaseId, {
      protocolVersion: 'v1',
      projectId: input.projectId,
      runId: input.runId,
      leaseId: input.leaseId,
      taskId: input.taskId,
      nodeId: input.nodeId,
      ...(input.workerId === undefined ? {} : { workerId: input.workerId }),
      ...(input.worktreeId === undefined ? {} : { worktreeId: input.worktreeId }),
      ...(input.branch === undefined ? {} : { branch: input.branch }),
      claimedAt,
      lastRenewedAt: claimedAt,
      ttlMs,
      ...(input.traceId === undefined ? {} : { traceId: input.traceId }),
      channels: ['runtime'],
      messageTypes: ['lease.claim'],
      messageCount: 1,
      lastSequenceId: 0
    });

    return this.getSnapshot(claimedAt) as LeaseStoreSnapshot;
  }

  renew(input: LeaseRenewInput): LeaseStoreSnapshot {
    const renewedAt = input.renewedAt ?? new Date().toISOString();
    this.expire(renewedAt);
    const lease = this.leases.get(input.leaseId);

    if (lease === undefined) {
      throw new LeaseStoreError(`Lease ${input.leaseId} was not found.`);
    }

    if (materializeLeaseRecord(lease, renewedAt).status !== 'active') {
      throw new LeaseStoreError(`Lease ${input.leaseId} is expired and cannot be renewed.`);
    }

    lease.lastRenewedAt = renewedAt;
    lease.ttlMs = input.ttlMs ?? lease.ttlMs;
    lease.messageCount += 1;
    lease.lastSequenceId += 1;
    pushUnique(lease.messageTypes, 'lease.renew');

    if (input.traceId !== undefined) {
      lease.traceId = input.traceId;
    }

    return this.getSnapshot(renewedAt) as LeaseStoreSnapshot;
  }

  reclaim(input: LeaseClaimInput): LeaseStoreSnapshot {
    this.expire(input.claimedAt);
    return this.claim(input);
  }

  expire(expiredAt: string = new Date().toISOString()): LeaseStoreSnapshot | null {
    return this.getSnapshot(expiredAt);
  }

  applyEnvelope(envelope: CanonicalEnvelopePilot): LeaseStoreSnapshot {
    return this.applyEnvelopes([envelope]);
  }

  applyEnvelopes(envelopes: readonly CanonicalEnvelopePilot[]): LeaseStoreSnapshot {
    this.envelopes.push(...envelopes);
    const snapshot = buildLeaseStore(this.envelopes, this.options);
    this.leases.clear();
    for (const lease of snapshot.leases) {
      this.leases.set(lease.leaseId, {
        protocolVersion: lease.protocolVersion,
        projectId: lease.projectId,
        runId: lease.runId,
        leaseId: lease.leaseId,
        taskId: lease.taskId,
        nodeId: lease.nodeId,
        ...(lease.workerId === undefined ? {} : { workerId: lease.workerId }),
        ...(lease.worktreeId === undefined ? {} : { worktreeId: lease.worktreeId }),
        ...(lease.branch === undefined ? {} : { branch: lease.branch }),
        claimedAt: lease.claimedAt,
        lastRenewedAt: lease.lastRenewedAt,
        ttlMs: lease.ttlMs,
        ...(lease.traceId === undefined ? {} : { traceId: lease.traceId }),
        channels: [...lease.channels],
        messageTypes: [...lease.messageTypes],
        messageCount: lease.messageCount,
        lastSequenceId: lease.lastSequenceId
      });
    }

    return snapshot;
  }

  getSnapshot(scannedAt?: string): LeaseStoreSnapshot | null {
    if (this.leases.size === 0) {
      return null;
    }

    const resolvedScannedAt =
      scannedAt ??
      this.options.scannedAt ??
      this.envelopes[this.envelopes.length - 1]?.header.emittedAt ??
      Array.from(this.leases.values()).reduce<string | null>((latest, lease) => {
        if (latest === null) {
          return lease.lastRenewedAt;
        }

        return compareIsoTimestamp(latest, lease.lastRenewedAt) >= 0 ? latest : lease.lastRenewedAt;
      }, null) ??
      new Date().toISOString();

    const leases = Array.from(this.leases.values())
      .sort((left, right) => left.leaseId.localeCompare(right.leaseId))
      .map((lease) => materializeLeaseRecord(lease, resolvedScannedAt));
    const first = leases[0];

    if (first === undefined) {
      return null;
    }

    return LeaseStoreSnapshotSchema.parse({
      registryVersion: LEASE_STORE_VERSION,
      generatedAt: resolvedScannedAt,
      projectId: first.projectId,
      runId: first.runId,
      leases,
      summary: {
        leaseCount: leases.length,
        activeLeaseCount: leases.filter((lease) => lease.status === 'active').length,
        expiredLeaseCount: leases.filter((lease) => lease.status === 'expired').length
      }
    });
  }

  assertTaskMutationLease(taskId: string, context: LeaseMutationContext | undefined, at?: string): LeaseRecord {
    const scannedAt = at ?? new Date().toISOString();
    if (context === undefined) {
      throw new LeaseStoreError(`Task ${taskId} requires an active lease context.`);
    }

    const lease = this.leases.get(context.leaseId);
    if (lease === undefined) {
      throw new LeaseStoreError(`Lease ${context.leaseId} was not found.`);
    }

    const record = materializeLeaseRecord(lease, scannedAt);

    if (record.status !== 'active') {
      throw new LeaseStoreError(`Lease ${context.leaseId} is expired.`);
    }

    if (record.taskId !== taskId) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not own task ${taskId}.`);
    }

    if (record.projectId !== context.projectId || record.runId !== context.runId) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not match the active project or run.`);
    }

    if (record.nodeId !== context.nodeId) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not belong to node ${context.nodeId}.`);
    }

    if (record.worktreeId === undefined) {
      throw new LeaseStoreError(`Lease ${context.leaseId} is missing a resolved worktree.`);
    }

    if (record.branch === undefined) {
      throw new LeaseStoreError(`Lease ${context.leaseId} is missing a resolved branch.`);
    }

    if (context.worktreeId === undefined) {
      throw new LeaseStoreError(`Task ${taskId} requires a resolved worktree ownership context.`);
    }

    if (context.branch === undefined) {
      throw new LeaseStoreError(`Task ${taskId} requires a resolved branch ownership context.`);
    }

    if (context.workerId !== undefined && record.workerId !== context.workerId) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not belong to worker ${context.workerId}.`);
    }

    if (record.worktreeId !== context.worktreeId) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not match worktree ${context.worktreeId}.`);
    }

    if (record.branch !== context.branch) {
      throw new LeaseStoreError(`Lease ${context.leaseId} does not match branch ${context.branch}.`);
    }

    return record;
  }
}

function resolveLeaseOptions(options: LeaseStoreBuildOptions, fallbackScannedAt: string | undefined): ResolvedLeaseOptions {
  return {
    scannedAt: options.scannedAt ?? fallbackScannedAt ?? new Date().toISOString(),
    ttlMs: options.ttlMs ?? DEFAULT_LEASE_TTL_MS
  };
}

function materializeLeaseRecord(record: MutableLeaseRecord, scannedAt: string): LeaseRecord {
  const ageMs = computeAgeMs(record.lastRenewedAt, scannedAt);
  const status = ageMs > record.ttlMs ? 'expired' : 'active';

  return LeaseRecordSchema.parse({
    protocolVersion: record.protocolVersion,
    projectId: record.projectId,
    runId: record.runId,
    leaseId: record.leaseId,
    taskId: record.taskId,
    nodeId: record.nodeId,
    ...(record.workerId === undefined ? {} : { workerId: record.workerId }),
    ...(record.worktreeId === undefined ? {} : { worktreeId: record.worktreeId }),
    ...(record.branch === undefined ? {} : { branch: record.branch }),
    claimedAt: record.claimedAt,
    lastRenewedAt: record.lastRenewedAt,
    expiresAt: new Date(Date.parse(record.lastRenewedAt) + record.ttlMs).toISOString(),
    ttlMs: record.ttlMs,
    ageMs,
    status,
    messageCount: record.messageCount,
    lastSequenceId: record.lastSequenceId,
    ...(record.traceId === undefined ? {} : { traceId: record.traceId }),
    channels: [...record.channels],
    messageTypes: [...record.messageTypes]
  });
}

function computeAgeMs(lastRenewedAt: string, scannedAt: string): number {
  const lastRenewedMs = Date.parse(lastRenewedAt);
  const scannedAtMs = Date.parse(scannedAt);

  if (!Number.isFinite(lastRenewedMs) || !Number.isFinite(scannedAtMs)) {
    return 0;
  }

  return Math.max(0, scannedAtMs - lastRenewedMs);
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

  throw new LeaseStoreError(
    `Canonical envelope ${envelope.header.messageId} is missing a resolvable sequence identifier.`
  );
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

function assertResolvedOwnershipInput(input: LeaseClaimInput): asserts input is LeaseClaimInput & {
  branch: string;
  worktreeId: string;
} {
  if (input.branch === undefined) {
    throw new LeaseStoreError(`Lease ${input.leaseId} requires a resolved branch.`);
  }

  if (input.worktreeId === undefined) {
    throw new LeaseStoreError(`Lease ${input.leaseId} requires a resolved worktreeId.`);
  }
}

function findActiveOwnershipConflict(
  leases: Iterable<MutableLeaseRecord>,
  input: LeaseClaimInput & { branch: string; worktreeId: string },
  at: string
): { kind: 'branch' | 'worktree'; lease: MutableLeaseRecord } | null {
  for (const lease of leases) {
    if (
      lease.projectId !== input.projectId ||
      lease.runId !== input.runId ||
      lease.leaseId === input.leaseId ||
      materializeLeaseRecord(lease, at).status !== 'active'
    ) {
      continue;
    }

    if (lease.branch === input.branch) {
      return { kind: 'branch', lease };
    }

    if (lease.worktreeId === input.worktreeId) {
      return { kind: 'worktree', lease };
    }
  }

  return null;
}