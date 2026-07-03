import type { AgentPresence, JsonValue } from '../contracts/events';

import type { GameState, WorkflowStepLogEntry } from './game-state';

export const DEFAULT_XP_PER_LEVEL = 100;

export interface ProgressionAchievement {
  achievementId: string;
  title: string;
  unlockedAtSequenceId: number | null;
}

export interface AgentProgressionView {
  agentId: string;
  agentName: string;
  totalXp: number;
  level: number;
  creditedActionIds: readonly string[];
  unlockedAchievements: readonly ProgressionAchievement[];
}

type JsonObject = { [key: string]: JsonValue };

export type ProgressionPersistenceAgentSnapshot = JsonObject & {
  totalXp: number;
  level: number;
  creditedActionIds: string[];
  achievements: string[];
};

export type ProgressionPersistenceSnapshot = JsonObject & {
  xpPerLevel: number;
  agents: Record<string, ProgressionPersistenceAgentSnapshot>;
};

export interface ProgressionViewSummary {
  agentCount: number;
  totalXp: number;
  highestLevel: number;
  achievementCount: number;
  duplicateCreditBlockedCount: number;
}

export interface ProgressionView {
  xpPerLevel: number;
  agents: readonly AgentProgressionView[];
  persistenceState: ProgressionPersistenceSnapshot;
  summary: ProgressionViewSummary;
}

interface MutableProgressionState {
  agentId: string;
  agentName: string;
  totalXp: number;
  creditedActionIds: Set<string>;
  unlockedAchievements: Map<string, ProgressionAchievement>;
}

export function createProgressionView(state: GameState): ProgressionView {
  const xpPerLevel = readXpPerLevel(state);
  const persistedSnapshot = readPersistedSnapshot(state, xpPerLevel);
  const byAgentId = new Map<string, MutableProgressionState>();

  for (const [agentId, agentSnapshot] of Object.entries(persistedSnapshot.agents)) {
    const agent = state.agents[agentId];
    byAgentId.set(agentId, {
      agentId,
      agentName: agent?.name ?? agentId,
      totalXp: agentSnapshot.totalXp,
      creditedActionIds: new Set(agentSnapshot.creditedActionIds),
      unlockedAchievements: new Map(
        agentSnapshot.achievements.map((achievementId) => [
          achievementId,
          {
            achievementId,
            title: achievementId,
            unlockedAtSequenceId: null
          }
        ])
      )
    });
  }

  let duplicateCreditBlockedCount = 0;
  for (const workflowStep of [...state.recentWorkflowSteps].sort((left, right) => left.sequenceId - right.sequenceId)) {
    const progressionEvent = readProgressionEvent(workflowStep);
    if (progressionEvent === null) {
      continue;
    }

    const agent = state.agents[progressionEvent.agentId];
    const current =
      byAgentId.get(progressionEvent.agentId) ??
      createMutableProgressionState(progressionEvent.agentId, agent);

    if (current.creditedActionIds.has(progressionEvent.actionId)) {
      duplicateCreditBlockedCount += 1;
      byAgentId.set(progressionEvent.agentId, current);
      continue;
    }

    current.totalXp += progressionEvent.xpAward;
    current.creditedActionIds.add(progressionEvent.actionId);
    for (const achievementId of progressionEvent.achievementIds) {
      if (!current.unlockedAchievements.has(achievementId)) {
        current.unlockedAchievements.set(achievementId, {
          achievementId,
          title: achievementId,
          unlockedAtSequenceId: workflowStep.sequenceId
        });
      }
    }

    byAgentId.set(progressionEvent.agentId, current);
  }

  for (const agent of Object.values(state.agents)) {
    if (!byAgentId.has(agent.id)) {
      byAgentId.set(agent.id, createMutableProgressionState(agent.id, agent));
    }
  }

  const agents = Array.from(byAgentId.values())
    .map((progression) => ({
      agentId: progression.agentId,
      agentName: progression.agentName,
      totalXp: progression.totalXp,
      level: levelForXp(progression.totalXp, xpPerLevel),
      creditedActionIds: [...progression.creditedActionIds].sort((left, right) => left.localeCompare(right)),
      unlockedAchievements: [...progression.unlockedAchievements.values()].sort((left, right) =>
        left.achievementId.localeCompare(right.achievementId)
      )
    }))
    .sort((left, right) => right.totalXp - left.totalXp || left.agentName.localeCompare(right.agentName));

  return {
    xpPerLevel,
    agents,
    persistenceState: {
      xpPerLevel,
      agents: Object.fromEntries(
        agents.map((agent) => [
          agent.agentId,
          {
            totalXp: agent.totalXp,
            level: agent.level,
            creditedActionIds: [...agent.creditedActionIds],
            achievements: agent.unlockedAchievements.map((achievement) => achievement.achievementId)
          }
        ])
      )
    },
    summary: {
      agentCount: agents.length,
      totalXp: agents.reduce((sum, agent) => sum + agent.totalXp, 0),
      highestLevel: agents.reduce((highest, agent) => Math.max(highest, agent.level), 1),
      achievementCount: agents.reduce((sum, agent) => sum + agent.unlockedAchievements.length, 0),
      duplicateCreditBlockedCount
    }
  };
}

export function evaluateAgentProgression(state: GameState, agentId: string): AgentProgressionView | null {
  return createProgressionView(state).agents.find((agent) => agent.agentId === agentId) ?? null;
}

function createMutableProgressionState(
  agentId: string,
  agent: AgentPresence | undefined
): MutableProgressionState {
  return {
    agentId,
    agentName: agent?.name ?? agentId,
    totalXp: 0,
    creditedActionIds: new Set<string>(),
    unlockedAchievements: new Map<string, ProgressionAchievement>()
  };
}

function readXpPerLevel(state: GameState): number {
  const candidate = state.config['progression.xpPerLevel'];
  return typeof candidate === 'number' && Number.isInteger(candidate) && candidate > 0
    ? candidate
    : DEFAULT_XP_PER_LEVEL;
}

function readPersistedSnapshot(state: GameState, xpPerLevel: number): ProgressionPersistenceSnapshot {
  const snapshot = state.config['progression.snapshot'];
  if (snapshot === null || snapshot === undefined || Array.isArray(snapshot) || typeof snapshot !== 'object') {
    return { xpPerLevel, agents: {} };
  }

  const rawAgents = snapshot.agents;
  if (rawAgents === null || rawAgents === undefined || Array.isArray(rawAgents) || typeof rawAgents !== 'object') {
    return { xpPerLevel, agents: {} };
  }

  const agents = Object.fromEntries(
    Object.entries(rawAgents).flatMap(([agentId, agentSnapshot]) => {
      if (agentSnapshot === null || Array.isArray(agentSnapshot) || typeof agentSnapshot !== 'object') {
        return [];
      }

      const totalXp = typeof agentSnapshot.totalXp === 'number' && agentSnapshot.totalXp >= 0 ? agentSnapshot.totalXp : 0;
      const level = typeof agentSnapshot.level === 'number' && agentSnapshot.level > 0 ? agentSnapshot.level : levelForXp(totalXp, xpPerLevel);
      const creditedActionIds = Array.isArray(agentSnapshot.creditedActionIds)
        ? agentSnapshot.creditedActionIds.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0)
        : [];
      const achievements = Array.isArray(agentSnapshot.achievements)
        ? agentSnapshot.achievements.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0)
        : [];

      return [
        [
          agentId,
          {
            totalXp,
            level,
            creditedActionIds,
            achievements
          } satisfies ProgressionPersistenceAgentSnapshot
        ]
      ];
    })
  );

  return { xpPerLevel, agents };
}

function readProgressionEvent(workflowStep: WorkflowStepLogEntry): {
  agentId: string;
  actionId: string;
  xpAward: number;
  achievementIds: readonly string[];
} | null {
  const metadata = workflowStep.metadata as Record<string, unknown>;
  const agentId = readString(metadata, ['agentId', 'agent_id']) ?? workflowStep.agentId ?? null;
  const actionId = readString(metadata, ['progressionActionId', 'progression_action_id', 'actionId', 'action_id']);
  const xpAward = readPositiveInteger(metadata, ['xpAward', 'xp_award']);

  if (agentId === null || actionId === null || xpAward === null) {
    return null;
  }

  return {
    agentId,
    actionId,
    xpAward,
    achievementIds: readStringArray(metadata, ['achievementIds', 'achievement_ids', 'achievementId', 'achievement_id'])
  };
}

function levelForXp(totalXp: number, xpPerLevel: number): number {
  return 1 + Math.floor(totalXp / xpPerLevel);
}

function readString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value !== 'string') {
      continue;
    }

    const normalized = value.trim();
    if (normalized.length > 0) {
      return normalized;
    }
  }

  return null;
}

function readPositiveInteger(record: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
      return value;
    }
  }

  return null;
}

function readStringArray(record: Record<string, unknown>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string') {
      const normalized = value.trim();
      return normalized.length > 0 ? [normalized] : [];
    }

    if (Array.isArray(value)) {
      return value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0);
    }
  }

  return [];
}