import type { TaskPriority } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';

export const INCIDENT_RECOVERY_SCENARIO_ORDER = [
  'ws_unavailable',
  'duplicate',
  'out_of_order',
  'replay_partial',
  'adapter_unavailable'
] as const;
export const INCIDENT_RECOVERY_PHASE_ORDER = ['detection', 'containment', 'recovery', 'verification'] as const;
export const INCIDENT_RECOVERY_ISSUE_ORDER = [
  'INCIDENT_RUNBOOK_MISSING',
  'INCIDENT_EXERCISE_MISSING',
  'INCIDENT_RECOVERY_CHECKLIST_INCOMPLETE',
  'INCIDENT_RESYNC_PROOF_MISSING'
] as const;

export type IncidentRecoveryScenario = (typeof INCIDENT_RECOVERY_SCENARIO_ORDER)[number];
export type IncidentRecoveryPhase = (typeof INCIDENT_RECOVERY_PHASE_ORDER)[number];
export type IncidentRecoveryIssueCode = (typeof INCIDENT_RECOVERY_ISSUE_ORDER)[number];

export interface IncidentRunbookEntry {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  scenario: IncidentRecoveryScenario;
  runbookRef: string;
  traceId: string | null;
  sourceSequenceId: number;
}

export interface IncidentRecoveryExerciseEntry {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  scenario: IncidentRecoveryScenario;
  exerciseRef: string;
  runbookRef: string | null;
  checklistCompletedPhases: readonly IncidentRecoveryPhase[];
  checklistMissingPhases: readonly IncidentRecoveryPhase[];
  beforeStateRef: string | null;
  afterStateRef: string | null;
  resyncProofRef: string | null;
  isChecklistComplete: boolean;
  traceId: string | null;
  sourceSequenceId: number;
}

export interface IncidentRecoveryTaskGate {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  isApplicable: boolean;
  isReady: boolean;
  scenarios: readonly IncidentRecoveryScenario[];
  runbooks: readonly IncidentRunbookEntry[];
  exercises: readonly IncidentRecoveryExerciseEntry[];
  issueCodes: readonly IncidentRecoveryIssueCode[];
  blockingReason: string | null;
}

export interface IncidentRecoveryViewSummary {
  taskCount: number;
  applicableCount: number;
  readyCount: number;
  blockedCount: number;
  runbookCount: number;
  exerciseCount: number;
  proofReadyCount: number;
}

export interface IncidentRecoveryView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly IncidentRecoveryTaskGate[];
  runbooks: readonly IncidentRunbookEntry[];
  exercises: readonly IncidentRecoveryExerciseEntry[];
  summary: IncidentRecoveryViewSummary;
}

interface IncidentRecoveryScenarioContext {
  scenario: IncidentRecoveryScenario;
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  runbookRef: string | null;
  exerciseRef: string | null;
  checklistCompletedPhases: Set<IncidentRecoveryPhase>;
  beforeStateRef: string | null;
  afterStateRef: string | null;
  resyncProofRef: string | null;
  traceId: string | null;
  sourceSequenceId: number;
}

const TASK_PRIORITY_RANK: Record<TaskPriority | 'none', number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4
};

export function createIncidentRecoveryView(state: GameState): IncidentRecoveryView {
  const taskView = createTaskView(state);
  const tasks = taskView.tasks.map(createIncidentRecoveryTaskGate).sort(compareIncidentRecoveryTaskGates);
  const runbooks = tasks.flatMap((task) => task.runbooks).sort(compareIncidentRunbooks);
  const exercises = tasks.flatMap((task) => task.exercises).sort(compareIncidentExercises);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks,
    runbooks,
    exercises,
    summary: {
      taskCount: tasks.length,
      applicableCount: tasks.filter((task) => task.isApplicable).length,
      readyCount: tasks.filter((task) => task.isApplicable && task.isReady).length,
      blockedCount: tasks.filter((task) => task.isApplicable && !task.isReady).length,
      runbookCount: runbooks.length,
      exerciseCount: exercises.length,
      proofReadyCount: exercises.filter(
        (exercise) => exercise.isChecklistComplete && exercise.beforeStateRef !== null && exercise.afterStateRef !== null && exercise.resyncProofRef !== null
      ).length
    }
  };
}

export function evaluateTaskIncidentRecoveryGate(state: GameState, taskId: string): IncidentRecoveryTaskGate | null {
  return createIncidentRecoveryView(state).tasks.find((task) => task.taskId === taskId) ?? null;
}

function createIncidentRecoveryTaskGate(task: TaskInspectionView): IncidentRecoveryTaskGate {
  const scenarioContexts = collectScenarioContexts(task);
  const contexts = Array.from(scenarioContexts.values()).sort(compareIncidentScenarioContexts);
  const runbooks = contexts
    .filter((context) => context.runbookRef !== null)
    .map<IncidentRunbookEntry>((context) => ({
      taskId: context.taskId,
      taskTitle: context.taskTitle,
      priority: context.priority,
      scenario: context.scenario,
      runbookRef: context.runbookRef ?? 'runbook://missing',
      traceId: context.traceId,
      sourceSequenceId: context.sourceSequenceId
    }));
  const exercises = contexts
    .filter((context) => context.exerciseRef !== null)
    .map<IncidentRecoveryExerciseEntry>((context) => {
      const checklistCompletedPhases = Array.from(context.checklistCompletedPhases.values()).sort(compareIncidentPhases);
      const checklistMissingPhases = INCIDENT_RECOVERY_PHASE_ORDER.filter(
        (phase) => !context.checklistCompletedPhases.has(phase)
      );

      return {
        taskId: context.taskId,
        taskTitle: context.taskTitle,
        priority: context.priority,
        scenario: context.scenario,
        exerciseRef: context.exerciseRef ?? 'exercise://missing',
        runbookRef: context.runbookRef,
        checklistCompletedPhases,
        checklistMissingPhases,
        beforeStateRef: context.beforeStateRef,
        afterStateRef: context.afterStateRef,
        resyncProofRef: context.resyncProofRef,
        isChecklistComplete: checklistMissingPhases.length === 0,
        traceId: context.traceId,
        sourceSequenceId: context.sourceSequenceId
      };
    });
  const issueCodes = createIncidentIssueCodes(contexts, exercises);

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    isApplicable: contexts.length > 0,
    isReady: issueCodes.length === 0,
    scenarios: contexts.map((context) => context.scenario),
    runbooks,
    exercises,
    issueCodes,
    blockingReason:
      issueCodes.length === 0 ? null : describeIncidentBlockingReason(task.task.title, contexts, exercises, issueCodes)
  };
}

function collectScenarioContexts(task: TaskInspectionView): Map<IncidentRecoveryScenario, IncidentRecoveryScenarioContext> {
  const contexts = new Map<IncidentRecoveryScenario, IncidentRecoveryScenarioContext>();

  for (const workflowStep of [...task.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const scenario = readIncidentScenario(workflowStep);
    if (scenario === null) {
      continue;
    }

    const context =
      contexts.get(scenario) ??
      createIncidentScenarioContext(task, scenario, workflowStep.sequenceId, workflowStep.traceId ?? null);
    const metadata = workflowStep.metadata as Record<string, unknown>;

    if (context.runbookRef === null) {
      context.runbookRef = readStringByKeys(metadata, ['runbookRef', 'runbook_ref']);
    }

    if (context.exerciseRef === null) {
      context.exerciseRef = readStringByKeys(metadata, ['exerciseRef', 'exercise_ref', 'recoveryExerciseRef', 'recovery_exercise_ref']);
    }

    if (context.beforeStateRef === null) {
      context.beforeStateRef = readStringByKeys(metadata, ['beforeStateRef', 'before_state_ref']);
    }

    if (context.afterStateRef === null) {
      context.afterStateRef = readStringByKeys(metadata, ['afterStateRef', 'after_state_ref']);
    }

    if (context.resyncProofRef === null) {
      context.resyncProofRef = readStringByKeys(metadata, ['resyncProofRef', 'resync_proof_ref', 'recoveryProofRef', 'recovery_proof_ref']);
    }

    for (const phase of readIncidentPhases(metadata)) {
      context.checklistCompletedPhases.add(phase);
    }

    contexts.set(scenario, context);
  }

  return contexts;
}

function createIncidentScenarioContext(
  task: TaskInspectionView,
  scenario: IncidentRecoveryScenario,
  sourceSequenceId: number,
  traceId: string | null
): IncidentRecoveryScenarioContext {
  return {
    scenario,
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    runbookRef: null,
    exerciseRef: null,
    checklistCompletedPhases: new Set<IncidentRecoveryPhase>(),
    beforeStateRef: null,
    afterStateRef: null,
    resyncProofRef: null,
    traceId,
    sourceSequenceId
  };
}

function createIncidentIssueCodes(
  contexts: readonly IncidentRecoveryScenarioContext[],
  exercises: readonly IncidentRecoveryExerciseEntry[]
): IncidentRecoveryIssueCode[] {
  const issueCodes = new Set<IncidentRecoveryIssueCode>();
  const exerciseByScenario = new Map(exercises.map((exercise) => [exercise.scenario, exercise]));

  for (const context of contexts) {
    if (context.runbookRef === null) {
      issueCodes.add('INCIDENT_RUNBOOK_MISSING');
    }

    const exercise = exerciseByScenario.get(context.scenario);
    if (exercise === undefined) {
      issueCodes.add('INCIDENT_EXERCISE_MISSING');
      continue;
    }

    if (!exercise.isChecklistComplete) {
      issueCodes.add('INCIDENT_RECOVERY_CHECKLIST_INCOMPLETE');
    }

    if (exercise.beforeStateRef === null || exercise.afterStateRef === null || exercise.resyncProofRef === null) {
      issueCodes.add('INCIDENT_RESYNC_PROOF_MISSING');
    }
  }

  return INCIDENT_RECOVERY_ISSUE_ORDER.filter((issueCode) => issueCodes.has(issueCode));
}

function readIncidentScenario(workflowStep: WorkflowStepLogEntry): IncidentRecoveryScenario | null {
  const metadata = workflowStep.metadata as Record<string, unknown>;
  const explicitScenario = readStringByKeys(metadata, [
    'incidentType',
    'incident_type',
    'scenario',
    'recoveryScenario',
    'recovery_scenario'
  ]);
  if (explicitScenario !== null) {
    return normalizeIncidentScenario(explicitScenario);
  }

  return null;
}

function readIncidentPhases(metadata: Record<string, unknown>): IncidentRecoveryPhase[] {
  const rawPhases = readStringListByKeys(metadata, [
    'recoveryChecklist',
    'recovery_checklist',
    'completedPhases',
    'completed_phases',
    'checklistPhases',
    'checklist_phases'
  ]);

  return [...new Set(rawPhases.map(normalizeIncidentPhase).filter((phase): phase is IncidentRecoveryPhase => phase !== null))].sort(
    compareIncidentPhases
  );
}

function normalizeIncidentScenario(value: string): IncidentRecoveryScenario | null {
  const normalizedValue = value.trim().toLowerCase().replace(/-/g, '_');

  switch (normalizedValue) {
    case 'ws_unavailable':
    case 'websocket_unavailable':
    case 'ws_down':
      return 'ws_unavailable';
    case 'duplicate':
    case 'duplicate_event':
      return 'duplicate';
    case 'out_of_order':
    case 'out_of_order_event':
      return 'out_of_order';
    case 'replay_partial':
    case 'partial_replay':
      return 'replay_partial';
    case 'adapter_unavailable':
    case 'adapter_down':
      return 'adapter_unavailable';
    default:
      return null;
  }
}

function normalizeIncidentPhase(value: string): IncidentRecoveryPhase | null {
  const normalizedValue = value.trim().toLowerCase();

  switch (normalizedValue) {
    case 'detection':
    case 'detect':
      return 'detection';
    case 'containment':
    case 'contain':
      return 'containment';
    case 'recovery':
    case 'recover':
      return 'recovery';
    case 'verification':
    case 'verify':
      return 'verification';
    default:
      return null;
  }
}

function compareIncidentRecoveryTaskGates(left: IncidentRecoveryTaskGate, right: IncidentRecoveryTaskGate): number {
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

function compareIncidentRunbooks(left: IncidentRunbookEntry, right: IncidentRunbookEntry): number {
  if (left.taskTitle !== right.taskTitle) {
    return left.taskTitle.localeCompare(right.taskTitle);
  }

  return compareIncidentScenarios(left.scenario, right.scenario);
}

function compareIncidentExercises(left: IncidentRecoveryExerciseEntry, right: IncidentRecoveryExerciseEntry): number {
  if (left.taskTitle !== right.taskTitle) {
    return left.taskTitle.localeCompare(right.taskTitle);
  }

  if (left.isChecklistComplete !== right.isChecklistComplete) {
    return left.isChecklistComplete ? 1 : -1;
  }

  return compareIncidentScenarios(left.scenario, right.scenario);
}

function compareIncidentScenarioContexts(
  left: IncidentRecoveryScenarioContext,
  right: IncidentRecoveryScenarioContext
): number {
  return compareIncidentScenarios(left.scenario, right.scenario);
}

function compareIncidentScenarios(left: IncidentRecoveryScenario, right: IncidentRecoveryScenario): number {
  return INCIDENT_RECOVERY_SCENARIO_ORDER.indexOf(left) - INCIDENT_RECOVERY_SCENARIO_ORDER.indexOf(right);
}

function compareIncidentPhases(left: IncidentRecoveryPhase, right: IncidentRecoveryPhase): number {
  return INCIDENT_RECOVERY_PHASE_ORDER.indexOf(left) - INCIDENT_RECOVERY_PHASE_ORDER.indexOf(right);
}

function describeIncidentBlockingReason(
  taskTitle: string,
  contexts: readonly IncidentRecoveryScenarioContext[],
  exercises: readonly IncidentRecoveryExerciseEntry[],
  issueCodes: readonly IncidentRecoveryIssueCode[]
): string {
  const scenarioNames = contexts.map((context) => formatIncidentScenario(context.scenario)).join(', ');
  const messages: string[] = [];
  const incompleteExercises = exercises.filter((exercise) => !exercise.isChecklistComplete);
  const missingProofExercises = exercises.filter(
    (exercise) => exercise.beforeStateRef === null || exercise.afterStateRef === null || exercise.resyncProofRef === null
  );

  if (issueCodes.includes('INCIDENT_RUNBOOK_MISSING')) {
    messages.push(`Critical task ${taskTitle} is missing a versioned runbook for ${scenarioNames}.`);
  }

  if (issueCodes.includes('INCIDENT_EXERCISE_MISSING')) {
    messages.push(`Critical task ${taskTitle} is missing a traced recovery exercise for ${scenarioNames}.`);
  }

  if (issueCodes.includes('INCIDENT_RECOVERY_CHECKLIST_INCOMPLETE')) {
    const missingPhases = incompleteExercises.flatMap((exercise) => exercise.checklistMissingPhases);
    messages.push(
      `Critical task ${taskTitle} has an incomplete recovery checklist: ${[...new Set(missingPhases)].join(', ')}.`
    );
  }

  if (issueCodes.includes('INCIDENT_RESYNC_PROOF_MISSING')) {
    const scenariosMissingProof = missingProofExercises.map((exercise) => formatIncidentScenario(exercise.scenario));
    messages.push(
      `Critical task ${taskTitle} is missing before/after state or resync proof for ${[...new Set(scenariosMissingProof)].join(', ')}.`
    );
  }

  return messages.join(' ');
}

function formatIncidentScenario(scenario: IncidentRecoveryScenario): string {
  switch (scenario) {
    case 'ws_unavailable':
      return 'WS unavailable';
    case 'duplicate':
      return 'duplicate';
    case 'out_of_order':
      return 'out-of-order';
    case 'replay_partial':
      return 'partial replay';
    case 'adapter_unavailable':
      return 'adapter unavailable';
  }
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