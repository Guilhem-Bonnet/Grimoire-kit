import type { TaskPriority } from '../contracts/events';

import {
  createBoardView,
  DECISION_CARD_REQUIRED_FIELD_ORDER,
  type BoardDecisionCard,
  type DecisionCardRequiredField
} from './board-view';
import type { GameState } from './game-state';

export interface DecisionCardQuery {
  taskId?: string;
  traceId?: string;
  actionId?: string;
  structuredOnly?: boolean;
  transitionOnly?: boolean;
}

export interface DecisionCardGate {
  taskId: string;
  taskTitle: string;
  priority: TaskPriority | null;
  requiredActionId: string;
  isApplicable: boolean;
  isReady: boolean;
  cardId: string | null;
  missingFields: readonly DecisionCardRequiredField[];
}

export interface DecisionCardViewSummary {
  cardCount: number;
  structuredCount: number;
  transitionCardCount: number;
  incompleteTransitionCount: number;
}

export interface DecisionCardView {
  protocolVersion: string;
  lastSequenceId: number;
  cards: readonly BoardDecisionCard[];
  taskGates: readonly DecisionCardGate[];
  summary: DecisionCardViewSummary;
}

export interface DecisionCardQueryResult {
  cards: readonly BoardDecisionCard[];
  totalCount: number;
}

const DECISION_CARD_GATE_ACTION_ID = 'task.transition.done';

export function createDecisionCardView(state: GameState): DecisionCardView {
  const boardView = createBoardView(state);
  const cards = boardView.decisionCards;
  const taskGates = Object.values(state.tasks)
    .map((task) => createDecisionCardGate(task.id, task.title, task.priority ?? null, cards))
    .sort(compareDecisionCardGates);

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    cards,
    taskGates,
    summary: {
      cardCount: cards.length,
      structuredCount: cards.filter((card) => card.isStructured).length,
      transitionCardCount: cards.filter((card) => card.isTransitionCard).length,
      incompleteTransitionCount: cards.filter((card) => card.isTransitionCard && !card.isStructured).length
    }
  };
}

export function queryDecisionCardView(
  view: DecisionCardView,
  query: DecisionCardQuery = {}
): DecisionCardQueryResult {
  const cards = view.cards.filter((card) => matchesDecisionCardQuery(card, query));

  return {
    cards,
    totalCount: cards.length
  };
}

export function evaluateTaskDecisionCardGate(state: GameState, taskId: string): DecisionCardGate | null {
  return createDecisionCardView(state).taskGates.find((taskGate) => taskGate.taskId === taskId) ?? null;
}

function createDecisionCardGate(
  taskId: string,
  taskTitle: string,
  priority: TaskPriority | null,
  cards: readonly BoardDecisionCard[]
): DecisionCardGate {
  const relevantCards = cards
    .filter((card) => card.taskId === taskId && card.actionId === DECISION_CARD_GATE_ACTION_ID)
    .sort((left, right) => right.sequenceId - left.sequenceId);
  const latestCard = relevantCards[0] ?? null;

  if (priority !== 'critical') {
    return {
      taskId,
      taskTitle,
      priority,
      requiredActionId: DECISION_CARD_GATE_ACTION_ID,
      isApplicable: false,
      isReady: true,
      cardId: latestCard?.id ?? null,
      missingFields: []
    };
  }

  if (latestCard === null) {
    return {
      taskId,
      taskTitle,
      priority,
      requiredActionId: DECISION_CARD_GATE_ACTION_ID,
      isApplicable: true,
      isReady: false,
      cardId: null,
      missingFields: [...DECISION_CARD_REQUIRED_FIELD_ORDER]
    };
  }

  return {
    taskId,
    taskTitle,
    priority,
    requiredActionId: DECISION_CARD_GATE_ACTION_ID,
    isApplicable: true,
    isReady: latestCard.isStructured,
    cardId: latestCard.id,
    missingFields: latestCard.missingFields
  };
}

function matchesDecisionCardQuery(card: BoardDecisionCard, query: DecisionCardQuery): boolean {
  if (query.taskId !== undefined && card.taskId !== query.taskId) {
    return false;
  }

  if (query.traceId !== undefined && card.traceId !== query.traceId) {
    return false;
  }

  if (query.actionId !== undefined && card.actionId !== query.actionId) {
    return false;
  }

  if (query.structuredOnly === true && !card.isStructured) {
    return false;
  }

  if (query.transitionOnly === true && !card.isTransitionCard) {
    return false;
  }

  return true;
}

function compareDecisionCardGates(left: DecisionCardGate, right: DecisionCardGate): number {
  if (left.isReady !== right.isReady) {
    return left.isReady ? 1 : -1;
  }

  if (left.missingFields.length !== right.missingFields.length) {
    return right.missingFields.length - left.missingFields.length;
  }

  return left.taskTitle.localeCompare(right.taskTitle);
}