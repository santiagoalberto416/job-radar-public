import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import YAML from "yaml";
import type { JobDetail, JobStatus, JobsPage, JobSummary, Overview, SourceStatus } from "../shared/types";

const SUMMARY_COLUMNS = `id, source, title, company, location, url, posted_at, first_seen_at, prefilter_passed,
  prefilter_reason, score, fits_location, seniority, reason, salary, scored_at, notified_at, last_score_error`;

export interface JobsQuery {
  q?: string;
  status?: JobStatus;
  source?: string;
  minScore?: number;
  days?: number;
  sort?: "score" | "recent" | "posted";
  page?: number;
  pageSize?: number;
}

interface ConfigBits {
  dbPath: string;
  minScore: number;
  requireFit: boolean;
  credit: { balance: number | null; asOf: string | null };
}

export function readConfigBits(repo: string, configFile: string): ConfigBits {
  const data = YAML.parse(fs.readFileSync(configFile, "utf8")) ?? {};
  const credit = data.credit ?? {};
  return {
    dbPath: path.resolve(repo, data.db_path ?? "data/jobs.db"),
    minScore: Number(data.min_score ?? 70),
    requireFit: data.require_location_fit !== false,
    credit: {
      balance: credit.balance_usd === null || credit.balance_usd === undefined ? null : Number(credit.balance_usd),
      asOf: credit.as_of ? String(credit.as_of) : null,
    },
  };
}

/** Local "YYYY-MM-DD[ HH:MM]" -> UTC ISO string, as job-radar interprets credit.as_of. */
export function localToIso(value: string): string {
  const [date, time = "00:00"] = value.trim().split(/[ T]/);
  return new Date(`${date}T${time}:00`).toISOString();
}

export class JobsDb {
  private db: DatabaseSync | null = null;

  constructor(readonly file: string) {}

  private conn(): DatabaseSync | null {
    if (this.db) return this.db;
    if (!fs.existsSync(this.file)) return null;
    this.db = new DatabaseSync(this.file);
    this.db.exec("PRAGMA busy_timeout = 5000");
    return this.db;
  }

  close(): void {
    this.db?.close();
    this.db = null;
  }

  listJobs(query: JobsQuery, minScore: number, requireFit: boolean): JobsPage {
    const page = Math.max(1, Math.floor(query.page ?? 1));
    const pageSize = Math.min(200, Math.max(1, Math.floor(query.pageSize ?? 50)));
    const db = this.conn();
    if (!db) return { jobs: [], total: 0, page, pageSize, sources: [] };

    const where: string[] = [];
    const params: Record<string, string | number> = {};
    const match = `score >= :min ${requireFit ? "AND fits_location = 1" : ""}`;
    switch (query.status ?? "all") {
      case "relevant": where.push("prefilter_passed = 1"); break;
      case "matches": where.push(match); params.min = minScore; break;
      case "waiting": where.push(`${match} AND notified_at IS NULL`); params.min = minScore; break;
      case "sent": where.push("notified_at IS NOT NULL"); break;
      case "scored": where.push("score IS NOT NULL"); break;
      case "unscored": where.push("prefilter_passed = 1 AND score IS NULL"); break;
      case "rejected": where.push("prefilter_passed = 0"); break;
    }
    if (query.q?.trim()) {
      where.push("(title LIKE :q OR company LIKE :q OR location LIKE :q)");
      params.q = `%${query.q.trim()}%`;
    }
    if (query.source) {
      where.push("source = :source");
      params.source = query.source;
    }
    if (query.minScore !== undefined && Number.isFinite(query.minScore)) {
      where.push("score >= :minScore");
      params.minScore = query.minScore;
    }
    if (query.days && query.days > 0) {
      where.push("julianday(first_seen_at) >= julianday(:since)");
      params.since = new Date(Date.now() - query.days * 86_400_000).toISOString();
    }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
    const order = {
      score: "score IS NULL, score DESC, first_seen_at DESC",
      recent: "first_seen_at DESC, id DESC",
      posted: "posted_at IS NULL, posted_at DESC, id DESC",
    }[query.sort ?? "score"];

    const total = Number((db.prepare(`SELECT COUNT(*) AS n FROM jobs ${whereSql}`).get(params) as { n: number }).n);
    const jobs = db
      .prepare(`SELECT ${SUMMARY_COLUMNS} FROM jobs ${whereSql} ORDER BY ${order} LIMIT :limit OFFSET :offset`)
      .all({ ...params, limit: pageSize, offset: (page - 1) * pageSize }) as unknown as JobSummary[];
    const sources = (db.prepare("SELECT DISTINCT source FROM jobs ORDER BY source").all() as { source: string }[]).map(
      (r) => r.source,
    );
    return { jobs: jobs.map((j) => ({ ...j })), total, page, pageSize, sources };
  }

  getMeta(key: string): string | null {
    const row = this.conn()?.prepare("SELECT value FROM meta WHERE key = ?").get(key) as { value: string } | undefined;
    return row?.value ?? null;
  }

  getJob(id: number): JobDetail | null {
    const db = this.conn();
    const row = db?.prepare("SELECT * FROM jobs WHERE id = ?").get(id) as Record<string, unknown> | undefined;
    if (!row) return null;
    let flags: string[] = [];
    try {
      flags = JSON.parse(String(row.red_flags ?? "[]"));
    } catch {
      flags = [];
    }
    return { ...(row as unknown as JobDetail), red_flags: flags };
  }

  overview(bits: ConfigBits): Omit<Overview, "agents"> {
    const empty = {
      sources: [], lastRunAt: null,
      totals: { jobs: 0, scored: 0, matches: 0, sent: 0, waiting: 0 },
      spend: { today: 0, last30Days: 0 },
      credit: { ...bits.credit, spentSince: null, remaining: null },
    };
    const db = this.conn();
    if (!db) return empty;
    const sources = db.prepare("SELECT * FROM source_runs ORDER BY source").all() as unknown as SourceStatus[];
    const meta = db.prepare("SELECT value FROM meta WHERE key = 'last_run_at'").get() as { value: string } | undefined;
    const fit = bits.requireFit ? "AND fits_location = 1" : "";
    const totals = db.prepare(`SELECT
        COUNT(*) AS jobs,
        SUM(score IS NOT NULL) AS scored,
        SUM(score >= :min ${fit}) AS matches,
        SUM(notified_at IS NOT NULL) AS sent,
        SUM(score >= :min ${fit} AND notified_at IS NULL) AS waiting
      FROM jobs`).get({ min: bits.minScore }) as Record<string, number | null>;
    const spendSince = (iso: string) =>
      Number((db.prepare("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_spend WHERE julianday(at) >= julianday(?)")
        .get(iso) as { s: number }).s);
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const spentSince = bits.credit.asOf && bits.credit.balance !== null ? spendSince(localToIso(bits.credit.asOf)) : null;
    return {
      sources: sources.map((s) => ({ ...s })),
      lastRunAt: meta?.value ?? null,
      totals: {
        jobs: Number(totals.jobs ?? 0), scored: Number(totals.scored ?? 0), matches: Number(totals.matches ?? 0),
        sent: Number(totals.sent ?? 0), waiting: Number(totals.waiting ?? 0),
      },
      spend: { today: spendSince(midnight.toISOString()), last30Days: spendSince(new Date(Date.now() - 30 * 86_400_000).toISOString()) },
      credit: {
        ...bits.credit,
        spentSince,
        remaining: spentSince === null || bits.credit.balance === null ? null : bits.credit.balance - spentSince,
      },
    };
  }
}
