// Types shared by the Express API (server/) and the React UI (src/).

export interface JobSummary {
  id: number;
  source: string;
  title: string;
  company: string;
  location: string | null;
  url: string;
  posted_at: string | null;
  first_seen_at: string;
  prefilter_passed: number | null;
  prefilter_reason: string | null;
  score: number | null;
  fits_location: number | null;
  seniority: string | null;
  reason: string | null;
  salary: string | null;
  scored_at: string | null;
  notified_at: string | null;
  last_score_error: string | null;
}

export interface JobDetail extends JobSummary {
  description: string | null;
  red_flags: string[];
  score_model: string | null;
  score_attempts: number;
}

export type JobStatus = "all" | "relevant" | "matches" | "sent" | "waiting" | "scored" | "unscored" | "rejected";

export interface JobsPage {
  jobs: JobSummary[];
  total: number;
  page: number;
  pageSize: number;
  sources: string[];
}

export interface EnvVar {
  key: string;
  set: boolean;
  masked: string;
  secret: boolean;
  description: string;
}

export interface CommandInfo {
  id: string;
  label: string;
  cli: string;
  description: string;
  sendsTelegram: boolean;
  costs: boolean;
  /** Writes to data/jobs.db. Only commands that don't can be run through the read-only remote tunnel. */
  writesDb: boolean;
}

export interface TelegramCommandInfo {
  command: string;
  description: string;
}

export interface CommandRun {
  id: string;
  commandId: string;
  cli: string;
  status: "running" | "ok" | "failed";
  exitCode: number | null;
  startedAt: string;
  finishedAt: string | null;
  output: string;
}

export interface AgentStatus {
  label: string;
  loaded: boolean;
  running: boolean;
  pid: number | null;
  lastExitCode: string | null;
}

export interface SourceStatus {
  source: string;
  last_fetch_at: string | null;
  last_ok_at: string | null;
  last_count: number | null;
  last_error: string | null;
}

export interface Overview {
  agents: AgentStatus[];
  sources: SourceStatus[];
  lastRunAt: string | null;
  totals: { jobs: number; scored: number; matches: number; sent: number; waiting: number };
  spend: { today: number; last30Days: number };
  credit: { balance: number | null; asOf: string | null; spentSince: number | null; remaining: number | null };
}
