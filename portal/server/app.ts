import express, { type NextFunction, type Request, type Response } from "express";
import fs from "node:fs";
import path from "node:path";
import type { JobStatus } from "../shared/types";
import { CommandRunner, commandList, runnableRemotely, telegramCommands } from "./commands";
import { readSettings, readText, updateSettings, writeConfigText, writeProfile } from "./config";
import { JobsDb, readConfigBits } from "./db";
import { listEnv, setEnv, validateEntry } from "./env";
import type { Paths } from "./paths";
import { localOnly } from "./security";
import {
  AGENTS, LOGS, agentStatus, detectRuntime, dockerAgentStatus, kickstart, tailLog, type AgentKey, type LogKey, type Runtime,
} from "./system";

export interface AppOptions {
  paths: Paths;
  port: number;
  remoteHosts?: string[];
  runtime?: Runtime;
  staticDir?: string;
  runner?: CommandRunner;
}

class BadRequest extends Error {}

const STATUSES: JobStatus[] = ["all", "relevant", "matches", "sent", "waiting", "scored", "unscored", "rejected", "tracking"];
const FEEDBACK = [null, "like", "dislike"];
const APPLICATION = [null, "applied", "interview", "offer", "rejected"];

export function createApp({ paths, port, remoteHosts = [], runtime = detectRuntime(), staticDir, runner }: AppOptions) {
  const app = express();
  const commands = runner ?? new CommandRunner(paths.python, paths.repo);
  let jobsDb: JobsDb | null = null;
  const db = () => {
    const bits = readConfigBits(paths.home, paths.config);
    if (!jobsDb || jobsDb.file !== bits.dbPath) {
      jobsDb?.close();
      jobsDb = new JobsDb(bits.dbPath);
    }
    return { db: jobsDb, bits };
  };
  const route =
    (handler: (req: Request, res: Response) => unknown | Promise<unknown>) =>
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const result = await handler(req, res);
        if (result !== undefined && !res.headersSent) res.json(result);
      } catch (error) {
        next(error);
      }
    };
  const body = (req: Request) => (req.body ?? {}) as Record<string, unknown>;

  app.disable("x-powered-by");
  // Remote access is read-only for .env, config and profile, but may run commands, start a search, restart the bot
  // and track jobs (👍/👎, application status, notes).
  const remoteMayWrite = (req: Request) => {
    if (req.method === "PATCH") return /^\/api\/jobs\/\d+\/tracking$/.test(req.path);  // 👍/👎, status, notes
    if (req.method !== "POST") return false;
    const command = /^\/api\/commands\/([^/]+)\/run$/.exec(req.path);
    if (command) return runnableRemotely(decodeURIComponent(command[1]));
    return /^\/api\/agents\/(search|bot)\/kickstart$/.test(req.path);
  };
  app.use(localOnly({ port, remoteHosts, remoteMayWrite }));
  app.use(express.json({ limit: "256kb" }));

  app.get("/api/health", route(() => ({ ok: true })));
  app.get("/api/session", route((_req, res) => ({ remote: Boolean(res.locals.remote), readOnly: Boolean(res.locals.remote) })));

  // --- jobs -------------------------------------------------------------------------------------
  app.get("/api/jobs", route((req) => {
    const { db: jobs, bits } = db();
    const q = req.query as Record<string, string | undefined>;
    const status = STATUSES.includes(q.status as JobStatus) ? (q.status as JobStatus) : "all";
    const sort = q.sort === "recent" || q.sort === "posted" ? q.sort : "score";
    return jobs.listJobs({
      q: q.q, status, source: q.source || undefined, sort,
      minScore: q.minScore ? Number(q.minScore) : undefined,
      days: q.days ? Number(q.days) : undefined,
      page: q.page ? Number(q.page) : 1,
      pageSize: q.pageSize ? Number(q.pageSize) : 50,
    }, bits.minScore, bits.requireFit);
  }));
  app.get("/api/jobs/:id", route((req, res) => {
    const job = db().db.getJob(Number(req.params.id));
    if (!job) res.status(404).json({ error: "Oferta no encontrada" });
    return job ?? undefined;
  }));

  app.patch("/api/jobs/:id/tracking", route((req, res) => {
    const change: { feedback?: string | null; status?: string | null; notes?: string | null } = {};
    const b = body(req);
    if ("feedback" in b) {
      if (!FEEDBACK.includes(b.feedback as string | null)) throw new BadRequest("feedback inválido");
      change.feedback = b.feedback as string | null;
    }
    if ("status" in b) {
      if (!APPLICATION.includes(b.status as string | null)) throw new BadRequest("estado inválido");
      change.status = b.status as string | null;
    }
    if ("notes" in b) {
      const notes = b.notes === null ? null : String(b.notes);
      if (notes && notes.length > 5000) throw new BadRequest("Las notas son demasiado largas (máx. 5,000).");
      change.notes = notes || null;
    }
    const { db: jobs } = db();
    if (!jobs.updateTracking(Number(req.params.id), change)) {
      res.status(404).json({ error: "Oferta no encontrada" });
      return undefined;
    }
    return jobs.getJob(Number(req.params.id));
  }));

  // --- status -----------------------------------------------------------------------------------
  app.get("/api/overview", route(async () => {
    const { db: jobs, bits } = db();
    const keys = Object.keys(AGENTS) as AgentKey[];
    const agents = runtime === "docker"
      ? keys.map((key) => dockerAgentStatus(key, jobs.getMeta(`heartbeat_${key}`)))
      : await Promise.all(keys.map(agentStatus));
    return { agents, ...jobs.overview(bits) };
  }));
  app.post("/api/agents/:key/kickstart", route(async (req) => {
    const key = String(req.params.key);
    if (!(key in AGENTS)) throw new BadRequest("Agente desconocido");
    if (runtime === "docker") {
      if (key === "bot") throw new BadRequest("En Docker, reinicia el bot con:  docker compose restart bot");
      try {
        commands.start("run");  // same effect as starting the launchd agent: one search now
      } catch (error) {
        throw new BadRequest((error as Error).message);
      }
      return { ok: true };
    }
    await kickstart(key as AgentKey);
    return { ok: true };
  }));
  app.get("/api/logs/:key", route((req) => {
    const key = String(req.params.key);
    if (!(key in LOGS)) throw new BadRequest("Log desconocido");
    return { text: tailLog(paths.logDir, key as LogKey, Math.min(Number(req.query.lines ?? 200), 1000)) };
  }));

  // --- .env ---------------------------------------------------------------------------------------
  app.get("/api/env", route(() => listEnv(paths.env, paths.envExample)));
  app.put("/api/env", route((req) => {
    const key = String(body(req).key ?? "");
    const value = String(body(req).value ?? "");
    const error = validateEntry(key, value);
    if (error) throw new BadRequest(error);
    setEnv(paths.env, key, value);
    return listEnv(paths.env, paths.envExample);
  }));

  // --- config.yaml / profile.md -----------------------------------------------------------------
  app.get("/api/settings", route(() => readSettings(paths.config)));
  app.patch("/api/settings", route((req) => {
    const changes = body(req).changes;
    if (!changes || typeof changes !== "object") throw new BadRequest("Faltan cambios");
    try {
      updateSettings(paths.config, changes as Record<string, unknown>);
    } catch (error) {
      throw new BadRequest((error as Error).message);
    }
    return readSettings(paths.config);
  }));
  app.get("/api/config/raw", route(() => ({ text: readText(paths.config) })));
  app.put("/api/config/raw", route((req) => {
    try {
      writeConfigText(paths.config, String(body(req).text ?? ""));
    } catch (error) {
      throw new BadRequest((error as Error).message);
    }
    return { text: readText(paths.config) };
  }));
  app.get("/api/profile", route(() => ({ text: readText(paths.profile) })));
  app.put("/api/profile", route((req) => {
    try {
      writeProfile(paths.profile, String(body(req).text ?? ""));
    } catch (error) {
      throw new BadRequest((error as Error).message);
    }
    return { text: readText(paths.profile) };
  }));

  // --- commands -----------------------------------------------------------------------------------
  app.get("/api/commands", route(() => ({
    cli: commandList(), telegram: telegramCommands(paths.repo), runs: commands.list(),
  })));
  app.post("/api/commands/:id/run", route((req) => {
    try {
      return commands.start(String(req.params.id));
    } catch (error) {
      throw new BadRequest((error as Error).message);
    }
  }));
  app.get("/api/runs/:id", route((req, res) => {
    const found = commands.get(String(req.params.id));
    if (!found) res.status(404).json({ error: "Ejecución no encontrada" });
    return found ?? undefined;
  }));

  app.use("/api", (_req: Request, res: Response) => {
    res.status(404).json({ error: "Not found" });
  });

  // --- the built React UI ---------------------------------------------------------------------------
  if (staticDir && fs.existsSync(staticDir)) {
    app.use(express.static(staticDir, { index: false }));
    app.get("/{*splat}", (_req: Request, res: Response) => res.sendFile(path.join(staticDir, "index.html")));
  }

  app.use((error: Error, _req: Request, res: Response, _next: NextFunction) => {
    const status = error instanceof BadRequest ? 400 : 500;
    if (status === 500) console.error("[portal]", error);
    res.status(status).json({ error: status === 400 ? error.message : "Error interno; revisa la terminal del portal" });
  });
  return app;
}
