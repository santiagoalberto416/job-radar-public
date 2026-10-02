import path from "node:path";
import { fileURLToPath } from "node:url";

// portal/server/paths.ts -> repo root is two levels up. Overridable for tests.
const here = path.dirname(fileURLToPath(import.meta.url));

export interface Paths {
  /** job-radar code (job_radar/, .venv). */
  repo: string;
  /** Your personal files: .env, config.yaml, profile.md, data/ (JOB_RADAR_HOME in Docker; the repo otherwise). */
  home: string;
  env: string;
  envExample: string;
  config: string;
  profile: string;
  python: string;
  logDir: string;
}

export function defaultPaths(repo = process.env.JOB_RADAR_REPO ?? path.resolve(here, "..", "..")): Paths {
  const home = process.env.JOB_RADAR_HOME || repo;
  return {
    repo,
    home,
    env: path.join(home, ".env"),
    envExample: path.join(repo, ".env.example"),
    config: path.join(home, "config.yaml"),
    profile: path.join(home, "profile.md"),
    python: process.env.PORTAL_PYTHON || path.join(repo, ".venv", "bin", "python"),
    logDir: process.env.JOB_RADAR_LOG_DIR || path.join(process.env.HOME ?? "", "Library", "Logs"),
  };
}
