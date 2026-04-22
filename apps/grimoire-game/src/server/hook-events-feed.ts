/**
 * hook-events-feed.ts — Node-side reader for the canonical ledger.
 *
 * Used by the cockpit prepare script (to dump a static snapshot in
 * ``.generated/public/hook-events.json``) and by the dev-mode Vite
 * middleware when one is wired up.
 *
 * The browser never imports this module — it only consumes the static
 * snapshot (V1.6) or a /api/events endpoint (V1.5 dev mode follow-up).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  createHookEventSnapshot,
  filterHookEvents,
  parseHookEventsJsonl,
  type HookEvent,
  type HookEventFilter,
  type HookEventSnapshot
} from '../contracts/hookEvents';

/** Default ledger path relative to the project root. */
export const DEFAULT_LEDGER_PATH = '_grimoire-runtime/_memory/activity.jsonl';

export interface ReadLedgerOptions extends HookEventFilter {
  /** Project root to resolve the ledger path from. */
  projectRoot: string;
  /** Override the default ledger path. */
  ledgerPath?: string;
}

/** Read + parse + filter the activity ledger. Returns [] if the file is missing. */
export function readHookEventLedger(options: ReadLedgerOptions): HookEvent[] {
  const path = resolve(options.projectRoot, options.ledgerPath ?? DEFAULT_LEDGER_PATH);
  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return [];
    }
    throw error;
  }
  const parsed = parseHookEventsJsonl(raw);
  const filter: HookEventFilter = {};
  if (options.sinceTs !== undefined) {
    filter.sinceTs = options.sinceTs;
  }
  if (options.scope !== undefined) {
    filter.scope = options.scope;
  }
  if (options.limit !== undefined) {
    filter.limit = options.limit;
  }
  return filterHookEvents(parsed, filter);
}

export interface BuildSnapshotOptions {
  projectRoot: string;
  ledgerPath?: string;
  limit?: number;
  generatedAt?: string;
}

/** Build a snapshot suitable for static publication. */
export function buildHookEventSnapshot(
  options: BuildSnapshotOptions
): HookEventSnapshot {
  const readOptions: ReadLedgerOptions = { projectRoot: options.projectRoot };
  if (options.ledgerPath !== undefined) {
    readOptions.ledgerPath = options.ledgerPath;
  }
  const events = readHookEventLedger(readOptions);
  const snapshotOptions: { limit?: number; generatedAt?: string } = {};
  if (options.limit !== undefined) {
    snapshotOptions.limit = options.limit;
  }
  if (options.generatedAt !== undefined) {
    snapshotOptions.generatedAt = options.generatedAt;
  }
  return createHookEventSnapshot(events, snapshotOptions);
}
