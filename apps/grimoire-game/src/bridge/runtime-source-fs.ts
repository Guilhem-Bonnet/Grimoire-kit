import { readFile } from 'node:fs/promises';

import {
  createAgentStateEvent,
  createStateSnapshotEvent,
  createToolCallEvent,
  createTaskUpdateEvent,
  createWorkflowStepEvent,
  type GameStateSnapshot,
  RUNTIME_PROTOCOL_VERSION,
  type AgentPresence,
  type JsonValue,
  type ToolCallLogEntry,
  type ToolCallPayload,
  type TaskSnapshot,
  type TaskStatus,
  type WorkflowStepLogEntry,
  type WorkflowStepPayload,
  type ServerEvent
} from '../contracts/events';
import { hydrateGameState } from '../state/game-state';
import { evaluateTaskReviewVerificationGate, evaluateTaskVerificationGate } from '../state/verification-view';
import { LeaseStore } from '../server/control-plane/lease-store';
import type {
  GrimoireAgentRecord,
  GrimoireRuntimeAgentStatusMutation,
  GrimoireRuntimeConfigMutation,
  GrimoireRuntimeLeaseContext,
  GrimoireRuntimeMutationResult,
  GrimoireRuntimeTaskAssignMutation,
  GrimoireRuntimeTaskMutation,
  GrimoireRuntimeSource
} from './adapter-grimoire';
import {
  DEFAULT_CONNECTION_STALE_AFTER_MS,
  createAgentConnectionHealthSnapshot,
  serializeAgentConnectionHealthToConfig
} from './agent-connection-health';
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

interface EventLogEntry {
  id: string;
  ts: string;
  agent: string;
  type: string;
  payload?: Record<string, unknown>;
  trace_id?: string;
  seq: number;
  tags?: string[];
}

interface RuntimeProjection {
  readonly agents: Map<string, GrimoireAgentRecord>;
  readonly tasks: Map<string, TaskSnapshot>;
  lastSequenceId: number;
  generatedAt: string;
  readonly config: Record<string, JsonValue>;
  recentToolCalls: ToolCallLogEntry[];
  recentWorkflowSteps: WorkflowStepLogEntry[];
}

interface EventLogReadResult {
  entries: EventLogEntry[];
  found: boolean;
  scannedAt: string;
}

type RuntimeMutationType =
  | 'CONFIG_UPDATE'
  | 'TASK_TRANSITION'
  | 'TASK_ASSIGN'
  | 'AGENT_STATUS_UPDATE';

export interface FileSystemConnectionHealthOptions {
  enabled?: boolean;
  staleAfterMs?: number;
}

export interface FileSystemGrimoireRuntimeSourceOptions {
  eventLogPath: string;
  initialConfig?: Record<string, JsonValue>;
  connectionHealth?: FileSystemConnectionHealthOptions;
  processedMutationCacheMaxEntries?: number;
  leaseStore?: LeaseStore;
}

const BASE_POSITION_BY_KIND: Record<string, { x: number; y: number; roomId: string }> = {
  orchestrator: { x: 4, y: 4, roomId: 'war-room' },
  architect: { x: 8, y: 4, roomId: 'design-room' },
  dev: { x: 8, y: 8, roomId: 'build-room' },
  qa: { x: 10, y: 8, roomId: 'qa-room' },
  analyst: { x: 2, y: 6, roomId: 'research-room' },
  pm: { x: 2, y: 4, roomId: 'vision-room' },
  sm: { x: 4, y: 8, roomId: 'ops-room' },
  'tech-writer': { x: 2, y: 10, roomId: 'library-room' }
};

const WORKING_EVENT_TYPES = new Set([
  'routing',
  'task_started',
  'decision',
  'security_finding',
  'branch_finish_options',
  'branch_finish_decision',
  'branch_finish_result',
  'trust_scored',
  'artifact_created',
  'cross_validation',
  'debate',
  'graph_update'
]);

const TASK_EVENT_TYPES = new Set(['task_started', 'task_completed']);
const MAX_RECENT_TOOL_CALLS = 50;
const MAX_RECENT_WORKFLOW_STEPS = 100;

export class FileSystemGrimoireRuntimeSource implements GrimoireRuntimeSource {
  private readonly eventLogPath: string;
  private readonly initialConfig: Record<string, JsonValue>;
  private readonly connectionHealthEnabled: boolean;
  private readonly connectionHealthStaleAfterMs: number;
  private readonly processedMutationCacheMaxEntries: number;
  private readonly leaseStore: LeaseStore | undefined;
  private readonly processedMutations = new Map<string, GrimoireRuntimeMutationResult>();
  private runtimeSnapshot?: GameStateSnapshot;
  private readonly runtimeEvents: ServerEvent[] = [];

  constructor(options: FileSystemGrimoireRuntimeSourceOptions) {
    this.eventLogPath = options.eventLogPath;
    this.initialConfig = { ...(options.initialConfig ?? {}) };
    this.connectionHealthEnabled = options.connectionHealth?.enabled ?? true;
    this.connectionHealthStaleAfterMs = normalizePositiveNumber(
      options.connectionHealth?.staleAfterMs,
      DEFAULT_CONNECTION_STALE_AFTER_MS
    );
    this.processedMutationCacheMaxEntries = normalizeProcessedMutationCacheMaxEntries(
      options.processedMutationCacheMaxEntries
    );
    if (options.leaseStore !== undefined) {
      this.leaseStore = options.leaseStore;
    }
  }

  async readAgents(): Promise<readonly GrimoireAgentRecord[]> {
    const snapshot = await this.readSnapshot();
    return snapshot.agents.map((agent) => ({
      ...agent,
      position: { ...agent.position }
    }));
  }

  async readConfig(): Promise<Record<string, JsonValue>> {
    const snapshot = await this.readSnapshot();
    return Object.fromEntries(
      Object.entries(snapshot.config ?? {}).map(([key, value]) => [key, cloneJsonValue(value)])
    );
  }

  async readSnapshot(): Promise<{
    generatedAt?: string;
    lastSequenceId?: number;
    agents: readonly GrimoireAgentRecord[];
    tasks?: TaskSnapshot[];
    config?: Record<string, JsonValue>;
    recentToolCalls?: ToolCallLogEntry[];
    recentWorkflowSteps?: WorkflowStepLogEntry[];
    hostBindings?: GameStateSnapshot['hostBindings'];
    recentHostInvocationDecisions?: GameStateSnapshot['recentHostInvocationDecisions'];
    recentHostContextEntries?: GameStateSnapshot['recentHostContextEntries'];
    recentHostReviews?: GameStateSnapshot['recentHostReviews'];
  }> {
    if (this.runtimeSnapshot !== undefined) {
      const snapshot = cloneSnapshot(this.runtimeSnapshot);
      if (!this.connectionHealthEnabled) {
        return snapshot;
      }

      const connectionHealthConfig = await this.readConnectionHealthConfig(
        snapshot.agents.map((agent) => agent.id)
      );
      return {
        ...snapshot,
        config: {
          ...snapshot.config,
          ...connectionHealthConfig
        }
      };
    }

    const eventLog = await this.readEventLog();
    const projection = buildProjectionFromEntries(eventLog.entries, this.initialConfig);
    const connectionHealthConfig =
      this.connectionHealthEnabled
        ? this.toConnectionHealthConfig(eventLog, Array.from(projection.agents.keys()))
        : {};

    return {
      generatedAt: projection.generatedAt,
      lastSequenceId: projection.lastSequenceId,
      agents: Array.from(projection.agents.values()),
      tasks: Array.from(projection.tasks.values()),
      config: {
        ...projection.config,
        ...connectionHealthConfig
      },
      recentToolCalls: projection.recentToolCalls.map((entry) => ({
        ...entry,
        params: { ...entry.params }
      })),
      recentWorkflowSteps: projection.recentWorkflowSteps.map((entry) => ({
        ...entry,
        metadata: { ...entry.metadata }
      }))
    };
  }

  async readEventsSince(lastSequenceId: number): Promise<readonly ServerEvent[]> {
    const eventLog = await this.readEventLog();
    const projection = buildProjectionFromEntries(eventLog.entries, this.initialConfig);
    let replayRequiresSnapshot = false;
    const replayEvents: ServerEvent[] = [];

    for (const entry of eventLog.entries) {
      const outcome = applyEntryToProjection(projection, entry);

      if (entry.seq <= lastSequenceId) {
        continue;
      }

      if (outcome.taskChanged !== undefined) {
        replayEvents.push(
          createTaskUpdateEvent(entry.seq, outcome.taskChanged, {
            timestamp: entry.ts,
            ...(outcome.agentChanged === undefined ? {} : { agent: toAgentPresence(outcome.agentChanged) })
          })
        );
        continue;
      }

      if (outcome.workflowStep !== undefined) {
        replayEvents.push(
          createWorkflowStepEvent(entry.seq, outcome.workflowStep, {
            timestamp: entry.ts,
            ...(outcome.agentChanged === undefined ? {} : { agent: toAgentPresence(outcome.agentChanged) })
          })
        );
        continue;
      }

      if (outcome.toolCall !== undefined) {
        replayEvents.push(
          createToolCallEvent(entry.seq, outcome.toolCall, {
            timestamp: entry.ts,
            ...(outcome.agentChanged === undefined ? {} : { agent: toAgentPresence(outcome.agentChanged) })
          })
        );
        continue;
      }

      if (outcome.agentChanged !== undefined) {
        replayEvents.push(createAgentStateEvent(entry.seq, toAgentPresence(outcome.agentChanged), entry.ts));
        continue;
      }

      replayRequiresSnapshot = true;
    }

    let replayedPersistedEvents: readonly ServerEvent[] = replayEvents;

    if (replayRequiresSnapshot && projection.lastSequenceId > lastSequenceId) {
      replayedPersistedEvents = [
        createStateSnapshotEvent(
          projection.lastSequenceId,
          {
            protocolVersion: RUNTIME_PROTOCOL_VERSION,
            generatedAt: projection.generatedAt,
            lastSequenceId: projection.lastSequenceId,
            agents: Array.from(projection.agents.values()).map(toAgentPresence),
            tasks: Array.from(projection.tasks.values()),
            config: { ...projection.config },
            recentToolCalls: projection.recentToolCalls.map((entry) => ({
              ...entry,
              params: { ...entry.params }
            })),
            recentWorkflowSteps: projection.recentWorkflowSteps.map((entry) => ({
              ...entry,
              metadata: { ...entry.metadata }
            }))
          },
          projection.generatedAt
        )
      ];
    }

    const runtimeEvents = this.runtimeEvents.filter((event) => event.sequenceId > lastSequenceId);
    if (runtimeEvents.length === 0) {
      return replayedPersistedEvents;
    }

    return [...replayedPersistedEvents, ...runtimeEvents].sort((left, right) => left.sequenceId - right.sequenceId);
  }

  async applyConfigUpdate(
    mutation: GrimoireRuntimeConfigMutation
  ): Promise<GrimoireRuntimeMutationResult> {
    ensureOrchestratorWriteRole(mutation.auth.role, 'CONFIG_UPDATE');
    ensureMutationIdentity('CONFIG_UPDATE', mutation.requestId, mutation.idempotencyKey);

    if (!isAllowedConfigMutationKey(mutation.key)) {
      throw new Error(`Config key ${mutation.key} is outside the bounded V5 write budget.`);
    }

    const cachedResult = this.readCachedMutationResult('CONFIG_UPDATE', mutation.idempotencyKey);
    if (cachedResult !== undefined) {
      return cachedResult;
    }

    const snapshot = await this.ensureRuntimeSnapshot();
    const connectionHealthConfig = await this.readConnectionHealthConfig(
      snapshot.agents.map((agent) => agent.id)
    );
    const timestamp = new Date().toISOString();
    const sequenceId = snapshot.lastSequenceId + 1;
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      config: {
        ...snapshot.config,
        [mutation.key]: cloneJsonValue(mutation.value),
        ...connectionHealthConfig
      }
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);

    this.runtimeSnapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.runtimeEvents.push(snapshotEvent);

    const mutationResult = {
      sequenceId,
      snapshot: cloneSnapshot(snapshotEvent.snapshot),
      timestamp
    };

    this.cacheMutationResult('CONFIG_UPDATE', mutation.idempotencyKey, mutationResult);
    return mutationResult;
  }

  async applyTaskTransition(
    mutation: GrimoireRuntimeTaskMutation
  ): Promise<GrimoireRuntimeMutationResult> {
    ensureOrchestratorWriteRole(mutation.auth.role, 'TASK_TRANSITION');
    ensureMutationIdentity('TASK_TRANSITION', mutation.requestId, mutation.idempotencyKey);

    if (!isAllowedTaskTransitionStatus(mutation.status)) {
      throw new Error(`Task status ${mutation.status} is outside the bounded V5 transition budget.`);
    }

    const cachedResult = this.readCachedMutationResult('TASK_TRANSITION', mutation.idempotencyKey);
    if (cachedResult !== undefined) {
      return cachedResult;
    }

    const snapshot = await this.ensureRuntimeSnapshot();
    const timestamp = new Date().toISOString();
    assertTaskLeaseOwnership(this.leaseStore, mutation.taskId, mutation.leaseContext, timestamp);
    const currentTask = snapshot.tasks.find((task) => task.id === mutation.taskId);

    if (currentTask === undefined) {
      throw new Error(`Task ${mutation.taskId} is not present in runtime snapshot.`);
    }

    if (!isAllowedTaskStatusTransition(currentTask.status, mutation.status)) {
      throw new Error(
        `Task transition ${currentTask.status} -> ${mutation.status} is outside the bounded V5 transition graph.`
      );
    }

    const hydratedState = hydrateGameState(snapshot, snapshot.generatedAt);

    if (mutation.status === 'review') {
      const reviewGate = evaluateTaskReviewVerificationGate(hydratedState, mutation.taskId);

      if (reviewGate !== null && reviewGate.isApplicable && !reviewGate.isReadyForReview) {
        const unmetRequirements = reviewGate.unmetRequirementCodes.join(', ');
        if (unmetRequirements.length > 0) {
          throw new Error(
            `Task ${mutation.taskId} cannot transition to review before investigation evidence is complete. Unmet requirements: ${unmetRequirements}.`
          );
        }

        throw new Error(
          `Task ${mutation.taskId} cannot transition to review before investigation evidence is complete.`
        );
      }
    }

    if (mutation.status === 'done') {
      const verificationGate = evaluateTaskVerificationGate(hydratedState, mutation.taskId);

      if (verificationGate !== null && !verificationGate.isReadyForDone) {
        const unmetRequirements = verificationGate.unmetRequirementCodes.join(', ');
        if (unmetRequirements.length > 0) {
          throw new Error(
            `Task ${mutation.taskId} cannot transition to done without verification evidence. Unmet requirements: ${unmetRequirements}.`
          );
        }

        throw new Error(`Task ${mutation.taskId} cannot transition to done without verification evidence.`);
      }
    }

    const sequenceId = snapshot.lastSequenceId + 1;
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      tasks: snapshot.tasks.map((task) =>
        task.id === mutation.taskId
          ? {
              ...task,
              status: mutation.status
            }
          : {
              ...task,
              ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
            }
      )
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);

    this.runtimeSnapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.runtimeEvents.push(snapshotEvent);

    const mutationResult = {
      sequenceId,
      snapshot: cloneSnapshot(snapshotEvent.snapshot),
      timestamp
    };

    this.cacheMutationResult('TASK_TRANSITION', mutation.idempotencyKey, mutationResult);
    return mutationResult;
  }

  async applyTaskAssign(
    mutation: GrimoireRuntimeTaskAssignMutation
  ): Promise<GrimoireRuntimeMutationResult> {
    ensureOrchestratorWriteRole(mutation.auth.role, 'TASK_ASSIGN');
    ensureMutationIdentity('TASK_ASSIGN', mutation.requestId, mutation.idempotencyKey);

    const cachedResult = this.readCachedMutationResult('TASK_ASSIGN', mutation.idempotencyKey);
    if (cachedResult !== undefined) {
      return cachedResult;
    }

    const snapshot = await this.ensureRuntimeSnapshot();
    const timestamp = new Date().toISOString();
    assertTaskLeaseOwnership(this.leaseStore, mutation.taskId, mutation.leaseContext, timestamp);
    const currentTask = snapshot.tasks.find((task) => task.id === mutation.taskId);

    if (currentTask === undefined) {
      throw new Error(`Task ${mutation.taskId} is not present in runtime snapshot.`);
    }

    const assignee = snapshot.agents.find((agent) => agent.id === mutation.assigneeId);
    if (assignee === undefined) {
      throw new Error(`Assignee ${mutation.assigneeId} is not present in runtime snapshot.`);
    }

    const sequenceId = snapshot.lastSequenceId + 1;
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      tasks: snapshot.tasks.map((task) =>
        task.id === mutation.taskId
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

    this.runtimeSnapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.runtimeEvents.push(snapshotEvent);

    const mutationResult = {
      sequenceId,
      snapshot: cloneSnapshot(snapshotEvent.snapshot),
      timestamp
    };

    this.cacheMutationResult('TASK_ASSIGN', mutation.idempotencyKey, mutationResult);
    return mutationResult;
  }

  async applyAgentStatusUpdate(
    mutation: GrimoireRuntimeAgentStatusMutation
  ): Promise<GrimoireRuntimeMutationResult> {
    ensureOrchestratorWriteRole(mutation.auth.role, 'AGENT_STATUS_UPDATE');
    ensureMutationIdentity('AGENT_STATUS_UPDATE', mutation.requestId, mutation.idempotencyKey);

    if (!isAllowedAgentStatusUpdateStatus(mutation.status)) {
      throw new Error(
        `Agent status ${mutation.status} is outside the bounded V5 pause/resume budget.`
      );
    }

    const cachedResult = this.readCachedMutationResult('AGENT_STATUS_UPDATE', mutation.idempotencyKey);
    if (cachedResult !== undefined) {
      return cachedResult;
    }

    const snapshot = await this.ensureRuntimeSnapshot();
    const currentAgent = snapshot.agents.find((agent) => agent.id === mutation.agentId);

    if (currentAgent === undefined) {
      throw new Error(`Agent ${mutation.agentId} is not present in runtime snapshot.`);
    }

    if (!isAllowedAgentStatusTransition(currentAgent.status, mutation.status)) {
      throw new Error(
        `Agent status transition ${currentAgent.status} -> ${mutation.status} is outside the bounded V5 pause/resume graph.`
      );
    }

    const timestamp = new Date().toISOString();
    const sequenceId = snapshot.lastSequenceId + 1;
    const nextSnapshot: GameStateSnapshot = {
      ...cloneSnapshot(snapshot),
      generatedAt: timestamp,
      lastSequenceId: sequenceId,
      agents: snapshot.agents.map((agent) =>
        agent.id === mutation.agentId
          ? {
              ...agent,
              status: mutation.status
            }
          : {
              ...agent,
              position: { ...agent.position }
            }
      )
    };
    const snapshotEvent = createStateSnapshotEvent(sequenceId, nextSnapshot, timestamp);

    this.runtimeSnapshot = cloneSnapshot(snapshotEvent.snapshot);
    this.runtimeEvents.push(snapshotEvent);

    const mutationResult = {
      sequenceId,
      snapshot: cloneSnapshot(snapshotEvent.snapshot),
      timestamp
    };

    this.cacheMutationResult('AGENT_STATUS_UPDATE', mutation.idempotencyKey, mutationResult);
    return mutationResult;
  }

  private readCachedMutationResult(
    mutationType: RuntimeMutationType,
    idempotencyKey: string
  ): GrimoireRuntimeMutationResult | undefined {
    const cachedResult = this.processedMutations.get(mutationCacheKey(mutationType, idempotencyKey));
    if (cachedResult === undefined) {
      return undefined;
    }

    return {
      sequenceId: cachedResult.sequenceId,
      snapshot: cloneSnapshot(cachedResult.snapshot),
      ...(cachedResult.timestamp === undefined ? {} : { timestamp: cachedResult.timestamp })
    };
  }

  private cacheMutationResult(
    mutationType: RuntimeMutationType,
    idempotencyKey: string,
    mutationResult: GrimoireRuntimeMutationResult
  ): void {
    setBoundedMutationCacheEntry(
      this.processedMutations,
      mutationCacheKey(mutationType, idempotencyKey),
      {
        sequenceId: mutationResult.sequenceId,
        snapshot: cloneSnapshot(mutationResult.snapshot),
        ...(mutationResult.timestamp === undefined ? {} : { timestamp: mutationResult.timestamp })
      },
      this.processedMutationCacheMaxEntries
    );
  }

  private async ensureRuntimeSnapshot(): Promise<GameStateSnapshot> {
    if (this.runtimeSnapshot !== undefined) {
      return this.runtimeSnapshot;
    }

    const snapshot = await this.readSnapshot();
    this.runtimeSnapshot = {
      protocolVersion: RUNTIME_PROTOCOL_VERSION,
      generatedAt: snapshot.generatedAt ?? new Date().toISOString(),
      lastSequenceId: snapshot.lastSequenceId ?? 0,
      agents: snapshot.agents.map((agent) => ({
        ...agent,
        position: { ...agent.position }
      })),
      tasks: (snapshot.tasks ?? []).map((task) => ({
        ...task,
        ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
      })),
      config: Object.fromEntries(
        Object.entries(snapshot.config ?? {}).map(([key, value]) => [key, cloneJsonValue(value)])
      ),
      recentToolCalls: (snapshot.recentToolCalls ?? []).map((entry) => ({
        ...entry,
        params: { ...entry.params }
      })),
      recentWorkflowSteps: (snapshot.recentWorkflowSteps ?? []).map((entry) => ({
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
        : { recentHostReviews: cloneSerializable(snapshot.recentHostReviews) })
    };
    return this.runtimeSnapshot;
  }

  private async readConnectionHealthConfig(knownAgentIds: readonly string[]): Promise<Record<string, JsonValue>> {
    if (!this.connectionHealthEnabled) {
      return {};
    }

    const eventLog = await this.readEventLog();
    return this.toConnectionHealthConfig(eventLog, knownAgentIds);
  }

  private toConnectionHealthConfig(
    eventLog: EventLogReadResult,
    knownAgentIds: readonly string[]
  ): Record<string, JsonValue> {
    const connectionHealth = createAgentConnectionHealthSnapshot(
      eventLog.entries.map((entry) => ({
        agentId: parseAgentIdentity(entry.agent).id,
        timestamp: entry.ts
      })),
      {
        found: eventLog.found,
        path: this.eventLogPath,
        scannedAt: eventLog.scannedAt,
        staleAfterMs: this.connectionHealthStaleAfterMs,
        knownAgentIds
      }
    );

    return serializeAgentConnectionHealthToConfig(connectionHealth);
  }

  private async readEventLog(): Promise<EventLogReadResult> {
    const scannedAt = new Date().toISOString();
    let content: string;

    try {
      content = await readFile(this.eventLogPath, 'utf8');
    } catch (error) {
      if (isFileNotFoundError(error)) {
        return {
          entries: [],
          found: false,
          scannedAt
        };
      }

      throw error;
    }

    const entries = content
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map(parseEventLogEntry)
      .sort((left, right) => left.seq - right.seq);

    return {
      entries,
      found: true,
      scannedAt
    };
  }
}

function buildProjectionFromEntries(
  entries: readonly EventLogEntry[],
  initialConfig: Record<string, JsonValue>
): RuntimeProjection {
  const projection = createEmptyProjection(initialConfig);

  for (const entry of entries) {
    applyEntryToProjection(projection, entry);
  }

  return projection;
}

function createEmptyProjection(initialConfig: Record<string, JsonValue>): RuntimeProjection {
  return {
    agents: new Map<string, GrimoireAgentRecord>(),
    tasks: new Map<string, TaskSnapshot>(),
    lastSequenceId: 0,
    generatedAt: new Date(0).toISOString(),
    config: { ...initialConfig },
    recentToolCalls: [],
    recentWorkflowSteps: []
  };
}

function parseEventLogEntry(line: string): EventLogEntry {
  const parsed = JSON.parse(line) as EventLogEntry;

  if (typeof parsed.agent !== 'string' || typeof parsed.type !== 'string' || typeof parsed.ts !== 'string') {
    throw new Error(`Invalid event log entry: ${line}`);
  }

  return parsed;
}

function applyEntryToProjection(
  projection: RuntimeProjection,
  entry: EventLogEntry
): {
  agentChanged?: GrimoireAgentRecord;
  taskChanged?: TaskSnapshot;
  toolCall?: ToolCallPayload;
  workflowStep?: WorkflowStepPayload;
} {
  projection.lastSequenceId = entry.seq;
  projection.generatedAt = entry.ts;

  const identity = parseAgentIdentity(entry.agent);
  const currentAgent = ensureAgentRecord(projection.agents, identity);
  const toolCall = inferToolCall(entry);
  const workflowStep = inferWorkflowStep(entry);
  const nextAgent = {
    ...currentAgent,
    status: WORKING_EVENT_TYPES.has(entry.type) ? 'working' : 'idle',
    ...(toolCall === undefined ? {} : { lastTool: toolCall.tool })
  } satisfies GrimoireAgentRecord;

  projection.agents.set(nextAgent.id, nextAgent);
  const taskChanged = applyTaskUpdate(projection.tasks, nextAgent.id, entry);

  if (toolCall !== undefined) {
    projection.recentToolCalls = appendRecent(
      projection.recentToolCalls,
      {
        ...toolCall,
        sequenceId: entry.seq,
        timestamp: entry.ts,
        agentId: nextAgent.id
      },
      MAX_RECENT_TOOL_CALLS
    );
  }

  if (workflowStep !== undefined) {
    projection.recentWorkflowSteps = appendRecent(
      projection.recentWorkflowSteps,
      {
        ...workflowStep,
        sequenceId: entry.seq,
        timestamp: entry.ts,
        agentId: nextAgent.id
      },
      MAX_RECENT_WORKFLOW_STEPS
    );
  }

  return {
    agentChanged: nextAgent,
    ...(taskChanged === undefined ? {} : { taskChanged }),
    ...(toolCall === undefined ? {} : { toolCall }),
    ...(workflowStep === undefined ? {} : { workflowStep })
  };
}

function appendRecent<T>(items: readonly T[], nextItem: T, limit: number): T[] {
  return [...items, nextItem].slice(-limit);
}

function cloneJsonValue<T extends JsonValue>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function cloneSerializable<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isFileNotFoundError(error: unknown): error is NodeJS.ErrnoException {
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    (error as NodeJS.ErrnoException).code === 'ENOENT'
  );
}

function normalizePositiveNumber(value: number | undefined, fallback: number): number {
  if (value === undefined || !Number.isFinite(value)) {
    return fallback;
  }

  const normalized = Math.trunc(value);
  return normalized > 0 ? normalized : fallback;
}

function cloneSnapshot(snapshot: GameStateSnapshot): GameStateSnapshot {
  return {
    ...snapshot,
    agents: snapshot.agents.map((agent) => ({
      ...agent,
      position: { ...agent.position }
    })),
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
      Object.entries(snapshot.config).map(([key, value]) => [key, cloneJsonValue(value as JsonValue)])
    )
  };
}

function parseAgentIdentity(rawAgent: string): { kind: string; id: string; name: string; roomId: string; position: { x: number; y: number } } {
  const [kindPart, namePart] = rawAgent.includes('/') ? rawAgent.split('/') : [rawAgent, rawAgent];
  const kind = normalizeKind(kindPart ?? 'agent');
  const name = (namePart ?? kindPart ?? 'Agent').trim();
  const key = `${kind}-${name}`.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const base = BASE_POSITION_BY_KIND[kind] ?? { x: 6, y: 6, roomId: 'commons' };

  return {
    kind,
    id: key,
    name,
    roomId: base.roomId,
    position: { x: base.x, y: base.y }
  };
}

function normalizeKind(kind: string): string {
  const normalized = kind.trim().toLowerCase();
  if (normalized === 'orchestrator') {
    return 'orchestrator';
  }
  return normalized.length === 0 ? 'agent' : normalized;
}

function ensureAgentRecord(
  agents: Map<string, GrimoireAgentRecord>,
  identity: ReturnType<typeof parseAgentIdentity>
): GrimoireAgentRecord {
  const existing = agents.get(identity.id);
  if (existing !== undefined) {
    return existing;
  }

  const created: GrimoireAgentRecord = {
    id: identity.id,
    name: identity.name,
    role: identity.kind === 'orchestrator' ? 'orchestrator' : 'agent',
    status: 'idle',
    roomId: identity.roomId,
    position: identity.position
  };
  agents.set(created.id, created);
  return created;
}

function applyTaskUpdate(
  tasks: Map<string, TaskSnapshot>,
  assigneeId: string,
  entry: EventLogEntry
): TaskSnapshot | undefined {
  if (!TASK_EVENT_TYPES.has(entry.type)) {
    return undefined;
  }

  const taskId = readTaskIdentifier(entry.payload);
  if (taskId === undefined) {
    return undefined;
  }

  const title = typeof entry.payload?.description === 'string' ? entry.payload.description : taskId;
  const status: TaskStatus = entry.type === 'task_completed' ? 'done' : 'in_progress';
  const nextTask: TaskSnapshot = {
    id: taskId,
    title,
    status,
    assigneeId
  };

  tasks.set(taskId, nextTask);
  return nextTask;
}

function inferToolCall(entry: EventLogEntry): ToolCallPayload | undefined {
  const traceId = readTraceIdentifier(entry);
  const explicitTool = typeof entry.payload?.tool === 'string' ? entry.payload.tool : undefined;
  if (explicitTool !== undefined) {
    return {
      tool: explicitTool,
      params: toJsonPayload(entry.payload),
      sourceEventType: entry.type,
      ...(traceId === undefined ? {} : { traceId })
    };
  }

  switch (entry.type) {
    case 'artifact_created':
      return {
        tool: 'create_file',
        params: toJsonPayload(entry.payload),
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId })
      };
    case 'graph_update':
      return {
        tool: 'memory',
        params: toJsonPayload(entry.payload),
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId })
      };
    default:
      return undefined;
  }
}

function inferWorkflowStep(entry: EventLogEntry): WorkflowStepPayload | undefined {
  const taskId = readTaskIdentifier(entry.payload);
  const traceId = readTraceIdentifier(entry);

  switch (entry.type) {
    case 'routing': {
      const intent = typeof entry.payload?.intent === 'string' ? entry.payload.intent : 'Dispatch';
      return {
        step: 'Routing dispatch',
        detail: `Intent routed: ${intent}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'decision': {
      const topic = typeof entry.payload?.topic === 'string' ? entry.payload.topic : 'decision';
      const choice = typeof entry.payload?.choice === 'string' ? entry.payload.choice : 'recorded';
      return {
        step: 'Decision recorded',
        detail: `${topic}: ${choice}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        ...(taskId === undefined ? {} : { taskId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'security_finding': {
      const findingId =
        readOptionalNonEmptyString(entry.payload?.finding_id) ??
        readOptionalNonEmptyString(entry.payload?.findingId) ??
        `finding-${entry.seq}`;
      const severity = readOptionalNonEmptyString(entry.payload?.severity)?.toUpperCase() ?? 'MEDIUM';
      const title =
        readOptionalNonEmptyString(entry.payload?.title) ??
        readOptionalNonEmptyString(entry.payload?.finding_title) ??
        findingId;
      return {
        step: 'Security finding recorded',
        detail: `${severity} ${findingId}: ${title}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        ...(taskId === undefined ? {} : { taskId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'branch_finish_options': {
      const branch =
        readOptionalNonEmptyString(entry.payload?.branch) ??
        readOptionalNonEmptyString(entry.payload?.branch_name) ??
        'current-branch';
      const testsPassed =
        typeof entry.payload?.testsPassed === 'boolean'
          ? entry.payload.testsPassed
          : typeof entry.payload?.tests_passed === 'boolean'
            ? entry.payload.tests_passed
            : false;
      return {
        step: 'Branch finisher options updated',
        detail: `${branch}: testsPassed=${testsPassed}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'branch_finish_decision': {
      const branch =
        readOptionalNonEmptyString(entry.payload?.branch) ??
        readOptionalNonEmptyString(entry.payload?.branch_name) ??
        'current-branch';
      const selectedOption =
        readOptionalNonEmptyString(entry.payload?.selected_option) ??
        readOptionalNonEmptyString(entry.payload?.selectedOption) ??
        'keep';
      return {
        step: 'Branch finisher decision proposed',
        detail: `${branch}: ${selectedOption}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'branch_finish_result': {
      const branch =
        readOptionalNonEmptyString(entry.payload?.branch) ??
        readOptionalNonEmptyString(entry.payload?.branch_name) ??
        'current-branch';
      const result = readOptionalNonEmptyString(entry.payload?.result) ?? 'recorded';
      return {
        step: 'Branch finisher result recorded',
        detail: `${branch}: ${result}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'trust_scored': {
      const producer = typeof entry.payload?.producer === 'string' ? entry.payload.producer : 'producer';
      const validator = typeof entry.payload?.validator === 'string' ? entry.payload.validator : 'validator';
      const score = typeof entry.payload?.score === 'number' ? entry.payload.score : 'n/a';
      return {
        step: 'Trust score recorded',
        detail: `${validator} validated ${producer} at ${score}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'cross_validation': {
      const topic = typeof entry.payload?.topic === 'string' ? entry.payload.topic : 'cross-validation';
      return {
        step: 'Cross validation completed',
        detail: `Cross-validation completed for ${topic}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'debate': {
      const topic = typeof entry.payload?.topic === 'string' ? entry.payload.topic : 'debate';
      const result = typeof entry.payload?.result === 'string' ? entry.payload.result : 'resolved';
      return {
        step: 'Structured debate completed',
        detail: `${topic}: ${result}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'verification_gate': {
      const actionId =
        readOptionalNonEmptyString(entry.payload?.actionId) ??
        readOptionalNonEmptyString(entry.payload?.action_id) ??
        'verification-action';
      const verificationRef =
        readOptionalNonEmptyString(entry.payload?.verificationRef) ??
        readOptionalNonEmptyString(entry.payload?.verification_ref) ??
        'verification-ref';
      const result =
        readOptionalNonEmptyString(entry.payload?.result)?.toUpperCase() === 'PASS' ||
        readOptionalNonEmptyString(entry.payload?.result)?.toUpperCase() === 'FAIL'
          ? readOptionalNonEmptyString(entry.payload?.result)?.toUpperCase()
          : 'UNKNOWN';

      return {
        step: `Verification gate ${result}`,
        detail: `${actionId}: ${verificationRef}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        ...(taskId === undefined ? {} : { taskId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    case 'aggregation': {
      const tasksCompleted = typeof entry.payload?.tasks_completed === 'number' ? entry.payload.tasks_completed : 'n/a';
      const trustAverage = typeof entry.payload?.trust_avg === 'number' ? entry.payload.trust_avg : 'n/a';
      return {
        step: 'Aggregation completed',
        detail: `Completed ${tasksCompleted} tasks with average trust ${trustAverage}`,
        sourceEventType: entry.type,
        ...(traceId === undefined ? {} : { traceId }),
        metadata: toJsonPayload(entry.payload)
      };
    }
    default:
      return taskId === undefined
        ? undefined
        : {
            step: 'Task lifecycle update',
            detail: `Task ${taskId} changed via ${entry.type}`,
            sourceEventType: entry.type,
            ...(traceId === undefined ? {} : { traceId }),
            taskId,
            metadata: toJsonPayload(entry.payload)
          };
  }
}

function readTraceIdentifier(entry: EventLogEntry): string | undefined {
  return (
    readOptionalNonEmptyString(entry.trace_id) ??
    readOptionalNonEmptyString(entry.payload?.trace_id) ??
    readOptionalNonEmptyString(entry.payload?.traceId) ??
    readOptionalNonEmptyString(entry.payload?.correlation_id) ??
    readOptionalNonEmptyString(entry.payload?.correlationId) ??
    readOptionalNonEmptyString(entry.payload?.request_id) ??
    readOptionalNonEmptyString(entry.payload?.requestId)
  );
}

function readOptionalNonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }

  const normalized = value.trim();
  return normalized.length > 0 ? normalized : undefined;
}

function readTaskIdentifier(payload: Record<string, unknown> | undefined): string | undefined {
  return readOptionalNonEmptyString(payload?.task_id) ?? readOptionalNonEmptyString(payload?.taskId);
}

function ensureOrchestratorWriteRole(role: string, mutationType: string): void {
  if (role !== 'orchestrator') {
    throw new Error(`Role ${role} cannot mutate runtime state directly for ${mutationType}.`);
  }
}

function ensureMutationIdentity(
  mutationType: RuntimeMutationType,
  requestId: string,
  idempotencyKey: string
): void {
  assertCanonicalMutationIdentity(mutationType, 'requestId', requestId);
  assertCanonicalMutationIdentity(mutationType, 'idempotencyKey', idempotencyKey);
}

function assertTaskLeaseOwnership(
  leaseStore: LeaseStore | undefined,
  taskId: string,
  leaseContext: GrimoireRuntimeLeaseContext | undefined,
  timestamp: string
): void {
  if (leaseStore === undefined) {
    return;
  }

  leaseStore.assertTaskMutationLease(taskId, leaseContext, timestamp);
}

function mutationCacheKey(mutationType: RuntimeMutationType, idempotencyKey: string): string {
  return `${mutationType}:${idempotencyKey}`;
}

function toJsonPayload(payload: Record<string, unknown> | undefined): Record<string, JsonValue> {
  return Object.fromEntries(Object.entries(payload ?? {}).map(([key, value]) => [key, value as JsonValue]));
}

function toAgentPresence(agent: GrimoireAgentRecord): AgentPresence {
  return {
    id: agent.id,
    name: agent.name,
    role: agent.role,
    status: agent.status,
    roomId: agent.roomId,
    position: { ...agent.position },
    ...(agent.parentId === undefined ? {} : { parentId: agent.parentId }),
    ...(agent.lastTool === undefined ? {} : { lastTool: agent.lastTool })
  };
}