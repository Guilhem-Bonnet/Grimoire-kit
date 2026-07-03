import {
  LEASE_STORE_VERSION,
  RUNTIME_PROTOCOL_VERSION,
  type LeaseStoreSnapshot,
  type TaskSnapshot
} from '../../src/contracts/events';
import type { GameState, WorkflowStepLogEntry } from '../../src/state/game-state';
import { createWorktreeRoomView } from '../../src/state/worktree-room-view';

function createState(
  recentWorkflowSteps: readonly WorkflowStepLogEntry[],
  tasks: Record<string, TaskSnapshot>
): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 420,
    hydratedAt: '2026-04-11T10:00:00.000Z',
    agents: {
      'dev-1': {
        id: 'dev-1',
        name: 'Amelia',
        role: 'agent',
        status: 'working',
        roomId: 'build-room',
        position: { x: 8, y: 8 }
      }
    },
    tasks,
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps,
    lastErrors: []
  };
}

function createLeaseStore(leases: LeaseStoreSnapshot['leases']): LeaseStoreSnapshot {
  return {
    registryVersion: LEASE_STORE_VERSION,
    generatedAt: '2026-04-11T10:00:10.000Z',
    projectId: 'grimoire-game',
    runId: 'run-42',
    leases,
    summary: {
      leaseCount: leases.length,
      activeLeaseCount: leases.filter((lease) => lease.status === 'active').length,
      expiredLeaseCount: leases.filter((lease) => lease.status === 'expired').length
    }
  };
}

describe('worktree-room-view', () => {
  it('projects an active worktree room with closure actions and branch audit trail', () => {
    const state = createState(
      [
        {
          step: 'Branch finisher options updated',
          detail: 'feature/auth',
          sourceEventType: 'branch_finish_options',
          taskId: 'task-auth',
          traceId: 'trace-auth',
          metadata: {
            branch: 'feature/auth',
            testsPassed: true,
            allowedOptions: ['merge', 'pr', 'keep', 'discard'],
            typedDiscardConfirmation: 'DROP-BRANCH'
          },
          sequenceId: 210,
          timestamp: '2026-04-11T10:00:01.000Z'
        },
        {
          step: 'Decision recorded',
          detail: 'auth ready for review',
          sourceEventType: 'decision',
          taskId: 'task-auth',
          traceId: 'trace-auth',
          metadata: {
            topic: 'auth',
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-auth/worktree-room',
            controlsExecuted: ['tests:unit', 'review:critical-findings'],
            evidenceRefs: ['tests://grimoire-game/worktree-room#task-auth'],
            verdict: 'PASS'
          },
          sequenceId: 211,
          timestamp: '2026-04-11T10:00:02.000Z'
        },
        {
          step: 'Branch finisher decision proposed',
          detail: 'feature/auth: pr',
          sourceEventType: 'branch_finish_decision',
          taskId: 'task-auth',
          traceId: 'trace-auth',
          metadata: {
            branch: 'feature/auth',
            selectedOption: 'pr',
            typedConfirmation: ''
          },
          sequenceId: 212,
          timestamp: '2026-04-11T10:00:03.000Z'
        }
      ],
      {
        'task-auth': {
          id: 'task-auth',
          title: 'Implement auth room',
          status: 'review',
          assigneeId: 'dev-1'
        }
      }
    );
    const snapshot = createLeaseStore([
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth',
        claimedAt: '2026-04-11T10:00:00.000Z',
        lastRenewedAt: '2026-04-11T10:00:05.000Z',
        expiresAt: '2026-04-11T10:00:35.000Z',
        ttlMs: 30_000,
        ageMs: 5_000,
        status: 'active',
        messageCount: 2,
        lastSequenceId: 212,
        channels: ['runtime'],
        messageTypes: ['task.update']
      }
    ]);

    const view = createWorktreeRoomView(state, snapshot);
    const room = view.rooms[0];

    expect(room).toBeDefined();
    if (room === undefined) {
      throw new Error('Expected worktree room to be present.');
    }

    const actionByOption = Object.fromEntries(room.actions.map((action) => [action.option, action]));

    expect(view.summary).toMatchObject({
      roomCount: 1,
      activeRoomCount: 1,
      dirtyRoomCount: 1,
      alertCount: 0
    });
    expect(room).toMatchObject({
      leaseId: 'lease-auth',
      branch: 'feature/auth',
      worktreeId: 'wt-auth',
      ownershipStatus: 'owned',
      dirtyStatus: 'dirty',
      testsPassed: true,
      verificationStatus: 'verifying',
      tone: 'warning'
    });
    expect(actionByOption.merge).toMatchObject({ allowed: true });
    expect(actionByOption.pr).toMatchObject({ allowed: true, selected: true });
    expect(actionByOption.keep).toMatchObject({ allowed: true });
    expect(actionByOption.discard).toMatchObject({
      allowed: true,
      requiresTypedConfirmation: true,
      requiredTypedConfirmation: 'DROP-BRANCH'
    });
    expect(room?.latestDecision).toMatchObject({
      selectedOption: 'pr',
      allowed: true
    });
    expect(room?.auditTrail.map((entry) => entry.sourceEventType)).toEqual([
      'branch_finish_decision',
      'branch_finish_options'
    ]);
    expect(actionByOption.pr?.surfaces.map((surface) => [surface.surface, surface.status])).toEqual([
      ['lease_view', 'ready'],
      ['branch_finisher', 'ready'],
      ['verification_queue', 'ready'],
      ['security_audit', 'ready']
    ]);
  });

  it('surfaces branch collisions, stale rooms and verification blockers before branch closure', () => {
    const state = createState(
      [
        {
          step: 'Branch finisher options updated',
          detail: 'feature/auth',
          sourceEventType: 'branch_finish_options',
          metadata: {
            branch: 'feature/auth',
            testsPassed: true,
            allowedOptions: ['merge', 'pr', 'keep', 'discard'],
            typedDiscardConfirmation: 'CLEAR-ROOM'
          },
          sequenceId: 310,
          timestamp: '2026-04-11T10:01:00.000Z'
        },
        {
          step: 'Decision recorded',
          detail: 'Collision review rejected',
          sourceEventType: 'decision',
          taskId: 'task-conflict',
          traceId: 'trace-conflict',
          metadata: {
            topic: 'auth',
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-conflict/worktree-room',
            controlsExecuted: ['tests:integration'],
            evidenceRefs: ['tests://grimoire-game/worktree-room#task-conflict'],
            verdict: 'FAIL'
          },
          sequenceId: 311,
          timestamp: '2026-04-11T10:01:01.000Z'
        },
        {
          step: 'Branch finisher options updated',
          detail: 'feature/stale',
          sourceEventType: 'branch_finish_options',
          metadata: {
            branch: 'feature/stale',
            testsPassed: true,
            allowedOptions: ['merge', 'pr', 'keep', 'discard']
          },
          sequenceId: 312,
          timestamp: '2026-04-11T10:01:02.000Z'
        },
        {
          step: 'Decision recorded',
          detail: 'Stale branch verification passed',
          sourceEventType: 'decision',
          taskId: 'task-stale',
          traceId: 'trace-stale',
          metadata: {
            topic: 'cleanup',
            actionId: 'task.transition.done',
            verificationRef: 'verify://task-stale/worktree-room',
            controlsExecuted: ['tests:unit'],
            evidenceRefs: ['tests://grimoire-game/worktree-room#task-stale'],
            verdict: 'PASS'
          },
          sequenceId: 313,
          timestamp: '2026-04-11T10:01:03.000Z'
        }
      ],
      {
        'task-auth': {
          id: 'task-auth',
          title: 'Auth implementation',
          status: 'review',
          assigneeId: 'dev-1'
        },
        'task-conflict': {
          id: 'task-conflict',
          title: 'Resolve auth collision',
          status: 'review',
          assigneeId: 'dev-1'
        },
        'task-stale': {
          id: 'task-stale',
          title: 'Retire stale worktree',
          status: 'done',
          assigneeId: 'dev-1'
        }
      }
    );
    const snapshot = createLeaseStore([
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth',
        claimedAt: '2026-04-11T10:01:00.000Z',
        lastRenewedAt: '2026-04-11T10:01:05.000Z',
        expiresAt: '2026-04-11T10:01:35.000Z',
        ttlMs: 30_000,
        ageMs: 5_000,
        status: 'active',
        messageCount: 2,
        lastSequenceId: 313,
        channels: ['runtime'],
        messageTypes: ['task.update']
      },
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-conflict',
        taskId: 'task-conflict',
        nodeId: 'node-beta',
        workerId: 'worker-dev-2',
        worktreeId: 'wt-auth-alt',
        branch: 'feature/auth',
        claimedAt: '2026-04-11T10:01:00.000Z',
        lastRenewedAt: '2026-04-11T10:01:05.000Z',
        expiresAt: '2026-04-11T10:01:35.000Z',
        ttlMs: 30_000,
        ageMs: 5_000,
        status: 'active',
        messageCount: 2,
        lastSequenceId: 313,
        channels: ['runtime'],
        messageTypes: ['task.update']
      },
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-stale',
        taskId: 'task-stale',
        nodeId: 'node-gamma',
        workerId: 'worker-dev-3',
        worktreeId: 'wt-stale',
        branch: 'feature/stale',
        claimedAt: '2026-04-11T09:50:00.000Z',
        lastRenewedAt: '2026-04-11T09:55:00.000Z',
        expiresAt: '2026-04-11T10:00:00.000Z',
        ttlMs: 30_000,
        ageMs: 600_000,
        status: 'expired',
        messageCount: 2,
        lastSequenceId: 313,
        channels: ['runtime'],
        messageTypes: ['task.update']
      }
    ]);

    const view = createWorktreeRoomView(state, snapshot);
    const conflictRoom = view.rooms.find((room) => room.leaseId === 'lease-conflict');
    const staleRoom = view.rooms.find((room) => room.leaseId === 'lease-stale');

    expect(conflictRoom).toBeDefined();
    expect(staleRoom).toBeDefined();
    if (conflictRoom === undefined || staleRoom === undefined) {
      throw new Error('Expected conflict and stale worktree rooms to be present.');
    }

    const conflictActions = Object.fromEntries(conflictRoom.actions.map((action) => [action.option, action]));

    expect(view.summary).toMatchObject({
      roomCount: 3,
      conflictRoomCount: 2,
      staleRoomCount: 1
    });
    expect(conflictRoom).toMatchObject({
      ownershipStatus: 'conflicted',
      verificationStatus: 'rejected',
      branchCollisionCount: 1,
      tone: 'critical'
    });
    expect(conflictRoom?.alerts.map((alert) => alert.code)).toEqual([
      'ownership_conflict',
      'verification_blocked'
    ]);
    expect(conflictActions.merge?.allowed).toBe(false);
    expect(conflictActions.pr?.allowed).toBe(false);
    expect(conflictActions.discard?.allowed).toBe(false);
    expect(conflictActions.merge?.blockedReasons).toContain(
      'Worktree room requires exclusive branch/worktree ownership.'
    );
    expect(conflictActions.merge?.blockedReasons).toContain(
      'Task Resolve auth collision is rejected in verification.'
    );
    expect(conflictActions.keep?.allowed).toBe(true);
    expect(staleRoom).toMatchObject({
      status: 'expired',
      verificationStatus: 'accepted',
      tone: 'warning'
    });
    expect(staleRoom?.alerts.map((alert) => alert.code)).toEqual(['lease_expired']);
    expect(staleRoom?.actions.find((action) => action.option === 'merge')?.blockedReasons).toContain(
      'Worktree room lease is expired.'
    );
  });

  it('reflects room creation, synchronization and removal directly from lease topology', () => {
    const workflowSteps: WorkflowStepLogEntry[] = [
      {
        step: 'Branch finisher options updated',
        detail: 'feature/auth',
        sourceEventType: 'branch_finish_options',
        taskId: 'task-auth',
        traceId: 'trace-auth',
        metadata: {
          branch: 'feature/auth',
          testsPassed: true,
          allowedOptions: ['merge', 'pr', 'keep', 'discard']
        },
        sequenceId: 410,
        timestamp: '2026-04-11T10:02:00.000Z'
      },
      {
        step: 'Decision recorded',
        detail: 'auth verified',
        sourceEventType: 'decision',
        taskId: 'task-auth',
        traceId: 'trace-auth',
        metadata: {
          topic: 'auth',
          actionId: 'task.transition.done',
          verificationRef: 'verify://task-auth/worktree-room',
          controlsExecuted: ['tests:unit'],
          evidenceRefs: ['tests://grimoire-game/worktree-room#task-auth'],
          verdict: 'PASS'
        },
        sequenceId: 411,
        timestamp: '2026-04-11T10:02:01.000Z'
      }
    ];
    const activeLease = createLeaseStore([
      {
        protocolVersion: RUNTIME_PROTOCOL_VERSION,
        projectId: 'grimoire-game',
        runId: 'run-42',
        leaseId: 'lease-auth',
        taskId: 'task-auth',
        nodeId: 'node-alpha',
        workerId: 'worker-dev-1',
        worktreeId: 'wt-auth',
        branch: 'feature/auth',
        claimedAt: '2026-04-11T10:02:00.000Z',
        lastRenewedAt: '2026-04-11T10:02:05.000Z',
        expiresAt: '2026-04-11T10:02:35.000Z',
        ttlMs: 30_000,
        ageMs: 5_000,
        status: 'active',
        messageCount: 2,
        lastSequenceId: 411,
        channels: ['runtime'],
        messageTypes: ['task.update']
      }
    ]);
    const emptyLeaseStore = createLeaseStore([]);
    const reviewState = createState(workflowSteps, {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth room',
        status: 'review',
        assigneeId: 'dev-1'
      }
    });
    const doneState = createState(workflowSteps, {
      'task-auth': {
        id: 'task-auth',
        title: 'Implement auth room',
        status: 'done',
        assigneeId: 'dev-1'
      }
    });

    const createdView = createWorktreeRoomView(reviewState, activeLease);
    const syncedView = createWorktreeRoomView(doneState, activeLease);
    const removedView = createWorktreeRoomView(doneState, emptyLeaseStore);

    expect(createdView.rooms).toHaveLength(1);
    expect(createdView.rooms[0]).toMatchObject({
      leaseId: 'lease-auth',
      dirtyStatus: 'dirty',
      verificationStatus: 'verifying'
    });
    expect(syncedView.rooms).toHaveLength(1);
    expect(syncedView.rooms[0]).toMatchObject({
      leaseId: 'lease-auth',
      dirtyStatus: 'clean',
      verificationStatus: 'accepted'
    });
    expect(removedView.rooms).toHaveLength(0);
    expect(createWorktreeRoomView(doneState, activeLease)).toEqual(syncedView);
  });
});