import type { JsonValue } from '../contracts/events';
import type { AuthContext } from '../server/auth/rbac';
import { isReadOnlyRole } from '../server/auth/rbac';

import { createBoardView } from './board-view';
import { createDeepInspectionView } from './deep-inspection-view';
import type { GameState, WorkflowStepLogEntry } from './game-state';

export const COVERAGE_SLOT_ORDER = ['F01', 'F02', 'F03', 'F19', 'F21', 'F22'] as const;
export const COVERAGE_WORKTREE_STATUS_ORDER = ['created', 'active', 'archived', 'closed'] as const;

export type CoverageSlotId = (typeof COVERAGE_SLOT_ORDER)[number];
export type CoverageWorktreeStatus = (typeof COVERAGE_WORKTREE_STATUS_ORDER)[number];
export type CoverageSlotsIssueCode =
  | 'ambiguous_desk'
  | 'ambiguous_directory'
  | 'grid_bounds'
  | 'grid_resize_overflow'
  | 'invalid_transition'
  | 'missing_desk'
  | 'position_occupied'
  | 'read_only';
export type CoverageSlotsIssueSurface = 'desk_directory' | 'map_editor' | 'worktree_room';

export interface CoverageSlotsGridPosition {
  x: number;
  y: number;
}

export interface CoverageSlotsIssue {
  code: CoverageSlotsIssueCode;
  surface: CoverageSlotsIssueSurface;
  message: string;
  sequenceId: number | null;
  timestamp: string | null;
  deskId: string | null;
  roomId: string | null;
  directory: string | null;
}

export interface CoverageSlotsMapDesk {
  deskId: string;
  position: CoverageSlotsGridPosition;
  teamId: string | null;
  directory: string | null;
  floatingLabel: string | null;
  assignedAgentIds: readonly string[];
}

export interface CoverageSlotsMapEditorView {
  width: number;
  height: number;
  maxWidth: number;
  maxHeight: number;
  readOnly: boolean;
  canUndo: boolean;
  canRedo: boolean;
  desks: readonly CoverageSlotsMapDesk[];
  issues: readonly CoverageSlotsIssue[];
}

export interface CoverageSlotsTeamRoom {
  roomId: string;
  decorationTheme: string | null;
  visitableBy: readonly string[];
  occupantAgentIds: readonly string[];
}

export interface CoverageSlotsAgentState {
  agentId: string;
  agentName: string;
  role: GameState['agents'][string]['role'];
  status: GameState['agents'][string]['status'];
  roomId: string;
  parentId: string | null;
  childAgentIds: readonly string[];
  activeTaskCount: number;
  badge: string | null;
  model: string | null;
  activeTool: string | null;
  detailPanelAvailable: boolean;
  availableActions: readonly string[];
}

export interface CoverageSlotsSpectatorShare {
  readOnly: boolean;
  tokenId: string | null;
  shareUrl: string | null;
  oneClickCopy: boolean;
  blockedMutationCount: number;
}

export interface CoverageSlotsDeskDirectoryBinding {
  deskId: string;
  directory: string;
  floatingLabel: string;
  assignedAgentIds: readonly string[];
}

export interface CoverageSlotsDeskDirectoryMapView {
  bindings: readonly CoverageSlotsDeskDirectoryBinding[];
  persistenceState: Record<string, string>;
  ambiguousBindingCount: number;
  issues: readonly CoverageSlotsIssue[];
}

export interface CoverageSlotsWorktreeTransition {
  action: CoverageWorktreeStatus;
  fromStatus: CoverageWorktreeStatus | null;
  toStatus: CoverageWorktreeStatus;
  valid: boolean;
  sequenceId: number;
  timestamp: string;
}

export interface CoverageSlotsWorktreeRoom {
  roomId: string;
  directory: string | null;
  branch: string | null;
  status: CoverageWorktreeStatus | null;
  wallActions: readonly string[];
  transitionHistory: readonly CoverageSlotsWorktreeTransition[];
  issues: readonly CoverageSlotsIssue[];
}

export interface CoverageSlotStatus {
  slotId: CoverageSlotId;
  covered: boolean;
  detail: string;
}

export interface CoverageSlotsViewSummary {
  slotCount: number;
  coveredSlotCount: number;
  blockedMutationCount: number;
  ambiguousBindingCount: number;
  invalidTransitionCount: number;
}

export interface CoverageSlotsView {
  protocolVersion: string;
  lastSequenceId: number;
  slotMatrix: readonly CoverageSlotStatus[];
  mapEditor: CoverageSlotsMapEditorView;
  teamRooms: readonly CoverageSlotsTeamRoom[];
  agentStates: readonly CoverageSlotsAgentState[];
  spectator: CoverageSlotsSpectatorShare;
  deskDirectoryMap: CoverageSlotsDeskDirectoryMapView;
  worktreeRooms: readonly CoverageSlotsWorktreeRoom[];
  summary: CoverageSlotsViewSummary;
}

export interface CoverageSlotsViewOptions {
  auth?: AuthContext;
}

interface MutableMapDesk {
  deskId: string;
  position: CoverageSlotsGridPosition;
  teamId: string | null;
}

interface MutableMapState {
  width: number;
  height: number;
  desks: Map<string, MutableMapDesk>;
}

interface MapCommand {
  action: 'place_desk' | 'move_desk' | 'resize_grid';
  deskId: string | null;
  position: CoverageSlotsGridPosition | null;
  teamId: string | null;
  width: number | null;
  height: number | null;
  sequenceId: number;
  timestamp: string;
}

interface MapControlAction {
  action: 'undo' | 'redo';
  deskId: string | null;
  sequenceId: number;
  timestamp: string;
}

interface WorktreeTransitionInput {
  roomId: string;
  toStatus: CoverageWorktreeStatus;
  directory: string | null;
  branch: string | null;
  wallActions: string[];
  sequenceId: number;
  timestamp: string;
}

const DEFAULT_MAP_SIZE = 8;
const DEFAULT_MAX_MAP_SIZE = 64;
const DEFAULT_WORKTREE_WALL_ACTIONS = ['merge', 'pr', 'discard', 'keep'] as const;

export function createCoverageSlotsView(
  state: GameState,
  options: CoverageSlotsViewOptions = {}
): CoverageSlotsView {
  const boardView = createBoardView(state);
  const agentDeskAssignments = readStringRecord(state.config, [
    'coverageSlots.agentDeskAssignments',
    'agentDeskAssignments'
  ]);
  const deskDirectoryMap = createDeskDirectoryMapView(state.config, agentDeskAssignments);
  const readOnly =
    (options.auth !== undefined && isReadOnlyRole(options.auth.role)) ||
    readBoolean(state.config, ['coverageSlots.mapEditor.readOnly', 'mapEditor.readOnly'], false);
  const mapEditor = createMapEditorView(state, readOnly, deskDirectoryMap.bindings, agentDeskAssignments);
  const teamRooms = createTeamRooms(state, boardView);
  const agentStates = boardView.agents.map((agent) => {
    const inspection = createDeepInspectionView(state, agent.id);

    return {
      agentId: agent.id,
      agentName: agent.name,
      role: agent.role,
      status: agent.status,
      roomId: agent.roomId,
      parentId: agent.parentId,
      childAgentIds: agent.childAgentIds,
      activeTaskCount: agent.activeTaskCount,
      badge: agent.role === 'orchestrator' ? 'ORCH' : null,
      model: inspection?.profile.model ?? null,
      activeTool: inspection?.profile.activeTool ?? null,
      detailPanelAvailable: inspection !== null,
      availableActions: inspection?.actions.map((action) => action.kind) ?? []
    } satisfies CoverageSlotsAgentState;
  });
  const spectator = createSpectatorShare(state.config, options.auth, mapEditor.issues);
  const worktreeRooms = createWorktreeRooms(state.recentWorkflowSteps);
  const invalidTransitionCount = worktreeRooms.reduce((count, room) => count + room.issues.length, 0);
  const blockedMutationCount =
    mapEditor.issues.length + deskDirectoryMap.issues.length + invalidTransitionCount;
  const slotMatrix = createSlotMatrix({
    teamRooms,
    agentStates,
    spectator,
    deskDirectoryMap,
    mapEditor,
    worktreeRooms
  });

  return {
    protocolVersion: state.protocolVersion,
    lastSequenceId: state.lastSequenceId,
    slotMatrix,
    mapEditor,
    teamRooms,
    agentStates,
    spectator,
    deskDirectoryMap,
    worktreeRooms,
    summary: {
      slotCount: slotMatrix.length,
      coveredSlotCount: slotMatrix.filter((slot) => slot.covered).length,
      blockedMutationCount,
      ambiguousBindingCount: deskDirectoryMap.ambiguousBindingCount,
      invalidTransitionCount
    }
  };
}

function createMapEditorView(
  state: GameState,
  readOnly: boolean,
  bindings: readonly CoverageSlotsDeskDirectoryBinding[],
  agentDeskAssignments: Record<string, string>
): CoverageSlotsMapEditorView {
  const initialState = createInitialMapState(state.config);
  const issueList: CoverageSlotsIssue[] = [];
  const appliedCommands: MapCommand[] = [];
  const redoCommands: MapCommand[] = [];
  let currentState = cloneMapState(initialState);

  for (const event of collectMapEvents(state.recentWorkflowSteps)) {
    if (event.action === 'undo' || event.action === 'redo') {
      if (readOnly) {
        issueList.push(
          createIssue(
            'read_only',
            'map_editor',
            `Spectator mode blocks map ${event.action}.`,
            event.sequenceId,
            event.timestamp,
            event.deskId
          )
        );
        continue;
      }

      if (event.action === 'undo') {
        const previous = appliedCommands.pop();
        if (previous !== undefined) {
          redoCommands.push(previous);
          currentState = replayMapCommands(initialState, appliedCommands);
        }

        continue;
      }

      const next = redoCommands.pop();
      if (next !== undefined) {
        appliedCommands.push(next);
        currentState = replayMapCommands(initialState, appliedCommands);
      }

      continue;
    }

    if (!isMapCommand(event)) {
      continue;
    }

    if (readOnly) {
      issueList.push(
        createIssue(
          'read_only',
          'map_editor',
          `Spectator mode blocks map action ${event.action}.`,
          event.sequenceId,
          event.timestamp,
          event.deskId
        )
      );
      continue;
    }

    const result = applyMapCommand(currentState, event);
    if ('code' in result) {
      issueList.push(
        createIssue(result.code, 'map_editor', result.message, event.sequenceId, event.timestamp, event.deskId)
      );
      continue;
    }

    currentState = result;
    appliedCommands.push(event);
    redoCommands.length = 0;
  }

  const bindingByDeskId = new Map(bindings.map((binding) => [binding.deskId, binding]));
  const assignedAgentsByDeskId = invertAssignments(agentDeskAssignments);
  const desks = Array.from(currentState.desks.values())
    .map((desk) => {
      const binding = bindingByDeskId.get(desk.deskId) ?? null;

      return {
        deskId: desk.deskId,
        position: desk.position,
        teamId: desk.teamId,
        directory: binding?.directory ?? null,
        floatingLabel: binding?.floatingLabel ?? null,
        assignedAgentIds: assignedAgentsByDeskId[desk.deskId] ?? []
      } satisfies CoverageSlotsMapDesk;
    })
    .sort((left, right) => left.deskId.localeCompare(right.deskId));

  return {
    width: currentState.width,
    height: currentState.height,
    maxWidth: DEFAULT_MAX_MAP_SIZE,
    maxHeight: DEFAULT_MAX_MAP_SIZE,
    readOnly,
    canUndo: appliedCommands.length > 0,
    canRedo: redoCommands.length > 0,
    desks,
    issues: issueList
  };
}

function createDeskDirectoryMapView(
  config: Record<string, JsonValue>,
  agentDeskAssignments: Record<string, string>
): CoverageSlotsDeskDirectoryMapView {
  const bindings: CoverageSlotsDeskDirectoryBinding[] = [];
  const issues: CoverageSlotsIssue[] = [];
  const bindingByDeskId = new Map<string, string>();
  const deskByDirectory = new Map<string, string>();
  const assignedAgentsByDeskId = invertAssignments(agentDeskAssignments);

  for (const entry of readDeskDirectoryEntries(config)) {
    const normalizedDirectory = normalizeDirectory(entry.directory);
    const existingDirectory = bindingByDeskId.get(entry.deskId);
    if (existingDirectory !== undefined && existingDirectory !== normalizedDirectory) {
      issues.push(
        createIssue(
          'ambiguous_desk',
          'desk_directory',
          `Desk ${entry.deskId} is bound to multiple directories.`,
          null,
          null,
          entry.deskId,
          null,
          normalizedDirectory
        )
      );
      continue;
    }

    const existingDesk = deskByDirectory.get(normalizedDirectory);
    if (existingDesk !== undefined && existingDesk !== entry.deskId) {
      issues.push(
        createIssue(
          'ambiguous_directory',
          'desk_directory',
          `Directory ${normalizedDirectory} is already assigned to desk ${existingDesk}.`,
          null,
          null,
          entry.deskId,
          null,
          normalizedDirectory
        )
      );
      continue;
    }

    bindingByDeskId.set(entry.deskId, normalizedDirectory);
    deskByDirectory.set(normalizedDirectory, entry.deskId);
  }

  for (const [deskId, directory] of bindingByDeskId.entries()) {
    bindings.push({
      deskId,
      directory,
      floatingLabel: `[dir] ${directoryLeafName(directory)}`,
      assignedAgentIds: assignedAgentsByDeskId[deskId] ?? []
    });
  }

  bindings.sort((left, right) => left.deskId.localeCompare(right.deskId));

  return {
    bindings,
    persistenceState: Object.fromEntries(bindings.map((binding) => [binding.deskId, binding.directory])),
    ambiguousBindingCount: issues.length,
    issues
  };
}

function createTeamRooms(state: GameState, boardView: ReturnType<typeof createBoardView>): CoverageSlotsTeamRoom[] {
  const roomThemes = readStringRecord(state.config, ['coverageSlots.roomThemes', 'roomThemes']);
  const roomAccess = readStringArrayRecord(state.config, ['coverageSlots.roomAccess', 'roomAccess']);
  const roomIds = uniqueStrings([
    ...boardView.rooms.map((room) => room.id),
    ...Object.keys(roomThemes),
    ...Object.keys(roomAccess)
  ]);

  return roomIds
    .map((roomId) => ({
      roomId,
      decorationTheme: roomThemes[roomId] ?? null,
      visitableBy: roomAccess[roomId] ?? [],
      occupantAgentIds: boardView.rooms.find((room) => room.id === roomId)?.agentIds ?? []
    }))
    .sort((left, right) => left.roomId.localeCompare(right.roomId));
}

function createSpectatorShare(
  config: Record<string, JsonValue>,
  auth: AuthContext | undefined,
  issues: readonly CoverageSlotsIssue[]
): CoverageSlotsSpectatorShare {
  const readOnly = auth !== undefined && isReadOnlyRole(auth.role);
  const tokenId = auth?.tokenId ?? readString(config, ['coverageSlots.spectator.tokenId', 'spectator.tokenId']);
  const shareUrl =
    readString(config, ['coverageSlots.spectator.shareUrl', 'spectator.shareUrl']) ??
    deriveShareUrl(readString(config, ['coverageSlots.spectator.shareBaseUrl', 'spectator.shareBaseUrl']), tokenId);

  return {
    readOnly,
    tokenId,
    shareUrl,
    oneClickCopy: readBoolean(config, ['coverageSlots.spectator.oneClickCopy', 'spectator.oneClickCopy'], shareUrl !== null),
    blockedMutationCount: issues.filter((issue) => issue.code === 'read_only').length
  };
}

function createWorktreeRooms(recentWorkflowSteps: readonly WorkflowStepLogEntry[]): CoverageSlotsWorktreeRoom[] {
  const rooms = new Map<string, CoverageSlotsWorktreeRoom>();

  for (const transition of collectWorktreeTransitions(recentWorkflowSteps)) {
    const current =
      rooms.get(transition.roomId) ??
      {
        roomId: transition.roomId,
        directory: transition.directory,
        branch: transition.branch,
        status: null,
        wallActions: transition.wallActions,
        transitionHistory: [],
        issues: []
      };

    const valid = isValidWorktreeTransition(current.status, transition.toStatus);
    const historyEntry = {
      action: transition.toStatus,
      fromStatus: current.status,
      toStatus: transition.toStatus,
      valid,
      sequenceId: transition.sequenceId,
      timestamp: transition.timestamp
    } satisfies CoverageSlotsWorktreeTransition;

    if (valid) {
      current.status = transition.toStatus;
      current.directory = transition.directory ?? current.directory;
      current.branch = transition.branch ?? current.branch;
      current.wallActions = transition.wallActions.length > 0 ? transition.wallActions : current.wallActions;
    } else {
      current.issues = [
        ...current.issues,
        createIssue(
          'invalid_transition',
          'worktree_room',
          `Worktree room ${transition.roomId} cannot transition from ${current.status ?? 'none'} to ${transition.toStatus}.`,
          transition.sequenceId,
          transition.timestamp,
          null,
          transition.roomId
        )
      ];
    }

    current.transitionHistory = [...current.transitionHistory, historyEntry];
    rooms.set(transition.roomId, current);
  }

  return Array.from(rooms.values()).sort((left, right) => left.roomId.localeCompare(right.roomId));
}

function createSlotMatrix(input: {
  teamRooms: readonly CoverageSlotsTeamRoom[];
  agentStates: readonly CoverageSlotsAgentState[];
  spectator: CoverageSlotsSpectatorShare;
  deskDirectoryMap: CoverageSlotsDeskDirectoryMapView;
  mapEditor: CoverageSlotsMapEditorView;
  worktreeRooms: readonly CoverageSlotsWorktreeRoom[];
}): CoverageSlotStatus[] {
  return [
    {
      slotId: 'F01',
      covered: input.mapEditor.width > 0 && input.mapEditor.height > 0,
      detail: `${input.mapEditor.desks.length} desk(s) projected on a ${input.mapEditor.width}x${input.mapEditor.height} grid.`
    },
    {
      slotId: 'F02',
      covered: input.teamRooms.some((room) => room.decorationTheme !== null),
      detail: `${input.teamRooms.filter((room) => room.decorationTheme !== null).length} themed team room(s).`
    },
    {
      slotId: 'F03',
      covered: input.agentStates.some((agent) => agent.detailPanelAvailable) && input.agentStates.some((agent) => agent.badge === 'ORCH'),
      detail: `${input.agentStates.length} agent runtime state card(s).`
    },
    {
      slotId: 'F19',
      covered: input.spectator.readOnly && input.spectator.tokenId !== null && input.spectator.oneClickCopy,
      detail:
        input.spectator.shareUrl === null
          ? 'Spectator share URL missing.'
          : `Spectator share URL ready: ${input.spectator.shareUrl}.`
    },
    {
      slotId: 'F21',
      covered: input.deskDirectoryMap.bindings.length > 0 && input.agentStates.some((agent) => agent.detailPanelAvailable),
      detail: `${input.deskDirectoryMap.bindings.length} desk-directory binding(s) linked to inspection-capable agents.`
    },
    {
      slotId: 'F22',
      covered: input.worktreeRooms.some((room) => hasFullWorktreeLifecycle(room.transitionHistory)),
      detail: `${input.worktreeRooms.length} worktree lifecycle room(s).`
    }
  ];
}

function createInitialMapState(config: Record<string, JsonValue>): MutableMapState {
  const width = readInteger(config, ['coverageSlots.mapEditor.width', 'mapEditor.width'], DEFAULT_MAP_SIZE);
  const height = readInteger(config, ['coverageSlots.mapEditor.height', 'mapEditor.height'], DEFAULT_MAP_SIZE);
  const rawDesks = readConfigValue(config, ['coverageSlots.mapEditor.desks', 'mapEditor.desks']);
  const desks = new Map<string, MutableMapDesk>();

  for (const desk of readDeskEntries(rawDesks)) {
    if (isWithinBounds(desk.position, width, height)) {
      desks.set(desk.deskId, desk);
    }
  }

  return { width, height, desks };
}

function collectMapEvents(recentWorkflowSteps: readonly WorkflowStepLogEntry[]): Array<MapCommand | MapControlAction> {
  return recentWorkflowSteps
    .map((workflowStep) => {
      const metadata = asJsonRecord(workflowStep.metadata);
      const action = normalizeMapAction(readMetadataString(metadata, ['mapAction', 'map_action', 'editorAction', 'editor_action']));
      if (action === null) {
        return null;
      }

      if (action === 'undo' || action === 'redo') {
        return {
          action,
          deskId: readMetadataString(metadata, ['deskId', 'desk_id']),
          sequenceId: workflowStep.sequenceId,
          timestamp: workflowStep.timestamp
        } satisfies MapControlAction;
      }

      return {
        action,
        deskId: readMetadataString(metadata, ['deskId', 'desk_id']),
        position: readMetadataPosition(metadata),
        teamId: readMetadataString(metadata, ['teamId', 'team_id']),
        width: readMetadataInteger(metadata, ['width', 'gridWidth', 'grid_width']),
        height: readMetadataInteger(metadata, ['height', 'gridHeight', 'grid_height']),
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp
      } satisfies MapCommand;
    })
    .filter((event): event is MapCommand | MapControlAction => event !== null)
    .sort((left, right) => left.sequenceId - right.sequenceId);
}

function collectWorktreeTransitions(
  recentWorkflowSteps: readonly WorkflowStepLogEntry[]
): WorktreeTransitionInput[] {
  return recentWorkflowSteps
    .map((workflowStep) => {
      const metadata = asJsonRecord(workflowStep.metadata);
      const roomId = readMetadataString(metadata, ['worktreeRoomId', 'worktree_room_id', 'roomId', 'room_id']);
      const action = normalizeWorktreeStatus(
        readMetadataString(metadata, ['worktreeAction', 'worktree_action', 'roomAction', 'room_action', 'worktreeStatus', 'worktree_status', 'roomStatus', 'room_status'])
      );

      if (roomId === null || action === null) {
        return null;
      }

      return {
        roomId,
        toStatus: action,
        directory: readMetadataString(metadata, ['directory', 'worktreeDirectory', 'worktree_directory']),
        branch: readMetadataString(metadata, ['branch', 'gitBranch', 'worktreeBranch']),
        wallActions: readMetadataStringArray(metadata, ['wallActions', 'wall_actions']),
        sequenceId: workflowStep.sequenceId,
        timestamp: workflowStep.timestamp
      } satisfies WorktreeTransitionInput;
    })
    .filter((transition): transition is WorktreeTransitionInput => transition !== null)
    .sort((left, right) => left.sequenceId - right.sequenceId);
}

function applyMapCommand(
  currentState: MutableMapState,
  command: MapCommand
): MutableMapState | { code: CoverageSlotsIssueCode; message: string } {
  if (command.action === 'resize_grid') {
    if (command.width === null || command.height === null || command.width < 1 || command.height < 1) {
      return {
        code: 'grid_bounds',
        message: 'Grid resize requires positive width and height.'
      };
    }

    if (command.width > DEFAULT_MAX_MAP_SIZE || command.height > DEFAULT_MAX_MAP_SIZE) {
      return {
        code: 'grid_bounds',
        message: `Grid size exceeds ${DEFAULT_MAX_MAP_SIZE}x${DEFAULT_MAX_MAP_SIZE}.`
      };
    }

    const outOfBoundsDesk = Array.from(currentState.desks.values()).find(
      (desk) => !isWithinBounds(desk.position, command.width ?? currentState.width, command.height ?? currentState.height)
    );
    if (outOfBoundsDesk !== undefined) {
      return {
        code: 'grid_resize_overflow',
        message: `Desk ${outOfBoundsDesk.deskId} would fall outside the resized grid.`
      };
    }

    return {
      width: command.width,
      height: command.height,
      desks: cloneDeskMap(currentState.desks)
    };
  }

  if (command.deskId === null || command.position === null) {
    return {
      code: 'missing_desk',
      message: `Map action ${command.action} requires both deskId and position.`
    };
  }

  if (!isWithinBounds(command.position, currentState.width, currentState.height)) {
    return {
      code: 'grid_bounds',
      message: `Desk ${command.deskId} targets ${command.position.x},${command.position.y} outside the current grid.`
    };
  }

  if (command.action === 'move_desk' && !currentState.desks.has(command.deskId)) {
    return {
      code: 'missing_desk',
      message: `Desk ${command.deskId} must exist before it can move.`
    };
  }

  const occupiedBy = Array.from(currentState.desks.values()).find(
    (desk) =>
      desk.deskId !== command.deskId && desk.position.x === command.position?.x && desk.position.y === command.position?.y
  );
  if (occupiedBy !== undefined) {
    return {
      code: 'position_occupied',
      message: `Desk ${occupiedBy.deskId} already occupies ${command.position.x},${command.position.y}.`
    };
  }

  const desks = cloneDeskMap(currentState.desks);
  const existing = desks.get(command.deskId) ?? null;
  desks.set(command.deskId, {
    deskId: command.deskId,
    position: command.position,
    teamId: command.teamId ?? existing?.teamId ?? null
  });

  return {
    width: currentState.width,
    height: currentState.height,
    desks
  };
}

function replayMapCommands(initialState: MutableMapState, commands: readonly MapCommand[]): MutableMapState {
  let currentState = cloneMapState(initialState);

  for (const command of commands) {
    const nextState = applyMapCommand(currentState, command);
    if ('code' in nextState) {
      continue;
    }

    currentState = nextState;
  }

  return currentState;
}

function cloneMapState(state: MutableMapState): MutableMapState {
  return {
    width: state.width,
    height: state.height,
    desks: cloneDeskMap(state.desks)
  };
}

function cloneDeskMap(source: Map<string, MutableMapDesk>): Map<string, MutableMapDesk> {
  return new Map(Array.from(source.entries()).map(([deskId, desk]) => [deskId, { ...desk, position: { ...desk.position } }]));
}

function createIssue(
  code: CoverageSlotsIssueCode,
  surface: CoverageSlotsIssueSurface,
  message: string,
  sequenceId: number | null,
  timestamp: string | null,
  deskId: string | null,
  roomId: string | null = null,
  directory: string | null = null
): CoverageSlotsIssue {
  return {
    code,
    surface,
    message,
    sequenceId,
    timestamp,
    deskId,
    roomId,
    directory
  };
}

function hasFullWorktreeLifecycle(transitions: readonly CoverageSlotsWorktreeTransition[]): boolean {
  const validStates = new Set(
    transitions.filter((transition) => transition.valid).map((transition) => transition.toStatus)
  );

  return COVERAGE_WORKTREE_STATUS_ORDER.every((status) => validStates.has(status));
}

function isValidWorktreeTransition(
  currentStatus: CoverageWorktreeStatus | null,
  nextStatus: CoverageWorktreeStatus
): boolean {
  if (currentStatus === null) {
    return nextStatus === 'created';
  }

  const nextByCurrent: Record<CoverageWorktreeStatus, CoverageWorktreeStatus | null> = {
    created: 'active',
    active: 'archived',
    archived: 'closed',
    closed: null
  };

  return nextByCurrent[currentStatus] === nextStatus;
}

function readDeskDirectoryEntries(config: Record<string, JsonValue>): Array<{ deskId: string; directory: string }> {
  const value = readConfigValue(config, ['coverageSlots.deskDirectoryMap', 'deskDirectoryMap']);
  if (value === undefined) {
    return [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((entry) => {
      const record = asJsonRecord(entry);
      const deskId = readMetadataString(record, ['deskId', 'desk_id']);
      const directory = readMetadataString(record, ['directory', 'path']);
      return deskId !== null && directory !== null ? [{ deskId, directory }] : [];
    });
  }

  if (isJsonRecord(value)) {
    return Object.entries(value).flatMap(([deskId, directory]) => {
      if (typeof directory === 'string' && directory.trim().length > 0) {
        return [{ deskId, directory }];
      }

      const record = asJsonRecord(directory);
      const path = readMetadataString(record, ['directory', 'path']);
      return path !== null ? [{ deskId, directory: path }] : [];
    });
  }

  return [];
}

function readDeskEntries(value: JsonValue | undefined): MutableMapDesk[] {
  if (value === undefined) {
    return [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((entry) => {
      const record = asJsonRecord(entry);
      const deskId = readMetadataString(record, ['deskId', 'desk_id']);
      const position = readMetadataPosition(record);
      if (deskId === null || position === null) {
        return [];
      }

      return [
        {
          deskId,
          position,
          teamId: readMetadataString(record, ['teamId', 'team_id'])
        }
      ];
    });
  }

  if (isJsonRecord(value)) {
    return Object.entries(value).flatMap(([deskId, entry]) => {
      const record = asJsonRecord(entry);
      const position = readMetadataPosition(record);
      if (position === null) {
        return [];
      }

      return [
        {
          deskId,
          position,
          teamId: readMetadataString(record, ['teamId', 'team_id'])
        }
      ];
    });
  }

  return [];
}

function readConfigValue(config: Record<string, JsonValue>, keys: readonly string[]): JsonValue | undefined {
  for (const key of keys) {
    if (config[key] !== undefined) {
      return config[key];
    }

    const pathValue = readConfigPath(config, key.split('.'));
    if (pathValue !== undefined) {
      return pathValue;
    }
  }

  return undefined;
}

function readConfigPath(value: JsonValue | Record<string, JsonValue>, path: readonly string[]): JsonValue | undefined {
  let cursor: JsonValue | Record<string, JsonValue> | undefined = value;

  for (const segment of path) {
    if (!isJsonRecord(cursor)) {
      return undefined;
    }

    cursor = cursor[segment];
  }

  return cursor;
}

function readBoolean(config: Record<string, JsonValue>, keys: readonly string[], fallback: boolean): boolean {
  const value = readConfigValue(config, keys);
  return typeof value === 'boolean' ? value : fallback;
}

function readInteger(config: Record<string, JsonValue>, keys: readonly string[], fallback: number): number {
  const value = readConfigValue(config, keys);
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : fallback;
}

function readString(config: Record<string, JsonValue>, keys: readonly string[]): string | null {
  const value = readConfigValue(config, keys);
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function readStringRecord(config: Record<string, JsonValue>, keys: readonly string[]): Record<string, string> {
  const value = readConfigValue(config, keys);
  if (!isJsonRecord(value)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(value).flatMap(([key, entry]) =>
      typeof entry === 'string' && entry.trim().length > 0 ? [[key, entry.trim()]] : []
    )
  );
}

function readStringArrayRecord(config: Record<string, JsonValue>, keys: readonly string[]): Record<string, string[]> {
  const value = readConfigValue(config, keys);
  if (!isJsonRecord(value)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(value).flatMap(([key, entry]) => {
      if (!Array.isArray(entry)) {
        return [];
      }

      const strings = entry.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
      return strings.length > 0 ? [[key, strings]] : [];
    })
  );
}

function readMetadataString(record: Record<string, JsonValue>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }

  return null;
}

function readMetadataInteger(record: Record<string, JsonValue>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number' && Number.isInteger(value)) {
      return value;
    }
  }

  return null;
}

function readMetadataPosition(record: Record<string, JsonValue>): CoverageSlotsGridPosition | null {
  const rawPosition = record.position;
  if (isJsonRecord(rawPosition)) {
    const x = typeof rawPosition.x === 'number' && Number.isInteger(rawPosition.x) ? rawPosition.x : null;
    const y = typeof rawPosition.y === 'number' && Number.isInteger(rawPosition.y) ? rawPosition.y : null;
    if (x !== null && y !== null) {
      return { x, y };
    }
  }

  const x = readMetadataInteger(record, ['x']);
  const y = readMetadataInteger(record, ['y']);
  return x !== null && y !== null ? { x, y } : null;
}

function readMetadataStringArray(record: Record<string, JsonValue>, keys: readonly string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (!Array.isArray(value)) {
      continue;
    }

    return value.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0);
  }

  return [];
}

function normalizeMapAction(value: string | null): MapCommand['action'] | MapControlAction['action'] | null {
  if (value === null) {
    return null;
  }

  switch (value.toLowerCase()) {
    case 'place':
    case 'place_desk':
      return 'place_desk';
    case 'move':
    case 'move_desk':
      return 'move_desk';
    case 'resize':
    case 'resize_grid':
      return 'resize_grid';
    case 'undo':
      return 'undo';
    case 'redo':
      return 'redo';
    default:
      return null;
  }
}

function isMapCommand(event: MapCommand | MapControlAction): event is MapCommand {
  return event.action === 'place_desk' || event.action === 'move_desk' || event.action === 'resize_grid';
}

function normalizeWorktreeStatus(value: string | null): CoverageWorktreeStatus | null {
  if (value === null) {
    return null;
  }

  switch (value.toLowerCase()) {
    case 'create':
    case 'created':
      return 'created';
    case 'activate':
    case 'active':
      return 'active';
    case 'archive':
    case 'archived':
      return 'archived';
    case 'close':
    case 'closed':
      return 'closed';
    default:
      return null;
  }
}

function deriveShareUrl(baseUrl: string | null, tokenId: string | null): string | null {
  if (baseUrl === null || tokenId === null) {
    return null;
  }

  return `${baseUrl.replace(/\/+$/, '')}/${tokenId}`;
}

function invertAssignments(assignments: Record<string, string>): Record<string, string[]> {
  const byDeskId: Record<string, string[]> = {};

  for (const [agentId, deskId] of Object.entries(assignments)) {
    const current = byDeskId[deskId] ?? [];
    current.push(agentId);
    byDeskId[deskId] = current.sort((left, right) => left.localeCompare(right));
  }

  return byDeskId;
}

function isWithinBounds(position: CoverageSlotsGridPosition, width: number, height: number): boolean {
  return position.x >= 0 && position.y >= 0 && position.x < width && position.y < height;
}

function normalizeDirectory(directory: string): string {
  const normalized = directory.trim().replace(/\\/g, '/').replace(/\/+/g, '/');
  if (normalized === '/') {
    return normalized;
  }

  return normalized.replace(/\/+$/, '');
}

function directoryLeafName(directory: string): string {
  const segments = normalizeDirectory(directory).split('/').filter((segment) => segment.length > 0);
  return segments[segments.length - 1] ?? directory;
}

function uniqueStrings(values: readonly string[]): string[] {
  return Array.from(new Set(values)).sort((left, right) => left.localeCompare(right));
}

function asJsonRecord(value: unknown): Record<string, JsonValue> {
  return isJsonRecord(value) ? value : {};
}

function isJsonRecord(value: unknown): value is Record<string, JsonValue> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}