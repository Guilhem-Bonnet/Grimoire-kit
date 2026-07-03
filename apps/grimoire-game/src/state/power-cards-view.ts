import type { JsonValue, MutationPolicy, MutationProvenanceSource, MutationTrustLevel } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';
import {
  createSurfaceGovernanceView,
  type ExecutionSurfaceConfigurationCard,
  type ExecutionSurfaceRiskClass
} from './surface-governance-view';

export const POWER_CARD_ISSUE_ORDER = [
  'POWER_CARD_INVALID_RUNTIME_SNAPSHOT',
  'POWER_CARD_INVALID_STORAGE_SNAPSHOT',
  'POWER_CARD_PERSISTENCE_DIVERGENCE',
  'POWER_CARD_GOVERNANCE_BLOCKED',
  'POWER_CARD_ACTIVATION_REJECTED'
] as const;

export type PowerCardIssueCode = (typeof POWER_CARD_ISSUE_ORDER)[number];
export type PowerCardTargetKind = 'agent' | 'room';

export interface PowerCardActivationRecord {
  activationId: string;
  cardId: string;
  requestedEnabled: boolean | null;
  allowed: boolean;
  reason: string | null;
  actorId: string | null;
  sequenceId: number;
  timestamp: string;
  traceId: string | null;
  taskId: string | null;
}

export interface PowerCard {
  cardId: string;
  pluginId: string;
  label: string;
  targetKind: PowerCardTargetKind;
  targetId: string;
  runtimeEnabled: boolean;
  storageEnabled: boolean;
  effectiveEnabled: boolean;
  persistenceStatus: 'synced' | 'runtime_only' | 'storage_only' | 'invalid';
  origin: MutationProvenanceSource;
  requiredPolicy: MutationPolicy;
  trustStatus: MutationTrustLevel;
  riskClass: ExecutionSurfaceRiskClass;
  issueCodes: readonly PowerCardIssueCode[];
  diagnostic: string | null;
  lastActivation: PowerCardActivationRecord | null;
}

export interface PowerCardsViewSummary {
  cardCount: number;
  enabledRuntimeCount: number;
  enabledStorageCount: number;
  blockedCount: number;
  divergedCount: number;
  invalidCount: number;
  rejectedActivationCount: number;
}

export interface PowerCardsView {
  protocolVersion: string;
  lastSequenceId: number;
  cards: readonly PowerCard[];
  activations: readonly PowerCardActivationRecord[];
  summary: PowerCardsViewSummary;
}

interface PowerCardBlueprint {
  cardId: string;
  pluginId: string;
  label: string;
  targetKind: PowerCardTargetKind;
  targetId: string;
  riskClass: ExecutionSurfaceRiskClass;
}

interface PowerCardGovernanceOverride {
  origin?: MutationProvenanceSource;
  requiredPolicy?: MutationPolicy;
  trustStatus?: MutationTrustLevel;
  riskClass?: ExecutionSurfaceRiskClass;
}

interface PowerCardState {
  enabled: boolean;
  diagnostics: string[];
}

const POWER_CARD_BLUEPRINTS: readonly PowerCardBlueprint[] = [
  {
    cardId: 'power-card.host-review',
    pluginId: 'plugin.host-review',
    label: 'Host Review Relay',
    targetKind: 'room',
    targetId: 'challenge-room',
    riskClass: 'high'
  },
  {
    cardId: 'power-card.branch-guard',
    pluginId: 'plugin.branch-guard',
    label: 'Branch Guard',
    targetKind: 'agent',
    targetId: 'orch-1',
    riskClass: 'critical'
  }
] as const;

export function createPowerCardsView(state: GameState): PowerCardsView {
  const runtimeSnapshot = readConfigValue(state.config, 'powerCards.runtimeSnapshot', ['powerCards', 'runtimeSnapshot']);
  const storageSnapshot = readConfigValue(state.config, 'powerCards.storageSnapshot', ['powerCards', 'storageSnapshot']);
  const governanceOverrides = readGovernanceOverrides(
    readConfigValue(state.config, 'powerCards.cardGovernance', ['powerCards', 'cardGovernance'])
  );
  const surfaceGovernance = createSurfaceGovernanceView(state);
  const defaultGovernance =
    surfaceGovernance.configurationCards.find((card) => card.surfaceId === 'power-card.activate') ??
    createFallbackGovernanceCard();
  const activations = collectPowerCardActivations(state.recentWorkflowSteps);
  const latestActivationByCardId = createLatestActivationByCardId(activations);
  const cards = POWER_CARD_BLUEPRINTS.map((blueprint) =>
    createPowerCard(
      blueprint,
      runtimeSnapshot,
      storageSnapshot,
      governanceOverrides[blueprint.cardId] ?? {},
      defaultGovernance,
      latestActivationByCardId[blueprint.cardId] ?? null
    )
  );

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    cards,
    activations,
    summary: {
      cardCount: cards.length,
      enabledRuntimeCount: cards.filter((card) => card.runtimeEnabled).length,
      enabledStorageCount: cards.filter((card) => card.storageEnabled).length,
      blockedCount: cards.filter((card) => card.issueCodes.includes('POWER_CARD_GOVERNANCE_BLOCKED')).length,
      divergedCount: cards.filter((card) => card.issueCodes.includes('POWER_CARD_PERSISTENCE_DIVERGENCE')).length,
      invalidCount: cards.filter(
        (card) =>
          card.issueCodes.includes('POWER_CARD_INVALID_RUNTIME_SNAPSHOT') ||
          card.issueCodes.includes('POWER_CARD_INVALID_STORAGE_SNAPSHOT')
      ).length,
      rejectedActivationCount: activations.filter((activation) => !activation.allowed).length
    }
  };
}

function createPowerCard(
  blueprint: PowerCardBlueprint,
  runtimeSnapshot: JsonValue | undefined,
  storageSnapshot: JsonValue | undefined,
  governanceOverride: PowerCardGovernanceOverride,
  defaultGovernance: ExecutionSurfaceConfigurationCard,
  lastActivation: PowerCardActivationRecord | null
): PowerCard {
  const runtimeState = readPowerCardState(runtimeSnapshot, 'powerCards.runtimeSnapshot', blueprint.cardId);
  const storageState = readPowerCardState(storageSnapshot, 'powerCards.storageSnapshot', blueprint.cardId);
  const origin = governanceOverride.origin ?? defaultGovernance.origin;
  const requiredPolicy = governanceOverride.requiredPolicy ?? defaultGovernance.requiredPolicy;
  const trustStatus = governanceOverride.trustStatus ?? defaultGovernance.trustStatus;
  const riskClass = governanceOverride.riskClass ?? blueprint.riskClass;
  const issueCodes: PowerCardIssueCode[] = [
    ...(runtimeState.diagnostics.length > 0 ? ['POWER_CARD_INVALID_RUNTIME_SNAPSHOT' as const] : []),
    ...(storageState.diagnostics.length > 0 ? ['POWER_CARD_INVALID_STORAGE_SNAPSHOT' as const] : []),
    ...(runtimeState.diagnostics.length === 0 &&
    storageState.diagnostics.length === 0 &&
    runtimeState.enabled !== storageState.enabled
      ? ['POWER_CARD_PERSISTENCE_DIVERGENCE' as const]
      : []),
    ...(trustStatus === 'blocked' && (runtimeState.enabled || storageState.enabled)
      ? ['POWER_CARD_GOVERNANCE_BLOCKED' as const]
      : []),
    ...(lastActivation !== null && !lastActivation.allowed ? ['POWER_CARD_ACTIVATION_REJECTED' as const] : [])
  ];

  return {
    cardId: blueprint.cardId,
    pluginId: blueprint.pluginId,
    label: blueprint.label,
    targetKind: blueprint.targetKind,
    targetId: blueprint.targetId,
    runtimeEnabled: runtimeState.enabled,
    storageEnabled: storageState.enabled,
    effectiveEnabled: runtimeState.enabled,
    persistenceStatus:
      runtimeState.diagnostics.length > 0 || storageState.diagnostics.length > 0
        ? 'invalid'
        : runtimeState.enabled === storageState.enabled
          ? 'synced'
          : runtimeState.enabled
            ? 'runtime_only'
            : 'storage_only',
    origin,
    requiredPolicy,
    trustStatus,
    riskClass,
    issueCodes: uniqueIssueCodes(issueCodes),
    diagnostic: createCardDiagnostic(blueprint.label, issueCodes, runtimeState.diagnostics, storageState.diagnostics, trustStatus, lastActivation),
    lastActivation
  };
}

function collectPowerCardActivations(recentWorkflowSteps: readonly WorkflowStepLogEntry[]): PowerCardActivationRecord[] {
  return [...recentWorkflowSteps]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .map((workflowStep) => {
      const metadata = asJsonRecord(workflowStep.metadata);
      const cardId = readMetadataString(metadata, ['powerCardId', 'power_card_id', 'cardId']);
      if (cardId === null) {
        return null;
      }

      const validationError = readMetadataString(metadata, ['validationError', 'validation_error', 'reason']);
      const allowed = (readMetadataBoolean(metadata, ['allowed']) ?? true) && validationError === null;
      return {
        activationId:
          readMetadataString(metadata, ['activationId', 'activation_id']) ??
          `power-card-activation:${workflowStep.sequenceId}`,
        cardId,
        requestedEnabled: readMetadataBoolean(metadata, ['enabled', 'nextEnabled', 'requestedEnabled']),
        allowed,
        reason: validationError,
        actorId: readMetadataString(metadata, ['actorId', 'actor_id']),
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp,
        traceId: workflowStep.traceId ?? null,
        taskId: workflowStep.taskId ?? null
      } satisfies PowerCardActivationRecord;
    })
    .filter((activation): activation is PowerCardActivationRecord => activation !== null);
}

function createLatestActivationByCardId(
  activations: readonly PowerCardActivationRecord[]
): Record<string, PowerCardActivationRecord | undefined> {
  const latestByCardId: Record<string, PowerCardActivationRecord | undefined> = {};

  for (const activation of activations) {
    if (latestByCardId[activation.cardId] === undefined) {
      latestByCardId[activation.cardId] = activation;
    }
  }

  return latestByCardId;
}

function readPowerCardState(snapshot: JsonValue | undefined, rootKey: string, cardId: string): PowerCardState {
  if (snapshot === undefined) {
    return { enabled: false, diagnostics: [] };
  }

  if (!isJsonRecord(snapshot)) {
    return {
      enabled: false,
      diagnostics: [`${rootKey} must be an object.`]
    };
  }

  const card = snapshot[cardId];
  if (card === undefined) {
    return { enabled: false, diagnostics: [] };
  }

  if (!isJsonRecord(card)) {
    return {
      enabled: false,
      diagnostics: [`${rootKey}.${cardId} must be an object.`]
    };
  }

  const enabled = card.enabled;
  if (enabled === undefined) {
    return { enabled: false, diagnostics: [] };
  }

  if (typeof enabled !== 'boolean') {
    return {
      enabled: false,
      diagnostics: [`${rootKey}.${cardId}.enabled must be boolean.`]
    };
  }

  return { enabled, diagnostics: [] };
}

function createCardDiagnostic(
  label: string,
  issueCodes: readonly PowerCardIssueCode[],
  runtimeDiagnostics: readonly string[],
  storageDiagnostics: readonly string[],
  trustStatus: MutationTrustLevel,
  lastActivation: PowerCardActivationRecord | null
): string | null {
  if (runtimeDiagnostics.length > 0) {
    return runtimeDiagnostics[0] ?? null;
  }

  if (storageDiagnostics.length > 0) {
    return storageDiagnostics[0] ?? null;
  }

  if (issueCodes.includes('POWER_CARD_PERSISTENCE_DIVERGENCE')) {
    return `Power card ${label} diverges between runtime and storage snapshots.`;
  }

  if (issueCodes.includes('POWER_CARD_GOVERNANCE_BLOCKED')) {
    return `Power card ${label} is blocked by trust status ${trustStatus}.`;
  }

  if (issueCodes.includes('POWER_CARD_ACTIVATION_REJECTED')) {
    return lastActivation?.reason ?? `Power card ${label} activation was rejected.`;
  }

  return null;
}

function readGovernanceOverrides(snapshot: JsonValue | undefined): Record<string, PowerCardGovernanceOverride> {
  if (!isJsonRecord(snapshot)) {
    return {};
  }

  const overrides: Record<string, PowerCardGovernanceOverride> = {};
  for (const [cardId, rawOverride] of Object.entries(snapshot)) {
    if (!isJsonRecord(rawOverride)) {
      continue;
    }

    const override: PowerCardGovernanceOverride = {};
    const origin = readMetadataString(rawOverride, ['origin']);
    const requiredPolicy = readMetadataString(rawOverride, ['requiredPolicy', 'required_policy']);
    const trustStatus = readMetadataString(rawOverride, ['trustStatus', 'trust_status']);
    const riskClass = readMetadataString(rawOverride, ['riskClass', 'risk_class']);

    if (origin !== null && isProvenanceSource(origin)) {
      override.origin = origin;
    }

    if (requiredPolicy !== null && isMutationPolicy(requiredPolicy)) {
      override.requiredPolicy = requiredPolicy;
    }

    if (trustStatus !== null && isTrustStatus(trustStatus)) {
      override.trustStatus = trustStatus;
    }

    if (riskClass !== null && isRiskClass(riskClass)) {
      override.riskClass = riskClass;
    }

    overrides[cardId] = override;
  }

  return overrides;
}

function createFallbackGovernanceCard(): ExecutionSurfaceConfigurationCard {
  return {
    surfaceId: 'power-card.activate',
    label: 'Power Card activation',
    category: 'power_card',
    mutationSurface: 'task_lifecycle',
    origin: 'runtime_ui',
    requiredPolicy: 'surface_scoped',
    trustStatus: 'trusted',
    riskClass: 'high',
    requiredControls: ['policy:surface-scoped', 'audit:power-card'],
    gateRef: 'gate://surface/power-card.activate',
    status: 'ready'
  };
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

function asJsonRecord(value: JsonValue | undefined): Record<string, JsonValue> {
  return isJsonRecord(value) ? value : {};
}

function readMetadataString(value: Record<string, JsonValue>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === 'string' && candidate.trim().length > 0) {
      return candidate;
    }
  }

  return null;
}

function readMetadataBoolean(value: Record<string, JsonValue>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === 'boolean') {
      return candidate;
    }
  }

  return null;
}

function uniqueIssueCodes(issueCodes: readonly PowerCardIssueCode[]): PowerCardIssueCode[] {
  return POWER_CARD_ISSUE_ORDER.filter((issueCode) => issueCodes.includes(issueCode));
}

function isJsonRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isMutationPolicy(value: string): value is MutationPolicy {
  return value === 'read_only' || value === 'surface_scoped' || value === 'elevated';
}

function isProvenanceSource(value: string): value is MutationProvenanceSource {
  return value === 'runtime_ui' || value === 'runtime_adapter' || value === 'runtime_replay' || value === 'runtime_api';
}

function isTrustStatus(value: string): value is MutationTrustLevel {
  return value === 'trusted' || value === 'restricted' || value === 'blocked';
}

function isRiskClass(value: string): value is ExecutionSurfaceRiskClass {
  return value === 'low' || value === 'medium' || value === 'high' || value === 'critical';
}