import type { AgentRole, JsonValue, VerificationGateResult } from '../contracts/events';
import type { AuthContext } from '../server/auth/rbac';
import { isReadOnlyRole } from '../server/auth/rbac';

import { createAgentInspection, type AgentInspectionView } from './board-view';
import type { GameState } from './game-state';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export type DeepInspectionActionKind = 'pause' | 'chat_direct' | 'redirect' | 'restart';
export type DeepInspectionTokenSource = 'config' | 'derived' | 'unknown';

export interface DeepInspectionTokenUsage {
  budget: number | null;
  used: number | null;
  remaining: number | null;
  source: DeepInspectionTokenSource;
}

export interface DeepInspectionProfile {
  model: string | null;
  branch: string | null;
  systemPrompt: string | null;
  activeTool: string | null;
  tokenUsage: DeepInspectionTokenUsage;
}

export interface DeepInspectionToolHistoryEntry {
  id: string;
  tool: string;
  timestamp: string;
  sequenceId: number;
  sourceEventType: string;
  traceId: string | null;
  taskId: string | null;
  fileRefs: readonly string[];
  testRelated: boolean;
  summary: string;
}

export interface DeepInspectionSessionSummary {
  toolCallCount: number;
  uniqueFileCount: number;
  testRunCount: number;
  workflowStepCount: number;
  decisionCardCount: number;
  traceCount: number;
}

export interface DeepInspectionActionAvailability {
  kind: DeepInspectionActionKind;
  label: string;
  allowed: boolean;
  reason: string | null;
}

export interface DeepInspectionActionRequest {
  action: DeepInspectionActionKind;
  targetAgentId: string;
  taskId?: string | null;
  traceId?: string | null;
  detail?: string | null;
}

export interface DeepInspectionActionDecision {
  allowed: boolean;
  reason: string | null;
  requiredRole: AgentRole;
}

export interface DeepInspectionActionAuditEntry {
  id: string;
  at: string;
  actorId: string;
  actorRole: AgentRole;
  targetAgentId: string;
  action: DeepInspectionActionKind;
  allowed: boolean;
  reason: string | null;
  taskId: string | null;
  traceId: string | null;
  detail: string | null;
}

export interface DeepInspectionView {
  protocolVersion: string;
  lastSequenceId: number;
  targetAgentId: string;
  inspection: AgentInspectionView;
  profile: DeepInspectionProfile;
  sessionSummary: DeepInspectionSessionSummary;
  toolHistory: readonly DeepInspectionToolHistoryEntry[];
  verificationProofRefs: readonly string[];
  latestVerificationVerdict: VerificationGateResult | null;
  latestVerificationCorrelationId: string | null;
  actions: readonly DeepInspectionActionAvailability[];
  auditTrail: readonly DeepInspectionActionAuditEntry[];
}

export interface DeepInspectionViewOptions {
  actor?: AuthContext;
  dashboard?: RuntimeDashboardView;
  auditTrail?: readonly DeepInspectionActionAuditEntry[];
}

const DEEP_INSPECTION_ACTION_ORDER: readonly DeepInspectionActionKind[] = [
  'pause',
  'chat_direct',
  'redirect',
  'restart'
];

const DEEP_INSPECTION_ACTION_LABELS: Record<DeepInspectionActionKind, string> = {
  pause: 'Pause',
  chat_direct: 'Chat direct',
  redirect: 'Redirect',
  restart: 'Restart'
};

export function createDeepInspectionView(
  state: GameState,
  targetAgentId: string,
  options: DeepInspectionViewOptions = {}
): DeepInspectionView | null {
  const inspection = createAgentInspection(state, targetAgentId);

  if (inspection === null) {
    return null;
  }

  const toolHistory = inspection.recentToolCalls.map(createToolHistoryEntry);
  const verificationSummary = createVerificationSummary(inspection);
  const taskId = inspection.assignedTasks[0]?.id ?? null;
  const traceId =
    inspection.decisionCards[0]?.traceId ??
    inspection.recentWorkflowSteps[0]?.traceId ??
    inspection.recentToolCalls[0]?.traceId ??
    null;

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    targetAgentId,
    inspection,
    profile: {
      model: resolveAgentStringField(state.config, targetAgentId, ['model', 'modelId', 'activeModel']),
      branch: resolveBranch(state.config, targetAgentId, inspection, options.dashboard),
      systemPrompt: resolveAgentStringField(state.config, targetAgentId, ['systemPrompt', 'system_prompt', 'prompt']),
      activeTool: inspection.agent.lastTool,
      tokenUsage: resolveTokenUsage(state.config, targetAgentId)
    },
    sessionSummary: createSessionSummary(inspection, toolHistory),
    toolHistory,
    verificationProofRefs: verificationSummary.verificationProofRefs,
    latestVerificationVerdict: verificationSummary.latestVerificationVerdict,
    latestVerificationCorrelationId: verificationSummary.latestVerificationCorrelationId,
    actions: DEEP_INSPECTION_ACTION_ORDER.map((action) =>
      createActionAvailability(options.actor, {
        action,
        targetAgentId,
        taskId,
        traceId
      })
    ),
    auditTrail: [...(options.auditTrail ?? [])]
      .filter((entry) => entry.targetAgentId === targetAgentId)
      .sort(compareDeepInspectionAuditEntries)
  };
}

export function authorizeDeepInspectionAction(
  actor: AuthContext | undefined,
  request: DeepInspectionActionRequest
): DeepInspectionActionDecision {
  const requiredRole = request.action === 'chat_direct' || request.action === 'pause' ? 'agent' : 'orchestrator';

  if (actor === undefined) {
    return {
      allowed: false,
      reason: 'Inspection actor context is required.',
      requiredRole
    };
  }

  if (isReadOnlyRole(actor.role)) {
    return {
      allowed: false,
      reason: 'Spectator tokens are read-only and cannot trigger inspection actions.',
      requiredRole
    };
  }

  if (actor.role === 'orchestrator') {
    return {
      allowed: true,
      reason: null,
      requiredRole
    };
  }

  if (request.action === 'chat_direct') {
    return {
      allowed: true,
      reason: null,
      requiredRole
    };
  }

  if (request.action === 'pause' && actor.principalId === request.targetAgentId) {
    return {
      allowed: true,
      reason: null,
      requiredRole
    };
  }

  return {
    allowed: false,
    reason:
      request.action === 'pause'
        ? 'Agents can only pause themselves from Deep Inspection.'
        : `Role ${actor.role} cannot execute inspection action ${request.action}.`,
    requiredRole
  };
}

export function createDeepInspectionActionAuditEntry(
  actor: AuthContext,
  request: DeepInspectionActionRequest,
  decision: DeepInspectionActionDecision,
  at: string = new Date().toISOString()
): DeepInspectionActionAuditEntry {
  return {
    id: `inspection-action:${request.action}:${request.targetAgentId}:${at}`,
    at,
    actorId: actor.principalId,
    actorRole: actor.role,
    targetAgentId: request.targetAgentId,
    action: request.action,
    allowed: decision.allowed,
    reason: decision.reason,
    taskId: request.taskId ?? null,
    traceId: request.traceId ?? null,
    detail: request.detail ?? null
  };
}

function createActionAvailability(
  actor: AuthContext | undefined,
  request: DeepInspectionActionRequest
): DeepInspectionActionAvailability {
  const decision = authorizeDeepInspectionAction(actor, request);

  return {
    kind: request.action,
    label: DEEP_INSPECTION_ACTION_LABELS[request.action],
    allowed: decision.allowed,
    reason: decision.reason
  };
}

function createToolHistoryEntry(
  toolCall: AgentInspectionView['recentToolCalls'][number]
): DeepInspectionToolHistoryEntry {
  const fileRefs = extractToolFileRefs(toolCall.params);
  const testRelated = isTestRelatedToolCall(toolCall);

  return {
    id: `inspection-tool:${toolCall.sequenceId}`,
    tool: toolCall.tool,
    timestamp: toolCall.timestamp,
    sequenceId: toolCall.sequenceId,
    sourceEventType: toolCall.sourceEventType,
    traceId: toolCall.traceId ?? null,
    taskId: readStringValue(toolCall.params.task_id) ?? null,
    fileRefs,
    testRelated,
    summary: createToolHistorySummary(toolCall.tool, fileRefs, testRelated)
  };
}

function createVerificationSummary(inspection: AgentInspectionView): {
  verificationProofRefs: readonly string[];
  latestVerificationVerdict: VerificationGateResult | null;
  latestVerificationCorrelationId: string | null;
} {
  const verificationSteps = inspection.recentWorkflowSteps
    .filter((workflowStep) => workflowStep.sourceEventType === 'verification_gate')
    .sort((left, right) => right.sequenceId - left.sequenceId);
  const latestVerificationStep = verificationSteps[0];

  return {
    verificationProofRefs: [
      ...new Set(
        verificationSteps
          .map((workflowStep) => readStringValue(workflowStep.metadata.verificationRef))
          .filter((verificationRef): verificationRef is string => verificationRef !== null)
      )
    ],
    latestVerificationVerdict:
      latestVerificationStep === undefined ? null : readVerificationGateResult(latestVerificationStep.metadata.verdict),
    latestVerificationCorrelationId:
      latestVerificationStep === undefined ? null : readStringValue(latestVerificationStep.metadata.correlationId)
  };
}

function createSessionSummary(
  inspection: AgentInspectionView,
  toolHistory: readonly DeepInspectionToolHistoryEntry[]
): DeepInspectionSessionSummary {
  const uniqueFileRefs = new Set(toolHistory.flatMap((entry) => entry.fileRefs));
  const traceIds = new Set<string>();

  for (const toolEntry of toolHistory) {
    if (toolEntry.traceId !== null) {
      traceIds.add(toolEntry.traceId);
    }
  }

  for (const workflowStep of inspection.recentWorkflowSteps) {
    if (workflowStep.traceId !== undefined) {
      traceIds.add(workflowStep.traceId);
    }
  }

  for (const decisionCard of inspection.decisionCards) {
    if (decisionCard.traceId !== null) {
      traceIds.add(decisionCard.traceId);
    }
  }

  return {
    toolCallCount: toolHistory.length,
    uniqueFileCount: uniqueFileRefs.size,
    testRunCount: toolHistory.filter((entry) => entry.testRelated).length,
    workflowStepCount: inspection.recentWorkflowSteps.length,
    decisionCardCount: inspection.decisionCards.length,
    traceCount: traceIds.size
  };
}

function createToolHistorySummary(tool: string, fileRefs: readonly string[], testRelated: boolean): string {
  if (fileRefs.length === 0) {
    return testRelated ? `${tool} executed a test-oriented action.` : `${tool} executed without file targets.`;
  }

  if (fileRefs.length === 1) {
    return `${tool} touched ${fileRefs[0]}.`;
  }

  return `${tool} touched ${fileRefs.length} file targets.`;
}

function extractToolFileRefs(params: Record<string, JsonValue>): string[] {
  const values: string[] = [];

  for (const key of ['path', 'filePath', 'targetPath', 'outputPath']) {
    const value = params[key];
    if (typeof value === 'string' && value.length > 0) {
      values.push(value);
    }
  }

  const filesValue = params.files;
  if (Array.isArray(filesValue)) {
    for (const value of filesValue) {
      if (typeof value === 'string' && value.length > 0) {
        values.push(value);
      }
    }
  }

  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function isTestRelatedToolCall(toolCall: AgentInspectionView['recentToolCalls'][number]): boolean {
  return (
    toolCall.sourceEventType === 'test_run' ||
    toolCall.tool.toLowerCase().includes('test') ||
    (Array.isArray(toolCall.params.files) && toolCall.params.files.some((value) => typeof value === 'string' && value.includes('.test.')))
  );
}

function resolveBranch(
  config: Record<string, JsonValue>,
  targetAgentId: string,
  inspection: AgentInspectionView,
  dashboard: RuntimeDashboardView | undefined
): string | null {
  const taskIds = new Set(inspection.assignedTasks.map((task) => task.id));

  if (dashboard !== undefined) {
    const lease = dashboard.leaseView.leases.find(
      (entry) => taskIds.has(entry.taskId) && entry.branch !== null && entry.branch !== undefined
    );

    if (lease?.branch !== null && lease?.branch !== undefined) {
      return lease.branch;
    }
  }

  return resolveAgentStringField(config, targetAgentId, ['branch', 'gitBranch', 'worktreeBranch']);
}

function resolveTokenUsage(config: Record<string, JsonValue>, targetAgentId: string): DeepInspectionTokenUsage {
  const tokenObject = resolveAgentField(config, targetAgentId, ['tokens', 'tokenUsage', 'token_usage']);
  let budget = readNumberValue(resolveAgentField(config, targetAgentId, ['tokenBudget', 'token_budget']));
  let used = readNumberValue(resolveAgentField(config, targetAgentId, ['contextTokens', 'context_tokens', 'tokenUsed', 'token_used']));
  let remaining = readNumberValue(resolveAgentField(config, targetAgentId, ['tokensRemaining', 'tokens_remaining']));
  let source: DeepInspectionTokenSource = 'unknown';

  if (isJsonRecord(tokenObject)) {
    budget = budget ?? readNumberValue(tokenObject.budget ?? tokenObject.max ?? tokenObject.limit);
    used = used ?? readNumberValue(tokenObject.used ?? tokenObject.consumed ?? tokenObject.context);
    remaining = remaining ?? readNumberValue(tokenObject.remaining ?? tokenObject.left);
    source = 'config';
  }

  if (budget !== null || used !== null || remaining !== null) {
    source = source === 'unknown' ? 'config' : source;
  }

  if (remaining === null && budget !== null && used !== null) {
    remaining = Math.max(0, budget - used);
    source = source === 'unknown' ? 'derived' : source;
  }

  return {
    budget,
    used,
    remaining,
    source
  };
}

function resolveAgentStringField(
  config: Record<string, JsonValue>,
  targetAgentId: string,
  aliases: readonly string[]
): string | null {
  return readStringValue(resolveAgentField(config, targetAgentId, aliases));
}

function resolveAgentField(
  config: Record<string, JsonValue>,
  targetAgentId: string,
  aliases: readonly string[]
): JsonValue | undefined {
  const basePaths = [
    ['agentProfiles', targetAgentId],
    ['agent_profiles', targetAgentId],
    ['agents', targetAgentId],
    ['inspection', 'agents', targetAgentId],
    [targetAgentId]
  ];

  for (const basePath of basePaths) {
    for (const alias of aliases) {
      const value = readConfigPath(config, [...basePath, alias]);
      if (value !== undefined) {
        return value;
      }
    }
  }

  for (const alias of aliases) {
    for (const prefix of ['agentProfiles', 'agent_profiles', 'agents', 'inspection.agents']) {
      const flatValue = config[`${prefix}.${targetAgentId}.${alias}`];
      if (flatValue !== undefined) {
        return flatValue;
      }
    }

    const directValue = config[`${targetAgentId}.${alias}`];
    if (directValue !== undefined) {
      return directValue;
    }
  }

  return undefined;
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

function isJsonRecord(
  value: JsonValue | Record<string, JsonValue> | undefined
): value is Record<string, JsonValue> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readStringValue(value: JsonValue | undefined): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function readNumberValue(value: JsonValue | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function readVerificationGateResult(value: JsonValue | undefined): VerificationGateResult | null {
  return value === 'PASS' || value === 'FAIL' ? value : null;
}

function compareDeepInspectionAuditEntries(
  left: DeepInspectionActionAuditEntry,
  right: DeepInspectionActionAuditEntry
): number {
  if (left.at !== right.at) {
    return right.at.localeCompare(left.at);
  }

  return left.id.localeCompare(right.id);
}