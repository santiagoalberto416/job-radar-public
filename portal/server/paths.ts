import path from "node:path";
import { fileURLToPath } from "node:url";

// portal/server/paths.ts -> repo root is two levels up. Overridable for tests.
const here = path.dirname(fileURLToPath(import.meta.url));

export interface Paths {
  repo: string;
  env: string;
  envExample: string;
  config: string;
  profile: string;
  python: string;
  logDir: string;
}

export function defaultPaths(repo = process.env.JOB_RADAR_REPO ?? path.resolve(here, "..", "..")): Paths {
  return {
    repo,
    env: path.join(repo, ".env"),
    envExample: path.join(repo, ".env.example"),
    config: path.join(repo, "config.yaml"),
    profile: path.join(repo, "profile.md"),
    python: path.join(repo, ".venv", "bin", "python"),
    logDir: path.join(process.env.HOME ?? "", "Library", "Logs"),
  };
}
