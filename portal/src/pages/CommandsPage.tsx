import { useEffect, useState } from "react";
import type { CommandInfo, CommandRun, TelegramCommandInfo } from "../../shared/types";
import { api, timeAgo } from "../api";
import Console from "../components/Console";
import { useSession } from "../session";

interface CommandsData {
  cli: CommandInfo[];
  telegram: TelegramCommandInfo[];
  runs: CommandRun[];
}

export default function CommandsPage() {
  const { readOnly } = useSession();
  const [data, setData] = useState<CommandsData | null>(null);
  const [run, setRun] = useState<CommandRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.get<CommandsData>("/api/commands").then(setData, (e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  // Poll the active run until it finishes.
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(async () => {
      try {
        const next = await api.get<CommandRun>(`/api/runs/${run.id}`);
        setRun(next);
        if (next.status !== "running") load();
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [run]);

  const start = async (command: CommandInfo) => {
    const warnings = [
      command.sendsTelegram ? "enviará mensajes a tu Telegram" : "",
      command.costs ? "puede usar crédito de Claude" : "",
    ].filter(Boolean);
    if (warnings.length && !window.confirm(`"${command.label}" ${warnings.join(" y ")}. ¿Continuar?`)) return;
    setError(null);
    try {
      setRun(await api.post<CommandRun>(`/api/commands/${command.id}/run`));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const running = run?.status === "running";
  return (
    <>
      <h1>Comandos</h1>
      {readOnly && (
        <div className="notice">
          🔒 Acceso remoto: puedes ejecutar los comandos que <strong>no escriben en la base de datos</strong>. Los
          marcados «escribe en la DB» solo desde la Mac.
        </div>
      )}
      {error && <div className="error">{error}</div>}
      <div className="card">
        <h2>Línea de comandos</h2>
        <p className="muted small">
          Se ejecutan en el repo con <code>.venv/bin/python -m job_radar …</code>. Solo esta lista está permitida.
        </p>
        <table className="stack">
          <tbody>
            {data?.cli.map((command) => (
              <tr key={command.id} style={{ cursor: "default" }}>
                <td style={{ width: "32%" }}>
                  <strong>{command.label}</strong>
                  <div><code>{command.cli}</code></div>
                </td>
                <td className="small">
                  {command.description}
                  <div className="row" style={{ marginTop: 4 }}>
                    {command.sendsTelegram && <span className="pill warn">envía a Telegram</span>}
                    {command.costs && <span className="pill warn">usa crédito</span>}
                    {command.writesDb && <span className="pill">escribe en la DB</span>}
                  </div>
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    className="primary"
                    disabled={running || (readOnly && command.writesDb)}
                    title={readOnly && command.writesDb ? "Escribe en la base de datos: solo desde la Mac" : undefined}
                    onClick={() => start(command)}
                  >
                    Ejecutar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {run && (
        <div className="card">
          <div className="row" style={{ marginBottom: 8 }}>
            <h2 style={{ margin: 0 }}><code>{run.cli}</code></h2>
            <span className="spacer" />
            {run.status === "running" && <span className="pill warn">ejecutando…</span>}
            {run.status === "ok" && <span className="pill good">terminó bien</span>}
            {run.status === "failed" && <span className="pill bad">falló (exit {run.exitCode})</span>}
          </div>
          <Console text={run.output || "…"} />
        </div>
      )}

      <div className="card">
        <h2>Comandos del bot de Telegram</h2>
        <p className="muted small">Escríbelos a tu bot (la barra es opcional). Se leen de <code>job_radar/bot.py</code>.</p>
        <table className="stack">
          <tbody>
            {data?.telegram.map((c) => (
              <tr key={c.command} style={{ cursor: "default" }}>
                <td style={{ width: 160 }}><code>/{c.command}</code></td>
                <td>{c.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data && data.runs.length > 0 && (
        <div className="card">
          <h2>Ejecuciones recientes (desde que inició el portal)</h2>
          <table className="stack">
            <tbody>
              {data.runs.map((r) => (
                <tr key={r.id} onClick={() => setRun(r)}>
                  <td><code>{r.cli}</code></td>
                  <td>{r.status === "ok" ? "✅" : r.status === "failed" ? "❌" : "⏳"}</td>
                  <td className="muted small">{timeAgo(r.startedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
