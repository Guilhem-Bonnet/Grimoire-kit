import type { GameState } from './game-state';
import { createMissionLedgerView } from './mission-ledger-view';
import { createSessionView, type SessionStatus, type SessionViewSession } from './session-view';

export type SessionLineageEdgeKind = 'handoff' | 'shared_task' | 'shared_evidence' | 'causal';
export type SessionLineageAlertCode = 'MISSING_LINEAGE' | 'MISSING_EVIDENCE' | 'MISSING_RUN_ID';
export type SessionLineageAlertSeverity = 'warning' | 'info';

export interface SessionLineageNode {
  traceId: string;
  sessionId: string;
  runId: string;
  correlationIds: readonly string[];
  title: string;
  status: SessionStatus;
  missionIds: readonly string[];
  agentIds: readonly string[];
  taskIds: readonly string[];
  evidenceRefs: readonly string[];
  decisionTitles: readonly string[];
  tags: readonly string[];
  startedAt: string;
  updatedAt: string;
  firstSequenceId: number;
  lastSequenceId: number;
  predecessorTraceIds: readonly string[];
  successorTraceIds: readonly string[];
}

export interface SessionLineageEdge {
  edgeId: string;
  fromTraceId: string;
  toTraceId: string;
  kind: SessionLineageEdgeKind;
  score: number;
  sharedMissionIds: readonly string[];
  sharedTaskIds: readonly string[];
  sharedAgentIds: readonly string[];
  sharedEvidenceRefs: readonly string[];
}

export interface SessionLineageAlert {
  traceId: string;
  code: SessionLineageAlertCode;
  severity: SessionLineageAlertSeverity;
  message: string;
}

export interface SessionLineageMetrics {
  sessionCount: number;
  closedSessionCount: number;
  edgeCount: number;
  orphanSessionCount: number;
  staleAlertCount: number;
}

export interface SessionLineageView {
  protocolVersion: string;
  lastSequenceId: number;
  nodes: readonly SessionLineageNode[];
  edges: readonly SessionLineageEdge[];
  alerts: readonly SessionLineageAlert[];
  metrics: SessionLineageMetrics;
}

export interface SeanceQuery {
  missionId?: string;
  runId?: string;
  traceId?: string;
  agentId?: string;
  taskId?: string;
  evidenceRef?: string;
  tag?: string;
  status?: SessionStatus;
  includeActive?: boolean;
}

export interface SeanceQueryResult {
  sessions: readonly SessionLineageNode[];
  totalCount: number;
  returnedCount: number;
}

export function createSessionLineageView(state: GameState): SessionLineageView {
  const sessionView = createSessionView(state);
  const missionLedger = createMissionLedgerView(state);
  const missionsById = Object.fromEntries(missionLedger.missions.map((mission) => [mission.missionId, mission]));
  const evidenceByTraceId = new Map<string, string[]>();

  for (const record of missionLedger.evidenceRecords) {
    if (record.traceId === null) {
      continue;
    }

    const currentRefs = evidenceByTraceId.get(record.traceId) ?? [];
    currentRefs.push(record.evidenceRef);
    evidenceByTraceId.set(record.traceId, currentRefs);
  }

  const draftNodes = sessionView.sessions.map((session) => createLineageNode(session, missionsById, evidenceByTraceId));
  const edges = createEdges(draftNodes);
  const predecessorsByTraceId = new Map<string, string[]>();
  const successorsByTraceId = new Map<string, string[]>();

  for (const edge of edges) {
    const predecessors = predecessorsByTraceId.get(edge.toTraceId) ?? [];
    predecessors.push(edge.fromTraceId);
    predecessorsByTraceId.set(edge.toTraceId, predecessors);

    const successors = successorsByTraceId.get(edge.fromTraceId) ?? [];
    successors.push(edge.toTraceId);
    successorsByTraceId.set(edge.fromTraceId, successors);
  }

  const nodes = draftNodes
    .map((node) => ({
      ...node,
      predecessorTraceIds: uniqueStrings(predecessorsByTraceId.get(node.traceId) ?? []),
      successorTraceIds: uniqueStrings(successorsByTraceId.get(node.traceId) ?? [])
    }))
    .sort(compareNodes);
  const alerts = createAlerts(nodes);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    nodes,
    edges,
    alerts,
    metrics: {
      sessionCount: nodes.length,
      closedSessionCount: nodes.filter((node) => node.status === 'completed').length,
      edgeCount: edges.length,
      orphanSessionCount: nodes.filter(
        (node) => node.predecessorTraceIds.length === 0 && node.successorTraceIds.length === 0
      ).length,
      staleAlertCount: alerts.length
    }
  };
}

export function querySeanceSessions(
  lineage: SessionLineageView,
  query: SeanceQuery = {}
): SeanceQueryResult {
  const sessions = lineage.nodes
    .filter((node) => {
      if (query.includeActive !== true && query.status === undefined && node.status !== 'completed') {
        return false;
      }

      if (query.status !== undefined && node.status !== query.status) {
        return false;
      }

      if (query.missionId !== undefined && !node.missionIds.includes(query.missionId)) {
        return false;
      }

      if (query.runId !== undefined && node.runId !== query.runId) {
        return false;
      }

      if (query.traceId !== undefined && node.traceId !== query.traceId) {
        return false;
      }

      if (query.agentId !== undefined && !node.agentIds.includes(query.agentId)) {
        return false;
      }

      if (query.taskId !== undefined && !node.taskIds.includes(query.taskId)) {
        return false;
      }

      if (query.evidenceRef !== undefined && !node.evidenceRefs.includes(query.evidenceRef)) {
        return false;
      }

      if (query.tag !== undefined && !node.tags.includes(query.tag)) {
        return false;
      }

      return true;
    })
    .sort(compareNodes);

  return {
    sessions,
    totalCount: lineage.nodes.length,
    returnedCount: sessions.length
  };
}

function createLineageNode(
  session: SessionViewSession,
  missionsById: Record<string, ReturnType<typeof createMissionLedgerView>['missions'][number]>,
  evidenceByTraceId: Map<string, string[]>
): SessionLineageNode {
  const missionIds = session.summary.taskIds
    .map((taskId) => `mission:task:${taskId}`)
    .filter((missionId) => missionsById[missionId] !== undefined);
  const correlationIds = collectCorrelationIds(session);
  const runId = deriveRunId(session, correlationIds);
  const evidenceRefs = uniqueStrings([
    ...collectEvidenceRefs(session),
    ...(evidenceByTraceId.get(session.summary.traceId) ?? [])
  ]).filter(isActionableEvidenceRef);
  const tags = uniqueStrings([
    ...missionIds.flatMap((missionId) => missionsById[missionId]?.labels ?? []),
    ...collectMetadataTags(session),
    session.summary.status
  ]);

  return {
    traceId: session.summary.traceId,
    sessionId: `session:${session.summary.traceId}`,
    runId,
    correlationIds,
    title: deriveNodeTitle(session, missionIds, missionsById),
    status: session.summary.status,
    missionIds,
    agentIds: session.summary.agentIds,
    taskIds: session.summary.taskIds,
    evidenceRefs,
    decisionTitles: uniqueStrings([
      ...session.decisionCards.map((card) => card.title),
      ...session.entries.filter((entry) => entry.sourceEventType === 'decision').map((entry) => entry.title)
    ]),
    tags,
    startedAt: session.summary.startedAt,
    updatedAt: session.summary.updatedAt,
    firstSequenceId: session.summary.firstSequenceId,
    lastSequenceId: session.summary.lastSequenceId,
    predecessorTraceIds: [],
    successorTraceIds: []
  };
}

function deriveNodeTitle(
  session: SessionViewSession,
  missionIds: readonly string[],
  missionsById: Record<string, ReturnType<typeof createMissionLedgerView>['missions'][number]>
): string {
  const firstMissionId = missionIds[0];

  if (
    firstMissionId !== undefined &&
    missionIds.length === 1 &&
    (session.summary.title === session.summary.traceId || /^Decision recorded$/u.test(session.summary.title))
  ) {
    return missionsById[firstMissionId]?.title ?? session.summary.title;
  }

  return session.summary.title;
}

function createEdges(nodes: readonly SessionLineageNode[]): SessionLineageEdge[] {
  const edges: SessionLineageEdge[] = [];
  const orderedNodes = [...nodes].sort((left, right) => left.firstSequenceId - right.firstSequenceId);

  for (let leftIndex = 0; leftIndex < orderedNodes.length; leftIndex += 1) {
    const left = orderedNodes[leftIndex];
    if (left === undefined) {
      continue;
    }

    for (let rightIndex = leftIndex + 1; rightIndex < orderedNodes.length; rightIndex += 1) {
      const right = orderedNodes[rightIndex];
      if (right === undefined || left.lastSequenceId > right.lastSequenceId) {
        continue;
      }

      const sharedMissionIds = intersect(left.missionIds, right.missionIds);
      const sharedTaskIds = intersect(left.taskIds, right.taskIds);
      const sharedAgentIds = intersect(left.agentIds, right.agentIds);
      const sharedEvidenceRefs = intersect(left.evidenceRefs, right.evidenceRefs);
      const sameRun = left.runId === right.runId && left.runId !== left.traceId;
      const score =
        sharedMissionIds.length * 2 +
        sharedTaskIds.length * 3 +
        sharedEvidenceRefs.length * 2 +
        (sameRun ? 2 : 0);

      if (score === 0) {
        continue;
      }

      edges.push({
        edgeId: `lineage:${left.traceId}:${right.traceId}`,
        fromTraceId: left.traceId,
        toTraceId: right.traceId,
        kind: determineEdgeKind(sharedTaskIds, sharedEvidenceRefs, sameRun),
        score,
        sharedMissionIds,
        sharedTaskIds,
        sharedAgentIds,
        sharedEvidenceRefs
      });
    }
  }

  return edges.sort(compareEdges);
}

function createAlerts(nodes: readonly SessionLineageNode[]): SessionLineageAlert[] {
  const alerts: SessionLineageAlert[] = [];

  for (const node of nodes) {
    const hasLineage = node.predecessorTraceIds.length > 0 || node.successorTraceIds.length > 0;
    if (!hasLineage && node.decisionTitles.length > 0) {
      alerts.push({
        traceId: node.traceId,
        code: 'MISSING_LINEAGE',
        severity: 'warning',
        message: `Session ${node.traceId} has decisions but no predecessor or successor lineage.`
      });
    }

    if (node.decisionTitles.length > 0 && node.evidenceRefs.length === 0) {
      alerts.push({
        traceId: node.traceId,
        code: 'MISSING_EVIDENCE',
        severity: 'warning',
        message: `Session ${node.traceId} has decisions without attached evidence refs.`
      });
    }

    if (node.runId === node.traceId && node.correlationIds.length === 0) {
      alerts.push({
        traceId: node.traceId,
        code: 'MISSING_RUN_ID',
        severity: 'info',
        message: `Session ${node.traceId} did not expose a stable run or correlation identifier.`
      });
    }
  }

  return alerts.sort((left, right) => left.traceId.localeCompare(right.traceId));
}

function deriveRunId(session: SessionViewSession, correlationIds: readonly string[]): string {
  for (const entry of session.entries) {
    const runId = readMetadataString(entry.metadata, ['runId', 'run_id']);
    if (runId !== null) {
      return runId;
    }
  }

  return correlationIds[0] ?? session.summary.traceId;
}

function collectCorrelationIds(session: SessionViewSession): string[] {
  return uniqueStrings(
    session.entries.flatMap((entry) => [
      readMetadataString(entry.metadata, ['requestId', 'request_id']),
      readMetadataString(entry.metadata, ['correlationId', 'correlation_id'])
    ]).filter((value): value is string => value !== null)
  );
}

function collectEvidenceRefs(session: SessionViewSession): string[] {
  return uniqueStrings(
    session.entries.flatMap((entry) => readMetadataStringList(entry.metadata, ['evidenceRefs', 'evidence_refs']))
  );
}

function collectMetadataTags(session: SessionViewSession): string[] {
  return uniqueStrings(
    session.entries.flatMap((entry) => [
      readMetadataString(entry.metadata, ['intent']),
      readMetadataString(entry.metadata, ['topic']),
      ...readMetadataStringList(entry.metadata, ['tags'])
    ]).filter((value): value is string => value !== null)
  );
}

function readMetadataString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
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

function readMetadataStringList(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) {
      continue;
    }

    const normalized = value
      .filter((entry): entry is string => typeof entry === 'string')
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);

    if (normalized.length > 0) {
      return [...new Set(normalized)];
    }
  }

  return [];
}

function determineEdgeKind(
  sharedTaskIds: readonly string[],
  sharedEvidenceRefs: readonly string[],
  sameRun: boolean
): SessionLineageEdgeKind {
  if (sharedTaskIds.length > 0 && sharedEvidenceRefs.length > 0) {
    return 'handoff';
  }

  if (sharedTaskIds.length > 0) {
    return 'shared_task';
  }

  if (sharedEvidenceRefs.length > 0) {
    return 'shared_evidence';
  }

  return 'causal';
}

function intersect(left: readonly string[], right: readonly string[]): string[] {
  const rightSet = new Set(right);
  return uniqueStrings(left.filter((value) => rightSet.has(value)));
}

function isActionableEvidenceRef(evidenceRef: string): boolean {
  return !evidenceRef.startsWith('decision-card:') && !evidenceRef.startsWith('tool-call:');
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}

function compareNodes(left: SessionLineageNode, right: SessionLineageNode): number {
  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.traceId.localeCompare(right.traceId);
}

function compareEdges(left: SessionLineageEdge, right: SessionLineageEdge): number {
  if (left.score !== right.score) {
    return right.score - left.score;
  }

  if (left.fromTraceId !== right.fromTraceId) {
    return left.fromTraceId.localeCompare(right.fromTraceId);
  }

  return left.toTraceId.localeCompare(right.toTraceId);
}