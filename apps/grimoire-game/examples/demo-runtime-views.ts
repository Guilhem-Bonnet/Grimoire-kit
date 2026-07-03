import { createRuntimeViewsDemoData } from './runtime-views-demo-data';

function printSection(title: string, payload: unknown): void {
  console.log(`\n=== ${title} ===`);
  console.log(JSON.stringify(payload, null, 2));
}

function runDemo(): void {
  const { scenarios } = createRuntimeViewsDemoData();

  for (const scenario of scenarios) {
    printSection(`SCENARIO ${scenario.title.toUpperCase()}`, {
      id: scenario.id,
      outcome: scenario.outcome,
      description: scenario.description,
      tags: scenario.tags,
      walkthrough: scenario.walkthrough
    });

    printSection('POWER CARDS VIEW', {
      summary: scenario.powerCardsView.summary,
      cards: scenario.powerCardsView.cards.map((card) => ({
        cardId: card.cardId,
        target: `${card.targetKind}:${card.targetId}`,
        runtimeEnabled: card.runtimeEnabled,
        storageEnabled: card.storageEnabled,
        persistenceStatus: card.persistenceStatus,
        trustStatus: card.trustStatus,
        issueCodes: card.issueCodes,
        diagnostic: card.diagnostic
      }))
    });

    printSection('PROVENANCE COMPLIANCE VIEW', {
      shipBlocked: scenario.provenanceView.shipBlocked,
      summary: scenario.provenanceView.summary,
      blockingReasons: scenario.provenanceView.blockingReasons,
      attributionBundles: scenario.provenanceView.attributionBundles
    });

    printSection('BRANCH FINISHER VIEW', {
      branch: scenario.branchFinisherView.branch,
      shipBlocked: scenario.branchFinisherView.shipBlocked,
      blockingReasons: scenario.branchFinisherView.blockingReasons,
      provenanceCompliance: scenario.branchFinisherView.provenanceCompliance,
      options: scenario.branchFinisherView.options.map((option) => ({
        option: option.option,
        allowed: option.allowed,
        blockedReasons: option.blockedReasons
      }))
    });
  }
}

runDemo();