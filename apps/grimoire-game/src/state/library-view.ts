import type {
  HostContextSourceType,
  HostContextTrustStatus,
  HostContextVisibility,
  HostReviewVerdict
} from '../contracts/events';

import type { GameState } from './game-state';
import { countOpenHostFindings, createHostBridgeView, type HostBridgeHostCard } from './host-bridge-view';
import {
  createSessionLineageView,
  type SessionLineageAlertCode,
  type SessionLineageNode
} from './session-lineage-view';

export const LIBRARY_SHELF_ORDER = ['hosts', 'context', 'stale', 'reviews', 'traces'] as const;

export type LibraryShelfId = (typeof LIBRARY_SHELF_ORDER)[number];

export interface LibraryHostCard extends HostBridgeHostCard {
  reviewArtifactCount: number;
  contextEntryCount: number;
  staleContextEntryCount: number;
  linkedTraceCount: number;
}

export interface LibraryContextCard {
  entryId: string;
  hostId: string;
  hostDisplayName: string | null;
  sourceType: HostContextSourceType;
  visibility: HostContextVisibility;
  trustStatus: HostContextTrustStatus;
  confidence: number;
  importedAt: string;
  expiresAt: string;
  stale: boolean;
  supersedes: string | null;
  supersededBy: string | null;
  traceId: string | null;
  taskId: string | null;
  correlationId: string | null;
  contentRef: string;
}

export interface LibraryReviewCard {
  reviewId: string;
  hostId: string;
  hostDisplayName: string | null;
  sourceType: string;
  subjectRef: string;
  verdict: HostReviewVerdict;
  findingCount: number;
  openFindingCount: number;
  importedAt: string;
  traceId: string | null;
  taskId: string | null;
  linkedEvidenceRefs: readonly string[];
}

export interface LibraryTraceVolume {
  traceId: string;
  title: string;
  runId: string;
  status: SessionLineageNode['status'];
  missionIds: readonly string[];
  taskIds: readonly string[];
  contextEntryIds: readonly string[];
  reviewIds: readonly string[];
  staleContextCount: number;
  alertCodes: readonly SessionLineageAlertCode[];
  updatedAt: string;
}

export interface LibraryShelf {
  shelfId: LibraryShelfId;
  label: string;
  count: number;
  attentionCount: number;
}

export interface LibraryViewSummary {
  hostCount: number;
  shelfCount: number;
  contextEntryCount: number;
  staleContextCount: number;
  supersededContextCount: number;
  reviewArtifactCount: number;
  openReviewFindingCount: number;
  linkedTraceCount: number;
}

export interface LibraryView {
  protocolVersion: string;
  lastSequenceId: number;
  referenceTimestamp: string;
  hosts: readonly LibraryHostCard[];
  shelves: readonly LibraryShelf[];
  contextEntries: readonly LibraryContextCard[];
  reviews: readonly LibraryReviewCard[];
  traces: readonly LibraryTraceVolume[];
  summary: LibraryViewSummary;
}

export interface LibraryQuery {
  hostId?: string;
  traceId?: string;
  taskId?: string;
  sourceType?: HostContextSourceType;
  trustStatus?: HostContextTrustStatus;
  verdict?: HostReviewVerdict;
  staleOnly?: boolean;
}

export interface LibraryQueryResult {
  contextEntries: readonly LibraryContextCard[];
  reviews: readonly LibraryReviewCard[];
  traces: readonly LibraryTraceVolume[];
  totalCount: number;
}

const LIBRARY_SHELF_LABELS: Record<LibraryShelfId, string> = {
  hosts: 'Hosts',
  context: 'Context ledger',
  stale: 'Stale memory',
  reviews: 'Imported reviews',
  traces: 'Trace volumes'
};

export function createLibraryView(state: GameState): LibraryView {
  const hostBridge = createHostBridgeView(state);
  const lineage = createSessionLineageView(state);
  const referenceTimestamp = deriveReferenceTimestamp(state, hostBridge.contextEntries, hostBridge.reviews, lineage.nodes);
  const hostDisplayNames = Object.fromEntries(hostBridge.hosts.map((host) => [host.hostId, host.displayName]));
  const supersededById = createSupersededByIdIndex(hostBridge.contextEntries);

  const contextEntries = hostBridge.contextEntries
    .map((record) => createLibraryContextCard(record, hostDisplayNames[record.entry.hostId] ?? null, supersededById, referenceTimestamp))
    .sort(compareLibraryContextCards);
  const reviews = hostBridge.reviews
    .map((record) => createLibraryReviewCard(record, hostDisplayNames[record.review.hostId] ?? null))
    .sort(compareLibraryReviewCards);
  const traces = lineage.nodes
    .map((node) => createLibraryTraceVolume(node, contextEntries, reviews, lineage.alerts))
    .filter((trace): trace is LibraryTraceVolume => trace !== null)
    .sort(compareLibraryTraceVolumes);
  const hosts = hostBridge.hosts
    .map((host) => createLibraryHostCard(host, contextEntries, reviews, traces))
    .sort(compareLibraryHostCards);

  const shelves = LIBRARY_SHELF_ORDER.map((shelfId) => createLibraryShelf(shelfId, hosts, contextEntries, reviews, traces));
  const openReviewFindingCount = reviews.reduce((count, review) => count + review.openFindingCount, 0);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    referenceTimestamp,
    hosts,
    shelves,
    contextEntries,
    reviews,
    traces,
    summary: {
      hostCount: hosts.length,
      shelfCount: shelves.length,
      contextEntryCount: contextEntries.length,
      staleContextCount: contextEntries.filter((entry) => entry.stale).length,
      supersededContextCount: contextEntries.filter((entry) => entry.supersededBy !== null).length,
      reviewArtifactCount: reviews.length,
      openReviewFindingCount,
      linkedTraceCount: traces.length
    }
  };
}

export function queryLibraryView(view: LibraryView, query: LibraryQuery = {}): LibraryQueryResult {
  const contextEntries = view.contextEntries.filter((entry) => matchesLibraryContextCard(entry, query));
  const reviews = view.reviews.filter((review) => matchesLibraryReviewCard(review, query));
  const traces = view.traces.filter((trace) => matchesLibraryTraceVolume(trace, query));

  return {
    contextEntries,
    reviews,
    traces,
    totalCount: contextEntries.length + reviews.length + traces.length
  };
}

function createLibraryContextCard(
  record: ReturnType<typeof createHostBridgeView>['contextEntries'][number],
  hostDisplayName: string | null,
  supersededById: Record<string, string>,
  referenceTimestamp: string
): LibraryContextCard {
  const expiresAt = computeExpiryTimestamp(record.entry.importedAt, record.entry.ttlSeconds);
  return {
    entryId: record.entry.entryId,
    hostId: record.entry.hostId,
    hostDisplayName,
    sourceType: record.entry.sourceType,
    visibility: record.entry.visibility,
    trustStatus: record.entry.trustStatus,
    confidence: record.entry.confidence,
    importedAt: record.entry.importedAt,
    expiresAt,
    stale: isTimestampExpired(referenceTimestamp, expiresAt),
    supersedes: record.entry.supersedes ?? null,
    supersededBy: supersededById[record.entry.entryId] ?? null,
    traceId: record.meta.traceId ?? null,
    taskId: record.meta.taskId ?? null,
    correlationId: record.meta.correlationId ?? null,
    contentRef: record.entry.contentRef
  };
}

function createLibraryReviewCard(
  record: ReturnType<typeof createHostBridgeView>['reviews'][number],
  hostDisplayName: string | null
): LibraryReviewCard {
  return {
    reviewId: record.review.reviewId,
    hostId: record.review.hostId,
    hostDisplayName,
    sourceType: record.review.sourceType,
    subjectRef: record.review.subjectRef,
    verdict: record.review.verdict,
    findingCount: record.review.findings.length,
    openFindingCount: countOpenHostFindings(record.review),
    importedAt: record.review.importedAt,
    traceId: record.review.traceId ?? record.meta.traceId ?? null,
    taskId: record.review.taskId ?? record.meta.taskId ?? null,
    linkedEvidenceRefs: record.review.linkedEvidenceRefs
  };
}

function createLibraryTraceVolume(
  node: SessionLineageNode,
  contextEntries: readonly LibraryContextCard[],
  reviews: readonly LibraryReviewCard[],
  alerts: ReturnType<typeof createSessionLineageView>['alerts']
): LibraryTraceVolume | null {
  const linkedContextEntries = contextEntries.filter((entry) => entry.traceId === node.traceId);
  const linkedReviews = reviews.filter((review) => review.traceId === node.traceId);
  const traceAlerts = alerts.filter((alert) => alert.traceId === node.traceId);

  if (linkedContextEntries.length === 0 && linkedReviews.length === 0 && traceAlerts.length === 0) {
    return null;
  }

  return {
    traceId: node.traceId,
    title: node.title,
    runId: node.runId,
    status: node.status,
    missionIds: node.missionIds,
    taskIds: node.taskIds,
    contextEntryIds: linkedContextEntries.map((entry) => entry.entryId),
    reviewIds: linkedReviews.map((review) => review.reviewId),
    staleContextCount: linkedContextEntries.filter((entry) => entry.stale).length,
    alertCodes: uniqueAlertCodes(traceAlerts.map((alert) => alert.code)),
    updatedAt: node.updatedAt
  };
}

function createLibraryHostCard(
  host: HostBridgeHostCard,
  contextEntries: readonly LibraryContextCard[],
  reviews: readonly LibraryReviewCard[],
  traces: readonly LibraryTraceVolume[]
): LibraryHostCard {
  return {
    ...host,
    reviewArtifactCount: reviews.filter((review) => review.hostId === host.hostId).length,
    contextEntryCount: contextEntries.filter((entry) => entry.hostId === host.hostId).length,
    staleContextEntryCount: contextEntries.filter((entry) => entry.hostId === host.hostId && entry.stale).length,
    linkedTraceCount: traces.filter(
      (trace) =>
        trace.contextEntryIds.some((entryId) => contextEntries.some((entry) => entry.entryId === entryId && entry.hostId === host.hostId)) ||
        trace.reviewIds.some((reviewId) => reviews.some((review) => review.reviewId === reviewId && review.hostId === host.hostId))
    ).length
  };
}

function createLibraryShelf(
  shelfId: LibraryShelfId,
  hosts: readonly LibraryHostCard[],
  contextEntries: readonly LibraryContextCard[],
  reviews: readonly LibraryReviewCard[],
  traces: readonly LibraryTraceVolume[]
): LibraryShelf {
  if (shelfId === 'hosts') {
    return {
      shelfId,
      label: LIBRARY_SHELF_LABELS[shelfId],
      count: hosts.length,
      attentionCount: hosts.filter((host) => host.connectionState !== 'online' || host.trustStatus !== 'trusted').length
    };
  }

  if (shelfId === 'context') {
    return {
      shelfId,
      label: LIBRARY_SHELF_LABELS[shelfId],
      count: contextEntries.length,
      attentionCount: contextEntries.filter((entry) => entry.trustStatus !== 'trusted').length
    };
  }

  if (shelfId === 'stale') {
    return {
      shelfId,
      label: LIBRARY_SHELF_LABELS[shelfId],
      count: contextEntries.filter((entry) => entry.stale || entry.supersededBy !== null).length,
      attentionCount: contextEntries.filter((entry) => entry.stale).length
    };
  }

  if (shelfId === 'reviews') {
    return {
      shelfId,
      label: LIBRARY_SHELF_LABELS[shelfId],
      count: reviews.length,
      attentionCount: reviews.filter((review) => review.openFindingCount > 0).length
    };
  }

  return {
    shelfId,
    label: LIBRARY_SHELF_LABELS[shelfId],
    count: traces.length,
    attentionCount: traces.filter((trace) => trace.staleContextCount > 0 || trace.alertCodes.length > 0).length
  };
}

function createSupersededByIdIndex(
  contextEntries: ReturnType<typeof createHostBridgeView>['contextEntries']
): Record<string, string> {
  const index: Record<string, string> = {};

  for (const record of contextEntries) {
    const supersedes = record.entry.supersedes;
    if (supersedes !== undefined) {
      index[supersedes] = record.entry.entryId;
    }
  }

  return index;
}

function deriveReferenceTimestamp(
  state: GameState,
  contextEntries: ReturnType<typeof createHostBridgeView>['contextEntries'],
  reviews: ReturnType<typeof createHostBridgeView>['reviews'],
  nodes: readonly SessionLineageNode[]
): string {
  const timestamps = [
    state.hydratedAt,
    ...contextEntries.map((entry) => entry.entry.importedAt),
    ...reviews.map((review) => review.review.importedAt),
    ...nodes.map((node) => node.updatedAt)
  ].filter((value): value is string => value !== null && value !== undefined);

  return timestamps.sort((left, right) => right.localeCompare(left))[0] ?? '1970-01-01T00:00:00.000Z';
}

function computeExpiryTimestamp(importedAt: string, ttlSeconds: number): string {
  const importedAtMs = Date.parse(importedAt);
  if (!Number.isFinite(importedAtMs)) {
    return importedAt;
  }

  return new Date(importedAtMs + ttlSeconds * 1000).toISOString();
}

function isTimestampExpired(referenceTimestamp: string, expiresAt: string): boolean {
  const referenceMs = Date.parse(referenceTimestamp);
  const expiresAtMs = Date.parse(expiresAt);
  return Number.isFinite(referenceMs) && Number.isFinite(expiresAtMs) && referenceMs > expiresAtMs;
}

function matchesLibraryContextCard(entry: LibraryContextCard, query: LibraryQuery): boolean {
  if (query.hostId !== undefined && entry.hostId !== query.hostId) {
    return false;
  }

  if (query.traceId !== undefined && entry.traceId !== query.traceId) {
    return false;
  }

  if (query.taskId !== undefined && entry.taskId !== query.taskId) {
    return false;
  }

  if (query.sourceType !== undefined && entry.sourceType !== query.sourceType) {
    return false;
  }

  if (query.trustStatus !== undefined && entry.trustStatus !== query.trustStatus) {
    return false;
  }

  if (query.staleOnly === true && !entry.stale) {
    return false;
  }

  return true;
}

function matchesLibraryReviewCard(review: LibraryReviewCard, query: LibraryQuery): boolean {
  if (query.hostId !== undefined && review.hostId !== query.hostId) {
    return false;
  }

  if (query.traceId !== undefined && review.traceId !== query.traceId) {
    return false;
  }

  if (query.taskId !== undefined && review.taskId !== query.taskId) {
    return false;
  }

  if (query.verdict !== undefined && review.verdict !== query.verdict) {
    return false;
  }

  return true;
}

function matchesLibraryTraceVolume(trace: LibraryTraceVolume, query: LibraryQuery): boolean {
  if (query.traceId !== undefined && trace.traceId !== query.traceId) {
    return false;
  }

  if (query.taskId !== undefined && !trace.taskIds.includes(query.taskId)) {
    return false;
  }

  if (query.staleOnly === true && trace.staleContextCount === 0) {
    return false;
  }

  return true;
}

function compareLibraryContextCards(left: LibraryContextCard, right: LibraryContextCard): number {
  const staleDelta = Number(right.stale) - Number(left.stale);
  if (staleDelta !== 0) {
    return staleDelta;
  }

  if (left.importedAt !== right.importedAt) {
    return right.importedAt.localeCompare(left.importedAt);
  }

  return left.entryId.localeCompare(right.entryId);
}

function compareLibraryReviewCards(left: LibraryReviewCard, right: LibraryReviewCard): number {
  if (left.openFindingCount !== right.openFindingCount) {
    return right.openFindingCount - left.openFindingCount;
  }

  if (left.importedAt !== right.importedAt) {
    return right.importedAt.localeCompare(left.importedAt);
  }

  return left.reviewId.localeCompare(right.reviewId);
}

function compareLibraryTraceVolumes(left: LibraryTraceVolume, right: LibraryTraceVolume): number {
  if (left.staleContextCount !== right.staleContextCount) {
    return right.staleContextCount - left.staleContextCount;
  }

  if (left.alertCodes.length !== right.alertCodes.length) {
    return right.alertCodes.length - left.alertCodes.length;
  }

  if (left.updatedAt !== right.updatedAt) {
    return right.updatedAt.localeCompare(left.updatedAt);
  }

  return left.traceId.localeCompare(right.traceId);
}

function compareLibraryHostCards(left: LibraryHostCard, right: LibraryHostCard): number {
  if (left.staleContextEntryCount !== right.staleContextEntryCount) {
    return right.staleContextEntryCount - left.staleContextEntryCount;
  }

  if (left.contextEntryCount !== right.contextEntryCount) {
    return right.contextEntryCount - left.contextEntryCount;
  }

  return left.displayName.localeCompare(right.displayName);
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function uniqueAlertCodes(values: readonly SessionLineageAlertCode[]): SessionLineageAlertCode[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}