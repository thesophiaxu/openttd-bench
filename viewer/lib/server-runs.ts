import { promises as fs } from "node:fs";
import path from "node:path";
import type { Trajectory } from "./trace";

export function outputsDir(): string {
  return path.resolve(/* turbopackIgnore: true */ process.env.OUTPUTS_DIR ?? path.join(process.cwd(), "..", "outputs"));
}
export type RunEntry = { path: string; name: string; group: string; mtime: number; size: number; episodes: number };
type Index = { format: string; run: string; trajectories: { file: string; episode_id: string; size: number }[] };

async function checkedPath(rel: string): Promise<string> {
  const root = await fs.realpath(/* turbopackIgnore: true */ outputsDir());
  const full = path.resolve(root, rel);
  const parts = path.relative(root, full).split(path.sep);
  if (parts.length !== 3 || parts[0] === "archived" || parts[1] !== "trajectories" || parts[2] !== "index.json") throw new Error("invalid ATIF run path");
  // Refuse symlink components, including links back inside the output tree.
  let current = root;
  for (const part of parts) {
    current = path.join(current, part);
    if ((await fs.lstat(current)).isSymbolicLink()) throw new Error("symlinks are not run paths");
  }
  if (!full.startsWith(root + path.sep)) throw new Error("invalid run path");
  return full;
}
async function readIndex(file: string): Promise<Index> {
  const index = JSON.parse(await fs.readFile(file, "utf8")) as Index;
  if (index.format !== "openttd-atif-index-v1" || !Array.isArray(index.trajectories)) throw new Error("invalid ATIF index");
  return index;
}
export async function listRuns(): Promise<RunEntry[]> {
  const root = outputsDir();
  const runs: RunEntry[] = [];
  for (const entry of await fs.readdir(/* turbopackIgnore: true */ root, { withFileTypes: true })) {
    if (!entry.isDirectory() || entry.name === "archived" || entry.name.startsWith(".")) continue;
    const rel = path.join(entry.name, "trajectories", "index.json");
    try {
      const file = await checkedPath(rel);
      const index = await readIndex(file);
      const stat = await fs.stat(file);
      runs.push({ path: rel, name: entry.name, group: "", mtime: stat.mtimeMs,
        size: index.trajectories.reduce((n, t) => n + t.size, 0), episodes: new Set(index.trajectories.map(t => t.episode_id)).size });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") console.warn(`Skipping ${rel}: ${error}`);
    }
  }
  return runs.sort((a, b) => b.mtime - a.mtime);
}
function presentation(t: Trajectory): Trajectory {
  // Archival source records remain in the ATIF files, but don't send encrypted
  // blobs/duplicate native logs over the wire just to render normalized steps.
  const extra = { ...t.extra };
  delete extra.source;
  delete extra.prime_source;
  return { ...t, extra, subagent_trajectories: t.subagent_trajectories?.map(presentation) };
}
export async function readRun(rel: string): Promise<string> {
  const file = await checkedPath(rel);
  const index = await readIndex(file);
  const trajectories = [];
  for (const item of index.trajectories) {
    if (!/^[a-f0-9]{64}\.atif\.json$/.test(item.file)) throw new Error("invalid trajectory filename");
    const full = path.join(path.dirname(file), item.file);
    if (!(await fs.lstat(full)).isFile()) throw new Error("invalid trajectory file");
    const t = JSON.parse(await fs.readFile(full, "utf8")) as Trajectory;
    if (!t.schema_version?.startsWith("ATIF-")) throw new Error("not ATIF");
    trajectories.push(presentation(t));
  }
  return JSON.stringify(trajectories);
}
