// Grimoire MCP — test du chemin moteur (pi-ai) via le provider FAUX (sans clef API).
// Prouve que la cross-validation CVTL s'intègre et fusionne correctement.
// Lance : node --experimental-strip-types src/engine-test.ts

process.env.GRIMOIRE_MCP_ENGINE = "faux";

const { configureFauxForTest, resetFauxForTest } = await import("./engine.ts");
const { validate } = await import("./verbs.ts");

let failures = 0;
function check(label: string, cond: boolean, detail?: unknown): void {
	if (!cond) failures++;
	console.log(`[${cond ? "PASS" : "FAIL"}] ${label}${cond ? "" : `  -> ${JSON.stringify(detail)}`}`);
}

// Cas A — le juge signale une affirmation non étayée.
resetFauxForTest();
configureFauxForTest('{"unsupported_claims":["la fonction parse() n\'est pas testée"],"confidence":0.3}');
const a = await validate({ output: "Le parseur est complet et robuste.", level: "critical" });
check("moteur faux -> cross_checked:true", a.cross_checked === true, a);
check("CVTL injecte l'affirmation non étayée", a.uncertainty.some((u) => u.includes("CVTL")), a.uncertainty);
check("trust abaissé -> verdict revise", a.verdict === "revise", a);

// Cas B — le juge confirme une bonne sortie.
resetFauxForTest();
configureFauxForTest('{"unsupported_claims":[],"confidence":0.95}');
const b = await validate({ output: "Corrigé : 12 tests passent (pytest), diff sur auth.ts.", level: "critical" });
check("moteur faux (bon) -> cross_checked:true", b.cross_checked === true, b);
check("sortie étayée + juge confiant -> pass", b.verdict === "pass", b);

resetFauxForTest();
console.log(`\n${failures === 0 ? "ALL GREEN" : failures + " FAILURE(S)"} — moteur pi-ai (chemin faux)`);
process.exit(failures === 0 ? 0 : 1);
