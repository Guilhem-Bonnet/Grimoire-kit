// Grimoire MCP — smoke test (sans dependance, sans transport MCP).
// Lance : node --experimental-strip-types src/smoke.ts
// Valide le coeur des 8 verbes contre le depot reel.

import { tmpdir } from "node:os";
import { join } from "node:path";
import { rmSync } from "node:fs";

// Memoire isolee + moteur desactive pour les checks deterministes.
const MEM = join(tmpdir(), `grimoire-mcp-smoke-${Date.now()}.jsonl`);
process.env.GRIMOIRE_MCP_MEMORY = MEM;
delete process.env.GRIMOIRE_MCP_ENGINE;

const { listSkills, runSkill, route, validate, orchestrate, escalateQuestions, recall, remember } = await import(
	"./verbs.ts"
);

let failures = 0;
function check(label: string, cond: boolean, detail?: unknown): void {
	if (!cond) failures++;
	console.log(`[${cond ? "PASS" : "FAIL"}] ${label}${cond ? "" : `  -> ${JSON.stringify(detail)}`}`);
}

// --- list_skills ---
const all = listSkills();
check("list_skills retourne les 44 skills", all.skills.length === 44, all.skills.length);
check("chaque skill a id+description+domaine", all.skills.every((s) => s.id && s.description && s.domain));
check("filtre par domaine 'quality' non vide", listSkills({ domain: "quality" }).skills.length > 0);

// --- run_skill ---
check("run_skill(grimoire-tdd) ok", (runSkill({ id: "grimoire-tdd" }).result.instructions?.length ?? 0) > 50);
check("run_skill(inconnu) -> ok:false", runSkill({ id: "skill-inexistante" }).ok === false);

// --- route ---
check("route TDD -> grimoire-tdd", route({ intent: "test driven development sur ce module" }).target.id === "grimoire-tdd");
check(
	"route perf -> grimoire-performance-profiling",
	route({ intent: "profiling et optimisation de la performance" }).target.id === "grimoire-performance-profiling",
);
check("route sans signal -> fallback intent-routing", route({ intent: "xyzzy plugh qwerty" }).target.id === "grimoire-intent-routing");
// Robustesse : args vides/undefined (cas réel observé via OpenCode) ne doit pas crasher.
check(
	"route(args vides) -> fallback sans crash",
	(route({} as { intent: string }).target.id === "grimoire-intent-routing"),
);

// --- validate (moteur desactive => deterministe) ---
const v1 = await validate({ output: "C'est terminé, tout fonctionne.", level: "standard" });
check("validate claim sans preuve -> revise", v1.verdict === "revise", v1);
check("validate sans moteur -> cross_checked:false", v1.cross_checked === false, v1);
const v2 = await validate({ output: "Corrigé : les 12 tests passent (pytest), voir diff sur auth.ts." });
check("validate claim avec preuve -> pass", v2.verdict === "pass", v2);
check("validate vide -> block", (await validate({ output: "", level: "light" })).verdict === "block");

// --- orchestrate ---
const orch = orchestrate({ goal: "écris les tests puis review la sécurité" });
check("orchestrate -> 2 étapes", orch.plan.length === 2, orch.plan.map((s) => s.target.id));
check("orchestrate -> 1 handoff", orch.handoffs.length === 1, orch.handoffs);

// --- escalate_questions (dedup) ---
const esc = escalateQuestions({
	questions: [{ prompt: "Quelle DB ?" }, { prompt: "Quelle DB ?" }, { prompt: "Quel framework ?", options: ["a", "b"] }],
});
check("escalate dédup -> 2 questions", esc.batch.length === 2, esc.batch);
check("escalate consolidated non vide", esc.consolidated_prompt.includes("Quel framework"));

// --- memory: remember + recall ---
const r = remember({ fact: "Le projet utilise un backend mémoire lexical sans DB vectorielle.", type: "decision", tags: ["memory"] });
check("remember -> id", typeof r.id === "string" && r.id.length > 0);
remember({ fact: "RTK compresse les sorties verboses des commandes shell.", type: "note", tags: ["rtk"] });
const rec = recall({ query: "backend mémoire lexical" });
check("recall trouve le fait pertinent", rec.hits.length >= 1 && rec.hits[0].fact.includes("lexical"), rec.hits[0]);
const rec0 = recall({ query: "zzz introuvable kkk" });
check("recall sans match -> vide", rec0.hits.length === 0);

rmSync(MEM, { force: true });
console.log(`\n${failures === 0 ? "ALL GREEN" : failures + " FAILURE(S)"} — coeur grimoire-mcp (8 verbes)`);
process.exit(failures === 0 ? 0 : 1);
