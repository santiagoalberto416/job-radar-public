import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { after, before, describe, test } from "node:test";
import { DatabaseSync } from "node:sqlite";
import { createApp } from "../server/app";
import { CommandRunner } from "../server/commands";
import type { Paths } from "../server/paths";

const REMOTE = "radar-test.ngrok-free.app";
const REAL_REPO = path.resolve(import.meta.dirname, "..", "..");
let repo: string;
let port: number;
let server: http.Server;

const CONFIG = `# job-radar config (test)
min_score: 70   # send from this score
require_location_fit: true
notify_window:
  start: "07:00"
  end: "22:00"   # inclusive
db_path: data/jobs.db
credit:
  balance_usd: 4.04
  as_of: "2026-09-01"
  warn_below_usd: 2.0
llm:
  model: claude-sonnet-5
  effort: low
  max_jobs_per_run: 40
sources:
  linkedin:
    enabled: true
    min_interval_hours: 4
`;

function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const s = net.createServer().listen(0, "127.0.0.1", () => {
      const p = (s.address() as net.AddressInfo).port;
      s.close(() => resolve(p));
    });
  });
}

function request(method: string, url: string, opts: { body?: unknown; headers?: Record<string, string> } = {}) {
  return new Promise<{ status: number; json: any; text: string }>((resolve, reject) => {
    const payload = opts.body === undefined ? undefined : JSON.stringify(opts.body);
    const headers: Record<string, string> = {
      host: `127.0.0.1:${port}`,
      ...(payload ? { "content-type": "application/json", "x-portal": "1" } : {}),
      ...opts.headers,
    };
    const req = http.request({ host: "127.0.0.1", port, method, path: url, headers }, (res) => {
      let text = "";
      res.on("data", (c) => (text += c));
      res.on("end", () => {
        let json: any = null;
        try { json = JSON.parse(text); } catch { /* not json */ }
        resolve({ status: res.statusCode ?? 0, json, text });
      });
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function seedDb(file: string) {
  // Use job-radar's real schema so the portal's queries break loudly if it changes.
  const schema = /SCHEMA = """([\s\S]*?)"""/.exec(fs.readFileSync(path.join(REAL_REPO, "job_radar", "db.py"), "utf8"))![1];
  const db = new DatabaseSync(file);
  db.exec(schema);
  const insert = db.prepare(`INSERT INTO jobs (url_key, company_title_key, source, title, company, location, url,
    description, first_seen_at, prefilter_passed, prefilter_reason, score, fits_location, reason, red_flags,
    scored_at, notified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
  const now = new Date().toISOString();
  insert.run("a/1", "acme|a", "greenhouse", "Senior Angular Developer", "Acme", "Remote", "https://a/1", "Angular 18",
    now, 1, "ok", 90, 1, "Muy buen fit", '["pago en MXN"]', now, now);
  insert.run("a/2", "beta|b", "linkedin", "Senior React Engineer", "Beta", "Mexico", "https://a/2", "React",
    now, 1, "ok", 75, 1, "Buen fit", "[]", now, null);
  insert.run("a/3", "gamma|c", "remotive", "Frontend Engineer", "Gamma", "US only", "https://a/3", "",
    now, 1, "ok", 85, 0, "US only", "[]", now, null);
  insert.run("a/4", "delta|d", "indeed_mx", "Senior Backend Engineer", "Delta", "Remote", "https://a/4", "",
    now, 0, "title excludes 'backend'", null, null, null, null, null, null);
  insert.run("a/5", "eps|e", "linkedin", "Staff Frontend Engineer", "Epsilon", "Remote", "https://a/5", "",
    now, 1, "ok", null, null, null, null, null, null);
  db.prepare("INSERT INTO llm_spend (at, model, calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd) VALUES (?, 'm', 1, 0, 0, 0, 0, 0.5)").run(now);
  db.prepare("INSERT INTO source_runs VALUES ('linkedin', ?, ?, 43, NULL)").run(now, now);
  db.close();
}

before(async () => {
  repo = fs.mkdtempSync(path.join(os.tmpdir(), "portal-test-"));
  fs.writeFileSync(path.join(repo, "config.yaml"), CONFIG);
  fs.writeFileSync(path.join(repo, "profile.md"), "# Profile\nSenior FE\n");
  fs.writeFileSync(path.join(repo, ".env.example"), "ANTHROPIC_API_KEY=\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n");
  fs.writeFileSync(path.join(repo, ".env"), "# secrets\nANTHROPIC_API_KEY=sk-ant-secret-value-1234\nTELEGRAM_CHAT_ID=42\n", { mode: 0o600 });
  fs.mkdirSync(path.join(repo, "job_radar"));
  fs.copyFileSync(path.join(REAL_REPO, "job_radar", "bot.py"), path.join(repo, "job_radar", "bot.py"));
  fs.mkdirSync(path.join(repo, "data"));
  seedDb(path.join(repo, "data", "jobs.db"));
  const fakePython = path.join(repo, "fake-python");
  fs.writeFileSync(fakePython, '#!/bin/sh\necho "ran: $@"\n', { mode: 0o755 });
  const paths: Paths = {
    repo, env: path.join(repo, ".env"), envExample: path.join(repo, ".env.example"),
    config: path.join(repo, "config.yaml"), profile: path.join(repo, "profile.md"),
    python: fakePython, logDir: path.join(repo, "logs"),
  };
  port = await freePort();
  server = createApp({ paths, port, remoteHosts: [REMOTE], runner: new CommandRunner(fakePython, repo) })
    .listen(port, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
});

after(() => {
  server.close();
  fs.rmSync(repo, { recursive: true, force: true });
});

describe("security", () => {
  test("rejects foreign Host headers (DNS rebinding)", async () => {
    assert.equal((await request("GET", "/api/health", { headers: { host: `evil.com:${port}` } })).status, 403);
  });
  test("rejects cross-site origins", async () => {
    assert.equal((await request("GET", "/api/env", { headers: { origin: "https://evil.com" } })).status, 403);
  });
  test("rejects writes without the X-Portal header", async () => {
    const res = await request("PUT", "/api/env", { body: { key: "A", value: "b" }, headers: { "x-portal": "" } });
    assert.equal(res.status, 403);
  });
  test("accepts its own origin", async () => {
    const res = await request("GET", "/api/health", { headers: { origin: `http://127.0.0.1:${port}` } });
    assert.deepEqual(res.json, { ok: true });
  });
});

describe("remote access (tunnel)", () => {
  const remote = { host: REMOTE, "x-forwarded-for": "203.0.113.7", "x-forwarded-proto": "https" };
  test("remote host can read, and the session says read-only", async () => {
    assert.equal((await request("GET", "/api/jobs", { headers: remote })).status, 200);
    assert.deepEqual((await request("GET", "/api/session", { headers: remote })).json, { remote: true, readOnly: true });
    assert.deepEqual((await request("GET", "/api/session")).json, { remote: false, readOnly: false });
  });
  test("remote requests can't write anything", async () => {
    const origin = { ...remote, origin: `https://${REMOTE}` };
    for (const [method, url, body] of [
      ["PUT", "/api/env", { key: "ANTHROPIC_API_KEY", value: "stolen" }],
      ["PATCH", "/api/settings", { changes: { min_score: 1 } }],
      ["PUT", "/api/config/raw", { text: "sources: {}" }],
      ["PUT", "/api/profile", { text: "x" }],
      ["POST", "/api/commands/run/run", {}],
      ["POST", "/api/agents/search/kickstart", {}],
    ] as const) {
      const res = await request(method, url, { body, headers: origin });
      assert.equal(res.status, 403, `${method} ${url}`);
      assert.match(res.json.error, /solo lectura/);
    }
    assert.match(fs.readFileSync(path.join(repo, ".env"), "utf8"), /sk-ant-secret-value-1234/);
  });
  test("remote may run only the commands that don't write to the DB", async () => {
    const origin = { ...remote, origin: `https://${REMOTE}` };
    const allowed = await request("POST", "/api/commands/credit/run", { body: {}, headers: origin });
    assert.equal(allowed.status, 200);
    for (let i = 0; i < 50; i++) {  // let it finish so the next test isn't blocked by a running command
      const run = (await request("GET", `/api/runs/${allowed.json.id}`, { headers: remote })).json;
      if (run.status !== "running") break;
      await new Promise((r) => setTimeout(r, 50));
    }
    for (const id of ["run", "run-dry", "credit-send", "skills-send"]) {
      const res = await request("POST", `/api/commands/${id}/run`, { body: {}, headers: origin });
      assert.equal(res.status, 403, id);
    }
    const listed = (await request("GET", "/api/commands", { headers: remote })).json.cli;
    const readOnlyIds = listed.filter((c: any) => !c.writesDb).map((c: any) => c.id).sort();
    assert.deepEqual(readOnlyIds, ["check-sources", "credit", "skills", "test-telegram", "top"]);
  });
  test("a tunneled request can't pose as local by sending a local Host header", async () => {
    const res = await request("PUT", "/api/env", {
      body: { key: "X_KEY", value: "y" },
      headers: { "x-forwarded-for": "203.0.113.7" },  // Host stays 127.0.0.1:<port>
    });
    assert.equal(res.status, 403);
  });
  test("other public hosts and origins are still rejected", async () => {
    assert.equal((await request("GET", "/api/jobs", { headers: { host: "evil.ngrok-free.app" } })).status, 403);
    const res = await request("GET", "/api/jobs", { headers: { ...remote, origin: "https://evil.example" } });
    assert.equal(res.status, 403);
  });
});

describe(".env", () => {
  test("never returns secret values", async () => {
    const res = await request("GET", "/api/env");
    assert.ok(!res.text.includes("sk-ant-secret-value-1234"));
    const key = res.json.find((v: any) => v.key === "ANTHROPIC_API_KEY");
    assert.equal(key.set, true);
    assert.match(key.masked, /1234/);
    assert.equal(res.json.find((v: any) => v.key === "TELEGRAM_CHAT_ID").masked, "42");
    assert.equal(res.json.find((v: any) => v.key === "TELEGRAM_BOT_TOKEN").set, false);
  });
  test("updates one key, keeps comments, stays mode 600", async () => {
    const res = await request("PUT", "/api/env", { body: { key: "TELEGRAM_BOT_TOKEN", value: "123:ABC" } });
    assert.equal(res.status, 200);
    const text = fs.readFileSync(path.join(repo, ".env"), "utf8");
    assert.match(text, /^# secrets$/m);
    assert.match(text, /^TELEGRAM_BOT_TOKEN=123:ABC$/m);
    assert.match(text, /^ANTHROPIC_API_KEY=sk-ant-secret-value-1234$/m);
    assert.equal(fs.statSync(path.join(repo, ".env")).mode & 0o777, 0o600);
  });
  test("rejects invalid keys and multi-line values", async () => {
    assert.equal((await request("PUT", "/api/env", { body: { key: "bad key", value: "x" } })).status, 400);
    assert.equal((await request("PUT", "/api/env", { body: { key: "OK_KEY", value: "a\nb" } })).status, 400);
  });
});

describe("config", () => {
  test("settings edits keep comments and validate", async () => {
    const ok = await request("PATCH", "/api/settings", {
      body: { changes: { min_score: 75, "notify_window.end": "21:30", "sources.linkedin.enabled": false } },
    });
    assert.equal(ok.status, 200);
    const text = fs.readFileSync(path.join(repo, "config.yaml"), "utf8");
    assert.match(text, /^min_score: 75   # send from this score$/m);  // alignment kept byte-for-byte
    assert.match(text, /end: "21:30"   # inclusive/);
    assert.match(text, /enabled: false/);
    const bad = await request("PATCH", "/api/settings", { body: { changes: { min_score: 150 } } });
    assert.equal(bad.status, 400);
    assert.match(bad.json.error, /entre 0 y 100/);
    assert.match(fs.readFileSync(path.join(repo, "config.yaml"), "utf8"), /^min_score: 75/m);
  });
  test("raw YAML is validated before saving", async () => {
    const bad = await request("PUT", "/api/config/raw", { body: { text: "sources: [unclosed" } });
    assert.equal(bad.status, 400);
    const noSources = await request("PUT", "/api/config/raw", { body: { text: "min_score: 70\n" } });
    assert.equal(noSources.status, 400);
  });
  test("profile round-trip", async () => {
    await request("PUT", "/api/profile", { body: { text: "# New profile" } });
    assert.equal((await request("GET", "/api/profile")).json.text, "# New profile\n");
    assert.equal((await request("PUT", "/api/profile", { body: { text: "  " } })).status, 400);
  });
});

describe("jobs", () => {
  test("status filters", async () => {
    await request("PATCH", "/api/settings", { body: { changes: { min_score: 70 } } });
    const titles = async (qs: string) => (await request("GET", `/api/jobs?${qs}`)).json.jobs.map((j: any) => j.title);
    assert.deepEqual(await titles("status=matches"), ["Senior Angular Developer", "Senior React Engineer"]);
    assert.deepEqual(await titles("status=waiting"), ["Senior React Engineer"]);
    assert.deepEqual(await titles("status=sent"), ["Senior Angular Developer"]);
    assert.deepEqual(await titles("status=rejected"), ["Senior Backend Engineer"]);
    assert.deepEqual((await titles("status=relevant&sort=recent")).length, 4);  // passed the prefilter, any score
    assert.deepEqual(await titles("status=unscored"), ["Staff Frontend Engineer"]);
    assert.deepEqual(await titles("q=beta"), ["Senior React Engineer"]);
    assert.deepEqual(await titles("source=linkedin&sort=recent"), ["Staff Frontend Engineer", "Senior React Engineer"]);
    const all = (await request("GET", "/api/jobs?pageSize=2")).json;
    assert.equal(all.total, 5);
    assert.equal(all.jobs.length, 2);
    assert.ok(all.sources.includes("remotive"));
  });
  test("job detail parses red flags", async () => {
    const res = await request("GET", "/api/jobs/1");
    assert.deepEqual(res.json.red_flags, ["pago en MXN"]);
    assert.equal(res.json.description, "Angular 18");
    assert.equal((await request("GET", "/api/jobs/999")).status, 404);
  });
  test("overview totals and credit", async () => {
    const o = (await request("GET", "/api/overview")).json;
    assert.deepEqual(o.totals, { jobs: 5, scored: 3, matches: 2, sent: 1, waiting: 1 });
    assert.equal(o.credit.remaining, 3.54);
    assert.equal(o.sources[0].source, "linkedin");
    assert.equal(o.agents.length, 2);
  });
});

describe("commands", () => {
  test("lists CLI and Telegram commands", async () => {
    const res = (await request("GET", "/api/commands")).json;
    assert.ok(res.cli.some((c: any) => c.id === "check-sources"));
    assert.ok(res.telegram.some((c: any) => c.command === "ultimos"));
    assert.ok(res.telegram.some((c: any) => c.command === "skills"));
  });
  test("runs only whitelisted commands and captures output", async () => {
    assert.equal((await request("POST", `/api/commands/${encodeURIComponent("rm -rf")}/run`, { body: {} })).status, 400);
    const started = (await request("POST", "/api/commands/credit/run", { body: {} })).json;
    let run = started;
    for (let i = 0; i < 50 && run.status === "running"; i++) {
      await new Promise((r) => setTimeout(r, 50));
      run = (await request("GET", `/api/runs/${started.id}`)).json;
    }
    assert.equal(run.status, "ok");
    assert.match(run.output, /ran: -m job_radar credit/);
  });
});
