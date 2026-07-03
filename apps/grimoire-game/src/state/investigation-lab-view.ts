import type { VerificationGateResult } from '../contracts/events';

import { createTaskView, type TaskInspectionView } from './task-view';
import type { GameState } from './game-state';

export const INVESTIGATION_PHASE_ORDER = [
  'root_cause_identified',
  'pattern_identified',
  'hypothesis',
  'implementation_completed',
  'fix_proposed'
] as const;
export const INVESTIGATION_REVIEW_SEVERITY_ORDER = ['critical', 'important', 'minor'] as const;
export const INVESTIGATION_LAB_ISSUE_ORDER = [
  'INVESTIGATION_FIX_PROPOSED_BEFORE_ROOT_CAUSE',
  'INVESTIGATION_PHASE_SEQUENCE_INCOMPLETE',
  'INVESTIGATION_OPEN_CRITICAL_FINDING',
  'INVESTIGATION_ARCHITECTURE_REVIEW_REQUIRED'
] as const;

export type InvestigationPhase = (typeof INVESTIGATION_PHASE_ORDER)[number];
export type InvestigationReviewSeverity = (typeof INVESTIGATION_REVIEW_SEVERITY_ORDER)[number];
export type InvestigationLabIssueCode = (typeof INVESTIGATION_LAB_ISSUE_ORDER)[number];

export interface InvestigationLabTaskView {
  taskId: string;
  taskTitle: string;
  taskStatus: TaskInspectionView['task']['status'];
  isApplicable: boolean;
  isReadyForReviewProgression: boolean;
  latestVerificationRef: string | null;
  latestVerificationVerdict: VerificationGateResult | null;
  latestVerificationCorrelationId: string | null;
  completedPhases: readonly InvestigationPhase[];
  reviewSeverities: readonly InvestigationReviewSeverity[];
  openCriticalFindingCount: number;
  consecutiveFixFailureCount: number;
  hasArchitectureReviewEscalation: boolean;
  issueCodes: readonly InvestigationLabIssueCode[];
  blockingReasons: readonly string[];
}

export interface InvestigationLabViewSummary {
  taskCount: number;
  applicableCount: number;
  readyCount: number;
  blockedCount: number;
  architectureEscalationCount: number;
  openCriticalBlockingCount: number;
}

export interface InvestigationLabView {
  protocolVersion: string;
  lastSequenceId: number;
  tasks: readonly InvestigationLabTaskView[];
  summary: InvestigationLabViewSummary;
}

interface InvestigationSignal {
  sequenceId: number;
  rootCauseIdentified: boolean;
  patternIdentified: boolean;
  hypothesisDefined: boolean;
  implementationCompleted: boolean;
  fixProposed: boolean;
  fixFailed: boolean;
  architectureReviewRequested: boolean;
}

export function createInvestigationLabView(state: GameState): InvestigationLabView {
  const taskView = createTaskView(state);
  const tasks = taskView.tasks.map(createInvestigationLabTaskView).sort(compareInvestigationLabTasks);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    tasks,
    summary: {
      taskCount: tasks.length,
      applicableCount: tasks.filter((task) => task.isApplicable).length,
      readyCount: tasks.filter((task) => task.isApplicable && task.isReadyForReviewProgression).length,
      blockedCount: tasks.filter((task) => task.isApplicable && !task.isReadyForReviewProgression).length,
      architectureEscalationCount: tasks.filter((task) => task.hasArchitectureReviewEscalation).length,
      openCriticalBlockingCount: tasks.filter((task) => task.openCriticalFindingCount > 0).length
    }
  };
}

export function evaluateTaskInvestigationLab(
  state: GameState,
  taskId: string
): InvestigationLabTaskView | null {
  return createInvestigationLabView(state).tasks.find((task) => task.taskId === taskId) ?? null;
}

function createInvestigationLabTaskView(task: TaskInspectionView): InvestigationLabTaskView {
  const signals = collectInvestigationSignals(task);
  const latestVerificationGate = findLatestVerificationGate(task);
  const openCriticalFindingCount = countOpenCriticalReviewFindings(task);
  const hasInvestigationSignal =
    signals.some((signal) => signal.rootCauseIdentified || signal.patternIdentified || signal.hypothesisDefined || signal.implementationCompleted || signal.fixProposed || signal.fixFailed) ||
    openCriticalFindingCount > 0;
  const completedPhases = collectCompletedPhases(signals);
  const rootCauseBeforeFix = hasRootCauseIdentifiedBeforeFixProposed(signals);
  const completePhaseSequence = hasCompleteDebugPhaseSequence(signals);
  const consecutiveFixFailureCount = countMaximumConsecutiveFixFailures(signals);
  const hasArchitectureReviewEscalation = signals.some((signal) => signal.architectureReviewRequested);

  const issueCodes = uniqueIssueCodes([
    ...(hasInvestigationSignal && !rootCauseBeforeFix ? ['INVESTIGATION_FIX_PROPOSED_BEFORE_ROOT_CAUSE' as const] : []),
    ...(hasInvestigationSignal && !completePhaseSequence ? ['INVESTIGATION_PHASE_SEQUENCE_INCOMPLETE' as const] : []),
    ...(openCriticalFindingCount > 0 ? ['INVESTIGATION_OPEN_CRITICAL_FINDING' as const] : []),
    ...(consecutiveFixFailureCount >= 3 && !hasArchitectureReviewEscalation
      ? ['INVESTIGATION_ARCHITECTURE_REVIEW_REQUIRED' as const]
      : [])
  ]);

  return {
    taskId: task.task.id,
    taskTitle: task.task.title,
    taskStatus: task.task.status,
    isApplicable: hasInvestigationSignal,
    isReadyForReviewProgression: issueCodes.length === 0,
    latestVerificationRef: latestVerificationGate?.verificationRef ?? null,
    latestVerificationVerdict: latestVerificationGate?.verdict ?? null,
    latestVerificationCorrelationId: latestVerificationGate?.correlationId ?? null,
    completedPhases,
    reviewSeverities: collectReviewSeverities(task),
    openCriticalFindingCount,
    consecutiveFixFailureCount,
    hasArchitectureReviewEscalation,
    issueCodes,
    blockingReasons: createBlockingReasons(task.task.title, issueCodes, openCriticalFindingCount, consecutiveFixFailureCount)
  };
}

function collectCompletedPhases(signals: readonly InvestigationSignal[]): InvestigationPhase[] {
  return INVESTIGATION_PHASE_ORDER.filter((phase) => {
    if (phase === 'root_cause_identified') {
      return signals.some((signal) => signal.rootCauseIdentified);
    }

    if (phase === 'pattern_identified') {
      return signals.some((signal) => signal.patternIdentified);
    }

    if (phase === 'hypothesis') {
      return signals.some((signal) => signal.hypothesisDefined);
    }

    if (phase === 'implementation_completed') {
      return signals.some((signal) => signal.implementationCompleted);
    }

    return signals.some((signal) => signal.fixProposed);
  });
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
        fixProposed: tokens.has('fix_proposed'),
        fixFailed: tokens.has('fix_failed'),
        architectureReviewRequested: tokens.has('architecture_review') || tokens.has('architecture_review_required')
      };
    });
}

function collectInvestigationTokens(
  workflowStep: TaskInspectionView['recentWorkflowSteps'][number]
): Set<string> {
  const tokens = new Set<string>();

  addToken(tokens, workflowStep.sourceEventType);
  addToken(tokens, workflowStep.step);
  addToken(tokens, workflowStep.metadata.topic);
  addToken(tokens, workflowStep.metadata.phase);
  addToken(tokens, workflowStep.metadata.status);
  addToken(tokens, workflowStep.metadata.outcome);
  addToken(tokens, workflowStep.metadata.result);
  addToken(tokens, workflowStep.metadata.checkpoint);

  return tokens;
}

function addToken(tokens: Set<string>, value: unknown): void {
  const normalized = normalizeToken(value);
  if (normalized !== null) {
    tokens.add(normalized);
  }
}

function normalizeToken(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized.length === 0) {
    return null;
  }

  return normalized.replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
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

function countOpenCriticalReviewFindings(task: TaskInspectionView): number {
  return task.recentEntries.filter(isOpenCriticalReviewFinding).length;
}

function findLatestVerificationGate(task: TaskInspectionView): {
  verificationRef: string;
  verdict: VerificationGateResult | null;
  correlationId: string | null;
} | null {
  const latestEntry = [...task.recentWorkflowSteps]
    .filter((entry) => entry.sourceEventType === 'verification_gate')
    .sort((left, right) => right.sequenceId - left.sequenceId)[0];

  if (latestEntry === undefined) {
    return null;
  }

  const verificationRef = readMetadataString(latestEntry.metadata, ['verificationRef']);

  if (verificationRef === null) {
    return null;
  }

  return {
    verificationRef,
    verdict: readVerificationGateResult(latestEntry.metadata.verdict),
    correlationId: readMetadataString(latestEntry.metadata, ['correlationId'])
  };
}

function isOpenCriticalReviewFinding(entry: TaskInspectionView['recentEntries'][number]): boolean {
  if (entry.sourceEventType !== 'review' && entry.sourceEventType !== 'decision') {
    return false;
  }

  const severity = readMetadataString(entry.metadata, ['severity']);
  if (severity !== 'critical') {
    return false;
  }

  const resolvedFlag = readMetadataBoolean(entry.metadata, ['resolved']);
  if (resolvedFlag === true) {
    return false;
  }

  const status = readMetadataString(entry.metadata, ['status']);
  if (status === 'resolved' || status === 'closed' || status === 'done') {
    return false;
  }

  return true;
}

function collectReviewSeverities(task: TaskInspectionView): InvestigationReviewSeverity[] {
  return INVESTIGATION_REVIEW_SEVERITY_ORDER.filter((severity) =>
    task.recentEntries.some((entry) => readMetadataString(entry.metadata, ['severity']) === severity)
  );
}

function countMaximumConsecutiveFixFailures(signals: readonly InvestigationSignal[]): number {
  let currentCount = 0;
  let maximumCount = 0;

  for (const signal of signals) {
    if (signal.fixFailed) {
      currentCount += 1;
      maximumCount = Math.max(maximumCount, currentCount);
      continue;
    }

    currentCount = 0;
  }

  return maximumCount;
}

function createBlockingReasons(
  taskTitle: string,
  issueCodes: readonly InvestigationLabIssueCode[],
  openCriticalFindingCount: number,
  consecutiveFixFailureCount: number
): string[] {
  return issueCodes.map((issueCode) => {
    switch (issueCode) {
      case 'INVESTIGATION_FIX_PROPOSED_BEFORE_ROOT_CAUSE':
        return `Task ${taskTitle} cannot propose a fix before ROOT_CAUSE_IDENTIFIED.`;
      case 'INVESTIGATION_PHASE_SEQUENCE_INCOMPLETE':
        return `Task ${taskTitle} must complete root cause -> pattern -> hypothesis -> implementation before review.`;
      case 'INVESTIGATION_OPEN_CRITICAL_FINDING':
        return `Task ${taskTitle} still has ${openCriticalFindingCount} unresolved critical review finding(s).`;
      case 'INVESTIGATION_ARCHITECTURE_REVIEW_REQUIRED':
        return `Task ${taskTitle} reached ${consecutiveFixFailureCount} consecutive fix_failed events and now requires architecture review.`;
    }
  });
}

function compareInvestigationLabTasks(left: InvestigationLabTaskView, right: InvestigationLabTaskView): number {
  if (left.isApplicable !== right.isApplicable) {
    return left.isApplicable ? -1 : 1;
  }

  if (left.isReadyForReviewProgression !== right.isReadyForReviewProgression) {
    return left.isReadyForReviewProgression ? 1 : -1;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function readMetadataString(metadata: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalized = value.trim().toLowerCase();
    if (normalized.length > 0) {
      return normalized;
    }
  }

  return null;
}

function readMetadataBoolean(metadata: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readVerificationGateResult(value: unknown): VerificationGateResult | null {
  return value === 'PASS' || value === 'FAIL' ? value : null;
}

function uniqueIssueCodes(values: readonly InvestigationLabIssueCode[]): InvestigationLabIssueCode[] {
  return INVESTIGATION_LAB_ISSUE_ORDER.filter((issueCode) => values.includes(issueCode));
}