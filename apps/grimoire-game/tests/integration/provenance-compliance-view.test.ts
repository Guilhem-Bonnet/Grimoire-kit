import type { GameState } from '../../src/state/game-state';
import { createBranchFinisherView } from '../../src/state/branch-finisher-view';
import { createProvenanceComplianceView } from '../../src/state/provenance-compliance-view';

function createBaseState(): GameState {
  return {
    protocolVersion: 'v1',
    lastSequenceId: 32,
    hydratedAt: '2026-04-12T01:30:00.000Z',
    agents: {},
    tasks: {},
    config: {
      'provenanceRegistry.snapshot': {
        'plugin.host-review': {
          kind: 'plugin',
          label: 'Host Review Relay',
          sourceRef: 'repo://plugins/host-review',
          licenseId: 'MIT',
          attributionRequired: false,
          attributionRefs: []
        },
        'asset.hero-banner': {
          kind: 'asset',
          label: 'Hero Banner',
          sourceRef: 'asset://hero-banner/source',
          licenseId: 'CC-BY-4.0',
          attributionRequired: true,
          attributionRefs: ['artifact://attribution/hero-banner']
        }
      }
    },
    recentToolCalls: [],
    recentWorkflowSteps: [
      {
        step: 'Branch finisher options updated',
        detail: 'feature/provenance-clean',
        sourceEventType: 'branch_finish_options',
        metadata: {
          branch: 'feature/provenance-clean',
          testsPassed: true,
          allowedOptions: ['merge', 'pr', 'keep', 'discard']
        },
        sequenceId: 32,
        timestamp: '2026-04-12T01:30:32.000Z'
      }
    ],
    hostBindings: {},
    recentHostInvocationDecisions: [],
    recentHostContextEntries: [],
    recentHostReviews: [],
    lastErrors: []
  };
}

describe('provenance compliance view', () => {
  it('builds a fail-closed compliance register and attribution bundles', () => {
    const state = {
      ...createBaseState(),
      config: {
        'provenanceRegistry.snapshot': {
          'plugin.host-review': {
            kind: 'plugin',
            label: 'Host Review Relay',
            sourceRef: 'repo://plugins/host-review',
            licenseId: 'MIT',
            attributionRequired: false,
            attributionRefs: []
          },
          'asset.hero-banner': {
            kind: 'asset',
            label: 'Hero Banner',
            sourceRef: 'asset://hero-banner/source',
            licenseId: 'CC-BY-4.0',
            attributionRequired: true,
            attributionRefs: ['artifact://attribution/hero-banner']
          },
          'plugin.unlicensed': {
            kind: 'plugin',
            label: 'Unlicensed Plugin',
            sourceRef: 'repo://plugins/unlicensed',
            attributionRequired: false,
            attributionRefs: []
          },
          'asset.missing-source': {
            kind: 'asset',
            label: 'Unknown Asset',
            licenseId: 'CC0-1.0',
            attributionRequired: false,
            attributionRefs: []
          }
        }
      }
    } satisfies GameState;

    const view = createProvenanceComplianceView(state);

    expect(view.summary).toEqual({
      entryCount: 4,
      compliantCount: 2,
      blockedEntryCount: 2,
      missingSourceCount: 1,
      missingLicenseCount: 1,
      missingAttributionCount: 0,
      attributionBundleCount: 1
    });
    expect(view.shipBlocked).toBe(true);
    expect(view.blockingReasons).toEqual(
      expect.arrayContaining([
        'Provenance entry Unlicensed Plugin is missing license metadata.',
        'Provenance entry Unknown Asset is missing source reference.'
      ])
    );
    expect(view.attributionBundles[0]).toMatchObject({
      bundleId: 'attribution://asset.hero-banner',
      entryIds: ['asset.hero-banner'],
      attributionRefs: ['artifact://attribution/hero-banner']
    });
  });

  it('blocks merge and PR options when provenance compliance is unresolved', () => {
    const state = {
      ...createBaseState(),
      config: {
        'provenanceRegistry.snapshot': {
          'plugin.host-review': {
            kind: 'plugin',
            label: 'Host Review Relay',
            sourceRef: 'repo://plugins/host-review',
            licenseId: 'MIT',
            attributionRequired: false,
            attributionRefs: []
          },
          'asset.hero-banner': {
            kind: 'asset',
            label: 'Hero Banner',
            sourceRef: 'asset://hero-banner/source',
            licenseId: 'CC-BY-4.0',
            attributionRequired: true,
            attributionRefs: []
          }
        }
      }
    } satisfies GameState;

    const branchFinisher = createBranchFinisherView(state);
    const optionByName = Object.fromEntries(branchFinisher.options.map((option) => [option.option, option]));

    expect(branchFinisher.shipBlocked).toBe(true);
    expect(branchFinisher.blockingReasons).toContain(
      'Provenance entry Hero Banner requires an attribution bundle before merge.'
    );
    expect(branchFinisher.provenanceCompliance).toMatchObject({
      blockedEntryCount: 1,
      missingAttributionCount: 1
    });
    expect(optionByName.merge?.allowed).toBe(false);
    expect(optionByName.merge?.blockedReasons).toContain('Provenance compliance has unresolved blocking entries.');
    expect(optionByName.pr?.allowed).toBe(false);
    expect(optionByName.keep?.allowed).toBe(true);
  });
});