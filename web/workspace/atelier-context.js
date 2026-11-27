// Le chantier ouvert dans l'Atelier, dit au chat. Sans import : le chat (chat.js) et l'espace Atelier le partagent
// sans cycle. L'Atelier le pose quand un document est affiché et le retire à son démontage ; hors Atelier, le chat
// n'envoie rien de plus. Le serveur revalide tout (drapeau, chantier, phase) : ce n'est qu'une indication.

let current = null;

/** `{ slug, phase }` d'un chantier affiché, ou `null`. */
export function setAtelierContext(value) {
  current = value && value.slug ? { slug: String(value.slug), phase: value.phase ? String(value.phase) : '' } : null;
}

export function getAtelierContext() {
  return current ? { ...current } : null;
}

// Le plan du chantier ouvert, pour la palette ⌘K (« Aller à : Exigences »). Il ne part jamais au chat : un canal à part.
let outline = null;

/**
 * `{ slug, outline(): [{ id, title, rubriques: [{ name }] }], go(phase, rub) }` tant qu'un document est affiché ; `null` au
 * démontage. `outline` est lu à l'ouverture de la palette, jamais en continu ; `go` mène à la phase (`rub` = -1) ou à sa rubrique.
 */
export function setAtelierOutline(value) {
  outline = value && value.slug ? value : null;
}

export function getAtelierOutline() {
  return outline;
}
