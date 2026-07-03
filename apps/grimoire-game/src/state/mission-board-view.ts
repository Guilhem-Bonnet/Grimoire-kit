import type { RuntimeDashboardUiTone } from './runtime-dashboard-ui-view'
import type { RuntimeDashboardView } from './runtime-dashboard-view'

export type MissionBoardRoomId =
  | 'intake-desk'
  | 'war-room'
  | 'workshop'
  | 'branch-finisher'
  | 'seance-archive'
  | 'watchtower'

export interface MissionBoardHeader {
  title: string
  subtitle: string
  tone: RuntimeDashboardUiTone
  summary: string
  runId: string | null
  branch: string
}

export interface MissionBoardStatCard {
  id: string
  label: string
  value: string | number
  note: string
  tone: RuntimeDashboardUiTone
}

export interface MissionBoardRoom {
  roomId: MissionBoardRoomId
  label: string
  question: string
  dominantCommand: string
  tone: RuntimeDashboardUiTone
  count: number
  summary: string
  pills: readonly string[]
}

export interface MissionBoardRoomCard {
  cardId: string
  roomId: MissionBoardRoomId
  kindLabel: string
  seal: string
  title: string
  subtitle: string
  detail: string
  tone: RuntimeDashboardUiTone
  badges: readonly string[]
  nextAction: string
  missionId: string | null
  taskId: string | null
  traceId: string | null
  verificationRef: string | null
}

export interface MissionBoardDrawerSection {
  id: string
  label: string
  tone: RuntimeDashboardUiTone
  lines: readonly string[]
}

export interface MissionBoardDrawer {
  kindLabel: string
  title: string
  subtitle: string
  tone: RuntimeDashboardUiTone
  nextAction: string
  referencePills: readonly string[]
  sections: readonly MissionBoardDrawerSection[]
}

export interface MissionBoardTimelineEntry {
  id: string
  roomId: MissionBoardRoomId
  title: string
  detail: string
  timestamp: string
  tone: RuntimeDashboardUiTone
  taskId: string | null
  traceId: string | null
}

export interface MissionBoardView {
  protocolVersion: string
  lastSequenceId: number
  header: MissionBoardHeader
  statCards: readonly MissionBoardStatCard[]
  rooms: readonly MissionBoardRoom[]
  cards: readonly MissionBoardRoomCard[]
  drawer: MissionBoardDrawer
  timeline: readonly MissionBoardTimelineEntry[]
}

type MissionBoardTaskInspection = RuntimeDashboardView['tasks']['tasks'][number]
type MissionBoardMission = RuntimeDashboardView['missionLedger']['missions'][number]
type MissionBoardDecisionCard = RuntimeDashboardView['board']['decisionCards'][number]
type MissionBoardVerificationGate = RuntimeDashboardView['verification']['tasks'][number]
type MissionBoardVerificationQueueItem = RuntimeDashboardView['verificationQueue']['items'][number]
type MissionBoardSessionNode = RuntimeDashboardView['sessionLineage']['nodes'][number]
type MissionBoardWorkflowStep = MissionBoardTaskInspection['recentWorkflowSteps'][number]

interface MissionBoardIndices {
  taskById: Map<string, MissionBoardTaskInspection>
  verificationByTaskId: Map<string, MissionBoardVerificationGate>
  queueByTaskId: Map<string, MissionBoardVerificationQueueItem>
  missionById: Map<string, MissionBoardMission>
  missionByTaskId: Map<string, MissionBoardMission>
  decisionCardsByTaskId: Map<string, MissionBoardDecisionCard[]>
  sessionByTraceId: Map<string, MissionBoardSessionNode>
  dependencyCountByMissionId: Map<string, number>
  evidenceCountByMissionId: Map<string, number>
  openEscalationCountByMissionId: Map<string, number>
}

interface MissionBoardSequencedTimelineEntry extends MissionBoardTimelineEntry {
  sequenceId: number
}

const ROOM_ORDER: readonly MissionBoardRoomId[] = [
  'intake-desk',
  'war-room',
  'workshop',
  'branch-finisher',
  'seance-archive',
  'watchtower'
]

const ROOM_META: Record<MissionBoardRoomId, { label: string; question: string; dominantCommand: string }> = {
  'intake-desk': {
    label: 'Intake Desk',
    question: 'Qu est-ce qui entre dans le systeme et comment le qualifier ?',
    dominantCommand: 'Qualifier'
  },
  'war-room': {
    label: 'War Room',
    question: 'Quelle est la situation tactique de la mission ?',
    dominantCommand: 'Arbitrer'
  },
  workshop: {
    label: 'Workshop',
    question: 'Que font les lanes et les runs en ce moment ?',
    dominantCommand: 'Ouvrir le run'
  },
  'branch-finisher': {
    label: 'Branch Finisher',
    question: 'Qu est-ce qui peut etre verifie ou cloture ?',
    dominantCommand: 'Demander verification'
  },
  'seance-archive': {
    label: 'Seance Archive',
    question: 'Qui a decide quoi, quand, sur quelle preuve ?',
    dominantCommand: 'Relire'
  },
  watchtower: {
    label: 'Watchtower',
    question: 'Qu est-ce qui derive, stagne ou demande escalation ?',
    dominantCommand: 'Escalader'
  }
}

const TASK_STATUS_LABELS: Record<string, string> = {
  backlog: 'Backlog',
  todo: 'Qualified',
  in_progress: 'Running',
  review: 'Review',
  done: 'Done'
}

const MISSION_STATUS_LABELS: Record<string, string> = {
  blocked: 'Blocked',
  verifying: 'Verifying',
  active: 'Active',
  ready: 'Ready',
  planned: 'Planned',
  completed: 'Completed',
  archived: 'Archived'
}

const VERIFICATION_QUEUE_LABELS: Record<string, string> = {
  rejected: 'Rejected',
  needs_work: 'Needs work',
  verifying: 'Verifying',
  queued: 'Queued',
  accepted: 'Accepted'
}

const PRIORITY_RANK: Record<string, number> = {
  high: 0,
  medium: 1,
  low: 2
}

const VERIFICATION_QUEUE_RANK: Record<string, number> = {
  rejected: 0,
  needs_work: 1,
  verifying: 2,
  queued: 3,
  accepted: 4
}

const TONE_RANK: Record<RuntimeDashboardUiTone, number> = {
  critical: 0,
  warning: 1,
  positive: 2,
  neutral: 3
}

const ROOM_FOCUS_RANK: Record<MissionBoardRoomId, number> = {
  watchtower: 0,
  'branch-finisher': 1,
  'war-room': 2,
  workshop: 3,
  'intake-desk': 4,
  'seance-archive': 5
}

export function createMissionBoardView(dashboard: RuntimeDashboardView): MissionBoardView {
  const indices = createIndices(dashboard)
  const cardsByRoom: Record<MissionBoardRoomId, MissionBoardRoomCard[]> = {
    'intake-desk': createIntakeDeskCards(dashboard, indices),
    'war-room': createWarRoomCards(dashboard, indices),
    workshop: createWorkshopCards(dashboard, indices),
    'branch-finisher': createBranchFinisherCards(dashboard, indices),
    'seance-archive': createSeanceArchiveCards(dashboard, indices),
    watchtower: createWatchtowerCards(dashboard, indices)
  }
  const rooms = createRooms(dashboard, cardsByRoom)
  const cards = ROOM_ORDER.flatMap((roomId) => cardsByRoom[roomId])
  const watchSignalCount = countWatchSignals(dashboard)
  const headerTone =
    dashboard.supervision.releaseGate.releaseBlocked || dashboard.summary.securityBlockingFindingCount > 0
      ? 'critical'
      : watchSignalCount > 0
        ? 'warning'
        : 'positive'

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Mission Board',
      subtitle:
        'Shell a rooms specialisees pour lire, arbitrer et verifier une task sans sortir du meme GameState.',
      tone: headerTone,
      summary: `${dashboard.summary.missionCount} mission(s), ${dashboard.summary.verificationQueueCount} file(s) de verification, ${watchSignalCount} signal(aux) de veille.`,
      runId: dashboard.projectRegistry?.activeProject.runId ?? dashboard.nodeFleet.summary.runId,
      branch: dashboard.branchFinisher.branch
    },
    statCards: [
      {
        id: 'missions',
        label: 'Missions',
        value: dashboard.summary.missionCount,
        note: `${dashboard.summary.blockedMissionCount} bloquee(s)`,
        tone: dashboard.summary.blockedMissionCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'verification',
        label: 'Verification',
        value: dashboard.summary.verificationQueueCount,
        note: `${dashboard.summary.verificationNeedsWorkCount} needs work / ${dashboard.summary.verificationAcceptedCount} accepted`,
        tone: dashboard.summary.verificationNeedsWorkCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'watch-signals',
        label: 'Watchtower',
        value: watchSignalCount,
        note: dashboard.supervision.releaseGate.releaseBlocked ? 'release NO-GO' : 'release readable',
        tone: headerTone
      },
      {
        id: 'seances',
        label: 'Seances',
        value: dashboard.sessionLineage.metrics.sessionCount,
        note: `${dashboard.sessionLineage.metrics.edgeCount} lien(s) causaux`,
        tone: dashboard.sessionLineage.metrics.staleAlertCount > 0 ? 'warning' : 'neutral'
      },
      {
        id: 'proof-packs',
        label: 'Evidence packs',
        value: dashboard.summary.verificationEvidencePackCount,
        note: `${dashboard.summary.missingExpectedProofCount} preuve(s) attendue(s) manquante(s)`,
        tone: dashboard.summary.missingExpectedProofCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'operators',
        label: 'Operators',
        value: dashboard.summary.workingAgentCount,
        note: `${dashboard.summary.activeTaskCount} task(s) active(s)`,
        tone: dashboard.summary.activeTaskCount > 0 ? 'positive' : 'neutral'
      }
    ],
    rooms,
    cards,
    drawer: createDrawer(cards, rooms, dashboard, indices),
    timeline: createTimeline(dashboard)
  }
}

function createIndices(dashboard: RuntimeDashboardView): MissionBoardIndices {
  const taskById = new Map(dashboard.tasks.tasks.map((task) => [task.task.id, task]))
  const verificationByTaskId = new Map(dashboard.verification.tasks.map((task) => [task.taskId, task]))
  const queueByTaskId = new Map(dashboard.verificationQueue.items.map((item) => [item.taskId, item]))
  const missionById = new Map(dashboard.missionLedger.missions.map((mission) => [mission.missionId, mission]))
  const missionByTaskId = new Map(
    dashboard.missionLedger.missions.flatMap((mission) => {
      const taskId = extractTaskIdFromMissionId(mission.missionId)
      return taskId === null ? [] : [[taskId, mission] as const]
    })
  )
  const decisionCardsByTaskId = dashboard.board.decisionCards.reduce((index, card) => {
    if (card.taskId === null) {
      return index
    }

    const cards = index.get(card.taskId) ?? []
    cards.push(card)
    index.set(card.taskId, cards)
    return index
  }, new Map<string, MissionBoardDecisionCard[]>())
  const sessionByTraceId = new Map(dashboard.sessionLineage.nodes.map((node) => [node.traceId, node]))
  const dependencyCountByMissionId = new Map(
    dashboard.missionLedger.missions.map((mission) => [
      mission.missionId,
      dashboard.missionLedger.dependencies.filter(
        (dependency) => mission.itemIds.includes(dependency.fromItemId) || mission.itemIds.includes(dependency.toItemId)
      ).length
    ])
  )
  const evidenceCountByMissionId = new Map(
    dashboard.missionLedger.missions.map((mission) => [
      mission.missionId,
      dashboard.missionLedger.evidenceRecords.filter((record) => mission.itemIds.includes(record.itemId)).length
    ])
  )
  const openEscalationCountByMissionId = new Map(
    dashboard.missionLedger.missions.map((mission) => [
      mission.missionId,
      dashboard.missionLedger.escalationRecords.filter(
        (record) => record.status === 'open' && mission.itemIds.includes(record.itemId)
      ).length
    ])
  )

  return {
    taskById,
    verificationByTaskId,
    queueByTaskId,
    missionById,
    missionByTaskId,
    decisionCardsByTaskId,
    sessionByTraceId,
    dependencyCountByMissionId,
    evidenceCountByMissionId,
    openEscalationCountByMissionId
  }
}

function createRooms(
  dashboard: RuntimeDashboardView,
  cardsByRoom: Record<MissionBoardRoomId, MissionBoardRoomCard[]>
): MissionBoardRoom[] {
  const intakeCount = dashboard.tasks.tasks.filter(
    (task) => task.task.status === 'backlog' || task.task.status === 'todo'
  ).length
  const workshopCount = dashboard.tasks.tasks.filter((task) => task.task.status === 'in_progress').length
  const watchSignalCount = countWatchSignals(dashboard)

  return [
    {
      roomId: 'intake-desk',
      label: ROOM_META['intake-desk'].label,
      question: ROOM_META['intake-desk'].question,
      dominantCommand: ROOM_META['intake-desk'].dominantCommand,
      tone: cardsByRoom['intake-desk'].some((card) => card.tone === 'warning') ? 'warning' : 'neutral',
      count: intakeCount,
      summary: `${intakeCount} entree(s) attendent qualification ou confirmation de routage.`,
      pills: compactStrings([
        `${intakeCount} file(s)`,
        `${dashboard.tasks.metrics.attentionCount} alert(s) task`,
        `${dashboard.summary.boardAlertCount} board alert(s)`
      ])
    },
    {
      roomId: 'war-room',
      label: ROOM_META['war-room'].label,
      question: ROOM_META['war-room'].question,
      dominantCommand: ROOM_META['war-room'].dominantCommand,
      tone:
        dashboard.summary.blockedMissionCount > 0
          ? 'warning'
          : cardsByRoom['war-room'].length > 0
            ? 'positive'
            : 'neutral',
      count: cardsByRoom['war-room'].length,
      summary: `${dashboard.board.decisionCards.length} carte(s) de decision et ${dashboard.summary.blockedMissionCount} mission(s) bloquee(s) cadrent l arbitrage.`,
      pills: compactStrings([
        `${dashboard.board.decisionCards.length} decision(s)`,
        `${dashboard.summary.blockedMissionCount} bloquee(s)`,
        `${dashboard.summary.activeTaskCount} active(s)`
      ])
    },
    {
      roomId: 'workshop',
      label: ROOM_META.workshop.label,
      question: ROOM_META.workshop.question,
      dominantCommand: ROOM_META.workshop.dominantCommand,
      tone:
        dashboard.tasks.tasks.some((task) => task.task.status === 'in_progress' && task.alerts.length > 0)
          ? 'warning'
          : workshopCount > 0
            ? 'positive'
            : 'neutral',
      count: workshopCount,
      summary: `${workshopCount} run(s) actif(s) et ${dashboard.summary.workingAgentCount} operateur(s) restent visibles cote execution.`,
      pills: compactStrings([
        `${dashboard.summary.workingAgentCount} working`,
        `${dashboard.summary.activeLeaseCount} lease(s)`,
        `${dashboard.summary.nodeCount} node(s)`
      ])
    },
    {
      roomId: 'branch-finisher',
      label: ROOM_META['branch-finisher'].label,
      question: ROOM_META['branch-finisher'].question,
      dominantCommand: ROOM_META['branch-finisher'].dominantCommand,
      tone:
        dashboard.branchFinisher.verification.blockingItemCount > 0 || dashboard.branchFinisher.shipBlocked
          ? 'critical'
          : dashboard.summary.verificationQueueCount > 0
            ? 'warning'
            : 'positive',
      count: dashboard.summary.verificationQueueCount,
      summary: `${dashboard.summary.verificationQueueCount} item(s) dans la file de verification, ${dashboard.summary.verificationAcceptedCount} accepte(s).`,
      pills: compactStrings([
        `${dashboard.branchFinisher.verification.needsWorkCount} needs work`,
        `${dashboard.branchFinisher.verification.acceptedCount} accepted`,
        `${dashboard.branchFinisher.options.filter((option) => option.allowed).length} option(s) ouvertes`
      ])
    },
    {
      roomId: 'seance-archive',
      label: ROOM_META['seance-archive'].label,
      question: ROOM_META['seance-archive'].question,
      dominantCommand: ROOM_META['seance-archive'].dominantCommand,
      tone: dashboard.sessionLineage.metrics.staleAlertCount > 0 ? 'warning' : 'neutral',
      count: dashboard.sessionLineage.metrics.sessionCount,
      summary: `${dashboard.sessionLineage.metrics.sessionCount} seance(s), ${dashboard.sessionLineage.metrics.edgeCount} lien(s) causaux, ${dashboard.sessionLineage.metrics.orphanSessionCount} orphelin(s).`,
      pills: compactStrings([
        `${dashboard.sessionLineage.metrics.edgeCount} edge(s)`,
        `${dashboard.sessionLineage.metrics.staleAlertCount} alert(s)`,
        `${dashboard.summary.canonicalEnvelopeCount} envelope(s)`
      ])
    },
    {
      roomId: 'watchtower',
      label: ROOM_META.watchtower.label,
      question: ROOM_META.watchtower.question,
      dominantCommand: ROOM_META.watchtower.dominantCommand,
      tone:
        dashboard.supervision.releaseGate.releaseBlocked || dashboard.summary.securityBlockingFindingCount > 0
          ? 'critical'
          : watchSignalCount > 0
            ? 'warning'
            : 'positive',
      count: watchSignalCount,
      summary: `${watchSignalCount} signal(aux) de surveillance gardent la release et les derives visibles.`,
      pills: compactStrings([
        dashboard.supervision.releaseGate.releaseBlocked ? 'release NO-GO' : 'release GO',
        `${dashboard.summary.securityBlockingFindingCount} security blocker(s)`,
        `${dashboard.summary.totalAttentionCount} attention item(s)`
      ])
    }
  ]
}

function createIntakeDeskCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  return dashboard.tasks.tasks
    .filter((task) => task.task.status === 'backlog' || task.task.status === 'todo')
    .sort(compareQueuedTasks)
    .slice(0, 4)
    .map((task) => {
      const mission = indices.missionByTaskId.get(task.task.id)

      return {
        cardId: `intake:${task.task.id}`,
        roomId: 'intake-desk',
        kindLabel: 'Task',
        seal: task.task.status === 'backlog' ? 'Qualify' : 'Route',
        title: task.task.title,
        subtitle: `${task.task.id} · ${formatTaskStatus(task.task.status)} · ${task.assigneeAgentName ?? 'non assignee'}`,
        detail:
          task.recentWorkflowSteps[0]?.detail ?? 'Aucune execution canonique encore observee pour cette entree.',
        tone: task.alerts.length > 0 ? 'warning' : 'neutral',
        badges: compactStrings([
          formatPriority(task.task.priority),
          task.task.kind ?? null,
          `${task.traceIds.length} trace(s)`,
          `${task.task.dependencyIds?.length ?? 0} dep(s)`
        ]),
        nextAction:
          task.alerts.length > 0 ? 'Completer la qualification avant routage.' : 'Confirmer le routage propose.',
        missionId: mission?.missionId ?? null,
        taskId: task.task.id,
        traceId: task.traceIds[0] ?? null,
        verificationRef: null
      }
    })
}

function createWarRoomCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  const decisionCards: MissionBoardRoomCard[] = dashboard.board.decisionCards.slice(0, 2).map((card) => ({
    cardId: `war:decision:${card.id}`,
    roomId: 'war-room' as const,
    kindLabel: 'Decision',
    seal: card.missingFields.length > 0 ? 'Arbitrate' : 'Ready',
    title: card.title,
    subtitle: `${card.taskTitle ?? card.taskId ?? card.roomId ?? 'unscoped'} · seq ${card.sequenceId}`,
    detail: card.detail,
    tone: card.missingFields.length > 0 ? 'warning' : 'positive',
    badges: compactStrings([
      `${card.evidence.length + card.supportingToolCalls.length} evidence`,
      `${card.missingFields.length} gap(s)`,
      card.traceId
    ]),
    nextAction:
      card.missingFields.length > 0 ? 'Completer la carte de decision ou rerouter.' : 'Confirmer le choix tactique.',
    missionId: card.taskId === null ? null : indices.missionByTaskId.get(card.taskId)?.missionId ?? null,
    taskId: card.taskId,
    traceId: card.traceId,
    verificationRef: null
  }))
  const missionCards: MissionBoardRoomCard[] = dashboard.missionLedger.missions
    .filter((mission) => ['active', 'blocked', 'verifying', 'ready'].includes(mission.status))
    .sort((left, right) => compareActiveMissions(left, right, indices))
    .slice(0, Math.max(0, 4 - decisionCards.length))
    .map((mission) => {
      const taskId = extractTaskIdFromMissionId(mission.missionId)

      return {
        cardId: `war:mission:${mission.missionId}`,
        roomId: 'war-room' as const,
        kindLabel: 'Mission',
        seal: formatMissionStatus(mission.status),
        title: mission.title,
        subtitle: `${mission.missionId} · ${mission.owner}`,
        detail: `${mission.activeItemCount} item(s) actifs, ${mission.blockedItemIds.length} bloque(s), ${indices.openEscalationCountByMissionId.get(mission.missionId) ?? 0} escalation(s) ouverte(s).`,
        tone: toneForMission(mission, indices),
        badges: compactStrings([
          formatPriority(mission.priority),
          `${indices.dependencyCountByMissionId.get(mission.missionId) ?? 0} dep(s)`,
          `${indices.evidenceCountByMissionId.get(mission.missionId) ?? 0} evidence`,
          `${mission.traceRefs.length} trace(s)`
        ]),
        nextAction: mission.status === 'blocked' ? 'Arbitrer le blocage ou rerouter.' : 'Relire la situation tactique.',
        missionId: mission.missionId,
        taskId,
        traceId: mission.traceRefs[0] ?? null,
        verificationRef: null
      }
    })

  return [...decisionCards, ...missionCards].slice(0, 4)
}

function createWorkshopCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  return dashboard.tasks.tasks
    .filter((task) => task.task.status === 'in_progress')
    .sort(compareActiveTasks)
    .slice(0, 4)
    .map((task) => {
      const mission = indices.missionByTaskId.get(task.task.id)
      const latestStep = task.recentWorkflowSteps[0]

      return {
        cardId: `workshop:${task.task.id}`,
        roomId: 'workshop',
        kindLabel: 'Run',
        seal: latestStep === undefined ? 'Live' : `Seq ${latestStep.sequenceId}`,
        title: task.task.title,
        subtitle: `${task.task.id} · ${task.assigneeAgentName ?? task.assigneeAgentId ?? 'non assigne'}`,
        detail:
          latestStep?.detail ?? task.recentEntries[0]?.detail ?? 'Execution active sans checkpoint recent détaillé.',
        tone: task.alerts.length > 0 ? 'warning' : 'positive',
        badges: compactStrings([
          formatPriority(task.task.priority),
          `${task.recentWorkflowSteps.length} step(s)`,
          `${task.traceIds.length} trace(s)`,
          `${task.decisionCards.length} decision(s)`
        ]),
        nextAction: task.alerts.length > 0 ? 'Nudger, reassigner ou debugger le run.' : 'Ouvrir le run et lire le prochain checkpoint.',
        missionId: mission?.missionId ?? null,
        taskId: task.task.id,
        traceId: latestStep?.traceId ?? task.traceIds[0] ?? null,
        verificationRef: null
      }
    })
}

function createBranchFinisherCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  const allowedOptionCount = dashboard.branchFinisher.options.filter((option) => option.allowed).length
  const branchCard: MissionBoardRoomCard = {
    cardId: `branch:${dashboard.branchFinisher.branch}`,
    roomId: 'branch-finisher',
    kindLabel: 'Gate',
    seal: dashboard.branchFinisher.shipBlocked ? 'Blocked' : 'Ready',
    title: `Branch ${dashboard.branchFinisher.branch}`,
    subtitle: 'merge / pr / keep / discard',
    detail:
      dashboard.branchFinisher.blockingReasons[0] ??
      'Merge et PR redeviennent lisibles depuis les options de fin de branche.',
    tone: dashboard.branchFinisher.shipBlocked ? 'critical' : 'positive',
    badges: compactStrings([
      `${allowedOptionCount} option(s) autorisee(s)`,
      `${dashboard.branchFinisher.verification.acceptedCount} accepted`,
      `${dashboard.branchFinisher.verification.needsWorkCount} needs work`
    ]),
    nextAction:
      dashboard.branchFinisher.shipBlocked
        ? 'Demander verification ou combler les preuves manquantes.'
        : 'Clore proprement ou lancer la PR.',
    missionId: null,
    taskId: null,
    traceId: null,
    verificationRef: null
  }

  const queueCards: MissionBoardRoomCard[] = [...dashboard.verificationQueue.items]
    .sort(compareVerificationQueueItems)
    .slice(0, 3)
    .map((item: MissionBoardVerificationQueueItem) => ({
      cardId: `branch:item:${item.queueId}`,
      roomId: 'branch-finisher' as const,
      kindLabel: 'Verification',
      seal: formatVerificationQueueStatus(item.queueStatus),
      title: item.taskTitle,
      subtitle: `${item.taskId} · ${formatTaskStatus(item.taskStatus)} · ${item.assigneeAgentName ?? 'non assigne'}`,
      detail: describeVerificationQueueItem(item),
      tone: toneForVerificationQueueItem(item),
      badges: compactStrings([
        `${item.unmetRequirementCodes.length} requirement(s)`,
        `${item.evidenceCount} evidence`,
        item.verificationRef
      ]),
      nextAction: nextActionForVerificationQueueItem(item.queueStatus),
      missionId: indices.missionByTaskId.get(item.taskId)?.missionId ?? null,
      taskId: item.taskId,
      traceId: item.traceId ?? null,
      verificationRef: item.verificationRef
    }))

  return [branchCard, ...queueCards]
}

function createSeanceArchiveCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  const lineageCards: MissionBoardRoomCard[] = [...dashboard.sessionLineage.nodes]
    .sort((left, right) => right.lastSequenceId - left.lastSequenceId)
    .slice(0, 3)
    .map((node) => ({
      cardId: `seance:${node.traceId}`,
      roomId: 'seance-archive' as const,
      kindLabel: 'Seance',
      seal: node.status === 'completed' ? 'Closed' : 'Live',
      title: node.title,
      subtitle: `${node.traceId} · ${node.runId}`,
      detail:
        node.decisionTitles[0] ??
        `${node.evidenceRefs.length} evidence ref(s), ${node.missionIds.length} mission(s), ${node.agentIds.length} agent(s).`,
      tone:
        node.predecessorTraceIds.length === 0 && node.successorTraceIds.length === 0 && node.decisionTitles.length > 0
          ? 'warning'
          : node.status === 'completed'
            ? 'neutral'
            : 'positive',
      badges: compactStrings([
        `${node.decisionTitles.length} decision(s)`,
        `${node.evidenceRefs.length} evidence`,
        `${node.successorTraceIds.length + node.predecessorTraceIds.length} lien(s)`
      ]),
      nextAction: 'Relire la seance ou comparer les preuves.',
      missionId: node.missionIds[0] ?? null,
      taskId: node.taskIds[0] ?? null,
      traceId: node.traceId,
      verificationRef: null
    }))

  if (lineageCards.length > 0) {
    return lineageCards
  }

  return dashboard.board.decisionCards.slice(0, 3).map((card) => ({
    cardId: `seance:decision:${card.id}`,
    roomId: 'seance-archive' as const,
    kindLabel: 'Decision',
    seal: card.isStructured ? 'Anchored' : 'Loose',
    title: card.title,
    subtitle: `${card.taskTitle ?? card.taskId ?? 'unscoped'} · ${card.timestamp}`,
    detail: card.detail,
    tone: card.isStructured ? 'neutral' : 'warning',
    badges: compactStrings([
      `${card.evidence.length} evidence`,
      `${card.missingFields.length} gap(s)`,
      card.traceId
    ]),
    nextAction: 'Comparer la decision avec sa preuve et sa lineage.',
    missionId: card.taskId === null ? null : indices.missionByTaskId.get(card.taskId)?.missionId ?? null,
    taskId: card.taskId,
    traceId: card.traceId,
    verificationRef: null
  }))
}

function createWatchtowerCards(
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardRoomCard[] {
  const releaseGateCard: MissionBoardRoomCard = {
    cardId: 'watchtower:release-gate',
    roomId: 'watchtower',
    kindLabel: 'Gate',
    seal: dashboard.supervision.releaseGate.releaseBlocked ? 'NO-GO' : 'GO',
    title: 'Release gate',
    subtitle: `branch ${dashboard.branchFinisher.branch}`,
    detail:
      dashboard.supervision.releaseGate.blockingReasons[0] ?? 'Aucun blocage release explicite sur cette run.',
    tone: dashboard.supervision.releaseGate.releaseBlocked ? 'critical' : 'positive',
    badges: compactStrings([
      `${dashboard.supervision.releaseGate.blockedMissionCount} mission(s) bloquee(s)`,
      `${dashboard.supervision.releaseGate.verificationBlockingCount} verification blocker(s)`,
      `${dashboard.supervision.releaseGate.securityBlockingCount} security blocker(s)`
    ]),
    nextAction:
      dashboard.supervision.releaseGate.releaseBlocked
        ? 'Escalader ou resoudre les preuves et dependances critiques.'
        : 'Conserver la surveillance et confirmer la cloture.',
    missionId: null,
    taskId: dashboard.supervision.releaseGate.blockingTaskIds[0] ?? null,
    traceId: null,
    verificationRef: null
  }
  const securityCards: MissionBoardRoomCard[] = dashboard.branchFinisher.securityCards
    .filter((card) => card.blocksShip)
    .slice(0, 2)
    .map((card) => ({
      cardId: `watchtower:security:${card.id}`,
      roomId: 'watchtower' as const,
      kindLabel: 'Incident',
      seal: card.severity,
      title: card.title,
      subtitle: `${card.surfaceId} · ${card.taskId ?? 'global'}`,
      detail: card.detail,
      tone: card.blocksShip ? 'critical' : 'warning',
      badges: compactStrings([card.traceId, card.taskId, `seq ${card.sequenceId}`]),
      nextAction: 'Ouvrir une review securite ou isoler la surface.',
      missionId: card.taskId === null ? null : indices.missionByTaskId.get(card.taskId)?.missionId ?? null,
      taskId: card.taskId,
      traceId: card.traceId,
      verificationRef: null
    }))
  const lineageAlerts: MissionBoardRoomCard[] = dashboard.sessionLineage.alerts.slice(0, 2).map((alert) => ({
    cardId: `watchtower:lineage:${alert.traceId}:${alert.code}`,
    roomId: 'watchtower' as const,
    kindLabel: 'Lineage',
    seal: alert.code,
    title: alert.code,
    subtitle: alert.traceId,
    detail: alert.message,
    tone: alert.severity === 'warning' ? 'warning' : 'neutral',
    badges: compactStrings([alert.severity]),
    nextAction: 'Rejouer la causalite ou rattacher la preuve manquante.',
    missionId: null,
    taskId: null,
    traceId: alert.traceId,
    verificationRef: null
  }))
  const attentionCards: MissionBoardRoomCard[] = dashboard.observability.attentionItems
    .filter((item) => item.severity === 'critical' || item.severity === 'warning')
    .slice(0, 2)
    .map((item) => ({
      cardId: `watchtower:attention:${item.id}`,
      roomId: 'watchtower' as const,
      kindLabel: 'Signal',
      seal: item.severity,
      title: item.label,
      subtitle: describeAttentionItemContext(item),
      detail: item.detail,
      tone: toneForAttentionSeverity(item.severity),
      badges: compactStrings([item.taskId, item.traceId]),
      nextAction: 'Traiter le signal avant de continuer la fermeture.',
      missionId: item.taskId === null ? null : indices.missionByTaskId.get(item.taskId)?.missionId ?? null,
      taskId: item.taskId,
      traceId: item.traceId,
      verificationRef: null
    }))

  return [releaseGateCard, ...securityCards, ...lineageAlerts, ...attentionCards].slice(0, 5)
}

function createDrawer(
  cards: readonly MissionBoardRoomCard[],
  rooms: readonly MissionBoardRoom[],
  dashboard: RuntimeDashboardView,
  indices: MissionBoardIndices
): MissionBoardDrawer {
  const focusCard = [...cards].sort(compareFocusCards)[0]
  if (focusCard === undefined) {
    return {
      kindLabel: 'Overview',
      title: 'No focused card',
      subtitle: 'Mission Board attend des signaux runtime pour ouvrir un dossier laterale.',
      tone: 'neutral',
      nextAction: 'Choisir une autre run ou attendre un signal canonique.',
      referencePills: [],
      sections: [
        {
          id: 'overview',
          label: 'Overview',
          tone: 'neutral',
          lines: ['Aucune carte n est encore disponible pour peupler le dossier lateral.']
        }
      ]
    }
  }

  const task = focusCard.taskId === null ? null : indices.taskById.get(focusCard.taskId) ?? null
  const mission =
    focusCard.missionId !== null
      ? indices.missionById.get(focusCard.missionId) ?? null
      : focusCard.taskId === null
        ? null
        : indices.missionByTaskId.get(focusCard.taskId) ?? null
  const verificationGate = focusCard.taskId === null ? null : indices.verificationByTaskId.get(focusCard.taskId) ?? null
  const queueItem = focusCard.taskId === null ? null : indices.queueByTaskId.get(focusCard.taskId) ?? null
  const lineage =
    focusCard.traceId !== null
      ? indices.sessionByTraceId.get(focusCard.traceId) ?? null
      : task?.traceIds[0] === undefined
        ? null
        : indices.sessionByTraceId.get(task.traceIds[0]) ?? null
  const room = rooms.find((candidate) => candidate.roomId === focusCard.roomId)

  return {
    kindLabel: focusCard.kindLabel,
    title: focusCard.title,
    subtitle: `${room?.label ?? focusCard.roomId} · ${focusCard.subtitle}`,
    tone: focusCard.tone,
    nextAction: focusCard.nextAction,
    referencePills: compactStrings([
      focusCard.seal,
      focusCard.taskId,
      focusCard.traceId,
      focusCard.verificationRef,
      mission?.missionId ?? null
    ]),
    sections: [
      {
        id: 'overview',
        label: 'Overview',
        tone: focusCard.tone,
        lines: compactStrings([
          focusCard.detail,
          task === null
            ? null
            : `Task ${task.task.id} · ${formatTaskStatus(task.task.status)} · ${task.assigneeAgentName ?? task.assigneeAgentId ?? 'non assigne'}`,
          mission === null
            ? null
            : `Mission ${formatMissionStatus(mission.status)} · ${mission.activeItemCount} item(s) actifs · ${mission.blockedItemIds.length} bloque(s)`
        ])
      },
      {
        id: 'acceptance',
        label: 'Acceptance',
        tone: verificationGate?.isReadyForDone === true ? 'positive' : verificationGate === null ? 'neutral' : 'warning',
        lines:
          verificationGate === null
            ? ['Aucune gate de verification explicite n est attachee a cette carte.']
            : compactStrings([
                verificationGate.isReadyForDone
                  ? 'Verification gate ready for done.'
                  : `${verificationGate.unmetRequirementCodes.length} requirement(s) restent non satisfaits.`,
                verificationGate.unmetRequirementCodes[0] ?? null,
                verificationGate.unmetRequirementCodes[1] ?? null,
                queueItem?.verificationRef ?? null
              ])
      },
      {
        id: 'execution',
        label: 'Execution',
        tone: task?.statusCategory === 'active' ? 'positive' : 'neutral',
        lines:
          task === null
            ? ['Aucun contexte d execution cible n est attache a cette carte.']
            : compactStrings([
                task.recentWorkflowSteps[0]?.step ?? null,
                task.recentWorkflowSteps[0]?.detail ?? task.recentEntries[0]?.detail ?? null,
                `${task.traceIds.length} trace(s) · ${task.recentToolCalls.length} tool call(s) · ${task.decisionCards.length} decision(s)`
              ])
      },
      {
        id: 'lineage',
        label: 'Lineage',
        tone: lineage === null ? 'neutral' : 'positive',
        lines:
          lineage === null
            ? ['Aucune seance indexee ne recoupe encore cette carte.']
            : compactStrings([
                `run ${lineage.runId}`,
                `${lineage.decisionTitles.length} decision(s)`,
                `${lineage.evidenceRefs.length} evidence ref(s)`,
                `${lineage.predecessorTraceIds.length + lineage.successorTraceIds.length} lien(s) causaux` 
              ])
      },
      {
        id: 'commands',
        label: 'Commands',
        tone: 'neutral',
        lines: compactStrings([
          `Prochaine action: ${focusCard.nextAction}`,
          room === undefined ? null : `Commande dominante: ${room.dominantCommand}`
        ])
      }
    ]
  }
}

function createTimeline(dashboard: RuntimeDashboardView): MissionBoardTimelineEntry[] {
  const workflowEntries: MissionBoardSequencedTimelineEntry[] = dashboard.tasks.tasks.flatMap((task) =>
    task.recentWorkflowSteps.map((step) => ({
      id: `timeline:step:${step.sequenceId}`,
      sequenceId: step.sequenceId,
      roomId: inferRoomFromWorkflowStep(step),
      title: step.step,
      detail: step.detail,
      timestamp: step.timestamp,
      tone: toneForWorkflowStep(step),
      taskId: step.taskId ?? task.task.id,
      traceId: step.traceId ?? task.traceIds[0] ?? null
    }))
  )
  const decisionEntries: MissionBoardSequencedTimelineEntry[] = dashboard.board.decisionCards.map((card) => ({
    id: `timeline:decision:${card.id}`,
    sequenceId: card.sequenceId,
    roomId: 'seance-archive' as const,
    title: card.title,
    detail: card.detail,
    timestamp: card.timestamp,
    tone: card.missingFields.length > 0 ? 'warning' : 'neutral',
    taskId: card.taskId,
    traceId: card.traceId
  }))

  return [...workflowEntries, ...decisionEntries]
    .sort((left, right) => right.sequenceId - left.sequenceId)
    .slice(0, 8)
    .map(({ sequenceId: _sequenceId, ...entry }) => entry)
}

function compareQueuedTasks(left: MissionBoardTaskInspection, right: MissionBoardTaskInspection): number {
  const leftPriority = PRIORITY_RANK[left.task.priority ?? 'medium'] ?? 1
  const rightPriority = PRIORITY_RANK[right.task.priority ?? 'medium'] ?? 1
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority
  }

  if (left.alerts.length !== right.alerts.length) {
    return right.alerts.length - left.alerts.length
  }

  return left.task.title.localeCompare(right.task.title)
}

function compareActiveTasks(left: MissionBoardTaskInspection, right: MissionBoardTaskInspection): number {
  if (left.alerts.length !== right.alerts.length) {
    return right.alerts.length - left.alerts.length
  }

  const leftStep = left.recentWorkflowSteps[0]?.sequenceId ?? -1
  const rightStep = right.recentWorkflowSteps[0]?.sequenceId ?? -1
  if (leftStep !== rightStep) {
    return rightStep - leftStep
  }

  return left.task.title.localeCompare(right.task.title)
}

function compareVerificationQueueItems(
  left: MissionBoardVerificationQueueItem,
  right: MissionBoardVerificationQueueItem
): number {
  const leftRank = VERIFICATION_QUEUE_RANK[left.queueStatus] ?? 3
  const rightRank = VERIFICATION_QUEUE_RANK[right.queueStatus] ?? 3
  if (leftRank !== rightRank) {
    return leftRank - rightRank
  }

  if (left.unmetRequirementCodes.length !== right.unmetRequirementCodes.length) {
    return right.unmetRequirementCodes.length - left.unmetRequirementCodes.length
  }

  return left.taskTitle.localeCompare(right.taskTitle)
}

function compareActiveMissions(
  left: MissionBoardMission,
  right: MissionBoardMission,
  indices: MissionBoardIndices
): number {
  const leftPriority = PRIORITY_RANK[left.priority] ?? 1
  const rightPriority = PRIORITY_RANK[right.priority] ?? 1
  if (leftPriority !== rightPriority) {
    return leftPriority - rightPriority
  }

  const leftEscalations = indices.openEscalationCountByMissionId.get(left.missionId) ?? 0
  const rightEscalations = indices.openEscalationCountByMissionId.get(right.missionId) ?? 0
  if (leftEscalations !== rightEscalations) {
    return rightEscalations - leftEscalations
  }

  return right.updatedAt.localeCompare(left.updatedAt)
}

function compareFocusCards(left: MissionBoardRoomCard, right: MissionBoardRoomCard): number {
  const leftRoomRank = ROOM_FOCUS_RANK[left.roomId] ?? 99
  const rightRoomRank = ROOM_FOCUS_RANK[right.roomId] ?? 99
  if (leftRoomRank !== rightRoomRank) {
    return leftRoomRank - rightRoomRank
  }

  const leftToneRank = TONE_RANK[left.tone] ?? 99
  const rightToneRank = TONE_RANK[right.tone] ?? 99
  if (leftToneRank !== rightToneRank) {
    return leftToneRank - rightToneRank
  }

  if (left.taskId !== null && right.taskId === null) {
    return -1
  }

  if (left.taskId === null && right.taskId !== null) {
    return 1
  }

  return left.title.localeCompare(right.title)
}

function toneForMission(
  mission: MissionBoardMission,
  indices: MissionBoardIndices
): RuntimeDashboardUiTone {
  if (mission.status === 'blocked' || (indices.openEscalationCountByMissionId.get(mission.missionId) ?? 0) > 0) {
    return 'critical'
  }

  if (mission.status === 'verifying' || mission.blockedItemIds.length > 0) {
    return 'warning'
  }

  return mission.activeItemCount > 0 ? 'positive' : 'neutral'
}

function toneForVerificationQueueItem(
  item: MissionBoardVerificationQueueItem
): RuntimeDashboardUiTone {
  switch (item.queueStatus) {
    case 'rejected':
      return 'critical'
    case 'needs_work':
      return 'warning'
    case 'verifying':
      return 'positive'
    case 'accepted':
      return 'positive'
    default:
      return 'neutral'
  }
}

function nextActionForVerificationQueueItem(status: MissionBoardVerificationQueueItem['queueStatus']): string {
  switch (status) {
    case 'rejected':
      return 'Corriger les preuves et relancer la verification.'
    case 'needs_work':
      return 'Combler les requirements avant une nouvelle passe.'
    case 'verifying':
      return 'Attendre le verdict ou ouvrir le dossier de verification.'
    case 'accepted':
      return 'Clore la task ou preparer la fermeture de mission.'
    default:
      return 'Mettre la task en verification lorsqu elle devient eligible.'
  }
}

function describeVerificationQueueItem(item: MissionBoardVerificationQueueItem): string {
  if (item.unmetRequirementCodes.length > 0) {
    return compactStrings([
      `${item.unmetRequirementCodes.length} requirement(s) encore ouvertes`,
      item.unmetRequirementCodes[0] ?? null,
      item.reviewApplicable ? `review ${item.reviewReady ? 'ready' : 'blocked'}` : null,
      `done ${item.doneReady ? 'ready' : 'blocked'}`
    ]).join(' · ')
  }

  return compactStrings([
    `${item.evidenceCount} evidence`,
    `${item.traceCount} trace(s)`,
    item.verdict === null ? 'verdict pending' : `verdict ${item.verdict}`
  ]).join(' · ')
}

function describeAttentionItemContext(
  item: RuntimeDashboardView['observability']['attentionItems'][number]
): string {
  const context = compactStrings([item.taskId, item.traceId, item.kind]).join(' · ')
  return context.length > 0 ? context : item.kind
}

function toneForAttentionSeverity(
  severity: RuntimeDashboardView['observability']['attentionItems'][number]['severity']
): RuntimeDashboardUiTone {
  switch (severity) {
    case 'critical':
      return 'critical'
    case 'warning':
      return 'warning'
    default:
      return 'neutral'
  }
}

function inferRoomFromWorkflowStep(step: MissionBoardWorkflowStep): MissionBoardRoomId {
  if (step.sourceEventType === 'verification_gate' || readMetadataString(step.metadata, 'verificationRef') !== null) {
    return 'branch-finisher'
  }

  if (
    step.sourceEventType === 'routing' ||
    step.sourceEventType === 'task_handoff' ||
    step.sourceEventType === 'branch_finish_options'
  ) {
    return 'war-room'
  }

  if (
    step.sourceEventType === 'power_card_activation' ||
    step.sourceEventType.includes('security') ||
    step.sourceEventType.includes('error')
  ) {
    return 'watchtower'
  }

  return 'workshop'
}

function toneForWorkflowStep(step: MissionBoardWorkflowStep): RuntimeDashboardUiTone {
  if (step.sourceEventType === 'verification_gate') {
    return readMetadataString(step.metadata, 'verdict') === 'FAIL' ? 'critical' : 'positive'
  }

  if (step.sourceEventType === 'power_card_activation') {
    return readMetadataBoolean(step.metadata, 'allowed') === false ? 'warning' : 'neutral'
  }

  if (step.sourceEventType === 'task_handoff' || step.sourceEventType === 'routing') {
    return 'positive'
  }

  return 'neutral'
}

function countWatchSignals(dashboard: RuntimeDashboardView): number {
  return (
    dashboard.supervision.releaseGate.blockingReasons.length +
    dashboard.sessionLineage.alerts.length +
    dashboard.branchFinisher.securityCards.filter((card) => card.blocksShip).length +
    dashboard.observability.attentionItems.filter((item) => item.severity === 'critical' || item.severity === 'warning').length
  )
}

function formatTaskStatus(status: string): string {
  return TASK_STATUS_LABELS[status] ?? status
}

function formatMissionStatus(status: string): string {
  return MISSION_STATUS_LABELS[status] ?? status
}

function formatVerificationQueueStatus(status: string): string {
  return VERIFICATION_QUEUE_LABELS[status] ?? status
}

function formatPriority(priority: string | null | undefined): string | null {
  if (priority === null || priority === undefined || priority.length === 0) {
    return null
  }

  return `priority ${priority}`
}

function extractTaskIdFromMissionId(missionId: string): string | null {
  const prefix = 'mission:task:'
  return missionId.startsWith(prefix) ? missionId.slice(prefix.length) : null
}

function readMetadataString(metadata: unknown, key: string): string | null {
  if (metadata === null || typeof metadata !== 'object' || !(key in metadata)) {
    return null
  }

  const value = (metadata as Record<string, unknown>)[key]
  return typeof value === 'string' && value.length > 0 ? value : null
}

function readMetadataBoolean(metadata: unknown, key: string): boolean | null {
  if (metadata === null || typeof metadata !== 'object' || !(key in metadata)) {
    return null
  }

  const value = (metadata as Record<string, unknown>)[key]
  return typeof value === 'boolean' ? value : null
}

function compactStrings(values: ReadonlyArray<string | null | undefined>): string[] {
  return values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
}