import { constants as fsConstants } from 'node:fs';
import { access, copyFile, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { dirname, extname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export type ProofSourceKind = 'summary' | 'decision' | 'artifact' | 'spec';
export type ProofSourceFormat = 'md' | 'json' | 'html' | 'tgz';
export type ProofSourceEmphasis = 'primary' | 'secondary';

export interface ProofSource {
  id: string;
  label: string;
  kind: ProofSourceKind;
  format: ProofSourceFormat;
  emphasis: ProofSourceEmphasis;
  description: string;
  runId: string | null;
  filePath: string;
  url: string;
  available: boolean;
}

export interface MaterializedProofSource extends ProofSource {
  browserPath: string | null;
}

export interface ProofManifest {
  generatedAt: string;
  latestRunId: string | null;
  sources: readonly {
    id: string;
    label: string;
    kind: ProofSourceKind;
    format: ProofSourceFormat;
    emphasis: ProofSourceEmphasis;
    description: string;
    runId: string | null;
    available: boolean;
    browserPath: string | null;
  }[];
}

interface ProofSourceCandidate {
  id: string;
  label: string;
  kind: ProofSourceKind;
  format: ProofSourceFormat;
  emphasis: ProofSourceEmphasis;
  description: string;
  runId: string | null;
  filePath: string;
}

export async function collectProofSources(projectRoot: string): Promise<readonly ProofSource[]> {
  const latestRunId = await resolveLatestRunId(projectRoot);
  const candidates = createProofSourceCandidates(projectRoot, latestRunId);
  const sources: ProofSource[] = [];

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

export async function materializeProofSources(
  projectRoot: string,
  outputDir: string
): Promise<{ latestRunId: string | null; sources: readonly MaterializedProofSource[] }> {
  const sources = await collectProofSources(projectRoot);
  const latestRunId = sources.find((source) => source.runId !== null)?.runId ?? null;

  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });

  const materializedSources: MaterializedProofSource[] = [];

  for (const source of sources) {
    if (!source.available) {
      materializedSources.push({
        ...source,
        browserPath: null
      });
      continue;
    }

    const extension = extname(source.filePath) || '.txt';
    const targetFileName = `${source.id}${extension}`;
    await copyFile(source.filePath, resolve(outputDir, targetFileName));
    materializedSources.push({
      ...source,
      browserPath: `./proofs/${targetFileName}`
    });
  }

  return {
    latestRunId,
    sources: materializedSources
  };
}

export async function writeProofManifest(
  manifestPath: string,
  latestRunId: string | null,
  sources: readonly MaterializedProofSource[]
): Promise<void> {
  const manifest: ProofManifest = {
    generatedAt: new Date().toISOString(),
    latestRunId,
    sources: sources.map((source) => ({
      id: source.id,
      label: source.label,
      kind: source.kind,
      format: source.format,
      emphasis: source.emphasis,
      description: source.description,
      runId: source.runId,
      available: source.available,
      browserPath: source.browserPath
    }))
  };

  await mkdir(dirname(manifestPath), { recursive: true });
  await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
}

async function resolveLatestRunId(projectRoot: string): Promise<string | null> {
  const runRoot = resolve(projectRoot, '_grimoire-runtime-output/test-artifacts/grimoire-game/v5');

  try {
    const entries = await readdir(runRoot, { withFileTypes: true });
    const runIds = entries
      .filter((entry) => entry.isDirectory() && /^RUN-\d{8}-\d{6}$/u.test(entry.name))
      .map((entry) => entry.name)
      .sort((left, right) => right.localeCompare(left));

    return runIds[0] ?? null;
  } catch {
    return null;
  }
}

function createProofSourceCandidates(projectRoot: string, latestRunId: string | null): readonly ProofSourceCandidate[] {
  const runRoot =
    latestRunId === null
      ? null
      : resolve(projectRoot, `_grimoire-runtime-output/test-artifacts/grimoire-game/v5/${latestRunId}`);

  return [
    {
      id: 'v5-scope',
      label: 'Scope V5',
      kind: 'spec',
      format: 'md',
      emphasis: 'secondary',
      description: 'Cadre produit, cuts de scope et evidence pack attendu.',
      runId: null,
      filePath: resolve(projectRoot, 'docs/exploitation/livrable-v5-agent-os-game-ui.md')
    },
    {
      id: 'execution-summary',
      label: 'Synthese d execution',
      kind: 'summary',
      format: 'md',
      emphasis: 'primary',
      description: 'Resume operatoire du run V5 de reference.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/execution-summary.md') : resolve(runRoot, 'summary/execution-summary.md')
    },
    {
      id: 'run-manifest',
      label: 'Manifest du run',
      kind: 'summary',
      format: 'json',
      emphasis: 'primary',
      description: 'Metriques, commandes et verdicts structures pour le run courant.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/manifest.json') : resolve(runRoot, 'summary/manifest.json')
    },
    {
      id: 'go-no-go',
      label: 'GO / NO-GO V5',
      kind: 'decision',
      format: 'md',
      emphasis: 'primary',
      description: 'Decision programme versus release immediate.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/GO-NO-GO-V5.md') : resolve(runRoot, 'decision/GO-NO-GO-V5.md')
    },
    {
      id: 'validation-matrix',
      label: 'Matrice de validation',
      kind: 'decision',
      format: 'md',
      emphasis: 'primary',
      description: 'Gates V5, preuves et verdict courant.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/MATRICE-validation-V5.md') : resolve(runRoot, 'decision/MATRICE-validation-V5.md')
    },
    {
      id: 'risk-register',
      label: 'Risk register',
      kind: 'decision',
      format: 'md',
      emphasis: 'primary',
      description: 'Risques ouverts, mitigation et etat de fermeture.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/RISK-REGISTER-V5.md') : resolve(runRoot, 'decision/RISK-REGISTER-V5.md')
    },
    {
      id: 'release-notes',
      label: 'Release notes V5',
      kind: 'decision',
      format: 'md',
      emphasis: 'secondary',
      description: 'Delta runtime, packaging et couverture de tests.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/RELEASE-NOTES-V5.md') : resolve(runRoot, 'decision/RELEASE-NOTES-V5.md')
    },
    {
      id: 'known-limitations',
      label: 'Known limitations',
      kind: 'decision',
      format: 'md',
      emphasis: 'secondary',
      description: 'Limites explicites pour eviter les faux positifs de maturite.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/KNOWN-LIMITATIONS-V5.md') : resolve(runRoot, 'decision/KNOWN-LIMITATIONS-V5.md')
    },
    {
      id: 'observability-summary',
      label: 'Synthese observability',
      kind: 'summary',
      format: 'md',
      emphasis: 'secondary',
      description: 'Preuves observability et assets generes pour le run.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/observability-summary.md') : resolve(runRoot, 'observability/summary.md')
    },
    {
      id: 'security-summary',
      label: 'Synthese securite',
      kind: 'summary',
      format: 'md',
      emphasis: 'secondary',
      description: 'Suites RBAC et auth reliees au candidat courant.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/security-summary.md') : resolve(runRoot, 'security/summary.md')
    },
    {
      id: 'perf-summary',
      label: 'Synthese perf',
      kind: 'summary',
      format: 'md',
      emphasis: 'secondary',
      description: 'Empreinte package et artefacts du candidat courant.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/perf-summary.md') : resolve(runRoot, 'perf/summary.md')
    },
    {
      id: 'runtime-report',
      label: 'Runtime views report',
      kind: 'artifact',
      format: 'html',
      emphasis: 'secondary',
      description: 'Rapport statique regenere pour le run courant.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/runtime-views-report.html') : resolve(runRoot, 'observability/runtime-views-report.html')
    },
    {
      id: 'npm-package',
      label: 'Package npm',
      kind: 'artifact',
      format: 'tgz',
      emphasis: 'secondary',
      description: 'Artefact npm du package grimoire-game capture dans le run.',
      runId: latestRunId,
      filePath: runRoot === null ? resolve(projectRoot, '__missing__/grimoire-game.tgz') : resolve(runRoot, 'release/grimoire-kit-grimoire-game-0.0.0.tgz')
    }
  ];
}