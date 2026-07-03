// Grimoire MCP — Memory OS (backend lexical JSONL, sans DB vectorielle).
// Aligne sur la direction "memoire lexicale" du projet. Verbes recall / remember.

import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { findRepoRoot, tokenize } from "./core.ts";

export interface MemoryRecord {
	id: string;
	fact: string;
	type: string;
	tags: string[];
	ts: string;
}

export interface MemoryHit extends MemoryRecord {
	score: number;
}

function storePath(): string {
	const env = process.env.GRIMOIRE_MCP_MEMORY;
	if (env) return env;
	return join(findRepoRoot(), "_grimoire-runtime-output", "grimoire-mcp", "memory.jsonl");
}

function readAll(): MemoryRecord[] {
	const path = storePath();
	if (!existsSync(path)) return [];
	const out: MemoryRecord[] = [];
	for (const line of readFileSync(path, "utf8").split("\n")) {
		const trimmed = line.trim();
		if (!trimmed) continue;
		try {
			out.push(JSON.parse(trimmed) as MemoryRecord);
		} catch {
			// ligne corrompue ignoree
		}
	}
	return out;
}

export function remember(input: { fact: string; type?: string; tags?: string[] }): { id: string } {
	if (!input.fact || !input.fact.trim()) throw new Error("remember: 'fact' requis");
	const rec: MemoryRecord = {
		id: `m${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`,
		fact: input.fact.trim(),
		type: input.type ?? "note",
		tags: input.tags ?? [],
		ts: new Date().toISOString(),
	};
	const path = storePath();
	mkdirSync(dirname(path), { recursive: true });
	appendFileSync(path, `${JSON.stringify(rec)}\n`, "utf8");
	return { id: rec.id };
}

export function recall(input: { query: string; k?: number }): { hits: MemoryHit[] } {
	const k = input.k ?? 5;
	const qTokens = tokenize(input.query);
	const hits = readAll()
		.map((rec) => {
			const hay = `${rec.fact} ${rec.tags.join(" ")} ${rec.type}`.toLowerCase();
			let score = 0;
			for (const tok of qTokens) if (hay.includes(tok)) score += 1;
			return { ...rec, score };
		})
		.filter((h) => h.score > 0)
		.sort((a, b) => b.score - a.score)
		.slice(0, k);
	return { hits };
}
