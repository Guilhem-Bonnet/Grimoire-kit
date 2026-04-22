import { cp, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { buildHookEventSnapshot } from '../src/server/hook-events-feed';
import { buildPheromoneBoardSnapshot } from '../src/server/pheromone-board-feed';
import { materializeObservatorySources, writeObservatoryManifest } from './observatory-sources';
import { materializeProofSources, writeProofManifest } from './proof-sources';

async function main(): Promise<void> {
  const packageRoot = process.cwd();
  const projectRoot = resolve(packageRoot, '../../..');
  const observatoryDir = resolve(packageRoot, '.generated/public/observatory');
  const manifestPath = resolve(observatoryDir, 'manifest.json');
  const proofsDir = resolve(packageRoot, '.generated/public/proofs');
  const proofManifestPath = resolve(proofsDir, 'manifest.json');
  const sources = await materializeObservatorySources(projectRoot, observatoryDir);
  const proofSources = await materializeProofSources(projectRoot, proofsDir);

  await writeObservatoryManifest(manifestPath, sources);
  await writeProofManifest(proofManifestPath, proofSources.latestRunId, proofSources.sources);

  // Mirror durable static assets from /public into the publicDir used by Vite.
  const switchboardSrc = resolve(packageRoot, 'public/switchboard');
  const switchboardDst = resolve(packageRoot, '.generated/public/switchboard');
  await mkdir(switchboardDst, { recursive: true });
  await cp(switchboardSrc, switchboardDst, { recursive: true });

  const pixelAgentsSrc = resolve(packageRoot, 'public/pixel-agents');
  const pixelAgentsDst = resolve(packageRoot, '.generated/public/pixel-agents');
  await mkdir(pixelAgentsDst, { recursive: true });
  await cp(pixelAgentsSrc, pixelAgentsDst, { recursive: true });

  // V1.5b — publish a static snapshot of the hook-events ledger for the
  // browser to poll. Missing ledger is not an error: writer returns an
  // empty snapshot so the client renders a neutral "waiting" state.
  const hookEventsPath = resolve(packageRoot, '.generated/public/hook-events.json');
  const hookEventsSnapshot = buildHookEventSnapshot({
    projectRoot,
    limit: 500
  });
  await writeFile(hookEventsPath, JSON.stringify(hookEventsSnapshot, null, 2), 'utf8');

  // V4.4.b — publish a static snapshot of the pheromone board (BM-20).
  // Missing board is not an error: writer returns an empty board so the
  // observability surface renders a neutral "no signals" state.
  const pheromoneBoardPath = resolve(packageRoot, '.generated/public/pheromone-board.json');
  const pheromoneBoardSnapshot = buildPheromoneBoardSnapshot({
    projectRoot,
    limit: 500
  });
  await writeFile(
    pheromoneBoardPath,
    JSON.stringify(pheromoneBoardSnapshot, null, 2),
    'utf8'
  );

  console.log(
    JSON.stringify(
      {
        manifestPath,
        availableSources: sources.filter((source) => source.available).map((source) => source.id),
        proofManifestPath,
        latestProofRunId: proofSources.latestRunId,
        availableProofSources: proofSources.sources.filter((source) => source.available).map((source) => source.id),
        hookEventsPath,
        hookEventsCount: hookEventsSnapshot.events.length,
        pheromoneBoardPath,
        pheromoneBoardActive: pheromoneBoardSnapshot.counters.active,
        pheromoneBoardHeatmapCells: pheromoneBoardSnapshot.heatmap.length
      },
      null,
      2
    )
  );
}

main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});