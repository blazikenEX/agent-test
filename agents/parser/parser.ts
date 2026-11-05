/**
 * Parser: deterministic shell around the two LLM passes (extract per file, merge per workspace).
 *
 * Runs for ONE workspace per invocation, ideally in its own NanoClaw container with only that
 * workspace's sanitized folder mounted read-only. The path check below is a second line of defence.
 *
 * Usage:  bun run parser.ts --workspace client-a [--root /data] [--in /data/sanitized]
 *                           [--out /data/entries] [--logs /data/logs] [--stats /data/stats]
 * Deps:   @anthropic-ai/sdk, mailparser, zod
 */
import Anthropic from "@anthropic-ai/sdk";
import { simpleParser } from "mailparser";
import { z } from "zod";
import { promises as fs } from "node:fs";
import path from "node:path";

// ---------------------------------------------------------------- config

const args = parseArgs(process.argv.slice(2));
const WORKSPACE = required(args.workspace, "--workspace");
if (!/^[a-z0-9-]+$/.test(WORKSPACE)) throw new Error(`Invalid workspace name: ${WORKSPACE}`);

const ROOT = path.resolve(args.root ?? "/data");
const IN_DIR = path.resolve(args.in ?? path.join(ROOT, "sanitized"));
const OUT_DIR = path.resolve(args.out ?? path.join(ROOT, "entries"));
const LOG_DIR = path.resolve(args.logs ?? path.join(ROOT, "logs"));
const STATS_DIR = path.resolve(args.stats ?? path.join(ROOT, "stats"));
const PROMPT_DIR = path.resolve(args.prompts ?? path.join(import.meta.dir, "prompts"));

const MODEL = process.env.PARSER_MODEL ?? "claude-sonnet-5-5";
const MAX_CHARS = 60_000; // per extract call; longer files are split on blank lines
const MAX_ENTRIES_PER_DOC = 12;
const SUPPORTED = new Set([".md", ".txt", ".eml", ".json", ".csv"]);

const WS_DIR = path.join(IN_DIR, WORKSPACE);
const LOG_FILE = path.join(LOG_DIR, `${WORKSPACE}.parser.jsonl`); // merged into actions.log.jsonl later
const API_HOST = new URL(process.env.ANTHROPIC_BASE_URL ?? "https://api.anthropic.com").host;
const client = new Anthropic();

const stats = {
  calls: 0,
  tokens_in: 0,
  tokens_out: 0,
  skipped: [] as string[],
  failures: [] as string[],
};

// ---------------------------------------------------------------- schemas

const Draft = z.object({ specific: z.string().min(1), general: z.string().min(1) });
const ExtractOut = z.object({ entries: z.array(Draft).max(MAX_ENTRIES_PER_DOC) });

const MergeOut = z.object({
  groups: z.array(
    z.object({
      merged_from: z.array(z.string()).min(1),
      specific: z.string().min(1),
      general: z.string().min(1),
    }),
  ),
});

// Every input id must appear in exactly one group; checked inside the schema so a bad answer is retried.
const mergeSchemaFor = (ids: string[]) =>
  MergeOut.superRefine((v, ctx) => {
    const seen = new Set<string>();
    for (const g of v.groups)
      for (const id of g.merged_from) {
        if (!ids.includes(id)) ctx.addIssue({ code: "custom", message: `unknown id ${id}` });
        else if (seen.has(id)) ctx.addIssue({ code: "custom", message: `id ${id} appears twice` });
        seen.add(id);
      }
    const missing = ids.filter((id) => !seen.has(id));
    if (missing.length) ctx.addIssue({ code: "custom", message: `missing ids ${missing.join(", ")}` });
  });

/** The contract with the entry classifier. */
export const Entry = z.object({
  entry_id: z.string(),
  workspace: z.string(),
  sources: z.array(z.string()).min(1),
  specific: z.string(),
  general: z.string(),
});
export type Entry = z.infer<typeof Entry>;

const draftJson = {
  type: "object",
  properties: { specific: { type: "string" }, general: { type: "string" } },
  required: ["specific", "general"],
} as const;

const EXTRACT_TOOL: Anthropic.Tool = {
  name: "record_entries",
  description: "Record the knowledge entries extracted from the document.",
  input_schema: {
    type: "object",
    properties: { entries: { type: "array", maxItems: MAX_ENTRIES_PER_DOC, items: draftJson } },
    required: ["entries"],
  },
};

const MERGE_TOOL: Anthropic.Tool = {
  name: "record_merged_entries",
  description: "Record the workspace entries after merging those that describe the same insight.",
  input_schema: {
    type: "object",
    properties: {
      groups: {
        type: "array",
        items: {
          type: "object",
          properties: {
            merged_from: { type: "array", items: { type: "string" } },
            specific: { type: "string" },
            general: { type: "string" },
          },
          required: ["merged_from", "specific", "general"],
        },
      },
    },
    required: ["groups"],
  },
};

// ---------------------------------------------------------------- logged I/O

async function logAction(action: "read" | "write" | "http" | "other", target: string) {
  const line = { ts: new Date().toISOString(), scope: WORKSPACE, actor: "parser", action, target };
  await fs.mkdir(LOG_DIR, { recursive: true });
  await fs.appendFile(LOG_FILE, JSON.stringify(line) + "\n");
}

const rel = (p: string) => {
  const r = path.relative(ROOT, p);
  return r.startsWith("..") ? path.resolve(p) : r; // files outside ROOT (e.g. prompts) logged by full path
};

function assertInWorkspace(p: string) {
  const r = path.resolve(p);
  if (!r.startsWith(WS_DIR + path.sep)) throw new Error(`Isolation violation: ${r}`);
}

async function readWorkspaceFile(p: string) {
  assertInWorkspace(p);
  await logAction("read", rel(p));
  return fs.readFile(p, "utf8");
}

async function readOther(p: string) {
  await logAction("read", rel(p));
  return fs.readFile(p, "utf8");
}

async function writeAtomic(p: string, data: string) {
  await fs.mkdir(path.dirname(p), { recursive: true });
  const tmp = `${p}.tmp`;
  await fs.writeFile(tmp, data);
  await fs.rename(tmp, p);
  await logAction("write", rel(p));
}

async function listFiles(dir: string): Promise<string[]> {
  const names = (await fs.readdir(dir, { recursive: true })) as string[];
  const files: string[] = [];
  for (const n of names) {
    const p = path.join(dir, n);
    if ((await fs.stat(p)).isFile()) files.push(p);
  }
  return files.sort(); // stable order → stable temp ids
}

// ---------------------------------------------------------------- preprocessing

async function toText(file: string): Promise<string | null> {
  const ext = path.extname(file).toLowerCase();
  if (!SUPPORTED.has(ext)) return null;
  const raw = await readWorkspaceFile(file);
  if (ext === ".eml") return renderEmail(raw);
  if (ext === ".json") return renderChat(raw);
  return raw; // .md, .txt, .csv go in as written
}

async function renderEmail(raw: string): Promise<string> {
  const m = await simpleParser(raw);
  const to = Array.isArray(m.to) ? m.to.map((t) => t.text).join(", ") : (m.to?.text ?? "");
  const head = [
    `From: ${m.from?.text ?? ""}`,
    `To: ${to}`,
    `Date: ${m.date?.toISOString() ?? ""}`,
    `Subject: ${m.subject ?? ""}`,
  ].join("\n");
  return `${head}\n\n${m.text ?? ""}`; // forwarded messages stay in the body
}

// Generic chat renderer; adjust the field names once we have seen the actual export format.
function renderChat(raw: string): string {
  let data: any;
  try {
    data = JSON.parse(raw);
  } catch {
    return raw;
  }
  const msgs = Array.isArray(data) ? data : data?.messages;
  if (!Array.isArray(msgs)) return JSON.stringify(data, null, 2);
  return msgs
    .map((m: any) => {
      const who = m.author ?? m.user ?? m.sender ?? m.from ?? "?";
      const name = typeof who === "object" ? (who.name ?? JSON.stringify(who)) : who;
      const when = m.ts ?? m.time ?? m.timestamp ?? "";
      const text = m.text ?? m.message ?? m.content ?? "";
      return `[${when}] ${name}: ${text}`;
    })
    .join("\n");
}

function chunk(text: string): string[] {
  if (text.length <= MAX_CHARS) return [text];
  const parts: string[] = [];
  let cur = "";
  for (const para of text.split(/\n\s*\n/)) {
    if (cur && cur.length + para.length + 2 > MAX_CHARS) {
      parts.push(cur);
      cur = "";
    }
    cur += (cur ? "\n\n" : "") + para;
  }
  if (cur) parts.push(cur);
  return parts; // a single paragraph over MAX_CHARS stays oversized; fine for the MVP
}

// Keep document text from closing our delimiter tags.
const fence = (s: string) => s.replaceAll("</document>", "<\\/document>").replaceAll("</entries>", "<\\/entries>");

// ---------------------------------------------------------------- LLM calls

async function callTool<T>(system: string, user: string, tool: Anthropic.Tool, schema: z.ZodType<T>): Promise<T> {
  let lastErr = "";
  for (let attempt = 1; attempt <= 2; attempt++) {
    const content =
      attempt === 1 ? user : `${user}\n\nYour previous output was invalid: ${lastErr}\nCall ${tool.name} again with valid input.`;
    await logAction("http", API_HOST);
    const res = await client.messages.create({
      model: MODEL,
      max_tokens: 8000,
      temperature: 0,
      system,
      tools: [tool],
      tool_choice: { type: "tool", name: tool.name },
      messages: [{ role: "user", content }],
    });
    stats.calls++;
    stats.tokens_in += res.usage.input_tokens;
    stats.tokens_out += res.usage.output_tokens;

    const block = res.content.find((b) => b.type === "tool_use");
    const parsed = schema.safeParse(block?.type === "tool_use" ? block.input : undefined);
    if (parsed.success) return parsed.data;
    lastErr = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ").slice(0, 500);
  }
  throw new Error(`invalid ${tool.name} output after retry: ${lastErr}`);
}

type DraftEntry = { tid: string; source: string; specific: string; general: string };
type MergedEntry = { sources: string[]; specific: string; general: string };

// Pass 1: one call per file (or per chunk of a long file).
async function extractAll(files: string[], system: string): Promise<DraftEntry[]> {
  const drafts: DraftEntry[] = [];
  for (const file of files) {
    const name = path.relative(WS_DIR, file);
    const text = await toText(file);
    if (text === null) {
      stats.skipped.push(name);
      continue;
    }
    const parts = chunk(text);
    for (let i = 0; i < parts.length; i++) {
      const label = parts.length > 1 ? `${name} (part ${i + 1}/${parts.length})` : name;
      const user = `Workspace: ${WORKSPACE}\nFile: ${label}\n\n<document>\n${fence(parts[i])}\n</document>`;
      try {
        const { entries } = await callTool(system, user, EXTRACT_TOOL, ExtractOut);
        for (const e of entries) drafts.push({ tid: `t${drafts.length + 1}`, source: name, ...e });
      } catch (err) {
        stats.failures.push(`${label}: ${(err as Error).message}`); // file contributes nothing; never guess
      }
    }
  }
  return drafts;
}

// Pass 2: one call for the whole workspace.
async function mergeAll(drafts: DraftEntry[], system: string): Promise<MergedEntry[]> {
  const unmerged = drafts.map((d) => ({ sources: [d.source], specific: d.specific, general: d.general }));
  if (drafts.length < 2) return unmerged;

  const ids = drafts.map((d) => d.tid);
  const payload = JSON.stringify(drafts.map(({ tid, specific, general }) => ({ id: tid, specific, general })), null, 1);
  const user = `Workspace: ${WORKSPACE}\n\n<entries>\n${fence(payload)}\n</entries>`;
  try {
    const { groups } = await callTool(system, user, MERGE_TOOL, mergeSchemaFor(ids));
    const byId = new Map(drafts.map((d) => [d.tid, d]));
    return groups.map((g) => ({
      sources: [...new Set(g.merged_from.map((id) => byId.get(id)!.source))].sort(),
      specific: g.specific,
      general: g.general,
    }));
  } catch (err) {
    // Safe fallback: within-workspace duplicates cannot inflate the counter, which counts workspaces.
    stats.failures.push(`merge: ${(err as Error).message}`);
    return unmerged;
  }
}

// ---------------------------------------------------------------- main

async function main() {
  const t0 = Date.now();
  const extractPrompt = await readOther(path.join(PROMPT_DIR, "parser-extract.md"));
  const mergePrompt = await readOther(path.join(PROMPT_DIR, "parser-merge.md"));

  const files = await listFiles(WS_DIR);
  const drafts = await extractAll(files, extractPrompt);
  const merged = await mergeAll(drafts, mergePrompt);

  const entries: Entry[] = merged.map((m, i) =>
    Entry.parse({
      entry_id: `${WORKSPACE}#${String(i + 1).padStart(3, "0")}`,
      workspace: WORKSPACE,
      ...m,
    }),
  );

  // Overwrite on every run: entries are transient, so a re-run replaces rather than duplicates.
  const jsonl = entries.map((e) => JSON.stringify(e)).join("\n");
  await writeAtomic(path.join(OUT_DIR, `${WORKSPACE}.jsonl`), jsonl ? jsonl + "\n" : "");

  await writeAtomic(
    path.join(STATS_DIR, `parser-${WORKSPACE}.json`),
    JSON.stringify(
      {
        workspace: WORKSPACE,
        model: MODEL,
        duration_s: Math.round((Date.now() - t0) / 1000),
        files: files.length,
        drafts: drafts.length,
        entries: entries.length,
        ...stats,
      },
      null,
      2,
    ),
  );

  if (stats.failures.length) process.exitCode = 2; // partial output: the orchestrator decides what to do
}

// ---------------------------------------------------------------- helpers

function parseArgs(a: string[]): Record<string, string | undefined> {
  const o: Record<string, string | undefined> = {};
  for (let i = 0; i < a.length; i += 2) o[a[i].replace(/^--/, "")] = a[i + 1];
  return o;
}

function required(v: string | undefined, name: string): string {
  if (!v) throw new Error(`Missing ${name}`);
  return v;
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
