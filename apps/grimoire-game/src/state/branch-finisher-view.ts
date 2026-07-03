import {
  BranchFinishDecisionPayloadSchema,
  BranchFinishOptionsPayloadSchema,
  type BranchFinishOption,
  type JsonValue,
  SecurityFindingPayloadSchema,
  type SecurityFindingPayload
} from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';
import { createProvenanceComplianceView, type ProvenanceComplianceSummary } from './provenance-compliance-view';
import { createVerificationEvidencePackView } from './verification-evidence-pack-view';
import { createVerificationQueueView } from './verification-queue-view';

export const SECURITY_CONFIDENCE_PUBLISH_THRESHOLD = 8;
export const BRANCH_FINISH_OPTION_ORDER = ['merge', 'pr', 'keep', 'discard'] as const;
export const OWASP_FOCUS_AREA_ORDER = [
  'prompt_injection',
  'sensitive_information_disclosure',
  'supply_chain',
  'data_model_poisoning',
  'improper_output_handling',
  'excessive_agency',
  'system_prompt_leakage',
  'vector_embedding_weakness',
  'misinformation',
  'unbounded_consumption'
] as const;

export type OwaspFocusArea = (typeof OWASP_FOCUS_AREA_ORDER)[number];

const OWASP_FOCUS_LABELS: Record<OwaspFocusArea, string> = {
  prompt_injection: 'LLM01 Prompt Injection',
  sensitive_information_disclosure: 'LLM02 Sensitive Information Disclosure',
  supply_chain: 'LLM03 Supply Chain',
  data_model_poisoning: 'LLM04 Data and Model Poisoning',
  improper_output_handling: 'LLM05 Improper Output Handling',
  excessive_agency: 'LLM06 Excessive Agency',
  system_prompt_leakage: 'LLM07 System Prompt Leakage',
  vector_embedding_weakness: 'LLM08 Vector and Embedding Weaknesses',
  misinformation: 'LLM09 Misinformation',
  unbounded_consumption: 'LLM10 Unbounded Consumption'
};

const OWASP_FOCUS_BY_RISK_CODE: Partial<Record<string, OwaspFocusArea>> = {
  LLM01: 'prompt_injection',
  LLM02: 'sensitive_information_disclosure',
  LLM03: 'supply_chain',
  LLM04: 'data_model_poisoning',
  LLM05: 'improper_output_handling',
  LLM06: 'excessive_agency',
  LLM07: 'system_prompt_leakage',
  LLM08: 'vector_embedding_weakness',
  LLM09: 'misinformation',
  LLM10: 'unbounded_consumption'
};

export interface SecurityAuditFinding extends SecurityFindingPayload {
  sequenceId: number;
  timestamp: string;
  traceId: string | null;
  taskId: string | null;
  normalizedOwaspCategory: string | null;
  owaspFocusAreas: readonly OwaspFocusArea[];
  isPublished: boolean;
  hasProvenanceGap: boolean;
  hasPolicyGap: boolean;
  hasTrustBlock: boolean;
  blocksShip: boolean;
  blockingReasons: readonly string[];
}

export interface SecurityOwaspCategorySummary {
  category: string;
  label: string;
  findingCount: number;
  openFindingCount: number;
  blockingFindingCount: number;
}

export interface SecurityOwaspFocusSummary {
  focusArea: OwaspFocusArea;
  label: string;
  findingCount: number;
  openFindingCount: number;
  blockingFindingCount: number;
  explicitCategoryCount: number;
  derivedSignalCount: number;
}

export interface SecurityKanbanCard {
  id: string;
  findingId: string;
  title: string;
  status: 'todo' | 'review';
  severity: SecurityAuditFinding['severity'];
  detail: string;
  exploitScenario: string;
  surfaceId: string;
  taskId: string | null;
  traceId: string | null;
  blocksShip: boolean;
  sequenceId: number;
  timestamp: string;
}

export interface SecuritySurfaceSummary {
  surfaceId: string;
  findingCount: number;
  openFindingCount: number;
  blockingFindingCount: number;
}

export interface SecurityAuditMetrics {
  totalFindingCount: number;
  publishedFindingCount: number;
  openFindingCount: number;
  blockingFindingCount: number;
  criticalOpenCount: number;
  provenanceGapCount: number;
  policyGapCount: number;
  trustBlockedCount: number;
  owaspCategorizedFindingCount: number;
  owaspFocusAreaCount: number;
  autoSecurityCardCount: number;
}

export interface SecurityAuditView {
  protocolVersion: string;
  lastSequenceId: number;
  findings: readonly SecurityAuditFinding[];
  publishedFindings: readonly SecurityAuditFinding[];
  openBlockingFindings: readonly SecurityAuditFinding[];
  shipBlocked: boolean;
  blockingReasons: readonly string[];
  kanbanCards: readonly SecurityKanbanCard[];
  surfaceMatrix: readonly SecuritySurfaceSummary[];
  owaspCategories: readonly SecurityOwaspCategorySummary[];
  owaspFocusAreas: readonly SecurityOwaspFocusSummary[];
  metrics: SecurityAuditMetrics;
}

export function getSecurityOwaspFocusLabel(focusArea: OwaspFocusArea): string {
  return OWASP_FOCUS_LABELS[focusArea];
}

export function describeSecurityFindingOwaspRisk(
  finding: Pick<SecurityAuditFinding, 'normalizedOwaspCategory' | 'owaspFocusAreas'>
): string | null {
  if (finding.normalizedOwaspCategory !== null) {
    const explicitFocusArea = readOwaspFocusAreaFromCategory(finding.normalizedOwaspCategory);
    return explicitFocusArea === null
      ? finding.normalizedOwaspCategory
      : getSecurityOwaspFocusLabel(explicitFocusArea);
  }

  const firstFocusArea = finding.owaspFocusAreas[0];
  return firstFocusArea === undefined ? null : getSecurityOwaspFocusLabel(firstFocusArea);
}

export interface BranchFinishOptionState {
  option: BranchFinishOption;
  allowed: boolean;
  blockedReasons: readonly string[];
  requiresTypedConfirmation: boolean;
  requiredTypedConfirmation: string | null;
}

export interface BranchFinishDecisionEvaluation {
  branch: string;
  selectedOption: BranchFinishOption;
  typedConfirmation: string;
  allowed: boolean;
  blockedReasons: readonly string[];
}

export interface BranchFinisherVerificationSummary {
  queueCount: number;
  queuedCount: number;
  verifyingCount: number;
  acceptedCount: number;
  rejectedCount: number;
  needsWorkCount: number;
  blockingItemCount: number;
  blockingTaskIds: readonly string[];
  blockingReasons: readonly string[];
  evidencePackCount: number;
  attestedPackCount: number;
  unattestedPackCount: number;
  missingEvidencePackCount: number;
}

export interface BranchFinisherView {
  protocolVersion: string;
  lastSequenceId: number;
  branch: string;
  testsPassed: boolean;
  requiredTypedDiscardConfirmation: string;
  options: readonly BranchFinishOptionState[];
  latestDecision: BranchFinishDecisionEvaluation | null;
  shipBlocked: boolean;
  blockingReasons: readonly string[];
  securityCards: readonly SecurityKanbanCard[];
  provenanceCompliance: ProvenanceComplianceSummary;
  verification: BranchFinisherVerificationSummary;
}

const SECURITY_SEVERITY_RANK: Record<SecurityAuditFinding['severity'], number> = {
  critical: 0,
  high: 1,
  medium: 2,
  info: 3
};

export function createSecurityAuditView(state: GameState): SecurityAuditView {
  const findings = collectSecurityFindings(state);
  const publishedFindings = findings.filter((finding) => finding.isPublished);
  const openBlockingFindings = publishedFindings.filter((finding) => finding.status === 'open' && finding.blocksShip);
  const blockingReasons = uniqueStrings(openBlockingFindings.flatMap((finding) => finding.blockingReasons));
  const kanbanCards = publishedFindings
    .filter((finding) => finding.status === 'open')
    .map(toSecurityKanbanCard)
    .sort(compareSecurityKanbanCards);
  const surfaceMatrix = createSurfaceMatrix(publishedFindings);
  const owaspCategories = createOwaspCategoryMatrix(publishedFindings);
  const owaspFocusAreas = createOwaspFocusMatrix(publishedFindings);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    findings,
    publishedFindings,
    openBlockingFindings,
    shipBlocked: openBlockingFindings.length > 0,
    blockingReasons,
    kanbanCards,
    surfaceMatrix,
    owaspCategories,
    owaspFocusAreas,
    metrics: {
      totalFindingCount: findings.length,
      publishedFindingCount: publishedFindings.length,
      openFindingCount: publishedFindings.filter((finding) => finding.status === 'open').length,
      blockingFindingCount: openBlockingFindings.length,
      criticalOpenCount: publishedFindings.filter(
        (finding) => finding.status === 'open' && finding.severity === 'critical'
      ).length,
      provenanceGapCount: publishedFindings.filter((finding) => finding.hasProvenanceGap).length,
      policyGapCount: publishedFindings.filter((finding) => finding.hasPolicyGap).length,
      trustBlockedCount: publishedFindings.filter((finding) => finding.hasTrustBlock).length,
      owaspCategorizedFindingCount: publishedFindings.filter(
        (finding) => finding.normalizedOwaspCategory !== null
      ).length,
      owaspFocusAreaCount: owaspFocusAreas.length,
      autoSecurityCardCount: kanbanCards.length
    }
  };
}

export function createBranchFinisherView(state: GameState): BranchFinisherView {
  const securityAudit = createSecurityAuditView(state);
  const provenanceCompliance = createProvenanceComplianceView(state);
  const verification = createBranchFinisherVerificationSummary(state);
  const optionsPayload = readLatestBranchFinishOptions(state);
  const options = BRANCH_FINISH_OPTION_ORDER.map((option) =>
    evaluateBranchFinishOption(option, optionsPayload, securityAudit, provenanceCompliance)
  );
  const latestDecision = evaluateLatestBranchFinishDecision(state, optionsPayload, options);
  const shipBlocked = securityAudit.shipBlocked || provenanceCompliance.shipBlocked;
  const blockingReasons = uniqueStrings([...securityAudit.blockingReasons, ...provenanceCompliance.blockingReasons]);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    branch: optionsPayload.branch,
    testsPassed: optionsPayload.testsPassed,
    requiredTypedDiscardConfirmation: optionsPayload.typedDiscardConfirmation,
    options,
    latestDecision,
    shipBlocked,
    blockingReasons,
    securityCards: securityAudit.kanbanCards,
    provenanceCompliance: provenanceCompliance.summary,
    verification
  };
}

function createBranchFinisherVerificationSummary(state: GameState): BranchFinisherVerificationSummary {
  const verificationQueue = createVerificationQueueView(state);
  const evidencePacks = createVerificationEvidencePackView(state);
  const blockingItems = verificationQueue.items.filter(
    (item) => item.queueStatus === 'rejected' || item.queueStatus === 'needs_work'
  );
  const blockingReasons = blockingItems.map((item) => {
    if (item.queueStatus === 'rejected') {
      return `Task ${item.taskTitle} is rejected in verification.`;
    }

    return `Task ${item.taskTitle} still needs work before verification can complete.`;
  });

  if (evidencePacks.summary.unattestedCount > 0) {
    blockingReasons.push('Some verification evidence packs are still unattested.');
  }

  if (evidencePacks.summary.missingEvidenceCount > 0) {
    blockingReasons.push('Some verification evidence packs are missing evidence records.');
  }

  return {
    queueCount: verificationQueue.metrics.itemCount,
    queuedCount: verificationQueue.metrics.queuedCount,
    verifyingCount: verificationQueue.metrics.verifyingCount,
    acceptedCount: verificationQueue.metrics.acceptedCount,
    rejectedCount: verificationQueue.metrics.rejectedCount,
    needsWorkCount: verificationQueue.metrics.needsWorkCount,
    blockingItemCount: blockingItems.length,
    blockingTaskIds: blockingItems.map((item) => item.taskId),
    blockingReasons: uniqueStrings(blockingReasons),
    evidencePackCount: evidencePacks.summary.packCount,
    attestedPackCount: evidencePacks.summary.attestedCount,
    unattestedPackCount: evidencePacks.summary.unattestedCount,
    missingEvidencePackCount: evidencePacks.summary.missingEvidenceCount
  };
}

function collectSecurityFindings(state: GameState): SecurityAuditFinding[] {
  const findingsById = new Map<string, SecurityAuditFinding>();
  const orderedWorkflowSteps = [...state.recentWorkflowSteps].sort((left, right) => right.sequenceId - left.sequenceId);

  for (const workflowStep of orderedWorkflowSteps) {
    if (!isSecurityFindingStep(workflowStep)) {
      continue;
    }

    const finding = toSecurityAuditFinding(workflowStep);
    if (finding === null || findingsById.has(finding.findingId)) {
      continue;
    }

    findingsById.set(finding.findingId, finding);
  }

  return [...findingsById.values()].sort(compareSecurityFindings);
}

function toSecurityAuditFinding(workflowStep: WorkflowStepLogEntry): SecurityAuditFinding | null {
  const metadata = workflowStep.metadata as Record<string, JsonValue>;
  const findingId =
    readMetadataString(metadata, ['findingId', 'finding_id']) ??
    `finding-${workflowStep.sequenceId}`;
  const title = readMetadataString(metadata, ['title', 'findingTitle', 'finding_title']) ?? workflowStep.step;
  const confidenceScore = readMetadataNumber(metadata, [
    'confidenceScore',
    'confidence_score',
    'confidence',
    'trustScore',
    'trust_score'
  ]);
  const surfaceId =
    readMetadataString(metadata, ['surfaceId', 'surface_id', 'surface']) ?? 'unknown-surface';
  const exploitScenario =
    readMetadataString(metadata, ['exploitScenario', 'exploit_scenario', 'exploit']) ?? workflowStep.detail;

  const candidate: Record<string, unknown> = {
    findingId,
    title,
    severity: readMetadataString(metadata, ['severity']) ?? 'medium',
    status: readMetadataString(metadata, ['status']) ?? 'open',
    confidenceScore: confidenceScore ?? 0,
    exploitScenario,
    surfaceId,
    controls: readMetadataStringArray(metadata, ['controls', 'controlsExecuted', 'controls_executed'])
  };

  const origin = readMetadataString(metadata, ['origin', 'provenance', 'source']);
  if (origin !== null) {
    candidate.origin = origin;
  }

  const requiredPolicy = readMetadataString(metadata, ['requiredPolicy', 'required_policy', 'policy']);
  if (requiredPolicy !== null) {
    candidate.requiredPolicy = requiredPolicy;
  }

  const trustStatus = readMetadataString(metadata, ['trustStatus', 'trust_status']);
  if (trustStatus !== null) {
    candidate.trustStatus = trustStatus;
  }

  const owaspCategory = readMetadataString(metadata, ['owaspCategory', 'owasp_category']);
  if (owaspCategory !== null) {
    candidate.owaspCategory = owaspCategory;
  }

  const strideCategory = readMetadataString(metadata, ['strideCategory', 'stride_category']);
  if (strideCategory !== null) {
    candidate.strideCategory = strideCategory;
  }

  const agenticSkillCategory = readMetadataString(metadata, ['agenticSkillCategory', 'agentic_skill_category']);
  if (agenticSkillCategory !== null) {
    candidate.agenticSkillCategory = agenticSkillCategory;
  }

  const parsed = SecurityFindingPayloadSchema.safeParse(candidate);
  if (!parsed.success) {
    return null;
  }

  const finding = parsed.data;
  const isPublished = finding.confidenceScore >= SECURITY_CONFIDENCE_PUBLISH_THRESHOLD;
  const hasProvenanceGap =
    (readMetadataBoolean(metadata, ['missingProvenance', 'missing_provenance']) ?? false) || finding.origin === undefined;
  const hasPolicyGap =
    (readMetadataBoolean(metadata, ['missingPolicy', 'missing_policy']) ?? false) || finding.requiredPolicy === undefined;
  const hasTrustBlock = finding.trustStatus === 'blocked';
  const normalizedOwaspCategory = normalizeOwaspCategory(finding.owaspCategory);
  const owaspFocusAreas = deriveOwaspFocusAreas({
    normalizedOwaspCategory,
    hasProvenanceGap,
    hasPolicyGap,
    hasTrustBlock
  });
  const isCriticalOpen = finding.status === 'open' && finding.severity === 'critical';

  const blockingReasons: string[] = [];
  if (isPublished && isCriticalOpen) {
    blockingReasons.push('Critical security finding is still open.');
  }

  if (isPublished && finding.status === 'open' && hasProvenanceGap) {
    blockingReasons.push(`Surface ${finding.surfaceId} is missing provenance.`);
  }

  if (isPublished && finding.status === 'open' && hasPolicyGap) {
    blockingReasons.push(`Surface ${finding.surfaceId} is missing required policy.`);
  }

  if (isPublished && finding.status === 'open' && hasTrustBlock) {
    blockingReasons.push(`Surface ${finding.surfaceId} has blocked trust status.`);
  }

  return {
    ...finding,
    sequenceId: workflowStep.sequenceId,
    timestamp: workflowStep.timestamp,
    traceId: workflowStep.traceId ?? null,
    taskId: workflowStep.taskId ?? null,
    normalizedOwaspCategory,
    owaspFocusAreas,
    isPublished,
    hasProvenanceGap,
    hasPolicyGap,
    hasTrustBlock,
    blocksShip: blockingReasons.length > 0,
    blockingReasons
  };
}

function createSurfaceMatrix(findings: readonly SecurityAuditFinding[]): SecuritySurfaceSummary[] {
  const surfaceBuckets = new Map<string, SecuritySurfaceSummary>();

  for (const finding of findings) {
    const current =
      surfaceBuckets.get(finding.surfaceId) ??
      {
        surfaceId: finding.surfaceId,
        findingCount: 0,
        openFindingCount: 0,
        blockingFindingCount: 0
      };

    const next: SecuritySurfaceSummary = {
      surfaceId: finding.surfaceId,
      findingCount: current.findingCount + 1,
      openFindingCount: current.openFindingCount + (finding.status === 'open' ? 1 : 0),
      blockingFindingCount: current.blockingFindingCount + (finding.status === 'open' && finding.blocksShip ? 1 : 0)
    };

    surfaceBuckets.set(finding.surfaceId, next);
  }

  return [...surfaceBuckets.values()].sort((left, right) => left.surfaceId.localeCompare(right.surfaceId));
}

function createOwaspCategoryMatrix(findings: readonly SecurityAuditFinding[]): SecurityOwaspCategorySummary[] {
  const categoryBuckets = new Map<string, SecurityOwaspCategorySummary>();

  for (const finding of findings) {
    if (finding.normalizedOwaspCategory === null) {
      continue;
    }

    const current =
      categoryBuckets.get(finding.normalizedOwaspCategory) ??
      {
        category: finding.normalizedOwaspCategory,
        label: describeOwaspCategory(finding.normalizedOwaspCategory),
        findingCount: 0,
        openFindingCount: 0,
        blockingFindingCount: 0
      };

    categoryBuckets.set(finding.normalizedOwaspCategory, {
      category: current.category,
      label: current.label,
      findingCount: current.findingCount + 1,
      openFindingCount: current.openFindingCount + (finding.status === 'open' ? 1 : 0),
      blockingFindingCount: current.blockingFindingCount + (finding.status === 'open' && finding.blocksShip ? 1 : 0)
    });
  }

  return [...categoryBuckets.values()].sort(compareOwaspCategorySummaries);
}

function createOwaspFocusMatrix(findings: readonly SecurityAuditFinding[]): SecurityOwaspFocusSummary[] {
  const focusBuckets = new Map<OwaspFocusArea, SecurityOwaspFocusSummary>();

  for (const finding of findings) {
    const explicitFocusArea =
      finding.normalizedOwaspCategory === null ? null : readOwaspFocusAreaFromCategory(finding.normalizedOwaspCategory);

    for (const focusArea of finding.owaspFocusAreas) {
      const current =
        focusBuckets.get(focusArea) ??
        {
          focusArea,
          label: getSecurityOwaspFocusLabel(focusArea),
          findingCount: 0,
          openFindingCount: 0,
          blockingFindingCount: 0,
          explicitCategoryCount: 0,
          derivedSignalCount: 0
        };

      focusBuckets.set(focusArea, {
        focusArea,
        label: current.label,
        findingCount: current.findingCount + 1,
        openFindingCount: current.openFindingCount + (finding.status === 'open' ? 1 : 0),
        blockingFindingCount: current.blockingFindingCount + (finding.status === 'open' && finding.blocksShip ? 1 : 0),
        explicitCategoryCount: current.explicitCategoryCount + (explicitFocusArea === focusArea ? 1 : 0),
        derivedSignalCount: current.derivedSignalCount + (explicitFocusArea === focusArea ? 0 : 1)
      });
    }
  }

  return [...focusBuckets.values()].sort(compareOwaspFocusSummaries);
}

function toSecurityKanbanCard(finding: SecurityAuditFinding): SecurityKanbanCard {
  return {
    id: `security-${finding.findingId}`,
    findingId: finding.findingId,
    title: `[${finding.severity.toUpperCase()}] ${finding.title}`,
    status: finding.blocksShip ? 'review' : 'todo',
    severity: finding.severity,
    detail: `${finding.exploitScenario} (surface ${finding.surfaceId})`,
    exploitScenario: finding.exploitScenario,
    surfaceId: finding.surfaceId,
    taskId: finding.taskId,
    traceId: finding.traceId,
    blocksShip: finding.blocksShip,
    sequenceId: finding.sequenceId,
    timestamp: finding.timestamp
  };
}

function normalizeOwaspCategory(value: string | undefined): string | null {
  if (value === undefined) {
    return null;
  }

  const normalized = value.trim();
  if (normalized.length === 0) {
    return null;
  }

  const riskCode = readOwaspRiskCode(normalized);
  return riskCode ?? normalized;
}

function deriveOwaspFocusAreas(input: {
  normalizedOwaspCategory: string | null;
  hasProvenanceGap: boolean;
  hasPolicyGap: boolean;
  hasTrustBlock: boolean;
}): OwaspFocusArea[] {
  const focusAreas = new Set<OwaspFocusArea>();
  const explicitFocusArea =
    input.normalizedOwaspCategory === null ? null : readOwaspFocusAreaFromCategory(input.normalizedOwaspCategory);

  if (explicitFocusArea !== null) {
    focusAreas.add(explicitFocusArea);
  }

  if (input.hasPolicyGap || input.hasTrustBlock) {
    focusAreas.add('excessive_agency');
  }

  if (input.hasProvenanceGap) {
    focusAreas.add('supply_chain');
  }

  return OWASP_FOCUS_AREA_ORDER.filter((focusArea) => focusAreas.has(focusArea));
}

function readOwaspRiskCode(value: string): string | null {
  const match = /llm0?(\d{1,2})/iu.exec(value);
  if (match === null) {
    return null;
  }

  const riskIndex = match[1];
  if (riskIndex === undefined) {
    return null;
  }

  return `LLM${riskIndex.padStart(2, '0')}`;
}

function readOwaspFocusAreaFromCategory(category: string): OwaspFocusArea | null {
  const riskCode = readOwaspRiskCode(category);
  if (riskCode === null) {
    return null;
  }

  return OWASP_FOCUS_BY_RISK_CODE[riskCode] ?? null;
}

function describeOwaspCategory(category: string): string {
  const focusArea = readOwaspFocusAreaFromCategory(category);
  return focusArea === null ? category : getSecurityOwaspFocusLabel(focusArea);
}

function readLatestBranchFinishOptions(state: GameState) {
  const step = [...state.recentWorkflowSteps]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .find((entry) => normalizeToken(entry.sourceEventType) === 'branch_finish_options');

  const metadata = (step?.metadata ?? {}) as Record<string, JsonValue>;
  const allowedOptions = readMetadataStringArray(metadata, ['allowedOptions', 'allowed_options'])
    .map((value) => normalizeToken(value))
    .filter((value): value is BranchFinishOption => isBranchFinishOption(value));
  const testsPassed = readMetadataBoolean(metadata, ['testsPassed', 'tests_passed']) ?? false;

  return BranchFinishOptionsPayloadSchema.parse({
    branch: readMetadataString(metadata, ['branch', 'branch_name']) ?? 'current-branch',
    testsPassed,
    ...(allowedOptions.length === 0 ? {} : { allowedOptions }),
    typedDiscardConfirmation:
      readMetadataString(metadata, ['typedDiscardConfirmation', 'typed_discard_confirmation']) ?? 'DISCARD'
  });
}

function evaluateLatestBranchFinishDecision(
  state: GameState,
  optionsPayload: ReturnType<typeof readLatestBranchFinishOptions>,
  optionStates: readonly BranchFinishOptionState[]
): BranchFinishDecisionEvaluation | null {
  const step = [...state.recentWorkflowSteps]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .find((entry) => normalizeToken(entry.sourceEventType) === 'branch_finish_decision');

  if (step === undefined) {
    return null;
  }

  const metadata = step.metadata as Record<string, JsonValue>;
  const parsedDecision = BranchFinishDecisionPayloadSchema.safeParse({
    branch: readMetadataString(metadata, ['branch', 'branch_name']) ?? optionsPayload.branch,
    selectedOption:
      normalizeToken(readMetadataString(metadata, ['selectedOption', 'selected_option']) ?? '') ||
      'keep',
    typedConfirmation: readMetadataString(metadata, ['typedConfirmation', 'typed_confirmation']) ?? ''
  });

  if (!parsedDecision.success) {
    return null;
  }

  const decision = parsedDecision.data;
  const selectedOption = optionStates.find((optionState) => optionState.option === decision.selectedOption);
  const blockedReasons = [...(selectedOption?.blockedReasons ?? ['Selected branch finish option is unknown.'])];

  if (
    decision.selectedOption === 'discard' &&
    decision.typedConfirmation.trim() !== optionsPayload.typedDiscardConfirmation
  ) {
    blockedReasons.push('Discard option requires an exact typed confirmation.');
  }

  return {
    branch: decision.branch,
    selectedOption: decision.selectedOption,
    typedConfirmation: decision.typedConfirmation,
    allowed: blockedReasons.length === 0,
    blockedReasons
  };
}

function evaluateBranchFinishOption(
  option: BranchFinishOption,
  optionsPayload: ReturnType<typeof readLatestBranchFinishOptions>,
  securityAudit: SecurityAuditView,
  provenanceCompliance: ReturnType<typeof createProvenanceComplianceView>
): BranchFinishOptionState {
  const blockedReasons: string[] = [];

  if (!optionsPayload.allowedOptions.includes(option)) {
    blockedReasons.push('Option is disabled by branch finisher policy matrix.');
  }

  if (isDestructiveBranchOption(option) && !optionsPayload.testsPassed) {
    blockedReasons.push('Tests must pass before destructive branch closure actions.');
  }

  if ((option === 'merge' || option === 'pr') && securityAudit.shipBlocked) {
    blockedReasons.push('Security audit has unresolved blocking findings.');
  }

  if ((option === 'merge' || option === 'pr') && provenanceCompliance.shipBlocked) {
    blockedReasons.push('Provenance compliance has unresolved blocking entries.');
  }

  return {
    option,
    allowed: blockedReasons.length === 0,
    blockedReasons,
    requiresTypedConfirmation: option === 'discard',
    requiredTypedConfirmation: option === 'discard' ? optionsPayload.typedDiscardConfirmation : null
  };
}

function isSecurityFindingStep(workflowStep: WorkflowStepLogEntry): boolean {
  const source = normalizeToken(workflowStep.sourceEventType);
  if (source === 'security_finding' || source === 'security_audit_finding') {
    return true;
  }

  const metadata = workflowStep.metadata as Record<string, JsonValue>;
  return normalizeToken(readMetadataString(metadata, ['topic']) ?? '') === 'security_finding';
}

function isDestructiveBranchOption(option: BranchFinishOption): boolean {
  return option === 'merge' || option === 'pr' || option === 'discard';
}

function isBranchFinishOption(value: string): value is BranchFinishOption {
  return BRANCH_FINISH_OPTION_ORDER.includes(value as BranchFinishOption);
}

function compareSecurityFindings(left: SecurityAuditFinding, right: SecurityAuditFinding): number {
  const blockingDelta = Number(right.blocksShip) - Number(left.blocksShip);
  if (blockingDelta !== 0) {
    return blockingDelta;
  }

  const severityDelta = SECURITY_SEVERITY_RANK[left.severity] - SECURITY_SEVERITY_RANK[right.severity];
  if (severityDelta !== 0) {
    return severityDelta;
  }

  return right.sequenceId - left.sequenceId;
}

function compareSecurityKanbanCards(left: SecurityKanbanCard, right: SecurityKanbanCard): number {
  const blockingDelta = Number(right.blocksShip) - Number(left.blocksShip);
  if (blockingDelta !== 0) {
    return blockingDelta;
  }

  const severityDelta = SECURITY_SEVERITY_RANK[left.severity] - SECURITY_SEVERITY_RANK[right.severity];
  if (severityDelta !== 0) {
    return severityDelta;
  }

  return right.sequenceId - left.sequenceId;
}

function compareOwaspCategorySummaries(left: SecurityOwaspCategorySummary, right: SecurityOwaspCategorySummary): number {
  if (left.blockingFindingCount !== right.blockingFindingCount) {
    return right.blockingFindingCount - left.blockingFindingCount;
  }

  if (left.openFindingCount !== right.openFindingCount) {
    return right.openFindingCount - left.openFindingCount;
  }

  if (left.findingCount !== right.findingCount) {
    return right.findingCount - left.findingCount;
  }

  return left.label.localeCompare(right.label);
}

function compareOwaspFocusSummaries(left: SecurityOwaspFocusSummary, right: SecurityOwaspFocusSummary): number {
  if (left.blockingFindingCount !== right.blockingFindingCount) {
    return right.blockingFindingCount - left.blockingFindingCount;
  }

  if (left.openFindingCount !== right.openFindingCount) {
    return right.openFindingCount - left.openFindingCount;
  }

  if (left.findingCount !== right.findingCount) {
    return right.findingCount - left.findingCount;
  }

  return OWASP_FOCUS_AREA_ORDER.indexOf(left.focusArea) - OWASP_FOCUS_AREA_ORDER.indexOf(right.focusArea);
}

function readMetadataString(metadata: Record<string, JsonValue>, keys: readonly string[]): string | null {
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

function readMetadataNumber(metadata: Record<string, JsonValue>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }

    if (typeof value === 'string') {
      const parsedValue = Number(value);
      if (Number.isFinite(parsedValue)) {
        return parsedValue;
      }
    }
  }

  return null;
}

function readMetadataBoolean(metadata: Record<string, JsonValue>, keys: readonly string[]): boolean | null {
  for (const key of keys) {
    const value = metadata[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function readMetadataStringArray(metadata: Record<string, JsonValue>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = metadata[key];
    if (!Array.isArray(value)) {
      continue;
    }

    const normalizedValues = value
      .filter((entry): entry is string => typeof entry === 'string')
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
    if (normalizedValues.length > 0) {
      return uniqueStrings(normalizedValues);
    }
  }

  return [];
}

function normalizeToken(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, '_');
}

function uniqueStrings(values: readonly string[]): string[] {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}