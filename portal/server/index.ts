import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createApp } from "./app";
import { defaultPaths } from "./paths";

const PORT = Number(process.env.PORTAL_PORT ?? 4747);
// Only this machine by default: the portal can read and change secrets. Docker sets PORTAL_BIND=0.0.0.0 INSIDE the
// container and publishes the port on the host's 127.0.0.1 only (see docker-compose.yml).
const HOST = process.env.PORTAL_BIND || "127.0.0.1";
const portalDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const staticDir = path.join(portalDir, "dist");

// Tunnel hostnames allowed in (read-only): PORTAL_REMOTE_HOSTS, or portal/.remote-hosts written by
// scripts/install_tunnel.sh (one per line; the file is gitignored).
function remoteHosts(): string[] {
  const file = path.join(portalDir, ".remote-hosts");
  const raw = process.env.PORTAL_REMOTE_HOSTS ?? (fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "");
  return raw.split(/[\s,]+/).map((h) => h.trim()).filter(Boolean);
}
const REMOTE_HOSTS = remoteHosts();

const paths = defaultPaths();
createApp({ paths, port: PORT, remoteHosts: REMOTE_HOSTS, staticDir }).listen(PORT, HOST, () => {
  console.log(`job-radar portal: http://${HOST}:${PORT}  (repo: ${paths.repo})`);
  if (REMOTE_HOSTS.length) console.log(`remote (read-only) hosts: ${REMOTE_HOSTS.join(", ")}`);
});
