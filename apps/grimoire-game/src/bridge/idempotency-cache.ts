export const DEFAULT_PROCESSED_MUTATION_CACHE_MAX_ENTRIES = 2048;

export function normalizeProcessedMutationCacheMaxEntries(value: number | undefined): number {
  if (value === undefined) {
    return DEFAULT_PROCESSED_MUTATION_CACHE_MAX_ENTRIES;
  }

  if (!Number.isFinite(value)) {
    return DEFAULT_PROCESSED_MUTATION_CACHE_MAX_ENTRIES;
  }

  const normalized = Math.floor(value);
  if (normalized < 1) {
    return DEFAULT_PROCESSED_MUTATION_CACHE_MAX_ENTRIES;
  }

  return normalized;
}

export function setBoundedMutationCacheEntry<T>(
  cache: Map<string, T>,
  key: string,
  value: T,
  maxEntries: number
): void {
  if (cache.has(key)) {
    cache.delete(key);
  }

  cache.set(key, value);

  while (cache.size > maxEntries) {
    const oldestKey = cache.keys().next().value as string | undefined;
    if (oldestKey === undefined) {
      break;
    }

    cache.delete(oldestKey);
  }
}