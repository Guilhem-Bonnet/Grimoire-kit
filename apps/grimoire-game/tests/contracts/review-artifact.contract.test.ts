import {
  type ReviewArtifact,
  ReviewArtifactSchema,
  createHostReviewArtifactEvent,
  parseServerEvent
} from '../../src/contracts/events';

const REVIEW_ARTIFACT: ReviewArtifact = {
  reviewId: 'review-001',
  hostId: 'host-copilot',
  sourceType: 'copilot_review',
  subjectRef: 'task:task-auth',
  verdict: 'warn',
  findings: [
    {
      id: 'finding-001',
      severity: 'high',
      message: 'Permission prompt is not yet enforced on preview mode.',
      resolutionStatus: 'open'
    },
    {
      id: 'finding-002',
      severity: 'info',
      message: 'Host dashboard could expose connector version.',
      resolutionStatus: 'acknowledged'
    }
  ],
  linkedEvidenceRefs: ['artifact://host-bridge/review-001'],
  importedAt: '2026-04-10T10:20:00.000Z',
  traceId: 'session-host-001',
  taskId: 'task-auth'
};

describe('host review artifact contract', () => {
  it('accepts canonical imported review artifacts and preserves severities', () => {
    const review = ReviewArtifactSchema.parse(REVIEW_ARTIFACT);

    expect(review.verdict).toBe('warn');
    expect(review.findings.map((finding) => finding.severity)).toEqual(['high', 'info']);
    expect(review.linkedEvidenceRefs).toEqual(['artifact://host-bridge/review-001']);
  });

  it('emits replay-safe review events for imported host findings', () => {
    const event = parseServerEvent(
      createHostReviewArtifactEvent(
        41,
        {
          review: REVIEW_ARTIFACT,
          meta: {
            traceId: 'session-host-001',
            taskId: 'task-auth',
            correlationId: 'corr-review-001',
            hostId: 'host-copilot'
          }
        },
        {
          timestamp: '2026-04-10T10:20:41.000Z'
        }
      )
    );

    expect(event.type).toBe('HOST_REVIEW_ARTIFACT');
    if (event.type !== 'HOST_REVIEW_ARTIFACT') {
      throw new Error('Expected a HOST_REVIEW_ARTIFACT event.');
    }
    expect(event.review.findings).toHaveLength(2);
    expect(event.meta.correlationId).toBe('corr-review-001');
  });

  it('rejects review artifacts without subject or actionable findings', () => {
    expect(() =>
      ReviewArtifactSchema.parse({
        ...REVIEW_ARTIFACT,
        subjectRef: ''
      })
    ).toThrow();

    expect(() =>
      ReviewArtifactSchema.parse({
        ...REVIEW_ARTIFACT,
        findings: []
      })
    ).toThrow();
  });
});