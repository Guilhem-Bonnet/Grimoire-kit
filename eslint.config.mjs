// Lint minimal du JavaScript de `web/` (cockpit, vitrine, atelier de blueprints).
//
// Seulement les règles qui attrapent un défaut réel ; aucune règle de style.
// Lancé par `make lint-js` et par le job `lint` de ci-sdk.yml, avec un ESLint
// épinglé et installé hors du dépôt : ce fichier n'importe donc aucun paquet
// (pas de `globals`, pas de `@eslint/js`), et vit à la racine parce que `web/`
// part en entier dans la roue Python.
//
// Les globales sont déclarées à la main. Un `no-undef` sur une API du
// navigateur absente de la liste se corrige ici, en l'ajoutant ; sur tout autre
// nom, c'est le défaut que la règle cherche.

const regles = {
  "no-undef": "error",
  "no-unused-vars": "warn",
  "no-dupe-keys": "error",
  "no-unreachable": "error",
  "no-redeclare": "error",
};

const lecture = (noms) => Object.fromEntries(noms.map((nom) => [nom, "readonly"]));

// Les globales d'ECMAScript (Promise, Map, JSON...) viennent d'`ecmaVersion`.
const navigateur = lecture([
  // Fenêtre et document
  "window", "document", "navigator", "location", "history", "console", "performance",
  "localStorage", "sessionStorage", "innerWidth", "innerHeight",
  "addEventListener", "removeEventListener", "dispatchEvent",
  "getComputedStyle", "matchMedia", "alert", "confirm", "prompt",
  // Minuteries et ordonnancement
  "setTimeout", "clearTimeout", "setInterval", "clearInterval",
  "requestAnimationFrame", "cancelAnimationFrame", "queueMicrotask",
  // Réseau et données
  "fetch", "AbortController", "AbortSignal", "EventSource", "URL", "URLSearchParams",
  "Blob", "FileReader", "atob", "btoa",
  // DOM et événements
  "Node", "Element", "HTMLElement", "HTMLDialogElement", "Event", "CustomEvent", "CSS", "Image",
  // Observateurs, notifications, workers
  "MutationObserver", "IntersectionObserver", "ResizeObserver", "PerformanceObserver",
  "Notification", "Worker",
]);

// Posées sur `window` par un script classique, lues par les autres scripts de la
// même page (`blueprint-editor.html` charge les `bp2-*.js` dans cet ordre).
const partagees = lecture([
  "Atelier",      // atelier-nav.js
  "BPEditor",     // bp2-core.js
  "BP2Assist",    // bp2-assist.js
  "BP2Composer",  // bp2-composer.js
  "BP2Cost",      // bp2-cost.js
  "BP2Docs",      // bp2-docs.js
  "BP2Evals",     // bp2-evals.js
  "BP2Lexicon",   // bp2-lexicon.js
  "BP2Team",      // bp2-team.js
]);

const WORKER = "web/workspace/spaces/carte-worker.js";

export default [
  // Modules ES du cockpit (`web/workspace/`, chargés depuis `<script type="module">`).
  {
    files: ["web/**/*.js"],
    ignores: [WORKER],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: navigateur },
    rules: regles,
  },
  // Scripts classiques à la racine de `web/` : pas de module, des globales partagées.
  {
    files: ["web/*.js"],
    languageOptions: { sourceType: "script", globals: partagees },
  },
  // Web Worker de la carte 3D : script classique, sans fenêtre ni document.
  {
    files: [WORKER],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: lecture(["self", "performance", "console", "setTimeout", "clearTimeout"]),
    },
    rules: regles,
  },
  // Scripts Node du teaser (Playwright) : `window` et `document` n'y vivent que
  // dans les fonctions passées à `page.evaluate`.
  {
    files: ["web/teaser/*.mjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: lecture(["process", "console", "window", "document"]),
    },
    rules: regles,
  },
];
