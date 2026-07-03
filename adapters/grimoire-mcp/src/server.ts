#!/usr/bin/env -S node --experimental-strip-types
// Grimoire MCP server (stdio). Prise UNIVERSELLE du coeur grimoire.
// Expose 8 verbes (contrat v1) comme outils MCP. Surface bornee (anti-bloat).
// Lancer : node --experimental-strip-types src/server.ts  (apres `npm install`)

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import {
	escalateQuestions,
	listSkills,
	orchestrate,
	recall,
	remember,
	route,
	runSkill,
	validate,
} from "./verbs.ts";

const TOOLS = [
	{
		name: "list_skills",
		description:
			"Liste les capacités (skills) grimoire disponibles, avec triggers et domaine. Optionnel: filtrer par domaine.",
		inputSchema: {
			type: "object",
			properties: { domain: { type: "string", description: "Filtre par domaine (ex: quality, testing, orchestration)." } },
		},
	},
	{
		name: "run_skill",
		description: "Retourne les instructions d'une skill grimoire (procédure à exécuter par l'agent hôte).",
		inputSchema: {
			type: "object",
			properties: {
				id: { type: "string", description: "Identifiant de la skill (ex: grimoire-tdd)." },
				input: { type: "object", description: "Entrée optionnelle passée à la skill." },
			},
			required: ["id"],
		},
	},
	{
		name: "route",
		description:
			"Classe une intention et route vers la skill la plus pertinente avec confiance et fallback (auditable).",
		inputSchema: {
			type: "object",
			properties: {
				intent: { type: "string", description: "L'intention/requête en langage naturel." },
				candidates: { type: "array", items: { type: "string" }, description: "Restreindre aux skills listées." },
			},
			required: ["intent"],
		},
	},
	{
		name: "orchestrate",
		description:
			"Décompose un objectif et produit un plan de dispatch SOG (étapes + skills + handoffs + questions ouvertes). L'hôte exécute le plan.",
		inputSchema: {
			type: "object",
			properties: {
				goal: { type: "string", description: "L'objectif global." },
				autonomy: { type: "string", enum: ["L1", "L2", "L3"], description: "Niveau d'autonomie." },
				constraints: { type: "object", description: "Contraintes optionnelles." },
			},
			required: ["goal"],
		},
	},
	{
		name: "validate",
		description:
			"Vérifie une sortie (CVTL/HUP) : score de confiance, incertitudes déclarées, verdict pass/revise/block. Cross-check modèle opt-in.",
		inputSchema: {
			type: "object",
			properties: {
				output: { description: "La sortie à valider (texte ou objet)." },
				level: { type: "string", enum: ["light", "standard", "critical"], description: "Niveau d'exigence." },
			},
			required: ["output"],
		},
	},
	{
		name: "escalate_questions",
		description: "Consolide un lot de questions (QEC) en un batch unique dédupliqué, à présenter par l'hôte.",
		inputSchema: {
			type: "object",
			properties: {
				questions: {
					type: "array",
					items: {
						type: "object",
						properties: {
							id: { type: "string" },
							prompt: { type: "string" },
							options: { type: "array", items: { type: "string" } },
						},
						required: ["prompt"],
					},
				},
			},
			required: ["questions"],
		},
	},
	{
		name: "remember",
		description: "Persiste un fait/apprentissage dans la mémoire opérationnelle (Memory OS lexical).",
		inputSchema: {
			type: "object",
			properties: {
				fact: { type: "string", description: "Le fait à mémoriser." },
				type: { type: "string", description: "Type (note, feedback, decision...)." },
				tags: { type: "array", items: { type: "string" } },
			},
			required: ["fact"],
		},
	},
	{
		name: "recall",
		description: "Interroge la mémoire opérationnelle (recherche lexicale).",
		inputSchema: {
			type: "object",
			properties: {
				query: { type: "string", description: "La requête." },
				k: { type: "number", description: "Nombre max de résultats (défaut 5)." },
			},
			required: ["query"],
		},
	},
];

async function dispatch(name: string, args: Record<string, unknown>): Promise<unknown> {
	switch (name) {
		case "list_skills":
			return listSkills(args as { domain?: string });
		case "run_skill":
			return runSkill(args as { id: string; input?: unknown });
		case "route":
			return route(args as { intent: string; candidates?: string[] });
		case "orchestrate":
			return orchestrate(args as { goal: string; autonomy?: "L1" | "L2" | "L3" });
		case "validate":
			return await validate(args as { output: unknown; level?: "light" | "standard" | "critical" });
		case "escalate_questions":
			return escalateQuestions(args as { questions: Array<{ id?: string; prompt: string; options?: string[] }> });
		case "remember":
			return remember(args as { fact: string; type?: string; tags?: string[] });
		case "recall":
			return recall(args as { query: string; k?: number });
		default:
			throw new Error(`outil inconnu: ${name}`);
	}
}

async function main(): Promise<void> {
	const server = new Server({ name: "grimoire-mcp", version: "0.2.0" }, { capabilities: { tools: {} } });

	server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

	server.setRequestHandler(CallToolRequestSchema, async (req) => {
		const { name, arguments: args } = req.params;
		if (process.env.GRIMOIRE_MCP_DEBUG) console.error(`[grimoire-mcp] call name=${name} args=${JSON.stringify(args)}`);
		try {
			const result = await dispatch(name, (args ?? {}) as Record<string, unknown>);
			return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
		} catch (err) {
			return {
				isError: true,
				content: [{ type: "text", text: `Erreur grimoire-mcp: ${err instanceof Error ? err.message : String(err)}` }],
			};
		}
	});

	await server.connect(new StdioServerTransport());
	console.error("grimoire-mcp v0.2.0 — 8 verbes — prêt");
}

main().catch((err) => {
	console.error("grimoire-mcp fatal:", err);
	process.exit(1);
});
