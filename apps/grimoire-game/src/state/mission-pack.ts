import type { GameState } from './game-state';

export interface MissionPackSnapshot {
  objective: string;
  scope: readonly string[];
  canonicalSourceRefs: readonly string[];
  constraints: readonly string[];
  expectedOutput: string | null;
  expectedProofRefs: readonly string[];
  mode: string | null;
  recordedAt: string;
  traceId: string | null;
  sourceStep: string;
}

export function createMissionPackByTaskId(state: GameState): Record<string, MissionPackSnapshot> {
  const missionPackByTaskId: Record<string, MissionPackSnapshot> = {};
  const orderedSteps = [...state.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId);

  for (const workflowStep of orderedSteps) {
    if (workflowStep.taskId === undefined) {
      continue;
    }

    if (missionPackByTaskId[workflowStep.taskId] !== undefined) {
      continue;
    }

    const missionPack = createMissionPackSnapshot(workflowStep);
    if (missionPack !== null) {
      missionPackByTaskId[workflowStep.taskId] = missionPack;
    }
  }

  return missionPackByTaskId;
}

export function createMissionPackSnapshot(
  workflowStep: GameState['recentWorkflowSteps'][number]
): MissionPackSnapshot | null {
  const metadata = asRecord(workflowStep.metadata);
  const nestedMissionPack = asNullableRecord(metadata.missionPack) ?? asNullableRecord(metadata.mission_pack);
  const sourceRecord = nestedMissionPack ?? metadata;

  if (!hasMissionPackSignal(metadata, nestedMissionPack)) {
    return null;
  }

  const objective =
    readMetadataStringByKeys(sourceRecord, ['objective', 'objectif']) ??
    (nestedMissionPack === null ? readMetadataStringByKeys(metadata, ['intent']) : null) ??
    workflowStep.detail;
  const scope = readMetadataStringListByKeys(sourceRecord, ['scope']);
  const canonicalSourceRefs = readMetadataStringListByKeys(sourceRecord, [
    'sources_canoniques',
    'canonicalSources',
    'canonical_sources',
    'sourceRefs'
  ]);
  const constraints = readMetadataStringListByKeys(sourceRecord, ['contraintes', 'constraints']);
  const expectedOutput = readMetadataStringByKeys(sourceRecord, [
    'sortie_attendue',
    'expectedOutput',
    'expected_output'
  ]);
  const expectedProofRefs = uniqueStrings(
    readMetadataStringListByKeys(sourceRecord, [
      'preuve_attendue',
      'expectedProof',
      'expected_proof',
      'expectedProofRefs',
      'expected_proof_refs'
    ]).map(normalizeMissionProofRef)
  );
  const mode = readMetadataStringByKeys(sourceRecord, ['mode']);

  return {
    objective,
    scope,
    canonicalSourceRefs,
    constraints,
    expectedOutput,
    expectedProofRefs,
    mode,
    recordedAt: workflowStep.timestamp,
    traceId: workflowStep.traceId ?? null,
    sourceStep: workflowStep.step
  };
}

function hasMissionPackSignal(
  metadata: Record<string, unknown>,
  nestedMissionPack: Record<string, unknown> | null
): boolean {
  if (nestedMissionPack !== null) {
    return true;
  }

  return [
    'objective',
    'objectif',
    'scope',
    'sources_canoniques',
    'canonicalSources',
    'canonical_sources',
    'contraintes',
    'constraints',
    'sortie_attendue',
    'expectedOutput',
    'expected_output',
    'preuve_attendue',
    'expectedProof',
    'expected_proof',
    'expectedProofRefs',
    'expected_proof_refs',
    'mode'
  ].some((key) => metadata[key] !== undefined);
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asNullableRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readMetadataStringByKeys(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.length > 0) {
      return value;
    }
  }

  return null;
}

function readMetadataStringListByKeys(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.length > 0) {
      return [value];
    }

    if (Array.isArray(value)) {
      const values = value.filter((entry): entry is string => typeof entry === 'string' && entry.length > 0);
      if (values.length > 0) {
        return uniqueStrings(values);
      }
    }
  }

  return [];
}

function normalizeMissionProofRef(value: string): string {
  if (value.includes('://') || value.startsWith('attestation:') || value.startsWith('action:')) {
    return value;
  }

  if (value.includes(':')) {
    return `control://${value}`;
  }

  return value;
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort((left, right) => left.localeCompare(right));
}