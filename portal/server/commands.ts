import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { CommandInfo, CommandRun, TelegramCommandInfo } from "../shared/types";

/**
 * The only commands the portal can run. Arguments are fixed; nothing typed in the UI reaches a shell.
 * `writesDb` is checked against the Python code: e.g. `credit --send` records that today's credit message was
 * sent and `skills --send` records Claude spend, so both count as writes.
 */
export const COMMANDS: (Omit<CommandInfo, "cli"> & { args: string[] })[] = [
  { id: "check-sources", writesDb: false, label: "Revisar fuentes", args: ["check-sources"], sendsTelegram: false, costs: false,
    description: "Consulta cada fuente en vivo y muestra cuántas ofertas trae (no guarda nada). Tarda 2–4 min." },
  { id: "run-dry", writesDb: true, label: "Búsqueda de prueba", args: ["run", "--dry-run"], sendsTelegram: false, costs: true,
    description: "Corrida completa, pero imprime el resumen en vez de enviarlo. Sí califica con Claude." },
  { id: "run", writesDb: true, label: "Buscar ahora", args: ["run"], sendsTelegram: true, costs: true,
    description: "Corrida real: consulta las fuentes que tocan, califica y envía a Telegram." },
  { id: "test-telegram", writesDb: false, label: "Probar Telegram", args: ["test-telegram"], sendsTelegram: true, costs: false,
    description: "Envía un mensaje de prueba a tu chat." },
  { id: "credit", writesDb: false, label: "Ver crédito", args: ["credit"], sendsTelegram: false, costs: false,
    description: "Gasto de Claude y saldo estimado." },
  { id: "credit-send", writesDb: true, label: "Enviar crédito a Telegram", args: ["credit", "--send"], sendsTelegram: true, costs: false,
    description: "Lo mismo, y lo envía a Telegram (cuenta como el mensaje del día)." },
  { id: "weekly", writesDb: false, label: "Resumen semanal", args: ["weekly"], sendsTelegram: false, costs: false,
    description: "Resumen de los últimos 7 días (el mismo que llega los lunes a las 7:00)." },
  { id: "filter-report", writesDb: false, label: "Revisar el filtro de palabras", args: ["filter-report"],
    sendsTelegram: false, costs: false,
    description: "Títulos que el filtro descartó y quizá te interesan, y exclusiones que chocan con tus palabras." },
  { id: "top", writesDb: false, label: "Top 7 días", args: ["top", "--days", "7"], sendsTelegram: false, costs: false,
    description: "Las mejores ofertas de la última semana." },
  { id: "skills", writesDb: false, label: "Skills (solo números)", args: ["skills", "--no-llm"], sendsTelegram: false, costs: false,
    description: "Skills más pedidas, backend en full stack y lo que falta en tus casi-coincidencias." },
  { id: "skills-send", writesDb: true, label: "Skills + recomendación a Telegram", args: ["skills", "--send"], sendsTelegram: true,
    costs: true, description: "Incluye la recomendación de Claude (~$0.015) y lo envía a Telegram." },
];

export function commandList(): CommandInfo[] {
  return COMMANDS.map(({ args, ...info }) => ({ ...info, cli: `python -m job_radar ${args.join(" ")}` }));
}

/** Read the bot's command menu straight from job_radar/bot.py so the portal never drifts from it. */
export function telegramCommands(repo: string): TelegramCommandInfo[] {
  const source = fs.readFileSync(path.join(repo, "job_radar", "bot.py"), "utf8");
  const block = /COMMANDS = \[([\s\S]*?)\n\]/.exec(source)?.[1] ?? "";
  return [...block.matchAll(/\("([a-z]+)",\s*"([^"]+)"\)/g)].map((m) => ({ command: m[1], description: m[2] }));
}

const MAX_OUTPUT = 200_000;

export class CommandRunner {
  private runs = new Map<string, CommandRun>();

  constructor(private readonly python: string, private readonly repo: string) {}

  get(id: string): CommandRun | undefined {
    return this.runs.get(id);
  }

  list(): CommandRun[] {
    return [...this.runs.values()].sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  }

  busy(): CommandRun | undefined {
    return this.list().find((r) => r.status === "running");
  }

  start(commandId: string): CommandRun {
    const command = COMMANDS.find((c) => c.id === commandId);
    if (!command) throw new Error(`Comando desconocido: ${commandId}`);
    const running = this.busy();
    if (running) throw new Error(`Ya se está ejecutando "${running.cli}". Espera a que termine.`);
    const run: CommandRun = {
      id: randomUUID(), commandId, cli: `python -m job_radar ${command.args.join(" ")}`, status: "running",
      exitCode: null, startedAt: new Date().toISOString(), finishedAt: null, output: "",
    };
    this.runs.set(run.id, run);
    const child = spawn(this.python, ["-m", "job_radar", ...command.args], {
      cwd: this.repo,
      env: { ...process.env, PYTHONUNBUFFERED: "1", NO_COLOR: "1" },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const append = (chunk: Buffer) => {
      run.output = (run.output + chunk.toString("utf8")).slice(-MAX_OUTPUT);
    };
    child.stdout.on("data", append);
    child.stderr.on("data", append);
    child.on("error", (error) => {
      run.output += `\n[portal] no se pudo iniciar: ${error.message}\n`;
      run.status = "failed";
      run.finishedAt = new Date().toISOString();
    });
    child.on("close", (code) => {
      run.exitCode = code;
      run.status = code === 0 ? "ok" : "failed";
      run.finishedAt = new Date().toISOString();
    });
    // Keep only the last 20 runs.
    for (const old of this.list().slice(20)) this.runs.delete(old.id);
    return run;
  }
}

/** Remote (tunnel) visitors, already authenticated by the tunnel's email allowlist, may run any listed command. */
export function runnableRemotely(commandId: string): boolean {
  return COMMANDS.some((c) => c.id === commandId);
}
