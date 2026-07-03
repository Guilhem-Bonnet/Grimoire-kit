import type { AgentPresence } from '../contracts/events';

import { createAuditView, type AuditEntry } from './audit-view';
import type { GameState } from './game-state';
import { createSessionView } from './session-view';

export const COMMUNICATION_BUBBLE_KIND_ORDER = ['message', 'handoff', 'broadcast'] as const;
export const COMMUNICATION_CRITICALITY_ORDER = ['critical', 'warning', 'info'] as const;

export type CommunicationBubbleKind = (typeof COMMUNICATION_BUBBLE_KIND_ORDER)[number];
export type CommunicationCriticality = (typeof COMMUNICATION_CRITICALITY_ORDER)[number];
export type CommunicationScope = 'intra_room' | 'inter_room' | 'multi_room';

export interface CommunicationFilter {
  agentId?: string;
  teamId?: string;
  traceId?: string;
  kinds?: readonly CommunicationBubbleKind[];
  criticalities?: readonly CommunicationCriticality[];
  query?: string;
}

export interface CommunicationEndpoint {
  agentId: string;
  agentName: string;
  roomId: string;
  teamId: string;
}

export interface CommunicationBubble {
  id: string;
  kind: CommunicationBubbleKind;
  messageType: string;
  messageId: string;
  correlationId: string | null;
  traceId: string | null;
  taskId: string | null;
  sequenceId: number;
  timestamp: string;
  criticality: CommunicationCriticality;
  scope: CommunicationScope;
  title: string;
  detail: string;
  sourceEventType: string;
  source: CommunicationEndpoint;
  targets: readonly CommunicationEndpoint[];
  relatedAuditEntryIds: readonly string[];
}

export interface CommunicationLink {
  id: string;
  bubbleId: string;
  kind: CommunicationBubbleKind;
  criticality: CommunicationCriticality;
  traceId: string | null;
  taskId: string | null;
  sourceAgentId: string;
  targetAgentId: string;
  sourceRoomId: string;
  targetRoomId: string;
  scope: CommunicationScope;
}

export interface CommunicationThread {
  id: string;
  traceId: string | null;
  title: string;
  startedAt: string;
  updatedAt: string;
  participantAgentIds: readonly string[];
  roomIds: readonly string[];
  teamIds: readonly string[];
  bubbleIds: readonly string[];
  messageCount: number;
  handoffCount: number;
  broadcastCount: number;
  criticalCount: number;
}

export interface CommunicationMetrics {
  totalBubbleCount: number;
  filteredBubbleCount: number;
  messageCount: number;
  handoffCount: number;
  broadcastCount: number;
  interRoomCount: number;
  criticalCount: number;
  threadCount: number;
}

export interface CommunicationView {
  protocolVersion: string;
  lastSequenceId: number;
  hasActiveFilters: boolean;
  filter: CommunicationFilter;
  bubbles: readonly CommunicationBubble[];
  links: readonly CommunicationLink[];
  timeline: readonly CommunicationBubble[];
  threads: readonly CommunicationThread[];
  metrics: CommunicationMetrics;
}

export function createCommunicationView(
  state: GameState,
  filter: CommunicationFilter = {}
): CommunicationView {
  const normalizedFilter = normalizeCommunicationFilter(filter);
  const auditView = createAuditView(state);
  const sessionView = createSessionView(state);
  const communicationBubbles: CommunicationBubble[] = [];
  const consumedSequenceIds = new Set<number>();

  for (const auditEntry of [...auditView.entries].sort(compareAuditEntriesAscending)) {
    if (auditEntry.kind !== 'tool_call' || auditEntry.sourceEventType !== 'graph_update') {
      continue;
    }

    const graphUpdateBubble = createGraphUpdateBubble(state, auditEntry);
    if (graphUpdateBubble === null) {
      continue;
    }

    communicationBubbles.push(graphUpdateBubble);
    consumedSequenceIds.add(auditEntry.sequenceId);
  }

  for (const auditEntry of [...auditView.entries].sort(compareAuditEntriesAscending)) {
    if (auditEntry.kind !== 'task_handoff') {
      continue;
    }

    const handoffBubble = createHandoffBubble(state, auditEntry);
    if (handoffBubble === null) {
      continue;
    }

    communicationBubbles.push(handoffBubble);
    consumedSequenceIds.add(auditEntry.sequenceId);
  }

  for (const session of sessionView.sessions) {
    const participantEndpoints = session.summary.agentIds
      .map((agentId) => toCommunicationEndpoint(state.agents[agentId]))
      .filter((endpoint): endpoint is CommunicationEndpoint => endpoint !== null);

    for (const auditEntry of [...session.entries].sort(compareAuditEntriesAscending)) {
      if (auditEntry.kind !== 'workflow_step' || consumedSequenceIds.has(auditEntry.sequenceId)) {
        continue;
      }

      const source = auditEntry.agentId === null ? null : toCommunicationEndpoint(state.agents[auditEntry.agentId]);
      if (source === null) {
        continue;
      }

      const targets = participantEndpoints.filter((endpoint) => endpoint.agentId !== source.agentId);
      if (targets.length === 0) {
        continue;
      }

      communicationBubbles.push(
        createSessionBubble(auditEntry, source, targets, targets.length === 1 ? 'message' : 'broadcast')
      );
    }
  }

  const filteredBubbles = communicationBubbles
    .filter((bubble) => matchesCommunicationFilter(bubble, normalizedFilter))
    .sort(compareBubblesDescending);
  const timeline = [...filteredBubbles].sort(compareBubblesAscending);
  const links = createCommunicationLinks(filteredBubbles);
  const threads = createCommunicationThreads(filteredBubbles);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    hasActiveFilters: isCommunicationFilterActive(normalizedFilter),
    filter: normalizedFilter,
    bubbles: filteredBubbles,
    links,
    timeline,
    threads,
    metrics: {
      totalBubbleCount: communicationBubbles.length,
      filteredBubbleCount: filteredBubbles.length,
      messageCount: filteredBubbles.filter((bubble) => bubble.kind === 'message').length,
      handoffCount: filteredBubbles.filter((bubble) => bubble.kind === 'handoff').length,
      broadcastCount: filteredBubbles.filter((bubble) => bubble.kind === 'broadcast').length,
      interRoomCount: filteredBubbles.filter((bubble) => bubble.scope !== 'intra_room').length,
      criticalCount: filteredBubbles.filter((bubble) => bubble.criticality === 'critical').length,
      threadCount: threads.length
    }
  };
}

function createGraphUpdateBubble(state: GameState, auditEntry: AuditEntry): CommunicationBubble | null {
  const rawEdge = readJsonString(auditEntry.metadata.edge);
  if (rawEdge === null) {
    return null;
  }

  const parsedEdge = parseGraphUpdateEdge(rawEdge);
  if (parsedEdge === null) {
    return null;
  }

  const sourceAgentId = resolveAgentReference(parsedEdge.fromReference, state.agents);
  const targetAgentId = resolveAgentReference(parsedEdge.toReference, state.agents);
  if (sourceAgentId === null || targetAgentId === null) {
    return null;
  }

  const source = toCommunicationEndpoint(state.agents[sourceAgentId]);
  const target = toCommunicationEndpoint(state.agents[targetAgentId]);
  if (source === null || target === null) {
    return null;
  }

  return {
    id: `communication:${auditEntry.id}`,
    kind: 'message',
    messageType: 'graph.update',
    messageId: `graph.update:${auditEntry.sequenceId}`,
    correlationId: readJsonString(auditEntry.metadata.correlationId) ?? toTraceCorrelationId(auditEntry.traceId),
    traceId: auditEntry.traceId,
    taskId: auditEntry.taskId,
    sequenceId: auditEntry.sequenceId,
    timestamp: auditEntry.timestamp,
    criticality: resolveCommunicationCriticality(auditEntry),
    scope: resolveCommunicationScope(source, [target]),
    title: auditEntry.title,
    detail: auditEntry.detail,
    sourceEventType: auditEntry.sourceEventType,
    source,
    targets: [target],
    relatedAuditEntryIds: [auditEntry.id]
  };
}

function createHandoffBubble(state: GameState, auditEntry: AuditEntry): CommunicationBubble | null {
  const source = auditEntry.agentId === null ? null : toCommunicationEndpoint(state.agents[auditEntry.agentId]);
  const targetAgentId = readJsonString(auditEntry.metadata.toAgentId);
  const target = targetAgentId === null ? null : toCommunicationEndpoint(state.agents[targetAgentId]);
  if (source === null || target === null) {
    return null;
  }

  return {
    id: `communication:${auditEntry.id}`,
    kind: 'handoff',
    messageType: 'task.handoff',
    messageId: `task.handoff:${auditEntry.sequenceId}`,
    correlationId: readJsonString(auditEntry.metadata.correlationId) ?? toTraceCorrelationId(auditEntry.traceId),
    traceId: auditEntry.traceId,
    taskId: auditEntry.taskId,
    sequenceId: auditEntry.sequenceId,
    timestamp: auditEntry.timestamp,
    criticality: resolveCommunicationCriticality(auditEntry),
    scope: resolveCommunicationScope(source, [target]),
    title: auditEntry.title,
    detail: auditEntry.detail,
    sourceEventType: auditEntry.sourceEventType,
    source,
    targets: [target],
    relatedAuditEntryIds: [auditEntry.id]
  };
}

function createSessionBubble(
  auditEntry: AuditEntry,
  source: CommunicationEndpoint,
  targets: readonly CommunicationEndpoint[],
  kind: CommunicationBubbleKind
): CommunicationBubble {
  return {
    id: `communication:${auditEntry.id}`,
    kind,
    messageType: 'workflow.step',
    messageId: `workflow.step:${auditEntry.sequenceId}`,
    correlationId: readJsonString(auditEntry.metadata.correlationId) ?? toTraceCorrelationId(auditEntry.traceId),
    traceId: auditEntry.traceId,
    taskId: auditEntry.taskId,
    sequenceId: auditEntry.sequenceId,
    timestamp: auditEntry.timestamp,
    criticality: resolveCommunicationCriticality(auditEntry),
    scope: resolveCommunicationScope(source, targets),
    title: auditEntry.title,
    detail: auditEntry.detail,
    sourceEventType: auditEntry.sourceEventType,
    source,
    targets,
    relatedAuditEntryIds: [auditEntry.id]
  };
}

function createCommunicationLinks(bubbles: readonly CommunicationBubble[]): CommunicationLink[] {
  return bubbles
    .flatMap((bubble) =>
      bubble.targets.map((target) => ({
        id: `${bubble.id}:${target.agentId}`,
        bubbleId: bubble.id,
        kind: bubble.kind,
        criticality: bubble.criticality,
        traceId: bubble.traceId,
        taskId: bubble.taskId,
        sourceAgentId: bubble.source.agentId,
        targetAgentId: target.agentId,
        sourceRoomId: bubble.source.roomId,
        targetRoomId: target.roomId,
        scope: bubble.scope
      }))
    )
    .sort(compareLinks);
}

function createCommunicationThreads(bubbles: readonly CommunicationBubble[]): CommunicationThread[] {
  const bubblesByTraceId = new Map<string, CommunicationBubble[]>();

  for (const bubble of bubbles) {
    const threadKey = bubble.traceId ?? bubble.id;
    const threadBubbles = bubblesByTraceId.get(threadKey) ?? [];
    threadBubbles.push(bubble);
    bubblesByTraceId.set(threadKey, threadBubbles);
  }

  return Array.from(bubblesByTraceId.entries())
    .map(([threadKey, threadBubbles]) => {
      const timeline = [...threadBubbles].sort(compareBubblesAscending);
      const firstBubble = timeline[0];
      const lastBubble = timeline[timeline.length - 1];
      const participantAgentIds = uniqueStrings(
        timeline.flatMap((bubble) => [bubble.source.agentId, ...bubble.targets.map((target) => target.agentId)])
      );
      const roomIds = uniqueStrings(
        timeline.flatMap((bubble) => [bubble.source.roomId, ...bubble.targets.map((target) => target.roomId)])
      );
      const teamIds = uniqueStrings(
        timeline.flatMap((bubble) => [bubble.source.teamId, ...bubble.targets.map((target) => target.teamId)])
      );

      return {
        id: `communication-thread:${threadKey}`,
        traceId: firstBubble?.traceId ?? null,
        title: firstBubble?.traceId ?? firstBubble?.title ?? threadKey,
        startedAt: firstBubble?.timestamp ?? new Date(0).toISOString(),
        updatedAt: lastBubble?.timestamp ?? new Date(0).toISOString(),
        participantAgentIds,
        roomIds,
        teamIds,
        bubbleIds: timeline.map((bubble) => bubble.id),
        messageCount: timeline.filter((bubble) => bubble.kind === 'message').length,
        handoffCount: timeline.filter((bubble) => bubble.kind === 'handoff').length,
        broadcastCount: timeline.filter((bubble) => bubble.kind === 'broadcast').length,
        criticalCount: timeline.filter((bubble) => bubble.criticality === 'critical').length
      } satisfies CommunicationThread;
    })
    .sort(compareThreads);
}

function toCommunicationEndpoint(agent: AgentPresence | undefined): CommunicationEndpoint | null {
  if (agent === undefined) {
    return null;
  }

  return {
    agentId: agent.id,
    agentName: agent.name,
    roomId: agent.roomId,
    teamId: agent.roomId
  };
}

function normalizeCommunicationFilter(filter: CommunicationFilter): CommunicationFilter {
  return {
    ...(filter.agentId === undefined || filter.agentId.trim().length === 0 ? {} : { agentId: filter.agentId.trim() }),
    ...(filter.teamId === undefined || filter.teamId.trim().length === 0 ? {} : { teamId: filter.teamId.trim() }),
    ...(filter.traceId === undefined || filter.traceId.trim().length === 0 ? {} : { traceId: filter.traceId.trim() }),
    ...(filter.kinds === undefined || filter.kinds.length === 0 ? {} : { kinds: [...new Set(filter.kinds)] }),
    ...(filter.criticalities === undefined || filter.criticalities.length === 0
      ? {}
      : { criticalities: [...new Set(filter.criticalities)] }),
    ...(filter.query === undefined || filter.query.trim().length === 0 ? {} : { query: filter.query.trim() })
  };
}

function isCommunicationFilterActive(filter: CommunicationFilter): boolean {
  return Object.keys(filter).length > 0;
}

function matchesCommunicationFilter(bubble: CommunicationBubble, filter: CommunicationFilter): boolean {
  if (
    filter.agentId !== undefined &&
    bubble.source.agentId !== filter.agentId &&
    !bubble.targets.some((target) => target.agentId === filter.agentId)
  ) {
    return false;
  }

  if (
    filter.teamId !== undefined &&
    bubble.source.teamId !== filter.teamId &&
    !bubble.targets.some((target) => target.teamId === filter.teamId)
  ) {
    return false;
  }

  if (filter.traceId !== undefined && bubble.traceId !== filter.traceId) {
    return false;
  }

  if (filter.kinds !== undefined && !filter.kinds.includes(bubble.kind)) {
    return false;
  }

  if (filter.criticalities !== undefined && !filter.criticalities.includes(bubble.criticality)) {
    return false;
  }

  if (filter.query !== undefined && !buildCommunicationSearchText(bubble).includes(filter.query.toLowerCase())) {
    return false;
  }

  return true;
}

function buildCommunicationSearchText(bubble: CommunicationBubble): string {
  return [
    bubble.id,
    bubble.kind,
    bubble.messageType,
    bubble.messageId,
    bubble.correlationId,
    bubble.traceId,
    bubble.taskId,
    bubble.title,
    bubble.detail,
    bubble.sourceEventType,
    bubble.source.agentId,
    bubble.source.agentName,
    bubble.source.roomId,
    bubble.source.teamId,
    ...bubble.targets.flatMap((target) => [target.agentId, target.agentName, target.roomId, target.teamId])
  ]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .join(' ')
    .toLowerCase();
}

function resolveCommunicationCriticality(auditEntry: AuditEntry): CommunicationCriticality {
  if (auditEntry.level === 'error') {
    return 'critical';
  }

  if (auditEntry.level === 'warning') {
    return 'warning';
  }

  if (auditEntry.sourceEventType === 'verification_gate') {
    const verdict = readJsonString(auditEntry.metadata.verdict);
    if (verdict === 'FAIL') {
      return 'critical';
    }

    const unmetControls = readJsonStringArray(auditEntry.metadata.unmetControls);
    if (unmetControls.length > 0) {
      return 'warning';
    }
  }

  if (auditEntry.sourceEventType === 'security_finding') {
    const severity = readJsonString(auditEntry.metadata.severity);
    if (severity === 'critical' || severity === 'high') {
      return 'critical';
    }

    if (severity === 'medium') {
      return 'warning';
    }
  }

  return 'info';
}

function resolveCommunicationScope(
  source: CommunicationEndpoint,
  targets: readonly CommunicationEndpoint[]
): CommunicationScope {
  const roomIds = uniqueStrings([source.roomId, ...targets.map((target) => target.roomId)]);

  if (roomIds.length <= 1) {
    return 'intra_room';
  }

  return targets.length > 1 ? 'multi_room' : 'inter_room';
}

function compareAuditEntriesAscending(left: AuditEntry, right: AuditEntry): number {
  if (left.sequenceId !== right.sequenceId) {
    return left.sequenceId - right.sequenceId;
  }

  return left.id.localeCompare(right.id);
}

function compareBubblesAscending(left: CommunicationBubble, right: CommunicationBubble): number {
  if (left.sequenceId !== right.sequenceId) {
    return left.sequenceId - right.sequenceId;
  }

  return left.id.localeCompare(right.id);
}

function compareBubblesDescending(left: CommunicationBubble, right: CommunicationBubble): number {
  if (left.sequenceId !== right.sequenceId) {
    return right.sequenceId - left.sequenceId;
  }

  return left.id.localeCompare(right.id);
}

function compareLinks(left: CommunicationLink, right: CommunicationLink): number {
  if (left.traceId !== right.traceId) {
    return (left.traceId ?? '').localeCompare(right.traceId ?? '');
  }

  if (left.sourceAgentId !== right.sourceAgentId) {
    return left.sourceAgentId.localeCompare(right.sourceAgentId);
  }

  return left.targetAgentId.localeCompare(right.targetAgentId);
}

function compareThreads(left: CommunicationThread, right: CommunicationThread): number {
  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.id.localeCompare(right.id);
}

function parseGraphUpdateEdge(rawEdge: string): { fromReference: string; toReference: string } | null {
  for (const separator of ['→', '->']) {
    if (!rawEdge.includes(separator)) {
      continue;
    }

    const parts = rawEdge.split(separator).map((part) => part.trim());
    const fromReference = parts[0];
    const toReference = parts[1];
    if (fromReference === undefined || toReference === undefined) {
      return null;
    }

    if (fromReference.length === 0 || toReference.length === 0) {
      return null;
    }

    return { fromReference, toReference };
  }

  return null;
}

function resolveAgentReference(reference: string, agents: GameState['agents']): string | null {
  const normalizedReference = normalizeReference(reference);
  const matchingAgents = Object.values(agents).filter((agent) => {
    const normalizedId = normalizeReference(agent.id);
    const normalizedName = normalizeReference(agent.name);
    return (
      normalizedId === normalizedReference ||
      normalizedId.startsWith(`${normalizedReference}-`) ||
      normalizedName === normalizedReference ||
      (normalizedReference === 'orchestrator' && agent.role === 'orchestrator')
    );
  });

  return matchingAgents.length === 1 ? matchingAgents[0]?.id ?? null : null;
}

function normalizeReference(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function toTraceCorrelationId(traceId: string | null): string | null {
  return traceId === null ? null : `trace:${traceId}`;
}

function readJsonString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function readJsonStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((item): item is string => typeof item === 'string' && item.length > 0);
}