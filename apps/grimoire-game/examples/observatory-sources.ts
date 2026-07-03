import { constants as fsConstants } from 'node:fs';
import { access, copyFile, mkdir, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export interface ObservatorySource {
  id: string;
  label: string;
  scope: string;
  filePath: string;
  url: string;
  available: boolean;
}

export interface MaterializedObservatorySource extends ObservatorySource {
  browserPath: string | null;
}

export interface ObservatoryManifest {
  generatedAt: string;
  sources: readonly {
    id: string;
    label: string;
    scope: string;
    available: boolean;
    browserPath: string | null;
  }[];
}

function getObservatorySourceCandidates(projectRoot: string): readonly Omit<ObservatorySource, 'url' | 'available'>[] {
  return [
    {
      id: 'runtime-output-observatory',
      label: 'Observatory runtime',
      scope: '_grimoire-runtime-output',
      filePath: resolve(projectRoot, '_grimoire-runtime-output/observatory.html')
    },
    {
      id: 'product-output-observatory',
      label: 'Observatory produit',
      scope: '_grimoire-output',
      filePath: resolve(projectRoot, '_grimoire-output/observatory.html')
    }
  ];
}

export async function collectObservatorySources(projectRoot: string): Promise<readonly ObservatorySource[]> {
  const candidates = getObservatorySourceCandidates(projectRoot);
  const sources: ObservatorySource[] = [];

  for (const candidate of candidates) {
    let available = false;
    try {
      await access(candidate.filePath, fsConstants.F_OK);
      available = true;
    } catch {
      available = false;
    }

    sources.push({
      ...candidate,
      url: pathToFileURL(candidate.filePath).href,
      available
    });
  }

  return sources;
}

export async function materializeObservatorySources(
  projectRoot: string,
  outputDir: string
): Promise<readonly MaterializedObservatorySource[]> {
  const sources = await collectObservatorySources(projectRoot);

  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });

  const materializedSources: MaterializedObservatorySource[] = [];

  for (const source of sources) {
    if (!source.available) {
      materializedSources.push({
        ...source,
        browserPath: null
      });
      continue;
    }

    const browserPath = `./observatory/${source.id}.html`;
    await copyFile(source.filePath, resolve(outputDir, `${source.id}.html`));
    materializedSources.push({
      ...source,
      browserPath
    });
  }

  return materializedSources;
}

export async function writeObservatoryManifest(
  manifestPath: string,
  sources: readonly MaterializedObservatorySource[]
): Promise<void> {
  const manifest: ObservatoryManifest = {
    generatedAt: new Date().toISOString(),
    sources: sources.map((source) => ({
      id: source.id,
      label: source.label,
      scope: source.scope,
      available: source.available,
      browserPath: source.browserPath
    }))
  };

  await mkdir(dirname(manifestPath), { recursive: true });
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
}