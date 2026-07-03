import type { AgentRole, JsonValue, TaskPriority } from '../contracts/events';

import type { GameState, ToolCallLogEntry, WorkflowStepLogEntry } from './game-state';
import { createTaskView, type TaskInspectionView } from './task-view';

export const FINOPS_COMPLEXITY_ORDER = ['trivial', 'standard', 'complex', 'expert'] as const;
export const FINOPS_DRIFT_CODE_ORDER = [
  'FINOPS_METRICS_MISSING',
  'FINOPS_COST_DRIFT',
  'FINOPS_TOKEN_DRIFT',
  'FINOPS_LATENCY_DRIFT',
  'FINOPS_REVIEW_EXTRACT_MISSING'
] as const;

export const DEFAULT_FINOPS_THRESHOLDS = {
  normalizedCostUsd: 0.2,
  normalizedTokens: 800,
  normalizedLatencyMs: 1_500
} as const;

export type FinOpsComplexity = (typeof FINOPS_COMPLEXITY_ORDER)[number];
export type FinOpsDriftCode = (typeof FINOPS_DRIFT_CODE_ORDER)[number];

export interface FinOpsThresholds {
  normalizedCostUsd: number;
  normalizedTokens: number;
  normalizedLatencyMs: number;
}

export interface FinOpsRoleMetrics {
  role: AgentRole | 'unknown';
  sampleCount: number;
  costUsd: number;
  tokens: number;
  latencyMs: number;
}

export interface FinOpsModelMetrics {
  model: string;
  sampleCount: number;
  costUsd: number;
  tokens: number;
  latencyMs: number;
}

export interface FinOpsTaskGate {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  complexity: FinOpsComplexity;
  complexityWeight: number;
  metricsAvailable: boolean;
  sampleCount: number;
  totalCostUsd: number;
  totalTokens: number;
  totalLatencyMs: number;
  normalizedCostUsd: number;
  normalizedTokens: number;
  normalizedLatencyMs: number;
  requiresReviewExtract: boolean;
  hasReviewExtract: boolean;
  reviewExtractRef: string | null;
  driftCodes: readonly FinOpsDriftCode[];
  roleMetrics: readonly FinOpsRoleMetrics[];
  modelMetrics: readonly FinOpsModelMetrics[];
}

export interface FinOpsAlert {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  driftCodes: readonly FinOpsDriftCode[];
  message: string;
}

export interface FinOpsReviewItem {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  requiresReviewExtract: boolean;
  hasReviewExtract: boolean;
  reviewExtractRef: string | null;
  driftCodes: readonly FinOpsDriftCode[];
}

export interface FinOpsRetroItem {
  taskId: string;
  taskTitle: string;
  complexity: FinOpsComplexity;
  normalizedCostUsd: number;
  normalizedTokens: number;
  normalizedLatencyMs: number;
  driftCodes: readonly FinOpsDriftCode[];
}

export interface FinOpsViewSummary {
  taskCount: number;
  metricsReadyCount: number;
  driftedTaskCount: number;
  alertCount: number;
  criticalTaskCount: number;
  extractMissingCount: number;
}

export interface FinOpsView {
  protocolVersion: string;
  lastSequenceId: number;
  thresholds: FinOpsThresholds;
  tasks: readonly FinOpsTaskGate[];
  alerts: readonly FinOpsAlert[];
  reviewQueue: readonly FinOpsReviewItem[];
  retroDigest: readonly FinOpsRetroItem[];
  summary: FinOpsViewSummary;
}

export interface FinOpsViewOptions {
  thresholds?: Partial<FinOpsThresholds>;
}

interface FinOpsSample {
  source: 'tool_call' | 'workflow_step';
  role: AgentRole | 'unknown';
  model: string;
  costUsd: number | null;
  tokens: number | null;
  latencyMs: number | null;
  extractRef: string | null;
  explicitComplexity: FinOpsComplexity | null;
}

const FINOPS_COMPLEXITY_WEIGHT: Record<FinOpsComplexity, number> = {
  trivial: 1,
  standard: 2,
  complex: 3,
  expert: 5
};

const TASK_PRIORITY_RANK: Record<TaskPriority | 'none', number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  none: 4
};

export function createFinOpsView(state: GameState, options: FinOpsViewOptions = {}): FinOpsView {
  const thresholds = resolveFinOpsThresholds(state.config, options.thresholds);
  const taskView = createTaskView(state);
  const tasks = taskView.tasks.map((task) => createFinOpsTaskGate(state, task, thresholds)).sort(compareFinOpsTaskGates);
  const alerts = tasks
    .filter((task) => task.driftCodes.length > 0)
    .map((task) => ({
      taskId: task.taskId,
      taskTitle: task.taskTitle,
      priority: task.priority,
      driftCodes: task.driftCodes,
      message: describeFinOpsDrift(task)
    }))
    .sort(compareFinOpsAlerts);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    thresholds,
    tasks,
    alerts,
    reviewQueue: tasks
      .filter((task) => task.requiresReviewExtract || task.driftCodes.length > 0)
      .map((task) => ({
        taskId: task.taskId,
        taskTitle: task.taskTitle,
        priority: task.priority,
        requiresReviewExtract: task.requiresReviewExtract,
        hasReviewExtract: task.hasReviewExtract,
        reviewExtractRef: task.reviewExtractRef,
        driftCodes: task.driftCodes
      })),
    retroDigest: tasks
      .filter((task) => task.metricsAvailable || task.driftCodes.length > 0)
      .map((task) => ({
        taskId: task.taskId,
        taskTitle: task.taskTitle,
        complexity: task.complexity,
        normalizedCostUsd: task.normalizedCostUsd,
        normalizedTokens: task.normalizedTokens,
        normalizedLatencyMs: task.normalizedLatencyMs,
        driftCodes: task.driftCodes
      }))
      .sort(compareFinOpsRetroItems),
    summary: {
      taskCount: tasks.length,
      metricsReadyCount: tasks.filter((task) => task.metricsAvailable).length,
      driftedTaskCount: tasks.filter((task) => task.driftCodes.length > 0).length,
      alertCount: alerts.length,
      criticalTaskCount: tasks.filter((task) => task.priority === 'critical').length,
      extractMissingCount: tasks.filter((task) => task.requiresReviewExtract && !task.hasReviewExtract).length
    }
  };
}

export function evaluateTaskFinOpsGate(
  state: GameState,
  taskId: string,
  options: FinOpsViewOptions = {}
): FinOpsTaskGate | null {
  return createFinOpsView(state, options).tasks.find((task) => task.taskId === taskId) ?? null;
}

function createFinOpsTaskGate(
  state: GameState,
  task: TaskInspectionView,
  thresholds: FinOpsThresholds
): FinOpsTaskGate {
  const samples = collectFinOpsSamples(state, task);
  const metricsAvailable = samples.some(
    (sample) => sample.costUsd !== null || sample.tokens !== null || sample.latencyMs !== null
  );
  const complexity = deriveTaskComplexity(task, samples);
  const complexityWeight = FINOPS_COMPLEXITY_WEIGHT[complexity];
  const totalCostUsd = roundMetric(sumNullable(samples.map((sample) => sample.costUsd)));
  const totalTokens = roundMetric(sumNullable(samples.map((sample) => sample.tokens)));
  const totalLatencyMs = roundMetric(sumNullable(samples.map((sample) => sample.latencyMs)));
  const normalizedCostUsd = roundMetric(totalCostUsd / complexityWeight);
  const normalizedTokens = roundMetric(totalTokens / complexityWeight);
  const normalizedLatencyMs = roundMetric(totalLatencyMs / complexityWeight);
  const extractRefs = uniqueStrings(samples.map((sample) => sample.extractRef));
  const requiresReviewExtract = task.task.priority === 'critical';
  const driftCodes: FinOpsDriftCode[] = [];

  if ((task.recentToolCalls.length > 0 || task.recentWorkflowSteps.length > 0) && !metricsAvailable) {
    driftCodes.push('FINOPS_METRICS_MISSING');
  }

  if (metricsAvailable && normalizedCostUsd > thresholds.normalizedCostUsd) {
    driftCodes.push('FINOPS_COST_DRIFT');
  }

  if (metricsAvailable && normalizedTokens > thresholds.normalizedTokens) {
    driftCodes.push('FINOPS_TOKEN_DRIFT');
  }

  if (metricsAvailable && normalizedLatencyMs > thresholds.normalizedLatencyMs) {
    driftCodes.push('FINOPS_LATENCY_DRIFT');
  }

  if (requiresReviewExtract && extractRefs.length === 0) {
    driftCodes.push('FINOPS_REVIEW_EXTRACT_MISSING');
  }

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    priority: task.task.priority ?? null,
    complexity,
    complexityWeight,
    metricsAvailable,
    sampleCount: samples.length,
    totalCostUsd,
    totalTokens,
    totalLatencyMs,
    normalizedCostUsd,
    normalizedTokens,
    normalizedLatencyMs,
    requiresReviewExtract,
    hasReviewExtract: extractRefs.length > 0,
    reviewExtractRef: extractRefs[0] ?? null,
    driftCodes,
    roleMetrics: createRoleMetrics(samples),
    modelMetrics: createModelMetrics(samples)
  };
}

function collectFinOpsSamples(state: GameState, task: TaskInspectionView): FinOpsSample[] {
  return [
    ...task.recentToolCalls.map((toolCall) => createToolCallSample(state, toolCall)),
    ...task.recentWorkflowSteps.map((workflowStep) => createWorkflowStepSample(state, workflowStep))
  ];
}

function createToolCallSample(state: GameState, toolCall: ToolCallLogEntry): FinOpsSample {
  const explicitModel = readStringByKeys(toolCall.params, ['model', 'modelId', 'model_id', 'activeModel', 'selectedModel']);
  const tokens = readFinOpsTokens(toolCall.params);
  const model = explicitModel ?? resolveAgentModel(state.config, toolCall.agentId) ?? 'unknown';

  return {
    source: 'tool_call',
    role: resolveAgentRole(state, toolCall.agentId),
    model,
    costUsd: readFinOpsCostUsd(toolCall.params, state.config, model, tokens),
    tokens,
    latencyMs: readFinOpsLatencyMs(toolCall.params),
    extractRef: readFinOpsExtractRef(toolCall.params),
    explicitComplexity: readFinOpsComplexity(toolCall.params)
  };
}

function createWorkflowStepSample(state: GameState, workflowStep: WorkflowStepLogEntry): FinOpsSample {
  const explicitModel = readStringByKeys(workflowStep.metadata, [
    'model',
    'modelId',
    'model_id',
    'activeModel',
    'selectedModel'
  ]);
  const tokens = readFinOpsTokens(workflowStep.metadata);
  const model = explicitModel ?? resolveAgentModel(state.config, workflowStep.agentId) ?? 'unknown';

  return {
    source: 'workflow_step',
    role: resolveAgentRole(state, workflowStep.agentId),
    model,
    costUsd: readFinOpsCostUsd(workflowStep.metadata, state.config, model, tokens),
    tokens,
    latencyMs: readFinOpsLatencyMs(workflowStep.metadata),
    extractRef: readFinOpsExtractRef(workflowStep.metadata),
    explicitComplexity: readFinOpsComplexity(workflowStep.metadata)
  };
}

function createRoleMetrics(samples: readonly FinOpsSample[]): FinOpsRoleMetrics[] {
  const metricsByRole = new Map<FinOpsRoleMetrics['role'], FinOpsRoleMetrics>();

  for (const sample of samples) {
    const current = metricsByRole.get(sample.role) ?? {
      role: sample.role,
      sampleCount: 0,
      costUsd: 0,
      tokens: 0,
      latencyMs: 0
    };

    current.sampleCount += 1;
    current.costUsd = roundMetric(current.costUsd + (sample.costUsd ?? 0));
    current.tokens = roundMetric(current.tokens + (sample.tokens ?? 0));
    current.latencyMs = roundMetric(current.latencyMs + (sample.latencyMs ?? 0));
    metricsByRole.set(sample.role, current);
  }

  return Array.from(metricsByRole.values()).sort(compareRoleMetrics);
}

function createModelMetrics(samples: readonly FinOpsSample[]): FinOpsModelMetrics[] {
  const metricsByModel = new Map<string, FinOpsModelMetrics>();

  for (const sample of samples) {
    const current = metricsByModel.get(sample.model) ?? {
      model: sample.model,
      sampleCount: 0,
      costUsd: 0,
      tokens: 0,
      latencyMs: 0
    };

    current.sampleCount += 1;
    current.costUsd = roundMetric(current.costUsd + (sample.costUsd ?? 0));
    current.tokens = roundMetric(current.tokens + (sample.tokens ?? 0));
    current.latencyMs = roundMetric(current.latencyMs + (sample.latencyMs ?? 0));
    metricsByModel.set(sample.model, current);
  }

  return Array.from(metricsByModel.values()).sort(compareModelMetrics);
}

function deriveTaskComplexity(task: TaskInspectionView, samples: readonly FinOpsSample[]): FinOpsComplexity {
  const explicitComplexities = samples
    .map((sample) => sample.explicitComplexity)
    .filter((complexity): complexity is FinOpsComplexity => complexity !== null)
    .sort(compareComplexities);

  const highestExplicitComplexity = explicitComplexities[0];
  if (highestExplicitComplexity !== undefined) {
    return highestExplicitComplexity;
  }

  if (task.task.priority === 'critical' || task.task.kind === 'security') {
    return 'expert';
  }

  const activityCount = task.recentToolCalls.length + task.recentWorkflowSteps.length + task.decisionCards.length;
  if (task.task.priority === 'high' || activityCount >= 8) {
    return 'complex';
  }

  if (activityCount >= 3 || task.task.status === 'in_progress' || task.task.status === 'review') {
    return 'standard';
  }

  return 'trivial';
}

function describeFinOpsDrift(task: FinOpsTaskGate): string {
  const labels = task.driftCodes.map(describeDriftCode);
  return `Task ${task.taskTitle} triggers FinOps attention: ${labels.join(', ')}.`;
}

function describeDriftCode(code: FinOpsDriftCode): string {
  switch (code) {
    case 'FINOPS_METRICS_MISSING':
      return 'metrics missing';
    case 'FINOPS_COST_DRIFT':
      return 'cost drift';
    case 'FINOPS_TOKEN_DRIFT':
      return 'token drift';
    case 'FINOPS_LATENCY_DRIFT':
      return 'latency drift';
    case 'FINOPS_REVIEW_EXTRACT_MISSING':
      return 'review extract missing';
  }
}

function compareFinOpsTaskGates(left: FinOpsTaskGate, right: FinOpsTaskGate): number {
  if (left.driftCodes.length !== right.driftCodes.length) {
    return right.driftCodes.length - left.driftCodes.length;
  }

  const leftPriority = TASK_PRIORITY_RANK[left.priority ?? 'none'];
  const rightPriority = TASK_PRIORITY_RANK[right.priority ?? 'none'];
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority;
  }

  if (left.normalizedCostUsd !== right.normalizedCostUsd) {
    return right.normalizedCostUsd - left.normalizedCostUsd;
  }

  if (left.normalizedTokens !== right.normalizedTokens) {
    return right.normalizedTokens - left.normalizedTokens;
  }

  if (left.normalizedLatencyMs !== right.normalizedLatencyMs) {
    return right.normalizedLatencyMs - left.normalizedLatencyMs;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareFinOpsAlerts(left: FinOpsAlert, right: FinOpsAlert): number {
  if (left.driftCodes.length !== right.driftCodes.length) {
    return right.driftCodes.length - left.driftCodes.length;
  }

  const leftPriority = TASK_PRIORITY_RANK[left.priority ?? 'none'];
  const rightPriority = TASK_PRIORITY_RANK[right.priority ?? 'none'];
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareFinOpsRetroItems(left: FinOpsRetroItem, right: FinOpsRetroItem): number {
  if (left.driftCodes.length !== right.driftCodes.length) {
    return right.driftCodes.length - left.driftCodes.length;
  }

  if (left.normalizedCostUsd !== right.normalizedCostUsd) {
    return right.normalizedCostUsd - left.normalizedCostUsd;
  }

  if (left.normalizedTokens !== right.normalizedTokens) {
    return right.normalizedTokens - left.normalizedTokens;
  }

  if (left.normalizedLatencyMs !== right.normalizedLatencyMs) {
    return right.normalizedLatencyMs - left.normalizedLatencyMs;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareRoleMetrics(left: FinOpsRoleMetrics, right: FinOpsRoleMetrics): number {
  if (left.costUsd !== right.costUsd) {
    return right.costUsd - left.costUsd;
  }

  if (left.tokens !== right.tokens) {
    return right.tokens - left.tokens;
  }

  return left.role.localeCompare(right.role);
}

function compareModelMetrics(left: FinOpsModelMetrics, right: FinOpsModelMetrics): number {
  if (left.costUsd !== right.costUsd) {
    return right.costUsd - left.costUsd;
  }

  if (left.tokens !== right.tokens) {
    return right.tokens - left.tokens;
  }

  return left.model.localeCompare(right.model);
}

function compareComplexities(left: FinOpsComplexity, right: FinOpsComplexity): number {
  return FINOPS_COMPLEXITY_ORDER.indexOf(right) - FINOPS_COMPLEXITY_ORDER.indexOf(left);
}

function resolveFinOpsThresholds(
  config: Record<string, JsonValue>,
  overrides: Partial<FinOpsThresholds> | undefined
): FinOpsThresholds {
  return {
    normalizedCostUsd:
      overrides?.normalizedCostUsd ??
      readConfigNumber(config, ['finops', 'thresholds', 'normalizedCostUsd']) ??
      readConfigNumber(config, ['finops', 'thresholds', 'costUsdPerComplexityPoint']) ??
      DEFAULT_FINOPS_THRESHOLDS.normalizedCostUsd,
    normalizedTokens:
      overrides?.normalizedTokens ??
      readConfigNumber(config, ['finops', 'thresholds', 'normalizedTokens']) ??
      readConfigNumber(config, ['finops', 'thresholds', 'tokensPerComplexityPoint']) ??
      DEFAULT_FINOPS_THRESHOLDS.normalizedTokens,
    normalizedLatencyMs:
      overrides?.normalizedLatencyMs ??
      readConfigNumber(config, ['finops', 'thresholds', 'normalizedLatencyMs']) ??
      readConfigNumber(config, ['finops', 'thresholds', 'latencyMsPerComplexityPoint']) ??
      DEFAULT_FINOPS_THRESHOLDS.normalizedLatencyMs
  };
}

function resolveAgentRole(state: GameState, agentId: string | undefined): AgentRole | 'unknown' {
  if (agentId === undefined) {
    return 'unknown';
  }

  return state.agents[agentId]?.role ?? 'unknown';
}

function resolveAgentModel(config: Record<string, JsonValue>, agentId: string | undefined): string | null {
  if (agentId === undefined) {
    return null;
  }

  return readStringValue(resolveAgentField(config, agentId, ['model', 'modelId', 'model_id', 'activeModel']));
}

function resolveAgentField(
  config: Record<string, JsonValue>,
  agentId: string,
  aliases: readonly string[]
): JsonValue | undefined {
  const basePaths = [
    ['agentProfiles', agentId],
    ['agent_profiles', agentId],
    ['agents', agentId],
    ['inspection', 'agents', agentId],
    [agentId]
  ];

  for (const basePath of basePaths) {
    for (const alias of aliases) {
      const value = readConfigPath(config, [...basePath, alias]);
      if (value !== undefined) {
        return value;
      }
    }
  }

  return undefined;
}

function readFinOpsTokens(record: Record<string, unknown>): number | null {
  const direct = readNumberByKeys(record, [
    'tokens',
    'tokenCount',
    'token_count',
    'totalTokens',
    'total_tokens',
    'tokensUsed',
    'tokens_used'
  ]);
  if (direct !== null) {
    return direct;
  }

  for (const key of ['usage', 'tokenUsage', 'token_usage', 'metrics', 'finops']) {
    const nested = record[key];
    if (!isUnknownRecord(nested)) {
      continue;
    }

    const nestedTotal = readNumberByKeys(nested, [
      'tokens',
      'tokenCount',
      'token_count',
      'totalTokens',
      'total_tokens',
      'tokensUsed',
      'tokens_used'
    ]);
    if (nestedTotal !== null) {
      return nestedTotal;
    }

    const promptTokens = readNumberByKeys(nested, ['promptTokens', 'prompt_tokens']);
    const completionTokens = readNumberByKeys(nested, ['completionTokens', 'completion_tokens']);
    if (promptTokens !== null || completionTokens !== null) {
      return (promptTokens ?? 0) + (completionTokens ?? 0);
    }
  }

  return null;
}

function readFinOpsCostUsd(
  record: Record<string, unknown>,
  config: Record<string, JsonValue>,
  model: string,
  tokens: number | null
): number | null {
  const direct = readNumberByKeys(record, [
    'costUsd',
    'cost_usd',
    'estimatedCostUsd',
    'estimated_cost_usd',
    'usdCost',
    'usd_cost'
  ]);
  if (direct !== null) {
    return roundMetric(direct);
  }

  for (const key of ['usage', 'pricing', 'metrics', 'finops']) {
    const nested = record[key];
    if (!isUnknownRecord(nested)) {
      continue;
    }

    const nestedCost = readNumberByKeys(nested, [
      'costUsd',
      'cost_usd',
      'estimatedCostUsd',
      'estimated_cost_usd',
      'usdCost',
      'usd_cost'
    ]);
    if (nestedCost !== null) {
      return roundMetric(nestedCost);
    }
  }

  if (tokens === null) {
    return null;
  }

  const pricePer1kTokens = resolveModelPricePer1kTokens(config, model);
  if (pricePer1kTokens === null) {
    return null;
  }

  return roundMetric((tokens / 1_000) * pricePer1kTokens);
}

function readFinOpsLatencyMs(record: Record<string, unknown>): number | null {
  const direct = readNumberByKeys(record, [
    'latencyMs',
    'latency_ms',
    'durationMs',
    'duration_ms',
    'elapsedMs',
    'elapsed_ms'
  ]);
  if (direct !== null) {
    return direct;
  }

  for (const key of ['timing', 'metrics', 'finops']) {
    const nested = record[key];
    if (!isUnknownRecord(nested)) {
      continue;
    }

    const nestedLatency = readNumberByKeys(nested, [
      'latencyMs',
      'latency_ms',
      'durationMs',
      'duration_ms',
      'elapsedMs',
      'elapsed_ms'
    ]);
    if (nestedLatency !== null) {
      return nestedLatency;
    }
  }

  return null;
}

function readFinOpsExtractRef(record: Record<string, unknown>): string | null {
  const direct = readStringByKeys(record, [
    'finopsExtractRef',
    'finops_extract_ref',
    'reviewExtractRef',
    'review_extract_ref'
  ]);
  if (direct !== null) {
    return direct;
  }

  const evidenceRefs = readStringListByKeys(record, ['evidenceRefs', 'evidence_refs']);
  const finopsEvidenceRef = evidenceRefs.find((ref) => ref.startsWith('finops://'));
  if (finopsEvidenceRef !== undefined) {
    return finopsEvidenceRef;
  }

  const included = readBooleanByKeys(record, ['finopsIncluded', 'finops_included', 'includeFinops']);
  return included === true ? 'finops://inline-extract' : null;
}

function readFinOpsComplexity(record: Record<string, unknown>): FinOpsComplexity | null {
  const value = readStringByKeys(record, ['complexity', 'taskComplexity', 'task_complexity', 'complexityLevel']);
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'trivial' || normalized === 'simple') {
    return 'trivial';
  }

  if (normalized === 'standard' || normalized === 'medium') {
    return 'standard';
  }

  if (normalized === 'complex' || normalized === 'high') {
    return 'complex';
  }

  if (normalized === 'expert' || normalized === 'critical') {
    return 'expert';
  }

  return null;
}

function resolveModelPricePer1kTokens(config: Record<string, JsonValue>, model: string): number | null {
  return (
    readConfigNumber(config, ['finops', 'pricing', 'models', model, 'usdPer1kTokens']) ??
    readConfigNumber(config, ['finops', 'pricing', 'models', model, 'usd_per_1k_tokens']) ??
    readConfigNumber(config, ['finops', 'pricing', model, 'usdPer1kTokens']) ??
    readConfigNumber(config, ['finops', 'pricing', model, 'usd_per_1k_tokens']) ??
    readConfigNumber(config, ['pricing', 'models', model, 'usdPer1kTokens']) ??
    readConfigNumber(config, ['pricing', 'models', model, 'usd_per_1k_tokens'])
  );
}

function readConfigNumber(config: Record<string, JsonValue>, path: readonly string[]): number | null {
  return readNumberValue(readConfigPath(config, path));
}

function readConfigPath(config: Record<string, JsonValue>, path: readonly string[]): JsonValue | undefined {
  let cursor: JsonValue | Record<string, JsonValue> | undefined = config;

  for (const segment of path) {
    if (!isJsonRecord(cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
    if (cursor === undefined) {
      return undefined;
    }
  }

  return cursor;
}

function readNumberByKeys(record: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = readNumberValue(record[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function readStringByKeys(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = readStringValue(record[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function readStringListByKeys(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = normalizeStringList(record[key]);
    if (value.length > 0) {
      return value;
    }
  }

  return [];
}

function readBooleanByKeys(record: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    if (typeof record[key] === 'boolean') {
      return record[key] as boolean;
    }
  }

  return null;
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return uniqueStrings(value.filter((entry): entry is string => typeof entry === 'string'));
}

function readStringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function readNumberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function isJsonRecord(value: JsonValue | Record<string, JsonValue> | undefined): value is Record<string, JsonValue> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isUnknownRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function uniqueStrings(values: readonly (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0))].sort(
    (left, right) => left.localeCompare(right)
  );
}

function sumNullable(values: readonly (number | null)[]): number {
  return values.reduce<number>((sum, value) => sum + (value ?? 0), 0);
}

function roundMetric(value: number): number {
  return Math.round(value * 1_000) / 1_000;
}