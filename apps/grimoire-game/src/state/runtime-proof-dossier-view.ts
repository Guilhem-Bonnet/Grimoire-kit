import type { RuntimeDashboardUiTone } from './runtime-dashboard-ui-view';
import type { RuntimeDashboardView } from './runtime-dashboard-view';

export interface RuntimeProofDossierHeader {
  title: string;
  subtitle: string;
  tone: RuntimeDashboardUiTone;
  runId: string | null;
  releaseBlocked: boolean;
  blockingReasonCount: number;
}

export interface RuntimeProofDossierStatCard {
  id: string;
  label: string;
  value: string | number;
  hint: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeProofDossierGateCard {
  id: string;
  label: string;
  value: string | number;
  detail: string;
  tone: RuntimeDashboardUiTone;
}

export interface RuntimeProofDossierPackCard {
  id: string;
  missionTitle: string;
  verificationRef: string;
  status: RuntimeDashboardView['verificationEvidencePacks']['packs'][number]['status'];
  verdict: RuntimeDashboardView['verificationEvidencePacks']['packs'][number]['verdict'];
  checkedBy: string;
  checkedAt: string;
  evidenceCount: number;
  controlCount: number;
  externalReviewCount: number;
  attested: boolean;
  missingExpectedProofCount: number;
  tone: RuntimeDashboardUiTone;
  detail: string;
}

export interface RuntimeProofDossierView {
  protocolVersion: string;
  lastSequenceId: number;
  header: RuntimeProofDossierHeader;
  statCards: readonly RuntimeProofDossierStatCard[];
  gates: readonly RuntimeProofDossierGateCard[];
  blockingReasons: readonly string[];
  packs: readonly RuntimeProofDossierPackCard[];
}

export function createRuntimeProofDossierView(dashboard: RuntimeDashboardView): RuntimeProofDossierView {
  const releaseGate = dashboard.supervision.releaseGate;
  const packs = dashboard.verificationEvidencePacks.packs.map((pack) => ({
    id: pack.packId,
    missionTitle: pack.missionTitle,
    verificationRef: pack.verificationRef,
    status: pack.status,
    verdict: pack.verdict,
    checkedBy: pack.checkedBy,
    checkedAt: pack.checkedAt,
    evidenceCount: pack.evidence.length,
    controlCount: pack.controlRefs.length,
    externalReviewCount: pack.externalReviews.length,
    attested: pack.attestation !== null,
    missingExpectedProofCount: pack.proofCoverage?.missingExpectedProofRefs.length ?? 0,
    tone: toneForPack(pack),
    detail:
      pack.proofCoverage?.missingExpectedProofRefs.length
        ? `${pack.proofCoverage.missingExpectedProofRefs.length} preuve(s) attendue(s) manquante(s).`
        : `${pack.evidence.length} evidence(s), ${pack.controlRefs.length} control(s), verdict ${pack.verdict}.`
  }));

  return {
    protocolVersion: dashboard.protocolVersion,
    lastSequenceId: dashboard.lastSequenceId,
    header: {
      title: 'Dossier de preuve',
      subtitle: releaseGate.releaseBlocked
        ? 'Le verdict de release reste bloque tant que les preuves et gates critiques ne sont pas closes.'
        : 'Le verdict de release est justifiable a partir des evidence packs et des gates visibles.',
      tone: releaseGate.releaseBlocked ? 'critical' : 'positive',
      runId: dashboard.projectRegistry?.activeProject.runId ?? dashboard.nodeFleet.summary.runId,
      releaseBlocked: releaseGate.releaseBlocked,
      blockingReasonCount: releaseGate.blockingReasons.length
    },
    statCards: [
      {
        id: 'packs',
        label: 'Evidence packs',
        value: dashboard.summary.verificationEvidencePackCount,
        hint: `${dashboard.summary.verificationAttestationCount} attestation(s)`,
        tone: dashboard.summary.verificationEvidencePackCount > 0 ? 'positive' : 'critical'
      },
      {
        id: 'missing-proofs',
        label: 'Preuves manquantes',
        value: dashboard.summary.missingExpectedProofCount,
        hint: 'proof coverage attendu',
        tone: dashboard.summary.missingExpectedProofCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'verification-blockers',
        label: 'Verification blockers',
        value: releaseGate.verificationBlockingCount,
        hint: `${dashboard.summary.verificationQueueCount} item(s) en file`,
        tone: releaseGate.verificationBlockingCount > 0 ? 'critical' : 'positive'
      },
      {
        id: 'security-blockers',
        label: 'Security blockers',
        value: releaseGate.securityBlockingCount,
        hint: 'cards qui bloquent le ship',
        tone: releaseGate.securityBlockingCount > 0 ? 'critical' : 'positive'
      },
      {
        id: 'blocked-missions',
        label: 'Missions bloquees',
        value: releaseGate.blockedMissionCount,
        hint: `${dashboard.summary.missionCount} mission(s) suivie(s)`,
        tone: releaseGate.blockedMissionCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'lineage-alerts',
        label: 'Lineage alerts',
        value: releaseGate.staleLineageAlertCount,
        hint: 'coherence causale avant diffusion',
        tone: releaseGate.staleLineageAlertCount > 0 ? 'warning' : 'positive'
      }
    ],
    gates: [
      {
        id: 'release-gate',
        label: 'Verdict release',
        value: releaseGate.releaseBlocked ? 'NO-GO' : 'GO',
        detail: releaseGate.blockingReasons[0] ?? 'Aucun blocage release explicite.',
        tone: releaseGate.releaseBlocked ? 'critical' : 'positive'
      },
      {
        id: 'ship-gate',
        label: 'Ship gate',
        value: releaseGate.shipBlocked ? 'bloque' : 'ouvert',
        detail: `${releaseGate.blockingTaskIds.length} task(s) reliee(s) au blocage.`,
        tone: releaseGate.shipBlocked ? 'critical' : 'positive'
      },
      {
        id: 'verification-blockers',
        label: 'Verification blockers',
        value: releaseGate.verificationBlockingCount,
        detail:
          releaseGate.verificationBlockingCount > 0
            ? `${releaseGate.verificationBlockingCount} item(s) de verification restent bloquants.`
            : 'Aucun item de verification ne bloque la diffusion.',
        tone: releaseGate.verificationBlockingCount > 0 ? 'critical' : 'positive'
      },
      {
        id: 'security-blockers',
        label: 'Security blockers',
        value: releaseGate.securityBlockingCount,
        detail:
          releaseGate.securityBlockingCount > 0
            ? 'Des security cards bloquent encore le ship.'
            : 'Aucune security card bloquante n est active.',
        tone: releaseGate.securityBlockingCount > 0 ? 'critical' : 'positive'
      },
      {
        id: 'mission-blockers',
        label: 'Missions bloquees',
        value: releaseGate.blockedMissionCount,
        detail:
          releaseGate.blockedMissionCount > 0
            ? 'Le mission ledger expose encore des missions bloquees.'
            : 'Le mission ledger ne contient pas de mission bloquee.',
        tone: releaseGate.blockedMissionCount > 0 ? 'warning' : 'positive'
      },
      {
        id: 'lineage-gate',
        label: 'Lineage',
        value: releaseGate.staleLineageAlertCount,
        detail:
          releaseGate.staleLineageAlertCount > 0
            ? 'Des alertes de lineage restent ouvertes sur les sessions closes.'
            : 'Aucune alerte stale de lineage n affecte le verdict release.',
        tone: releaseGate.staleLineageAlertCount > 0 ? 'warning' : 'positive'
      }
    ],
    blockingReasons: releaseGate.blockingReasons,
    packs
  };
}

function toneForPack(pack: RuntimeDashboardView['verificationEvidencePacks']['packs'][number]): RuntimeDashboardUiTone {
  if (pack.proofCoverage?.missingExpectedProofRefs.length) {
    return 'warning';
  }

  if (pack.verdict === 'fail' || pack.status === 'rejected') {
    return 'critical';
  }

  if (pack.verdict === 'warn' || pack.status === 'needs_work') {
    return 'warning';
  }

  if (pack.attestation !== null) {
    return 'positive';
  }

  return 'neutral';
}