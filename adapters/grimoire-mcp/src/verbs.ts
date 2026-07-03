// Grimoire MCP — implementation des verbes neutres (contrat v1).
// Verbes Phase 1 : list_skills, run_skill, route, validate.
// Logique deterministe, sans appel modele (le chemin pi-ai est opt-in, doc).

import { loadSkills, readSkillBody, tokenize, type SkillDescriptor } from "./core.ts";
import { crossCheck } from "./engine.ts";

export { recall, remember } from "./memory.ts";

// ---------------------------------------------------------------------------
// list_skills
// ---------------------------------------------------------------------------
export function listSkills(input: { domain?: string } = {}): { skills: SkillDescriptor[] } {
	let skills = loadSkills();
	if (input.domain) skills = skills.filter((s) => s.domain === input.domain);
	return { skills };
}

// ---------------------------------------------------------------------------
// run_skill — retourne les instructions a executer par l'hote (procedure)
// ---------------------------------------------------------------------------
export function runSkill(input: { id: string; input?: unknown }): {
	ok: boolean;
	result: { instructions?: string; path?: string; error?: string };
} {
	const skill = loadSkills().find((s) => s.id === input.id);
	if (!skill) return { ok: false, result: { error: `skill inconnue: ${input.id}` } };
	return {
		ok: true,
		result: { instructions: readSkillBody(skill.path), path: skill.path },
	};
}

// ---------------------------------------------------------------------------
// route — routing d'intention deterministe et auditable (intent-routing + ARG)
// ---------------------------------------------------------------------------
export interface RouteResult {
	target: { kind: "skill" | "agent" | "profile"; id: string };
	confidence: number;
	rationale: string;
	fallback: { kind: string; id: string } | null;
}

export function route(input: { intent: string; candidates?: string[] }): RouteResult {
	const intentTokens = tokenize(input.intent);
	let skills = loadSkills();
	if (input.candidates?.length) skills = skills.filter((s) => input.candidates?.includes(s.id));

	const scored = skills.map((s) => {
		const nameTokens = new Set(tokenize(s.name));
		const hay = `${s.name} ${s.description} ${s.domain}`.toLowerCase();
		const matched: string[] = [];
		let score = 0;
		for (const tok of intentTokens) {
			if (hay.includes(tok)) {
				score += 1;
				matched.push(tok);
				if (nameTokens.has(tok)) score += 1; // bonus si dans le nom
			}
		}
		return { skill: s, score, matched };
	});
	scored.sort((a, b) => b.score - a.score);

	const top = scored[0];
	const second = scored[1];

	// Aucun signal : repli vers le router d'intention lui-meme.
	if (!top || top.score === 0) {
		return {
			target: { kind: "skill", id: "grimoire-intent-routing" },
			confidence: 0,
			rationale: "Aucun terme de l'intention ne matche une skill ; escalade au routeur d'intention.",
			fallback: { kind: "skill", id: "grimoire-project-explore" },
		};
	}

	const denom = Math.max(1, intentTokens.length * 1.5);
	const confidence = Math.min(1, top.score / denom);
	return {
		target: { kind: "skill", id: top.skill.id },
		confidence: Number(confidence.toFixed(2)),
		rationale: `Termes communs: [${top.matched.join(", ")}] (score ${top.score}, domaine ${top.skill.domain}).`,
		fallback: second && second.score > 0 ? { kind: "skill", id: second.skill.id } : null,
	};
}

// ---------------------------------------------------------------------------
// validate — CVTL/HUP deterministe + score de confiance
// ---------------------------------------------------------------------------
export interface ValidateResult {
	trust_score: number;
	uncertainty: string[];
	cross_checked: boolean;
	verdict: "pass" | "revise" | "block";
	notes: string;
}

// Frontieres accent-aware (le \b natif ne gere pas é, à, ç...).
const L = "a-zà-ÿ0-9";
const CLAIM_RE = new RegExp(
	`(?<![${L}])(done|completed?|fixed|passes|passing|works|fonctionne|terminé|fini|corrigé|réussi|validé)(?![${L}])`,
	"i",
);
const EVIDENCE_RE = new RegExp(
	`(?<![${L}])(tests?|lint|diff|commit|coverage|passed|pytest|exit code|stack trace)(?![${L}])|\\.(py|ts|js)(?![${L}])`,
	"i",
);
const HEDGE_RE = new RegExp(
	`(?<![${L}])(maybe|peut-?être|probably|i think|je pense|might|should work|devrait|sans doute|a priori)(?![${L}])`,
	"gi",
);

export async function validate(input: {
	output: unknown;
	level?: "light" | "standard" | "critical";
}): Promise<ValidateResult> {
	const level = input.level ?? "standard";
	const text =
		typeof input.output === "string" ? input.output : JSON.stringify(input.output ?? "");
	const evidenceField =
		input.output && typeof input.output === "object" && "evidence" in (input.output as object)
			? (input.output as { evidence?: unknown[] }).evidence
			: undefined;

	const isEmpty = text.trim().length === 0;
	const claimsDone = CLAIM_RE.test(text);
	const hasEvidence = (Array.isArray(evidenceField) && evidenceField.length > 0) || EVIDENCE_RE.test(text);
	const hedges = [...new Set((text.match(HEDGE_RE) ?? []).map((h) => h.toLowerCase()))];

	let trust = 0.8;
	const uncertainty: string[] = [];

	if (isEmpty) {
		return {
			trust_score: 0,
			uncertainty: ["sortie vide"],
			cross_checked: false,
			verdict: "block",
			notes: "Sortie vide : rien a valider.",
		};
	}
	if (claimsDone && !hasEvidence) {
		trust -= 0.4;
		uncertainty.push("claim de complétion sans preuve (test/diff/lint)");
	}
	if (hasEvidence) trust += 0.1;
	for (const h of hedges) {
		trust -= 0.1;
		uncertainty.push(`marqueur d'incertitude: « ${h} »`);
	}

	// Cross-validation par modele (CVTL) — opt-in, degradation gracieuse.
	let crossChecked = false;
	let engineNote = "moteur désactivé";
	if (level === "critical" || process.env.GRIMOIRE_MCP_ENGINE) {
		const cc = await crossCheck(text);
		engineNote = cc.note;
		if (cc.available) {
			crossChecked = true;
			for (const claim of cc.unsupported_claims) {
				trust -= 0.2;
				uncertainty.push(`CVTL: affirmation non étayée — ${claim}`);
			}
			if (typeof cc.confidence === "number") trust = (trust + cc.confidence) / 2;
		}
	}

	trust = Math.max(0, Math.min(1, Number(trust.toFixed(2))));
	const threshold = level === "critical" ? 0.7 : level === "standard" ? 0.5 : 0.3;
	const verdict: ValidateResult["verdict"] = trust < threshold ? "revise" : "pass";

	return {
		trust_score: trust,
		uncertainty,
		cross_checked: crossChecked,
		verdict,
		notes: `niveau=${level} seuil=${threshold} preuve=${hasEvidence} claim=${claimsDone} | ${engineNote}`,
	};
}

// ---------------------------------------------------------------------------
// orchestrate — plan de dispatch SOG (le coeur planifie, l'hote execute)
// ---------------------------------------------------------------------------
export interface OrchestrateStep {
	index: number;
	subgoal: string;
	target: RouteResult["target"];
	confidence: number;
}

export function orchestrate(input: {
	goal: string;
	constraints?: Record<string, unknown>;
	autonomy?: "L1" | "L2" | "L3";
}): {
	plan: OrchestrateStep[];
	results: Record<string, unknown>;
	handoffs: Array<{ from: string; to: string }>;
	open_questions: Array<{ id: string; prompt: string }>;
	autonomy: "L1" | "L2" | "L3";
} {
	const autonomy = input.autonomy ?? "L2";
	const parts = input.goal
		.split(/\b(?:puis|ensuite|then|et ensuite)\b|;|(?:,\s+et\s+)/i)
		.map((s) => s.trim())
		.filter((s) => s.length > 2);
	const subgoals = parts.length > 0 ? parts : [input.goal];

	const plan: OrchestrateStep[] = subgoals.map((sg, i) => {
		const r = route({ intent: sg });
		return { index: i, subgoal: sg, target: r.target, confidence: r.confidence };
	});
	const handoffs = plan.slice(1).map((s, i) => ({ from: plan[i].target.id, to: s.target.id }));
	const open_questions = plan
		.filter((s) => s.confidence < 0.34)
		.map((s) => ({ id: `q${s.index}`, prompt: `Confiance faible pour « ${s.subgoal} » — confirmer la skill ?` }));

	return { plan, results: {}, handoffs, open_questions, autonomy };
}

// ---------------------------------------------------------------------------
// escalate_questions — batch QEC (le coeur consolide, l'hote presente)
// ---------------------------------------------------------------------------
export function escalateQuestions(input: {
	questions: Array<{ id?: string; prompt: string; options?: string[] }>;
}): {
	batch: Array<{ id: string; prompt: string; options: string[] }>;
	consolidated_prompt: string;
	awaiting_host: boolean;
	note: string;
} {
	const seen = new Set<string>();
	const batch: Array<{ id: string; prompt: string; options: string[] }> = [];
	(input.questions ?? []).forEach((q, i) => {
		const key = q.prompt.trim().toLowerCase();
		if (!key || seen.has(key)) return;
		seen.add(key);
		batch.push({ id: q.id ?? `q${i + 1}`, prompt: q.prompt.trim(), options: q.options ?? [] });
	});
	const consolidated = batch
		.map((q, i) => `${i + 1}. ${q.prompt}${q.options.length ? ` [${q.options.join(" / ")}]` : ""}`)
		.join("\n");
	return {
		batch,
		consolidated_prompt: consolidated,
		awaiting_host: true,
		note: "QEC : l'hôte présente ce batch unique et collecte les réponses (jamais d'interruption par agent).",
	};
}
