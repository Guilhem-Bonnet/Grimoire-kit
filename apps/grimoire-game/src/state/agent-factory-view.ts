import type { AgentPresence, AgentRole } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';

export const AGENT_FACTORY_ACTION_ORDER = ['create', 'clone', 'configure', 'deploy'] as const;
export const AGENT_FACTORY_ISSUE_ORDER = [
  'AGENT_FACTORY_CREATE_FIELDS_MISSING',
  'AGENT_FACTORY_CLONE_PROGRESS_RESET_MISSING',
  'AGENT_FACTORY_DEPLOYMENT_ROOM_MISMATCH',
  'AGENT_FACTORY_RESTART_CONFIRMATION_REQUIRED'
] as const;

export type AgentFactoryAction = (typeof AGENT_FACTORY_ACTION_ORDER)[number];
export type AgentFactoryIssueCode = (typeof AGENT_FACTORY_ISSUE_ORDER)[number];

export interface AgentFactoryOperationRecord {
  action: AgentFactoryAction;
  targetAgentId: string;
  targetAgentName: string | null;
  targetRole: AgentRole | null;
  sourceAgentId: string | null;
  roomId: string | null;
  model: string | null;
  promptRef: string | null;
  toolIds: readonly string[];
  restartRequired: boolean;
  restartConfirmed: boolean;
  rejected: boolean;
  validationError: string | null;
  hasResetProgress: boolean;
  issues: readonly AgentFactoryIssueCode[];
  sequenceId: number;
  timestamp: string;
  taskId: string | null;
  traceId: string | null;
}

export interface AgentFactoryAgentView {
  agentId: string;
  name: string;
  role: AgentRole;
  roomId: string;
  status: AgentPresence['status'];
  sourceAgentId: string | null;
  isClone: boolean;
  hasResetProgress: boolean;
  model: string | null;
  promptRef: string | null;
  toolIds: readonly string[];
  createdAtSequenceId: number | null;
  deployedAtSequenceId: number | null;
  rejectedCreateCount: number;
  pendingIssues: readonly AgentFactoryIssueCode[];
}

export interface AgentFactoryMutationGate {
  agentId: string;
  agentName: string;
  isApplicable: boolean;
  isReady: boolean;
  issueCodes: readonly AgentFactoryIssueCode[];
  blockingReason: string | null;
}

export interface AgentFactorySummary {
  agentCount: number;
  operationCount: number;
  createdCount: number;
  clonedCount: number;
  deployedCount: number;
  rejectedCreateCount: number;
  blockedMutationCount: number;
}

export interface AgentFactoryView {
  protocolVersion: string;
  lastSequenceId: number;
  operations: readonly AgentFactoryOperationRecord[];
  agents: readonly AgentFactoryAgentView[];
  mutationGates: readonly AgentFactoryMutationGate[];
  summary: AgentFactorySummary;
}

export function createAgentFactoryView(state: GameState): AgentFactoryView {
  const operations = collectAgentFactoryOperations(state);
  const agents = createAgentFactoryAgents(state, operations);
  const mutationGates = agents.map((agent) => createAgentFactoryMutationGate(agent));

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    operations,
    agents,
    mutationGates,
    summary: {
      agentCount: agents.length,
      operationCount: operations.length,
      createdCount: operations.filter((operation) => operation.action === 'create' && !operation.rejected).length,
      clonedCount: operations.filter((operation) => operation.action === 'clone').length,
      deployedCount: operations.filter((operation) => operation.action === 'deploy').length,
      rejectedCreateCount: operations.filter((operation) => operation.action === 'create' && operation.rejected).length,
      blockedMutationCount: mutationGates.filter((gate) => gate.isApplicable && !gate.isReady).length
    }
  };
}

export function evaluateAgentFactoryMutationGate(
  state: GameState,
  agentId: string
): AgentFactoryMutationGate | null {
  return createAgentFactoryView(state).mutationGates.find((gate) => gate.agentId === agentId) ?? null;
}

function collectAgentFactoryOperations(state: GameState): AgentFactoryOperationRecord[] {
  return [...state.recentWorkflowSteps]
    .sort((left, right) => left.sequenceId - right.sequenceId)
    .flatMap((workflowStep) => {
      const targetAgentId = readTargetAgentId(workflowStep);
      const agent = targetAgentId === null ? null : state.agents[targetAgentId] ?? null;
      return toAgentFactoryOperation(workflowStep, agent);
    })
    .sort(compareAgentFactoryOperations);
}

function createAgentFactoryAgents(
  state: GameState,
  operations: readonly AgentFactoryOperationRecord[]
): AgentFactoryAgentView[] {
  const byAgentId = new Map<string, AgentFactoryOperationRecord[]>();
  for (const operation of operations) {
    const current = byAgentId.get(operation.targetAgentId) ?? [];
    current.push(operation);
    byAgentId.set(operation.targetAgentId, current);
  }

  const agents: AgentFactoryAgentView[] = [];
  for (const [agentId, agentOperations] of byAgentId.entries()) {
    const agent = state.agents[agentId];
    if (agent === undefined) {
      continue;
    }

    const createOperation = agentOperations.find((operation) => operation.action === 'create' && !operation.rejected) ?? null;
    const cloneOperation = agentOperations.find((operation) => operation.action === 'clone') ?? null;
    const deployOperation = agentOperations.find((operation) => operation.action === 'deploy') ?? null;
    const latestOperation = [...agentOperations].sort((left, right) => right.sequenceId - left.sequenceId)[0] ?? null;
    const rejectedCreateCount = agentOperations.filter(
      (operation) => operation.action === 'create' && operation.rejected
    ).length;
    const pendingIssues = uniqueIssues(agentOperations.flatMap((operation) => operation.issues));

    agents.push({
      agentId: agent.id,
      name: agent.name,
      role: agent.role,
      roomId: agent.roomId,
      status: agent.status,
      sourceAgentId: cloneOperation?.sourceAgentId ?? null,
      isClone: cloneOperation !== null,
      hasResetProgress: cloneOperation?.hasResetProgress ?? true,
      model: latestOperation?.model ?? null,
      promptRef: latestOperation?.promptRef ?? null,
      toolIds: uniqueStrings(agentOperations.flatMap((operation) => operation.toolIds)),
      createdAtSequenceId: createOperation?.sequenceId ?? cloneOperation?.sequenceId ?? null,
      deployedAtSequenceId: deployOperation?.sequenceId ?? null,
      rejectedCreateCount,
      pendingIssues
    });
  }

  return agents.sort((left, right) => left.name.localeCompare(right.name));
}

function createAgentFactoryMutationGate(agent: AgentFactoryAgentView): AgentFactoryMutationGate {
  const issueCodes = uniqueIssues(
    agent.pendingIssues.filter((issueCode) => issueCode === 'AGENT_FACTORY_RESTART_CONFIRMATION_REQUIRED')
  );

  return {
    agentId: agent.agentId,
    agentName: agent.name,
    isApplicable: issueCodes.length > 0,
    isReady: issueCodes.length === 0,
    issueCodes,
    blockingReason:
      issueCodes.length === 0
        ? null
        : `Agent ${agent.name} requires explicit restart confirmation before sensitive post-deploy changes can apply.`
  };
}

function toAgentFactoryOperation(
  workflowStep: WorkflowStepLogEntry,
  agent: AgentPresence | null
): AgentFactoryOperationRecord[] {
  const metadata = workflowStep.metadata as Record<string, unknown>;
  const action = readAgentFactoryAction(metadata);
  if (action === null) {
    return [];
  }

  const targetAgentId = readTargetAgentId(workflowStep);
  if (targetAgentId === null) {
    return [];
  }

  const toolIds = readStringListByKeys(metadata, ['toolIds', 'tool_ids', 'tools']);
  const issues = createOperationIssues(action, metadata, agent);
  const sourceXp = readNumberByKeys(metadata, ['sourceXp', 'source_xp']);
  const clonedXp = readNumberByKeys(metadata, ['clonedXp', 'cloned_xp']);
  const sourceHistoryCount = readNumberByKeys(metadata, ['sourceHistoryCount', 'source_history_count']);
  const clonedHistoryCount = readNumberByKeys(metadata, ['clonedHistoryCount', 'cloned_history_count']);

  return [
    {
      action,
      targetAgentId,
      targetAgentName: readStringByKeys(metadata, ['agentName', 'agent_name']) ?? agent?.name ?? null,
      targetRole: readAgentRole(metadata) ?? agent?.role ?? null,
      sourceAgentId: readStringByKeys(metadata, ['sourceAgentId', 'source_agent_id']),
      roomId: readStringByKeys(metadata, ['roomId', 'room_id']) ?? agent?.roomId ?? null,
      model: readStringByKeys(metadata, ['model']),
      promptRef: readStringByKeys(metadata, ['promptRef', 'prompt_ref']),
      toolIds,
      restartRequired: readBooleanByKeys(metadata, ['restartRequired', 'restart_required']) ?? false,
      restartConfirmed: readBooleanByKeys(metadata, ['restartConfirmed', 'restart_confirmed']) ?? false,
      rejected:
        (readBooleanByKeys(metadata, ['rejected']) ?? false) ||
        readStringByKeys(metadata, ['validationError', 'validation_error']) !== null,
      validationError: readStringByKeys(metadata, ['validationError', 'validation_error']),
      hasResetProgress:
        action !== 'clone' ||
        ((sourceXp === null || clonedXp === 0) &&
          (sourceHistoryCount === null || clonedHistoryCount === 0)),
      issues,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp,
      taskId: workflowStep.taskId ?? null,
      traceId: workflowStep.traceId ?? null
    }
  ];
}

function createOperationIssues(
  action: AgentFactoryAction,
  metadata: Record<string, unknown>,
  agent: AgentPresence | null
): AgentFactoryIssueCode[] {
  const issues: AgentFactoryIssueCode[] = [];
  const rejected =
    (readBooleanByKeys(metadata, ['rejected']) ?? false) ||
    readStringByKeys(metadata, ['validationError', 'validation_error']) !== null;

  if (action === 'create') {
    const requiredFields = [
      readTargetAgentIdFromMetadata(metadata),
      readStringByKeys(metadata, ['agentName', 'agent_name']),
      readAgentRole(metadata),
      readStringByKeys(metadata, ['model']),
      readStringByKeys(metadata, ['promptRef', 'prompt_ref']),
      readStringByKeys(metadata, ['roomId', 'room_id'])
    ];
    if (requiredFields.some((field) => field === null) && !rejected) {
      issues.push('AGENT_FACTORY_CREATE_FIELDS_MISSING');
    }
  }

  if (action === 'clone') {
    const sourceXp = readNumberByKeys(metadata, ['sourceXp', 'source_xp']);
    const clonedXp = readNumberByKeys(metadata, ['clonedXp', 'cloned_xp']);
    const sourceHistoryCount = readNumberByKeys(metadata, ['sourceHistoryCount', 'source_history_count']);
    const clonedHistoryCount = readNumberByKeys(metadata, ['clonedHistoryCount', 'cloned_history_count']);
    if ((sourceXp !== null && sourceXp > 0 && clonedXp !== 0) || (sourceHistoryCount !== null && sourceHistoryCount > 0 && clonedHistoryCount !== 0)) {
      issues.push('AGENT_FACTORY_CLONE_PROGRESS_RESET_MISSING');
    }
  }

  if (action === 'deploy') {
    const expectedRoomId = readStringByKeys(metadata, ['roomId', 'room_id']);
    if (expectedRoomId !== null && agent?.roomId !== expectedRoomId) {
      issues.push('AGENT_FACTORY_DEPLOYMENT_ROOM_MISMATCH');
    }
  }

  if (action === 'configure') {
    const restartRequired = readBooleanByKeys(metadata, ['restartRequired', 'restart_required']) ?? false;
    const restartConfirmed = readBooleanByKeys(metadata, ['restartConfirmed', 'restart_confirmed']) ?? false;
    if (restartRequired && !restartConfirmed) {
      issues.push('AGENT_FACTORY_RESTART_CONFIRMATION_REQUIRED');
    }
  }

  return issues;
}

function compareAgentFactoryOperations(left: AgentFactoryOperationRecord, right: AgentFactoryOperationRecord): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  if (left.targetAgentId !== right.targetAgentId) {
    return left.targetAgentId.localeCompare(right.targetAgentId);
  }

  return AGENT_FACTORY_ACTION_ORDER.indexOf(left.action) - AGENT_FACTORY_ACTION_ORDER.indexOf(right.action);
}

function readAgentFactoryAction(metadata: Record<string, unknown>): AgentFactoryAction | null {
  const action = readStringByKeys(metadata, ['agentFactoryAction', 'agent_factory_action']);
  if (action === null) {
    return null;
  }

  const normalizedAction = action.trim().toLowerCase();
  if (normalizedAction === 'create' || normalizedAction === 'clone' || normalizedAction === 'configure' || normalizedAction === 'deploy') {
    return normalizedAction;
  }

  return null;
}

function readAgentRole(metadata: Record<string, unknown>): AgentRole | null {
  const role = readStringByKeys(metadata, ['agentRole', 'agent_role']);
  if (role === 'orchestrator' || role === 'agent' || role === 'spectator') {
    return role;
  }

  return null;
}

function readTargetAgentId(workflowStep: WorkflowStepLogEntry): string | null {
  return readTargetAgentIdFromMetadata(workflowStep.metadata as Record<string, unknown>);
}

function readTargetAgentIdFromMetadata(metadata: Record<string, unknown>): string | null {
  return readStringByKeys(metadata, ['targetAgentId', 'target_agent_id', 'agentId', 'agent_id']);
}

function readStringByKeys(metadata: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalizedValue = value.trim();
    if (normalizedValue.length > 0) {
      return normalizedValue;
    }
  }

  return null;
}

function readStringListByKeys(metadata: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = metadata[key];
    if (!Array.isArray(value)) {
      continue;
    }

    const normalizedValues = value
      .filter((entry): entry is string => typeof entry === 'string')
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
    if (normalizedValues.length > 0) {
      return normalizedValues;
    }
  }

  return [];
}

function readBooleanByKeys(metadata: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readNumberByKeys(metadata: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }

  return null;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}

function uniqueIssues(values: readonly AgentFactoryIssueCode[]): AgentFactoryIssueCode[] {
  return AGENT_FACTORY_ISSUE_ORDER.filter((issue) => values.includes(issue));
}