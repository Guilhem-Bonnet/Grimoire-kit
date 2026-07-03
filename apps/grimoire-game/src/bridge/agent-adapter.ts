import {
  createErrorEvent,
  createStateSnapshotEvent,
  createVerificationGateEvent,
  type AgentPresence,
  type ClientEvent,
  type GameStateSnapshot,
  type JsonValue,
  type ServerEvent,
  type VerificationEvidenceRef,
  type VerificationGateEvent
} from '../contracts/events';
import { hydrateGameState } from '../state/game-state';
import { evaluateTaskVerificationGate } from '../state/verification-view';
import type { AuthContext } from '../server/auth/rbac';
import { authorizeClientEvent, createAuthorizationAuditEntry } from '../server/auth/rbac';
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

export interface AgentAdapter {
  readonly source: string;
  getInitialSnapshot(auth: AuthContext): Promise<readonly ServerEvent[]>;
  reconnect(lastSequenceId: number | undefined, auth: AuthContext): Promise<readonly ServerEvent[]>;
  handleClientEvent(event: ClientEvent, auth: AuthContext): Promise<readonly ServerEvent[]>;
}

export interface AdapterAuditLogEntry {
  type:
    | 'SNAPSHOT_SENT'
    | 'REPLAY_SENT'
    | 'CONFIG_APPLIED'
    | 'CONFIG_DEDUPED'
    | 'TASK_TRANSITION_APPLIED'
    | 'TASK_TRANSITION_DEDUPED'
    | 'TASK_ASSIGN_APPLIED'
    | 'TASK_ASSIGN_DEDUPED'
    | 'AGENT_STATUS_APPLIED'
    | 'AGENT_STATUS_DEDUPED'
    | 'AUTH_REJECTED'
    | 'ERROR_EMITTED';
  at: string;
  requestId?: string;
  idempotencyKey?: string;
  principalId?: string;
  role?: AuthContext['role'];
  sequenceId?: number;
  detail?: string;
}

export interface MockAgentAdapterOptions {
  processedMutationCacheMaxEntries?: number;
}

function cloneJsonValue<T extends JsonValue>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function cloneSerializable<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function cloneSnapshot(snapshot: GameStateSnapshot): GameStateSnapshot {
  return {
    ...snapshot,
    agents: snapshot.agents.map((agent) => ({ ...agent, position: { ...agent.position } })),
    tasks: snapshot.tasks.map((task) => ({
      ...task,
      ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
    })),
    recentToolCalls: snapshot.recentToolCalls.map((entry) => ({
      ...entry,
      params: { ...entry.params }
    })),
    recentWorkflowSteps: snapshot.recentWorkflowSteps.map((entry) => ({
      ...entry,
      metadata: { ...entry.metadata }
    })),
    ...(snapshot.hostBindings === undefined ? {} : { hostBindings: cloneSerializable(snapshot.hostBindings) }),
    ...(snapshot.recentHostInvocationDecisions === undefined
      ? {}
      : { recentHostInvocationDecisions: cloneSerializable(snapshot.recentHostInvocationDecisions) }),
    ...(snapshot.recentHostContextEntries === undefined
      ? {}
      : { recentHostContextEntries: cloneSerializable(snapshot.recentHostContextEntries) }),
    ...(snapshot.recentHostReviews === undefined
      ? {}
      : { recentHostReviews: cloneSerializable(snapshot.recentHostReviews) }),
    config: Object.fromEntries(
      Object.entries(snapshot.config).map(([key, value]) => [key, cloneJsonValue(value)])
    )
  };
}

export class MockAgentAdapter implements AgentAdapter {
  readonly source = 'mock';

  private snapshot: GameStateSnapshot;
  private readonly eventLog: ServerEvent[] = [];
  private readonly processedMutations = new Map<string, readonly ServerEvent[]>();
  private readonly auditLog: AdapterAuditLogEntry[] = [];
  private readonly processedMutationCacheMaxEntries: number;
  private nextSequenceId: number;

  constructor(initialSnapshot: GameStateSnapshot, options: MockAgentAdapterOptions = {}) {
    this.snapshot = cloneSnapshot(initialSnapshot);
    this.nextSequenceId = initialSnapshot.lastSequenceId + 1;
    this.processedMutationCacheMaxEntries = normalizeProcessedMutationCacheMaxEntries(
      options.processedMutationCacheMaxEntries
    );
  }

  async getInitialSnapshot(_auth: AuthContext): Promise<readonly ServerEvent[]> {
    const event = createStateSnapshotEvent(this.snapshot.lastSequenceId, this.snapshot);
    this.recordAudit({
      type: 'SNAPSHOT_SENT',
      at: new Date().toISOString(),
      sequenceId: event.sequenceId,
      detail: 'Full snapshot sent.'
    });
    return [event];
  }

  async reconnect(lastSequenceId: number | undefined, auth: AuthContext): Promise<readonly ServerEvent[]> {
    if (lastSequenceId === undefined) {
      return this.getInitialSnapshot(auth);
    }

    const missedEvents = this.eventLog.filter((event) => event.sequenceId > lastSequenceId);

    if (missedEvents.length > 0) {
      const lastReplayedSequenceId = missedEvents[missedEvents.length - 1]?.sequenceId;
      this.recordAudit({
        type: 'REPLAY_SENT',
        at: new Date().toISOString(),
        ...(lastReplayedSequenceId === undefined ? {} : { sequenceId: lastReplayedSequenceId }),
        detail: `Replayed ${missedEvents.length} event(s) after sequence ${lastSequenceId}.`
      });
      return missedEvents;
    }

    if (lastSequenceId < this.snapshot.lastSequenceId) {
      return this.getInitialSnapshot(auth);
    }

    return [];
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
        return [this.recordError('FORBIDDEN', reconnectDecision.reason ?? 'Forbidden.', event.requestId)];
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
      return [this.recordError('FORBIDDEN', reason, event.requestId)];
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
      return [this.recordError('FORBIDDEN', decision.reason ?? 'Forbidden.', event.requestId)];
    }

    if (event.type === 'CONFIG_UPDATE') {
      return this.applyConfigUpdate(event);
    }

    if (event.type === 'TASK_TRANSITION') {
      return this.applyTaskTransition(event, auth);
    }

    if (event.type === 'TASK_ASSIGN') {
      return this.applyTaskAssign(event);
    }

    return this.applyAgentStatusUpdate(event);
  }

  getAuditLog(): readonly AdapterAuditLogEntry[] {
    return this.auditLog;
  }

  emitAgentState(agent: AgentPresence): ServerEvent {
    const sequenceId = this.allocateSequenceId();
    const event = {
      type: 'AGENT_STATE',
      version: this.snapshot.protocolVersion,
      sequenceId,
      timestamp: new Date().toISOString(),
      agent
    } as const;

    this.eventLog.push(event);
    this.snapshot = {
      ...this.snapshot,
      generatedAt: event.timestamp,
      lastSequenceId: sequenceId,
      agents: [...this.snapshot.agents.filter((currentAgent) => currentAgent.id !== agent.id), agent]
    };

    return event;
  }

  private applyConfigUpdate(event: Extract<ClientEvent, { type: 'CONFIG_UPDATE' }>): readonly ServerEvent[] {
    if (!isAllowedConfigMutationKey(event.key)) {
      return [
        this.recordError(
          'FORBIDDEN',
          `Config key ${event.key} is outside the bounded V5 write budget.`,
          event.requestId
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
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate mutation ignored for ${event.key}.`
      });
      return cachedEvents;
    }

    const timestamp = new Date().toISOString();
    const sequenceId = this.allocateSequenceId();
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(this.snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      config: {
        ...this.snapshot.config,
        [event.key]: cloneJsonValue(event.value)
      }
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);
    const response = [snapshotEvent] as const;

    this.eventLog.push(snapshotEvent);
    this.snapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'CONFIG_APPLIED',
      at: timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      sequenceId,
      detail: `Applied config key ${event.key}.`
    });

    return response;
  }

  private applyTaskTransition(
    event: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>,
    auth: AuthContext
  ): readonly ServerEvent[] {
    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));

    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'TASK_TRANSITION_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate transition ignored for task ${event.taskId}.`
      });
      return cachedEvents;
    }

    const currentTask = this.snapshot.tasks.find((task) => task.id === event.taskId);
    if (currentTask === undefined) {
      return [this.recordError('NOT_FOUND', `Task ${event.taskId} was not found.`, event.requestId)];
    }

    if (!isAllowedTaskTransitionStatus(event.status)) {
      return [
        this.recordError(
          'FORBIDDEN',
          `Task status ${event.status} is outside the bounded V5 transition budget.`,
          event.requestId
        )
      ];
    }

    if (!isAllowedTaskStatusTransition(currentTask.status, event.status)) {
      return [
        this.recordError(
          'FORBIDDEN',
          `Task transition ${currentTask.status} -> ${event.status} is outside the bounded V5 transition graph.`,
          event.requestId
        )
      ];
    }

    if (event.status === 'done') {
      const verificationGate = evaluateTaskVerificationGate(
        hydrateGameState(this.snapshot, this.snapshot.generatedAt),
        event.taskId
      );

      if (verificationGate !== null && !verificationGate.isReadyForDone) {
        const unmetRequirements = verificationGate.unmetRequirementCodes.join(', ');
        const detail =
          unmetRequirements.length === 0
            ? `Task ${event.taskId} cannot transition to done without verification evidence.`
            : `Task ${event.taskId} cannot transition to done without verification evidence. Unmet requirements: ${unmetRequirements}.`;

        const timestamp = new Date().toISOString();
        const gateEvent = this.createTaskTransitionVerificationGateEvent(
          event,
          auth,
          'FAIL',
          timestamp,
          verificationGate.unmetRequirementCodes
        );

        if (gateEvent === null) {
          return [this.recordError('FORBIDDEN', detail, event.requestId)];
        }

        this.eventLog.push(gateEvent);
        this.snapshot = this.appendVerificationGateToSnapshot(this.snapshot, gateEvent);

        const errorEvent = this.recordError('FORBIDDEN', detail, event.requestId);
        return [gateEvent, errorEvent];
      }
    }

    const timestamp = new Date().toISOString();
    const gateEvent =
      event.status === 'done'
        ? this.createTaskTransitionVerificationGateEvent(event, auth, 'PASS', timestamp)
        : null;
    const sequenceId = this.allocateSequenceId();
    const snapshotBase = gateEvent === null ? cloneSnapshot(this.snapshot) : this.appendVerificationGateToSnapshot(this.snapshot, gateEvent);
    const nextSnapshot: GameStateSnapshot = {
      ...snapshotBase,
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      tasks: snapshotBase.tasks.map((task) =>
        task.id === event.taskId
          ? {
              ...task,
              status: event.status
            }
          : {
              ...task,
              ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
            }
      )
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);
    const response: readonly ServerEvent[] = gateEvent === null ? [snapshotEvent] : [gateEvent, snapshotEvent];

    if (gateEvent !== null) {
      this.eventLog.push(gateEvent);
    }
    this.eventLog.push(snapshotEvent);
    this.snapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'TASK_TRANSITION_APPLIED',
      at: timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      sequenceId,
      detail: `Transitioned task ${event.taskId} to ${event.status}.`
    });

    return response;
  }

  private applyTaskAssign(event: Extract<ClientEvent, { type: 'TASK_ASSIGN' }>): readonly ServerEvent[] {
    const cachedEvents = this.processedMutations.get(mutationCacheKey(event.type, event.idempotencyKey));

    if (cachedEvents !== undefined) {
      const dedupedSequenceId = cachedEvents[0]?.sequenceId;
      this.recordAudit({
        type: 'TASK_ASSIGN_DEDUPED',
        at: new Date().toISOString(),
        requestId: event.requestId,
        idempotencyKey: event.idempotencyKey,
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate assignment ignored for task ${event.taskId}.`
      });
      return cachedEvents;
    }

    const currentTask = this.snapshot.tasks.find((task) => task.id === event.taskId);
    if (currentTask === undefined) {
      return [this.recordError('NOT_FOUND', `Task ${event.taskId} was not found.`, event.requestId)];
    }

    const assignee = this.snapshot.agents.find((agent) => agent.id === event.assigneeId);
    if (assignee === undefined) {
      return [this.recordError('NOT_FOUND', `Assignee ${event.assigneeId} was not found.`, event.requestId)];
    }

    const timestamp = new Date().toISOString();
    const sequenceId = this.allocateSequenceId();
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(this.snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      tasks: this.snapshot.tasks.map((task) =>
        task.id === event.taskId
          ? {
              ...task,
              assigneeId: assignee.id
            }
          : {
              ...task,
              ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
            }
      )
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);
    const response = [snapshotEvent] as const;

    this.eventLog.push(snapshotEvent);
    this.snapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'TASK_ASSIGN_APPLIED',
      at: timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      sequenceId,
      detail: `Assigned task ${event.taskId} to ${assignee.id}.`
    });

    return response;
  }

  private applyAgentStatusUpdate(
    event: Extract<ClientEvent, { type: 'AGENT_STATUS_UPDATE' }>
  ): readonly ServerEvent[] {
    if (!isAllowedAgentStatusUpdateStatus(event.status)) {
      return [
        this.recordError(
          'FORBIDDEN',
          `Agent status ${event.status} is outside the bounded V5 pause/resume budget.`,
          event.requestId
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
        ...(dedupedSequenceId === undefined ? {} : { sequenceId: dedupedSequenceId }),
        detail: `Duplicate agent status update ignored for ${event.agentId}.`
      });
      return cachedEvents;
    }

    const currentAgent = this.snapshot.agents.find((agent) => agent.id === event.agentId);
    if (currentAgent === undefined) {
      return [this.recordError('NOT_FOUND', `Agent ${event.agentId} was not found.`, event.requestId)];
    }

    if (!isAllowedAgentStatusTransition(currentAgent.status, event.status)) {
      return [
        this.recordError(
          'FORBIDDEN',
          `Agent status transition ${currentAgent.status} -> ${event.status} is outside the bounded V5 pause/resume graph.`,
          event.requestId
        )
      ];
    }

    const timestamp = new Date().toISOString();
    const sequenceId = this.allocateSequenceId();
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(this.snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      agents: this.snapshot.agents.map((agent) =>
        agent.id === event.agentId
          ? {
              ...agent,
              status: event.status
            }
          : { ...agent, position: { ...agent.position } }
      )
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);
    const response = [snapshotEvent] as const;

    this.eventLog.push(snapshotEvent);
    this.snapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.storeProcessedMutation(event.type, event.idempotencyKey, response);
    this.recordAudit({
      type: 'AGENT_STATUS_APPLIED',
      at: timestamp,
      requestId: event.requestId,
      idempotencyKey: event.idempotencyKey,
      sequenceId,
      detail: `Updated agent ${event.agentId} to status ${event.status}.`
    });

    return response;
  }

  private allocateSequenceId(): number {
    const currentSequenceId = this.nextSequenceId;
    this.nextSequenceId += 1;
    return currentSequenceId;
  }

  private createTaskTransitionVerificationGateEvent(
    event: Extract<ClientEvent, { type: 'TASK_TRANSITION' }>,
    auth: AuthContext,
    result: 'PASS' | 'FAIL',
    timestamp: string,
    unmetControls: readonly string[] = []
  ): VerificationGateEvent | null {
    if (event.status !== 'done' || event.verification === undefined) {
      return null;
    }

    return createVerificationGateEvent(
      this.allocateSequenceId(),
      {
        result,
        actionId: event.verification.actionId,
        verificationRef: event.verification.verificationRef,
        evidenceRefs: event.verification.evidenceRefs.map(toVerificationEvidenceRef),
        controlsExecuted: [...event.verification.controlsExecuted],
        ...(unmetControls.length === 0 ? {} : { unmetControls: [...unmetControls] }),
        ...(event.verification.traceId === undefined ? {} : { traceId: event.verification.traceId }),
        taskId: event.taskId,
        meta: {
          actorId: auth.principalId,
          actorRole: auth.role,
          correlationId: event.requestId,
          requestId: event.verification.requestId ?? event.requestId,
          idempotencyKey: event.verification.idempotencyKey ?? event.idempotencyKey,
          sourceEventType: event.type.toLowerCase()
        }
      },
      { timestamp }
    );
  }

  private appendVerificationGateToSnapshot(
    snapshot: GameStateSnapshot,
    gateEvent: VerificationGateEvent
  ): GameStateSnapshot {
    const nextSnapshot = cloneSnapshot(snapshot);

    return {
      ...nextSnapshot,
      generatedAt: gateEvent.timestamp,
      lastSequenceId: gateEvent.sequenceId,
      recentWorkflowSteps: [
        ...nextSnapshot.recentWorkflowSteps,
        {
          step: `Verification gate ${gateEvent.result}`,
          detail: `${gateEvent.actionId}: ${gateEvent.verificationRef}`,
          sourceEventType: 'verification_gate',
          ...(gateEvent.traceId === undefined ? {} : { traceId: gateEvent.traceId }),
          ...(gateEvent.taskId === undefined ? {} : { taskId: gateEvent.taskId }),
          metadata: {
            ...gateEvent.meta,
            actionId: gateEvent.actionId,
            verificationRef: gateEvent.verificationRef,
            controlsExecuted: [...gateEvent.controlsExecuted],
            evidenceRefs: gateEvent.evidenceRefs.map((evidenceRef) => evidenceRef.ref),
            typedEvidenceRefs: gateEvent.evidenceRefs.map((evidenceRef) => ({
              kind: evidenceRef.kind,
              ref: evidenceRef.ref
            })),
            verdict: gateEvent.result,
            ...(gateEvent.unmetControls.length === 0 ? {} : { unmetControls: [...gateEvent.unmetControls] })
          },
          sequenceId: gateEvent.sequenceId,
          timestamp: gateEvent.timestamp
        }
      ].slice(-100)
    };
  }

  private recordError(code: string, message: string, correlationId?: string): ServerEvent {
    const event = createErrorEvent(this.allocateSequenceId(), code, message, correlationId);
    this.eventLog.push(event);
    this.snapshot = {
      ...this.snapshot,
      generatedAt: event.timestamp,
      lastSequenceId: event.sequenceId
    };
    this.recordAudit({
      type: 'ERROR_EMITTED',
      at: event.timestamp,
      ...(correlationId === undefined ? {} : { requestId: correlationId }),
      sequenceId: event.sequenceId,
      detail: `${code}: ${message}`
    });
    return event;
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

function toVerificationEvidenceRef(ref: string): VerificationEvidenceRef {
  return {
    kind: inferVerificationEvidenceKind(ref),
    ref
  };
}

function inferVerificationEvidenceKind(ref: string): VerificationEvidenceRef['kind'] {
  const normalized = ref.trim().toLowerCase();

  if (normalized.startsWith('tests://')) {
    return 'test';
  }

  if (normalized.startsWith('log://')) {
    return 'log';
  }

  if (normalized.startsWith('coverage://')) {
    return 'coverage';
  }

  if (normalized.startsWith('screenshot://')) {
    return 'screenshot';
  }

  return 'artifact';
}