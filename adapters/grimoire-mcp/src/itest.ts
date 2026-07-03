// Grimoire MCP — test d'intégration (handshake MCP réel via la SDK client).
// Lance : node --experimental-strip-types src/itest.ts  (après npm install)

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

let failures = 0;
function check(label: string, cond: boolean, detail?: unknown): void {
	if (!cond) failures++;
	console.log(`[${cond ? "PASS" : "FAIL"}] ${label}${cond ? "" : `  -> ${JSON.stringify(detail)}`}`);
}

const here = dirname(fileURLToPath(import.meta.url));

const transport = new StdioClientTransport({
	command: "node",
	args: ["--experimental-strip-types", join(here, "server.ts")],
});
const client = new Client({ name: "grimoire-mcp-itest", version: "0.0.0" }, { capabilities: {} });

await client.connect(transport);

const tools = await client.listTools();
const names = tools.tools.map((t) => t.name).sort();
check(
	"8 outils exposés",
	names.length === 8 &&
		names.join(",") ===
			"escalate_questions,list_skills,orchestrate,recall,remember,route,run_skill,validate",
	names,
);

const routed = await client.callTool({
	name: "route",
	arguments: { intent: "je veux faire du test driven development" },
});
const routedText = (routed.content as Array<{ type: string; text: string }>)[0]?.text ?? "{}";
const routedObj = JSON.parse(routedText);
check("route via MCP -> grimoire-tdd", routedObj.target?.id === "grimoire-tdd", routedObj);

const listed = await client.callTool({ name: "list_skills", arguments: { domain: "testing" } });
const listedObj = JSON.parse((listed.content as Array<{ text: string }>)[0].text);
check("list_skills(testing) via MCP non vide", (listedObj.skills?.length ?? 0) > 0, listedObj.skills?.length);

const validated = await client.callTool({
	name: "validate",
	arguments: { output: "C'est terminé, ça marche.", level: "standard" },
});
const valObj = JSON.parse((validated.content as Array<{ text: string }>)[0].text);
check("validate via MCP -> revise (claim sans preuve)", valObj.verdict === "revise", valObj);

const orch = await client.callTool({
	name: "orchestrate",
	arguments: { goal: "écris les tests puis review la sécurité" },
});
const orchObj = JSON.parse((orch.content as Array<{ text: string }>)[0].text);
check("orchestrate via MCP -> 2 étapes", orchObj.plan?.length === 2, orchObj.plan);

await client.close();
console.log(`\n${failures === 0 ? "ALL GREEN" : failures + " FAILURE(S)"} — handshake MCP grimoire-mcp`);
process.exit(failures === 0 ? 0 : 1);
