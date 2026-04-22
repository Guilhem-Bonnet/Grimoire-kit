/**
 * pheromone-board-feed.ts — Node-side reader for the pheromone board.
 *
 * Mirror of hook-events-feed.ts: used by the cockpit prepare script to
 * dump a static snapshot in ``.generated/public/pheromone-board.json``.
 *
 * The browser never imports this module — it only consumes the static
 * snapshot via the polling client (V4.4.b BM-20).
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import {
  createPheromoneBoardSnapshot,
  emptyPheromoneBoard,
  parsePheromoneBoard,
  type PheromoneBoard,
  type PheromoneBoardSnapshot
} from '../contracts/pheromoneBoard';

/** Default board path relative to the project root. */
export const DEFAULT_PHEROMONE_BOARD_PATH = '_grimoire-output/pheromone-board.json';

export interface ReadBoardOptions {
  /** Project root to resolve the board path from. */
  projectRoot: string;
  /** Override the default board path. */
  boardPath?: string;
}

/** Read + parse the pheromone board. Returns an empty board if missing. */
export function readPheromoneBoard(options: ReadBoardOptions): PheromoneBoard {
  const path = resolve(options.projectRoot, options.boardPath ?? DEFAULT_PHEROMONE_BOARD_PATH);
  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      return emptyPheromoneBoard();
    }
    throw error;
  }
  return parsePheromoneBoard(raw);
}

export interface BuildPheromoneSnapshotOptions {
  projectRoot: string;
  boardPath?: string;
  limit?: number;
  generatedAt?: string;
  now?: string;
}

/** Build a snapshot suitable for static publication. */
export function buildPheromoneBoardSnapshot(
  options: BuildPheromoneSnapshotOptions
): PheromoneBoardSnapshot {
  const readOptions: ReadBoardOptions = { projectRoot: options.projectRoot };
  if (options.boardPath !== undefined) {
    readOptions.boardPath = options.boardPath;
  }
  const board = readPheromoneBoard(readOptions);
  const snapshotOptions: { limit?: number; generatedAt?: string; now?: string } = {};
  if (options.limit !== undefined) {
    snapshotOptions.limit = options.limit;
  }
  if (options.generatedAt !== undefined) {
    snapshotOptions.generatedAt = options.generatedAt;
  }
  if (options.now !== undefined) {
    snapshotOptions.now = options.now;
  }
  return createPheromoneBoardSnapshot(board, snapshotOptions);
}
