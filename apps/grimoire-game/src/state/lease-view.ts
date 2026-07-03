import type { LeaseStoreSnapshot, TaskSnapshot } from '../contracts/events';

export interface LeaseViewAlert {
  code: 'lease_expired' | 'ownership_conflict' | 'ownership_unresolved';
  severity: 'warning' | 'critical';
  leaseId: string;
  message: string;
}

export type LeaseOwnershipStatus = 'owned' | 'conflicted' | 'expired' | 'unresolved';
export type LeaseDirtyStatus = 'clean' | 'dirty' | 'unknown';

export interface LeaseViewRecord {
  leaseId: string;
  taskId: string;
  nodeId: string;
  workerId: string | null;
  ownerId: string | null;
  worktreeId: string | null;
  branch: string | null;
  status: 'active' | 'expired';
  ownershipStatus: LeaseOwnershipStatus;
  ownershipConflicts: readonly string[];
  dirtyStatus: LeaseDirtyStatus;
  expiresAt: string;
  ageMs: number;
}

export interface LeaseViewSummary {
  projectId: string | null;
  runId: string | null;
  leaseCount: number;
  activeLeaseCount: number;
  expiredLeaseCount: number;
  alertCount: number;
}

export interface LeaseView {
  summary: LeaseViewSummary;
  leases: readonly LeaseViewRecord[];
  alerts: readonly LeaseViewAlert[];
}

export function createLeaseView(snapshot: LeaseStoreSnapshot | null, tasks: readonly TaskSnapshot[] = []): LeaseView {
  if (snapshot === null) {
    return {
      summary: {
        projectId: null,
        runId: null,
        leaseCount: 0,
        activeLeaseCount: 0,
        expiredLeaseCount: 0,
        alertCount: 0
      },
      leases: [],
      alerts: []
    };
  }

  const taskById = new Map(tasks.map((task) => [task.id, task]));
  const branchOwners = groupActiveOwners(snapshot, 'branch');
  const worktreeOwners = groupActiveOwners(snapshot, 'worktreeId');
  const leases = snapshot.leases.map((lease) => {
    const branchConflicts = collectOwnershipConflicts(branchOwners, lease.branch, lease.leaseId);
    const worktreeConflicts = collectOwnershipConflicts(worktreeOwners, lease.worktreeId, lease.leaseId);
    const ownershipConflicts = [...new Set([...branchConflicts, ...worktreeConflicts])].sort();
    const ownershipStatus = resolveOwnershipStatus(lease, ownershipConflicts);

    return {
    leaseId: lease.leaseId,
    taskId: lease.taskId,
    nodeId: lease.nodeId,
    workerId: lease.workerId ?? null,
    ownerId: lease.workerId ?? lease.nodeId ?? null,
    worktreeId: lease.worktreeId ?? null,
    branch: lease.branch ?? null,
    status: lease.status,
    ownershipStatus,
    ownershipConflicts,
    dirtyStatus: resolveDirtyStatus(taskById.get(lease.taskId), ownershipStatus),
    expiresAt: lease.expiresAt,
    ageMs: lease.ageMs
    };
  });
  const alerts = leases.flatMap((lease) => {
    const records: LeaseViewAlert[] = [];

    if (lease.status === 'expired') {
      records.push({
        code: 'lease_expired',
        severity: 'warning',
        leaseId: lease.leaseId,
        message: `Lease ${lease.leaseId} for task ${lease.taskId} is expired.`
      });
    }

    if (lease.ownershipStatus === 'unresolved') {
      records.push({
        code: 'ownership_unresolved',
        severity: 'warning',
        leaseId: lease.leaseId,
        message: `Lease ${lease.leaseId} is missing a resolved branch or worktree.`
      });
    }

    if (lease.ownershipStatus === 'conflicted') {
      records.push({
        code: 'ownership_conflict',
        severity: 'critical',
        leaseId: lease.leaseId,
        message: `Lease ${lease.leaseId} conflicts with ${lease.ownershipConflicts.join(', ')} on the active Git perimeter.`
      });
    }

    return records;
  });

  return {
    summary: {
      projectId: snapshot.projectId,
      runId: snapshot.runId,
      leaseCount: snapshot.summary.leaseCount,
      activeLeaseCount: snapshot.summary.activeLeaseCount,
      expiredLeaseCount: snapshot.summary.expiredLeaseCount,
      alertCount: alerts.length
    },
    leases,
    alerts
  };
}

function groupActiveOwners(
  snapshot: LeaseStoreSnapshot,
  key: 'branch' | 'worktreeId'
): Map<string, string[]> {
  const grouped = new Map<string, string[]>();

  for (const lease of snapshot.leases) {
    if (lease.status !== 'active') {
      continue;
    }

    const value = key === 'branch' ? lease.branch : lease.worktreeId;
    if (value === undefined) {
      continue;
    }

    const current = grouped.get(value) ?? [];
    current.push(lease.leaseId);
    grouped.set(value, current);
  }

  return grouped;
}

function collectOwnershipConflicts(grouped: Map<string, string[]>, key: string | undefined, leaseId: string): string[] {
  if (key === undefined) {
    return [];
  }

  return (grouped.get(key) ?? []).filter((candidate) => candidate !== leaseId);
}

function resolveOwnershipStatus(
  lease: LeaseStoreSnapshot['leases'][number],
  conflicts: readonly string[]
): LeaseOwnershipStatus {
  if (lease.status === 'expired') {
    return 'expired';
  }

  if (lease.branch === undefined || lease.worktreeId === undefined) {
    return 'unresolved';
  }

  if (conflicts.length > 0) {
    return 'conflicted';
  }

  return 'owned';
}

function resolveDirtyStatus(
  task: TaskSnapshot | undefined,
  ownershipStatus: LeaseOwnershipStatus
): LeaseDirtyStatus {
  if (ownershipStatus === 'unresolved' || ownershipStatus === 'expired') {
    return 'unknown';
  }

  if (task === undefined) {
    return 'unknown';
  }

  if (task.status === 'in_progress' || task.status === 'review') {
    return 'dirty';
  }

  return 'clean';
}