import type { TaskStatus } from '../contracts/events';

import { createAuditView, type AuditEntry } from './audit-view';
import { createBoardView, type BoardDecisionCard } from './board-view';
import type { GameState, WorkflowStepLogEntry } from './game-state';
import { createTaskView } from './task-view';

export type WorkflowVisualizationStepStatus = 'completed' | 'active';

export interface WorkflowVisualizationContributor {
  agentId: string;
  agentName: string | null;
  roomId: string | null;
  stepCount: number;
  decisionCount: number;
}

export interface WorkflowVisualizationStep {
  id: string;
  title: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
  taskId: string | null;
  traceId: string | null;
  status: WorkflowVisualizationStepStatus;
  dependsOn: readonly string[];
  decisionIds: readonly string[];
}

export interface WorkflowVisualizationEdge {
  id: string;
  fromStepId: string;
  toStepId: string;
  kind: 'sequence';
}

export interface WorkflowVisualizationDecision {
  id: string;
  title: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
  taskId: string | null;
  traceId: string | null;
  stepId: string | null;
  evidenceCount: number;
  supportingToolCount: number;
}

export interface WorkflowVisualizationAuditEntry {
  id: string;
  kind: AuditEntry['kind'];
  title: string;
  detail: string;
  sourceEventType: string;
  sequenceId: number;
  timestamp: string;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
  taskId: string | null;
  traceId: string | null;
  relatedStepId: string | null;
  relatedDecisionId: string | null;
}

export interface WorkflowVisualizationPath {
  id: string;
  traceId: string | null;
  taskId: string | null;
  taskTitle: string | null;
  taskStatus: TaskStatus | null;
  roomId: string | null;
  isActive: boolean;
  currentStepId: string | null;
  contributorAgentIds: readonly string[];
  contributors: readonly WorkflowVisualizationContributor[];
  steps: readonly WorkflowVisualizationStep[];
  edges: readonly WorkflowVisualizationEdge[];
  decisions: readonly WorkflowVisualizationDecision[];
  auditTrail: readonly WorkflowVisualizationAuditEntry[];
}

export interface WorkflowVisualizationFocus {
  pathId: string | null;
  traceId: string | null;
  taskId: string | null;
  currentStepId: string | null;
}

export interface WorkflowVisualizationView {
  protocolVersion: string;
  lastSequenceId: number;
  focus: WorkflowVisualizationFocus;
  paths: readonly WorkflowVisualizationPath[];
}

export interface WorkflowVisualizationViewOptions {
  traceId?: string;
  taskId?: string;
  includeCompleted?: boolean;
  maxAuditEntries?: number;
}

export function createWorkflowVisualizationView(
  state: GameState,
  options: WorkflowVisualizationViewOptions = {}
): WorkflowVisualizationView {
  const taskView = createTaskView(state);
  const boardView = createBoardView(state);
  const auditView = createAuditView(state);
  const taskById = Object.fromEntries(taskView.tasks.map((task) => [task.task.id, task]));
  const groups = groupWorkflowSteps(state.recentWorkflowSteps);
  const paths = Array.from(groups.entries())
    .map(([groupKey, workflowSteps]) =>
      createWorkflowPath(groupKey, workflowSteps, state, boardView.decisionCards, auditView.entries, taskById, options)
    )
    .filter((path): path is WorkflowVisualizationPath => path !== null)
    .sort(compareWorkflowPaths);
  const focusPath = paths[0] ?? null;

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    focus: {
      pathId: focusPath?.id ?? null,
      traceId: focusPath?.traceId ?? null,
      taskId: focusPath?.taskId ?? null,
      currentStepId: focusPath?.currentStepId ?? null
    },
    paths
  };
}

function groupWorkflowSteps(workflowSteps: readonly WorkflowStepLogEntry[]): Map<string, WorkflowStepLogEntry[]> {
  const groups = new Map<string, WorkflowStepLogEntry[]>();

  for (const workflowStep of [...workflowSteps].sort((left, right) => left.sequenceId - right.sequenceId)) {
    const groupKey = createWorkflowGroupKey(workflowStep);
    const group = groups.get(groupKey) ?? [];
    group.push(workflowStep);
    groups.set(groupKey, group);
  }

  return groups;
}

function createWorkflowPath(
  groupKey: string,
  workflowSteps: readonly WorkflowStepLogEntry[],
  state: GameState,
  decisionCards: readonly BoardDecisionCard[],
  auditEntries: readonly AuditEntry[],
  taskById: Record<string, ReturnType<typeof createTaskView>['tasks'][number]>,
  options: WorkflowVisualizationViewOptions
): WorkflowVisualizationPath | null {
  const traceId = workflowSteps.find((step) => step.traceId !== undefined)?.traceId ?? null;
  const taskId = workflowSteps.find((step) => step.taskId !== undefined)?.taskId ?? null;
  const taskInspection = taskId === null ? null : (taskById[taskId] ?? null);
  const taskTitle = taskInspection?.task.title ?? null;
  const taskStatus = taskInspection?.task.status ?? null;

  if (!matchesWorkflowOptions(traceId, taskId, taskStatus, options)) {
    return null;
  }

  const pathDecisionCards = decisionCards
    .filter((card) => belongsToWorkflow(card.traceId, card.taskId, traceId, taskId))
    .sort((left, right) => left.sequenceId - right.sequenceId);
  const decisionIdsBySequence = new Map(pathDecisionCards.map((card) => [card.sequenceId, card.id]));
  const currentStep = workflowSteps[workflowSteps.length - 1] ?? null;
  const currentStepId = currentStep === null ? null : createWorkflowStepId(currentStep.sequenceId);
  const steps = workflowSteps.map((workflowStep, index) => {
    const agent = workflowStep.agentId === undefined ? undefined : state.agents[workflowStep.agentId];
    const stepId = createWorkflowStepId(workflowStep.sequenceId);
    const previousStep = index === 0 ? null : workflowSteps[index - 1];
    const decisionId = decisionIdsBySequence.get(workflowStep.sequenceId);

    return {
      id: stepId,
      title: workflowStep.step,
      detail: workflowStep.detail,
      sourceEventType: workflowStep.sourceEventType,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp,
      agentId: workflowStep.agentId ?? null,
      agentName: agent?.name ?? null,
      roomId: agent?.roomId ?? null,
      taskId: workflowStep.taskId ?? null,
      traceId: workflowStep.traceId ?? null,
      status: currentStepId === stepId && isActiveTaskStatus(taskStatus) ? 'active' : 'completed',
      dependsOn: previousStep == null ? [] : [createWorkflowStepId(previousStep.sequenceId)],
      decisionIds: decisionId === undefined ? [] : [decisionId]
    } satisfies WorkflowVisualizationStep;
  });
  const stepIdsBySequence = new Map(steps.map((step) => [step.sequenceId, step.id]));
  const contributors = createWorkflowContributors(workflowSteps, pathDecisionCards, state);
  const decisions = pathDecisionCards.map((card) => ({
    id: card.id,
    title: card.title,
    detail: card.detail,
    sourceEventType: card.sourceEventType,
    sequenceId: card.sequenceId,
    timestamp: card.timestamp,
    agentId: card.agentId,
    agentName: card.agentId === null ? null : (state.agents[card.agentId]?.name ?? null),
    roomId: card.roomId,
    taskId: card.taskId,
    traceId: card.traceId,
    stepId: stepIdsBySequence.get(card.sequenceId) ?? null,
    evidenceCount: card.evidence.length,
    supportingToolCount: card.supportingToolCalls.length
  }));
  const edges = createWorkflowEdges(steps);
  const auditTrail = auditEntries
    .filter((entry) => belongsToWorkflow(entry.traceId, entry.taskId, traceId, taskId))
    .sort((left, right) => left.sequenceId - right.sequenceId)
    .slice(-normalizeAuditEntryLimit(options.maxAuditEntries))
    .map((entry) => ({
      id: entry.id,
      kind: entry.kind,
      title: entry.title,
      detail: entry.detail,
      sourceEventType: entry.sourceEventType,
      sequenceId: entry.sequenceId,
      timestamp: entry.timestamp,
      agentId: entry.agentId,
      agentName: entry.agentName,
      roomId: entry.roomId,
      taskId: entry.taskId,
      traceId: entry.traceId,
      relatedStepId: stepIdsBySequence.get(entry.sequenceId) ?? null,
      relatedDecisionId: decisionIdsBySequence.get(entry.sequenceId) ?? null
    }));

  return {
    id: `workflow-path:${groupKey}`,
    traceId,
    taskId,
    taskTitle,
    taskStatus,
    roomId: currentStep?.agentId === undefined ? null : (state.agents[currentStep.agentId]?.roomId ?? null),
    isActive: isActiveTaskStatus(taskStatus),
    currentStepId,
    contributorAgentIds: contributors.map((contributor) => contributor.agentId),
    contributors,
    steps,
    edges,
    decisions,
    auditTrail
  };
}

function createWorkflowContributors(
  workflowSteps: readonly WorkflowStepLogEntry[],
  decisionCards: readonly BoardDecisionCard[],
  state: GameState
): WorkflowVisualizationContributor[] {
  const stepCountByAgentId = new Map<string, number>();
  const decisionCountByAgentId = new Map<string, number>();

  for (const workflowStep of workflowSteps) {
    if (workflowStep.agentId === undefined) {
      continue;
    }

    stepCountByAgentId.set(workflowStep.agentId, (stepCountByAgentId.get(workflowStep.agentId) ?? 0) + 1);
  }

  for (const decisionCard of decisionCards) {
    if (decisionCard.agentId === null) {
      continue;
    }

    decisionCountByAgentId.set(decisionCard.agentId, (decisionCountByAgentId.get(decisionCard.agentId) ?? 0) + 1);
  }

  return [...new Set([...stepCountByAgentId.keys(), ...decisionCountByAgentId.keys()])]
    .map((agentId) => ({
      agentId,
      agentName: state.agents[agentId]?.name ?? null,
      roomId: state.agents[agentId]?.roomId ?? null,
      stepCount: stepCountByAgentId.get(agentId) ?? 0,
      decisionCount: decisionCountByAgentId.get(agentId) ?? 0
    }))
    .sort(compareWorkflowContributors);
}

function createWorkflowEdges(steps: readonly WorkflowVisualizationStep[]): WorkflowVisualizationEdge[] {
  const edges: WorkflowVisualizationEdge[] = [];

  for (let index = 1; index < steps.length; index += 1) {
    const previousStep = steps[index - 1];
    const currentStep = steps[index];
    if (previousStep === undefined || currentStep === undefined) {
      continue;
    }

    edges.push({
      id: `workflow-edge:${previousStep.id}->${currentStep.id}`,
      fromStepId: previousStep.id,
      toStepId: currentStep.id,
      kind: 'sequence'
    });
  }

  return edges;
}

function matchesWorkflowOptions(
  traceId: string | null,
  taskId: string | null,
  taskStatus: TaskStatus | null,
  options: WorkflowVisualizationViewOptions
): boolean {
  if (options.traceId !== undefined && options.traceId !== traceId) {
    return false;
  }

  if (options.taskId !== undefined && options.taskId !== taskId) {
    return false;
  }

  if (options.includeCompleted === false && !isActiveTaskStatus(taskStatus)) {
    return false;
  }

  return true;
}

function belongsToWorkflow(
  candidateTraceId: string | null,
  candidateTaskId: string | null,
  traceId: string | null,
  taskId: string | null
): boolean {
  if (traceId !== null) {
    return candidateTraceId === traceId;
  }

  if (taskId !== null) {
    return candidateTaskId === taskId;
  }

  return false;
}

function createWorkflowGroupKey(workflowStep: WorkflowStepLogEntry): string {
  if (workflowStep.traceId !== undefined) {
    return `trace:${workflowStep.traceId}`;
  }

  if (workflowStep.taskId !== undefined) {
    return `task:${workflowStep.taskId}`;
  }

  if (workflowStep.agentId !== undefined) {
    return `agent:${workflowStep.agentId}`;
  }

  return `step:${workflowStep.sequenceId}`;
}

function createWorkflowStepId(sequenceId: number): string {
  return `workflow-step:${sequenceId}`;
}

function normalizeAuditEntryLimit(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value)) {
    return 12;
  }

  return Math.max(1, Math.trunc(value));
}

function isActiveTaskStatus(taskStatus: TaskStatus | null): boolean {
  return taskStatus === 'todo' || taskStatus === 'in_progress' || taskStatus === 'review';
}

function compareWorkflowPaths(left: WorkflowVisualizationPath, right: WorkflowVisualizationPath): number {
  if (left.isActive !== right.isActive) {
    return left.isActive ? -1 : 1;
  }

  const leftSequenceId = left.steps[left.steps.length - 1]?.sequenceId ?? -1;
  const rightSequenceId = right.steps[right.steps.length - 1]?.sequenceId ?? -1;
  if (leftSequenceId !== rightSequenceId) {
    return rightSequenceId - leftSequenceId;
  }

  const leftLabel = left.taskTitle ?? left.traceId ?? left.id;
  const rightLabel = right.taskTitle ?? right.traceId ?? right.id;
  return leftLabel.localeCompare(rightLabel);
}

function compareWorkflowContributors(
  left: WorkflowVisualizationContributor,
  right: WorkflowVisualizationContributor
): number {
  if (left.stepCount !== right.stepCount) {
    return right.stepCount - left.stepCount;
  }

  if (left.decisionCount !== right.decisionCount) {
    return right.decisionCount - left.decisionCount;
  }

  return left.agentId.localeCompare(right.agentId);
}