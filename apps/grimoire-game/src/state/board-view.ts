import type { AgentPresence, AgentStatus, RuntimeErrorEvent, TaskSnapshot, TaskStatus } from '../contracts/events';

import type { GameState, ToolCallLogEntry, WorkflowStepLogEntry } from './game-state';
import { createSecurityAuditView, type SecurityKanbanCard } from './branch-finisher-view';

export const BOARD_TASK_STATUS_ORDER = ['backlog', 'todo', 'in_progress', 'review', 'done'] as const;
export const BOARD_ACTIVE_TASK_STATUSES: readonly TaskStatus[] = ['todo', 'in_progress', 'review'];

export type BoardAlertCode =
  | 'RUNTIME_ERROR'
  | 'SECURITY_FINDING_BLOCKING'
  | 'SECURITY_FINDING_OPEN'
  | 'TASK_ASSIGNEE_MISSING'
  | 'TASK_UNASSIGNED_ACTIVE'
  | 'AGENT_PARENT_MISSING'
  | 'WORKING_AGENT_WITHOUT_ACTIVE_TASK';

export interface BoardAgentSummary {
  id: string;
  name: string;
  role: AgentPresence['role'];
  status: AgentPresence['status'];
  roomId: string;
  parentId: string | null;
  childAgentIds: readonly string[];
  taskIds: readonly string[];
  taskCount: number;
  activeTaskCount: number;
  lastTool: string | null;
}

export interface BoardRoomSummary {
  id: string;
  agentIds: readonly string[];
  taskIds: readonly string[];
  leadAgentId: string | null;
  agentCount: number;
  taskCount: number;
  activeTaskCount: number;
  workingCount: number;
  pausedCount: number;
  idleCount: number;
  offlineCount: number;
}

export interface BoardTaskColumn {
  status: TaskStatus;
  tasks: readonly TaskSnapshot[];
  count: number;
}

export interface BoardAlert {
  level: 'error' | 'warning' | 'info';
  code: BoardAlertCode;
  message: string;
  agentId?: string;
  taskId?: string;
  roomId?: string;
  sequenceId?: number;
}

export interface BoardExplainabilityEntry {
  kind: 'workflow_step' | 'tool_call';
  sequenceId: number;
  timestamp: string;
  sourceEventType: string;
  summary: string;
  detail: string;
  agentId: string | null;
}

export const DECISION_CARD_REQUIRED_FIELD_ORDER = ['context', 'options', 'choice', 'rationale', 'impact', 'evidence'] as const;

export type DecisionCardRequiredField = (typeof DECISION_CARD_REQUIRED_FIELD_ORDER)[number];

export interface BoardDecisionCard {
  id: string;
  title: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  agentId: string | null;
  roomId: string | null;
  traceId: string | null;
  taskId: string | null;
  taskTitle: string | null;
  actionId: string | null;
  decisionContext: string | null;
  consideredOptions: readonly string[];
  selectedOption: string | null;
  rationale: string | null;
  impact: string | null;
  evidenceRefs: readonly string[];
  missingFields: readonly DecisionCardRequiredField[];
  isStructured: boolean;
  isTransitionCard: boolean;
  evidence: readonly BoardExplainabilityEntry[];
  supportingToolCalls: readonly ToolCallLogEntry[];
}

export interface AgentInspectionView {
  agent: BoardAgentSummary;
  parentAgent: BoardAgentSummary | null;
  childAgents: readonly BoardAgentSummary[];
  assignedTasks: readonly TaskSnapshot[];
  recentToolCalls: readonly ToolCallLogEntry[];
  recentWorkflowSteps: readonly WorkflowStepLogEntry[];
  decisionCards: readonly BoardDecisionCard[];
  room: BoardRoomSummary | null;
  alerts: readonly BoardAlert[];
}

export interface BoardMetrics {
  roomCount: number;
  agentCount: number;
  workingAgentCount: number;
  taskCount: number;
  activeTaskCount: number;
  securityCardCount: number;
  alertCount: number;
}

export interface BoardView {
  protocolVersion: string;
  lastSequenceId: number;
  agents: readonly BoardAgentSummary[];
  rooms: readonly BoardRoomSummary[];
  taskColumns: readonly BoardTaskColumn[];
  alerts: readonly BoardAlert[];
  securityCards: readonly SecurityKanbanCard[];
  decisionCards: readonly BoardDecisionCard[];
  metrics: BoardMetrics;
  inspections: Record<string, AgentInspectionView>;
}

const TASK_STATUS_RANK: Record<TaskStatus, number> = {
  backlog: 0,
  todo: 1,
  in_progress: 2,
  review: 3,
  done: 4
};

const AGENT_STATUS_RANK: Record<AgentStatus, number> = {
  working: 0,
  paused: 1,
  idle: 2,
  offline: 3
};

const AGENT_ROLE_RANK: Record<AgentPresence['role'], number> = {
  orchestrator: 0,
  agent: 1,
  spectator: 2
};

const ALERT_CODE_RANK: Record<BoardAlertCode, number> = {
  RUNTIME_ERROR: 0,
  SECURITY_FINDING_BLOCKING: 1,
  SECURITY_FINDING_OPEN: 2,
  TASK_ASSIGNEE_MISSING: 3,
  AGENT_PARENT_MISSING: 4,
  TASK_UNASSIGNED_ACTIVE: 5,
  WORKING_AGENT_WITHOUT_ACTIVE_TASK: 6
};

export function createBoardView(state: GameState): BoardView {
  const tasks = Object.values(state.tasks).sort(compareTaskSnapshots);
  const taskMap = Object.fromEntries(tasks.map((task) => [task.id, task]));
  const securityAudit = createSecurityAuditView(state);
  const childAgentIdsByParentId = indexChildAgentIds(state);
  const taskIdsByAssigneeId = indexTaskIdsByAssignee(tasks);

  const agents = Object.values(state.agents)
    .sort(compareAgentPresence)
    .map<BoardAgentSummary>((agent) => {
      const taskIds = [...(taskIdsByAssigneeId.get(agent.id) ?? [])];
      const childAgentIds = [...(childAgentIdsByParentId.get(agent.id) ?? [])].sort((left, right) =>
        compareAgentsById(state, left, right)
      );
      const activeTaskCount = taskIds.reduce((count, taskId) => {
        const task = taskMap[taskId];
        return task !== undefined && isActiveTask(task) ? count + 1 : count;
      }, 0);

      return {
        id: agent.id,
        name: agent.name,
        role: agent.role,
        status: agent.status,
        roomId: agent.roomId,
        parentId: agent.parentId ?? null,
        childAgentIds,
        taskIds,
        taskCount: taskIds.length,
        activeTaskCount,
        lastTool: agent.lastTool ?? null
      };
    });

  const agentsById = Object.fromEntries(agents.map((agent) => [agent.id, agent]));
  const rooms = createRoomSummaries(agents, tasks, agentsById);
  const alerts = createBoardAlerts(state, agents, taskMap, securityAudit.kanbanCards);
  const decisionCards = createDecisionCards(state, agentsById, taskMap);
  const roomsById = Object.fromEntries(rooms.map((room) => [room.id, room]));
  const inspections = Object.fromEntries(
    agents.map((agent) => [
      agent.id,
      createInspectionView(state, agent, agentsById, roomsById, taskMap, alerts, decisionCards)
    ])
  );

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    agents,
    rooms,
    taskColumns: BOARD_TASK_STATUS_ORDER.map((status) => {
      const columnTasks = tasks.filter((task) => task.status === status);
      return {
        status,
        tasks: columnTasks,
        count: columnTasks.length
      };
    }),
    alerts,
    securityCards: securityAudit.kanbanCards,
    decisionCards,
    metrics: {
      roomCount: rooms.length,
      agentCount: agents.length,
      workingAgentCount: agents.filter((agent) => agent.status === 'working').length,
      taskCount: tasks.length,
      activeTaskCount: tasks.filter(isActiveTask).length,
      securityCardCount: securityAudit.kanbanCards.length,
      alertCount: alerts.length
    },
    inspections
  };
}

export function createAgentInspection(state: GameState, agentId: string): AgentInspectionView | null {
  const boardView = createBoardView(state);
  return boardView.inspections[agentId] ?? null;
}

function createRoomSummaries(
  agents: readonly BoardAgentSummary[],
  tasks: readonly TaskSnapshot[],
  agentsById: Record<string, BoardAgentSummary>
): BoardRoomSummary[] {
  const agentsByRoomId = new Map<string, BoardAgentSummary[]>();

  for (const agent of agents) {
    const roomAgents = agentsByRoomId.get(agent.roomId) ?? [];
    roomAgents.push(agent);
    agentsByRoomId.set(agent.roomId, roomAgents);
  }

  return Array.from(agentsByRoomId.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([roomId, roomAgents]) => {
      const roomAgentIds = new Set(roomAgents.map((agent) => agent.id));
      const roomTasks = tasks.filter((task) => task.assigneeId !== undefined && task.assigneeId !== null && roomAgentIds.has(task.assigneeId));
      const leadAgent =
        roomAgents.find((agent) => agent.role === 'orchestrator') ??
        roomAgents.find((agent) => agent.activeTaskCount > 0) ??
        roomAgents.find((agent) => agent.status === 'working') ??
        roomAgents[0];

      return {
        id: roomId,
        agentIds: roomAgents.map((agent) => agent.id),
        taskIds: roomTasks.map((task) => task.id),
        leadAgentId: leadAgent?.id ?? null,
        agentCount: roomAgents.length,
        taskCount: roomTasks.length,
        activeTaskCount: roomTasks.filter(isActiveTask).length,
        workingCount: countAgentsByStatus(roomAgents, 'working'),
        pausedCount: countAgentsByStatus(roomAgents, 'paused'),
        idleCount: countAgentsByStatus(roomAgents, 'idle'),
        offlineCount: countAgentsByStatus(roomAgents, 'offline')
      };
    })
    .map((room) => ({
      ...room,
      agentIds: [...room.agentIds].sort((left, right) => compareBoardAgents(agentsById[left], agentsById[right]))
    }));
}

function createBoardAlerts(
  state: GameState,
  agents: readonly BoardAgentSummary[],
  taskMap: Record<string, TaskSnapshot>,
  securityCards: readonly SecurityKanbanCard[]
): BoardAlert[] {
  const alerts: BoardAlert[] = createRuntimeErrorAlerts(state.lastErrors);
  const agentIds = new Set(agents.map((agent) => agent.id));

  for (const task of Object.values(taskMap)) {
    if (task.assigneeId !== undefined && task.assigneeId !== null && !agentIds.has(task.assigneeId)) {
      alerts.push({
        level: 'warning',
        code: 'TASK_ASSIGNEE_MISSING',
        message: `Task ${task.title} is assigned to unknown agent ${task.assigneeId}.`,
        taskId: task.id
      });
      continue;
    }

    if (isActiveTask(task) && (task.assigneeId === undefined || task.assigneeId === null)) {
      alerts.push({
        level: 'warning',
        code: 'TASK_UNASSIGNED_ACTIVE',
        message: `Task ${task.title} is active without an assignee.`,
        taskId: task.id
      });
    }
  }

  for (const agent of agents) {
    if (agent.parentId !== null && !agentIds.has(agent.parentId)) {
      alerts.push({
        level: 'warning',
        code: 'AGENT_PARENT_MISSING',
        message: `Agent ${agent.name} references missing parent ${agent.parentId}.`,
        agentId: agent.id,
        roomId: agent.roomId
      });
    }

    if (agent.status === 'working' && agent.activeTaskCount === 0) {
      alerts.push({
        level: 'info',
        code: 'WORKING_AGENT_WITHOUT_ACTIVE_TASK',
        message: `Agent ${agent.name} is marked working without an active task.`,
        agentId: agent.id,
        roomId: agent.roomId
      });
    }
  }

  for (const securityCard of securityCards) {
    alerts.push({
      level: securityCard.blocksShip ? 'error' : 'warning',
      code: securityCard.blocksShip ? 'SECURITY_FINDING_BLOCKING' : 'SECURITY_FINDING_OPEN',
      message: `${securityCard.findingId}: ${securityCard.detail}`,
      ...(securityCard.taskId === null ? {} : { taskId: securityCard.taskId }),
      ...(securityCard.sequenceId === undefined ? {} : { sequenceId: securityCard.sequenceId })
    });
  }

  return alerts.sort(compareBoardAlerts);
}

function createInspectionView(
  state: GameState,
  agent: BoardAgentSummary,
  agentsById: Record<string, BoardAgentSummary>,
  roomsById: Record<string, BoardRoomSummary>,
  taskMap: Record<string, TaskSnapshot>,
  alerts: readonly BoardAlert[],
  decisionCards: readonly BoardDecisionCard[]
): AgentInspectionView {
  const assignedTasks = agent.taskIds
    .map((taskId) => taskMap[taskId])
    .filter((task): task is TaskSnapshot => task !== undefined)
    .sort(compareTaskSnapshots);

  return {
    agent,
    parentAgent: agent.parentId === null ? null : (agentsById[agent.parentId] ?? null),
    childAgents: agent.childAgentIds
      .map((childAgentId) => agentsById[childAgentId])
      .filter((childAgent): childAgent is BoardAgentSummary => childAgent !== undefined),
    assignedTasks,
    recentToolCalls: [...state.recentToolCalls]
      .filter((toolCall) => toolCall.agentId === agent.id)
      .sort((left, right) => right.sequenceId - left.sequenceId),
    recentWorkflowSteps: [...state.recentWorkflowSteps]
      .filter((workflowStep) => workflowStep.agentId === agent.id)
      .sort((left, right) => right.sequenceId - left.sequenceId),
    decisionCards: decisionCards.filter(
      (decisionCard) =>
        decisionCard.agentId === agent.id ||
        (decisionCard.taskId !== null && agent.taskIds.includes(decisionCard.taskId))
    ),
    room: roomsById[agent.roomId] ?? null,
    alerts: alerts.filter(
      (alert) =>
        alert.agentId === agent.id || (alert.taskId !== undefined && agent.taskIds.includes(alert.taskId))
    )
  };
}

function createDecisionCards(
  state: GameState,
  agentsById: Record<string, BoardAgentSummary>,
  taskMap: Record<string, TaskSnapshot>
): BoardDecisionCard[] {
  return [...state.recentWorkflowSteps]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .map((workflowStep) => createDecisionCard(state, workflowStep, agentsById, taskMap));
}

function createDecisionCard(
  state: GameState,
  workflowStep: WorkflowStepLogEntry,
  agentsById: Record<string, BoardAgentSummary>,
  taskMap: Record<string, TaskSnapshot>
): BoardDecisionCard {
  const causalWorkflowSteps = collectCausalWorkflowSteps(state.recentWorkflowSteps, workflowStep);
  const earliestSequenceId = causalWorkflowSteps[0]?.sequenceId ?? workflowStep.sequenceId;
  const supportingToolCalls = [...state.recentToolCalls]
    .filter(
      (toolCall) =>
        isRelatedToolCall(toolCall, workflowStep) &&
        toolCall.sequenceId >= earliestSequenceId &&
        toolCall.sequenceId <= workflowStep.sequenceId
    )
    .sort((left, right) => left.sequenceId - right.sequenceId)
    .slice(-3);
  const evidence = [...causalWorkflowSteps.map(toWorkflowEvidence), ...supportingToolCalls.map(toToolEvidence)].sort(
    compareExplainabilityEntries
  );
  const agent = workflowStep.agentId === undefined ? undefined : agentsById[workflowStep.agentId];
  const taskId = workflowStep.taskId ?? null;
  const actionId = readMetadataStringByKeys(workflowStep.metadata, ['actionId', 'action_id']);
  const decisionContext = readMetadataStringByKeys(workflowStep.metadata, [
    'decisionContext',
    'decision_context',
    'context',
    'contextSummary',
    'context_summary'
  ]);
  const consideredOptions = readMetadataStringListByKeys(workflowStep.metadata, [
    'consideredOptions',
    'considered_options',
    'options'
  ]);
  const selectedOption = readMetadataStringByKeys(workflowStep.metadata, [
    'selectedOption',
    'selected_option',
    'choice',
    'decision'
  ]);
  const rationale = readMetadataStringByKeys(workflowStep.metadata, ['rationale', 'reason']);
  const impact = readMetadataStringByKeys(workflowStep.metadata, ['impact', 'decisionImpact', 'decision_impact']);
  const evidenceRefs = readMetadataEvidenceRefsByKeys(workflowStep.metadata, ['evidenceRefs', 'evidence_refs']);
  const missingFields = createDecisionCardMissingFields({
    decisionContext,
    consideredOptions,
    selectedOption,
    rationale,
    impact,
    evidenceRefs
  });

  return {
    id: `decision-${workflowStep.sequenceId}`,
    title: workflowStep.step,
    detail: workflowStep.detail,
    sourceEventType: workflowStep.sourceEventType,
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    agentId: workflowStep.agentId ?? null,
    roomId: agent?.roomId ?? null,
    traceId: workflowStep.traceId ?? null,
    taskId,
    taskTitle: taskId === null ? null : (taskMap[taskId]?.title ?? null),
    actionId,
    decisionContext,
    consideredOptions,
    selectedOption,
    rationale,
    impact,
    evidenceRefs,
    missingFields,
    isStructured: missingFields.length === 0,
    isTransitionCard: actionId !== null && actionId.startsWith('task.transition.'),
    evidence,
    supportingToolCalls
  };
}

function collectCausalWorkflowSteps(
  workflowSteps: readonly WorkflowStepLogEntry[],
  currentStep: WorkflowStepLogEntry
): WorkflowStepLogEntry[] {
  const relatedWorkflowSteps = [...workflowSteps]
    .filter((candidate) => isRelatedWorkflowStep(candidate, currentStep) && candidate.sequenceId <= currentStep.sequenceId)
    .sort((left, right) => left.sequenceId - right.sequenceId);
  const currentIndex = relatedWorkflowSteps.findIndex((candidate) => candidate.sequenceId === currentStep.sequenceId);

  if (currentIndex === -1) {
    return [currentStep];
  }

  return relatedWorkflowSteps.slice(Math.max(0, currentIndex - 2), currentIndex + 1);
}

function isRelatedWorkflowStep(candidate: WorkflowStepLogEntry, currentStep: WorkflowStepLogEntry): boolean {
  if (currentStep.traceId !== undefined) {
    return candidate.traceId === currentStep.traceId;
  }

  if (currentStep.taskId !== undefined) {
    return candidate.taskId === currentStep.taskId;
  }

  if (currentStep.agentId !== undefined) {
    return candidate.agentId === currentStep.agentId;
  }

  return candidate.sequenceId === currentStep.sequenceId;
}

function isRelatedToolCall(toolCall: ToolCallLogEntry, workflowStep: WorkflowStepLogEntry): boolean {
  if (workflowStep.traceId !== undefined) {
    if (toolCall.traceId !== workflowStep.traceId) {
      return false;
    }
  }

  if (workflowStep.agentId !== undefined && toolCall.agentId !== workflowStep.agentId) {
    return false;
  }

  const toolTaskId = readStringValue(toolCall.params.task_id);
  if (workflowStep.taskId !== undefined && toolTaskId !== undefined && workflowStep.taskId !== toolTaskId) {
    return false;
  }

  return true;
}

function toWorkflowEvidence(workflowStep: WorkflowStepLogEntry): BoardExplainabilityEntry {
  return {
    kind: 'workflow_step',
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    sourceEventType: workflowStep.sourceEventType,
    summary: workflowStep.step,
    detail: workflowStep.detail,
    agentId: workflowStep.agentId ?? null
  };
}

function toToolEvidence(toolCall: ToolCallLogEntry): BoardExplainabilityEntry {
  return {
    kind: 'tool_call',
    sequenceId: toolCall.sequenceId,
    timestamp: toolCall.timestamp,
    sourceEventType: toolCall.sourceEventType,
    summary: `Tool call: ${toolCall.tool}`,
    detail: formatToolCallDetail(toolCall),
    agentId: toolCall.agentId ?? null
  };
}

function formatToolCallDetail(toolCall: ToolCallLogEntry): string {
  const path = readStringValue(toolCall.params.path);
  if (path !== undefined) {
    return `${toolCall.tool} on ${path}`;
  }

  const taskId = readStringValue(toolCall.params.task_id);
  if (taskId !== undefined) {
    return `${toolCall.tool} for ${taskId}`;
  }

  const query = readStringValue(toolCall.params.query);
  if (query !== undefined) {
    return `${toolCall.tool} for ${query}`;
  }

  return toolCall.tool;
}

function compareExplainabilityEntries(left: BoardExplainabilityEntry, right: BoardExplainabilityEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return left.sequenceId - right.sequenceId;
  }

  if (left.kind !== right.kind) {
    return left.kind.localeCompare(right.kind);
  }

  return left.summary.localeCompare(right.summary);
}

function readStringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function readMetadataStringByKeys(metadata: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalized = value.trim();
    if (normalized.length > 0) {
      return normalized;
    }
  }

  return null;
}

function readMetadataStringListByKeys(metadata: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const values = normalizeStringList(metadata[key]);
    if (values.length > 0) {
      return values;
    }
  }

  return [];
}

function readMetadataEvidenceRefsByKeys(metadata: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const refs = normalizeEvidenceRefs(metadata[key]);
    if (refs.length > 0) {
      return refs;
    }
  }

  return [];
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return [...new Set(value.filter((entry): entry is string => typeof entry === 'string').map((entry) => entry.trim()).filter((entry) => entry.length > 0))];
}

function normalizeEvidenceRefs(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const refs: string[] = [];

  for (const entry of value) {
    if (typeof entry === 'string') {
      const normalized = entry.trim();
      if (normalized.length > 0) {
        refs.push(normalized);
      }
      continue;
    }

    if (typeof entry === 'object' && entry !== null && 'ref' in entry && typeof entry.ref === 'string') {
      const normalized = entry.ref.trim();
      if (normalized.length > 0) {
        refs.push(normalized);
      }
    }
  }

  return [...new Set(refs)];
}

function createDecisionCardMissingFields(card: {
  decisionContext: string | null;
  consideredOptions: readonly string[];
  selectedOption: string | null;
  rationale: string | null;
  impact: string | null;
  evidenceRefs: readonly string[];
}): DecisionCardRequiredField[] {
  const missingFields: DecisionCardRequiredField[] = [];

  if (card.decisionContext === null) {
    missingFields.push('context');
  }

  if (card.consideredOptions.length === 0) {
    missingFields.push('options');
  }

  if (card.selectedOption === null) {
    missingFields.push('choice');
  }

  if (card.rationale === null) {
    missingFields.push('rationale');
  }

  if (card.impact === null) {
    missingFields.push('impact');
  }

  if (card.evidenceRefs.length === 0) {
    missingFields.push('evidence');
  }

  return missingFields;
}

function createRuntimeErrorAlerts(lastErrors: readonly RuntimeErrorEvent[]): BoardAlert[] {
  return [...lastErrors].reverse().map((error) => ({
    level: 'error',
    code: 'RUNTIME_ERROR',
    message:
      error.correlationId === undefined
        ? `${error.code}: ${error.message}`
        : `${error.code}: ${error.message} (correlation ${error.correlationId})`,
    sequenceId: error.sequenceId
  }));
}

function indexChildAgentIds(state: GameState): Map<string, string[]> {
  const childAgentIdsByParentId = new Map<string, string[]>();

  for (const agent of Object.values(state.agents)) {
    if (agent.parentId === undefined || agent.parentId === null) {
      continue;
    }

    const childAgentIds = childAgentIdsByParentId.get(agent.parentId) ?? [];
    childAgentIds.push(agent.id);
    childAgentIdsByParentId.set(agent.parentId, childAgentIds);
  }

  return childAgentIdsByParentId;
}

function indexTaskIdsByAssignee(tasks: readonly TaskSnapshot[]): Map<string, string[]> {
  const taskIdsByAssigneeId = new Map<string, string[]>();

  for (const task of tasks) {
    if (task.assigneeId === undefined || task.assigneeId === null) {
      continue;
    }

    const taskIds = taskIdsByAssigneeId.get(task.assigneeId) ?? [];
    taskIds.push(task.id);
    taskIdsByAssigneeId.set(task.assigneeId, taskIds);
  }

  return taskIdsByAssigneeId;
}

function isActiveTask(task: TaskSnapshot): boolean {
  return task.status === 'todo' || task.status === 'in_progress' || task.status === 'review';
}

function compareBoardAlerts(left: BoardAlert, right: BoardAlert): number {
  const codeRankDelta = ALERT_CODE_RANK[left.code] - ALERT_CODE_RANK[right.code];
  if (codeRankDelta !== 0) {
    return codeRankDelta;
  }

  const leftSequenceId = left.sequenceId ?? -1;
  const rightSequenceId = right.sequenceId ?? -1;
  if (leftSequenceId !== rightSequenceId) {
    return rightSequenceId - leftSequenceId;
  }

  return left.message.localeCompare(right.message);
}

function compareTaskSnapshots(left: TaskSnapshot, right: TaskSnapshot): number {
  const statusRankDelta = TASK_STATUS_RANK[left.status] - TASK_STATUS_RANK[right.status];
  if (statusRankDelta !== 0) {
    return statusRankDelta;
  }

  const titleDelta = left.title.localeCompare(right.title);
  if (titleDelta !== 0) {
    return titleDelta;
  }

  return left.id.localeCompare(right.id);
}

function compareAgentPresence(left: AgentPresence, right: AgentPresence): number {
  const roleRankDelta = AGENT_ROLE_RANK[left.role] - AGENT_ROLE_RANK[right.role];
  if (roleRankDelta !== 0) {
    return roleRankDelta;
  }

  const statusRankDelta = AGENT_STATUS_RANK[left.status] - AGENT_STATUS_RANK[right.status];
  if (statusRankDelta !== 0) {
    return statusRankDelta;
  }

  const roomDelta = left.roomId.localeCompare(right.roomId);
  if (roomDelta !== 0) {
    return roomDelta;
  }

  const nameDelta = left.name.localeCompare(right.name);
  if (nameDelta !== 0) {
    return nameDelta;
  }

  return left.id.localeCompare(right.id);
}

function compareBoardAgents(left: BoardAgentSummary | undefined, right: BoardAgentSummary | undefined): number {
  if (left === undefined && right === undefined) {
    return 0;
  }

  if (left === undefined) {
    return 1;
  }

  if (right === undefined) {
    return -1;
  }

  const roleRankDelta = AGENT_ROLE_RANK[left.role] - AGENT_ROLE_RANK[right.role];
  if (roleRankDelta !== 0) {
    return roleRankDelta;
  }

  const statusRankDelta = AGENT_STATUS_RANK[left.status] - AGENT_STATUS_RANK[right.status];
  if (statusRankDelta !== 0) {
    return statusRankDelta;
  }

  const nameDelta = left.name.localeCompare(right.name);
  if (nameDelta !== 0) {
    return nameDelta;
  }

  return left.id.localeCompare(right.id);
}

function compareAgentsById(state: GameState, leftAgentId: string, rightAgentId: string): number {
  const left = state.agents[leftAgentId];
  const right = state.agents[rightAgentId];

  if (left === undefined && right === undefined) {
    return leftAgentId.localeCompare(rightAgentId);
  }

  if (left === undefined) {
    return 1;
  }

  if (right === undefined) {
    return -1;
  }

  return compareAgentPresence(left, right);
}

function countAgentsByStatus(agents: readonly BoardAgentSummary[], status: AgentStatus): number {
  return agents.filter((agent) => agent.status === status).length;
}