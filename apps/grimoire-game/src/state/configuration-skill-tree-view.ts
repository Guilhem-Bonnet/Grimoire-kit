import type { JsonValue, MutationPolicy, MutationProvenanceSource, MutationTrustLevel } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';
import {
  createSurfaceGovernanceView,
  type ExecutionSurfaceConfigurationCard,
  type ExecutionSurfaceRiskClass
} from './surface-governance-view';

export const CONFIGURATION_SKILL_TREE_ISSUE_ORDER = [
  'CONFIGURATION_SKILL_TREE_INVALID_RUNTIME_SNAPSHOT',
  'CONFIGURATION_SKILL_TREE_INVALID_STORAGE_SNAPSHOT',
  'CONFIGURATION_SKILL_TREE_RUNTIME_STORAGE_DIVERGENCE',
  'CONFIGURATION_SKILL_TREE_GOVERNANCE_BLOCKED',
  'CONFIGURATION_SKILL_TREE_MUTATION_REJECTED'
] as const;

export type ConfigurationSkillTreeIssueCode = (typeof CONFIGURATION_SKILL_TREE_ISSUE_ORDER)[number];

export interface ConfigurationSkillTreeMutationRecord {
  mutationId: string;
  nodeId: string;
  requestedEnabled: boolean | null;
  allowed: boolean;
  reason: string | null;
  actorId: string | null;
  sequenceId: number;
  timestamp: string;
  traceId: string | null;
  taskId: string | null;
}

export interface ConfigurationSkillTreeNode {
  nodeId: string;
  label: string;
  category: 'mcp' | 'skill';
  runtimeEnabled: boolean;
  storageEnabled: boolean;
  effectiveEnabled: boolean;
  persistenceStatus: 'synced' | 'runtime_only' | 'storage_only' | 'invalid';
  origin: MutationProvenanceSource;
  requiredPolicy: MutationPolicy;
  trustStatus: MutationTrustLevel;
  riskClass: ExecutionSurfaceRiskClass;
  issueCodes: readonly ConfigurationSkillTreeIssueCode[];
  diagnostic: string | null;
  lastMutation: ConfigurationSkillTreeMutationRecord | null;
}

export interface ConfigurationSkillTreeViewSummary {
  nodeCount: number;
  enabledRuntimeCount: number;
  enabledStorageCount: number;
  divergedCount: number;
  invalidCount: number;
  blockedCount: number;
  rejectedMutationCount: number;
}

export interface ConfigurationSkillTreeView {
  protocolVersion: string;
  lastSequenceId: number;
  nodes: readonly ConfigurationSkillTreeNode[];
  mutations: readonly ConfigurationSkillTreeMutationRecord[];
  reloadReady: boolean;
  reloadBlockingReasons: readonly string[];
  summary: ConfigurationSkillTreeViewSummary;
}

interface ConfigurationSkillTreeBlueprint {
  nodeId: string;
  label: string;
  category: 'mcp' | 'skill';
  path: readonly string[];
  riskClass: ExecutionSurfaceRiskClass;
}

interface SkillTreeNodeGovernanceOverride {
  origin?: MutationProvenanceSource;
  requiredPolicy?: MutationPolicy;
  trustStatus?: MutationTrustLevel;
  riskClass?: ExecutionSurfaceRiskClass;
}

interface SkillTreeNodeState {
  enabled: boolean;
  diagnostics: string[];
}

const CONFIGURATION_SKILL_TREE_BLUEPRINTS: readonly ConfigurationSkillTreeBlueprint[] = [
  {
    nodeId: 'mcp.github',
    label: 'GitHub MCP',
    category: 'mcp',
    path: ['mcp', 'github'],
    riskClass: 'high'
  },
  {
    nodeId: 'mcp.memory',
    label: 'Memory MCP',
    category: 'mcp',
    path: ['mcp', 'memory'],
    riskClass: 'medium'
  },
  {
    nodeId: 'skill.hostBridge',
    label: 'Host Bridge Session',
    category: 'skill',
    path: ['skills', 'hostBridge'],
    riskClass: 'critical'
  },
  {
    nodeId: 'skill.verificationEvidencePack',
    label: 'Verification Evidence Pack',
    category: 'skill',
    path: ['skills', 'verificationEvidencePack'],
    riskClass: 'high'
  }
] as const;

export function createConfigurationSkillTreeView(state: GameState): ConfigurationSkillTreeView {
  const runtimeSnapshot = readConfigValue(state.config, 'skillTree.runtimeSnapshot', ['skillTree', 'runtimeSnapshot']);
  const storageSnapshot = readConfigValue(state.config, 'skillTree.storageSnapshot', ['skillTree', 'storageSnapshot']);
  const governanceOverrides = readNodeGovernanceOverrides(
    readConfigValue(state.config, 'skillTree.nodeGovernance', ['skillTree', 'nodeGovernance'])
  );
  const surfaceGovernance = createSurfaceGovernanceView(state);
  const defaultGovernance =
    surfaceGovernance.configurationCards.find((card) => card.surfaceId === 'tool.runtime-config.apply') ??
    createFallbackGovernanceCard();
  const mutations = collectConfigurationSkillTreeMutations(state.recentWorkflowSteps);
  const latestMutationByNodeId = createLatestMutationByNodeId(mutations);
  const nodes = CONFIGURATION_SKILL_TREE_BLUEPRINTS.map((blueprint) =>
    createConfigurationSkillTreeNode(
      blueprint,
      runtimeSnapshot,
      storageSnapshot,
      governanceOverrides[blueprint.nodeId] ?? {},
      defaultGovernance,
      latestMutationByNodeId[blueprint.nodeId] ?? null
    )
  );
  const reloadBlockingReasons = uniqueStrings([
    ...nodes
      .filter((node) => node.issueCodes.length > 0)
      .map((node) => node.diagnostic)
      .filter((diagnostic): diagnostic is string => diagnostic !== null)
  ]);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    nodes,
    mutations,
    reloadReady: reloadBlockingReasons.length === 0,
    reloadBlockingReasons,
    summary: {
      nodeCount: nodes.length,
      enabledRuntimeCount: nodes.filter((node) => node.runtimeEnabled).length,
      enabledStorageCount: nodes.filter((node) => node.storageEnabled).length,
      divergedCount: nodes.filter((node) => node.issueCodes.includes('CONFIGURATION_SKILL_TREE_RUNTIME_STORAGE_DIVERGENCE')).length,
      invalidCount: nodes.filter(
        (node) =>
          node.issueCodes.includes('CONFIGURATION_SKILL_TREE_INVALID_RUNTIME_SNAPSHOT') ||
          node.issueCodes.includes('CONFIGURATION_SKILL_TREE_INVALID_STORAGE_SNAPSHOT')
      ).length,
      blockedCount: nodes.filter((node) => node.issueCodes.includes('CONFIGURATION_SKILL_TREE_GOVERNANCE_BLOCKED')).length,
      rejectedMutationCount: mutations.filter((mutation) => !mutation.allowed).length
    }
  };
}

function createConfigurationSkillTreeNode(
  blueprint: ConfigurationSkillTreeBlueprint,
  runtimeSnapshot: JsonValue | undefined,
  storageSnapshot: JsonValue | undefined,
  governanceOverride: SkillTreeNodeGovernanceOverride,
  defaultGovernance: ExecutionSurfaceConfigurationCard,
  lastMutation: ConfigurationSkillTreeMutationRecord | null
): ConfigurationSkillTreeNode {
  const runtimeState = readSkillTreeNodeState(runtimeSnapshot, 'skillTree.runtimeSnapshot', blueprint.path);
  const storageState = readSkillTreeNodeState(storageSnapshot, 'skillTree.storageSnapshot', blueprint.path);
  const origin = governanceOverride.origin ?? defaultGovernance.origin;
  const requiredPolicy = governanceOverride.requiredPolicy ?? defaultGovernance.requiredPolicy;
  const trustStatus = governanceOverride.trustStatus ?? defaultGovernance.trustStatus;
  const riskClass = governanceOverride.riskClass ?? blueprint.riskClass;
  const issueCodes: ConfigurationSkillTreeIssueCode[] = [
    ...(runtimeState.diagnostics.length > 0 ? ['CONFIGURATION_SKILL_TREE_INVALID_RUNTIME_SNAPSHOT' as const] : []),
    ...(storageState.diagnostics.length > 0 ? ['CONFIGURATION_SKILL_TREE_INVALID_STORAGE_SNAPSHOT' as const] : []),
    ...(runtimeState.diagnostics.length === 0 && storageState.diagnostics.length === 0 && runtimeState.enabled !== storageState.enabled
      ? ['CONFIGURATION_SKILL_TREE_RUNTIME_STORAGE_DIVERGENCE' as const]
      : []),
    ...(trustStatus === 'blocked' && (runtimeState.enabled || storageState.enabled)
      ? ['CONFIGURATION_SKILL_TREE_GOVERNANCE_BLOCKED' as const]
      : []),
    ...(lastMutation !== null && !lastMutation.allowed ? ['CONFIGURATION_SKILL_TREE_MUTATION_REJECTED' as const] : [])
  ];
  const diagnostic = createNodeDiagnostic(blueprint, issueCodes, runtimeState.diagnostics, storageState.diagnostics, trustStatus, lastMutation);

  return {
    nodeId: blueprint.nodeId,
    label: blueprint.label,
    category: blueprint.category,
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
    diagnostic,
    lastMutation
  };
}

function collectConfigurationSkillTreeMutations(
  recentWorkflowSteps: readonly WorkflowStepLogEntry[]
): ConfigurationSkillTreeMutationRecord[] {
  return [...recentWorkflowSteps]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .map((workflowStep) => {
      const nodeId = readMetadataString(workflowStep.metadata, ['configurationSkillTreeNodeId', 'skillTreeNodeId', 'nodeId']);
      if (nodeId === null) {
        return null;
      }

      const validationError = readMetadataString(workflowStep.metadata, ['validationError', 'validation_error', 'reason']);
      const allowed = (readMetadataBoolean(workflowStep.metadata, ['allowed']) ?? true) && validationError === null;
      return {
        mutationId:
          readMetadataString(workflowStep.metadata, ['mutationId', 'mutation_id']) ??
          `skill-tree-mutation:${workflowStep.sequenceId}`,
        nodeId,
        requestedEnabled: readMetadataBoolean(workflowStep.metadata, ['enabled', 'nextEnabled', 'requestedEnabled']),
        allowed,
        reason: validationError,
        actorId: readMetadataString(workflowStep.metadata, ['actorId', 'actor_id']),
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp,
        traceId: workflowStep.traceId ?? null,
        taskId: workflowStep.taskId ?? null
      } satisfies ConfigurationSkillTreeMutationRecord;
    })
    .filter((mutation): mutation is ConfigurationSkillTreeMutationRecord => mutation !== null);
}

function createLatestMutationByNodeId(
  mutations: readonly ConfigurationSkillTreeMutationRecord[]
): Record<string, ConfigurationSkillTreeMutationRecord | undefined> {
  const latestByNodeId: Record<string, ConfigurationSkillTreeMutationRecord | undefined> = {};

  for (const mutation of mutations) {
    if (latestByNodeId[mutation.nodeId] === undefined) {
      latestByNodeId[mutation.nodeId] = mutation;
    }
  }

  return latestByNodeId;
}

function readSkillTreeNodeState(
  snapshot: JsonValue | undefined,
  rootKey: string,
  path: readonly string[]
): SkillTreeNodeState {
  if (snapshot === undefined) {
    return { enabled: false, diagnostics: [] };
  }

  if (!isJsonRecord(snapshot)) {
    return {
      enabled: false,
      diagnostics: [`${rootKey} must be an object.`]
    };
  }

  let cursor: JsonValue | undefined = snapshot;
  const diagnostics: string[] = [];
  for (const [index, segment] of path.entries()) {
    if (!isJsonRecord(cursor)) {
      diagnostics.push(`${rootKey}.${path.slice(0, index).join('.')} must be an object.`);
      return { enabled: false, diagnostics };
    }

    cursor = cursor[segment];
    if (cursor === undefined) {
      return { enabled: false, diagnostics };
    }
  }

  if (!isJsonRecord(cursor)) {
    diagnostics.push(`${rootKey}.${path.join('.')} must be an object.`);
    return { enabled: false, diagnostics };
  }

  const enabled = cursor.enabled;
  if (enabled === undefined) {
    return { enabled: false, diagnostics };
  }

  if (typeof enabled !== 'boolean') {
    diagnostics.push(`${rootKey}.${path.join('.')}\.enabled must be boolean.`);
    return { enabled: false, diagnostics };
  }

  return { enabled, diagnostics };
}

function createNodeDiagnostic(
  blueprint: ConfigurationSkillTreeBlueprint,
  issueCodes: readonly ConfigurationSkillTreeIssueCode[],
  runtimeDiagnostics: readonly string[],
  storageDiagnostics: readonly string[],
  trustStatus: MutationTrustLevel,
  lastMutation: ConfigurationSkillTreeMutationRecord | null
): string | null {
  const diagnostics = [
    ...runtimeDiagnostics,
    ...storageDiagnostics,
    ...(issueCodes.includes('CONFIGURATION_SKILL_TREE_RUNTIME_STORAGE_DIVERGENCE')
      ? [`${blueprint.label} diverges between runtime and storage snapshots.`]
      : []),
    ...(issueCodes.includes('CONFIGURATION_SKILL_TREE_GOVERNANCE_BLOCKED')
      ? [`${blueprint.label} is enabled while trust status is ${trustStatus}.`]
      : []),
    ...(lastMutation !== null && !lastMutation.allowed && lastMutation.reason !== null ? [lastMutation.reason] : [])
  ];

  return diagnostics[0] ?? null;
}

function readNodeGovernanceOverrides(value: JsonValue | undefined): Record<string, SkillTreeNodeGovernanceOverride> {
  if (!isJsonRecord(value)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(value)
      .map(([nodeId, override]) => {
        if (!isJsonRecord(override)) {
          return null;
        }

        const origin = readMetadataOrigin(override, ['origin']);
        const requiredPolicy = readMetadataPolicy(override, ['requiredPolicy', 'required_policy']);
        const trustStatus = readMetadataTrustStatus(override, ['trustStatus', 'trust_status']);
        const riskClass = readRiskClass(override, ['riskClass', 'risk_class']);

        return [
          nodeId,
          {
            ...(origin === null ? {} : { origin }),
            ...(requiredPolicy === null ? {} : { requiredPolicy }),
            ...(trustStatus === null ? {} : { trustStatus }),
            ...(riskClass === null ? {} : { riskClass })
          } as SkillTreeNodeGovernanceOverride
        ] as const;
      })
      .filter((entry): entry is readonly [string, SkillTreeNodeGovernanceOverride] => entry !== null)
  );
}

function createFallbackGovernanceCard(): ExecutionSurfaceConfigurationCard {
  return {
    surfaceId: 'tool.runtime-config.apply',
    label: 'Runtime tool configuration apply',
    category: 'tool',
    mutationSurface: 'runtime_config',
    origin: 'runtime_ui',
    requiredPolicy: 'elevated',
    trustStatus: 'trusted',
    riskClass: 'critical',
    requiredControls: ['verification:config', 'audit:tool-call'],
    gateRef: 'gate://surface/tool.runtime-config.apply',
    status: 'ready'
  };
}

function readConfigValue(
  config: Record<string, JsonValue>,
  flatKey: string,
  nestedPath: readonly string[]
): JsonValue | undefined {
  const flatValue = config[flatKey];
  if (flatValue !== undefined) {
    return flatValue;
  }

  let cursor: JsonValue | undefined = config;
  for (const segment of nestedPath) {
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

function isJsonRecord(value: JsonValue | undefined): value is Record<string, JsonValue> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readMetadataString(metadata: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = metadata[key];
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

function readMetadataBoolean(metadata: Record<string, unknown>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readMetadataOrigin(
  metadata: Record<string, unknown>,
  keys: readonly string[]
): MutationProvenanceSource | null {
  const value = readMetadataString(metadata, keys);
  return value === 'runtime_ui' || value === 'runtime_adapter' || value === 'runtime_replay' || value === 'runtime_api'
    ? value
    : null;
}

function readMetadataPolicy(metadata: Record<string, unknown>, keys: readonly string[]): MutationPolicy | null {
  const value = readMetadataString(metadata, keys);
  return value === 'read_only' || value === 'surface_scoped' || value === 'elevated' ? value : null;
}

function readMetadataTrustStatus(
  metadata: Record<string, unknown>,
  keys: readonly string[]
): MutationTrustLevel | null {
  const value = readMetadataString(metadata, keys);
  return value === 'blocked' || value === 'restricted' || value === 'trusted' ? value : null;
}

function readRiskClass(
  metadata: Record<string, unknown>,
  keys: readonly string[]
): ExecutionSurfaceRiskClass | null {
  const value = readMetadataString(metadata, keys);
  return value === 'low' || value === 'medium' || value === 'high' || value === 'critical' ? value : null;
}

function uniqueIssueCodes(values: readonly ConfigurationSkillTreeIssueCode[]): ConfigurationSkillTreeIssueCode[] {
  return CONFIGURATION_SKILL_TREE_ISSUE_ORDER.filter((issueCode) => values.includes(issueCode));
}

function uniqueStrings(values: readonly (string | null)[]): string[] {
  return [...new Set(values.filter((value): value is string => typeof value === 'string' && value.length > 0))].sort((left, right) =>
    left.localeCompare(right)
  );
}