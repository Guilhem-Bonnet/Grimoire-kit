import type { GameState } from './game-state';

export const DEFAULT_ONBOARDING_STEP_ORDER = [
  'welcome',
  'controls',
  'rooms',
  'first-investigation',
  'ready'
] as const;
export const ONBOARDING_ENTRYPOINT_ORDER = ['hud', 'help'] as const;

export type OnboardingStepId = string;
export type OnboardingLaunchMode = 'automatic' | 'resume' | 'manual' | 'inactive';
export type OnboardingEntrypoint = (typeof ONBOARDING_ENTRYPOINT_ORDER)[number];

export interface OnboardingStepState {
  stepId: OnboardingStepId;
  index: number;
  status: 'completed' | 'current' | 'upcoming';
}

export interface OnboardingPersistenceState {
  steps: readonly OnboardingStepId[];
  currentStepIndex: number;
  started: boolean;
  completed: boolean;
  skippedPermanently: boolean;
  manualReplayRequested: boolean;
}

export interface OnboardingViewSummary {
  totalStepCount: number;
  completedStepCount: number;
  remainingStepCount: number;
  isActive: boolean;
  launchMode: OnboardingLaunchMode;
}

export interface OnboardingView {
  steps: readonly OnboardingStepState[];
  currentStepId: OnboardingStepId | null;
  currentStepIndex: number | null;
  isActive: boolean;
  isCompleted: boolean;
  skippedPermanently: boolean;
  launchMode: OnboardingLaunchMode;
  canSkip: boolean;
  canResume: boolean;
  manualReplayAvailable: boolean;
  manualReplayEntrypoints: readonly OnboardingEntrypoint[];
  persistenceState: OnboardingPersistenceState;
  summary: OnboardingViewSummary;
}

export function createOnboardingView(state: GameState): OnboardingView {
  const steps = readOnboardingSteps(state.config);
  const currentStepIndex = readOnboardingStepIndex(state.config, steps.length);
  const completed = readConfigBoolean(state, ['onboarding.completed', 'onboarding.done'], false);
  const skippedPermanently = readConfigBoolean(
    state,
    ['onboarding.skippedPermanently', 'onboarding.skipped_permanently', 'onboarding.skipped'],
    false
  );
  const started = readConfigBoolean(state, ['onboarding.started'], false);
  const manualReplayRequested = readConfigBoolean(
    state,
    ['onboarding.manualReplayRequested', 'onboarding.manual_replay_requested'],
    false
  );
  const hasPersistedState = hasAnyConfigKey(state, [
    'onboarding.steps',
    'onboarding.currentStepIndex',
    'onboarding.current_step_index',
    'onboarding.completed',
    'onboarding.done',
    'onboarding.skippedPermanently',
    'onboarding.skipped_permanently',
    'onboarding.skipped',
    'onboarding.started',
    'onboarding.manualReplayRequested',
    'onboarding.manual_replay_requested'
  ]);

  const launchMode = resolveLaunchMode({
    hasPersistedState,
    started,
    completed,
    skippedPermanently,
    manualReplayRequested,
    currentStepIndex
  });
  const isActive = launchMode !== 'inactive';
  const effectiveStepIndex = isActive ? currentStepIndex : null;
  const completedStepCount = computeCompletedStepCount(launchMode, currentStepIndex, steps.length);

  return {
    steps: steps.map((stepId, index) => ({
      stepId,
      index,
      status: classifyStepStatus(index, effectiveStepIndex, completed, skippedPermanently)
    })),
    currentStepId: effectiveStepIndex === null ? null : steps[effectiveStepIndex] ?? null,
    currentStepIndex: effectiveStepIndex,
    isActive,
    isCompleted: completed,
    skippedPermanently,
    launchMode,
    canSkip: isActive && !completed && !skippedPermanently,
    canResume: !isActive && !completed && !skippedPermanently && currentStepIndex > 0,
    manualReplayAvailable: true,
    manualReplayEntrypoints: [...ONBOARDING_ENTRYPOINT_ORDER],
    persistenceState: {
      steps,
      currentStepIndex,
      started: isActive,
      completed,
      skippedPermanently,
      manualReplayRequested
    },
    summary: {
      totalStepCount: steps.length,
      completedStepCount,
      remainingStepCount: Math.max(steps.length - completedStepCount, 0),
      isActive,
      launchMode
    }
  };
}

function readOnboardingSteps(config: GameState['config']): OnboardingStepId[] {
  const rawSteps = config['onboarding.steps'];
  if (!Array.isArray(rawSteps)) {
    return [...DEFAULT_ONBOARDING_STEP_ORDER];
  }

  const steps = rawSteps
    .filter((entry): entry is string => typeof entry === 'string')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);

  return steps.length === 0 ? [...DEFAULT_ONBOARDING_STEP_ORDER] : steps;
}

function readOnboardingStepIndex(config: GameState['config'], stepCount: number): number {
  const candidate = config['onboarding.currentStepIndex'] ?? config['onboarding.current_step_index'];
  if (typeof candidate !== 'number' || !Number.isInteger(candidate)) {
    return 0;
  }

  if (candidate < 0) {
    return 0;
  }

  if (candidate >= stepCount) {
    return stepCount - 1;
  }

  return candidate;
}

function readConfigBoolean(state: GameState, keys: readonly string[], defaultValue: boolean): boolean {
  for (const key of keys) {
    const value = state.config[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return defaultValue;
}

function hasAnyConfigKey(state: GameState, keys: readonly string[]): boolean {
  return keys.some((key) => Object.hasOwn(state.config, key));
}

function resolveLaunchMode(input: {
  hasPersistedState: boolean;
  started: boolean;
  completed: boolean;
  skippedPermanently: boolean;
  manualReplayRequested: boolean;
  currentStepIndex: number;
}): OnboardingLaunchMode {
  if (input.manualReplayRequested) {
    return 'manual';
  }

  if (input.completed || input.skippedPermanently) {
    return 'inactive';
  }

  if (!input.hasPersistedState) {
    return 'automatic';
  }

  if (input.started) {
    return input.currentStepIndex > 0 ? 'resume' : 'automatic';
  }

  if (input.currentStepIndex > 0) {
    return 'resume';
  }

  return 'inactive';
}

function classifyStepStatus(
  stepIndex: number,
  currentStepIndex: number | null,
  completed: boolean,
  skippedPermanently: boolean
): OnboardingStepState['status'] {
  if (completed || skippedPermanently) {
    return currentStepIndex !== null && stepIndex <= currentStepIndex ? 'completed' : 'upcoming';
  }

  if (currentStepIndex === null) {
    return 'upcoming';
  }

  if (stepIndex < currentStepIndex) {
    return 'completed';
  }

  if (stepIndex === currentStepIndex) {
    return 'current';
  }

  return 'upcoming';
}

function computeCompletedStepCount(
  launchMode: OnboardingLaunchMode,
  currentStepIndex: number,
  stepCount: number
): number {
  if (launchMode === 'inactive' && currentStepIndex >= stepCount - 1) {
    return stepCount;
  }

  if (launchMode === 'inactive') {
    return currentStepIndex + 1;
  }

  return currentStepIndex;
}