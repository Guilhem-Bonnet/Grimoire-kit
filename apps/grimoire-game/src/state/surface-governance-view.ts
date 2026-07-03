import type {
  JsonValue,
  MutationPolicy,
  MutationProvenanceSource,
  MutationSurface,
  MutationTrustLevel,
  WorkflowStepLogEntry
} from '../contracts/events';
import { createSurfaceExecutionRegistry } from '../contracts/events';

import type { GameState } from './game-state';

export const EXECUTION_SURFACE_CATEGORY_ORDER = [
  'config_action',
  'tool',
  'plugin',
  'power_card',
  'skill'
] as const;
export const EXECUTION_SURFACE_RISK_ORDER = ['low', 'medium', 'high', 'critical'] as const;

export type ExecutionSurfaceCategory = (typeof EXECUTION_SURFACE_CATEGORY_ORDER)[number];
export type ExecutionSurfaceRiskClass = (typeof EXECUTION_SURFACE_RISK_ORDER)[number];

export interface ExecutionSurfaceDefinition {
  surfaceId: string;
  label: string;
  category: ExecutionSurfaceCategory;
  mutationSurface: MutationSurface;
  origin: MutationProvenanceSource;
  requiredPolicy: MutationPolicy;
  trustStatus: MutationTrustLevel;
  riskClass: ExecutionSurfaceRiskClass;
  requiredControls: readonly string[];
  gateRef: string;
}

export interface ExecutionSurfaceConfigurationCard {
  surfaceId: string;
  label: string;
  category: ExecutionSurfaceCategory;
  mutationSurface: MutationSurface;
  origin: MutationProvenanceSource;
  requiredPolicy: MutationPolicy;
  trustStatus: MutationTrustLevel;
  riskClass: ExecutionSurfaceRiskClass;
  requiredControls: readonly string[];
  gateRef: string;
  status: 'ready' | 'blocked';
}

export interface ExecutionSurfaceActivationGate {
  activationId: string;
  surfaceId: string;
  label: string;
  allowed: boolean;
  reason: string | null;
  missingFields: readonly string[];
  origin: MutationProvenanceSource | null;
  requiredPolicy: MutationPolicy | null;
  trustStatus: MutationTrustLevel | null;
  riskClass: ExecutionSurfaceRiskClass;
  traceId: string | null;
  taskId: string | null;
  sequenceId: number;
  timestamp: string;
}

export interface ExecutionSurfaceSecurityFinding {
  findingId: string;
  surfaceId: string;
  severity: 'warning' | 'critical';
  riskClass: ExecutionSurfaceRiskClass;
  message: string;
  blocksActivation: boolean;
}

export interface SurfaceGovernanceViewSummary {
  surfaceCount: number;
  blockedSurfaceCount: number;
  activationCount: number;
  blockedActivationCount: number;
  criticalRiskCount: number;
}

export interface SurfaceGovernanceView {
  protocolVersion: string;
  lastSequenceId: number;
  configurationCards: readonly ExecutionSurfaceConfigurationCard[];
  activationGates: readonly ExecutionSurfaceActivationGate[];
  securityAuditFindings: readonly ExecutionSurfaceSecurityFinding[];
  summary: SurfaceGovernanceViewSummary;
}

interface SurfaceBlueprint {
  surfaceId: string;
  label: string;
  category: ExecutionSurfaceCategory;
  mutationSurface: MutationSurface;
  riskClass: ExecutionSurfaceRiskClass;
  requiredControls: readonly string[];
  requiredPolicy?: MutationPolicy;
  trustStatus?: MutationTrustLevel;
}

const MUTATION_POLICY_RANK: Record<MutationPolicy, number> = {
  read_only: 0,
  surface_scoped: 1,
  elevated: 2
};

const MUTATION_TRUST_RANK: Record<MutationTrustLevel, number> = {
  blocked: 0,
  restricted: 1,
  trusted: 2
};

const SURFACE_BLUEPRINTS: readonly SurfaceBlueprint[] = [
  {
    surfaceId: 'config.hud.theme',
    label: 'HUD theme override',
    category: 'config_action',
    mutationSurface: 'runtime_config',
    riskClass: 'medium',
    requiredControls: ['schema:config', 'audit:config-update']
  },
  {
    surfaceId: 'config.spectator.share',
    label: 'Spectator share link',
    category: 'config_action',
    mutationSurface: 'runtime_config',
    riskClass: 'high',
    requiredControls: ['auth:read-only-token', 'audit:share-link']
  },
  {
    surfaceId: 'task.transition.done',
    label: 'Task completion',
    category: 'skill',
    mutationSurface: 'task_lifecycle',
    riskClass: 'critical',
    requiredPolicy: 'elevated',
    requiredControls: ['verification:chain', 'review:critical-findings']
  },
  {
    surfaceId: 'task.assignment.reassign',
    label: 'Task reassignment',
    category: 'skill',
    mutationSurface: 'task_assignment',
    riskClass: 'high',
    requiredControls: ['rbac:assignee-scope', 'audit:assignment']
  },
  {
    surfaceId: 'inspection.redirect',
    label: 'Deep Inspection redirect',
    category: 'plugin',
    mutationSurface: 'agent_presence',
    riskClass: 'high',
    requiredControls: ['rbac:inspection-action', 'audit:inspection-action']
  },
  {
    surfaceId: 'power-card.activate',
    label: 'Power Card activation',
    category: 'power_card',
    mutationSurface: 'task_lifecycle',
    riskClass: 'high',
    requiredControls: ['policy:surface-scoped', 'audit:power-card']
  },
  {
    surfaceId: 'tool.runtime-config.apply',
    label: 'Runtime tool configuration apply',
    category: 'tool',
    mutationSurface: 'runtime_config',
    riskClass: 'critical',
    requiredControls: ['verification:config', 'audit:tool-call']
  }
] as const;

export function createSurfaceGovernanceView(state: GameState): SurfaceGovernanceView {
  const definitions = createExecutionSurfaceDefinitions(state.config);
  const definitionBySurfaceId = new Map(definitions.map((definition) => [definition.surfaceId, definition]));
  const activationGates = collectActivationGates(state.recentWorkflowSteps, definitionBySurfaceId);
  const securityAuditFindings = createSecurityAuditFindings(definitions, activationGates);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    configurationCards: definitions.map((definition) => ({
      ...definition,
      status: definition.trustStatus === 'blocked' ? 'blocked' : 'ready'
    })),
    activationGates,
    securityAuditFindings,
    summary: {
      surfaceCount: definitions.length,
      blockedSurfaceCount: definitions.filter((definition) => definition.trustStatus === 'blocked').length,
      activationCount: activationGates.length,
      blockedActivationCount: activationGates.filter((gate) => !gate.allowed).length,
      criticalRiskCount: definitions.filter((definition) => definition.riskClass === 'critical').length
    }
  };
}

export function createExecutionSurfaceDefinitions(
  config: Record<string, JsonValue>
): readonly ExecutionSurfaceDefinition[] {
  const registryBySurface = new Map(createSurfaceExecutionRegistry().map((record) => [record.surface, record]));
  const overrides = readSurfaceOverrides(config);

  return SURFACE_BLUEPRINTS.map((blueprint) => {
    const registryRecord = registryBySurface.get(blueprint.mutationSurface);
    const override = overrides[blueprint.surfaceId] ?? {};

    return {
      surfaceId: blueprint.surfaceId,
      label: blueprint.label,
      category: blueprint.category,
      mutationSurface: blueprint.mutationSurface,
      origin: override.origin ?? registryRecord?.origin ?? 'runtime_ui',
      requiredPolicy: override.requiredPolicy ?? blueprint.requiredPolicy ?? registryRecord?.requiredPolicy ?? 'surface_scoped',
      trustStatus: override.trustStatus ?? blueprint.trustStatus ?? registryRecord?.trustStatus ?? 'trusted',
      riskClass: override.riskClass ?? blueprint.riskClass,
      requiredControls: blueprint.requiredControls,
      gateRef: `gate://surface/${blueprint.surfaceId}`
    } satisfies ExecutionSurfaceDefinition;
  }).sort(compareExecutionSurfaceDefinitions);
}

function collectActivationGates(
  recentWorkflowSteps: readonly WorkflowStepLogEntry[],
  definitionBySurfaceId: Map<string, ExecutionSurfaceDefinition>
): ExecutionSurfaceActivationGate[] {
  return recentWorkflowSteps
    .map((workflowStep) => createActivationGate(workflowStep, definitionBySurfaceId))
    .filter((gate): gate is ExecutionSurfaceActivationGate => gate !== null)
    .sort((left, right) => right.sequenceId - left.sequenceId);
}

function createActivationGate(
  workflowStep: WorkflowStepLogEntry,
  definitionBySurfaceId: Map<string, ExecutionSurfaceDefinition>
): ExecutionSurfaceActivationGate | null {
  const metadata = asJsonRecord(workflowStep.metadata);
  const surfaceId = readMetadataString(metadata, ['surfaceId', 'surface_id']);
  const actionSignal = readMetadataString(metadata, ['activationAction', 'activation_action', 'action']);
  if (surfaceId === null || actionSignal === null || !actionSignal.toLowerCase().includes('activate')) {
    return null;
  }

  const definition = definitionBySurfaceId.get(surfaceId);
  const activationId =
    readMetadataString(metadata, ['activationId', 'activation_id']) ??
    `surface-activation:${workflowStep.sequenceId}:${surfaceId}`;
  const origin = readMetadataOrigin(metadata);
  const requiredPolicy = readMetadataPolicy(metadata);
  const trustStatus = readMetadataTrustStatus(metadata);
  const missingFields = [
    origin === null ? 'origin' : null,
    requiredPolicy === null ? 'requiredPolicy' : null,
    trustStatus === null ? 'trustStatus' : null
  ].filter((field): field is string => field !== null);

  if (definition === undefined) {
    return {
      activationId,
      surfaceId,
      label: surfaceId,
      allowed: false,
      reason: `Unknown execution surface ${surfaceId}.`,
      missingFields,
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: 'critical',
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  if (missingFields.length > 0) {
    return {
      activationId,
      surfaceId,
      label: definition.label,
      allowed: false,
      reason: `Activation ${surfaceId} is missing ${missingFields.join(', ')} metadata.`,
      missingFields,
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: definition.riskClass,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  if (origin === null || requiredPolicy === null || trustStatus === null) {
    return null;
  }

  if (definition.trustStatus === 'blocked') {
    return {
      activationId,
      surfaceId,
      label: definition.label,
      allowed: false,
      reason: `Execution surface ${surfaceId} is blocked by trust status policy.`,
      missingFields: [],
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: definition.riskClass,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  if (origin !== definition.origin) {
    return {
      activationId,
      surfaceId,
      label: definition.label,
      allowed: false,
      reason: `Activation ${surfaceId} declares origin ${origin} but expected ${definition.origin}.`,
      missingFields: [],
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: definition.riskClass,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  if (MUTATION_POLICY_RANK[requiredPolicy] < MUTATION_POLICY_RANK[definition.requiredPolicy]) {
    return {
      activationId,
      surfaceId,
      label: definition.label,
      allowed: false,
      reason: `Activation ${surfaceId} does not satisfy required policy ${definition.requiredPolicy}.`,
      missingFields: [],
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: definition.riskClass,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  if (MUTATION_TRUST_RANK[trustStatus] < MUTATION_TRUST_RANK[definition.trustStatus]) {
    return {
      activationId,
      surfaceId,
      label: definition.label,
      allowed: false,
      reason: `Activation ${surfaceId} does not satisfy trust status ${definition.trustStatus}.`,
      missingFields: [],
      origin,
      requiredPolicy,
      trustStatus,
      riskClass: definition.riskClass,
      traceId: workflowStep.traceId ?? null,
      taskId: workflowStep.taskId ?? null,
      sequenceId: workflowStep.sequenceId,
      timestamp: workflowStep.timestamp
    };
  }

  return {
    activationId,
    surfaceId,
    label: definition.label,
    allowed: true,
    reason: null,
    missingFields: [],
    origin,
    requiredPolicy,
    trustStatus,
    riskClass: definition.riskClass,
    traceId: workflowStep.traceId ?? null,
    taskId: workflowStep.taskId ?? null,
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp
  };
}

function createSecurityAuditFindings(
  definitions: readonly ExecutionSurfaceDefinition[],
  activationGates: readonly ExecutionSurfaceActivationGate[]
): ExecutionSurfaceSecurityFinding[] {
  const findings: ExecutionSurfaceSecurityFinding[] = definitions
    .filter((definition) => definition.trustStatus === 'blocked')
    .map((definition) => ({
      findingId: `surface-governance:${definition.surfaceId}:trust-blocked`,
      surfaceId: definition.surfaceId,
      severity: definition.riskClass === 'critical' ? 'critical' : 'warning',
      riskClass: definition.riskClass,
      message: `Execution surface ${definition.surfaceId} is blocked until governance trust is restored.`,
      blocksActivation: true
    }));

  for (const gate of activationGates.filter((gate) => !gate.allowed)) {
    findings.push({
      findingId: `surface-governance:${gate.activationId}`,
      surfaceId: gate.surfaceId,
      severity: gate.riskClass === 'critical' ? 'critical' : 'warning',
      riskClass: gate.riskClass,
      message: gate.reason ?? `Activation ${gate.surfaceId} was rejected.`,
      blocksActivation: true
    });
  }

  return findings.sort(compareSecurityFindings);
}

function readSurfaceOverrides(config: Record<string, JsonValue>): Record<string, Partial<ExecutionSurfaceDefinition>> {
  const rawOverrides = readConfigValue(config, ['surfaceGovernance.overrides']);
  if (!isJsonRecord(rawOverrides)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(rawOverrides).flatMap(([surfaceId, value]) => {
      const record = asJsonRecord(value);
      const origin = readMetadataOrigin(record);
      const requiredPolicy = readMetadataPolicy(record);
      const trustStatus = readMetadataTrustStatus(record);
      const riskClass = readMetadataRiskClass(record);

      if (origin === null && requiredPolicy === null && trustStatus === null && riskClass === null) {
        return [];
      }

      return [
        [
          surfaceId,
          {
            ...(origin === null ? {} : { origin }),
            ...(requiredPolicy === null ? {} : { requiredPolicy }),
            ...(trustStatus === null ? {} : { trustStatus }),
            ...(riskClass === null ? {} : { riskClass })
          }
        ]
      ];
    })
  );
}

function readConfigValue(config: Record<string, JsonValue>, keys: readonly string[]): JsonValue | undefined {
  for (const key of keys) {
    if (config[key] !== undefined) {
      return config[key];
    }

    const pathValue = readConfigPath(config, key.split('.'));
    if (pathValue !== undefined) {
      return pathValue;
    }
  }

  return undefined;
}

function readConfigPath(value: JsonValue | Record<string, JsonValue>, path: readonly string[]): JsonValue | undefined {
  let cursor: JsonValue | Record<string, JsonValue> | undefined = value;

  for (const segment of path) {
    if (!isJsonRecord(cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
  }

  return cursor;
}

function readMetadataString(record: Record<string, JsonValue>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }

  return null;
}

function readMetadataOrigin(record: Record<string, JsonValue>): MutationProvenanceSource | null {
  const value = readMetadataString(record, ['origin', 'source']);
  return value === 'runtime_ui' || value === 'runtime_adapter' || value === 'runtime_replay' || value === 'runtime_api'
    ? value
    : null;
}

function readMetadataPolicy(record: Record<string, JsonValue>): MutationPolicy | null {
  const value = readMetadataString(record, ['requiredPolicy', 'required_policy', 'policy']);
  return value === 'read_only' || value === 'surface_scoped' || value === 'elevated' ? value : null;
}

function readMetadataTrustStatus(record: Record<string, JsonValue>): MutationTrustLevel | null {
  const value = readMetadataString(record, ['trustStatus', 'trust_status']);
  return value === 'trusted' || value === 'restricted' || value === 'blocked' ? value : null;
}

function readMetadataRiskClass(record: Record<string, JsonValue>): ExecutionSurfaceRiskClass | null {
  const value = readMetadataString(record, ['riskClass', 'risk_class']);
  return value === 'low' || value === 'medium' || value === 'high' || value === 'critical' ? value : null;
}

function compareExecutionSurfaceDefinitions(
  left: ExecutionSurfaceDefinition,
  right: ExecutionSurfaceDefinition
): number {
  const categoryRank = Object.fromEntries(EXECUTION_SURFACE_CATEGORY_ORDER.map((category, index) => [category, index])) as Record<ExecutionSurfaceCategory, number>;
  const riskRank = Object.fromEntries(EXECUTION_SURFACE_RISK_ORDER.map((risk, index) => [risk, index])) as Record<ExecutionSurfaceRiskClass, number>;

  if (categoryRank[left.category] !== categoryRank[right.category]) {
    return categoryRank[left.category] - categoryRank[right.category];
  }

  if (riskRank[left.riskClass] !== riskRank[right.riskClass]) {
    return riskRank[right.riskClass] - riskRank[left.riskClass];
  }

  return left.surfaceId.localeCompare(right.surfaceId);
}

function compareSecurityFindings(
  left: ExecutionSurfaceSecurityFinding,
  right: ExecutionSurfaceSecurityFinding
): number {
  const severityRank = { critical: 0, warning: 1 } as const;
  if (severityRank[left.severity] !== severityRank[right.severity]) {
    return severityRank[left.severity] - severityRank[right.severity];
  }

  return left.surfaceId.localeCompare(right.surfaceId);
}

function asJsonRecord(value: unknown): Record<string, JsonValue> {
  return isJsonRecord(value) ? value : {};
}

function isJsonRecord(value: unknown): value is Record<string, JsonValue> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}