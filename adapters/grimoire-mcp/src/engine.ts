// Grimoire MCP — moteur de cross-validation (CVTL) via pi-ai.
// Opt-in : GRIMOIRE_MCP_ENGINE=pi-ai (avec clef provider) ou =faux (tests).
// Degradation gracieuse : toute erreur -> { available:false }, validate reste deterministe.

import { completeSimple, registerBuiltInApiProviders, registerFauxProvider } from "@earendil-works/pi-ai/compat";
import { fauxAssistantMessage, parseJsonWithRepair } from "@earendil-works/pi-ai";

export interface EngineResult {
	available: boolean;
	unsupported_claims: string[];
	confidence?: number;
	note: string;
}

const JUDGE_SYSTEM =
	"Tu es un validateur CVTL pour un agent de code. On te donne une sortie d'agent. " +
	"Réponds UNIQUEMENT en JSON, sans prose: " +
	'{"unsupported_claims": string[], "confidence": number}. ' +
	"unsupported_claims = affirmations non étayées par une preuve (test, diff, lint). " +
	"confidence = ta confiance globale dans la sortie, entre 0 et 1.";

// Provider faux (tests) — injecte une reponse canned.
let fauxHandle: ReturnType<typeof registerFauxProvider> | undefined;

export function configureFauxForTest(responseJson: string): void {
	fauxHandle = registerFauxProvider({ models: [{ id: "grimoire-judge" }] });
	fauxHandle.setResponses([fauxAssistantMessage(responseJson)]);
}

export function resetFauxForTest(): void {
	fauxHandle?.unregister?.();
	fauxHandle = undefined;
}

async function resolveModel(mode: string): Promise<unknown> {
	if (mode === "faux") {
		if (!fauxHandle) configureFauxForTest('{"unsupported_claims":[],"confidence":0.9}');
		return fauxHandle?.getModel();
	}
	// mode pi-ai : modele reel (best-effort, requiert une clef provider en env).
	registerBuiltInApiProviders();
	const provider = process.env.GRIMOIRE_MCP_PROVIDER ?? "anthropic";
	const modelId = process.env.GRIMOIRE_MCP_MODEL ?? "claude-haiku-4-5";
	const all = await import("@earendil-works/pi-ai/providers/all");
	return (all as { getBuiltinModel: (p: string, m: string) => unknown }).getBuiltinModel(provider, modelId);
}

export async function crossCheck(text: string): Promise<EngineResult> {
	const mode = process.env.GRIMOIRE_MCP_ENGINE;
	if (mode !== "pi-ai" && mode !== "faux") {
		return { available: false, unsupported_claims: [], note: "moteur désactivé (GRIMOIRE_MCP_ENGINE absent)" };
	}
	try {
		// biome-ignore lint/suspicious/noExplicitAny: frontiere dynamique pi-ai
		const model = (await resolveModel(mode)) as any;
		if (!model) return { available: false, unsupported_claims: [], note: "modèle introuvable" };
		const msg = await completeSimple(model, {
			systemPrompt: JUDGE_SYSTEM,
			messages: [{ role: "user", content: [{ type: "text", text }] }],
		});
		// biome-ignore lint/suspicious/noExplicitAny: contenu assistant pi-ai
		const out = (msg.content as any[])
			.filter((c) => c.type === "text")
			.map((c) => c.text)
			.join("");
		let parsed: { unsupported_claims?: unknown; confidence?: unknown } = {};
		try {
			parsed = parseJsonWithRepair(out) as typeof parsed;
		} catch {
			parsed = {};
		}
		return {
			available: true,
			unsupported_claims: Array.isArray(parsed.unsupported_claims)
				? (parsed.unsupported_claims as string[])
				: [],
			confidence: typeof parsed.confidence === "number" ? parsed.confidence : undefined,
			note: `moteur=${mode}`,
		};
	} catch (err) {
		return {
			available: false,
			unsupported_claims: [],
			note: `erreur moteur: ${err instanceof Error ? err.message : String(err)}`,
		};
	}
}
