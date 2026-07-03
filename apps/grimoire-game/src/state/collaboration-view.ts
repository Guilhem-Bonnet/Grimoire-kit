import type { AgentPresence, AgentRole, AgentStatus } from '../contracts/events';

import { createSessionView } from './session-view';
import { createTaskView } from './task-view';
import type { GameState, ToolCallLogEntry } from './game-state';

export const COLLABORATION_RELATION_ORDER = ['graph_update', 'task_handoff', 'shared_trace', 'hierarchy'] as const;

export type CollaborationRelation = (typeof COLLABORATION_RELATION_ORDER)[number];

export interface CollaborationNode {
  id: string;
  name: string;
  role: AgentRole;
  status: AgentStatus;
  roomId: string;
  parentId: string | null;
  childAgentIds: readonly string[];
  taskIds: readonly string[];
  traceIds: readonly string[];
  activeTaskCount: number;
  collaborationCount: number;
}

export interface CollaborationEdge {
  id: string;
  fromAgentId: string;
  toAgentId: string;
  directed: boolean;
  relation: CollaborationRelation;
  label: string;
  weight: number;
  traceIds: readonly string[];
  taskIds: readonly string[];
  sequenceIds: readonly number[];
  sourceEventTypes: readonly string[];
  strengthDelta: number | null;
}

export interface CollaborationHotspot {
  agentId: string;
  collaborationCount: number;
  incomingCount: number;
  outgoingCount: number;
  traceCount: number;
  handoffCount: number;
  graphUpdateCount: number;
  hierarchyCount: number;
}

export interface CollaborationMetrics {
  nodeCount: number;
  edgeCount: number;
  hierarchyEdgeCount: number;
  sharedTraceEdgeCount: number;
  taskHandoffEdgeCount: number;
  graphUpdateEdgeCount: number;
  hotspotCount: number;
}

export interface CollaborationView {
  protocolVersion: string;
  lastSequenceId: number;
  nodes: readonly CollaborationNode[];
  edges: readonly CollaborationEdge[];
  hotspots: readonly CollaborationHotspot[];
  metrics: CollaborationMetrics;
}

interface MutableEdge {
  id: string;
  fromAgentId: string;
  toAgentId: string;
  directed: boolean;
  relation: CollaborationRelation;
  label: string;
  weight: number;
  traceIds: Set<string>;
  taskIds: Set<string>;
  sequenceIds: Set<number>;
  sourceEventTypes: Set<string>;
  strengthDelta: number | null;
}

const COLLABORATION_RELATION_RANK: Record<CollaborationRelation, number> = {
  graph_update: 0,
  task_handoff: 1,
  shared_trace: 2,
  hierarchy: 3
};

export function createCollaborationView(state: GameState): CollaborationView {
  const sessionView = createSessionView(state);
  const taskView = createTaskView(state);
  const edgeMap = new Map<string, MutableEdge>();

  for (const agent of Object.values(state.agents)) {
    if (agent.parentId !== undefined && agent.parentId !== null && state.agents[agent.parentId] !== undefined) {
      upsertEdge(edgeMap, {
        fromAgentId: agent.parentId,
        toAgentId: agent.id,
        directed: true,
        relation: 'hierarchy',
        label: 'Hierarchy link',
        sourceEventType: 'hierarchy'
      });
    }
  }

  for (const session of sessionView.sessions) {
    const sortedAgentIds = [...session.summary.agentIds].sort((left, right) => left.localeCompare(right));

    for (let index = 0; index < sortedAgentIds.length; index += 1) {
      const fromAgentId = sortedAgentIds[index];
      if (fromAgentId === undefined) {
        continue;
      }

      for (let innerIndex = index + 1; innerIndex < sortedAgentIds.length; innerIndex += 1) {
        const toAgentId = sortedAgentIds[innerIndex];
        if (toAgentId === undefined) {
          continue;
        }

        upsertEdge(edgeMap, {
          fromAgentId,
          toAgentId,
          directed: false,
          relation: 'shared_trace',
          label: `Shared trace ${session.summary.traceId}`,
          traceId: session.summary.traceId,
          sequenceId: session.summary.lastSequenceId,
          sourceEventType: 'shared_trace'
        });
      }
    }
  }

  for (const task of taskView.tasks) {
    const orderedParticipants = uniqueConsecutiveAgentIds(
      [...task.recentWorkflowSteps]
        .sort((left, right) => left.sequenceId - right.sequenceId)
        .map((workflowStep) => workflowStep.agentId)
    );

    for (let index = 1; index < orderedParticipants.length; index += 1) {
      const fromAgentId = orderedParticipants[index - 1];
      const toAgentId = orderedParticipants[index];
      if (fromAgentId === undefined || toAgentId === undefined || fromAgentId === toAgentId) {
        continue;
      }

      const handoffSequenceId = task.recentWorkflowSteps[index]?.sequenceId;

      upsertEdge(edgeMap, {
        fromAgentId,
        toAgentId,
        directed: true,
        relation: 'task_handoff',
        label: `Task handoff: ${task.task.title}`,
        taskId: task.task.id,
        traceIds: task.traceIds,
        ...(handoffSequenceId === undefined ? {} : { sequenceId: handoffSequenceId }),
        sourceEventType: 'task_handoff'
      });
    }
  }

  for (const toolCall of state.recentToolCalls.filter((entry) => entry.sourceEventType === 'graph_update')) {
    const explicitEdge = readStringValue(toolCall.params.edge);
    if (explicitEdge === undefined) {
      continue;
    }

    const parsedEdge = parseGraphUpdateEdge(explicitEdge);
    if (parsedEdge === null) {
      continue;
    }

    const fromAgentId = resolveAgentReference(parsedEdge.fromReference, state.agents);
    const toAgentId = resolveAgentReference(parsedEdge.toReference, state.agents);
    if (fromAgentId === null || toAgentId === null) {
      continue;
    }

    upsertEdge(edgeMap, {
      fromAgentId,
      toAgentId,
      directed: true,
      relation: 'graph_update',
      label: createGraphUpdateLabel(toolCall),
      ...(toolCall.traceId === undefined ? {} : { traceId: toolCall.traceId }),
      sequenceId: toolCall.sequenceId,
      sourceEventType: toolCall.sourceEventType,
      strengthDelta: readNumberValue(toolCall.params.strength_after) - readNumberValue(toolCall.params.strength_before)
    });
  }

  const edges = Array.from(edgeMap.values()).map(toCollaborationEdge).sort(compareCollaborationEdges);
  const nodes = Object.values(state.agents)
    .map((agent) => createCollaborationNode(state, sessionView, edges, agent))
    .sort(compareCollaborationNodes);
  const hotspots = nodes
    .map((node) => createHotspot(edges, sessionView, node.id))
    .filter((hotspot) => hotspot.collaborationCount > 0)
    .sort(compareHotspots);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    nodes,
    edges,
    hotspots,
    metrics: {
      nodeCount: nodes.length,
      edgeCount: edges.length,
      hierarchyEdgeCount: edges.filter((edge) => edge.relation === 'hierarchy').length,
      sharedTraceEdgeCount: edges.filter((edge) => edge.relation === 'shared_trace').length,
      taskHandoffEdgeCount: edges.filter((edge) => edge.relation === 'task_handoff').length,
      graphUpdateEdgeCount: edges.filter((edge) => edge.relation === 'graph_update').length,
      hotspotCount: hotspots.length
    }
  };
}

function createCollaborationNode(
  state: GameState,
  sessionView: ReturnType<typeof createSessionView>,
  edges: readonly CollaborationEdge[],
  agent: AgentPresence
): CollaborationNode {
  const taskIds = Object.values(state.tasks)
    .filter((task) => task.assigneeId === agent.id)
    .map((task) => task.id)
    .sort((left, right) => left.localeCompare(right));
  const traceIds = uniqueStrings([
    ...sessionView.sessions
      .filter((session) => session.summary.agentIds.includes(agent.id))
      .map((session) => session.summary.traceId),
    ...edges
      .filter((edge) => edge.fromAgentId === agent.id || edge.toAgentId === agent.id)
      .flatMap((edge) => edge.traceIds)
  ]);
  const childAgentIds = Object.values(state.agents)
    .filter((candidate) => candidate.parentId === agent.id)
    .map((candidate) => candidate.id)
    .sort((left, right) => left.localeCompare(right));

  return {
    id: agent.id,
    name: agent.name,
    role: agent.role,
    status: agent.status,
    roomId: agent.roomId,
    parentId: agent.parentId ?? null,
    childAgentIds,
    taskIds,
    traceIds,
    activeTaskCount: taskIds.reduce((count, taskId) => {
      const task = state.tasks[taskId];
      return task !== undefined && task.status !== 'done' ? count + 1 : count;
    }, 0),
    collaborationCount: edges.filter((edge) => edge.fromAgentId === agent.id || edge.toAgentId === agent.id).length
  };
}

function createHotspot(
  edges: readonly CollaborationEdge[],
  sessionView: ReturnType<typeof createSessionView>,
  agentId: string
): CollaborationHotspot {
  const relatedEdges = edges.filter((edge) => edge.fromAgentId === agentId || edge.toAgentId === agentId);

  return {
    agentId,
    collaborationCount: relatedEdges.length,
    incomingCount: relatedEdges.filter((edge) => edge.toAgentId === agentId).length,
    outgoingCount: relatedEdges.filter((edge) => edge.fromAgentId === agentId).length,
    traceCount: sessionView.sessions.filter((session) => session.summary.agentIds.includes(agentId)).length,
    handoffCount: relatedEdges.filter((edge) => edge.relation === 'task_handoff').length,
    graphUpdateCount: relatedEdges.filter((edge) => edge.relation === 'graph_update').length,
    hierarchyCount: relatedEdges.filter((edge) => edge.relation === 'hierarchy').length
  };
}

function upsertEdge(
  edgeMap: Map<string, MutableEdge>,
  input: {
    fromAgentId: string;
    toAgentId: string;
    directed: boolean;
    relation: CollaborationRelation;
    label: string;
    traceId?: string;
    traceIds?: readonly string[];
    taskId?: string;
    sequenceId?: number;
    sourceEventType: string;
    strengthDelta?: number | null;
  }
): void {
  const key = buildEdgeKey(input.relation, input.fromAgentId, input.toAgentId, input.directed);
  const existing = edgeMap.get(key);

  if (existing === undefined) {
    edgeMap.set(key, {
      id: key,
      fromAgentId: input.directed ? input.fromAgentId : sortPair(input.fromAgentId, input.toAgentId)[0],
      toAgentId: input.directed ? input.toAgentId : sortPair(input.fromAgentId, input.toAgentId)[1],
      directed: input.directed,
      relation: input.relation,
      label: input.label,
      weight: 1,
      traceIds: new Set(compactStrings([input.traceId, ...(input.traceIds ?? [])])),
      taskIds: new Set(compactStrings([input.taskId])),
      sequenceIds: new Set(input.sequenceId === undefined ? [] : [input.sequenceId]),
      sourceEventTypes: new Set([input.sourceEventType]),
      strengthDelta: input.strengthDelta ?? null
    });
    return;
  }

  existing.weight += 1;
  for (const traceId of compactStrings([input.traceId, ...(input.traceIds ?? [])])) {
    existing.traceIds.add(traceId);
  }
  for (const taskId of compactStrings([input.taskId])) {
    existing.taskIds.add(taskId);
  }
  if (input.sequenceId !== undefined) {
    existing.sequenceIds.add(input.sequenceId);
  }
  existing.sourceEventTypes.add(input.sourceEventType);
  if (input.strengthDelta !== undefined && input.strengthDelta !== null) {
    existing.strengthDelta = input.strengthDelta;
  }
}

function buildEdgeKey(relation: CollaborationRelation, fromAgentId: string, toAgentId: string, directed: boolean): string {
  if (directed) {
    return `${relation}:${fromAgentId}->${toAgentId}`;
  }

  const [left, right] = sortPair(fromAgentId, toAgentId);
  return `${relation}:${left}<->${right}`;
}

function sortPair(left: string, right: string): [string, string] {
  return left.localeCompare(right) <= 0 ? [left, right] : [right, left];
}

function toCollaborationEdge(edge: MutableEdge): CollaborationEdge {
  return {
    id: edge.id,
    fromAgentId: edge.fromAgentId,
    toAgentId: edge.toAgentId,
    directed: edge.directed,
    relation: edge.relation,
    label: edge.label,
    weight: edge.weight,
    traceIds: [...edge.traceIds].sort((left, right) => left.localeCompare(right)),
    taskIds: [...edge.taskIds].sort((left, right) => left.localeCompare(right)),
    sequenceIds: [...edge.sequenceIds].sort((left, right) => left - right),
    sourceEventTypes: [...edge.sourceEventTypes].sort((left, right) => left.localeCompare(right)),
    strengthDelta: edge.strengthDelta
  };
}

function compareCollaborationEdges(left: CollaborationEdge, right: CollaborationEdge): number {
  if (left.relation !== right.relation) {
    return COLLABORATION_RELATION_RANK[left.relation] - COLLABORATION_RELATION_RANK[right.relation];
  }

  if (left.weight !== right.weight) {
    return right.weight - left.weight;
  }

  return left.id.localeCompare(right.id);
}

function compareCollaborationNodes(left: CollaborationNode, right: CollaborationNode): number {
  if (left.collaborationCount !== right.collaborationCount) {
    return right.collaborationCount - left.collaborationCount;
  }

  if (left.activeTaskCount !== right.activeTaskCount) {
    return right.activeTaskCount - left.activeTaskCount;
  }

  return left.name.localeCompare(right.name);
}

function compareHotspots(left: CollaborationHotspot, right: CollaborationHotspot): number {
  if (left.collaborationCount !== right.collaborationCount) {
    return right.collaborationCount - left.collaborationCount;
  }

  if (left.traceCount !== right.traceCount) {
    return right.traceCount - left.traceCount;
  }

  return left.agentId.localeCompare(right.agentId);
}

function uniqueConsecutiveAgentIds(agentIds: readonly (string | undefined)[]): string[] {
  const result: string[] = [];

  for (const agentId of agentIds) {
    if (agentId === undefined) {
      continue;
    }

    if (result[result.length - 1] !== agentId) {
      result.push(agentId);
    }
  }

  return result;
}

function parseGraphUpdateEdge(rawEdge: string): { fromReference: string; toReference: string } | null {
  const separators = ['→', '->'];

  for (const separator of separators) {
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
  const entries = Object.values(agents);
  const matches = entries.filter((agent) => {
    const normalizedId = normalizeReference(agent.id);
    const normalizedName = normalizeReference(agent.name);
    return (
      normalizedId === normalizedReference ||
      normalizedId.startsWith(`${normalizedReference}-`) ||
      normalizedName === normalizedReference ||
      (normalizedReference === 'orchestrator' && agent.role === 'orchestrator')
    );
  });

  return matches.length === 1 ? matches[0]?.id ?? null : null;
}

function normalizeReference(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function createGraphUpdateLabel(toolCall: ToolCallLogEntry): string {
  const reason = readStringValue(toolCall.params.reason);
  if (reason !== undefined) {
    return `Graph update: ${reason}`;
  }

  return 'Graph update';
}

function compactStrings(values: readonly (string | undefined)[]): string[] {
  return values.filter((value): value is string => typeof value === 'string' && value.length > 0);
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function readStringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

function readNumberValue(value: unknown): number {
  return typeof value === 'number' ? value : 0;
}