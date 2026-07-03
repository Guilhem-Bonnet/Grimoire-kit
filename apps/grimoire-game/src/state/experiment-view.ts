import type { TaskPriority } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';

export const EXPERIMENT_DECISION_ORDER = ['adopt', 'iterate', 'drop'] as const;
export const EXPERIMENT_REQUIRED_FIELD_ORDER = ['hypothesis', 'metric', 'guardrail', 'measurement', 'decision'] as const;

export type ExperimentDecision = (typeof EXPERIMENT_DECISION_ORDER)[number];
export type ExperimentRequiredField = (typeof EXPERIMENT_REQUIRED_FIELD_ORDER)[number];

export interface ExperimentRecord {
  experimentId: string;
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  theme: string;
  hypothesis: string | null;
  metric: string | null;
  guardrail: string | null;
  measurementRef: string | null;
  measurementSummary: string | null;
  decision: ExperimentDecision | null;
  linkedTaskIds: readonly string[];
  traceId: string | null;
  sourceSequenceId: number;
  missingFields: readonly ExperimentRequiredField[];
  isReady: boolean;
}

export interface ExperimentTaskGate {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  isApplicable: boolean;
  isReady: boolean;
  experimentIds: readonly string[];
  themes: readonly string[];
  decisions: readonly ExperimentDecision[];
  experiments: readonly ExperimentRecord[];
  missingFields: readonly ExperimentRequiredField[];
  blockingReason: string | null;
}

export interface ExperimentViewSummary {
  taskCount: number;
  applicableCount: number;
  readyCount: number;
  blockedCount: number;
  experimentCount: number;
  themeCount: number;
}

export interface ExperimentView {
  protocolVersion: string;
  lastSequenceId: number;
  experiments: readonly ExperimentRecord[];
  tasks: readonly ExperimentTaskGate[];
  summary: ExperimentViewSummary;
}

export interface ExperimentQuery {
  taskId?: string;
  theme?: string;
  decision?: ExperimentDecision;
}

export interface ExperimentQueryResult {
  experiments: readonly ExperimentRecord[];
  totalCount: number;
}

const TASK_PRIORITY_RANK: Record<TaskPriority | 'none', number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4
};

export function createExperimentView(state: GameState): ExperimentView {
  const taskView = createTaskView(state);
  const tasks = taskView.tasks.map(createExperimentTaskGate).sort(compareExperimentTaskGates);
  const experiments = tasks.flatMap((task) => task.experiments).sort(compareExperimentRecords);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    experiments,
    tasks,
    summary: {
      taskCount: tasks.length,
      applicableCount: tasks.filter((task) => task.isApplicable).length,
      readyCount: tasks.filter((task) => task.isApplicable && task.isReady).length,
      blockedCount: tasks.filter((task) => task.isApplicable && !task.isReady).length,
      experimentCount: experiments.length,
      themeCount: new Set(experiments.map((experiment) => experiment.theme)).size
    }
  };
}

export function queryExperimentView(view: ExperimentView, query: ExperimentQuery): ExperimentQueryResult {
  const experiments = view.experiments.filter((experiment) => matchesExperimentQuery(experiment, query));

  return {
    experiments,
    totalCount: experiments.length
  };
}

export function evaluateTaskExperimentGate(state: GameState, taskId: string): ExperimentTaskGate | null {
  return createExperimentView(state).tasks.find((task) => task.taskId === taskId) ?? null;
}

function createExperimentTaskGate(task: TaskInspectionView): ExperimentTaskGate {
  const experiments = collectExperiments(task);
  const missingFields = uniqueFields(experiments.flatMap((experiment) => experiment.missingFields));
  const decisions = [...new Set(experiments.map((experiment) => experiment.decision).filter((decision): decision is ExperimentDecision => decision !== null))];
  const themes = [...new Set(experiments.map((experiment) => experiment.theme))].sort((left, right) => left.localeCompare(right));

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    isApplicable: experiments.length > 0,
    isReady: experiments.length === 0 || experiments.every((experiment) => experiment.isReady),
    experimentIds: experiments.map((experiment) => experiment.experimentId),
    themes,
    decisions,
    experiments,
    missingFields,
    blockingReason:
      experiments.length === 0 || missingFields.length === 0
        ? null
        : describeExperimentBlockingReason(task.task.title, experiments)
  };
}

function collectExperiments(task: TaskInspectionView): ExperimentRecord[] {
  const experimentsById = new Map<string, ExperimentRecord>();

  for (const workflowStep of [...task.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    if (!isExperimentStep(workflowStep)) {
      continue;
    }

    const metadata = workflowStep.metadata as Record<string, unknown>;
    const experimentId =
      readStringByKeys(metadata, ['experimentId', 'experiment_id']) ??
      `experiment:${task.task.id}:${workflowStep.sequenceId}`;
    const current = experimentsById.get(experimentId);
    const linkedTaskIds = uniqueStrings([
      task.task.id,
      ...readStringListByKeys(metadata, ['linkedTaskIds', 'linked_task_ids', 'impactedTaskIds', 'impacted_task_ids'])
    ]);
    const next = current ?? {
      experimentId,
      taskId: task.task.id,
      taskTitle: task.task.title,
      priority: task.task.priority ?? null,
      theme: 'unscoped',
      hypothesis: null,
      metric: null,
      guardrail: null,
      measurementRef: null,
      measurementSummary: null,
      decision: null,
      linkedTaskIds,
      traceId: workflowStep.traceId ?? null,
      sourceSequenceId: workflowStep.sequenceId,
      missingFields: [],
      isReady: false
    };

    const record: ExperimentRecord = {
      ...next,
      theme: next.theme === 'unscoped' ? readStringByKeys(metadata, ['theme', 'experimentTheme', 'experiment_theme']) ?? next.theme : next.theme,
      hypothesis: next.hypothesis ?? readStringByKeys(metadata, ['hypothesis']),
      metric:
        next.metric ??
        readStringByKeys(metadata, ['metric', 'experimentMetric', 'experiment_metric', 'metricRef', 'metric_ref']),
      guardrail: next.guardrail ?? readStringByKeys(metadata, ['guardrail', 'experimentGuardrail', 'experiment_guardrail']),
      measurementRef:
        next.measurementRef ??
        readStringByKeys(metadata, [
          'measurementRef',
          'measurement_ref',
          'experimentMeasurementRef',
          'experiment_measurement_ref',
          'resultRef',
          'result_ref'
        ]),
      measurementSummary:
        next.measurementSummary ??
        readStringByKeys(metadata, ['measurement', 'measurementSummary', 'measurement_summary']),
      decision: next.decision ?? readExperimentDecision(metadata),
      linkedTaskIds: uniqueStrings([...next.linkedTaskIds, ...linkedTaskIds]),
      traceId: next.traceId ?? workflowStep.traceId ?? null,
      sourceSequenceId: next.sourceSequenceId
    };

    const missingFields = createExperimentMissingFields(record);
    experimentsById.set(experimentId, {
      ...record,
      missingFields,
      isReady: missingFields.length === 0
    });
  }

  return Array.from(experimentsById.values()).sort(compareExperimentRecords);
}

function isExperimentStep(workflowStep: WorkflowStepLogEntry): boolean {
  const metadata = workflowStep.metadata as Record<string, unknown>;
  if (readStringByKeys(metadata, ['experimentId', 'experiment_id']) !== null) {
    return true;
  }

  const topic = readStringByKeys(metadata, ['topic']);
  return topic !== null && topic.trim().toLowerCase() === 'experiment';
}

function createExperimentMissingFields(record: ExperimentRecord): ExperimentRequiredField[] {
  const missingFields: ExperimentRequiredField[] = [];

  if (record.hypothesis === null) {
    missingFields.push('hypothesis');
  }

  if (record.metric === null) {
    missingFields.push('metric');
  }

  if (record.guardrail === null) {
    missingFields.push('guardrail');
  }

  if (record.measurementRef === null && record.measurementSummary === null) {
    missingFields.push('measurement');
  }

  if (record.decision === null) {
    missingFields.push('decision');
  }

  return missingFields;
}

function readExperimentDecision(metadata: Record<string, unknown>): ExperimentDecision | null {
  const rawDecision = readStringByKeys(metadata, ['experimentDecision', 'experiment_decision', 'decision']);
  if (rawDecision === null) {
    return null;
  }

  const normalizedDecision = rawDecision.trim().toLowerCase();
  if (normalizedDecision === 'adopt') {
    return 'adopt';
  }

  if (normalizedDecision === 'iterate' || normalizedDecision === 'iterer') {
    return 'iterate';
  }

  if (normalizedDecision === 'drop') {
    return 'drop';
  }

  return null;
}

function matchesExperimentQuery(experiment: ExperimentRecord, query: ExperimentQuery): boolean {
  if (query.taskId !== undefined && experiment.taskId !== query.taskId && !experiment.linkedTaskIds.includes(query.taskId)) {
    return false;
  }

  if (query.theme !== undefined && experiment.theme !== query.theme) {
    return false;
  }

  if (query.decision !== undefined && experiment.decision !== query.decision) {
    return false;
  }

  return true;
}

function compareExperimentTaskGates(left: ExperimentTaskGate, right: ExperimentTaskGate): number {
  const leftPriority = left.priority ?? 'none';
  const rightPriority = right.priority ?? 'none';
  if (leftPriority !== rightPriority) {
    return TASK_PRIORITY_RANK[leftPriority] - TASK_PRIORITY_RANK[rightPriority];
  }

  if (left.isApplicable !== right.isApplicable) {
    return left.isApplicable ? -1 : 1;
  }

  if (left.isReady !== right.isReady) {
    return left.isReady ? 1 : -1;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareExperimentRecords(left: ExperimentRecord, right: ExperimentRecord): number {
  const leftPriority = left.priority ?? 'none';
  const rightPriority = right.priority ?? 'none';
  if (leftPriority !== rightPriority) {
    return TASK_PRIORITY_RANK[leftPriority] - TASK_PRIORITY_RANK[rightPriority];
  }

  if (left.theme !== right.theme) {
    return left.theme.localeCompare(right.theme);
  }

  if (left.taskTitle !== right.taskTitle) {
    return left.taskTitle.localeCompare(right.taskTitle);
  }

  if (left.isReady !== right.isReady) {
    return left.isReady ? 1 : -1;
  }

  return left.experimentId.localeCompare(right.experimentId);
}

function describeExperimentBlockingReason(
  taskTitle: string,
  experiments: readonly ExperimentRecord[]
): string {
  const summaries = experiments
    .filter((experiment) => !experiment.isReady)
    .map((experiment) => `${experiment.experimentId}: missing ${experiment.missingFields.join(', ')}`);

  return `Task ${taskTitle} cannot close experimentation without complete hypothesis/measurement/decision evidence. ${summaries.join(' ; ')}.`;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}

function uniqueFields(values: readonly ExperimentRequiredField[]): ExperimentRequiredField[] {
  return EXPERIMENT_REQUIRED_FIELD_ORDER.filter((field) => values.includes(field));
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