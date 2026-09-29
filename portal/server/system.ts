import { execFile } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import type { AgentStatus } from "../shared/types";

const run = promisify(execFile);

// launchd labels are <prefix>.search / <prefix>.bot (see scripts/install_mac.sh).
const PREFIX = process.env.JOB_RADAR_LABEL_PREFIX || "com.jobradar";
export const AGENTS = {
  search: `${PREFIX}.search`,
  bot: `${PREFIX}.bot`,
} as const;
export type AgentKey = keyof typeof AGENTS;

export const LOGS = {
  search: "job-radar.log",
  bot: "job-radar-bot.log",
} as const;
export type LogKey = keyof typeof LOGS;

const domain = () => `gui/${process.getuid?.() ?? 501}`;

export function parseLaunchctl(label: string, output: string | null): AgentStatus {
  if (output === null) return { label, loaded: false, running: false, pid: null, lastExitCode: null };
  const state = /^\s*state = (\S+)/m.exec(output)?.[1];
  const pid = /^\s*pid = (\d+)/m.exec(output)?.[1];
  const exit = /^\s*last exit code = (.+)$/m.exec(output)?.[1]?.trim() ?? null;
  return { label, loaded: true, running: state === "running", pid: pid ? Number(pid) : null, lastExitCode: exit };
}

export async function agentStatus(key: AgentKey): Promise<AgentStatus> {
  const label = AGENTS[key];
  try {
    const { stdout } = await run("/bin/launchctl", ["print", `${domain()}/${label}`]);
    return parseLaunchctl(label, stdout);
  } catch {
    return parseLaunchctl(label, null);
  }
}

/** search: start a run now (no-op if one is running). bot: restart it (e.g. after changing its code). */
export async function kickstart(key: AgentKey): Promise<void> {
  const args = key === "bot" ? ["kickstart", "-k"] : ["kickstart"];
  await run("/bin/launchctl", [...args, `${domain()}/${AGENTS[key]}`]);
}

export function tailLog(logDir: string, key: LogKey, lines = 200): string {
  const file = path.join(logDir, LOGS[key]);
  if (!fs.existsSync(file)) return "";
  const size = fs.statSync(file).size;
  const fd = fs.openSync(file, "r");
  try {
    const length = Math.min(size, 256 * 1024);
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, size - length);
    return buffer.toString("utf8").split("\n").slice(-lines - 1).join("\n");
  } finally {
    fs.closeSync(fd);
  }
}
