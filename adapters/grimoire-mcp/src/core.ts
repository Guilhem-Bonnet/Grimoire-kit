// Grimoire MCP — core loader (dependency-free).
// Lit le coeur grimoire (skills + manifeste) depuis le depot. Aucune dependance
// externe : utilisable par le smoke test sans `npm install`.

import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export interface SkillDescriptor {
	id: string;
	name: string;
	description: string;
	domain: string;
	path: string;
	model_invocable: boolean;
}

/** Remonte l'arborescence jusqu'a trouver la racine du depot (presence de .github/skills). */
export function findRepoRoot(start?: string): string {
	let dir = start ?? dirname(fileURLToPath(import.meta.url));
	for (let i = 0; i < 12; i++) {
		if (existsSync(join(dir, ".github", "skills"))) return dir;
		const parent = dirname(dir);
		if (parent === dir) break;
		dir = parent;
	}
	// repli : cwd
	return process.cwd();
}

/** Extrait un champ scalaire d'un frontmatter YAML simple (name/description). */
function frontmatterField(frontmatter: string, field: string): string | undefined {
	const re = new RegExp(`^${field}:\\s*(.+)$`, "m");
	const m = frontmatter.match(re);
	if (!m) return undefined;
	return m[1].trim().replace(/^["']/, "").replace(/["']$/, "");
}

/** Charge la carte id -> domain depuis le manifeste (regex, sans lib YAML). */
function loadDomainMap(repoRoot: string): Map<string, string> {
	const map = new Map<string, string>();
	const manifestPath = join(repoRoot, "grimoire-kit", "framework", "grimoire-core.manifest.yaml");
	if (!existsSync(manifestPath)) return map;
	const text = readFileSync(manifestPath, "utf8");
	const re = /\bid:\s*([a-z0-9-]+)\s*,\s*domain:\s*([a-z-]+)/g;
	let m: RegExpExecArray | null;
	// biome-ignore lint: assignation en condition volontaire
	while ((m = re.exec(text)) !== null) map.set(m[1], m[2]);
	return map;
}

/** Charge tous les SKILL.md du depot en descripteurs neutres. */
export function loadSkills(repoRoot?: string): SkillDescriptor[] {
	const root = repoRoot ?? findRepoRoot();
	const skillsDir = join(root, ".github", "skills");
	const domains = loadDomainMap(root);
	const out: SkillDescriptor[] = [];
	if (!existsSync(skillsDir)) return out;

	for (const entry of readdirSync(skillsDir)) {
		const skillFile = join(skillsDir, entry, "SKILL.md");
		if (!existsSync(skillFile) || !statSync(skillFile).isFile()) continue;
		const txt = readFileSync(skillFile, "utf8");
		const fm = txt.match(/^---\s*\n([\s\S]*?)\n---/);
		const frontmatter = fm ? fm[1] : "";
		const name = frontmatterField(frontmatter, "name") ?? entry;
		const description = frontmatterField(frontmatter, "description") ?? "";
		const disabled = /^disable-model-invocation:\s*true\s*$/m.test(frontmatter);
		out.push({
			id: entry,
			name,
			description,
			domain: domains.get(entry) ?? "uncategorized",
			path: resolve(skillFile),
			model_invocable: !disabled,
		});
	}
	out.sort((a, b) => a.id.localeCompare(b.id));
	return out;
}

/** Lit le corps (hors frontmatter) d'un SKILL.md pour run_skill. */
export function readSkillBody(skillPath: string): string {
	const txt = readFileSync(skillPath, "utf8");
	const m = txt.match(/^---\s*\n[\s\S]*?\n---\s*\n([\s\S]*)$/);
	return (m ? m[1] : txt).trim();
}

// --- Tokenisation lexicale partagee (route, recall) ---
const STOPWORDS = new Set([
	"the", "and", "for", "with", "que", "qui", "les", "des", "une", "pour", "dans",
	"sur", "par", "est", "are", "this", "that", "mon", "ton", "son", "nos", "vos",
	"avec", "sans", "comment", "what", "how", "can", "you", "moi", "ai", "de", "du", "la", "le",
]);

export function tokenize(text: string | undefined | null): string[] {
	if (!text) return [];
	return (String(text).toLowerCase().match(/[a-zà-ÿ0-9]+/g) ?? []).filter(
		(t) => t.length >= 3 && !STOPWORDS.has(t),
	);
}
