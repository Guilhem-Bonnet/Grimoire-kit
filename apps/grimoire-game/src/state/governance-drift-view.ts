import type { JsonValue, TaskPriority } from '../contracts/events';

import type { GameState, ToolCallLogEntry, WorkflowStepLogEntry } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';

export const GOVERNANCE_ARTIFACT_TYPE_ORDER = ['prompt', 'policy'] as const;
export const GOVERNANCE_DRIFT_ISSUE_ORDER = [
  'GOVERNANCE_VERSION_TRACE_MISSING',
  'GOVERNANCE_CANARY_REPORT_MISSING',
  'GOVERNANCE_DRIFT_THRESHOLD_EXCEEDED'
] as const;
export const DEFAULT_GOVERNANCE_DRIFT_THRESHOLD = 0.2;

export type GovernanceArtifactType = (typeof GOVERNANCE_ARTIFACT_TYPE_ORDER)[number];
export type GovernanceDriftIssueCode = (typeof GOVERNANCE_DRIFT_ISSUE_ORDER)[number];

export interface GovernanceVersionTrace {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  artifactType: GovernanceArtifactType;
  targetRef: string;
  baselineVersion: string | null;
  candidateVersion: string | null;
  source: 'workflow_step' | 'tool_call';
  sourceSequenceId: number;
  traceId: string | null;
}

export interface GovernanceCanaryScenarioResult {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  scenarioId: string;
  title: string;
  baselineVerdict: string | null;
  candidateVerdict: string | null;
  driftScore: number;
  threshold: number;
  exceedsThreshold: boolean;
  diagnostic: string | null;
  reportRef: string | null;
  traceId: string | null;
  sourceSequenceId: number;
}

export interface GovernanceDriftTaskGate {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  isApplicable: boolean;
  isReady: boolean;
  threshold: number;
  reportRef: string | null;
  maxDriftScore: number | null;
  targetRefs: readonly string[];
  versions: readonly GovernanceVersionTrace[];
  scenarios: readonly GovernanceCanaryScenarioResult[];
  issueCodes: readonly GovernanceDriftIssueCode[];
  blockingReason: string | null;
}

export interface GovernanceDriftViewSummary {
  taskCount: number;
  applicableCount: number;
  readyCount: number;
  blockedCount: number;
  versionTraceCount: number;
  scenarioCount: number;
  exceededScenarioCount: number;
}

export interface GovernanceDriftView {
  protocolVersion: string;
  lastSequenceId: number;
  defaultThreshold: number;
  tasks: readonly GovernanceDriftTaskGate[];
  register: readonly GovernanceVersionTrace[];
  reports: readonly GovernanceCanaryScenarioResult[];
  summary: GovernanceDriftViewSummary;
}

export interface GovernanceDriftViewOptions {
  threshold?: number;
}

interface GovernanceTargetSeed {
  artifactType: GovernanceArtifactType;
  targetRef: string;
  sourceSequenceId: number;
  traceId: string | null;
}

const TASK_PRIORITY_RANK: Record<TaskPriority | 'none', number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4
};

export function createGovernanceDriftView(
  state: GameState,
  options: GovernanceDriftViewOptions = {}
): GovernanceDriftView {
  const defaultThreshold = resolveGovernanceDriftThreshold(state.config, options.threshold);
  const taskView = createTaskView(state);
  const tasks = taskView.tasks
    .map((task) => createGovernanceDriftTaskGate(task, defaultThreshold))
    .sort(compareGovernanceTaskGates);
  const register = tasks.flatMap((task) => task.versions).sort(compareGovernanceVersionTraces);
  const reports = tasks.flatMap((task) => task.scenarios).sort(compareGovernanceScenarioResults);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    defaultThreshold,
    tasks,
    register,
    reports,
    summary: {
      taskCount: tasks.length,
      applicableCount: tasks.filter((task) => task.isApplicable).length,
      readyCount: tasks.filter((task) => task.isApplicable && task.isReady).length,
      blockedCount: tasks.filter((task) => task.isApplicable && !task.isReady).length,
      versionTraceCount: register.length,
      scenarioCount: reports.length,
      exceededScenarioCount: reports.filter((report) => report.exceedsThreshold).length
    }
  };
}

export function evaluateTaskGovernanceDriftGate(
  state: GameState,
  taskId: string,
  options: GovernanceDriftViewOptions = {}
): GovernanceDriftTaskGate | null {
  return createGovernanceDriftView(state, options).tasks.find((task) => task.taskId === taskId) ?? null;
}

function createGovernanceDriftTaskGate(
  task: TaskInspectionView,
  defaultThreshold: number
): GovernanceDriftTaskGate {
  const threshold = resolveTaskThreshold(task.recentWorkflowSteps, defaultThreshold);
  const versionsByKey = new Map<string, GovernanceVersionTrace>();
  const scenariosById = new Map<string, GovernanceCanaryScenarioResult>();
  const reportRef = readTaskReportRef(task.recentWorkflowSteps);
  let governanceChangeDetected = false;

  for (const workflowStep of [...task.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const metadata = workflowStep.metadata as Record<string, JsonValue>;

    if (readBooleanByKeys(metadata, ['governanceChangeDetected', 'governance_change_detected']) === true) {
      governanceChangeDetected = true;
    }

    for (const seed of readGovernanceTargetSeeds(metadata, workflowStep)) {
      governanceChangeDetected = true;
      ensureGovernanceVersionTrace(task, versionsByKey, seed);
    }

    for (const versionTrace of readGovernanceVersionTraces(task, workflowStep)) {
      governanceChangeDetected = true;
      const key = createGovernanceTraceKey(versionTrace.artifactType, versionTrace.targetRef);
      const currentTrace = versionsByKey.get(key);
      versionsByKey.set(key, mergeGovernanceVersionTrace(currentTrace, versionTrace));
    }

    for (const scenario of readGovernanceCanaryScenarios(task, workflowStep, threshold, reportRef)) {
      governanceChangeDetected = true;
      if (!scenariosById.has(scenario.scenarioId)) {
        scenariosById.set(scenario.scenarioId, scenario);
      }
    }
  }

  for (const seed of collectGovernanceToolSeeds(task.recentToolCalls)) {
    governanceChangeDetected = true;
    if (hasGovernanceVersionCoverage(versionsByKey, seed.artifactType)) {
      continue;
    }

    ensureGovernanceVersionTrace(task, versionsByKey, seed);
  }

  const versions = Array.from(versionsByKey.values()).sort(compareGovernanceVersionTraces);
  const scenarios = Array.from(scenariosById.values()).sort(compareGovernanceScenarioResults);
  const isApplicable = governanceChangeDetected || versions.length > 0 || scenarios.length > 0;
  const issueCodes: GovernanceDriftIssueCode[] = [];
  const hasIncompleteVersionTrace =
    isApplicable && (versions.length === 0 || versions.some((trace) => trace.baselineVersion === null || trace.candidateVersion === null));

  if (hasIncompleteVersionTrace) {
    issueCodes.push('GOVERNANCE_VERSION_TRACE_MISSING');
  }

  if (isApplicable && (reportRef === null || scenarios.length === 0)) {
    issueCodes.push('GOVERNANCE_CANARY_REPORT_MISSING');
  }

  const maxDriftScore = scenarios.length === 0 ? null : roundGovernanceMetric(Math.max(...scenarios.map((scenario) => scenario.driftScore)));
  if (isApplicable && maxDriftScore !== null && maxDriftScore > threshold) {
    issueCodes.push('GOVERNANCE_DRIFT_THRESHOLD_EXCEEDED');
  }

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    isApplicable,
    isReady: issueCodes.length === 0,
    threshold,
    reportRef,
    maxDriftScore,
    targetRefs: versions.map((trace) => trace.targetRef),
    versions,
    scenarios,
    issueCodes,
    blockingReason:
      issueCodes.length === 0 ? null : describeGovernanceBlockingReason(task.task.title, issueCodes, threshold, maxDriftScore)
  };
}

function ensureGovernanceVersionTrace(
  task: TaskInspectionView,
  versionsByKey: Map<string, GovernanceVersionTrace>,
  seed: GovernanceTargetSeed
): void {
  const key = createGovernanceTraceKey(seed.artifactType, seed.targetRef);
  if (versionsByKey.has(key)) {
    return;
  }

  versionsByKey.set(key, {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    artifactType: seed.artifactType,
    targetRef: seed.targetRef,
    baselineVersion: null,
    candidateVersion: null,
    source: 'tool_call',
    sourceSequenceId: seed.sourceSequenceId,
    traceId: seed.traceId
  });
}

function mergeGovernanceVersionTrace(
  currentTrace: GovernanceVersionTrace | undefined,
  nextTrace: GovernanceVersionTrace
): GovernanceVersionTrace {
  if (currentTrace === undefined) {
    return nextTrace;
  }

  const currentCompleteness = countVersionTraceValues(currentTrace);
  const nextCompleteness = countVersionTraceValues(nextTrace);
  const preferredTrace = nextCompleteness >= currentCompleteness ? nextTrace : currentTrace;
  const fallbackTrace = preferredTrace === nextTrace ? currentTrace : nextTrace;

  return {
    ...preferredTrace,
    baselineVersion: preferredTrace.baselineVersion ?? fallbackTrace.baselineVersion,
    candidateVersion: preferredTrace.candidateVersion ?? fallbackTrace.candidateVersion,
    traceId: preferredTrace.traceId ?? fallbackTrace.traceId
  };
}

function countVersionTraceValues(trace: GovernanceVersionTrace): number {
  let count = 0;

  if (trace.baselineVersion !== null) {
    count += 1;
  }

  if (trace.candidateVersion !== null) {
    count += 1;
  }

  return count;
}

function hasGovernanceVersionCoverage(
  versionsByKey: Map<string, GovernanceVersionTrace>,
  artifactType: GovernanceArtifactType
): boolean {
  return Array.from(versionsByKey.values()).some(
    (trace) => trace.artifactType === artifactType && countVersionTraceValues(trace) > 0
  );
}

function resolveGovernanceDriftThreshold(config: Record<string, JsonValue>, override?: number): number {
  if (typeof override === 'number' && Number.isFinite(override) && override >= 0) {
    return roundGovernanceMetric(override);
  }

  const configuredThreshold = readNumberByKeys(config, [
    'governanceDriftThreshold',
    'governance_drift_threshold',
    'policyDriftThreshold',
    'policy_drift_threshold',
    'promptDriftThreshold',
    'prompt_drift_threshold'
  ]);

  return configuredThreshold ?? DEFAULT_GOVERNANCE_DRIFT_THRESHOLD;
}

function resolveTaskThreshold(
  workflowSteps: readonly WorkflowStepLogEntry[],
  defaultThreshold: number
): number {
  for (const workflowStep of [...workflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const threshold = readNumberByKeys(workflowStep.metadata as Record<string, JsonValue>, [
      'governanceDriftThreshold',
      'governance_drift_threshold',
      'policyDriftThreshold',
      'policy_drift_threshold',
      'promptDriftThreshold',
      'prompt_drift_threshold',
      'driftThreshold',
      'drift_threshold'
    ]);

    if (threshold !== null) {
      return threshold;
    }
  }

  return defaultThreshold;
}

function readTaskReportRef(workflowSteps: readonly WorkflowStepLogEntry[]): string | null {
  for (const workflowStep of [...workflowSteps].sort((left, right) => right.sequenceId - left.sequenceId)) {
    const reportRef = readStringByKeys(workflowStep.metadata as Record<string, JsonValue>, [
      'canaryReportRef',
      'canary_report_ref',
      'driftReportRef',
      'drift_report_ref',
      'reportRef',
      'report_ref'
    ]);

    if (reportRef !== null) {
      return reportRef;
    }
  }

  return null;
}

function collectGovernanceToolSeeds(toolCalls: readonly ToolCallLogEntry[]): GovernanceTargetSeed[] {
  const seeds: GovernanceTargetSeed[] = [];

  for (const toolCall of toolCalls) {
    const path = readStringByKeys(toolCall.params as Record<string, JsonValue>, ['path', 'filePath', 'file_path', 'targetPath', 'target_path']);
    if (path === null) {
      continue;
    }

    const artifactType = classifyGovernancePath(path);
    if (artifactType === null) {
      continue;
    }

    seeds.push({
      artifactType,
      targetRef: normalizeTargetRef(path, artifactType),
      sourceSequenceId: toolCall.sequenceId,
      traceId: toolCall.traceId ?? null
    });
  }

  return uniqueGovernanceSeeds(seeds);
}

function readGovernanceTargetSeeds(
  metadata: Record<string, JsonValue>,
  workflowStep: WorkflowStepLogEntry
): GovernanceTargetSeed[] {
  const seeds: GovernanceTargetSeed[] = [];

  for (const targetRef of readStringListByKeys(metadata, ['governanceTargetRefs', 'governance_target_refs'])) {
    const artifactType = inferArtifactTypeFromTargetRef(targetRef) ?? 'policy';
    seeds.push({
      artifactType,
      targetRef: normalizeTargetRef(targetRef, artifactType),
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  for (const targetRef of readStringListByKeys(metadata, ['promptTargetRefs', 'prompt_target_refs'])) {
    seeds.push({
      artifactType: 'prompt',
      targetRef: normalizeTargetRef(targetRef, 'prompt'),
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  for (const targetRef of readStringListByKeys(metadata, ['policyTargetRefs', 'policy_target_refs'])) {
    seeds.push({
      artifactType: 'policy',
      targetRef: normalizeTargetRef(targetRef, 'policy'),
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  const promptRef = readStringByKeys(metadata, ['promptRef', 'prompt_ref', 'promptId', 'prompt_id']);
  if (promptRef !== null) {
    seeds.push({
      artifactType: 'prompt',
      targetRef: normalizeTargetRef(promptRef, 'prompt'),
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  const policyRef = readStringByKeys(metadata, ['policyRef', 'policy_ref', 'policyId', 'policy_id']);
  if (policyRef !== null) {
    seeds.push({
      artifactType: 'policy',
      targetRef: normalizeTargetRef(policyRef, 'policy'),
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  return uniqueGovernanceSeeds(seeds);
}

function readGovernanceVersionTraces(
  task: TaskInspectionView,
  workflowStep: WorkflowStepLogEntry
): GovernanceVersionTrace[] {
  const metadata = workflowStep.metadata as Record<string, JsonValue>;
  const traces: GovernanceVersionTrace[] = [];

  for (const entry of readObjectArrayByKeys(metadata, ['governanceVersions', 'governance_versions'])) {
    const artifactType = normalizeArtifactType(readStringByKeys(entry, ['artifactType', 'artifact_type', 'type']));
    const rawTargetRef = readStringByKeys(entry, ['targetRef', 'target_ref', 'target', 'ref']);
    if (artifactType === null || rawTargetRef === null) {
      continue;
    }

    traces.push({
      taskId: task.task.id,
      taskTitle: task.task.title,
      priority: task.task.priority ?? null,
      artifactType,
      targetRef: normalizeTargetRef(rawTargetRef, artifactType),
      baselineVersion: readStringByKeys(entry, ['baselineVersion', 'baseline_version', 'baseline']),
      candidateVersion: readStringByKeys(entry, ['candidateVersion', 'candidate_version', 'candidate']),
      source: 'workflow_step',
      sourceSequenceId: workflowStep.sequenceId,
      traceId: workflowStep.traceId ?? null
    });
  }

  const promptTrace = createExplicitVersionTrace(
    task,
    workflowStep,
    'prompt',
    readStringByKeys(metadata, ['promptRef', 'prompt_ref', 'promptId', 'prompt_id']) ?? `prompt://task/${task.task.id}`,
    readStringByKeys(metadata, ['promptBaselineVersion', 'prompt_baseline_version']),
    readStringByKeys(metadata, ['promptCandidateVersion', 'prompt_candidate_version', 'promptVersion', 'prompt_version'])
  );
  if (promptTrace !== null) {
    traces.push(promptTrace);
  }

  const policyTrace = createExplicitVersionTrace(
    task,
    workflowStep,
    'policy',
    readStringByKeys(metadata, ['policyRef', 'policy_ref', 'policyId', 'policy_id']) ?? `policy://task/${task.task.id}`,
    readStringByKeys(metadata, ['policyBaselineVersion', 'policy_baseline_version']),
    readStringByKeys(metadata, ['policyCandidateVersion', 'policy_candidate_version', 'policyVersion', 'policy_version'])
  );
  if (policyTrace !== null) {
    traces.push(policyTrace);
  }

  return traces;
}

function createExplicitVersionTrace(
  task: TaskInspectionView,
  workflowStep: WorkflowStepLogEntry,
  artifactType: GovernanceArtifactType,
  targetRef: string,
  baselineVersion: string | null,
  candidateVersion: string | null
): GovernanceVersionTrace | null {
  if (baselineVersion === null && candidateVersion === null) {
    return null;
  }

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    artifactType,
    targetRef: normalizeTargetRef(targetRef, artifactType),
    baselineVersion,
    candidateVersion,
    source: 'workflow_step',
    sourceSequenceId: workflowStep.sequenceId,
    traceId: workflowStep.traceId ?? null
  };
}

function readGovernanceCanaryScenarios(
  task: TaskInspectionView,
  workflowStep: WorkflowStepLogEntry,
  threshold: number,
  reportRef: string | null
): GovernanceCanaryScenarioResult[] {
  const metadata = workflowStep.metadata as Record<string, JsonValue>;
  const results: GovernanceCanaryScenarioResult[] = [];
  const scenarioObjects = readObjectArrayByKeys(metadata, [
    'canaryScenarios',
    'canary_scenarios',
    'driftScenarios',
    'drift_scenarios'
  ]);

  scenarioObjects.forEach((scenario, index) => {
    const scenarioId =
      readStringByKeys(scenario, ['scenarioId', 'scenario_id', 'id', 'scenarioRef', 'scenario_ref']) ??
      `scenario:${workflowStep.sequenceId}:${index}`;
    const title = readStringByKeys(scenario, ['title', 'label', 'name']) ?? scenarioId;
    const baselineVerdict = readStringByKeys(scenario, ['baselineVerdict', 'baseline_verdict']);
    const candidateVerdict = readStringByKeys(scenario, ['candidateVerdict', 'candidate_verdict']);
    const driftScore = readScenarioDriftScore(scenario, baselineVerdict, candidateVerdict);

    if (driftScore === null) {
      return;
    }

    results.push({
      taskId: task.task.id,
      taskTitle: task.task.title,
      priority: task.task.priority ?? null,
      scenarioId,
      title,
      baselineVerdict,
      candidateVerdict,
      driftScore,
      threshold,
      exceedsThreshold: driftScore > threshold,
      diagnostic: readStringByKeys(scenario, ['diagnostic', 'message', 'detail']),
      reportRef,
      traceId: workflowStep.traceId ?? null,
      sourceSequenceId: workflowStep.sequenceId
    });
  });

  return results;
}

function readScenarioDriftScore(
  metadata: Record<string, JsonValue>,
  baselineVerdict: string | null,
  candidateVerdict: string | null
): number | null {
  const explicitScore = readNumberByKeys(metadata, ['driftScore', 'drift_score', 'drift']);
  if (explicitScore !== null) {
    return explicitScore;
  }

  if (baselineVerdict !== null && candidateVerdict !== null) {
    return normalizeVerdict(baselineVerdict) === normalizeVerdict(candidateVerdict) ? 0 : 1;
  }

  return null;
}

function createGovernanceTraceKey(artifactType: GovernanceArtifactType, targetRef: string): string {
  return `${artifactType}:${targetRef}`;
}

function uniqueGovernanceSeeds(seeds: readonly GovernanceTargetSeed[]): GovernanceTargetSeed[] {
  const seedsByKey = new Map<string, GovernanceTargetSeed>();

  for (const seed of seeds) {
    const key = createGovernanceTraceKey(seed.artifactType, seed.targetRef);
    if (!seedsByKey.has(key)) {
      seedsByKey.set(key, seed);
    }
  }

  return Array.from(seedsByKey.values());
}

function classifyGovernancePath(path: string): GovernanceArtifactType | null {
  const normalizedPath = path.toLowerCase();

  if (
    normalizedPath.includes('/.github/prompts/') ||
    normalizedPath.includes('/prompts/') ||
    normalizedPath.endsWith('.prompt.md')
  ) {
    return 'prompt';
  }

  if (
    normalizedPath.includes('/.github/instructions/') ||
    normalizedPath.includes('copilot-instructions.md') ||
    normalizedPath.endsWith('/agents.md') ||
    normalizedPath.includes('/policy') ||
    normalizedPath.includes('policy.')
  ) {
    return 'policy';
  }

  return null;
}

function inferArtifactTypeFromTargetRef(targetRef: string): GovernanceArtifactType | null {
  const normalizedTargetRef = targetRef.toLowerCase();

  if (normalizedTargetRef.startsWith('prompt://')) {
    return 'prompt';
  }

  if (normalizedTargetRef.startsWith('policy://')) {
    return 'policy';
  }

  return classifyGovernancePath(targetRef);
}

function normalizeArtifactType(value: string | null): GovernanceArtifactType | null {
  if (value === null) {
    return null;
  }

  const normalizedValue = value.trim().toLowerCase();
  if (normalizedValue === 'prompt') {
    return 'prompt';
  }

  if (normalizedValue === 'policy') {
    return 'policy';
  }

  return null;
}

function normalizeTargetRef(targetRef: string, artifactType: GovernanceArtifactType): string {
  const normalizedTargetRef = targetRef.trim();
  if (normalizedTargetRef.includes('://')) {
    return normalizedTargetRef;
  }

  const withoutLeadingDot = normalizedTargetRef.replace(/^\.\//, '');
  return `${artifactType}://${withoutLeadingDot}`;
}

function compareGovernanceTaskGates(left: GovernanceDriftTaskGate, right: GovernanceDriftTaskGate): number {
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

function compareGovernanceVersionTraces(left: GovernanceVersionTrace, right: GovernanceVersionTrace): number {
  const leftPriority = left.priority ?? 'none';
  const rightPriority = right.priority ?? 'none';
  if (leftPriority !== rightPriority) {
    return TASK_PRIORITY_RANK[leftPriority] - TASK_PRIORITY_RANK[rightPriority];
  }

  if (left.taskTitle !== right.taskTitle) {
    return left.taskTitle.localeCompare(right.taskTitle);
  }

  if (left.artifactType !== right.artifactType) {
    return GOVERNANCE_ARTIFACT_TYPE_ORDER.indexOf(left.artifactType) - GOVERNANCE_ARTIFACT_TYPE_ORDER.indexOf(right.artifactType);
  }

  if (left.targetRef !== right.targetRef) {
    return left.targetRef.localeCompare(right.targetRef);
  }

  return right.sourceSequenceId - left.sourceSequenceId;
}

function compareGovernanceScenarioResults(
  left: GovernanceCanaryScenarioResult,
  right: GovernanceCanaryScenarioResult
): number {
  const leftPriority = left.priority ?? 'none';
  const rightPriority = right.priority ?? 'none';
  if (leftPriority !== rightPriority) {
    return TASK_PRIORITY_RANK[leftPriority] - TASK_PRIORITY_RANK[rightPriority];
  }

  if (left.taskTitle !== right.taskTitle) {
    return left.taskTitle.localeCompare(right.taskTitle);
  }

  if (left.exceedsThreshold !== right.exceedsThreshold) {
    return left.exceedsThreshold ? -1 : 1;
  }

  if (left.driftScore !== right.driftScore) {
    return right.driftScore - left.driftScore;
  }

  return left.scenarioId.localeCompare(right.scenarioId);
}

function describeGovernanceBlockingReason(
  taskTitle: string,
  issueCodes: readonly GovernanceDriftIssueCode[],
  threshold: number,
  maxDriftScore: number | null
): string {
  const messages: string[] = [];

  if (issueCodes.includes('GOVERNANCE_VERSION_TRACE_MISSING')) {
    messages.push(`Critical task ${taskTitle} is missing baseline/candidate versions for prompt or policy changes.`);
  }

  if (issueCodes.includes('GOVERNANCE_CANARY_REPORT_MISSING')) {
    messages.push(`Critical task ${taskTitle} is missing a scenario-by-scenario canary drift report.`);
  }

  if (issueCodes.includes('GOVERNANCE_DRIFT_THRESHOLD_EXCEEDED')) {
    messages.push(
      `Critical task ${taskTitle} exceeds the governance drift threshold ${formatGovernanceMetric(threshold)} with observed drift ${formatGovernanceMetric(maxDriftScore ?? 0)}.`
    );
  }

  return messages.join(' ');
}

function normalizeVerdict(verdict: string): string {
  return verdict.trim().toLowerCase();
}

function formatGovernanceMetric(value: number): string {
  return roundGovernanceMetric(value).toString();
}

function roundGovernanceMetric(value: number): number {
  return Number(value.toFixed(3));
}

function readBooleanByKeys(metadata: Record<string, JsonValue>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readStringByKeys(metadata: Record<string, JsonValue>, keys: readonly string[]): string | null {
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

function readStringListByKeys(metadata: Record<string, JsonValue>, keys: readonly string[]): string[] {
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
      return [...new Set(normalizedValues)];
    }
  }

  return [];
}

function readNumberByKeys(metadata: Record<string, JsonValue>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
      return roundGovernanceMetric(value);
    }

    if (typeof value === 'string') {
      const parsedValue = Number(value);
      if (Number.isFinite(parsedValue) && parsedValue >= 0) {
        return roundGovernanceMetric(parsedValue);
      }
    }
  }

  return null;
}

function readObjectArrayByKeys(metadata: Record<string, JsonValue>, keys: readonly string[]): Record<string, JsonValue>[] {
  for (const key of keys) {
    const value = metadata[key];
    if (!Array.isArray(value)) {
      continue;
    }

    const objects = value.filter((entry): entry is Record<string, JsonValue> => typeof entry === 'object' && entry !== null && !Array.isArray(entry));
    if (objects.length > 0) {
      return objects;
    }
  }

  return [];
}