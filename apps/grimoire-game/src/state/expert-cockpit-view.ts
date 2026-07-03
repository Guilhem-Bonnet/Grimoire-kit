import type { AgentRole } from '../contracts/events';
import type { AuthContext } from '../server/auth/rbac';

import {
  createDeepInspectionView,
  type DeepInspectionActionAuditEntry,
  type DeepInspectionView
} from './deep-inspection-view';
import type { GameState } from './game-state';
import {
  createRuntimeCockpitView,
  type RuntimeCockpitView
} from './runtime-cockpit-view';
import {
  createRuntimeDashboardView,
  type RuntimeDashboardControlPlaneState,
  type RuntimeDashboardView,
  type RuntimeDashboardViewOptions
} from './runtime-dashboard-view';
import type { RuntimeDashboardUiTone, RuntimeDashboardUiViewOptions } from './runtime-dashboard-ui-view';

export interface ExpertCockpitDecision {
  id: string;
  decision: RuntimeDashboardView['hostBridge']['invocations'][number]['decision'];
  actionKind: RuntimeDashboardView['hostBridge']['invocations'][number]['envelope']['actionKind'];
  mode: RuntimeDashboardView['hostBridge']['invocations'][number]['envelope']['mode'];
  reason: string;
  tone: RuntimeDashboardUiTone;
  hostId: string;
  traceId: string | null;
  taskId: string | null;
  correlationId: string;
  timestamp: string;
  requiredScopes: RuntimeDashboardView['hostBridge']['invocations'][number]['envelope']['requestedScopes'];
}

export interface ExpertCockpitWorkflowStep {
  id: string;
  title: string;
  detail: string;
  sourceEventType: string;
  timestamp: string;
  sequenceId: number;
  agentId: string | null;
  taskId: string | null;
  traceId: string | null;
}

export interface ExpertCockpitWorkflowSummary {
  traceId: string | null;
  currentStep: string | null;
  stepCount: number;
  decisionCount: number;
  decisionTitles: readonly string[];
  recentSteps: readonly ExpertCockpitWorkflowStep[];
}

export interface ExpertCockpitProofSummary {
  verificationRef: string | null;
  queueStatus: RuntimeDashboardView['verificationQueue']['items'][number]['queueStatus'] | null;
  verdict: RuntimeDashboardView['verificationQueue']['items'][number]['verdict'] | null;
  evidencePackId: string | null;
  evidenceRefCount: number;
  externalReviewCount: number;
  detail: string | null;
}

export interface ExpertCockpitReplaySummary {
  traceId: string | null;
  title: string | null;
  entryCount: number;
  canonicalEnvelopeCount: number;
  messageTypes: readonly string[];
  lastEventType: string | null;
}

export interface ExpertCockpitView {
  protocolVersion: string;
  lastSequenceId: number;
  runId: string | null;
  taskId: string | null;
  traceId: string | null;
  agentId: string | null;
  hostId: string | null;
  hostDisplayName: string | null;
  status: 'accepted' | 'refused' | 'pending';
  summary: string;
  decisions: readonly ExpertCockpitDecision[];
  inspection: DeepInspectionView | null;
  workflow: ExpertCockpitWorkflowSummary;
  proof: ExpertCockpitProofSummary;
  replay: ExpertCockpitReplaySummary;
  cockpit: RuntimeCockpitView;
}

export interface ExpertCockpitViewOptions {
  actor?: AuthContext;
  targetAgentId?: string;
  taskId?: string;
  traceId?: string;
  auditTrail?: readonly DeepInspectionActionAuditEntry[];
  dashboard?: RuntimeDashboardView;
  dashboardOptions?: RuntimeDashboardViewOptions;
  dashboardUiOptions?: RuntimeDashboardUiViewOptions;
  controlPlane?: RuntimeDashboardControlPlaneState;
}

export function createExpertCockpitView(
  state: GameState,
  options: ExpertCockpitViewOptions = {}
): ExpertCockpitView {
  const dashboardViewOptions =
    options.dashboardOptions?.observability === undefined
      ? undefined
      : { observability: options.dashboardOptions.observability };
  const dashboard =
    options.dashboard ??
    createRuntimeDashboardView(
      state,
      dashboardViewOptions,
      options.controlPlane ?? {
        projectRegistry: null,
        nodeRegistry: null,
        leaseStore: null
      }
    );
  const cockpit = createRuntimeCockpitView(dashboard, options.dashboardUiOptions);
  const traceId = resolveTraceId(dashboard, cockpit, options.traceId, options.taskId);
  const session = traceId === null ? null : (dashboard.session.sessions.find((record) => record.summary.traceId === traceId) ?? null);
  const taskId =
    options.taskId ??
    resolveTaskId(dashboard, session, cockpit.focus.taskId);
  const targetAgentId =
    options.targetAgentId ??
    (taskId === null ? null : (state.tasks[taskId]?.assigneeId ?? null));
  const decisions = collectExpertDecisions(dashboard, taskId, traceId);
  const hostId = decisions[0]?.hostId ?? dashboard.verificationEvidencePacks.packs[0]?.externalReviews[0]?.hostId ?? null;
  const hostDisplayName =
    hostId === null ? null : (dashboard.hostBridge.hosts.find((host) => host.hostId === hostId)?.displayName ?? null);
  const inspection =
    targetAgentId === null
      ? null
      : createDeepInspectionView(state, targetAgentId, {
          ...(options.actor === undefined ? {} : { actor: options.actor }),
          dashboard,
          ...(options.auditTrail === undefined ? {} : { auditTrail: options.auditTrail })
        });
  const proof = createExpertProofSummary(dashboard, taskId, traceId);
  const replay = createReplaySummary(session);
  const workflow = createWorkflowSummary(session);

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    runId: replay.traceId === null ? cockpit.focus.runId : (resolveRunId(session) ?? cockpit.focus.runId),
    taskId,
    traceId,
    agentId: targetAgentId,
    hostId,
    hostDisplayName,
    status: deriveExpertStatus(proof, decisions),
    summary: createExpertSummary(proof, decisions, hostDisplayName),
    decisions,
    inspection,
    workflow,
    proof,
    replay,
    cockpit
  };
}

function resolveTraceId(
  dashboard: RuntimeDashboardView,
  cockpit: RuntimeCockpitView,
  requestedTraceId: string | undefined,
  requestedTaskId: string | undefined
): string | null {
  if (requestedTraceId !== undefined) {
    return requestedTraceId;
  }

  const queueItem =
    requestedTaskId === undefined
      ? (dashboard.verificationQueue.items.find((item) => item.queueStatus === 'accepted') ?? dashboard.verificationQueue.items[0])
      : dashboard.verificationQueue.items.find((item) => item.taskId === requestedTaskId);

  if (queueItem?.traceId !== null && queueItem?.traceId !== undefined) {
    return queueItem.traceId;
  }

  const evidencePack =
    requestedTaskId === undefined
      ? dashboard.verificationEvidencePacks.packs[0]
      : dashboard.verificationEvidencePacks.packs.find((pack) => pack.taskRef === requestedTaskId);

  if (evidencePack?.traceId !== null && evidencePack?.traceId !== undefined) {
    return evidencePack.traceId;
  }

  if (requestedTaskId !== undefined) {
    const matchingSession = dashboard.session.sessions.find((session) => session.summary.taskIds.includes(requestedTaskId));
    if (matchingSession !== undefined) {
      return matchingSession.summary.traceId;
    }
  }

  return cockpit.focus.traceId ?? dashboard.session.sessions[0]?.summary.traceId ?? null;
}

function resolveTaskId(
  dashboard: RuntimeDashboardView,
  session: RuntimeDashboardView['session']['sessions'][number] | null,
  focusTaskId: string | null
): string | null {
  if (session?.summary.taskIds[0] !== undefined) {
    return session.summary.taskIds[0];
  }

  if (focusTaskId !== null) {
    return focusTaskId;
  }

  return dashboard.verificationEvidencePacks.packs[0]?.taskRef ?? null;
}

function collectExpertDecisions(
  dashboard: RuntimeDashboardView,
  taskId: string | null,
  traceId: string | null
): ExpertCockpitDecision[] {
  return dashboard.hostBridge.invocations
    .filter(
      (invocation) =>
        (taskId !== null && invocation.envelope.taskId === taskId) ||
        (traceId !== null && invocation.envelope.traceId === traceId)
    )
    .map((invocation) => ({
      id: `expert-decision:${invocation.sequenceId}`,
      decision: invocation.decision,
      actionKind: invocation.envelope.actionKind,
      mode: invocation.envelope.mode,
      reason: invocation.reason,
      tone: toneForDecision(invocation.decision),
      hostId: invocation.envelope.hostId,
      traceId: invocation.envelope.traceId ?? null,
      taskId: invocation.envelope.taskId ?? null,
      correlationId: invocation.envelope.correlationId,
      timestamp: invocation.timestamp,
      requiredScopes: [...invocation.envelope.requestedScopes]
    }))
    .sort(compareExpertDecisions);
}

function createWorkflowSummary(
  session: RuntimeDashboardView['session']['sessions'][number] | null
): ExpertCockpitWorkflowSummary {
  if (session === null) {
    return {
      traceId: null,
      currentStep: null,
      stepCount: 0,
      decisionCount: 0,
      decisionTitles: [],
      recentSteps: []
    };
  }

  const workflowSteps = session.entries
    .filter((entry) => entry.kind === 'workflow_step')
    .slice(0, 6)
    .map((entry) => ({
      id: `workflow:${entry.sequenceId}`,
      title: entry.title,
      detail: entry.detail,
      sourceEventType: entry.sourceEventType,
      timestamp: entry.timestamp,
      sequenceId: entry.sequenceId,
      agentId: entry.agentId,
      taskId: entry.taskId,
      traceId: entry.traceId
    }));

  return {
    traceId: session.summary.traceId,
    currentStep: workflowSteps[0]?.title ?? null,
    stepCount: session.summary.workflowStepCount,
    decisionCount: session.summary.decisionCount,
    decisionTitles: session.decisionCards.map((card) => card.title),
    recentSteps: workflowSteps
  };
}

function createExpertProofSummary(
  dashboard: RuntimeDashboardView,
  taskId: string | null,
  traceId: string | null
): ExpertCockpitProofSummary {
  const queueItem =
    (taskId === null ? undefined : dashboard.verificationQueue.items.find((item) => item.taskId === taskId)) ??
    (traceId === null ? undefined : dashboard.verificationQueue.items.find((item) => item.traceId === traceId));
  const evidencePack =
    (taskId === null ? undefined : dashboard.verificationEvidencePacks.packs.find((pack) => pack.taskRef === taskId)) ??
    (traceId === null ? undefined : dashboard.verificationEvidencePacks.packs.find((pack) => pack.traceId === traceId));

  return {
    verificationRef: queueItem?.verificationRef ?? evidencePack?.verificationRef ?? null,
    queueStatus: queueItem?.queueStatus ?? null,
    verdict: queueItem?.verdict ?? null,
    evidencePackId: evidencePack?.packId ?? null,
    evidenceRefCount: evidencePack?.evidenceRefs.length ?? 0,
    externalReviewCount: evidencePack?.externalReviews.length ?? 0,
    detail:
      evidencePack === undefined
        ? createVerificationQueueDetail(queueItem)
        : `${evidencePack.evidence.length} evidence record(s), ${evidencePack.externalReviews.length} external review(s).`
  };
}

function createVerificationQueueDetail(
  queueItem: RuntimeDashboardView['verificationQueue']['items'][number] | undefined
): string | null {
  if (queueItem === undefined) {
    return null;
  }

  return `${queueItem.unmetRequirementCodes.length} unmet requirement(s); ${queueItem.controlsExecuted.length} control(s); verdict ${queueItem.verdict ?? 'pending'}.`;
}

function createReplaySummary(
  session: RuntimeDashboardView['session']['sessions'][number] | null
): ExpertCockpitReplaySummary {
  if (session === null) {
    return {
      traceId: null,
      title: null,
      entryCount: 0,
      canonicalEnvelopeCount: 0,
      messageTypes: [],
      lastEventType: null
    };
  }

  return {
    traceId: session.summary.traceId,
    title: session.summary.title,
    entryCount: session.summary.entryCount,
    canonicalEnvelopeCount: session.canonicalEnvelopes.length,
    messageTypes: [...new Set(session.canonicalEnvelopes.map((envelope) => envelope.header.messageType))],
    lastEventType: session.summary.lastEventType
  };
}

function resolveRunId(session: RuntimeDashboardView['session']['sessions'][number] | null): string | null {
  if (session === null) {
    return null;
  }

  for (const envelope of session.canonicalEnvelopes) {
    if (envelope.context.runId !== undefined) {
      return envelope.context.runId;
    }
  }

  return null;
}

function deriveExpertStatus(
  proof: ExpertCockpitProofSummary,
  decisions: readonly ExpertCockpitDecision[]
): 'accepted' | 'refused' | 'pending' {
  if (proof.queueStatus === 'accepted') {
    return 'accepted';
  }

  if (decisions.some((decision) => decision.decision === 'DENY')) {
    return 'refused';
  }

  return 'pending';
}

function createExpertSummary(
  proof: ExpertCockpitProofSummary,
  decisions: readonly ExpertCockpitDecision[],
  hostDisplayName: string | null
): string {
  const acceptedDecision = decisions.find((decision) => decision.decision === 'ALLOW');
  const refusedDecision = decisions.find((decision) => decision.decision === 'DENY');
  const hostLabel = hostDisplayName ?? acceptedDecision?.hostId ?? refusedDecision?.hostId ?? 'unknown host';

  if (proof.queueStatus === 'accepted' && acceptedDecision !== undefined && refusedDecision !== undefined) {
    return `${hostLabel} completed a bounded ${acceptedDecision.mode} ${acceptedDecision.actionKind} flow; mirror path ${refusedDecision.mode} ${refusedDecision.actionKind} was denied explicitly.`;
  }

  if (proof.queueStatus === 'accepted' && acceptedDecision !== undefined) {
    return `${hostLabel} completed a bounded ${acceptedDecision.mode} ${acceptedDecision.actionKind} flow with verification proof attached.`;
  }

  if (refusedDecision !== undefined) {
    return `${hostLabel} was refused on ${refusedDecision.mode} ${refusedDecision.actionKind}: ${refusedDecision.reason}`;
  }

  return 'Expert cockpit is awaiting a proved or refused critical flow.';
}

function toneForDecision(
  decision: RuntimeDashboardView['hostBridge']['invocations'][number]['decision']
): RuntimeDashboardUiTone {
  if (decision === 'DENY') {
    return 'critical';
  }

  if (decision === 'PROMPT' || decision === 'DEGRADE') {
    return 'warning';
  }

  return 'positive';
}

function compareExpertDecisions(left: ExpertCockpitDecision, right: ExpertCockpitDecision): number {
  if (left.timestamp !== right.timestamp) {
    return right.timestamp.localeCompare(left.timestamp);
  }

  return left.id.localeCompare(right.id);
}