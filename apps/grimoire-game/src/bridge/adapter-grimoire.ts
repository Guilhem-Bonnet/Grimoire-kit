import {
  createErrorEvent,
  createStateSnapshotEvent,
  type AgentStatus,
  type AgentPresence,
  type ClientEvent,
  type GameStateSnapshot,
  type JsonValue,
  type TaskStatus,
  type ServerEvent
} from '../contracts/events';
import { hydrateGameState } from '../state/game-state';
import { evaluateTaskReviewVerificationGate, evaluateTaskVerificationGate } from '../state/verification-view';
import type { AuthContext } from '../server/auth/rbac';
import { authorizeClientEvent, createAuthorizationAuditEntry } from '../server/auth/rbac';
import type { AdapterAuditLogEntry, AgentAdapter } from './agent-adapter';
import {
  assertCanonicalMutationIdentity,
  isAllowedAgentStatusTransition,
  isAllowedAgentStatusUpdateStatus,
  isAllowedConfigMutationKey,
  isAllowedTaskStatusTransition,
  isAllowedTaskTransitionStatus
} from './runtime-mutation-guards';
import {
  normalizeProcessedMutationCacheMaxEntries,
  setBoundedMutationCacheEntry
} from './idempotency-cache';

export interface GrimoireAgentRecord {
  id: string;
  name: string;
  role: AgentPresence['role'];
  status: AgentPresence['status'];
  roomId: string;
  position: AgentPresence['position'];
  parentId?: string | null | undefined;
  lastTool?: string | null | undefined;
}

export interface GrimoireRuntimeLeaseContext {
  projectId: string;
  runId: string;
  leaseId: string;
  nodeId: string;
  workerId?: string;
  worktreeId?: string;
  branch?: string;
}

export interface GrimoireRuntimeSource {
  readAgents?(): Promise<readonly GrimoireAgentRecord[]>;
  readConfig?(): Promise<Record<string, JsonValue>>;
  readEventsSince?(lastSequenceId: number): Promise<readonly ServerEvent[]>;
  applyConfigUpdate?(mutation: GrimoireRuntimeConfigMutation): Promise<GrimoireRuntimeMutationResult>;
  applyTaskTransition?(mutation: GrimoireRuntimeTaskMutation): Promise<GrimoireRuntimeMutationResult>;
  applyTaskAssign?(mutation: GrimoireRuntimeTaskAssignMutation): Promise<GrimoireRuntimeMutationResult>;
  applyAgentStatusUpdate?(mutation: GrimoireRuntimeAgentStatusMutation): Promise<GrimoireRuntimeMutationResult>;
  readSnapshot?(): Promise<{
    generatedAt?: string;
    lastSequenceId?: number;
    agents: readonly GrimoireAgentRecord[];
    tasks?: GameStateSnapshot['tasks'];
    config?: Record<string, JsonValue>;
    recentToolCalls?: GameStateSnapshot['recentToolCalls'];
    recentWorkflowSteps?: GameStateSnapshot['recentWorkflowSteps'];
    hostBindings?: GameStateSnapshot['hostBindings'];
    recentHostInvocationDecisions?: GameStateSnapshot['recentHostInvocationDecisions'];
    recentHostContextEntries?: GameStateSnapshot['recentHostContextEntries'];
    recentHostReviews?: GameStateSnapshot['recentHostReviews'];
  }>;
}

export interface GrimoireRuntimeConfigMutation {
  requestId: string;
  idempotencyKey: string;
  key: string;
  value: JsonValue;
  guardrail?: Extract<ClientEvent, { type: 'CONFIG_UPDATE' }>['guardrail'];
  auth: AuthContext;
}

export interface GrimoireRuntimeTaskMutation {
  requestId: string;
  idempotencyKey: string;
  taskId: string;
  status: TaskStatus;
  verification?: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>['verification'];
  guardrail?: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>['guardrail'];
  leaseContext?: GrimoireRuntimeLeaseContext;
  auth: AuthContext;
}

export interface GrimoireRuntimeTaskAssignMutation {
  requestId: string;
  idempotencyKey: string;
  taskId: string;
  assigneeId: string;
  guardrail?: Extract<ClientEvent, { type: 'TASK_ASSIGN' }>['guardrail'];
  leaseContext?: GrimoireRuntimeLeaseContext;
  auth: AuthContext;
}

export interface GrimoireRuntimeAgentStatusMutation {
  requestId: string;
  idempotencyKey: string;
  agentId: string;
  status: AgentStatus;
  guardrail?: Extract<ClientEvent, { type: 'AGENT_STATUS_UPDATE' }>['guardrail'];
  auth: AuthContext;
}

export interface GrimoireRuntimeMutationResult {
  sequenceId: number;
  snapshot: GameStateSnapshot;
  timestamp?: string;
}

export interface AdapterGrimoireOptions {
  processedMutationCacheMaxEntries?: number;
  taskLeaseContext?: GrimoireRuntimeLeaseContext;
}

function cloneSerializable<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export class AdapterGrimoire implements AgentAdapter {
  readonly source = 'grimoire';
  private readonly processedMutations = new Map<string, readonly ServerEvent[]>();
  private readonly auditLog: AdapterAuditLogEntry[] = [];
  private readonly processedMutationCacheMaxEntries: number;
  private readonly taskLeaseContext: GrimoireRuntimeLeaseContext | undefined;
  private maxIssuedSequenceId = 0;

  constructor(
    private readonly runtimeSource: GrimoireRuntimeSource,
    options: AdapterGrimoireOptions = {}
  ) {
    this.processedMutationCacheMaxEntries = normalizeProcessedMutationCacheMaxEntries(
      options.processedMutationCacheMaxEntries
    );
    this.taskLeaseContext = options.taskLeaseContext;
  }

  async getInitialSnapshot(auth: AuthContext): Promise<readonly ServerEvent[]> {
    const snapshot = await this.buildSnapshot();
    this.syncMaxSequenceId(snapshot.lastSequenceId);
    const snapshotEvent = createStateSnapshotEvent(snapshot.lastSequenceId, snapshot);
    this.recordAudit({
      type: 'SNAPSHOT_SENT',
      at: snapshotEvent.timestamp,
      principalId: auth.principalId,
      role: auth.role,
      sequenceId: snapshotEvent.sequenceId,
      detail: 'Full snapshot sent.'
    });
    return [snapshotEvent];
  }

  async reconnect(lastSequenceId: number | undefined, auth: AuthContext): Promise<readonly ServerEvent[]> {
    if (lastSequenceId !== undefined && this.runtimeSource.readEventsSince !== undefined) {
      const replayEvents = await this.runtimeSource.readEventsSince(lastSequenceId);

      if (replayEvents.length > 0) {
        const replaySequenceId = replayEvents[replayEvents.length - 1]?.sequenceId;
        if (replaySequenceId !== undefined) {
          this.syncMaxSequenceId(replaySequenceId);
        }
        this.recordAudit({
          type: 'REPLAY_SENT',
          at: new Date().toISOString(),
          principalId: auth.principalId,
          role: auth.role,
          ...(replaySequenceId === undefined ? {} : { sequenceId: replaySequenceId }),
          detail: `Replayed ${replayEvents.length} event(s) after sequence ${lastSequenceId}.`
        });
        return replayEvents;
      }

      const snapshot = await this.buildSnapshot();
      if (lastSequenceId < snapshot.lastSequenceId) {
        this.syncMaxSequenceId(snapshot.lastSequenceId);
        const snapshotEvent = createStateSnapshotEvent(snapshot.lastSequenceId, snapshot);
        this.recordAudit({
          type: 'SNAPSHOT_SENT',
          at: snapshotEvent.timestamp,
          principalId: auth.principalId,
          role: auth.role,
          sequenceId: snapshotEvent.sequenceId,
          detail: `Snapshot fallback after sequence ${lastSequenceId}.`
        });
        return [snapshotEvent];
      }

      return [];
    }

    return this.getInitialSnapshot(auth);
  }

  async handleClientEvent(event: ClientEvent, auth: AuthContext): Promise<readonly ServerEvent[]> {
    if (event.type === 'RECONNECT_HANDSHAKE') {
      const reconnectDecision = authorizeClientEvent(auth, event);

      if (!reconnectDecision.allowed) {
        const auditEntry = createAuthorizationAuditEntry(auth, event, reconnectDecision);
        this.recordAudit({
          type: 'AUTH_REJECTED',
          at: auditEntry.at,
          requestId: event.requestId,
          principalId: auditEntry.principalId,
          role: auditEntry.role,
          ...(auditEntry.reason === undefined ? {} : { detail: auditEntry.reason })
        });
        return [await this.createRuntimeError('FORBIDDEN', reconnectDecision.reason ?? 'Forbidden.', event.requestId, auth)];
      }

      return this.reconnect(event.lastSequenceId, auth);
    }

    try {
      assertCanonicalMutationIdentity(event.type, 'requestId', event.requestId);
      assertCanonicalMutationIdentity(event.type, 'idempotencyKey', event.idempotencyKey);
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'Invalid mutation identity.';
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: reason
      });
      return [await this.createRuntimeError('FORBIDDEN', reason, event.requestId, auth)];
    }

    const decision = authorizeClientEvent(auth, event);

    if (!decision.allowed) {
      const auditEntry = createAuthorizationAuditEntry(auth, event, decision);
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: auditEntry.at,
        requestId: event.requestId,
        principalId: auditEntry.principalId,
        role: auditEntry.role,
        ...(auditEntry.reason === undefined ? {} : { detail: auditEntry.reason })
      });
      return [await this.createRuntimeError('FORBIDDEN', decision.reason ?? 'Forbidden.', event.requestId, auth)];
    }

    if (event.type === 'CONFIG_UPDATE') {
      return this.applyConfigUpdate(event, auth);
    }

    if (event.type === 'TASK_TRANSITION') {
      return this.applyTaskTransition(event, auth);
    }

    if (event.type === 'TASK_ASSIGN') {
      return this.applyTaskAssign(event, auth);
    }

    return this.applyAgentStatusUpdate(event, auth);
  }

  private async buildSnapshot(): Promise<GameStateSnapshot> {
    if (this.runtimeSource.readSnapshot !== undefined) {
      const snapshot = await this.runtimeSource.readSnapshot();
      const resolvedSnapshot: GameStateSnapshot = {
        protocolVersion: 'v1',
        generatedAt: snapshot.generatedAt ?? new Date().toISOString(),
        lastSequenceId: snapshot.lastSequenceId ?? 0,
        agents: snapshot.agents.map((agent) => ({
          id: agent.id,
          name: agent.name,
          role: agent.role,
          status: agent.status,
          roomId: agent.roomId,
          position: { ...agent.position },
          ...(agent.parentId === undefined ? {} : { parentId: agent.parentId }),
          ...(agent.lastTool === undefined ? {} : { lastTool: agent.lastTool })
        })),
        tasks:
          snapshot.tasks?.map((task) => ({
            ...task,
            ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
          })) ?? [],
        config: snapshot.config === undefined ? {} : { ...snapshot.config },
        recentToolCalls:
          snapshot.recentToolCalls?.map((entry) => ({
            ...entry,
            params: { ...entry.params }
          })) ?? [],
        recentWorkflowSteps:
          snapshot.recentWorkflowSteps?.map((entry) => ({
            ...entry,
            metadata: { ...entry.metadata }
          })) ?? [],
        ...(snapshot.hostBindings === undefined ? {} : { hostBindings: cloneSerializable(snapshot.hostBindings) }),
        ...(snapshot.recentHostInvocationDecisions === undefined
          ? {}
          : { recentHostInvocationDecisions: cloneSerializable(snapshot.recentHostInvocationDecisions) }),
        ...(snapshot.recentHostContextEntries === undefined
          ? {}
          : { recentHostContextEntries: cloneSerializable(snapshot.recentHostContextEntries) }),
        ...(snapshot.recentHostReviews === undefined
          ? {}
          : { recentHostReviews: cloneSerializable(snapshot.recentHostReviews) })
      };
      this.syncMaxSequenceId(resolvedSnapshot.lastSequenceId);
      return resolvedSnapshot;
    }

    if (this.runtimeSource.readAgents === undefined) {
      throw new Error('GrimoireRuntimeSource must provide readSnapshot() or readAgents().');
    }

    const agents = await this.runtimeSource.readAgents();
    const config = this.runtimeSource.readConfig === undefined ? {} : await this.runtimeSource.readConfig();

    const resolvedSnapshot: GameStateSnapshot = {
      protocolVersion: 'v1',
      generatedAt: new Date().toISOString(),
      lastSequenceId: 0,
      agents: agents.map((agent) => ({
        id: agent.id,
        name: agent.name,
        role: agent.role,
        status: agent.status,
        roomId: agent.roomId,
        position: { ...agent.position },
        ...(agent.parentId === undefined ? {} : { parentId: agent.parentId }),
        ...(agent.lastTool === undefined ? {} : { lastTool: agent.lastTool })
      })),
      tasks: [],
      config: { ...config },
      recentToolCalls: [],
      recentWorkflowSteps: []
    };
    this.syncMaxSequenceId(resolvedSnapshot.lastSequenceId);
    return resolvedSnapshot;
  }

  private async applyConfigUpdate(
    event: Extract<ClientEvent, { type: 'CONFIG_UPDATE' }>,
    auth: AuthContext
  ): Promise<readonly ServerEvent[]> {
    if (!isAllowedConfigMutationKey(event.key)) {
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `Config key ${event.key} is outside the bounded V5 write budget.`
      });
      return [
        await this.createRuntimeError(
          'FORBIDDEN',
          `Config key ${event.key} is outside the bounded V5 write budget.`,
          event.requestId,
          auth
        )
      ];
    }

    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));
    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'CONFIG_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        principalId: auth.principalId,
        role: auth.role,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate mutation ignored for ${event.key}.`
      });
      return cachedEvents;
    }

    if (this.runtimeSource.applyConfigUpdate === undefined) {
      return [
        await this.createRuntimeError(
          'NOT_IMPLEMENTED',
          'AdapterGrimoire write path is not wired yet.',
          event.requestId,
          auth
        )
      ];
    }

    let mutationResult: GrimoireRuntimeMutationResult;
    try {
      mutationResult = await this.runtimeSource.applyConfigUpdate({
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        key: event.key,
        value: event.value,
        guardrail: event.guardrail,
        auth
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown runtime write failure.';
      this.recordAudit({
        type: 'ERROR_EMITTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `RUNTIME_WRITE_FAILED: ${detail}`
      });
      return [
        await this.createRuntimeError(
          'RUNTIME_WRITE_FAILED',
          'Runtime source failed to apply config update.',
          event.requestId,
          auth
        )
      ];
    }

    this.syncMaxSequenceId(mutationResult.sequenceId);

    const snapshotEvent = createStateSnapshotEvent(
      mutationResult.sequenceId,
      mutationResult.snapshot,
      mutationResult.timestamp
    );
    const response = [snapshotEvent] as const;
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'CONFIG_APPLIED',
      at: snapshotEvent.timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      principalId: auth.principalId,
      role: auth.role,
      sequenceId: snapshotEvent.sequenceId,
      detail: `Applied config key ${event.key}.`
    });
    return response;
  }

  private async applyTaskTransition(
    event: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>,
    auth: AuthContext
  ): Promise<readonly ServerEvent[]> {
    if (!isAllowedTaskTransitionStatus(event.status)) {
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `Task status ${event.status} is outside the bounded V5 transition budget.`
      });
      return [
        await this.createRuntimeError(
          'FORBIDDEN',
          `Task status ${event.status} is outside the bounded V5 transition budget.`,
          event.requestId,
          auth
        )
      ];
    }

    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));
    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'TASK_TRANSITION_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        principalId: auth.principalId,
        role: auth.role,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate transition ignored for task ${event.taskId}.`
      });
      return cachedEvents;
    }

    const snapshot = await this.buildSnapshot();
    const currentTask = snapshot.tasks.find((task) => task.id === event.taskId);

    if (currentTask === undefined) {
      return [
        await this.createRuntimeError('NOT_FOUND', `Task ${event.taskId} was not found.`, event.requestId, auth)
      ];
    }

    if (!isAllowedTaskStatusTransition(currentTask.status, event.status)) {
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `Task transition ${currentTask.status} -> ${event.status} is outside the bounded V5 transition graph.`
      });
      return [
        await this.createRuntimeError(
          'FORBIDDEN',
          `Task transition ${currentTask.status} -> ${event.status} is outside the bounded V5 transition graph.`,
          event.requestId,
          auth
        )
      ];
    }

    const hydratedState = hydrateGameState(snapshot, snapshot.generatedAt);

    if (event.status === 'review') {
      const reviewGate = evaluateTaskReviewVerificationGate(hydratedState, event.taskId);

      if (reviewGate !== null && reviewGate.isApplicable && !reviewGate.isReadyForReview) {
        const unmetRequirements = reviewGate.unmetRequirementCodes.join(', ');
        const detail =
          unmetRequirements.length === 0
            ? `Task ${event.taskId} cannot transition to review before investigation evidence is complete.`
            : `Task ${event.taskId} cannot transition to review before investigation evidence is complete. Unmet requirements: ${unmetRequirements}.`;

        this.recordAudit({
          type: 'AUTH_REJECTED',
          at: new Date().toISOString(),
          requestId: event.requestId,
          principalId: auth.principalId,
          role: auth.role,
          detail
        });
        return [await this.createRuntimeError('FORBIDDEN', detail, event.requestId, auth)];
      }
    }

    if (event.status === 'done') {
      const verificationGate = evaluateTaskVerificationGate(hydratedState, event.taskId);

      if (verificationGate !== null && !verificationGate.isReadyForDone) {
        const unmetRequirements = verificationGate.unmetRequirementCodes.join(', ');
        const detail =
          unmetRequirements.length === 0
            ? `Task ${event.taskId} cannot transition to done without verification evidence.`
            : `Task ${event.taskId} cannot transition to done without verification evidence. Unmet requirements: ${unmetRequirements}.`;

        this.recordAudit({
          type: 'AUTH_REJECTED',
          at: new Date().toISOString(),
          requestId: event.requestId,
          principalId: auth.principalId,
          role: auth.role,
          detail
        });
        return [await this.createRuntimeError('FORBIDDEN', detail, event.requestId, auth)];
      }
    }

    if (this.runtimeSource.applyTaskTransition === undefined) {
      return [
        await this.createRuntimeError(
          'NOT_IMPLEMENTED',
          'AdapterGrimoire task transition write path is not wired yet.',
          event.requestId,
          auth
        )
      ];
    }

    let mutationResult: GrimoireRuntimeMutationResult;
    try {
      mutationResult = await this.runtimeSource.applyTaskTransition({
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        taskId: event.taskId,
        status: event.status,
        verification: event.verification,
        guardrail: event.guardrail,
        ...(this.taskLeaseContext === undefined ? {} : { leaseContext: this.taskLeaseContext }),
        auth
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown runtime write failure.';
      this.recordAudit({
        type: 'ERROR_EMITTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `RUNTIME_WRITE_FAILED: ${detail}`
      });
      return [
        await this.createRuntimeError(
          'RUNTIME_WRITE_FAILED',
          'Runtime source failed to apply task transition.',
          event.requestId,
          auth
        )
      ];
    }

    this.syncMaxSequenceId(mutationResult.sequenceId);

    const snapshotEvent = createStateSnapshotEvent(
      mutationResult.sequenceId,
      mutationResult.snapshot,
      mutationResult.timestamp
    );
    const response = [snapshotEvent] as const;
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'TASK_TRANSITION_APPLIED',
      at: snapshotEvent.timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      principalId: auth.principalId,
      role: auth.role,
      sequenceId: snapshotEvent.sequenceId,
      detail: `Transitioned task ${event.taskId} to ${event.status}.`
    });
    return response;
  }

  private async applyTaskAssign(
    event: Extract<ClientEvent, { type: 'TASK_ASSIGN' }>,
    auth: AuthContext
  ): Promise<readonly ServerEvent[]> {
    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));
    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'TASK_ASSIGN_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        principalId: auth.principalId,
        role: auth.role,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate assignment ignored for task ${event.taskId}.`
      });
      return cachedEvents;
    }

    const snapshot = await this.buildSnapshot();
    const currentTask = snapshot.tasks.find((task) => task.id === event.taskId);

    if (currentTask === undefined) {
      return [
        await this.createRuntimeError('NOT_FOUND', `Task ${event.taskId} was not found.`, event.requestId, auth)
      ];
    }

    const assignee = snapshot.agents.find((agent) => agent.id === event.assigneeId);
    if (assignee === undefined) {
      return [
        await this.createRuntimeError(
          'NOT_FOUND',
          `Assignee ${event.assigneeId} was not found.`,
          event.requestId,
          auth
        )
      ];
    }

    if (this.runtimeSource.applyTaskAssign === undefined) {
      return [
        await this.createRuntimeError(
          'NOT_IMPLEMENTED',
          'AdapterGrimoire task assign write path is not wired yet.',
          event.requestId,
          auth
        )
      ];
    }

    let mutationResult: GrimoireRuntimeMutationResult;
    try {
      mutationResult = await this.runtimeSource.applyTaskAssign({
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        taskId: event.taskId,
        assigneeId: event.assigneeId,
        guardrail: event.guardrail,
        ...(this.taskLeaseContext === undefined ? {} : { leaseContext: this.taskLeaseContext }),
        auth
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown runtime write failure.';
      this.recordAudit({
        type: 'ERROR_EMITTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `RUNTIME_WRITE_FAILED: ${detail}`
      });
      return [
        await this.createRuntimeError(
          'RUNTIME_WRITE_FAILED',
          'Runtime source failed to assign task.',
          event.requestId,
          auth
        )
      ];
    }

    this.syncMaxSequenceId(mutationResult.sequenceId);

    const snapshotEvent = createStateSnapshotEvent(
      mutationResult.sequenceId,
      mutationResult.snapshot,
      mutationResult.timestamp
    );
    const response = [snapshotEvent] as const;
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'TASK_ASSIGN_APPLIED',
      at: snapshotEvent.timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      principalId: auth.principalId,
      role: auth.role,
      sequenceId: snapshotEvent.sequenceId,
      detail: `Assigned task ${event.taskId} to ${event.assigneeId}.`
    });
    return response;
  }

  private async applyAgentStatusUpdate(
    event: Extract<ClientEvent, { type: 'AGENT_STATUS_UPDATE' }>,
    auth: AuthContext
  ): Promise<readonly ServerEvent[]> {
    if (!isAllowedAgentStatusUpdateStatus(event.status)) {
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `Agent status ${event.status} is outside the bounded V5 pause/resume budget.`
      });
      return [
        await this.createRuntimeError(
          'FORBIDDEN',
          `Agent status ${event.status} is outside the bounded V5 pause/resume budget.`,
          event.requestId,
          auth
        )
      ];
    }

    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));
    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'AGENT_STATUS_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        principalId: auth.principalId,
        role: auth.role,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate agent status update ignored for ${event.agentId}.`
      });
      return cachedEvents;
    }

    const snapshot = await this.buildSnapshot();
    const currentAgent = snapshot.agents.find((agent) => agent.id === event.agentId);

    if (currentAgent === undefined) {
      return [
        await this.createRuntimeError('NOT_FOUND', `Agent ${event.agentId} was not found.`, event.requestId, auth)
      ];
    }

    if (!isAllowedAgentStatusTransition(currentAgent.status, event.status)) {
      this.recordAudit({
        type: 'AUTH_REJECTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `Agent status transition ${currentAgent.status} -> ${event.status} is outside the bounded V5 pause/resume graph.`
      });
      return [
        await this.createRuntimeError(
          'FORBIDDEN',
          `Agent status transition ${currentAgent.status} -> ${event.status} is outside the bounded V5 pause/resume graph.`,
          event.requestId,
          auth
        )
      ];
    }

    if (this.runtimeSource.applyAgentStatusUpdate === undefined) {
      return [
        await this.createRuntimeError(
          'NOT_IMPLEMENTED',
          'AdapterGrimoire agent status write path is not wired yet.',
          event.requestId,
          auth
        )
      ];
    }

    let mutationResult: GrimoireRuntimeMutationResult;
    try {
      mutationResult = await this.runtimeSource.applyAgentStatusUpdate({
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        agentId: event.agentId,
        status: event.status,
        guardrail: event.guardrail,
        auth
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown runtime write failure.';
      this.recordAudit({
        type: 'ERROR_EMITTED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        principalId: auth.principalId,
        role: auth.role,
        detail: `RUNTIME_WRITE_FAILED: ${detail}`
      });
      return [
        await this.createRuntimeError(
          'RUNTIME_WRITE_FAILED',
          'Runtime source failed to update agent status.',
          event.requestId,
          auth
        )
      ];
    }

    this.syncMaxSequenceId(mutationResult.sequenceId);

    const snapshotEvent = createStateSnapshotEvent(
      mutationResult.sequenceId,
      mutationResult.snapshot,
      mutationResult.timestamp
    );
    const response = [snapshotEvent] as const;
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'AGENT_STATUS_APPLIED',
      at: snapshotEvent.timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      principalId: auth.principalId,
      role: auth.role,
      sequenceId: snapshotEvent.sequenceId,
      detail: `Updated agent ${event.agentId} to status ${event.status}.`
    });
    return response;
  }

  getAuditLog(): readonly AdapterAuditLogEntry[] {
    return this.auditLog;
  }

  private async createRuntimeError(
    code: string,
    message: string,
    requestId?: string,
    auth?: AuthContext
  ): Promise<ServerEvent> {
    const snapshot = await this.buildSnapshot();
    const sequenceId = this.allocateSequenceId(snapshot.lastSequenceId);
    const event = createErrorEvent(sequenceId, code, message, requestId);
    this.recordAudit({
      type: 'ERROR_EMITTED',
      at: event.timestamp,
      ...(requestId === undefined ? {} : { requestId }),
      ...(auth === undefined
        ? {}
        : {
            principalId: auth.principalId,
            role: auth.role
          }),
      sequenceId: event.sequenceId,
      detail: `${code}: ${message}`
    });
    return event;
  }

  private allocateSequenceId(snapshotLastSequenceId: number): number {
    this.syncMaxSequenceId(snapshotLastSequenceId);
    this.maxIssuedSequenceId += 1;
    return this.maxIssuedSequenceId;
  }

  private syncMaxSequenceId(sequenceId: number): void {
    this.maxIssuedSequenceId = Math.max(this.maxIssuedSequenceId, sequenceId);
  }

  private recordAudit(entry: AdapterAuditLogEntry): void {
    this.auditLog.push(entry);
  }

  private storeProcessedMutation(
    eventType: ClientEvent['type'],
    idempotencyKey: string,
    response: readonly ServerEvent[]
  ): void {
    setBoundedMutationCacheEntry(
      this.processedMutations,
      mutationCacheKey(eventType, idempotencyKey),
      response,
      this.processedMutationCacheMaxEntries
    );
  }
}

function mutationCacheKey(eventType: ClientEvent['type'], idempotencyKey: string): string {
  return `${eventType}:${idempotencyKey}`;
}