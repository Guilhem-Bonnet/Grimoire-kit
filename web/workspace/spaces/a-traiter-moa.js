// À traiter — les items MOA/MOE (#1342), purs et sans DOM : testés par node --test.
//
// Le serveur émet deux types (`workspace_attention.py`) : `requirement_unvalidated` (une exigence à faire valider,
// un incident dont le post-mortem est dû, des questions exemptées) et `requirement_recette` (une exigence livrée à
// recetter, ou une recette scellée dont la tâche de suite reste à créer). Ce module dit, pour chacun, qui doit agir,
// ce qu'il faut faire et la commande exacte à lancer dans un terminal.
//
// Le cockpit ne valide ni ne recette jamais : ces gestes exigent un terminal interactif humain, par conception. La
// commande est donc affichée et copiable, jamais exécutée d'ici.

/** Les mots, icônes et verbes par type ; le mot précis dépend de l'étape (`moaGuide`). */
export const MOA_KINDS = {
  requirement_unvalidated: { word: 'À valider par la MOA', icon: 'user', tone: 'warn', action: 'Voir le projet' },
  requirement_recette: { word: 'À recetter par la MOA', icon: 'seal', tone: 'warn', action: 'Voir le projet' },
};

const MAX_COMMANDS = 5;

const list = (value) => (Array.isArray(value) ? value.filter((v) => typeof v === 'string' && v) : []);
const plural = (n, one, many) => `${n} ${n > 1 ? many : one}`;

/** L'étape d'un item MOA : `validation`, `postmortem`, `questions`, `recette`, `suite` ; vide pour un autre type. */
export function moaStage(item) {
  const ref = (item && item.ref) || {};
  if (item && item.kind === 'requirement_recette') return ref.stage === 'suite' ? 'suite' : 'recette';
  if (item && item.kind === 'requirement_unvalidated') {
    if (list(ref.task_ids).length) return 'validation';
    if (list(ref.postmortem).length) return 'postmortem';
    if (list(ref.questions).length) return 'questions';
    return 'validation';
  }
  return '';
}

function validationCommands(ref) {
  const keys = list(ref.requirement_keys);
  if (!keys.length) return ['grimoire cadrage interview'];
  return keys.slice(0, MAX_COMMANDS).map((key) => `grimoire cadrage approve ${key}`);
}

/** Ce que l'item dit à l'humain : `{ stage, word, who, explain, gesture, commands, notes }`, ou `null` hors MOA.
 *  `who` répond à « qui doit agir » ; `commands` sont à lancer dans un terminal, `notes` complètent l'explication. */
export function moaGuide(item) {
  const stage = moaStage(item);
  if (!stage) return null;
  const ref = item.ref || {};
  const base = { stage, commands: [], notes: [] };
  if (stage === 'validation') {
    const tasks = list(ref.task_ids).length;
    const guide = {
      ...base,
      word: 'À valider par la MOA',
      who: 'La MOA',
      explain: `${plural(tasks, 'tâche est passée', 'tâches sont passées')} « prête » sans exigence validée. `
        + 'L’orchestrateur propose les exigences, il ne les valide jamais : seule la MOA le fait.',
      gesture: 'La MOA relit l’exigence puis la valide depuis son terminal.',
      commands: validationCommands(ref),
    };
    if (!list(ref.requirement_keys).length) {
      guide.notes.push('Aucune exigence n’est encore rattachée à ces tâches : l’entretien de cadrage vient d’abord.');
    }
    const incidents = list(ref.postmortem).length;
    const questions = list(ref.questions).length;
    if (incidents) guide.notes.push(`${plural(incidents, 'incident exempté', 'incidents exemptés')} : post-mortem dû.`);
    if (questions) guide.notes.push(`${plural(questions, 'question exemptée', 'questions exemptées')}.`);
    return guide;
  }
  if (stage === 'postmortem') {
    const n = list(ref.postmortem).length;
    return {
      ...base,
      word: 'Post-mortem dû',
      who: 'L’orchestrateur, puis la MOA',
      explain: `${plural(n, 'incident a été traité', 'incidents ont été traités')} sans exigence : on rétablit d’abord, `
        + 'on cadre après. L’exigence reste à poser au post-mortem.',
      gesture: 'L’orchestrateur mène l’entretien de cadrage de l’incident, puis la MOA valide l’exigence.',
      commands: ['grimoire cadrage interview'],
    };
  }
  if (stage === 'questions') {
    const n = list(ref.questions).length;
    return {
      ...base,
      word: n > 1 ? 'Questions exemptées' : 'Question exemptée',
      who: 'Personne pour l’instant',
      explain: `${plural(n, 'question a été traitée', 'questions ont été traitées')} sans exigence, par exemption : `
        + 'une question n’appelle pas de validation.',
      gesture: 'Rien à faire, sauf si la question devient une demande de construction : elle doit alors être cadrée.',
    };
  }
  if (stage === 'recette') {
    const keys = list(ref.requirements);
    return {
      ...base,
      word: 'À recetter par la MOA',
      who: 'La MOA',
      explain: `${plural(keys.length, 'exigence est livrée', 'exigences sont livrées')} : la MOA prononce la recette `
        + 'contre l’exigence qu’elle a validée, pas contre les critères écrits par l’agent.',
      gesture: 'La MOA recette depuis son terminal : le verdict est scellé à son nom.',
      commands: keys.slice(0, MAX_COMMANDS).map((key) => `grimoire cadrage recette ${key}`),
    };
  }
  const keys = list(ref.requirements);
  return {
    ...base,
    word: 'Suite à créer',
    who: 'L’orchestrateur (la MOE)',
    explain: `${plural(keys.length, 'recette a été scellée', 'recettes ont été scellées')} avec des écarts (réserve ou `
      + 'non conforme), mais la tâche de suite n’existe pas encore.',
    gesture: 'La MOE crée la tâche de reprise ou de réserve ; la MOA recettera de nouveau la livraison.',
    commands: list(ref.commands).slice(0, MAX_COMMANDS),
  };
}
