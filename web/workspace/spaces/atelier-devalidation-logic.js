// Atelier de spec : prévenir avant de défaire la validation d'une exigence, côté logique pure (aucun DOM ; testé par
// tests/js/atelier_devalidation.test.mjs).
//
// La validation de la MOA est liée à l'empreinte de l'exigence : sa formulation, sa priorité, ses critères (ordre indifférent) et
// son périmètre (`requirement_gate.fingerprint`). Le serveur n'expose pas cette empreinte : on compare donc, avant et après, ce que
// l'empreinte lit dans un bloc d'exigence (la ligne `- REQ-001 [Must] : formulation` et ses sous-puces), avec les mêmes retraits que
// le serveur (espaces de fin, case à cocher d'une sous-puce, puce, ordre des critères). Au moindre doute on compare plus strictement :
// une alerte de trop est sans danger, une validation défaite en silence est ce qu'on évite.

// Le même motif que `atelier_blocs._REQ` (un bloc d'exigence), et le marqueur de puce de `_ITEM`.
const REQ = /^[-*+]\s+(?:\*\*)?(REQ-\d+)(?:\*\*)?\s*(?:\[([^\]]+)\])?\s*:?\s*([\s\S]*)$/;
const ITEM = /^([-*+]|\d+[.)])\s+/;
const CHECKBOX = /^\[[ xX]\]\s+/;

/**
 * L'exigence qu'un texte de bloc déclare : `{ req, signature }`, ou `null` si le bloc n'est pas une exigence. La signature réunit
 * ce que lit l'empreinte du serveur : priorité, formulation, critères (indentation comprise, ordre et case cochée sans effet).
 */
export function parseRequirement(text) {
  const raw = String(text == null ? '' : text);
  const match = REQ.exec(raw.trim());
  if (!match) return null;
  const head = match[3].split('\n', 1)[0].trim();
  const criteria = raw.split('\n').slice(1)
    .filter((line) => line.trim() && ITEM.test(line.trim()))
    .map((line) => {
      const body = line.replace(/\s+$/, '');
      const indent = body.slice(0, body.length - body.trimStart().length);
      return `${indent}${body.trim().replace(ITEM, '').replace(CHECKBOX, '').trim()}`;
    })
    .sort();
  return { req: match[1], signature: JSON.stringify([match[1], (match[2] || '').trim(), head, criteria]) };
}

/** Les exigences que ce changement de texte réécrit (formulation, priorité ou critères), ou retire : `['REQ-001']` ou `[]`. */
export function changedRequirements(before, after) {
  const was = parseRequirement(before);
  if (!was) return [];
  const now = parseRequirement(after);
  return now && now.req === was.req && now.signature === was.signature ? [] : [was.req];
}

/**
 * Ce que dit la trace de ces exigences : `validated` (celles que la MOA a validées, avec qui et quand), `unknown` (celles dont l'état
 * n'est pas connu : trace absente). Une exigence absente d'une trace lue n'est pas validée.
 */
export function validationOf(reqs, trace) {
  const validated = [];
  const unknown = [];
  for (const req of reqs) {
    if (!trace || !Array.isArray(trace.requirements)) { unknown.push(req); continue; }
    const row = trace.requirements.find((r) => r.id === req);
    if (row && row.validated) validated.push({ req, by: String(row.validated_by || ''), at: String(row.validated_at || '') });
  }
  return { validated, unknown };
}

/** La commande que la MOA lance au terminal (la même que celle de la ligne d'état : `workspace_atelier_trace.approve_command`). */
export function approveCommand(req, slug) {
  return `grimoire cadrage approve ${req}${slug && slug !== 'principal' ? ` --chantier ${slug}` : ''}`;
}

function dayText(iso) {
  const date = new Date(iso);
  return iso && !Number.isNaN(date.getTime()) ? date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' }) : '';
}

/** La phrase de la confirmation pour une exigence validée : qui, quand, ce que la modification coûte. */
export function confirmLine(item) {
  const day = dayText(item.at);
  return `${item.req} est validée par ${item.by || 'la MOA'}${day ? ` le ${day}` : ''}. La modifier annule cette validation : il faudra la revalider.`;
}

/** La notification d'après l'enregistrement : la validation est défaite, voici la commande. */
export function afterMessage(reqs, slug) {
  const list = reqs.join(', ');
  const plural = reqs.length > 1;
  return `${list} ${plural ? 'ne sont plus validées' : 'n’est plus validée'} : revalide${plural ? '-les' : '-la'} au terminal (${reqs.map((r) => approveCommand(r, slug)).join(' ; ')}).`;
}

/** La mention discrète quand l'état de validation n'est pas connu (trace indisponible) au moment d'une modification. */
export function unknownMessage(reqs, slug) {
  return `État de validation inconnu pour ${reqs.join(', ')} : si ${reqs.length > 1 ? 'elles étaient validées, elles' : 'elle était validée, elle'} devr${reqs.length > 1 ? 'ont' : 'a'} être revalidée${reqs.length > 1 ? 's' : ''} (${reqs.map((r) => approveCommand(r, slug)).join(' ; ')}).`;
}
