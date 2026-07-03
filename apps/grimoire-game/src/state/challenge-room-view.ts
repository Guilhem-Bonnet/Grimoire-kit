import type { HostReviewFindingSeverity, TaskStatus, WorkflowStepLogEntry } from '../contracts/events';

import { createAuditView, type AuditEntry } from './audit-view';
import { createCommunicationView } from './communication-view';
import type { GameState } from './game-state';
import { createKanbanView, type KanbanCard } from './kanban-view';
import { evaluateTaskVerificationGate, type TaskVerificationGate } from './verification-view';

export const CHALLENGE_PHASE_ORDER = ['presentation', 'questions', 'critiques', 'vote', 'iteration'] as const;

export type ChallengePhase = (typeof CHALLENGE_PHASE_ORDER)[number];
export type ChallengePhaseStatus = 'pending' | 'active' | 'blocked' | 'completed';
export type ChallengeSessionStatus = 'active' | 'blocked' | 'completed';
export type ChallengeVoteValue = 'approve' | 'revise' | 'reject';
export type ChallengeVerdictKind = 'approved' | 'iteration_required' | 'rejected' | 'inconclusive';
export type ChallengeObjectionStatus = 'open' | 'resolved';
export type ChallengeJournalEntryKind = ChallengePhase | 'corrective_action' | 'audit';

export interface ChallengeRoomViewOptions {
  traceId?: string;
  taskId?: string;
  includeCompleted?: boolean;
  includeVerificationSummary?: boolean;
  includeRuntimeContext?: boolean;
}

export interface ChallengeRoomFocus {
  sessionId: string | null;
  traceId: string | null;
  taskId: string | null;
}

export interface ChallengePhaseSnapshot {
  phase: ChallengePhase;
  status: ChallengePhaseStatus;
  startedAt: string | null;
  updatedAt: string | null;
  turnCount: number;
  sequenceIds: readonly number[];
  speakerAgentIds: readonly string[];
}

export interface ChallengeSpeechTurn {
  id: string;
  phase: ChallengePhase;
  sequenceId: number;
  timestamp: string;
  title: string;
  detail: string;
  sourceEventType: string;
  decisionId: string;
  speakerAgentId: string | null;
  speakerAgentName: string | null;
  speakerRole: string | null;
  roomId: string | null;
  traceId: string | null;
  taskId: string | null;
  linkedTraceIds: readonly string[];
  linkedTaskIds: readonly string[];
  relatedBubbleIds: readonly string[];
}

export interface ChallengeObjection {
  id: string;
  sequenceId: number;
  timestamp: string;
  title: string;
  detail: string;
  severity: HostReviewFindingSeverity;
  status: ChallengeObjectionStatus;
  speakerAgentId: string | null;
  speakerAgentName: string | null;
  traceId: string | null;
  taskId: string | null;
  linkedTraceIds: readonly string[];
  linkedTaskIds: readonly string[];
  resolvedAt: string | null;
}

export interface ChallengeVote {
  id: string;
  sequenceId: number;
  timestamp: string;
  voterAgentId: string | null;
  voterAgentName: string | null;
  value: ChallengeVoteValue;
  score: number | null;
  verdict: ChallengeVerdictKind | null;
  rationale: string;
  linkedTraceIds: readonly string[];
  linkedTaskIds: readonly string[];
  linkedObjectionIds: readonly string[];
}

export interface ChallengeVerdict {
  kind: ChallengeVerdictKind;
  label: string;
  blocking: boolean;
  reason: string;
  decidedAt: string | null;
  sourceSequenceId: number | null;
  approveCount: number;
  reviseCount: number;
  rejectCount: number;
  score: number | null;
}

export interface ChallengeCorrectiveAction {
  id: string;
  taskId: string;
  title: string;
  status: TaskStatus | null;
  syncedStatus: TaskStatus | null;
  priority: KanbanCard['priority'];
  kind: KanbanCard['kind'];
  blockedReason: string | null;
  blockerCount: number;
  autoCreated: boolean;
  visibleInBacklog: boolean;
  createdAt: string;
  sourceSequenceId: number;
  traceId: string | null;
  parentTaskId: string | null;
}

export interface ChallengeVerificationSummary {
  taskId: string;
  isReadyForDone: boolean;
  verificationRef: string | null;
  verdict: TaskVerificationGate['verificationChain']['verdict'];
  unmetRequirementCodes: readonly string[];
}

export interface ChallengeJournalEntry {
  id: string;
  kind: ChallengeJournalEntryKind;
  sequenceId: number;
  timestamp: string;
  title: string;
  detail: string;
  traceId: string | null;
  taskId: string | null;
  linkedTraceIds: readonly string[];
  linkedTaskIds: readonly string[];
}

export interface ChallengeSessionMetrics {
  turnCount: number;
  objectionCount: number;
  openObjectionCount: number;
  voteCount: number;
  correctiveActionCount: number;
}

export interface ChallengeSession {
  id: string;
  challengeId: string;
  title: string;
  status: ChallengeSessionStatus;
  currentPhase: ChallengePhase | null;
  blockedReason: string | null;
  traceId: string | null;
  taskId: string | null;
  taskTitle: string | null;
  startedAt: string;
  updatedAt: string;
  participantAgentIds: readonly string[];
  roomIds: readonly string[];
  linkedDecisionIds: readonly string[];
  phases: readonly ChallengePhaseSnapshot[];
  speechTurns: readonly ChallengeSpeechTurn[];
  objections: readonly ChallengeObjection[];
  votes: readonly ChallengeVote[];
  verdict: ChallengeVerdict;
  correctiveActions: readonly ChallengeCorrectiveAction[];
  verification: ChallengeVerificationSummary | null;
  journal: readonly ChallengeJournalEntry[];
  metrics: ChallengeSessionMetrics;
}

export interface ChallengeRoomMetrics {
  sessionCount: number;
  activeCount: number;
  blockedCount: number;
  completedCount: number;
  openObjectionCount: number;
  voteCount: number;
  correctiveActionCount: number;
}

export interface ChallengeRoomView {
  protocolVersion: string;
  lastSequenceId: number;
  focus: ChallengeRoomFocus;
  sessions: readonly ChallengeSession[];
  metrics: ChallengeRoomMetrics;
}

interface ChallengeStepRecord {
  baseKey: string;
  challengeId: string;
  phase: ChallengePhase;
  workflowStep: WorkflowStepLogEntry;
}

export function createChallengeRoomView(
  state: GameState,
  options: ChallengeRoomViewOptions = {}
): ChallengeRoomView {
  const normalizedOptions = normalizeChallengeRoomOptions(options);
  const auditEntries = normalizedOptions.includeRuntimeContext ? createAuditView(state).entries : [];
  const bubbleIdsBySequence = normalizedOptions.includeRuntimeContext
    ? indexBubbleIdsBySequence(createCommunicationView(state).bubbles)
    : {};
  const kanbanCardsByTaskId = normalizedOptions.includeRuntimeContext
    ? Object.fromEntries(createKanbanView(state).cards.map((card) => [card.taskId, card]))
    : {};

  const sessions = collectChallengeSessions(state.recentWorkflowSteps)
    .map((records, index) =>
      createChallengeSession(
        records,
        index,
        state,
        auditEntries,
        bubbleIdsBySequence,
        kanbanCardsByTaskId,
        normalizedOptions.includeVerificationSummary
      )
    )
    .filter((session) => matchesChallengeRoomOptions(session, normalizedOptions))
    .sort(compareChallengeSessions);
  const focusSession = sessions[0] ?? null;

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    focus: {
      sessionId: focusSession?.id ?? null,
      traceId: focusSession?.traceId ?? null,
      taskId: focusSession?.taskId ?? null
    },
    sessions,
    metrics: {
      sessionCount: sessions.length,
      activeCount: sessions.filter((session) => session.status === 'active').length,
      blockedCount: sessions.filter((session) => session.status === 'blocked').length,
      completedCount: sessions.filter((session) => session.status === 'completed').length,
      openObjectionCount: sessions.reduce((count, session) => count + session.metrics.openObjectionCount, 0),
      voteCount: sessions.reduce((count, session) => count + session.metrics.voteCount, 0),
      correctiveActionCount: sessions.reduce((count, session) => count + session.metrics.correctiveActionCount, 0)
    }
  };
}

function collectChallengeSessions(workflowSteps: readonly WorkflowStepLogEntry[]): ChallengeStepRecord[][] {
  const challengeRecords = workflowSteps
    .map((workflowStep) => createChallengeStepRecord(workflowStep))
    .filter((record): record is ChallengeStepRecord => record !== null)
    .sort((left, right) => left.workflowStep.sequenceId - right.workflowStep.sequenceId);
  const sessionsByBaseKey = new Map<string, ChallengeStepRecord[][]>();

  for (const record of challengeRecords) {
    const baseSessions = sessionsByBaseKey.get(record.baseKey) ?? [];
    const currentSession = baseSessions[baseSessions.length - 1] ?? null;

    if (
      currentSession === null ||
      shouldStartNewChallengeSession(currentSession[currentSession.length - 1] ?? null, record)
    ) {
      baseSessions.push([record]);
    } else {
      currentSession.push(record);
    }

    sessionsByBaseKey.set(record.baseKey, baseSessions);
  }

  return Array.from(sessionsByBaseKey.values()).flatMap((sessions) => sessions);
}

function createChallengeStepRecord(workflowStep: WorkflowStepLogEntry): ChallengeStepRecord | null {
  const metadata = toMetadataRecord(workflowStep.metadata);
  const phase = readChallengePhase(workflowStep, metadata);
  if (phase === null) {
    return null;
  }

  const challengeId =
    readMetadataStringByKeys(metadata, ['challengeId', 'challenge_id']) ??
    createChallengeIdentifier(workflowStep.traceId ?? null, workflowStep.taskId ?? null);
  const baseKey = challengeId === 'challenge:unscoped' ? createChallengeBaseKey(workflowStep, metadata) : challengeId;

  return {
    baseKey,
    challengeId,
    phase,
    workflowStep
  };
}

function shouldStartNewChallengeSession(
  previousRecord: ChallengeStepRecord | null,
  nextRecord: ChallengeStepRecord
): boolean {
  if (previousRecord === null) {
    return true;
  }

  if (previousRecord.challengeId !== nextRecord.challengeId) {
    return true;
  }

  const previousPhaseIndex = CHALLENGE_PHASE_ORDER.indexOf(previousRecord.phase);
  const nextPhaseIndex = CHALLENGE_PHASE_ORDER.indexOf(nextRecord.phase);
  return previousPhaseIndex >= CHALLENGE_PHASE_ORDER.indexOf('vote') && nextPhaseIndex === 0;
}

function createChallengeSession(
  records: readonly ChallengeStepRecord[],
  index: number,
  state: GameState,
  auditEntries: readonly AuditEntry[],
  bubbleIdsBySequence: Record<number, string[]>,
  kanbanCardsByTaskId: Record<string, KanbanCard>,
  includeVerificationSummary: boolean
): ChallengeSession {
  const firstRecord = records[0];
  const lastRecord = records[records.length - 1];
  const taskId = resolveChallengeTaskId(records);
  const traceId = resolveChallengeTraceId(records);
  const taskTitle = taskId === null ? null : (state.tasks[taskId]?.title ?? null);
  const speechTurns = records.map((record) => createChallengeSpeechTurn(record, state, bubbleIdsBySequence, traceId, taskId));
  const objections = createChallengeObjections(records, state, traceId, taskId);
  const votes = createChallengeVotes(records, state, traceId, taskId);
  const verdict = createChallengeVerdict(records, votes);
  const currentPhase = resolveCurrentChallengePhase(records, verdict);
  const correctiveActions = createChallengeCorrectiveActions(records, state, kanbanCardsByTaskId, traceId, taskId);
  const blockedReason = resolveChallengeBlockedReason(verdict, objections, correctiveActions);
  const status = resolveChallengeSessionStatus(currentPhase, verdict, blockedReason);
  const phases = createChallengePhaseSnapshots(records, speechTurns, currentPhase, status);
  const verification =
    !includeVerificationSummary || taskId === null
      ? null
      : toChallengeVerificationSummary(evaluateTaskVerificationGate(state, taskId));
  const sessionId = `${records[0]?.challengeId ?? 'challenge'}:${index}:${firstRecord?.workflowStep.sequenceId ?? 0}`;

  return {
    id: sessionId,
    challengeId: records[0]?.challengeId ?? `challenge-${index}`,
    title: taskTitle === null ? `Challenge ${records[0]?.challengeId ?? index}` : `Challenge: ${taskTitle}`,
    status,
    currentPhase,
    blockedReason,
    traceId,
    taskId,
    taskTitle,
    startedAt: firstRecord?.workflowStep.timestamp ?? new Date(0).toISOString(),
    updatedAt: lastRecord?.workflowStep.timestamp ?? new Date(0).toISOString(),
    participantAgentIds: uniqueStrings(speechTurns.map((turn) => turn.speakerAgentId)),
    roomIds: uniqueStrings(speechTurns.map((turn) => turn.roomId)),
    linkedDecisionIds: records.map((record) => `decision-${record.workflowStep.sequenceId}`),
    phases,
    speechTurns,
    objections,
    votes,
    verdict,
    correctiveActions,
    verification,
    journal: createChallengeJournal(records, speechTurns, auditEntries, correctiveActions, traceId, taskId),
    metrics: {
      turnCount: speechTurns.length,
      objectionCount: objections.length,
      openObjectionCount: objections.filter((objection) => objection.status === 'open').length,
      voteCount: votes.length,
      correctiveActionCount: correctiveActions.length
    }
  };
}

function createChallengeSpeechTurn(
  record: ChallengeStepRecord,
  state: GameState,
  bubbleIdsBySequence: Record<number, string[]>,
  traceId: string | null,
  taskId: string | null
): ChallengeSpeechTurn {
  const workflowStep = record.workflowStep;
  const metadata = toMetadataRecord(workflowStep.metadata);
  const agent = workflowStep.agentId === undefined ? undefined : state.agents[workflowStep.agentId];

  return {
    id: `challenge-turn:${workflowStep.sequenceId}`,
    phase: record.phase,
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    title: workflowStep.step,
    detail: workflowStep.detail,
    sourceEventType: workflowStep.sourceEventType,
    decisionId: `decision-${workflowStep.sequenceId}`,
    speakerAgentId: workflowStep.agentId ?? null,
    speakerAgentName: agent?.name ?? null,
    speakerRole:
      readMetadataStringByKeys(metadata, ['challengeRole', 'challenge_role', 'speakerRole', 'speaker_role']) ??
      (agent?.role ?? null),
    roomId: agent?.roomId ?? null,
    traceId,
    taskId,
    linkedTraceIds: resolveChallengeLinkedTraceIds(workflowStep, metadata, traceId),
    linkedTaskIds: resolveChallengeLinkedTaskIds(workflowStep, metadata, taskId),
    relatedBubbleIds: bubbleIdsBySequence[workflowStep.sequenceId] ?? []
  };
}

function createChallengeObjections(
  records: readonly ChallengeStepRecord[],
  state: GameState,
  traceId: string | null,
  taskId: string | null
): ChallengeObjection[] {
  const resolutionIndex = indexResolvedObjections(records);

  return records
    .filter((record) => isChallengeObjectionRecord(record))
    .map((record) => {
      const workflowStep = record.workflowStep;
      const metadata = toMetadataRecord(workflowStep.metadata);
      const agent = workflowStep.agentId === undefined ? undefined : state.agents[workflowStep.agentId];
      const objectionId =
        readMetadataStringByKeys(metadata, ['objectionId', 'objection_id']) ??
        `objection:${workflowStep.sequenceId}`;
      const resolution = resolutionIndex.get(objectionId) ?? null;
      const explicitStatus = normalizeChallengeObjectionStatus(
        readMetadataStringByKeys(metadata, ['objectionStatus', 'objection_status', 'status'])
      );

      return {
        id: objectionId,
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp,
        title:
          readMetadataStringByKeys(metadata, ['objectionTitle', 'objection_title', 'title']) ??
          workflowStep.step,
        detail:
          readMetadataStringByKeys(metadata, ['objection', 'objectionMessage', 'objection_message']) ??
          workflowStep.detail,
        severity:
          normalizeChallengeObjectionSeverity(
            readMetadataStringByKeys(metadata, ['objectionSeverity', 'objection_severity', 'severity'])
          ) ?? 'medium',
        status: resolution !== null ? 'resolved' : (explicitStatus ?? 'open'),
        speakerAgentId: workflowStep.agentId ?? null,
        speakerAgentName: agent?.name ?? null,
        traceId,
        taskId,
        linkedTraceIds: resolveChallengeLinkedTraceIds(workflowStep, metadata, traceId),
        linkedTaskIds: resolveChallengeLinkedTaskIds(workflowStep, metadata, taskId),
        resolvedAt: resolution
      } satisfies ChallengeObjection;
    })
    .sort(compareChallengeObjections);
}

function createChallengeVotes(
  records: readonly ChallengeStepRecord[],
  state: GameState,
  traceId: string | null,
  taskId: string | null
): ChallengeVote[] {
  return records
    .filter((record) => isChallengeVoteRecord(record))
    .map((record) => {
      const workflowStep = record.workflowStep;
      const metadata = toMetadataRecord(workflowStep.metadata);
      const agent = workflowStep.agentId === undefined ? undefined : state.agents[workflowStep.agentId];

      return {
        id: `challenge-vote:${workflowStep.sequenceId}`,
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp,
        voterAgentId: workflowStep.agentId ?? null,
        voterAgentName: agent?.name ?? null,
        value:
          normalizeChallengeVoteValue(
            readMetadataStringByKeys(metadata, ['vote', 'voteValue', 'vote_value', 'decision'])
          ) ?? 'approve',
        score: readMetadataNumberByKeys(metadata, ['score', 'voteScore', 'vote_score']),
        verdict: normalizeChallengeVerdict(
          readMetadataStringByKeys(metadata, ['challengeVerdict', 'challenge_verdict', 'verdict'])
        ),
        rationale:
          readMetadataStringByKeys(metadata, ['rationale', 'reason', 'comment']) ??
          workflowStep.detail,
        linkedTraceIds: resolveChallengeLinkedTraceIds(workflowStep, metadata, traceId),
        linkedTaskIds: resolveChallengeLinkedTaskIds(workflowStep, metadata, taskId),
        linkedObjectionIds: readMetadataStringListByKeys(metadata, [
          'linkedObjectionIds',
          'linked_objection_ids',
          'objectionIds',
          'objection_ids'
        ])
      } satisfies ChallengeVote;
    })
    .sort(compareChallengeVotes);
}

function createChallengeVerdict(
  records: readonly ChallengeStepRecord[],
  votes: readonly ChallengeVote[]
): ChallengeVerdict {
  const orderedRecords = [...records].sort((left, right) => right.workflowStep.sequenceId - left.workflowStep.sequenceId);

  for (const record of orderedRecords) {
    const metadata = toMetadataRecord(record.workflowStep.metadata);
    const explicitVerdict = normalizeChallengeVerdict(
      readMetadataStringByKeys(metadata, ['challengeVerdict', 'challenge_verdict', 'verdict'])
    );

    if (explicitVerdict !== null) {
      return {
        kind: explicitVerdict,
        label: toChallengeVerdictLabel(explicitVerdict),
        blocking: explicitVerdict === 'iteration_required' || explicitVerdict === 'rejected',
        reason: readMetadataStringByKeys(metadata, ['reason', 'rationale']) ?? record.workflowStep.detail,
        decidedAt: record.workflowStep.timestamp,
        sourceSequenceId: record.workflowStep.sequenceId,
        approveCount: votes.filter((vote) => vote.value === 'approve').length,
        reviseCount: votes.filter((vote) => vote.value === 'revise').length,
        rejectCount: votes.filter((vote) => vote.value === 'reject').length,
        score: votes.find((vote) => vote.score !== null)?.score ?? null
      };
    }
  }

  const approveCount = votes.filter((vote) => vote.value === 'approve').length;
  const reviseCount = votes.filter((vote) => vote.value === 'revise').length;
  const rejectCount = votes.filter((vote) => vote.value === 'reject').length;
  const inferredVerdict: ChallengeVerdictKind =
    votes.length === 0
      ? 'inconclusive'
      : rejectCount > 0
        ? 'rejected'
        : reviseCount > 0
          ? 'iteration_required'
          : 'approved';
  const latestVote = votes[votes.length - 1] ?? null;

  return {
    kind: inferredVerdict,
    label: toChallengeVerdictLabel(inferredVerdict),
    blocking: inferredVerdict === 'iteration_required' || inferredVerdict === 'rejected',
    reason: latestVote?.rationale ?? 'Challenge verdict is waiting for an explicit room decision.',
    decidedAt: latestVote?.timestamp ?? null,
    sourceSequenceId: latestVote?.sequenceId ?? null,
    approveCount,
    reviseCount,
    rejectCount,
    score: latestVote?.score ?? null
  };
}

function resolveCurrentChallengePhase(
  records: readonly ChallengeStepRecord[],
  verdict: ChallengeVerdict
): ChallengePhase | null {
  if (records.length === 0) {
    return null;
  }

  if (verdict.kind === 'iteration_required') {
    return 'iteration';
  }

  return records[records.length - 1]?.phase ?? null;
}

function createChallengeCorrectiveActions(
  records: readonly ChallengeStepRecord[],
  state: GameState,
  kanbanCardsByTaskId: Record<string, KanbanCard>,
  traceId: string | null,
  taskId: string | null
): ChallengeCorrectiveAction[] {
  const actions = new Map<string, ChallengeCorrectiveAction>();

  for (const record of records) {
    const metadata = toMetadataRecord(record.workflowStep.metadata);
    const correctiveTaskIds = readMetadataStringListByKeys(metadata, [
      'correctiveTaskIds',
      'corrective_task_ids',
      'correctiveTaskId',
      'corrective_task_id',
      'followUpTaskIds',
      'follow_up_task_ids',
      'followUpTaskId',
      'follow_up_task_id'
    ]);

    for (const correctiveTaskId of correctiveTaskIds) {
      const task = state.tasks[correctiveTaskId];
      const kanbanCard = kanbanCardsByTaskId[correctiveTaskId];

      actions.set(correctiveTaskId, {
        id: `challenge-corrective-action:${record.workflowStep.sequenceId}:${correctiveTaskId}`,
        taskId: correctiveTaskId,
        title:
          task?.title ??
          readMetadataStringByKeys(metadata, ['correctiveTitle', 'corrective_title']) ??
          correctiveTaskId,
        status: task?.status ?? null,
        syncedStatus: kanbanCard?.syncedStatus ?? task?.status ?? null,
        priority: task?.priority ?? null,
        kind: task?.kind ?? null,
        blockedReason: task?.blockedReason ?? null,
        blockerCount: kanbanCard?.blockers.length ?? 0,
        autoCreated: readMetadataBooleanByKeys(metadata, ['autoCreated', 'auto_created']) ?? true,
        visibleInBacklog: kanbanCard !== undefined,
        createdAt: record.workflowStep.timestamp,
        sourceSequenceId: record.workflowStep.sequenceId,
        traceId,
        parentTaskId: taskId
      });
    }
  }

  return Array.from(actions.values()).sort(compareChallengeCorrectiveActions);
}

function resolveChallengeBlockedReason(
  verdict: ChallengeVerdict,
  objections: readonly ChallengeObjection[],
  correctiveActions: readonly ChallengeCorrectiveAction[]
): string | null {
  if (verdict.kind === 'rejected') {
    return verdict.reason.length > 0 ? verdict.reason : 'Challenge has been rejected.';
  }

  if (verdict.kind !== 'iteration_required') {
    return null;
  }

  const openObjections = objections.filter((objection) => objection.status === 'open');
  const openCorrectiveAction = correctiveActions.find((action) => action.status !== 'done');
  if (openCorrectiveAction !== undefined) {
    return `Iteration required: corrective action ${openCorrectiveAction.title} is still ${openCorrectiveAction.status ?? 'pending'}.`;
  }

  if (openObjections.length > 0) {
    return `Iteration required: ${openObjections.length} objection(s) remain open.`;
  }

  return verdict.reason.length > 0 ? verdict.reason : 'Iteration is required before the challenge can close.';
}

function resolveChallengeSessionStatus(
  currentPhase: ChallengePhase | null,
  verdict: ChallengeVerdict,
  blockedReason: string | null
): ChallengeSessionStatus {
  if (blockedReason !== null) {
    return 'blocked';
  }

  if (currentPhase === 'iteration' && verdict.kind === 'approved') {
    return 'completed';
  }

  return 'active';
}

function createChallengePhaseSnapshots(
  records: readonly ChallengeStepRecord[],
  speechTurns: readonly ChallengeSpeechTurn[],
  currentPhase: ChallengePhase | null,
  sessionStatus: ChallengeSessionStatus
): ChallengePhaseSnapshot[] {
  const currentPhaseIndex = currentPhase === null ? -1 : CHALLENGE_PHASE_ORDER.indexOf(currentPhase);

  return CHALLENGE_PHASE_ORDER.map((phase, index) => {
    const phaseRecords = records.filter((record) => record.phase === phase);
    const phaseTurns = speechTurns.filter((turn) => turn.phase === phase);

    return {
      phase,
      status: resolveChallengePhaseStatus(phaseRecords.length > 0, index, currentPhaseIndex, sessionStatus),
      startedAt: phaseRecords[0]?.workflowStep.timestamp ?? null,
      updatedAt: phaseRecords[phaseRecords.length - 1]?.workflowStep.timestamp ?? null,
      turnCount: phaseTurns.length,
      sequenceIds: phaseRecords.map((record) => record.workflowStep.sequenceId),
      speakerAgentIds: uniqueStrings(phaseTurns.map((turn) => turn.speakerAgentId))
    } satisfies ChallengePhaseSnapshot;
  });
}

function resolveChallengePhaseStatus(
  hasRecords: boolean,
  phaseIndex: number,
  currentPhaseIndex: number,
  sessionStatus: ChallengeSessionStatus
): ChallengePhaseStatus {
  if (!hasRecords && phaseIndex !== currentPhaseIndex) {
    return 'pending';
  }

  if (sessionStatus === 'completed') {
    return hasRecords ? 'completed' : 'pending';
  }

  if (phaseIndex < currentPhaseIndex) {
    return hasRecords ? 'completed' : 'pending';
  }

  if (phaseIndex > currentPhaseIndex) {
    return 'pending';
  }

  if (sessionStatus === 'blocked') {
    return 'blocked';
  }

  return 'active';
}

function toChallengeVerificationSummary(gate: TaskVerificationGate | null): ChallengeVerificationSummary | null {
  if (gate === null) {
    return null;
  }

  return {
    taskId: gate.taskId,
    isReadyForDone: gate.isReadyForDone,
    verificationRef: gate.verificationChain.verificationRef,
    verdict: gate.verificationChain.verdict,
    unmetRequirementCodes: gate.unmetRequirementCodes
  };
}

function createChallengeJournal(
  records: readonly ChallengeStepRecord[],
  speechTurns: readonly ChallengeSpeechTurn[],
  auditEntries: readonly AuditEntry[],
  correctiveActions: readonly ChallengeCorrectiveAction[],
  traceId: string | null,
  taskId: string | null
): ChallengeJournalEntry[] {
  const firstSequenceId = records[0]?.workflowStep.sequenceId ?? -1;
  const lastSequenceId = records[records.length - 1]?.workflowStep.sequenceId ?? -1;
  const challengeSequenceIds = new Set(records.map((record) => record.workflowStep.sequenceId));
  const journalEntries: ChallengeJournalEntry[] = speechTurns.map((turn) => ({
    id: turn.id,
    kind: turn.phase,
    sequenceId: turn.sequenceId,
    timestamp: turn.timestamp,
    title: turn.title,
    detail: turn.detail,
    traceId: turn.traceId,
    taskId: turn.taskId,
    linkedTraceIds: turn.linkedTraceIds,
    linkedTaskIds: turn.linkedTaskIds
  }));

  for (const action of correctiveActions) {
    journalEntries.push({
      id: action.id,
      kind: 'corrective_action',
      sequenceId: action.sourceSequenceId,
      timestamp: action.createdAt,
      title: `Corrective action: ${action.title}`,
      detail: action.visibleInBacklog
        ? `Backlog card ${action.taskId} is visible as ${action.syncedStatus ?? 'unknown'}.`
        : `Corrective action ${action.taskId} is referenced but not yet visible in the kanban backlog.`,
      traceId: action.traceId,
      taskId: action.taskId,
      linkedTraceIds: uniqueStrings([action.traceId]),
      linkedTaskIds: uniqueStrings([action.parentTaskId, action.taskId])
    });
  }

  for (const auditEntry of auditEntries) {
    if (challengeSequenceIds.has(auditEntry.sequenceId)) {
      continue;
    }

    if (!belongsToChallengeJournal(auditEntry, traceId, taskId, firstSequenceId, lastSequenceId)) {
      continue;
    }

    journalEntries.push({
      id: `challenge-journal-audit:${auditEntry.id}`,
      kind: 'audit',
      sequenceId: auditEntry.sequenceId,
      timestamp: auditEntry.timestamp,
      title: auditEntry.title,
      detail: auditEntry.detail,
      traceId: auditEntry.traceId,
      taskId: auditEntry.taskId,
      linkedTraceIds: uniqueStrings([auditEntry.traceId]),
      linkedTaskIds: uniqueStrings([auditEntry.taskId])
    });
  }

  return journalEntries.sort(compareChallengeJournalEntries);
}

function belongsToChallengeJournal(
  auditEntry: AuditEntry,
  traceId: string | null,
  taskId: string | null,
  firstSequenceId: number,
  lastSequenceId: number
): boolean {
  if (auditEntry.sequenceId < firstSequenceId || auditEntry.sequenceId > lastSequenceId) {
    return false;
  }

  if (traceId !== null && auditEntry.traceId === traceId) {
    return true;
  }

  return taskId !== null && auditEntry.taskId === taskId;
}

function resolveChallengeTaskId(records: readonly ChallengeStepRecord[]): string | null {
  for (const record of records) {
    const workflowStepTaskId = record.workflowStep.taskId;
    if (workflowStepTaskId !== undefined) {
      return workflowStepTaskId;
    }

    const metadataTaskId = readMetadataStringByKeys(toMetadataRecord(record.workflowStep.metadata), ['taskId', 'task_id']);
    if (metadataTaskId !== null) {
      return metadataTaskId;
    }
  }

  return null;
}

function resolveChallengeTraceId(records: readonly ChallengeStepRecord[]): string | null {
  for (const record of records) {
    const workflowStepTraceId = record.workflowStep.traceId;
    if (workflowStepTraceId !== undefined) {
      return workflowStepTraceId;
    }

    const metadataTraceId = readMetadataStringByKeys(toMetadataRecord(record.workflowStep.metadata), ['traceId', 'trace_id']);
    if (metadataTraceId !== null) {
      return metadataTraceId;
    }
  }

  return null;
}

function indexResolvedObjections(records: readonly ChallengeStepRecord[]): Map<string, string> {
  const resolutions = new Map<string, string>();

  for (const record of records) {
    const metadata = toMetadataRecord(record.workflowStep.metadata);
    const resolvedObjectionIds = readMetadataStringListByKeys(metadata, [
      'resolvedObjectionIds',
      'resolved_objection_ids',
      'resolvedObjectionId',
      'resolved_objection_id'
    ]);

    for (const resolvedObjectionId of resolvedObjectionIds) {
      resolutions.set(resolvedObjectionId, record.workflowStep.timestamp);
    }
  }

  return resolutions;
}

function matchesChallengeRoomOptions(
  session: ChallengeSession,
  options: Required<ChallengeRoomViewOptions>
): boolean {
  if (!options.includeCompleted && session.status === 'completed') {
    return false;
  }

  if (options.traceId !== '' && session.traceId !== options.traceId) {
    return false;
  }

  if (options.taskId !== '' && session.taskId !== options.taskId) {
    return false;
  }

  return true;
}

function normalizeChallengeRoomOptions(options: ChallengeRoomViewOptions): Required<ChallengeRoomViewOptions> {
  return {
    traceId: options.traceId ?? '',
    taskId: options.taskId ?? '',
    includeCompleted: options.includeCompleted ?? true,
    includeVerificationSummary: options.includeVerificationSummary ?? true,
    includeRuntimeContext: options.includeRuntimeContext ?? true
  };
}

function readChallengePhase(
  workflowStep: WorkflowStepLogEntry,
  metadata: Record<string, unknown>
): ChallengePhase | null {
  const explicitPhase = normalizeChallengePhase(
    readMetadataStringByKeys(metadata, ['challengePhase', 'challenge_phase', 'phase'])
  );
  if (explicitPhase !== null) {
    return explicitPhase;
  }

  const sourceEventPhase = normalizeChallengePhase(workflowStep.sourceEventType);
  if (sourceEventPhase !== null && workflowStep.sourceEventType.toLowerCase().includes('challenge')) {
    return sourceEventPhase;
  }

  if (readMetadataBooleanByKeys(metadata, ['challenge']) === true) {
    return normalizeChallengePhase(`${workflowStep.step} ${workflowStep.detail}`);
  }

  if (workflowStep.sourceEventType.toLowerCase().includes('challenge')) {
    return normalizeChallengePhase(`${workflowStep.step} ${workflowStep.detail}`);
  }

  return null;
}

function normalizeChallengePhase(value: string | null): ChallengePhase | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase().replace(/[^a-z]+/g, ' ');
  if (normalized.includes('presentation') || normalized.includes('present')) {
    return 'presentation';
  }

  if (normalized.includes('question') || normalized.includes('qa')) {
    return 'questions';
  }

  if (normalized.includes('critique') || normalized.includes('objection') || normalized.includes('review')) {
    return 'critiques';
  }

  if (normalized.includes('vote') || normalized.includes('ballot')) {
    return 'vote';
  }

  if (normalized.includes('iteration') || normalized.includes('follow up') || normalized.includes('corrective')) {
    return 'iteration';
  }

  return null;
}

function normalizeChallengeVoteValue(value: string | null): ChallengeVoteValue | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'approve' || normalized === 'approved' || normalized === 'accept') {
    return 'approve';
  }

  if (normalized === 'revise' || normalized === 'iteration_required' || normalized === 'iterate') {
    return 'revise';
  }

  if (normalized === 'reject' || normalized === 'rejected' || normalized === 'deny') {
    return 'reject';
  }

  return null;
}

function normalizeChallengeVerdict(value: string | null): ChallengeVerdictKind | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'approved' || normalized === 'approve' || normalized === 'pass') {
    return 'approved';
  }

  if (normalized === 'iteration_required' || normalized === 'iteration required' || normalized === 'revise') {
    return 'iteration_required';
  }

  if (normalized === 'rejected' || normalized === 'reject' || normalized === 'fail') {
    return 'rejected';
  }

  if (normalized === 'inconclusive' || normalized === 'pending') {
    return 'inconclusive';
  }

  return null;
}

function normalizeChallengeObjectionStatus(value: string | null): ChallengeObjectionStatus | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'open') {
    return 'open';
  }

  if (normalized === 'resolved' || normalized === 'closed') {
    return 'resolved';
  }

  return null;
}

function normalizeChallengeObjectionSeverity(value: string | null): HostReviewFindingSeverity | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'critical' || normalized === 'high' || normalized === 'medium' || normalized === 'low' || normalized === 'info') {
    return normalized;
  }

  return null;
}

function isChallengeObjectionRecord(record: ChallengeStepRecord): boolean {
  const metadata = toMetadataRecord(record.workflowStep.metadata);
  return (
    record.phase === 'critiques' ||
    readMetadataStringByKeys(metadata, ['objectionId', 'objection_id']) !== null ||
    record.workflowStep.sourceEventType.toLowerCase().includes('objection')
  );
}

function isChallengeVoteRecord(record: ChallengeStepRecord): boolean {
  const metadata = toMetadataRecord(record.workflowStep.metadata);
  return (
    record.phase === 'vote' ||
    readMetadataStringByKeys(metadata, ['vote', 'voteValue', 'vote_value']) !== null ||
    record.workflowStep.sourceEventType.toLowerCase().includes('vote')
  );
}

function resolveChallengeLinkedTaskIds(
  workflowStep: WorkflowStepLogEntry,
  metadata: Record<string, unknown>,
  sessionTaskId: string | null
): string[] {
  return uniqueStrings([
    sessionTaskId,
    workflowStep.taskId ?? null,
    ...readMetadataStringListByKeys(metadata, ['linkedTaskIds', 'linked_task_ids', 'taskIds', 'task_ids', 'ticketIds', 'ticket_ids'])
  ]);
}

function resolveChallengeLinkedTraceIds(
  workflowStep: WorkflowStepLogEntry,
  metadata: Record<string, unknown>,
  sessionTraceId: string | null
): string[] {
  return uniqueStrings([
    sessionTraceId,
    workflowStep.traceId ?? null,
    ...readMetadataStringListByKeys(metadata, ['linkedTraceIds', 'linked_trace_ids', 'traceIds', 'trace_ids'])
  ]);
}

function indexBubbleIdsBySequence(
  bubbles: readonly ReturnType<typeof createCommunicationView>['bubbles'][number][]
): Record<number, string[]> {
  const bubbleIdsBySequence = new Map<number, string[]>();

  for (const bubble of bubbles) {
    const currentBubbleIds = bubbleIdsBySequence.get(bubble.sequenceId) ?? [];
    currentBubbleIds.push(bubble.id);
    bubbleIdsBySequence.set(bubble.sequenceId, currentBubbleIds);
  }

  return Object.fromEntries(
    Array.from(bubbleIdsBySequence.entries()).map(([sequenceId, bubbleIds]) => [sequenceId, bubbleIds.sort((left, right) => left.localeCompare(right))])
  );
}

function compareChallengeSessions(left: ChallengeSession, right: ChallengeSession): number {
  const leftStatusRank = challengeSessionStatusRank(left.status);
  const rightStatusRank = challengeSessionStatusRank(right.status);
  if (leftStatusRank !== rightStatusRank) {
    return leftStatusRank - rightStatusRank;
  }

  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.title.localeCompare(right.title);
}

function compareChallengeObjections(left: ChallengeObjection, right: ChallengeObjection): number {
  if (left.status !== right.status) {
    return left.status === 'open' ? -1 : 1;
  }

  const leftSeverityRank = challengeObjectionSeverityRank(left.severity);
  const rightSeverityRank = challengeObjectionSeverityRank(right.severity);
  if (leftSeverityRank !== rightSeverityRank) {
    return leftSeverityRank - rightSeverityRank;
  }

  return left.sequenceId - right.sequenceId;
}

function compareChallengeVotes(left: ChallengeVote, right: ChallengeVote): number {
  return left.sequenceId - right.sequenceId;
}

function compareChallengeCorrectiveActions(
  left: ChallengeCorrectiveAction,
  right: ChallengeCorrectiveAction
): number {
  if (left.visibleInBacklog !== right.visibleInBacklog) {
    return left.visibleInBacklog ? -1 : 1;
  }

  if (left.createdAt !== right.createdAt) {
    return left.createdAt.localeCompare(right.createdAt);
  }

  return left.taskId.localeCompare(right.taskId);
}

function compareChallengeJournalEntries(left: ChallengeJournalEntry, right: ChallengeJournalEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return left.sequenceId - right.sequenceId;
  }

  return left.id.localeCompare(right.id);
}

function challengeSessionStatusRank(status: ChallengeSessionStatus): number {
  switch (status) {
    case 'blocked':
      return 0;
    case 'active':
      return 1;
    case 'completed':
      return 2;
  }
}

function challengeObjectionSeverityRank(severity: HostReviewFindingSeverity): number {
  switch (severity) {
    case 'critical':
      return 0;
    case 'high':
      return 1;
    case 'medium':
      return 2;
    case 'low':
      return 3;
    case 'info':
      return 4;
  }
}

function toChallengeVerdictLabel(verdict: ChallengeVerdictKind): string {
  switch (verdict) {
    case 'approved':
      return 'Approved';
    case 'iteration_required':
      return 'Iteration Required';
    case 'rejected':
      return 'Rejected';
    case 'inconclusive':
      return 'Inconclusive';
  }
}

function createChallengeIdentifier(traceId: string | null, taskId: string | null): string {
  if (traceId === null && taskId === null) {
    return 'challenge:unscoped';
  }

  return `challenge:${traceId ?? '__trace__'}:${taskId ?? '__task__'}`;
}

function createChallengeBaseKey(workflowStep: WorkflowStepLogEntry, metadata: Record<string, unknown>): string {
  return createChallengeIdentifier(
    workflowStep.traceId ?? readMetadataStringByKeys(metadata, ['traceId', 'trace_id']),
    workflowStep.taskId ?? readMetadataStringByKeys(metadata, ['taskId', 'task_id'])
  );
}

function toMetadataRecord(metadata: Record<string, unknown> | undefined): Record<string, unknown> {
  return metadata === undefined ? {} : metadata;
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

function readMetadataNumberByKeys(metadata: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }

    if (typeof value === 'string') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }

  return null;
}

function readMetadataBooleanByKeys(metadata: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }

    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase();
      if (normalized === 'true') {
        return true;
      }

      if (normalized === 'false') {
        return false;
      }
    }
  }

  return null;
}

function normalizeStringList(value: unknown): string[] {
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized.length === 0 ? [] : [normalized];
  }

  if (!Array.isArray(value)) {
    return [];
  }

  return [...new Set(value.filter((entry): entry is string => typeof entry === 'string').map((entry) => entry.trim()).filter(Boolean))];
}

function uniqueStrings(values: readonly (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.length > 0))].sort((left, right) => left.localeCompare(right));
}