import type { AgentStatus, TaskStatus } from '../contracts/events';

const MUTATION_IDENTITY_MAX_LENGTH = 128;
const MUTATION_IDENTITY_PATTERN = /^[A-Za-z0-9._:-]+$/;

const ALLOWED_CONFIG_MUTATION_PREFIXES = [
  'hud.',
  'board.',
  'inspect.',
  'collaboration.',
  'powerCards.',
  'provenanceRegistry.',
  'skillTree.',
  'verification.',
  'session.',
  'live.'
] as const;

const ALLOWED_TASK_TRANSITION_STATUSES = ['todo', 'in_progress', 'review', 'done'] as const;

const ALLOWED_AGENT_STATUS_UPDATE_STATUSES = ['paused', 'working'] as const;

const ALLOWED_TASK_STATUS_TRANSITIONS: Record<TaskStatus, readonly TaskStatus[]> = {
  backlog: ['todo'],
  todo: ['in_progress'],
  in_progress: ['todo', 'review'],
  review: ['in_progress', 'done'],
  done: []
};

const ALLOWED_AGENT_STATUS_TRANSITIONS: Record<AgentStatus, readonly AgentStatus[]> = {
  idle: ['paused'],
  working: ['paused'],
  paused: ['working'],
  offline: []
};

export function isAllowedConfigMutationKey(key: string): boolean {
  return ALLOWED_CONFIG_MUTATION_PREFIXES.some((prefix) => key.startsWith(prefix));
}

export function isAllowedTaskTransitionStatus(status: TaskStatus): boolean {
  return (ALLOWED_TASK_TRANSITION_STATUSES as readonly TaskStatus[]).includes(status);
}

export function isAllowedTaskStatusTransition(from: TaskStatus, to: TaskStatus): boolean {
  return ALLOWED_TASK_STATUS_TRANSITIONS[from].includes(to);
}

export function isAllowedAgentStatusUpdateStatus(status: AgentStatus): boolean {
  return (ALLOWED_AGENT_STATUS_UPDATE_STATUSES as readonly AgentStatus[]).includes(status);
}

export function isAllowedAgentStatusTransition(from: AgentStatus, to: AgentStatus): boolean {
  return ALLOWED_AGENT_STATUS_TRANSITIONS[from].includes(to);
}

export function assertCanonicalMutationIdentity(
  mutationType: string,
  fieldName: 'requestId' | 'idempotencyKey',
  value: string
): void {
  if (value.length === 0) {
    throw new Error(`Mutation ${mutationType} requires a non-empty ${fieldName}.`);
  }

  if (value.length > MUTATION_IDENTITY_MAX_LENGTH) {
    throw new Error(
      `Mutation ${mutationType} ${fieldName} exceeds ${MUTATION_IDENTITY_MAX_LENGTH} characters.`
    );
  }

  if (value.trim() !== value) {
    throw new Error(`Mutation ${mutationType} ${fieldName} must not contain leading or trailing spaces.`);
  }

  if (!MUTATION_IDENTITY_PATTERN.test(value)) {
    throw new Error(
      `Mutation ${mutationType} ${fieldName} contains unsupported characters (allowed: A-Z a-z 0-9 . _ : -).`
    );
  }
}
