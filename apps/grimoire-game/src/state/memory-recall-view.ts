import type { TaskSnapshot } from '../contracts/events';

import type { GameState } from './game-state';
import {
  createLibraryMemoryView,
  type LibraryMemoryAccess,
  type LibraryMemoryReference,
  type LibraryMemoryView
} from './library-memory-view';

export const MEMORY_RECALL_FRESHNESS_ORDER = ['fresh', 'stale', 'superseded', 'obsolete'] as const;
export const MEMORY_RECALL_LOCATION_TYPE_ORDER = ['review', 'challenge', 'workflow', 'tool'] as const;
export const DEFAULT_MEMORY_RECALL_OBSOLESCENCE_THRESHOLD = 0.25;

export type MemoryRecallFreshness = (typeof MEMORY_RECALL_FRESHNESS_ORDER)[number];
export type MemoryRecallLocationType = (typeof MEMORY_RECALL_LOCATION_TYPE_ORDER)[number];
export type MemoryRecallObsolescenceReason = 'expired' | 'superseded';
export type MemoryRecallResolutionMode = 'direct' | 'inferred' | 'unresolved';

export interface MemoryRecallFreshnessMarker {
  freshness: MemoryRecallFreshness;
  obsolescenceReasons: readonly MemoryRecallObsolescenceReason[];
  ttlRemainingSeconds: number | null;
  expiredBySeconds: number | null;
}

export interface MemoryRecallReference extends LibraryMemoryReference {
  freshnessMarker: MemoryRecallFreshnessMarker;
}

export interface MemoryRecallAccess extends LibraryMemoryAccess {
  resolutionMode: MemoryRecallResolutionMode;
  resolvedReferenceEntryIds: readonly string[];
  freshnessMarker: MemoryRecallFreshnessMarker;
  obsoleteReferenceEntryIds: readonly string[];
  obsoleteContentRefs: readonly string[];
}

export interface MemoryRecallObsolescenceFinding {
  findingId: string;
  taskId: string | null;
  taskTitle: string | null;
  traceId: string | null;
  accessId: string;
  sequenceId: number | null;
  locationType: MemoryRecallLocationType;
  sourceEventType: string;
  title: string;
  detail: string;
  freshness: MemoryRecallFreshness;
  obsolescenceReasons: readonly MemoryRecallObsolescenceReason[];
  contentRefs: readonly string[];
  referenceEntryIds: readonly string[];
}

export interface MemoryRecallMetrics {
  sampleSize: number;
  resolvedReadCount: number;
  preciseReadCount: number;
  unresolvedReadCount: number;
  obsoleteReadCount: number;
  precision: number;
  recall: number;
  obsolescenceRate: number;
  referenceCount: number;
  obsoleteReferenceCount: number;
  staleReferenceCount: number;
  supersededReferenceCount: number;
}

export interface MemoryRecallPeriodicReport {
  generatedAt: string;
  windowStartedAt: string | null;
  windowEndedAt: string | null;
  threshold: number;
  precision: number;
  recall: number;
  obsolescenceRate: number;
  sampleSize: number;
}

export interface MemoryRecallTaskGate {
  taskId: string;
  taskTitle: string;
  threshold: number;
  readCount: number;
  resolvedReadCount: number;
  obsoleteReadCount: number;
  obsolescenceRate: number;
  blocked: boolean;
  blockingFindingIds: readonly string[];
}

export interface MemoryRecallView {
  protocolVersion: string;
  lastSequenceId: number;
  generatedAt: string;
  references: readonly MemoryRecallReference[];
  accesses: readonly MemoryRecallAccess[];
  findings: readonly MemoryRecallObsolescenceFinding[];
  taskGates: readonly MemoryRecallTaskGate[];
  metrics: MemoryRecallMetrics;
  periodicReport: MemoryRecallPeriodicReport;
}

export interface MemoryRecallViewOptions {
  obsolescenceThreshold?: number;
}

export function createMemoryRecallView(state: GameState, options: MemoryRecallViewOptions = {}): MemoryRecallView {
  const libraryMemory = createLibraryMemoryView(state);
  const threshold = resolveMemoryRecallObsolescenceThreshold(state, options);
  const references = createMemoryRecallReferences(libraryMemory);
  const referenceIdsByContentRef = createReferenceIdsByContentRefIndex(references);
  const referencesById = new Map(references.map((reference) => [reference.entryId, reference]));
  const accesses = libraryMemory.accesses
    .map((access) => createMemoryRecallAccess(access, referencesById, referenceIdsByContentRef, libraryMemory.referenceTimestamp))
    .sort(compareMemoryRecallAccesses);
  const findings = accesses
    .filter((access) => access.accessType === 'read' && access.obsoleteReferenceEntryIds.length > 0)
    .map((access) => createMemoryRecallFinding(access, state.tasks[access.taskId ?? ''] ?? null))
    .sort(compareMemoryRecallFindings);
  const metrics = createMemoryRecallMetrics(references, accesses);
  const taskGates = Object.values(state.tasks)
    .map((task) => createMemoryRecallTaskGate(task, accesses, findings, threshold))
    .sort(compareMemoryRecallTaskGates);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    generatedAt: libraryMemory.referenceTimestamp,
    references,
    accesses,
    findings,
    taskGates,
    metrics,
    periodicReport: {
      generatedAt: libraryMemory.referenceTimestamp,
      windowStartedAt: accesses.length === 0 ? null : accesses[accesses.length - 1]?.timestamp ?? null,
      windowEndedAt: accesses.length === 0 ? null : accesses[0]?.timestamp ?? null,
      threshold,
      precision: metrics.precision,
      recall: metrics.recall,
      obsolescenceRate: metrics.obsolescenceRate,
      sampleSize: metrics.sampleSize
    }
  };
}

export function evaluateTaskMemoryRecallGate(
  state: GameState,
  taskId: string,
  options: MemoryRecallViewOptions = {}
): MemoryRecallTaskGate | null {
  const task = state.tasks[taskId];
  if (task === undefined) {
    return null;
  }

  const view = createMemoryRecallView(state, options);
  return view.taskGates.find((gate) => gate.taskId === taskId) ?? null;
}

function createMemoryRecallReferences(libraryMemory: LibraryMemoryView): MemoryRecallReference[] {
  return libraryMemory.references
    .map((reference) => ({
      ...reference,
      freshnessMarker: createReferenceFreshnessMarker(reference, libraryMemory.referenceTimestamp)
    }))
    .sort(compareMemoryRecallReferences);
}

function createReferenceIdsByContentRefIndex(
  references: readonly MemoryRecallReference[]
): Map<string, readonly string[]> {
  const index = new Map<string, string[]>();

  for (const reference of references) {
    const current = index.get(reference.contentRef) ?? [];
    current.push(reference.entryId);
    index.set(reference.contentRef, current);
  }

  return new Map(
    Array.from(index.entries()).map(([contentRef, entryIds]) => [
      contentRef,
      [...new Set(entryIds)].sort((left, right) => left.localeCompare(right))
    ])
  );
}

function createMemoryRecallAccess(
  access: LibraryMemoryAccess,
  referencesById: Map<string, MemoryRecallReference>,
  referenceIdsByContentRef: Map<string, readonly string[]>,
  referenceTimestamp: string
): MemoryRecallAccess {
  const resolvedReferenceEntryIds = uniqueStrings(
    access.contentRefs.flatMap((contentRef) => referenceIdsByContentRef.get(contentRef) ?? [])
  );
  const resolvedReferences = resolvedReferenceEntryIds
    .map((entryId) => referencesById.get(entryId))
    .filter((reference): reference is MemoryRecallReference => reference !== undefined);
  const obsoleteReferences = resolvedReferences.filter((reference) => reference.freshnessMarker.freshness !== 'fresh');

  return {
    ...access,
    resolutionMode:
      resolvedReferenceEntryIds.length > 0
        ? 'direct'
        : access.referenceEntryIds.length > 0
          ? 'inferred'
          : 'unresolved',
    resolvedReferenceEntryIds,
    freshnessMarker: createAggregateFreshnessMarker(resolvedReferences, referenceTimestamp),
    obsoleteReferenceEntryIds: obsoleteReferences.map((reference) => reference.entryId),
    obsoleteContentRefs: obsoleteReferences.map((reference) => reference.contentRef)
  };
}

function createMemoryRecallFinding(
  access: MemoryRecallAccess,
  task: TaskSnapshot | null
): MemoryRecallObsolescenceFinding {
  const referenceLabel = access.obsoleteContentRefs.join(', ');
  const locationType = classifyMemoryRecallLocation(access);

  return {
    findingId: `memory-recall:${access.accessId}`,
    taskId: access.taskId,
    taskTitle: task?.title ?? null,
    traceId: access.traceId,
    accessId: access.accessId,
    sequenceId: access.sequenceId,
    locationType,
    sourceEventType: access.sourceEventType,
    title: `Obsolete memory reference in ${locationType}`,
    detail:
      referenceLabel.length === 0
        ? `Memory access ${access.accessId} relies on obsolete references.`
        : `Memory access ${access.accessId} relies on obsolete references: ${referenceLabel}.`,
    freshness: access.freshnessMarker.freshness,
    obsolescenceReasons: access.freshnessMarker.obsolescenceReasons,
    contentRefs: access.contentRefs,
    referenceEntryIds: access.obsoleteReferenceEntryIds
  };
}

function createMemoryRecallMetrics(
  references: readonly MemoryRecallReference[],
  accesses: readonly MemoryRecallAccess[]
): MemoryRecallMetrics {
  const readAccesses = accesses.filter((access) => access.accessType === 'read');
  const resolvedReadCount = readAccesses.filter((access) => access.resolutionMode === 'direct').length;
  const preciseReadCount = readAccesses.filter(
    (access) => access.resolutionMode === 'direct' && access.obsoleteReferenceEntryIds.length === 0
  ).length;
  const obsoleteReadCount = readAccesses.filter((access) => access.obsoleteReferenceEntryIds.length > 0).length;
  const unresolvedReadCount = readAccesses.filter((access) => access.resolutionMode !== 'direct').length;
  const obsoleteReferenceCount = references.filter((reference) => reference.freshnessMarker.freshness !== 'fresh').length;
  const staleReferenceCount = references.filter((reference) =>
    reference.freshnessMarker.obsolescenceReasons.includes('expired')
  ).length;
  const supersededReferenceCount = references.filter((reference) =>
    reference.freshnessMarker.obsolescenceReasons.includes('superseded')
  ).length;

  return {
    sampleSize: readAccesses.length,
    resolvedReadCount,
    preciseReadCount,
    unresolvedReadCount,
    obsoleteReadCount,
    precision: resolvedReadCount === 0 ? 1 : preciseReadCount / resolvedReadCount,
    recall: readAccesses.length === 0 ? 1 : resolvedReadCount / readAccesses.length,
    obsolescenceRate: readAccesses.length === 0 ? 0 : obsoleteReadCount / readAccesses.length,
    referenceCount: references.length,
    obsoleteReferenceCount,
    staleReferenceCount,
    supersededReferenceCount
  };
}

function createMemoryRecallTaskGate(
  task: TaskSnapshot,
  accesses: readonly MemoryRecallAccess[],
  findings: readonly MemoryRecallObsolescenceFinding[],
  threshold: number
): MemoryRecallTaskGate {
  const taskReadAccesses = accesses.filter((access) => access.accessType === 'read' && access.taskId === task.id);
  const resolvedReadCount = taskReadAccesses.filter((access) => access.resolutionMode === 'direct').length;
  const obsoleteReadCount = taskReadAccesses.filter((access) => access.obsoleteReferenceEntryIds.length > 0).length;
  const obsolescenceRate = taskReadAccesses.length === 0 ? 0 : obsoleteReadCount / taskReadAccesses.length;
  const blocked = taskReadAccesses.length > 0 && obsolescenceRate > threshold;

  return {
    taskId: task.id,
    taskTitle: task.title,
    threshold,
    readCount: taskReadAccesses.length,
    resolvedReadCount,
    obsoleteReadCount,
    obsolescenceRate,
    blocked,
    blockingFindingIds: blocked
      ? findings.filter((finding) => finding.taskId === task.id).map((finding) => finding.findingId)
      : []
  };
}

function createReferenceFreshnessMarker(
  reference: LibraryMemoryReference,
  referenceTimestamp: string
): MemoryRecallFreshnessMarker {
  const reasons: MemoryRecallObsolescenceReason[] = [];
  if (reference.stale) {
    reasons.push('expired');
  }
  if (reference.supersededBy !== null) {
    reasons.push('superseded');
  }

  return {
    freshness: freshnessFromReasons(reasons),
    obsolescenceReasons: reasons,
    ttlRemainingSeconds: computeTtlRemainingSeconds(reference.expiresAt, referenceTimestamp),
    expiredBySeconds: computeExpiredBySeconds(reference.expiresAt, referenceTimestamp)
  };
}

function createAggregateFreshnessMarker(
  references: readonly MemoryRecallReference[],
  referenceTimestamp: string
): MemoryRecallFreshnessMarker {
  if (references.length === 0) {
    return {
      freshness: 'fresh',
      obsolescenceReasons: [],
      ttlRemainingSeconds: null,
      expiredBySeconds: null
    };
  }

  const reasons = uniqueReasons(references.flatMap((reference) => reference.freshnessMarker.obsolescenceReasons));
  const ttlRemainingSeconds = minNumber(
    references
      .map((reference) => reference.freshnessMarker.ttlRemainingSeconds)
      .filter((value): value is number => value !== null)
  );
  const expiredBySeconds = maxNumber(
    references
      .map((reference) => reference.freshnessMarker.expiredBySeconds)
      .filter((value): value is number => value !== null)
  );

  return {
    freshness: reasons.length === 0 ? 'fresh' : freshnessFromReasons(reasons),
    obsolescenceReasons: reasons,
    ttlRemainingSeconds,
    expiredBySeconds
  };
}

function classifyMemoryRecallLocation(access: MemoryRecallAccess): MemoryRecallLocationType {
  if (access.sourceEventType.startsWith('challenge_')) {
    return 'challenge';
  }

  if (access.sourceEventType === 'review' || access.sourceEventType.includes('review')) {
    return 'review';
  }

  if (access.toolName !== null) {
    return 'tool';
  }

  return 'workflow';
}

function resolveMemoryRecallObsolescenceThreshold(
  state: GameState,
  options: MemoryRecallViewOptions
): number {
  return normalizeThreshold(
    options.obsolescenceThreshold ?? readOptionalNumber(state.config['memory.recall.obsolescenceThreshold'])
  );
}

function normalizeThreshold(value: number | null | undefined): number {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return DEFAULT_MEMORY_RECALL_OBSOLESCENCE_THRESHOLD;
  }

  if (value > 1 && value <= 100) {
    return value / 100;
  }

  if (value <= 0) {
    return 0;
  }

  if (value >= 1) {
    return 1;
  }

  return value;
}

function computeTtlRemainingSeconds(expiresAt: string, referenceTimestamp: string): number | null {
  const expiresAtMs = Date.parse(expiresAt);
  const referenceTimestampMs = Date.parse(referenceTimestamp);
  if (!Number.isFinite(expiresAtMs) || !Number.isFinite(referenceTimestampMs)) {
    return null;
  }

  return Math.max(0, Math.floor((expiresAtMs - referenceTimestampMs) / 1000));
}

function computeExpiredBySeconds(expiresAt: string, referenceTimestamp: string): number | null {
  const expiresAtMs = Date.parse(expiresAt);
  const referenceTimestampMs = Date.parse(referenceTimestamp);
  if (!Number.isFinite(expiresAtMs) || !Number.isFinite(referenceTimestampMs) || referenceTimestampMs <= expiresAtMs) {
    return null;
  }

  return Math.floor((referenceTimestampMs - expiresAtMs) / 1000);
}

function freshnessFromReasons(reasons: readonly MemoryRecallObsolescenceReason[]): MemoryRecallFreshness {
  const hasExpired = reasons.includes('expired');
  const hasSuperseded = reasons.includes('superseded');

  if (hasExpired && hasSuperseded) {
    return 'obsolete';
  }

  if (hasExpired) {
    return 'stale';
  }

  if (hasSuperseded) {
    return 'superseded';
  }

  return 'fresh';
}

function compareMemoryRecallReferences(left: MemoryRecallReference, right: MemoryRecallReference): number {
  const freshnessDelta = compareFreshness(left.freshnessMarker.freshness, right.freshnessMarker.freshness);
  if (freshnessDelta !== 0) {
    return freshnessDelta;
  }

  if (left.importedAt !== right.importedAt) {
    return right.importedAt.localeCompare(left.importedAt);
  }

  return left.entryId.localeCompare(right.entryId);
}

function compareMemoryRecallAccesses(left: MemoryRecallAccess, right: MemoryRecallAccess): number {
  const freshnessDelta = compareFreshness(left.freshnessMarker.freshness, right.freshnessMarker.freshness);
  if (freshnessDelta !== 0) {
    return freshnessDelta;
  }

  if (left.timestamp !== right.timestamp) {
    return right.timestamp.localeCompare(left.timestamp);
  }

  return left.accessId.localeCompare(right.accessId);
}

function compareMemoryRecallFindings(left: MemoryRecallObsolescenceFinding, right: MemoryRecallObsolescenceFinding): number {
  const freshnessDelta = compareFreshness(left.freshness, right.freshness);
  if (freshnessDelta !== 0) {
    return freshnessDelta;
  }

  if (left.sequenceId !== right.sequenceId) {
    return (right.sequenceId ?? -1) - (left.sequenceId ?? -1);
  }

  return left.findingId.localeCompare(right.findingId);
}

function compareMemoryRecallTaskGates(left: MemoryRecallTaskGate, right: MemoryRecallTaskGate): number {
  if (left.blocked !== right.blocked) {
    return left.blocked ? -1 : 1;
  }

  if (left.obsolescenceRate !== right.obsolescenceRate) {
    return right.obsolescenceRate - left.obsolescenceRate;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}

function compareFreshness(left: MemoryRecallFreshness, right: MemoryRecallFreshness): number {
  return MEMORY_RECALL_FRESHNESS_ORDER.indexOf(right) - MEMORY_RECALL_FRESHNESS_ORDER.indexOf(left);
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function uniqueReasons(values: readonly MemoryRecallObsolescenceReason[]): MemoryRecallObsolescenceReason[] {
  return [...new Set(values)];
}

function minNumber(values: readonly number[]): number | null {
  if (values.length === 0) {
    return null;
  }

  return Math.min(...values);
}

function maxNumber(values: readonly number[]): number | null {
  if (values.length === 0) {
    return null;
  }

  return Math.max(...values);
}

function readOptionalNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}