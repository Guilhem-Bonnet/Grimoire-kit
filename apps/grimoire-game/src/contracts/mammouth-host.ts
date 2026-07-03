import { z } from 'zod';

import {
  CanonicalEnvelopePilotSchema,
  ContextLedgerEntrySchema,
  HostPermissionModeSchema,
  ReviewArtifactSchema
} from './schemas';

const NonEmptyStringSchema = z.string().min(1);

export const MAMMOUTH_HOST_ADAPTER_VERSION = 'mammouth-host-v1' as const;

export const MammouthHandoffTargetSchema = z
  .object({
    hostId: NonEmptyStringSchema,
    displayName: NonEmptyStringSchema,
    permissionMode: HostPermissionModeSchema,
    reviewChannels: z.array(NonEmptyStringSchema),
    contextSources: z.array(NonEmptyStringSchema),
    toolProviders: z.array(NonEmptyStringSchema)
  })
  .strict();

export const MammouthMissionPackSchema = z
  .object({
    objective: NonEmptyStringSchema,
    scope: z.array(NonEmptyStringSchema),
    canonicalSourceRefs: z.array(NonEmptyStringSchema),
    constraints: z.array(NonEmptyStringSchema),
    expectedOutput: NonEmptyStringSchema.nullable(),
    expectedProofRefs: z.array(NonEmptyStringSchema),
    mode: NonEmptyStringSchema.nullable()
  })
  .strict();

export const MammouthHandoffInstructionsSchema = z
  .object({
    requireRepoTruth: z.boolean(),
    requireEvidence: z.boolean(),
    responseFormat: z.literal('review_artifact')
  })
  .strict();

export const MammouthHandoffRequestSchema = z
  .object({
    version: z.literal(MAMMOUTH_HOST_ADAPTER_VERSION),
    packetId: NonEmptyStringSchema,
    taskId: NonEmptyStringSchema,
    taskTitle: NonEmptyStringSchema.nullable(),
    traceId: NonEmptyStringSchema.nullable(),
    sessionTitle: NonEmptyStringSchema.nullable(),
    targetHost: MammouthHandoffTargetSchema,
    missionPack: MammouthMissionPackSchema,
    canonicalEnvelopes: z.array(CanonicalEnvelopePilotSchema).min(1),
    priorReviews: z.array(ReviewArtifactSchema),
    importedContext: z.array(ContextLedgerEntrySchema),
    instructions: MammouthHandoffInstructionsSchema
  })
  .strict();

export const MammouthReviewResponseMetaSchema = z
  .object({
    provider: NonEmptyStringSchema,
    model: NonEmptyStringSchema,
    latencyMs: z.number().min(0).optional()
  })
  .strict();

export const MammouthReviewResponseSchema = z
  .object({
    version: z.literal(MAMMOUTH_HOST_ADAPTER_VERSION),
    packetId: NonEmptyStringSchema,
    review: ReviewArtifactSchema,
    importedContext: z.array(ContextLedgerEntrySchema).default([]),
    meta: MammouthReviewResponseMetaSchema.optional()
  })
  .strict();

export type MammouthHandoffTarget = z.infer<typeof MammouthHandoffTargetSchema>;
export type MammouthMissionPack = z.infer<typeof MammouthMissionPackSchema>;
export type MammouthHandoffInstructions = z.infer<typeof MammouthHandoffInstructionsSchema>;
export type MammouthHandoffRequest = z.infer<typeof MammouthHandoffRequestSchema>;
export type MammouthReviewResponseMeta = z.infer<typeof MammouthReviewResponseMetaSchema>;
export type MammouthReviewResponse = z.infer<typeof MammouthReviewResponseSchema>;

export function parseMammouthHandoffRequest(input: unknown): MammouthHandoffRequest {
  return MammouthHandoffRequestSchema.parse(input);
}

export function parseMammouthReviewResponse(input: unknown): MammouthReviewResponse {
  return MammouthReviewResponseSchema.parse(input);
}