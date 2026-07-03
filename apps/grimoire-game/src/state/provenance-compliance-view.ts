import type { JsonValue } from '../contracts/events';

import type { GameState } from './game-state';

export type ProvenanceEntryKind = 'asset' | 'plugin';
export type ProvenanceComplianceStatus = 'compliant' | 'missing_source' | 'missing_license' | 'missing_attribution';

export interface ProvenanceRegistryEntry {
  entryId: string;
  kind: ProvenanceEntryKind;
  label: string;
  sourceRef: string | null;
  licenseId: string | null;
  attributionRequired: boolean;
  attributionRefs: readonly string[];
  complianceStatus: ProvenanceComplianceStatus;
  blockingReason: string | null;
}

export interface AttributionBundle {
  bundleId: string;
  entryIds: readonly string[];
  attributionRefs: readonly string[];
}

export interface ProvenanceComplianceSummary {
  entryCount: number;
  compliantCount: number;
  blockedEntryCount: number;
  missingSourceCount: number;
  missingLicenseCount: number;
  missingAttributionCount: number;
  attributionBundleCount: number;
}

export interface ProvenanceComplianceView {
  protocolVersion: string;
  lastSequenceId: number;
  entries: readonly ProvenanceRegistryEntry[];
  attributionBundles: readonly AttributionBundle[];
  shipBlocked: boolean;
  blockingReasons: readonly string[];
  summary: ProvenanceComplianceSummary;
}

export function createProvenanceComplianceView(state: GameState): ProvenanceComplianceView {
  const snapshot = readConfigValue(state.config, 'provenanceRegistry.snapshot', ['provenanceRegistry', 'snapshot']);
  const entries = collectRegistryEntries(snapshot);
  const attributionBundles = createAttributionBundles(entries);
  const blockingReasons = entries
    .map((entry) => entry.blockingReason)
    .filter((reason): reason is string => reason !== null);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    entries,
    attributionBundles,
    shipBlocked: blockingReasons.length > 0,
    blockingReasons,
    summary: {
      entryCount: entries.length,
      compliantCount: entries.filter((entry) => entry.complianceStatus === 'compliant').length,
      blockedEntryCount: entries.filter((entry) => entry.complianceStatus !== 'compliant').length,
      missingSourceCount: entries.filter((entry) => entry.complianceStatus === 'missing_source').length,
      missingLicenseCount: entries.filter((entry) => entry.complianceStatus === 'missing_license').length,
      missingAttributionCount: entries.filter((entry) => entry.complianceStatus === 'missing_attribution').length,
      attributionBundleCount: attributionBundles.length
    }
  };
}

function collectRegistryEntries(snapshot: JsonValue | undefined): ProvenanceRegistryEntry[] {
  if (!isJsonRecord(snapshot)) {
    return [];
  }

  return Object.entries(snapshot)
    .map(([entryId, rawEntry]) => toRegistryEntry(entryId, rawEntry))
    .filter((entry): entry is ProvenanceRegistryEntry => entry !== null)
    .sort((left, right) => left.entryId.localeCompare(right.entryId));
}

function toRegistryEntry(entryId: string, rawEntry: JsonValue): ProvenanceRegistryEntry | null {
  if (!isJsonRecord(rawEntry)) {
    return null;
  }

  const kind = readEntryKind(rawEntry.kind);
  const label = typeof rawEntry.label === 'string' && rawEntry.label.trim().length > 0 ? rawEntry.label : entryId;
  const sourceRef = typeof rawEntry.sourceRef === 'string' && rawEntry.sourceRef.trim().length > 0 ? rawEntry.sourceRef : null;
  const licenseId = typeof rawEntry.licenseId === 'string' && rawEntry.licenseId.trim().length > 0 ? rawEntry.licenseId : null;
  const attributionRequired = rawEntry.attributionRequired === true;
  const attributionRefs = readStringArray(rawEntry.attributionRefs);
  const complianceStatus = resolveComplianceStatus(sourceRef, licenseId, attributionRequired, attributionRefs);

  return {
    entryId,
    kind,
    label,
    sourceRef,
    licenseId,
    attributionRequired,
    attributionRefs,
    complianceStatus,
    blockingReason: createBlockingReason(label, complianceStatus)
  };
}

function createAttributionBundles(entries: readonly ProvenanceRegistryEntry[]): AttributionBundle[] {
  return entries
    .filter((entry) => entry.attributionRequired && entry.attributionRefs.length > 0)
    .map((entry) => ({
      bundleId: `attribution://${entry.entryId}`,
      entryIds: [entry.entryId],
      attributionRefs: entry.attributionRefs
    }));
}

function resolveComplianceStatus(
  sourceRef: string | null,
  licenseId: string | null,
  attributionRequired: boolean,
  attributionRefs: readonly string[]
): ProvenanceComplianceStatus {
  if (sourceRef === null) {
    return 'missing_source';
  }

  if (licenseId === null) {
    return 'missing_license';
  }

  if (attributionRequired && attributionRefs.length === 0) {
    return 'missing_attribution';
  }

  return 'compliant';
}

function createBlockingReason(label: string, status: ProvenanceComplianceStatus): string | null {
  switch (status) {
    case 'missing_source':
      return `Provenance entry ${label} is missing source reference.`;
    case 'missing_license':
      return `Provenance entry ${label} is missing license metadata.`;
    case 'missing_attribution':
      return `Provenance entry ${label} requires an attribution bundle before merge.`;
    default:
      return null;
  }
}

function readConfigValue(
  config: Record<string, JsonValue>,
  directKey: string,
  path: readonly string[]
): JsonValue | undefined {
  const directValue = config[directKey];
  if (directValue !== undefined) {
    return directValue;
  }

  let cursor: JsonValue | undefined = config;
  for (const segment of path) {
    if (!isJsonRecord(cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
    if (cursor === undefined) {
      return undefined;
    }
  }

  return cursor;
}

function readEntryKind(value: JsonValue | undefined): ProvenanceEntryKind {
  return value === 'asset' ? 'asset' : 'plugin';
}

function readStringArray(value: JsonValue | undefined): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0);
}

function isJsonRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}