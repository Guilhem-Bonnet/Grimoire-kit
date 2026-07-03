import type { AgentPresence, HostContextSourceType, ToolCallLogEntry, WorkflowStepLogEntry } from '../contracts/events';

import type { GameState } from './game-state';
import {
  createLibraryView,
  type LibraryContextCard,
  type LibraryTraceVolume,
  type LibraryView
} from './library-view';

export const LIBRARY_MEMORY_TIER_ORDER = ['short_term', 'long_term'] as const;
export const LIBRARY_MEMORY_ACCESS_TYPE_ORDER = ['read', 'write'] as const;
export const LIBRARY_MEMORY_OBJECT_TYPE_ORDER = ['reading_desk', 'book_shelf', 'vector_orb'] as const;

export type LibraryMemoryTier = (typeof LIBRARY_MEMORY_TIER_ORDER)[number];
export type LibraryMemoryAccessType = (typeof LIBRARY_MEMORY_ACCESS_TYPE_ORDER)[number];
export type LibraryMemoryRoomObjectType = (typeof LIBRARY_MEMORY_OBJECT_TYPE_ORDER)[number];

export interface LibraryMemoryReference extends LibraryContextCard {
  tier: LibraryMemoryTier;
  objectId: string;
  objectType: LibraryMemoryRoomObjectType;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
}

export interface LibraryMemoryAccess {
  accessId: string;
  sequenceId: number | null;
  accessType: LibraryMemoryAccessType;
  tier: LibraryMemoryTier;
  objectId: string;
  objectType: LibraryMemoryRoomObjectType;
  hostId: string | null;
  hostDisplayName: string | null;
  agentId: string | null;
  agentName: string | null;
  roomId: string | null;
  traceId: string | null;
  taskId: string | null;
  correlationId: string | null;
  timestamp: string;
  sourceEventType: string;
  toolName: string | null;
  title: string;
  detail: string;
  contentRefs: readonly string[];
  referenceEntryIds: readonly string[];
  stale: boolean;
}

export interface LibraryMemoryRoomObject {
  objectId: string;
  objectType: LibraryMemoryRoomObjectType;
  tier: LibraryMemoryTier;
  label: string;
  hostId: string | null;
  hostDisplayName: string | null;
  traceIds: readonly string[];
  taskIds: readonly string[];
  referenceEntryIds: readonly string[];
  accessIds: readonly string[];
  accessCount: number;
  staleCount: number;
  updatedAt: string;
}

export interface LibraryAgentMemoryReference {
  agentId: string;
  agentName: string;
  roomId: string;
  shortTermEntryIds: readonly string[];
  longTermEntryIds: readonly string[];
  accessIds: readonly string[];
  traceIds: readonly string[];
  taskIds: readonly string[];
  readCount: number;
  writeCount: number;
  lastAccessAt: string | null;
}

export interface LibraryMemorySummary {
  referenceCount: number;
  shortTermReferenceCount: number;
  longTermReferenceCount: number;
  staleReferenceCount: number;
  accessCount: number;
  readCount: number;
  writeCount: number;
  roomObjectCount: number;
  agentReferenceCount: number;
}

export interface LibraryMemoryView {
  protocolVersion: string;
  lastSequenceId: number;
  referenceTimestamp: string;
  library: LibraryView;
  references: readonly LibraryMemoryReference[];
  accesses: readonly LibraryMemoryAccess[];
  roomObjects: readonly LibraryMemoryRoomObject[];
  agentReferences: readonly LibraryAgentMemoryReference[];
  summary: LibraryMemorySummary;
}

export interface LibraryMemoryQuery {
  hostId?: string;
  traceId?: string;
  taskId?: string;
  agentId?: string;
  tier?: LibraryMemoryTier;
  accessType?: LibraryMemoryAccessType;
  staleOnly?: boolean;
}

export interface LibraryMemoryQueryResult {
  references: readonly LibraryMemoryReference[];
  accesses: readonly LibraryMemoryAccess[];
  roomObjects: readonly LibraryMemoryRoomObject[];
  agentReferences: readonly LibraryAgentMemoryReference[];
  totalCount: number;
}

export function createLibraryMemoryView(state: GameState): LibraryMemoryView {
  const library = createLibraryView(state);
  const references = library.contextEntries.map((entry) => createLibraryMemoryReference(state, entry)).sort(compareLibraryMemoryReferences);
  const accesses = createLibraryMemoryAccesses(state, references).sort(compareLibraryMemoryAccesses);
  const roomObjects = createLibraryMemoryRoomObjects(references, accesses).sort(compareLibraryMemoryRoomObjects);
  const agentReferences = createLibraryAgentMemoryReferences(state, references, accesses).sort(compareLibraryAgentMemoryReferences);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    referenceTimestamp: library.referenceTimestamp,
    library,
    references,
    accesses,
    roomObjects,
    agentReferences,
    summary: {
      referenceCount: references.length,
      shortTermReferenceCount: references.filter((reference) => reference.tier === 'short_term').length,
      longTermReferenceCount: references.filter((reference) => reference.tier === 'long_term').length,
      staleReferenceCount: references.filter((reference) => reference.stale).length,
      accessCount: accesses.length,
      readCount: accesses.filter((access) => access.accessType === 'read').length,
      writeCount: accesses.filter((access) => access.accessType === 'write').length,
      roomObjectCount: roomObjects.length,
      agentReferenceCount: agentReferences.length
    }
  };
}

export function queryLibraryMemoryView(
  view: LibraryMemoryView,
  query: LibraryMemoryQuery = {}
): LibraryMemoryQueryResult {
  const references = view.references.filter((reference) => matchesLibraryMemoryReference(reference, query));
  const accesses = view.accesses.filter((access) => matchesLibraryMemoryAccess(access, query));
  const roomObjects = view.roomObjects.filter((roomObject) => matchesLibraryMemoryRoomObject(roomObject, query));
  const agentReferences = view.agentReferences.filter((agentReference) => matchesLibraryAgentMemoryReference(agentReference, query));

  return {
    references,
    accesses,
    roomObjects,
    agentReferences,
    totalCount: references.length + accesses.length + roomObjects.length + agentReferences.length
  };
}

function createLibraryMemoryReference(state: GameState, entry: LibraryContextCard): LibraryMemoryReference {
  const relatedAgent = resolveRelatedAgent(state, entry.traceId, entry.taskId, entry.correlationId, entry.importedAt);
  const objectDescriptor = createLibraryMemoryObjectDescriptor(
    resolveLibraryMemoryTier(entry.sourceType, entry.contentRef),
    entry.hostId,
    entry.hostDisplayName,
    [entry.contentRef]
  );

  return {
    ...entry,
    tier: resolveLibraryMemoryTier(entry.sourceType, entry.contentRef),
    objectId: objectDescriptor.objectId,
    objectType: objectDescriptor.objectType,
    agentId: relatedAgent?.id ?? null,
    agentName: relatedAgent?.name ?? null,
    roomId: relatedAgent?.roomId ?? null
  };
}

function createLibraryMemoryAccesses(
  state: GameState,
  references: readonly LibraryMemoryReference[]
): LibraryMemoryAccess[] {
  return [
    ...references.map((reference) => createContextWriteAccess(reference)),
    ...state.recentToolCalls
      .map((toolCall) => createToolMemoryAccess(state, toolCall, references))
      .filter((access): access is LibraryMemoryAccess => access !== null),
    ...state.recentWorkflowSteps
      .map((workflowStep) => createWorkflowMemoryAccess(state, workflowStep, references))
      .filter((access): access is LibraryMemoryAccess => access !== null)
  ];
}

function createContextWriteAccess(reference: LibraryMemoryReference): LibraryMemoryAccess {
  return {
    accessId: `library-memory-access:context:${reference.entryId}`,
    sequenceId: null,
    accessType: 'write',
    tier: reference.tier,
    objectId: reference.objectId,
    objectType: reference.objectType,
    hostId: reference.hostId,
    hostDisplayName: reference.hostDisplayName,
    agentId: reference.agentId,
    agentName: reference.agentName,
    roomId: reference.roomId,
    traceId: reference.traceId,
    taskId: reference.taskId,
    correlationId: reference.correlationId,
    timestamp: reference.importedAt,
    sourceEventType: 'host_context_ledger_update',
    toolName: null,
    title: `Memory write: ${reference.sourceType}`,
    detail: `${reference.hostDisplayName ?? reference.hostId ?? 'Library'} stored ${reference.contentRef}.`,
    contentRefs: [reference.contentRef],
    referenceEntryIds: [reference.entryId],
    stale: reference.stale
  };
}

function createToolMemoryAccess(
  state: GameState,
  toolCall: ToolCallLogEntry,
  references: readonly LibraryMemoryReference[]
): LibraryMemoryAccess | null {
  const accessType = resolveLibraryToolAccessType(toolCall);
  if (accessType === null) {
    return null;
  }

  const hostId = readRecordStringByKeys(toolCall.params, ['host_id', 'hostId']);
  const hostDisplayName = references.find((reference) => reference.hostId === hostId)?.hostDisplayName ?? null;
  const contentRefs = readRecordStringListByKeys(toolCall.params, ['contentRefs', 'content_refs', 'contentRef', 'content_ref', 'ref', 'refs']);
  const traceId = toolCall.traceId ?? null;
  const taskId = readRecordStringByKeys(toolCall.params, ['task_id', 'taskId']);
  const correlationId = readRecordStringByKeys(toolCall.params, ['correlationId', 'correlation_id']);
  const tier =
    normalizeLibraryMemoryTier(readRecordStringByKeys(toolCall.params, ['memory_tier', 'memoryTier'])) ??
    inferLibraryMemoryTierFromContentRefs(contentRefs) ??
    'long_term';
  const objectDescriptor = createLibraryMemoryObjectDescriptor(tier, hostId, hostDisplayName, contentRefs);
  const relatedAgent =
    toolCall.agentId === undefined
      ? resolveRelatedAgent(state, traceId, taskId, correlationId, toolCall.timestamp)
      : (state.agents[toolCall.agentId] ?? null);
  const referenceEntryIds = resolveReferenceEntryIds(references, traceId, taskId, contentRefs);

  return {
    accessId: `library-memory-access:tool:${toolCall.sequenceId}`,
    sequenceId: toolCall.sequenceId,
    accessType,
    tier,
    objectId: objectDescriptor.objectId,
    objectType: objectDescriptor.objectType,
    hostId,
    hostDisplayName,
    agentId: relatedAgent?.id ?? null,
    agentName: relatedAgent?.name ?? null,
    roomId: relatedAgent?.roomId ?? null,
    traceId,
    taskId,
    correlationId,
    timestamp: toolCall.timestamp,
    sourceEventType: toolCall.sourceEventType,
    toolName: toolCall.tool,
    title: `Memory ${accessType}: ${toolCall.tool}`,
    detail: formatLibraryMemoryAccessDetail(toolCall.tool, accessType, contentRefs, taskId),
    contentRefs,
    referenceEntryIds,
    stale: referenceEntryIds.some((entryId) => references.some((reference) => reference.entryId === entryId && reference.stale))
  };
}

function createWorkflowMemoryAccess(
  state: GameState,
  workflowStep: WorkflowStepLogEntry,
  references: readonly LibraryMemoryReference[]
): LibraryMemoryAccess | null {
  const metadata = toMetadataRecord(workflowStep.metadata);
  const accessType = resolveWorkflowMemoryAccessType(workflowStep, metadata);
  if (accessType === null) {
    return null;
  }

  const hostId = readRecordStringByKeys(metadata, ['hostId', 'host_id']);
  const hostDisplayName = references.find((reference) => reference.hostId === hostId)?.hostDisplayName ?? null;
  const contentRefs = readRecordStringListByKeys(metadata, ['contentRefs', 'content_refs', 'contentRef', 'content_ref', 'memoryRefs', 'memory_refs']);
  const traceId = workflowStep.traceId ?? null;
  const taskId = workflowStep.taskId ?? null;
  const correlationId = readRecordStringByKeys(metadata, ['correlationId', 'correlation_id']);
  const tier =
    normalizeLibraryMemoryTier(readRecordStringByKeys(metadata, ['memoryTier', 'memory_tier'])) ??
    inferLibraryMemoryTierFromContentRefs(contentRefs) ??
    'short_term';
  const objectDescriptor = createLibraryMemoryObjectDescriptor(tier, hostId, hostDisplayName, contentRefs);
  const relatedAgent =
    workflowStep.agentId === undefined
      ? resolveRelatedAgent(state, traceId, taskId, correlationId, workflowStep.timestamp)
      : (state.agents[workflowStep.agentId] ?? null);
  const referenceEntryIds = resolveReferenceEntryIds(references, traceId, taskId, contentRefs);

  return {
    accessId: `library-memory-access:workflow:${workflowStep.sequenceId}`,
    sequenceId: workflowStep.sequenceId,
    accessType,
    tier,
    objectId: objectDescriptor.objectId,
    objectType: objectDescriptor.objectType,
    hostId,
    hostDisplayName,
    agentId: relatedAgent?.id ?? null,
    agentName: relatedAgent?.name ?? null,
    roomId: relatedAgent?.roomId ?? null,
    traceId,
    taskId,
    correlationId,
    timestamp: workflowStep.timestamp,
    sourceEventType: workflowStep.sourceEventType,
    toolName: null,
    title: `Memory ${accessType}: ${workflowStep.step}`,
    detail: workflowStep.detail,
    contentRefs,
    referenceEntryIds,
    stale: referenceEntryIds.some((entryId) => references.some((reference) => reference.entryId === entryId && reference.stale))
  };
}

function createLibraryMemoryRoomObjects(
  references: readonly LibraryMemoryReference[],
  accesses: readonly LibraryMemoryAccess[]
): LibraryMemoryRoomObject[] {
  const objects = new Map<string, {
    objectId: string;
    objectType: LibraryMemoryRoomObjectType;
    tier: LibraryMemoryTier;
    label: string;
    hostId: string | null;
    hostDisplayName: string | null;
    traceIds: Set<string>;
    taskIds: Set<string>;
    referenceEntryIds: Set<string>;
    accessIds: Set<string>;
    accessCount: number;
    staleCount: number;
    updatedAt: string;
  }>();

  for (const reference of references) {
    const descriptor = createLibraryMemoryObjectDescriptor(reference.tier, reference.hostId, reference.hostDisplayName, [reference.contentRef]);
    const current = objects.get(reference.objectId) ?? {
      objectId: reference.objectId,
      objectType: reference.objectType,
      tier: reference.tier,
      label: descriptor.label,
      hostId: reference.hostId,
      hostDisplayName: reference.hostDisplayName,
      traceIds: new Set<string>(),
      taskIds: new Set<string>(),
      referenceEntryIds: new Set<string>(),
      accessIds: new Set<string>(),
      accessCount: 0,
      staleCount: 0,
      updatedAt: reference.importedAt
    };
    addIfPresent(current.traceIds, reference.traceId);
    addIfPresent(current.taskIds, reference.taskId);
    current.referenceEntryIds.add(reference.entryId);
    current.staleCount += reference.stale ? 1 : 0;
    if (reference.importedAt > current.updatedAt) {
      current.updatedAt = reference.importedAt;
    }
    objects.set(reference.objectId, current);
  }

  for (const access of accesses) {
    const descriptor = createLibraryMemoryObjectDescriptor(access.tier, access.hostId, access.hostDisplayName, access.contentRefs);
    const current = objects.get(access.objectId) ?? {
      objectId: access.objectId,
      objectType: access.objectType,
      tier: access.tier,
      label: descriptor.label,
      hostId: access.hostId,
      hostDisplayName: access.hostDisplayName,
      traceIds: new Set<string>(),
      taskIds: new Set<string>(),
      referenceEntryIds: new Set<string>(),
      accessIds: new Set<string>(),
      accessCount: 0,
      staleCount: 0,
      updatedAt: access.timestamp
    };
    addIfPresent(current.traceIds, access.traceId);
    addIfPresent(current.taskIds, access.taskId);
    access.referenceEntryIds.forEach((entryId) => current.referenceEntryIds.add(entryId));
    current.accessIds.add(access.accessId);
    current.accessCount += 1;
    if (access.stale) {
      current.staleCount += 1;
    }
    if (access.timestamp > current.updatedAt) {
      current.updatedAt = access.timestamp;
    }
    objects.set(access.objectId, current);
  }

  return Array.from(objects.values()).map((object) => ({
    objectId: object.objectId,
    objectType: object.objectType,
    tier: object.tier,
    label: object.label,
    hostId: object.hostId,
    hostDisplayName: object.hostDisplayName,
    traceIds: [...object.traceIds].sort((left, right) => left.localeCompare(right)),
    taskIds: [...object.taskIds].sort((left, right) => left.localeCompare(right)),
    referenceEntryIds: [...object.referenceEntryIds].sort((left, right) => left.localeCompare(right)),
    accessIds: [...object.accessIds].sort((left, right) => left.localeCompare(right)),
    accessCount: object.accessCount,
    staleCount: object.staleCount,
    updatedAt: object.updatedAt
  }));
}

function createLibraryAgentMemoryReferences(
  state: GameState,
  references: readonly LibraryMemoryReference[],
  accesses: readonly LibraryMemoryAccess[]
): LibraryAgentMemoryReference[] {
  const index = new Map<string, {
    agent: AgentPresence;
    shortTermEntryIds: Set<string>;
    longTermEntryIds: Set<string>;
    accessIds: Set<string>;
    traceIds: Set<string>;
    taskIds: Set<string>;
    readCount: number;
    writeCount: number;
    lastAccessAt: string | null;
  }>();

  for (const reference of references) {
    if (reference.agentId === null) {
      continue;
    }

    const agent = state.agents[reference.agentId];
    if (agent === undefined) {
      continue;
    }

    const current = index.get(agent.id) ?? {
      agent,
      shortTermEntryIds: new Set<string>(),
      longTermEntryIds: new Set<string>(),
      accessIds: new Set<string>(),
      traceIds: new Set<string>(),
      taskIds: new Set<string>(),
      readCount: 0,
      writeCount: 0,
      lastAccessAt: null
    };
    (reference.tier === 'short_term' ? current.shortTermEntryIds : current.longTermEntryIds).add(reference.entryId);
    addIfPresent(current.traceIds, reference.traceId);
    addIfPresent(current.taskIds, reference.taskId);
    index.set(agent.id, current);
  }

  for (const access of accesses) {
    if (access.agentId === null) {
      continue;
    }

    const agent = state.agents[access.agentId];
    if (agent === undefined) {
      continue;
    }

    const current = index.get(agent.id) ?? {
      agent,
      shortTermEntryIds: new Set<string>(),
      longTermEntryIds: new Set<string>(),
      accessIds: new Set<string>(),
      traceIds: new Set<string>(),
      taskIds: new Set<string>(),
      readCount: 0,
      writeCount: 0,
      lastAccessAt: null
    };
    current.accessIds.add(access.accessId);
    addIfPresent(current.traceIds, access.traceId);
    addIfPresent(current.taskIds, access.taskId);
    if (access.accessType === 'read') {
      current.readCount += 1;
    } else {
      current.writeCount += 1;
    }
    if (current.lastAccessAt === null || access.timestamp > current.lastAccessAt) {
      current.lastAccessAt = access.timestamp;
    }
    index.set(agent.id, current);
  }

  return Array.from(index.values()).map((record) => ({
    agentId: record.agent.id,
    agentName: record.agent.name,
    roomId: record.agent.roomId,
    shortTermEntryIds: [...record.shortTermEntryIds].sort((left, right) => left.localeCompare(right)),
    longTermEntryIds: [...record.longTermEntryIds].sort((left, right) => left.localeCompare(right)),
    accessIds: [...record.accessIds].sort((left, right) => left.localeCompare(right)),
    traceIds: [...record.traceIds].sort((left, right) => left.localeCompare(right)),
    taskIds: [...record.taskIds].sort((left, right) => left.localeCompare(right)),
    readCount: record.readCount,
    writeCount: record.writeCount,
    lastAccessAt: record.lastAccessAt
  }));
}

function resolveRelatedAgent(
  state: GameState,
  traceId: string | null,
  taskId: string | null,
  correlationId: string | null,
  upperTimestamp: string
): AgentPresence | null {
  const workflowMatch = [...state.recentWorkflowSteps]
    .filter((workflowStep) => workflowStep.agentId !== undefined)
    .filter((workflowStep) => workflowStep.timestamp <= upperTimestamp)
    .filter((workflowStep) => matchesTraceTaskCorrelation(workflowStep.traceId ?? null, workflowStep.taskId ?? null, readCorrelationId(workflowStep), traceId, taskId, correlationId))
    .sort(compareTimestampedSequenceDescending)[0];

  if (workflowMatch !== undefined && workflowMatch.agentId !== undefined) {
    return state.agents[workflowMatch.agentId] ?? null;
  }

  const toolMatch = [...state.recentToolCalls]
    .filter((toolCall) => toolCall.agentId !== undefined)
    .filter((toolCall) => toolCall.timestamp <= upperTimestamp)
    .filter((toolCall) => matchesTraceTaskCorrelation(toolCall.traceId ?? null, readToolTaskId(toolCall), readToolCorrelationId(toolCall), traceId, taskId, correlationId))
    .sort(compareTimestampedSequenceDescending)[0];

  if (toolMatch !== undefined && toolMatch.agentId !== undefined) {
    return state.agents[toolMatch.agentId] ?? null;
  }

  if (taskId !== null) {
    const assigneeId = state.tasks[taskId]?.assigneeId ?? null;
    if (assigneeId !== null) {
      return state.agents[assigneeId] ?? null;
    }
  }

  return null;
}

function matchesTraceTaskCorrelation(
  candidateTraceId: string | null,
  candidateTaskId: string | null,
  candidateCorrelationId: string | null,
  traceId: string | null,
  taskId: string | null,
  correlationId: string | null
): boolean {
  if (correlationId !== null && candidateCorrelationId === correlationId) {
    return true;
  }

  if (traceId !== null && candidateTraceId === traceId) {
    return true;
  }

  return taskId !== null && candidateTaskId === taskId;
}

function createLibraryMemoryObjectDescriptor(
  tier: LibraryMemoryTier,
  hostId: string | null,
  hostDisplayName: string | null,
  contentRefs: readonly string[]
): { objectId: string; objectType: LibraryMemoryRoomObjectType; label: string } {
  const suffix = hostId ?? 'runtime';
  const vectorBacked = contentRefs.some((contentRef) => /qdrant|chroma|vector|embedding/i.test(contentRef));

  if (tier === 'short_term') {
    return {
      objectId: `library-desk:${suffix}`,
      objectType: 'reading_desk',
      label: `${hostDisplayName ?? hostId ?? 'Shared'} desk cache`
    };
  }

  if (vectorBacked) {
    return {
      objectId: `library-orb:${suffix}`,
      objectType: 'vector_orb',
      label: `${hostDisplayName ?? hostId ?? 'Shared'} vector orb`
    };
  }

  return {
    objectId: `library-shelf:${suffix}`,
    objectType: 'book_shelf',
    label: `${hostDisplayName ?? hostId ?? 'Shared'} archive shelf`
  };
}

function resolveLibraryMemoryTier(sourceType: HostContextSourceType, contentRef: string): LibraryMemoryTier {
  if (sourceType === 'memory' || sourceType === 'instructions') {
    return 'long_term';
  }

  if (/qdrant|chroma|vector|embedding|memory:\/\//i.test(contentRef)) {
    return 'long_term';
  }

  return 'short_term';
}

function resolveLibraryToolAccessType(toolCall: ToolCallLogEntry): LibraryMemoryAccessType | null {
  const signal = `${toolCall.tool} ${toolCall.sourceEventType}`.toLowerCase();
  const isMemorySignal = signal.includes('memory') || readRecordStringByKeys(toolCall.params, ['memory_tier', 'memoryTier']) !== null;
  if (!isMemorySignal) {
    return null;
  }

  if (signal.includes('search') || signal.includes('read') || signal.includes('query') || signal.includes('recall')) {
    return 'read';
  }

  if (signal.includes('store') || signal.includes('write') || signal.includes('remember') || signal.includes('save')) {
    return 'write';
  }

  return null;
}

function resolveWorkflowMemoryAccessType(
  workflowStep: WorkflowStepLogEntry,
  metadata: Record<string, unknown>
): LibraryMemoryAccessType | null {
  const explicit = readRecordStringByKeys(metadata, ['memoryAccess', 'memory_access', 'accessType', 'access_type']);
  if (explicit !== null) {
    if (explicit === 'read') {
      return 'read';
    }

    if (explicit === 'write') {
      return 'write';
    }
  }

  const sourceEventType = workflowStep.sourceEventType.toLowerCase();
  if (sourceEventType.includes('memory_read')) {
    return 'read';
  }

  if (sourceEventType.includes('memory_write')) {
    return 'write';
  }

  return null;
}

function inferLibraryMemoryTierFromContentRefs(contentRefs: readonly string[]): LibraryMemoryTier | null {
  if (contentRefs.some((contentRef) => /memory:\/\//i.test(contentRef))) {
    return 'long_term';
  }

  if (contentRefs.some((contentRef) => /session:\/\//i.test(contentRef))) {
    return 'short_term';
  }

  return null;
}

function resolveReferenceEntryIds(
  references: readonly LibraryMemoryReference[],
  traceId: string | null,
  taskId: string | null,
  contentRefs: readonly string[]
): string[] {
  const directMatches = references
    .filter((reference) => contentRefs.includes(reference.contentRef))
    .map((reference) => reference.entryId);
  if (directMatches.length > 0) {
    return uniqueStrings(directMatches);
  }

  return uniqueStrings(
    references
      .filter((reference) => (traceId !== null && reference.traceId === traceId) || (taskId !== null && reference.taskId === taskId))
      .map((reference) => reference.entryId)
  );
}

function matchesLibraryMemoryReference(reference: LibraryMemoryReference, query: LibraryMemoryQuery): boolean {
  if (query.hostId !== undefined && reference.hostId !== query.hostId) {
    return false;
  }

  if (query.traceId !== undefined && reference.traceId !== query.traceId) {
    return false;
  }

  if (query.taskId !== undefined && reference.taskId !== query.taskId) {
    return false;
  }

  if (query.agentId !== undefined && reference.agentId !== query.agentId) {
    return false;
  }

  if (query.tier !== undefined && reference.tier !== query.tier) {
    return false;
  }

  if (query.staleOnly === true && !reference.stale) {
    return false;
  }

  return true;
}

function matchesLibraryMemoryAccess(access: LibraryMemoryAccess, query: LibraryMemoryQuery): boolean {
  if (query.hostId !== undefined && access.hostId !== query.hostId) {
    return false;
  }

  if (query.traceId !== undefined && access.traceId !== query.traceId) {
    return false;
  }

  if (query.taskId !== undefined && access.taskId !== query.taskId) {
    return false;
  }

  if (query.agentId !== undefined && access.agentId !== query.agentId) {
    return false;
  }

  if (query.tier !== undefined && access.tier !== query.tier) {
    return false;
  }

  if (query.accessType !== undefined && access.accessType !== query.accessType) {
    return false;
  }

  if (query.staleOnly === true && !access.stale) {
    return false;
  }

  return true;
}

function matchesLibraryMemoryRoomObject(roomObject: LibraryMemoryRoomObject, query: LibraryMemoryQuery): boolean {
  if (query.hostId !== undefined && roomObject.hostId !== query.hostId) {
    return false;
  }

  if (query.traceId !== undefined && !roomObject.traceIds.includes(query.traceId)) {
    return false;
  }

  if (query.taskId !== undefined && !roomObject.taskIds.includes(query.taskId)) {
    return false;
  }

  if (query.tier !== undefined && roomObject.tier !== query.tier) {
    return false;
  }

  if (query.staleOnly === true && roomObject.staleCount === 0) {
    return false;
  }

  return true;
}

function matchesLibraryAgentMemoryReference(
  agentReference: LibraryAgentMemoryReference,
  query: LibraryMemoryQuery
): boolean {
  if (query.agentId !== undefined && agentReference.agentId !== query.agentId) {
    return false;
  }

  if (query.traceId !== undefined && !agentReference.traceIds.includes(query.traceId)) {
    return false;
  }

  if (query.taskId !== undefined && !agentReference.taskIds.includes(query.taskId)) {
    return false;
  }

  if (query.tier === 'short_term' && agentReference.shortTermEntryIds.length === 0) {
    return false;
  }

  if (query.tier === 'long_term' && agentReference.longTermEntryIds.length === 0) {
    return false;
  }

  if (query.accessType === 'read' && agentReference.readCount === 0) {
    return false;
  }

  if (query.accessType === 'write' && agentReference.writeCount === 0) {
    return false;
  }

  return true;
}

function compareLibraryMemoryReferences(left: LibraryMemoryReference, right: LibraryMemoryReference): number {
  const staleDelta = Number(right.stale) - Number(left.stale);
  if (staleDelta !== 0) {
    return staleDelta;
  }

  if (left.importedAt !== right.importedAt) {
    return right.importedAt.localeCompare(left.importedAt);
  }

  return left.entryId.localeCompare(right.entryId);
}

function compareLibraryMemoryAccesses(left: LibraryMemoryAccess, right: LibraryMemoryAccess): number {
  if (left.timestamp !== right.timestamp) {
    return right.timestamp.localeCompare(left.timestamp);
  }

  if (left.accessType !== right.accessType) {
    return left.accessType === 'write' ? -1 : 1;
  }

  return left.accessId.localeCompare(right.accessId);
}

function compareLibraryMemoryRoomObjects(left: LibraryMemoryRoomObject, right: LibraryMemoryRoomObject): number {
  if (left.staleCount !== right.staleCount) {
    return right.staleCount - left.staleCount;
  }

  if (left.accessCount !== right.accessCount) {
    return right.accessCount - left.accessCount;
  }

  return left.label.localeCompare(right.label);
}

function compareLibraryAgentMemoryReferences(
  left: LibraryAgentMemoryReference,
  right: LibraryAgentMemoryReference
): number {
  const leftAccessCount = left.accessIds.length;
  const rightAccessCount = right.accessIds.length;
  if (leftAccessCount !== rightAccessCount) {
    return rightAccessCount - leftAccessCount;
  }

  if (left.lastAccessAt !== right.lastAccessAt) {
    return (right.lastAccessAt ?? '').localeCompare(left.lastAccessAt ?? '');
  }

  return left.agentName.localeCompare(right.agentName);
}

function readCorrelationId(workflowStep: WorkflowStepLogEntry): string | null {
  return readRecordStringByKeys(toMetadataRecord(workflowStep.metadata), ['correlationId', 'correlation_id']);
}

function readToolTaskId(toolCall: ToolCallLogEntry): string | null {
  return readRecordStringByKeys(toolCall.params, ['task_id', 'taskId']);
}

function readToolCorrelationId(toolCall: ToolCallLogEntry): string | null {
  return readRecordStringByKeys(toolCall.params, ['correlationId', 'correlation_id']);
}

function formatLibraryMemoryAccessDetail(
  toolName: string,
  accessType: LibraryMemoryAccessType,
  contentRefs: readonly string[],
  taskId: string | null
): string {
  const refLabel = contentRefs[0] ?? taskId ?? 'memory';
  return `${toolName} ${accessType === 'read' ? 'consulted' : 'updated'} ${refLabel}.`;
}

function normalizeLibraryMemoryTier(value: string | null): LibraryMemoryTier | null {
  if (value === null) {
    return null;
  }

  const normalized = value.trim().toLowerCase();
  if (normalized === 'short_term' || normalized === 'short-term' || normalized === 'short') {
    return 'short_term';
  }

  if (normalized === 'long_term' || normalized === 'long-term' || normalized === 'long') {
    return 'long_term';
  }

  return null;
}

function readRecordStringByKeys(record: Record<string, unknown>, keys: readonly string[]): string | null {
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

function readRecordStringListByKeys(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const values = normalizeStringList(record[key]);
    if (values.length > 0) {
      return values;
    }
  }

  return [];
}

function normalizeStringList(value: unknown): string[] {
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized.length === 0 ? [] : [normalized];
  }

  if (!Array.isArray(value)) {
    return [];
  }

  return uniqueStrings(value.filter((entry): entry is string => typeof entry === 'string').map((entry) => entry.trim()).filter(Boolean));
}

function toMetadataRecord(metadata: Record<string, unknown> | undefined): Record<string, unknown> {
  return metadata === undefined ? {} : metadata;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function addIfPresent(target: Set<string>, value: string | null): void {
  if (value !== null && value.length > 0) {
    target.add(value);
  }
}

function compareTimestampedSequenceDescending(
  left: { timestamp: string; sequenceId: number },
  right: { timestamp: string; sequenceId: number }
): number {
  if (left.timestamp !== right.timestamp) {
    return right.timestamp.localeCompare(left.timestamp);
  }

  return right.sequenceId - left.sequenceId;
}