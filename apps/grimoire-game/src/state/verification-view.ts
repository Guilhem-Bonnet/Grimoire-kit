import type { TaskStatus } from '../contracts/events';

import {
  createDecisionCardView,
  evaluateTaskDecisionCardGate,
  type DecisionCardGate
} from './decision-card-view';
import {
  createExperimentView,
  evaluateTaskExperimentGate,
  type ExperimentTaskGate
} from './experiment-view';
import {
  createGovernanceDriftView,
  evaluateTaskGovernanceDriftGate,
  type GovernanceDriftTaskGate
} from './governance-drift-view';
import {
  createIncidentRecoveryView,
  evaluateTaskIncidentRecoveryGate,
  type IncidentRecoveryTaskGate
} from './incident-recovery-view';
import {
  createInvestigationLabView,
  evaluateTaskInvestigationLab,
  type InvestigationLabTaskView
} from './investigation-lab-view';
import { createTaskView, type TaskInspectionView } from './task-view';
import type { GameState, HostReviewArtifactRecord } from './game-state';
import {
  createCounterReviewView,
  evaluateTaskCounterReviewProtocol,
  type CounterReviewProtocol
} from './counter-review-view';
import { createFinOpsView, evaluateTaskFinOpsGate, type FinOpsTaskGate } from './finops-view';
import {
  createMemoryRecallView,
  evaluateTaskMemoryRecallGate,
  type MemoryRecallTaskGate
} from './memory-recall-view';

export const VERIFICATION_REQUIREMENT_ORDER = [
  'TASK_ASSIGNED',
  'TASK_HAS_ACTIVITY',
  'TASK_HAS_TRACE',
  'TASK_HAS_ACTION_ID',
  'TASK_HAS_VERIFICATION_REF',
  'TASK_HAS_CONTROLS_EXECUTED',
  'TASK_HAS_EVIDENCE_REFS',
  'TASK_HAS_ACTIONABLE_EVIDENCE',
  'TASK_NO_OPEN_CRITICAL_FINDINGS',
  'TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS',
  'TASK_EXPERIMENT_DECISION_COMPLETE',
  'TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE',
  'TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD',
  'TASK_CRITICAL_DECISION_CARD_COMPLETE',
  'TASK_CRITICAL_COUNTER_REVIEW_COMPLETE',
  'TASK_CRITICAL_FINOPS_EXTRACT_PRESENT',
  'TASK_OBSOLESCENCE_RATE_WITHIN_THRESHOLD',
  'TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED',
  'TASK_DEBUG_PHASE_SEQUENCE_COMPLETE',
  'TASK_ARCHITECTURE_REVIEW_TRIGGERED_AFTER_REPEAT_FIX_FAILURES'
] as const;

export type VerificationRequirementCode = (typeof VERIFICATION_REQUIREMENT_ORDER)[number];

export interface VerificationRequirement {
  code: VerificationRequirementCode;
  satisfied: boolean;
  message: string;
}

export type VerificationVerdict = 'PASS' | 'FAIL' | 'WARN';

export interface VerificationChainSnapshot {
  actionId: string | null;
  verificationRef: string | null;
  traceId: string | null;
  verdict: VerificationVerdict | null;
  controlsExecuted: readonly string[];
  evidenceRefs: readonly string[];
  linkedExternalReviews: readonly VerificationLinkedExternalReview[];
}

export interface VerificationLinkedExternalReview {
  reviewId: string;
  hostId: string;
  hostDisplayName: string | null;
  sourceType: string;
  subjectRef: string;
  verdict: string;
  findingCount: number;
  openFindingCount: number;
  openCriticalFindingCount: number;
  linkedEvidenceRefs: readonly string[];
  importedAt: string;
  traceId: string | null;
  taskId: string | null;
}

export interface TaskVerificationGate {
  taskId: string;
  taskTitle: string;
  taskStatus: TaskStatus;
  isReadyForDone: boolean;
  evidenceCount: number;
  traceCount: number;
  verificationChain: VerificationChainSnapshot;
  unmetRequirementCodes: readonly VerificationRequirementCode[];
  requirements: readonly VerificationRequirement[];
}

export interface TaskReviewVerificationGate {
  taskId: string;
  taskTitle: string;
  taskStatus: TaskStatus;
  isApplicable: boolean;
  isReadyForReview: boolean;
  unmetRequirementCodes: readonly VerificationRequirementCode[];
  requirements: readonly VerificationRequirement[];
}

export interface ReviewVerificationViewMetrics {
  taskCount: number;
  applicableCount: number;
  readyCount: number;
  blockedCount: number;
}

export interface ReviewVerificationView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly TaskReviewVerificationGate[];
  metrics: ReviewVerificationViewMetrics;
}

export interface VerificationViewMetrics {
  taskCount: number;
  readyCount: number;
  blockedCount: number;
  activeReadyCount: number;
}

export interface VerificationView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly TaskVerificationGate[];
  metrics: VerificationViewMetrics;
}

export function createVerificationView(state: GameState): VerificationView {
  const taskView = createTaskView(state);
  const hostDisplayNames = createHostDisplayNameIndex(state);
  const experimentView = createExperimentView(state);
  const experimentByTaskId = new Map(experimentView.tasks.map((taskGate) => [taskGate.taskId, taskGate]));
  const incidentRecoveryView = createIncidentRecoveryView(state);
  const incidentRecoveryByTaskId = new Map(incidentRecoveryView.tasks.map((taskGate) => [taskGate.taskId, taskGate]));
  const governanceDriftView = createGovernanceDriftView(state);
  const governanceDriftByTaskId = new Map(governanceDriftView.tasks.map((taskGate) => [taskGate.taskId, taskGate]));
  const decisionCardView = createDecisionCardView(state);
  const decisionCardByTaskId = new Map(decisionCardView.taskGates.map((taskGate) => [taskGate.taskId, taskGate]));
  const counterReviewView = createCounterReviewView(state);
  const counterReviewByTaskId = new Map(counterReviewView.tasks.map((protocol) => [protocol.taskId, protocol]));
  const finOpsView = createFinOpsView(state);
  const finOpsByTaskId = new Map(finOpsView.tasks.map((task) => [task.taskId, task]));
  const tasks = taskView.tasks
    .map((task) =>
      createTaskVerificationGate(
        task,
        state.recentHostReviews ?? [],
        hostDisplayNames,
        experimentByTaskId.get(task.task.id) ?? null,
        incidentRecoveryByTaskId.get(task.task.id) ?? null,
        governanceDriftByTaskId.get(task.task.id) ?? null,
        decisionCardByTaskId.get(task.task.id) ?? null,
        counterReviewByTaskId.get(task.task.id) ?? null,
        finOpsByTaskId.get(task.task.id) ?? null
      )
    )
    .sort(compareTaskVerificationGates);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks,
    metrics: {
      taskCount: tasks.length,
      readyCount: tasks.filter((task) => task.isReadyForDone).length,
      blockedCount: tasks.filter((task) => !task.isReadyForDone).length,
      activeReadyCount: tasks.filter(
        (task) => task.isReadyForDone && (task.taskStatus === 'in_progress' || task.taskStatus === 'review')
      ).length
    }
  };
}

export function createReviewVerificationView(state: GameState): ReviewVerificationView {
  const taskView = createTaskView(state);
  const memoryRecallView = createMemoryRecallView(state);
  const memoryRecallGates = new Map(memoryRecallView.taskGates.map((gate) => [gate.taskId, gate]));
  const investigationLabView = createInvestigationLabView(state);
  const investigationLabByTaskId = new Map(investigationLabView.tasks.map((task) => [task.taskId, task]));
  const tasks = taskView.tasks
    .map((task) =>
      createTaskReviewVerificationGate(
        task,
        memoryRecallGates.get(task.task.id) ?? null,
        investigationLabByTaskId.get(task.task.id) ?? null
      )
    )
    .sort(compareTaskReviewVerificationGates);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks,
    metrics: {
      taskCount: tasks.length,
      applicableCount: tasks.filter((task) => task.isApplicable).length,
      readyCount: tasks.filter((task) => task.isReadyForReview).length,
      blockedCount: tasks.filter((task) => task.isApplicable && !task.isReadyForReview).length
    }
  };
}

export function evaluateTaskVerificationGate(state: GameState, taskId: string): TaskVerificationGate | null {
  const taskView = createTaskView(state);
  const inspection = taskView.tasks.find((task) => task.task.id === taskId);
  if (inspection === undefined) {
    return null;
  }

  return createTaskVerificationGate(
    inspection,
    state.recentHostReviews ?? [],
    createHostDisplayNameIndex(state),
    evaluateTaskExperimentGate(state, taskId),
    evaluateTaskIncidentRecoveryGate(state, taskId),
    evaluateTaskGovernanceDriftGate(state, taskId),
    evaluateTaskDecisionCardGate(state, taskId),
    evaluateTaskCounterReviewProtocol(state, taskId),
    evaluateTaskFinOpsGate(state, taskId)
  );
}

export function evaluateTaskReviewVerificationGate(state: GameState, taskId: string): TaskReviewVerificationGate | null {
  const taskView = createTaskView(state);
  const inspection = taskView.tasks.find((task) => task.task.id === taskId);
  if (inspection === undefined) {
    return null;
  }

  return createTaskReviewVerificationGate(
    inspection,
    evaluateTaskMemoryRecallGate(state, taskId),
    evaluateTaskInvestigationLab(state, taskId)
  );
}

function createTaskVerificationGate(
  task: TaskInspectionView,
  hostReviews: readonly HostReviewArtifactRecord[],
  hostDisplayNames: Record<string, string>,
  experimentTaskGate: ExperimentTaskGate | null,
  incidentRecoveryTaskGate: IncidentRecoveryTaskGate | null,
  governanceDriftTaskGate: GovernanceDriftTaskGate | null,
  decisionCardGate: DecisionCardGate | null,
  counterReviewProtocol: CounterReviewProtocol | null,
  finOpsTaskGate: FinOpsTaskGate | null
): TaskVerificationGate {
  const baseVerificationChain = extractVerificationChain(task);
  const linkedExternalReviews = collectLinkedExternalReviews(task, hostReviews, hostDisplayNames, baseVerificationChain);
  const verificationChain = enrichVerificationChainFromExternalReviews(
    baseVerificationChain,
    linkedExternalReviews,
    task.task.id
  );
  const hasActionId = baseVerificationChain.actionId !== null;
  const hasVerificationRef = baseVerificationChain.verificationRef !== null;
  const hasControlsExecuted = baseVerificationChain.controlsExecuted.length > 0;
  const hasEvidenceRefs = baseVerificationChain.evidenceRefs.length > 0;
  const evidenceCount =
    task.decisionCards.length + task.recentToolCalls.length + task.recentWorkflowSteps.length + linkedExternalReviews.length;
  const traceCount = task.traceIds.length > 0 ? task.traceIds.length : verificationChain.traceId === null ? 0 : 1;
  const hasActionableEvidence =
    task.recentToolCalls.length > 0 ||
    task.decisionCards.some((decisionCard) => decisionCard.sourceEventType !== 'routing') ||
    linkedExternalReviews.length > 0;
  const hasOpenCriticalFindings =
    task.recentEntries.some(isOpenCriticalReviewFinding) ||
    linkedExternalReviews.some((review) => review.openCriticalFindingCount > 0);
  const hasOpenBlockingSecurityFindings = task.recentEntries.some(isOpenBlockingSecurityFinding);
  const investigationRequirements = createInvestigationRequirements(task);
  const requirements: VerificationRequirement[] = [
    {
      code: 'TASK_ASSIGNED',
      satisfied: task.assigneeAgentId !== null,
      message:
        task.assigneeAgentId !== null
          ? `Task ${task.task.title} is assigned to ${task.assigneeAgentName ?? task.assigneeAgentId}.`
          : `Task ${task.task.title} has no valid assignee.`
    },
    {
      code: 'TASK_HAS_ACTIVITY',
      satisfied: task.recentEntries.length > 0,
      message:
        task.recentEntries.length > 0
          ? `Task ${task.task.title} has ${task.recentEntries.length} observable entries.`
          : `Task ${task.task.title} has no observable runtime activity.`
    },
    {
      code: 'TASK_HAS_TRACE',
      satisfied: traceCount > 0,
      message:
        traceCount > 0
          ? `Task ${task.task.title} is linked to ${traceCount} trace(s).`
          : `Task ${task.task.title} has no trace correlation.`
    },
    {
      code: 'TASK_HAS_ACTION_ID',
      satisfied: hasActionId,
      message:
        hasActionId
          ? `Task ${task.task.title} references action ${baseVerificationChain.actionId}.`
          : `Task ${task.task.title} is missing actionId in verification metadata.`
    },
    {
      code: 'TASK_HAS_VERIFICATION_REF',
      satisfied: hasVerificationRef,
      message:
        hasVerificationRef
          ? `Task ${task.task.title} is linked to verificationRef ${baseVerificationChain.verificationRef}.`
          : `Task ${task.task.title} is missing verificationRef in verification metadata.`
    },
    {
      code: 'TASK_HAS_CONTROLS_EXECUTED',
      satisfied: hasControlsExecuted,
      message:
        hasControlsExecuted
          ? `Task ${task.task.title} records controlsExecuted (${baseVerificationChain.controlsExecuted.length}).`
          : `Task ${task.task.title} is missing controlsExecuted in verification metadata.`
    },
    {
      code: 'TASK_HAS_EVIDENCE_REFS',
      satisfied: hasEvidenceRefs,
      message:
        hasEvidenceRefs
          ? `Task ${task.task.title} records evidenceRefs (${baseVerificationChain.evidenceRefs.length}).`
          : `Task ${task.task.title} is missing evidenceRefs in verification metadata.`
    },
    {
      code: 'TASK_HAS_ACTIONABLE_EVIDENCE',
      satisfied: hasActionableEvidence,
      message:
        hasActionableEvidence
          ? `Task ${task.task.title} has actionable evidence.`
          : `Task ${task.task.title} lacks tool or decision evidence.`
    },
    {
      code: 'TASK_NO_OPEN_CRITICAL_FINDINGS',
      satisfied: !hasOpenCriticalFindings,
      message: hasOpenCriticalFindings
        ? `Task ${task.task.title} has unresolved critical review findings.`
        : `Task ${task.task.title} has no unresolved critical review findings.`
    },
    {
      code: 'TASK_NO_OPEN_BLOCKING_SECURITY_FINDINGS',
      satisfied: !hasOpenBlockingSecurityFindings,
      message: hasOpenBlockingSecurityFindings
        ? `Task ${task.task.title} has unresolved blocking security findings.`
        : `Task ${task.task.title} has no unresolved blocking security findings.`
    },
    createExperimentRequirement(task, experimentTaskGate),
    createIncidentRecoveryRequirement(task, incidentRecoveryTaskGate),
    createGovernanceDriftRequirement(task, governanceDriftTaskGate),
    createDecisionCardRequirement(task, decisionCardGate),
    createCounterReviewRequirement(task, counterReviewProtocol),
    createFinOpsRequirement(task, finOpsTaskGate),
    ...investigationRequirements.requirements
  ];
  const unmetRequirementCodes = requirements.filter((requirement) => !requirement.satisfied).map((requirement) => requirement.code);

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    taskStatus: task.task.status,
    isReadyForDone:
      unmetRequirementCodes.length === 0 && (verificationChain.verdict === null || verificationChain.verdict === 'PASS'),
    evidenceCount,
    traceCount,
    verificationChain,
    unmetRequirementCodes,
    requirements
  };
}

function createExperimentRequirement(
  task: TaskInspectionView,
  experimentTaskGate: ExperimentTaskGate | null
): VerificationRequirement {
  if (experimentTaskGate === null || !experimentTaskGate.isApplicable) {
    return {
      code: 'TASK_EXPERIMENT_DECISION_COMPLETE',
      satisfied: true,
      message: `Task ${task.task.title} has no traced experimentation record.`
    };
  }

  if (!experimentTaskGate.isReady) {
    return {
      code: 'TASK_EXPERIMENT_DECISION_COMPLETE',
      satisfied: false,
      message:
        experimentTaskGate.blockingReason ??
        `Task ${task.task.title} is missing measurement or decision evidence for experimentation closeout.`
    };
  }

  return {
    code: 'TASK_EXPERIMENT_DECISION_COMPLETE',
    satisfied: true,
    message: `Task ${task.task.title} closes experimentation with explicit measurement and decision evidence.`
  };
}

function createIncidentRecoveryRequirement(
  task: TaskInspectionView,
  incidentRecoveryTaskGate: IncidentRecoveryTaskGate | null
): VerificationRequirement {
  if (task.task.priority !== 'critical') {
    return {
      code: 'TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE',
      satisfied: true,
      message: `Task ${task.task.title} is not marked critical; incident recovery evidence is optional.`
    };
  }

  if (incidentRecoveryTaskGate === null || !incidentRecoveryTaskGate.isApplicable) {
    return {
      code: 'TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE',
      satisfied: true,
      message: `Critical task ${task.task.title} has no traced incident recovery scenario.`
    };
  }

  if (!incidentRecoveryTaskGate.isReady) {
    return {
      code: 'TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE',
      satisfied: false,
      message:
        incidentRecoveryTaskGate.blockingReason ??
        `Critical task ${task.task.title} is missing compliant recovery evidence.`
    };
  }

  return {
    code: 'TASK_CRITICAL_RECOVERY_EXERCISE_COMPLETE',
    satisfied: true,
    message: `Critical task ${task.task.title} includes a compliant recovery exercise for ${incidentRecoveryTaskGate.scenarios.length} incident scenario(s).`
  };
}

function createGovernanceDriftRequirement(
  task: TaskInspectionView,
  governanceDriftTaskGate: GovernanceDriftTaskGate | null
): VerificationRequirement {
  if (task.task.priority !== 'critical') {
    return {
      code: 'TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD',
      satisfied: true,
      message: `Task ${task.task.title} is not marked critical; prompt/policy drift governance is optional.`
    };
  }

  if (governanceDriftTaskGate === null || !governanceDriftTaskGate.isApplicable) {
    return {
      code: 'TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD',
      satisfied: true,
      message: `Critical task ${task.task.title} has no prompt/policy governance change detected.`
    };
  }

  if (!governanceDriftTaskGate.isReady) {
    return {
      code: 'TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD',
      satisfied: false,
      message:
        governanceDriftTaskGate.blockingReason ??
        `Critical task ${task.task.title} is missing prompt/policy governance drift evidence.`
    };
  }

  return {
    code: 'TASK_CRITICAL_GOVERNANCE_DRIFT_WITHIN_THRESHOLD',
    satisfied: true,
    message:
      governanceDriftTaskGate.reportRef === null
        ? `Critical task ${task.task.title} keeps prompt/policy drift within threshold ${governanceDriftTaskGate.threshold}.`
        : `Critical task ${task.task.title} keeps prompt/policy drift within threshold ${governanceDriftTaskGate.threshold} with report ${governanceDriftTaskGate.reportRef}.`
  };
}

function createDecisionCardRequirement(
  task: TaskInspectionView,
  decisionCardGate: DecisionCardGate | null
): VerificationRequirement {
  if (task.task.priority !== 'critical') {
    return {
      code: 'TASK_CRITICAL_DECISION_CARD_COMPLETE',
      satisfied: true,
      message: `Task ${task.task.title} is not marked critical; a structured decision card is optional.`
    };
  }

  if (decisionCardGate === null || decisionCardGate.cardId === null) {
    return {
      code: 'TASK_CRITICAL_DECISION_CARD_COMPLETE',
      satisfied: false,
      message: `Critical task ${task.task.title} is missing a structured decision card for task.transition.done.`
    };
  }

  if (!decisionCardGate.isReady) {
    return {
      code: 'TASK_CRITICAL_DECISION_CARD_COMPLETE',
      satisfied: false,
      message: `Critical task ${task.task.title} has an incomplete decision card: missing ${decisionCardGate.missingFields.join(', ')}.`
    };
  }

  return {
    code: 'TASK_CRITICAL_DECISION_CARD_COMPLETE',
    satisfied: true,
    message: `Critical task ${task.task.title} includes structured decision card ${decisionCardGate.cardId}.`
  };
}

function createFinOpsRequirement(task: TaskInspectionView, finOpsTaskGate: FinOpsTaskGate | null): VerificationRequirement {
  if (task.task.priority !== 'critical') {
    return {
      code: 'TASK_CRITICAL_FINOPS_EXTRACT_PRESENT',
      satisfied: true,
      message: `Task ${task.task.title} is not marked critical; the FinOps review extract is optional.`
    };
  }

  if (finOpsTaskGate === null || !finOpsTaskGate.metricsAvailable) {
    return {
      code: 'TASK_CRITICAL_FINOPS_EXTRACT_PRESENT',
      satisfied: false,
      message: `Critical task ${task.task.title} is missing FinOps cost/token/latency metrics.`
    };
  }

  if (!finOpsTaskGate.hasReviewExtract) {
    return {
      code: 'TASK_CRITICAL_FINOPS_EXTRACT_PRESENT',
      satisfied: false,
      message: `Critical task ${task.task.title} must attach a FinOps extract before done.`
    };
  }

  return {
    code: 'TASK_CRITICAL_FINOPS_EXTRACT_PRESENT',
    satisfied: true,
    message:
      finOpsTaskGate.driftCodes.length === 0
        ? `Critical task ${task.task.title} includes FinOps extract ${finOpsTaskGate.reviewExtractRef}.`
        : `Critical task ${task.task.title} includes FinOps extract ${finOpsTaskGate.reviewExtractRef}; drift alerts remain visible for review.`
  };
}

function createCounterReviewRequirement(
  task: TaskInspectionView,
  counterReviewProtocol: CounterReviewProtocol | null
): VerificationRequirement {
  if (task.task.priority !== 'critical') {
    return {
      code: 'TASK_CRITICAL_COUNTER_REVIEW_COMPLETE',
      satisfied: true,
      message: `Task ${task.task.title} is not marked critical; the anti-echo counter-review protocol is optional.`
    };
  }

  if (counterReviewProtocol === null) {
    return {
      code: 'TASK_CRITICAL_COUNTER_REVIEW_COMPLETE',
      satisfied: false,
      message: `Critical task ${task.task.title} has no traced counter-review protocol.`
    };
  }

  return {
    code: 'TASK_CRITICAL_COUNTER_REVIEW_COMPLETE',
    satisfied: counterReviewProtocol.isReady,
    message:
      counterReviewProtocol.isReady
        ? `Critical task ${task.task.title} completed the anti-echo counter-review protocol.`
        : (counterReviewProtocol.blockingReason ?? `Critical task ${task.task.title} has an incomplete counter-review protocol.`)
  };
}

function extractVerificationChain(task: TaskInspectionView): VerificationChainSnapshot {
  const orderedWorkflowSteps = [...task.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId);

  let actionId: string | null = null;
  let verificationRef: string | null = null;
  let traceId: string | null = null;
  let verdict: VerificationVerdict | null = null;
  let controlsExecuted: string[] = [];
  let evidenceRefs: string[] = [];

  for (const workflowStep of orderedWorkflowSteps) {
    const metadata = workflowStep.metadata as Record<string, unknown>;

    if (traceId === null) {
      traceId =
        readMetadataStringByKeys(metadata, ['traceId', 'trace_id']) ??
        (workflowStep.traceId === undefined ? null : workflowStep.traceId);
    }

    if (actionId === null) {
      actionId = readMetadataStringByKeys(metadata, ['actionId', 'action_id']);
    }

    if (verificationRef === null) {
      verificationRef = readMetadataStringByKeys(metadata, ['verificationRef', 'verification_ref']);
    }

    if (verdict === null) {
      verdict = readVerificationVerdict(metadata);
    }

    if (controlsExecuted.length === 0) {
      controlsExecuted = readMetadataStringListByKeys(metadata, ['controlsExecuted', 'controls_executed', 'controls']);
    }

    if (evidenceRefs.length === 0) {
      evidenceRefs = readMetadataEvidenceRefsByKeys(metadata, ['evidenceRefs', 'evidence_refs']);
    }

    if (
      actionId !== null &&
      verificationRef !== null &&
      traceId !== null &&
      controlsExecuted.length > 0 &&
      evidenceRefs.length > 0 &&
      verdict !== null
    ) {
      break;
    }
  }

  if (traceId === null) {
    traceId = task.traceIds[0] ?? null;
  }

  return {
    actionId,
    verificationRef,
    traceId,
    verdict,
    controlsExecuted,
    evidenceRefs,
    linkedExternalReviews: []
  };
}

function createHostDisplayNameIndex(state: GameState): Record<string, string> {
  return Object.fromEntries(Object.values(state.hostBindings ?? {}).map((binding) => [binding.binding.hostId, binding.binding.displayName]));
}

function collectLinkedExternalReviews(
  task: TaskInspectionView,
  hostReviews: readonly HostReviewArtifactRecord[],
  hostDisplayNames: Record<string, string>,
  verificationChain: VerificationChainSnapshot
): VerificationLinkedExternalReview[] {
  const taskRef = `task:${task.task.id}`;
  const taskTraceIds = new Set(task.traceIds);
  const linkedReviews = new Map<string, VerificationLinkedExternalReview>();
  const evidenceRefs = new Set(verificationChain.evidenceRefs);
  const traceId = verificationChain.traceId;

  for (const record of hostReviews) {
    const reviewTaskId = record.review.taskId ?? record.meta.taskId ?? null;
    const reviewTraceId = record.review.traceId ?? record.meta.traceId ?? null;
    const matchesTask = reviewTaskId === task.task.id || record.review.subjectRef === taskRef;
    const matchesTrace = reviewTraceId !== null && (taskTraceIds.has(reviewTraceId) || reviewTraceId === traceId);
    const matchesEvidence = record.review.linkedEvidenceRefs.some((evidenceRef) => evidenceRefs.has(evidenceRef));

    if (!matchesEvidence && !matchesTask && !matchesTrace) {
      continue;
    }

    linkedReviews.set(record.review.reviewId, {
      reviewId: record.review.reviewId,
      hostId: record.review.hostId,
      hostDisplayName: hostDisplayNames[record.review.hostId] ?? null,
      sourceType: record.review.sourceType,
      subjectRef: record.review.subjectRef,
      verdict: record.review.verdict,
      findingCount: record.review.findings.length,
      openFindingCount: countOpenExternalReviewFindings(record),
      openCriticalFindingCount: countOpenCriticalExternalReviewFindings(record),
      linkedEvidenceRefs: [...record.review.linkedEvidenceRefs],
      importedAt: record.review.importedAt,
      traceId: reviewTraceId,
      taskId: reviewTaskId
    });
  }

  return Array.from(linkedReviews.values()).sort(compareLinkedExternalReviews);
}

function countOpenExternalReviewFindings(record: HostReviewArtifactRecord): number {
  return record.review.findings.filter((finding) => finding.resolutionStatus !== 'resolved').length;
}

function countOpenCriticalExternalReviewFindings(record: HostReviewArtifactRecord): number {
  return record.review.findings.filter(
    (finding) => finding.severity === 'critical' && finding.resolutionStatus !== 'resolved'
  ).length;
}

function enrichVerificationChainFromExternalReviews(
  verificationChain: VerificationChainSnapshot,
  linkedExternalReviews: readonly VerificationLinkedExternalReview[],
  taskId: string
): VerificationChainSnapshot {
  if (linkedExternalReviews.length === 0) {
    return {
      ...verificationChain,
      linkedExternalReviews
    };
  }

  const latestReview = linkedExternalReviews[0];
  if (latestReview === undefined) {
    return {
      ...verificationChain,
      linkedExternalReviews
    };
  }

  const evidenceRefs = uniqueStrings([
    ...verificationChain.evidenceRefs,
    ...linkedExternalReviews.flatMap((review) =>
      review.linkedEvidenceRefs.length > 0 ? review.linkedEvidenceRefs : [normalizeExternalReviewEvidenceRef(review.reviewId)]
    )
  ]);

  return {
    actionId: verificationChain.actionId ?? `host.review.import:${latestReview.reviewId}`,
    verificationRef:
      verificationChain.verificationRef ??
      selectExternalReviewVerificationRef(linkedExternalReviews) ??
      `verify://host-review/${taskId}`,
    traceId: verificationChain.traceId ?? latestReview.traceId,
    verdict: verificationChain.verdict ?? normalizeExternalReviewVerdict(latestReview),
    controlsExecuted:
      verificationChain.controlsExecuted.length > 0
        ? verificationChain.controlsExecuted
        : uniqueStrings(linkedExternalReviews.map((review) => `host_review:${review.sourceType}`)),
    evidenceRefs,
    linkedExternalReviews
  };
}

function selectExternalReviewVerificationRef(
  linkedExternalReviews: readonly VerificationLinkedExternalReview[]
): string | null {
  for (const review of linkedExternalReviews) {
    const verificationRef = review.linkedEvidenceRefs.find((evidenceRef) => evidenceRef.startsWith('verify://'));
    if (verificationRef !== undefined) {
      return verificationRef;
    }
  }

  return null;
}

function normalizeExternalReviewVerdict(review: VerificationLinkedExternalReview): VerificationVerdict {
  if (review.verdict === 'fail') {
    return 'FAIL';
  }

  if (review.verdict === 'warn') {
    return 'WARN';
  }

  if (review.verdict === 'comment') {
    return review.openFindingCount > 0 ? 'WARN' : 'PASS';
  }

  if (review.openFindingCount > 0 || review.openCriticalFindingCount > 0) {
    return 'WARN';
  }

  return 'PASS';
}

function uniqueStrings(values: readonly string[]): string[] {
  return Array.from(new Set(values));
}

function normalizeExternalReviewEvidenceRef(reviewId: string): string {
  return reviewId.includes('://') ? reviewId : `review-artifact:${reviewId}`;
}

function compareLinkedExternalReviews(
  left: VerificationLinkedExternalReview,
  right: VerificationLinkedExternalReview
): number {
  if (left.importedAt !== right.importedAt) {
    return right.importedAt.localeCompare(left.importedAt);
  }

  return left.reviewId.localeCompare(right.reviewId);
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

  const normalizedValues = value
    .filter((entry): entry is string => typeof entry === 'string')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);

  return [...new Set(normalizedValues)];
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

function readVerificationVerdict(metadata: Record<string, unknown>): VerificationVerdict | null {
  const candidate = readMetadataStringByKeys(metadata, ['verdict', 'result', 'status']);
  if (candidate === null) {
    return null;
  }

  const normalized = candidate.toUpperCase();
  if (normalized === 'PASS' || normalized === 'FAIL' || normalized === 'WARN') {
    return normalized;
  }

  return null;
}

function createTaskReviewVerificationGate(
  task: TaskInspectionView,
  memoryRecallGate: MemoryRecallTaskGate | null,
  investigationLabTask: InvestigationLabTaskView | null
): TaskReviewVerificationGate {
  const investigationRequirements = createInvestigationRequirements(task);
  const requirements = [
    ...investigationRequirements.requirements,
    createReviewCriticalFindingRequirement(task, investigationLabTask),
    createArchitectureEscalationRequirement(task, investigationLabTask),
    createMemoryRecallRequirement(task, memoryRecallGate)
  ];
  const unmetRequirementCodes = requirements
    .filter((requirement) => !requirement.satisfied)
    .map((requirement) => requirement.code);

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    taskStatus: task.task.status,
    isApplicable:
      investigationRequirements.isApplicable ||
      investigationLabTask?.isApplicable === true ||
      (memoryRecallGate?.readCount ?? 0) > 0,
    isReadyForReview: unmetRequirementCodes.length === 0,
    unmetRequirementCodes,
    requirements
  };
}

function createReviewCriticalFindingRequirement(
  task: TaskInspectionView,
  investigationLabTask: InvestigationLabTaskView | null
): VerificationRequirement {
  if (investigationLabTask === null || investigationLabTask.openCriticalFindingCount === 0) {
    return {
      code: 'TASK_NO_OPEN_CRITICAL_FINDINGS',
      satisfied: true,
      message: `Task ${task.task.title} has no unresolved critical review findings blocking review progression.`
    };
  }

  return {
    code: 'TASK_NO_OPEN_CRITICAL_FINDINGS',
    satisfied: false,
    message: `Task ${task.task.title} still has ${investigationLabTask.openCriticalFindingCount} unresolved critical review finding(s).`
  };
}

function createArchitectureEscalationRequirement(
  task: TaskInspectionView,
  investigationLabTask: InvestigationLabTaskView | null
): VerificationRequirement {
  if (investigationLabTask === null || investigationLabTask.consecutiveFixFailureCount < 3) {
    return {
      code: 'TASK_ARCHITECTURE_REVIEW_TRIGGERED_AFTER_REPEAT_FIX_FAILURES',
      satisfied: true,
      message: `Task ${task.task.title} has not reached the repeated fix failure escalation threshold.`
    };
  }

  return {
    code: 'TASK_ARCHITECTURE_REVIEW_TRIGGERED_AFTER_REPEAT_FIX_FAILURES',
    satisfied: investigationLabTask.hasArchitectureReviewEscalation,
    message: investigationLabTask.hasArchitectureReviewEscalation
      ? `Task ${task.task.title} triggered architecture review after repeated fix failures.`
      : `Task ${task.task.title} requires architecture review after ${investigationLabTask.consecutiveFixFailureCount} consecutive fix_failed events.`
  };
}

function createMemoryRecallRequirement(
  task: TaskInspectionView,
  memoryRecallGate: MemoryRecallTaskGate | null
): VerificationRequirement {
  if (memoryRecallGate === null || memoryRecallGate.readCount === 0) {
    return {
      code: 'TASK_OBSOLESCENCE_RATE_WITHIN_THRESHOLD',
      satisfied: true,
      message: `Task ${task.task.title} has no obsolete memory recall sample to validate.`
    };
  }

  const thresholdLabel = formatVerificationPercent(memoryRecallGate.threshold);
  const rateLabel = formatVerificationPercent(memoryRecallGate.obsolescenceRate);

  return {
    code: 'TASK_OBSOLESCENCE_RATE_WITHIN_THRESHOLD',
    satisfied: !memoryRecallGate.blocked,
    message: memoryRecallGate.blocked
      ? `Task ${task.task.title} exceeds the memory obsolescence threshold (${rateLabel} > ${thresholdLabel}).`
      : `Task ${task.task.title} stays within the memory obsolescence threshold (${rateLabel} <= ${thresholdLabel}).`
  };
}

function createInvestigationRequirements(task: TaskInspectionView): {
  isApplicable: boolean;
  requirements: VerificationRequirement[];
} {
  const signals = collectInvestigationSignals(task);
  const hasInvestigationSignal =
    signals.some((signal) =>
      signal.rootCauseIdentified ||
      signal.patternIdentified ||
      signal.hypothesisDefined ||
      signal.implementationCompleted ||
      signal.fixProposed
    );

  if (!hasInvestigationSignal) {
    return {
      isApplicable: false,
      requirements: [
        {
          code: 'TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED',
          satisfied: true,
          message: `Task ${task.task.title} has no explicit FIX_PROPOSED signal to validate.`
        },
        {
          code: 'TASK_DEBUG_PHASE_SEQUENCE_COMPLETE',
          satisfied: true,
          message: `Task ${task.task.title} has no explicit investigation phase signal to validate.`
        }
      ]
    };
  }

  const rootCauseBeforeFix = hasRootCauseIdentifiedBeforeFixProposed(signals);
  const completeDebugPhaseSequence = hasCompleteDebugPhaseSequence(signals);

  return {
    isApplicable: true,
    requirements: [
      {
        code: 'TASK_ROOT_CAUSE_IDENTIFIED_BEFORE_FIX_PROPOSED',
        satisfied: rootCauseBeforeFix,
        message: rootCauseBeforeFix
          ? `Task ${task.task.title} only proposes fixes after ROOT_CAUSE_IDENTIFIED.`
          : `Task ${task.task.title} cannot propose a fix before ROOT_CAUSE_IDENTIFIED.`
      },
      {
        code: 'TASK_DEBUG_PHASE_SEQUENCE_COMPLETE',
        satisfied: completeDebugPhaseSequence,
        message: completeDebugPhaseSequence
          ? `Task ${task.task.title} completed debug phases root cause -> pattern -> hypothesis -> implementation.`
          : `Task ${task.task.title} must complete debug phases in order: root cause -> pattern -> hypothesis -> implementation.`
      }
    ]
  };
}

interface InvestigationSignal {
  sequenceId: number;
  rootCauseIdentified: boolean;
  patternIdentified: boolean;
  hypothesisDefined: boolean;
  implementationCompleted: boolean;
  fixProposed: boolean;
}

function collectInvestigationSignals(task: TaskInspectionView): InvestigationSignal[] {
  return [...task.recentWorkflowSteps]
    .sort((left, right) => left.sequenceId - right.sequenceId)
    .map((workflowStep) => {
      const tokens = collectInvestigationTokens(workflowStep);

      return {
        sequenceId: workflowStep.sequenceId,
        rootCauseIdentified: tokens.has('root_cause_identified') || tokens.has('root_cause'),
        patternIdentified: tokens.has('pattern_identified') || tokens.has('pattern'),
        hypothesisDefined:
          tokens.has('hypothesis') || tokens.has('hypothesis_defined') || tokens.has('hypothesis_validated'),
        implementationCompleted:
          tokens.has('implementation') || tokens.has('implementation_completed') || tokens.has('fix_implemented'),
        fixProposed: tokens.has('fix_proposed')
      };
    });
}

function collectInvestigationTokens(
  workflowStep: TaskInspectionView['recentWorkflowSteps'][number]
): Set<string> {
  const tokens = new Set<string>();

  addInvestigationToken(tokens, workflowStep.sourceEventType);
  addInvestigationToken(tokens, workflowStep.metadata.phase);
  addInvestigationToken(tokens, workflowStep.metadata.status);
  addInvestigationToken(tokens, workflowStep.metadata.outcome);
  addInvestigationToken(tokens, workflowStep.metadata.result);
  addInvestigationToken(tokens, workflowStep.metadata.checkpoint);

  return tokens;
}

function addInvestigationToken(tokens: Set<string>, value: unknown): void {
  const normalized = normalizeInvestigationToken(value);
  if (normalized !== null) {
    tokens.add(normalized);
  }
}

function normalizeInvestigationToken(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized.length === 0) {
    return null;
  }

  return normalized.replace(/[\s-]+/g, '_');
}

function hasRootCauseIdentifiedBeforeFixProposed(signals: readonly InvestigationSignal[]): boolean {
  const rootCauseSequenceIds = signals.filter((signal) => signal.rootCauseIdentified).map((signal) => signal.sequenceId);
  const fixProposedSequenceIds = signals.filter((signal) => signal.fixProposed).map((signal) => signal.sequenceId);

  if (fixProposedSequenceIds.length === 0) {
    return true;
  }

  if (rootCauseSequenceIds.length === 0) {
    return false;
  }

  return fixProposedSequenceIds.every((fixProposedSequenceId) =>
    rootCauseSequenceIds.some((rootCauseSequenceId) => rootCauseSequenceId < fixProposedSequenceId)
  );
}

function hasCompleteDebugPhaseSequence(signals: readonly InvestigationSignal[]): boolean {
  const rootCauseSequenceIds = signals.filter((signal) => signal.rootCauseIdentified).map((signal) => signal.sequenceId);
  const patternSequenceIds = signals.filter((signal) => signal.patternIdentified).map((signal) => signal.sequenceId);
  const hypothesisSequenceIds = signals.filter((signal) => signal.hypothesisDefined).map((signal) => signal.sequenceId);
  const implementationSequenceIds = signals
    .filter((signal) => signal.implementationCompleted)
    .map((signal) => signal.sequenceId);

  for (const rootCauseSequenceId of rootCauseSequenceIds) {
    const patternSequenceId = nextSequenceAfter(patternSequenceIds, rootCauseSequenceId);
    if (patternSequenceId === null) {
      continue;
    }

    const hypothesisSequenceId = nextSequenceAfter(hypothesisSequenceIds, patternSequenceId);
    if (hypothesisSequenceId === null) {
      continue;
    }

    const implementationSequenceId = nextSequenceAfter(implementationSequenceIds, hypothesisSequenceId);
    if (implementationSequenceId !== null) {
      return true;
    }
  }

  return false;
}

function nextSequenceAfter(sequenceIds: readonly number[], minimumExclusive: number): number | null {
  for (const sequenceId of sequenceIds) {
    if (sequenceId > minimumExclusive) {
      return sequenceId;
    }
  }

  return null;
}

function isOpenCriticalReviewFinding(entry: TaskInspectionView['recentEntries'][number]): boolean {
  if (entry.sourceEventType !== 'review' && entry.sourceEventType !== 'decision') {
    return false;
  }

  const severity = readMetadataString(entry.metadata.severity);
  if (severity !== 'critical') {
    return false;
  }

  const resolvedFlag = readMetadataBoolean(entry.metadata.resolved);
  if (resolvedFlag === true) {
    return false;
  }

  const status = readMetadataString(entry.metadata.status);
  if (status === 'resolved' || status === 'closed' || status === 'done') {
    return false;
  }

  return true;
}

function isOpenBlockingSecurityFinding(entry: TaskInspectionView['recentEntries'][number]): boolean {
  if (!isSecurityFindingEntry(entry)) {
    return false;
  }

  const resolvedFlag = readMetadataBooleanByKeys(entry.metadata, ['resolved']);
  if (resolvedFlag === true) {
    return false;
  }

  const status = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['status']));
  if (status === 'resolved' || status === 'closed' || status === 'done') {
    return false;
  }

  const confidenceScore = readMetadataNumberByKeys(entry.metadata, [
    'confidenceScore',
    'confidence_score',
    'confidence',
    'trustScore',
    'trust_score'
  ]);
  if (confidenceScore === null || confidenceScore < 8) {
    return false;
  }

  const severity = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['severity']));
  const surfaceId = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['surfaceId', 'surface_id', 'surface']));
  const origin = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['origin', 'provenance', 'source']));
  const requiredPolicy = normalizeMetadataToken(
    readMetadataStringByKeys(entry.metadata, ['requiredPolicy', 'required_policy', 'policy'])
  );
  const trustStatus = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['trustStatus', 'trust_status']));

  const hasExplicitSurface = surfaceId !== null;
  const missingProvenance =
    readMetadataBooleanByKeys(entry.metadata, ['missingProvenance', 'missing_provenance']) === true ||
    (hasExplicitSurface && origin === null);
  const missingPolicy =
    readMetadataBooleanByKeys(entry.metadata, ['missingPolicy', 'missing_policy']) === true ||
    (hasExplicitSurface && requiredPolicy === null);

  return severity === 'critical' || missingProvenance || missingPolicy || trustStatus === 'blocked';
}

function isSecurityFindingEntry(entry: TaskInspectionView['recentEntries'][number]): boolean {
  const sourceEventType = normalizeMetadataToken(entry.sourceEventType);
  if (sourceEventType === 'security_finding' || sourceEventType === 'security_audit_finding') {
    return true;
  }

  const topic = normalizeMetadataToken(readMetadataStringByKeys(entry.metadata, ['topic']));
  return topic === 'security_finding';
}

function normalizeMetadataToken(value: string | null): string | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  return normalized.length === 0 ? null : normalized;
}

function readMetadataBooleanByKeys(metadata: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = readMetadataBoolean(metadata[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function readMetadataNumberByKeys(metadata: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }

    if (typeof value === 'string') {
      const parsedValue = Number(value);
      if (Number.isFinite(parsedValue)) {
        return parsedValue;
      }
    }
  }

  return null;
}

function readMetadataString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  return value.trim().toLowerCase();
}

function readMetadataBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function formatVerificationPercent(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function compareTaskVerificationGates(left: TaskVerificationGate, right: TaskVerificationGate): number {
  if (left.isReadyForDone !== right.isReadyForDone) {
    return left.isReadyForDone ? 1 : -1;
  }

  if (left.unmetRequirementCodes.length !== right.unmetRequirementCodes.length) {
    return right.unmetRequirementCodes.length - left.unmetRequirementCodes.length;
  }

  if (left.evidenceCount !== right.evidenceCount) {
    return left.evidenceCount - right.evidenceCount;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareTaskReviewVerificationGates(left: TaskReviewVerificationGate, right: TaskReviewVerificationGate): number {
  if (left.isApplicable !== right.isApplicable) {
    return left.isApplicable ? -1 : 1;
  }

  if (left.isReadyForReview !== right.isReadyForReview) {
    return left.isReadyForReview ? 1 : -1;
  }

  if (left.unmetRequirementCodes.length !== right.unmetRequirementCodes.length) {
    return right.unmetRequirementCodes.length - left.unmetRequirementCodes.length;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}