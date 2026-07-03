import type {
  AgentPresence,
  GameStateSnapshot,
  HostBindingRecord,
  HostContextLedgerRecord,
  HostInvocationDecisionRecord,
  HostReviewArtifactRecord,
  JsonValue,
  RuntimeErrorEvent,
  ServerEvent,
  ToolCallLogEntry,
  TaskSnapshot,
  WorkflowStepLogEntry
} from '../contracts/events';

export type {
  HostBindingRecord,
  HostContextLedgerRecord,
  HostInvocationDecisionRecord,
  HostReviewArtifactRecord,
  ToolCallLogEntry,
  WorkflowStepLogEntry
} from '../contracts/events';

export interface GameState {
  protocolVersion: string;
  lastSequenceId: number;
  hydratedAt: string | null;
  agents: Record<string, AgentPresence>;
  tasks: Record<string, TaskSnapshot>;
  config: Record<string, JsonValue>;
  recentToolCalls: readonly ToolCallLogEntry[];
  recentWorkflowSteps: readonly WorkflowStepLogEntry[];
  hostBindings?: Record<string, HostBindingRecord>;
  recentHostInvocationDecisions?: readonly HostInvocationDecisionRecord[];
  recentHostContextEntries?: readonly HostContextLedgerRecord[];
  recentHostReviews?: readonly HostReviewArtifactRecord[];
  lastErrors: readonly RuntimeErrorEvent[];
}

function indexById<T extends { id: string }>(items: readonly T[]): Record<string, T> {
  return Object.fromEntries(items.map((item) => [item.id, item]));
}

function cloneTaskSnapshot(task: TaskSnapshot): TaskSnapshot {
  return {
    ...task,
    ...(task.dependencyIds === undefined ? {} : { dependencyIds: [...task.dependencyIds] })
  };
}

function cloneSerializable<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function createEmptyGameState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: -1,
    hydratedAt: null,
    agents: {},
    tasks: {},
    config: {},
    recentToolCalls: [],
    recentWorkflowSteps: [],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

export function hydrateGameState(snapshot: GameStateSnapshot, hydratedAt: string | null = null): GameState {
  return {
    protocolVersion: snapshot.protocolVersion,
    lastSequenceId: snapshot.lastSequenceId,
    hydratedAt: hydratedAt ?? snapshot.generatedAt,
    agents: indexById(snapshot.agents),
    tasks: indexById(snapshot.tasks.map(cloneTaskSnapshot)),
    config: { ...snapshot.config },
    recentToolCalls: snapshot.recentToolCalls.map((entry) => ({
      ...entry,
      params: { ...entry.params }
    })),
    recentWorkflowSteps: snapshot.recentWorkflowSteps.map((entry) => ({
      ...entry,
      metadata: { ...entry.metadata }
    })),
    hostBindings: snapshot.hostBindings === undefined ? {} : cloneSerializable(snapshot.hostBindings),
    recentHostInvocationDecisions:
      snapshot.recentHostInvocationDecisions === undefined ? [] : cloneSerializable(snapshot.recentHostInvocationDecisions),
    recentHostContextEntries:
      snapshot.recentHostContextEntries === undefined ? [] : cloneSerializable(snapshot.recentHostContextEntries),
    recentHostReviews: snapshot.recentHostReviews === undefined ? [] : cloneSerializable(snapshot.recentHostReviews),
    lastErrors: []
  };
}

export function applyServerEvent(state: GameState, event: ServerEvent): GameState {
  if (event.sequenceId <= state.lastSequenceId) {
    return state;
  }

  if (event.type === 'STATE_SNAPSHOT') {
    return hydrateGameState(event.snapshot, event.timestamp);
  }

  if (event.type === 'AGENT_STATE') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      agents: {
        ...state.agents,
        [event.agent.id]: event.agent
      }
    };
  }

  if (event.type === 'TASK_UPDATE') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      agents:
        event.agent === undefined
          ? state.agents
          : {
              ...state.agents,
              [event.agent.id]: event.agent
            },
      tasks: {
        ...state.tasks,
        [event.task.id]: cloneTaskSnapshot(event.task)
      }
    };
  }

  if (event.type === 'TOOL_CALL') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      agents:
        event.agent === undefined
          ? state.agents
          : {
              ...state.agents,
              [event.agent.id]: event.agent
            },
      recentToolCalls: [
        ...state.recentToolCalls,
        {
          ...event.call,
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          ...(event.agent === undefined ? {} : { agentId: event.agent.id })
        }
      ].slice(-50)
    };
  }

  if (event.type === 'WORKFLOW_STEP') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      agents:
        event.agent === undefined
          ? state.agents
          : {
              ...state.agents,
              [event.agent.id]: event.agent
            },
      recentWorkflowSteps: [
        ...state.recentWorkflowSteps,
        {
          ...event.step,
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          ...(event.agent === undefined ? {} : { agentId: event.agent.id })
        }
      ].slice(-100)
    };
  }

  if (event.type === 'VERIFICATION_GATE') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      recentWorkflowSteps: [
        ...state.recentWorkflowSteps,
        {
          step: `Verification gate ${event.result}`,
          detail: `${event.actionId}: ${event.verificationRef}`,
          sourceEventType: 'verification_gate',
          ...(event.traceId === undefined ? {} : { traceId: event.traceId }),
          ...(event.taskId === undefined ? {} : { taskId: event.taskId }),
          metadata: {
            ...event.meta,
            actionId: event.actionId,
            verificationRef: event.verificationRef,
            controlsExecuted: [...event.controlsExecuted],
            evidenceRefs: event.evidenceRefs.map((evidenceRef) => evidenceRef.ref),
            typedEvidenceRefs: event.evidenceRefs.map((evidenceRef) => ({
              kind: evidenceRef.kind,
              ref: evidenceRef.ref
            })),
            verdict: event.result,
            ...(event.unmetControls.length === 0 ? {} : { unmetControls: [...event.unmetControls] })
          },
          sequenceId: event.sequenceId,
          timestamp: event.timestamp
        }
      ].slice(-100)
    };
  }

  if (event.type === 'HOST_BINDING_STATE') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      hostBindings: {
        ...(state.hostBindings ?? {}),
        [event.binding.hostId]: {
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          binding: event.binding,
          manifest: event.manifest,
          ...(event.reason === undefined ? {} : { reason: event.reason })
        }
      }
    };
  }

  if (event.type === 'HOST_INVOCATION_DECISION') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      recentHostInvocationDecisions: [
        ...(state.recentHostInvocationDecisions ?? []),
        {
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          envelope: event.envelope,
          decision: event.decision,
          reason: event.reason,
          meta: event.meta
        }
      ].slice(-100)
    };
  }

  if (event.type === 'HOST_REVIEW_ARTIFACT') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      recentHostReviews: [
        ...(state.recentHostReviews ?? []),
        {
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          review: event.review,
          meta: event.meta
        }
      ].slice(-100)
    };
  }

  if (event.type === 'HOST_CONTEXT_LEDGER_UPDATE') {
    return {
      ...state,
      lastSequenceId: event.sequenceId,
      recentHostContextEntries: [
        ...(state.recentHostContextEntries ?? []),
        {
          sequenceId: event.sequenceId,
          timestamp: event.timestamp,
          entry: event.entry,
          meta: event.meta
        }
      ].slice(-100)
    };
  }

  return {
    ...state,
    lastSequenceId: event.sequenceId,
    lastErrors: [...state.lastErrors, event].slice(-10)
  };
}

export function applyServerEvents(state: GameState, events: readonly ServerEvent[]): GameState {
  return events.reduce(applyServerEvent, state);
}