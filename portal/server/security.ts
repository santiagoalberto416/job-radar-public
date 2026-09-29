import type { NextFunction, Request, Response } from "express";

export interface SecurityOptions {
  port: number;
  /** Public hostnames that may reach the portal through a tunnel (e.g. the ngrok domain). Read-only. */
  remoteHosts?: string[];
  /** Remote requests that may use a write method anyway (e.g. running a command that doesn't touch the DB). */
  remoteMayWrite?: (req: Request) => boolean;
}

/**
 * The portal can read and change config, so access is tightly scoped:
 * - the server binds to 127.0.0.1 (see index.ts); remote access only arrives through a tunnel (ngrok) that
 *   enforces Google login for one allowed email before forwarding anything;
 * - Host must be 127.0.0.1/localhost on our port or a configured remote host (blocks DNS rebinding);
 * - requests that carry an Origin must come from one of those hosts (blocks other websites);
 * - state-changing requests must be JSON with the X-Portal header, which cross-site forms can't send;
 * - remote requests are READ-ONLY, except what `remoteMayWrite` allows (running commands that don't write to the
 *   DB). A request is remote if it uses a remote host or carries X-Forwarded-* headers (the tunnel always adds
 *   them and the client can't strip them), so it can't pose as local.
 */
export function localOnly({ port, remoteHosts = [], remoteMayWrite = () => false }: SecurityOptions) {
  const localHosts = new Set([`127.0.0.1:${port}`, `localhost:${port}`]);
  const remote = new Set(remoteHosts.map((h) => h.toLowerCase()));
  const allowedOrigins = new Set([
    ...[...localHosts].map((host) => `http://${host}`),
    ...[...remote].map((host) => `https://${host}`),
  ]);
  return (req: Request, res: Response, next: NextFunction) => {
    const host = String(req.headers.host ?? "").toLowerCase();
    if (!localHosts.has(host) && !remote.has(host)) {
      res.status(403).json({ error: "forbidden host" });
      return;
    }
    const origin = req.headers.origin;
    if (origin && !allowedOrigins.has(origin)) {
      res.status(403).json({ error: "forbidden origin" });
      return;
    }
    const isRemote = remote.has(host) || Boolean(req.headers["x-forwarded-for"] || req.headers["x-forwarded-host"]);
    res.locals.remote = isRemote;
    const mutating = !["GET", "HEAD"].includes(req.method);
    if (mutating && isRemote && !remoteMayWrite(req)) {
      res.status(403).json({ error: "Acceso remoto en modo solo lectura: haz este cambio desde la Mac." });
      return;
    }
    if (mutating && (req.headers["x-portal"] !== "1" || !req.is("application/json"))) {
      res.status(403).json({ error: "missing X-Portal header or JSON body" });
      return;
    }
    next();
  };
}
