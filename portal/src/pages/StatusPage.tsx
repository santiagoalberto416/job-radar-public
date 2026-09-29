import { useCallback, useEffect, useState } from "react";
import type { Overview } from "../../shared/types";
import { api, timeAgo, usd } from "../api";
import Console from "../components/Console";
import { ReadOnlyNotice, useSession } from "../session";

const AGENT_NAMES: Record<string, string> = {
  search: "Búsqueda programada (cada 2 h)",
  bot: "Bot de Telegram",
};
const agentName = (label: string) => AGENT_NAMES[label.split(".").pop() ?? ""] ?? label;

export default function StatusPage() {
  const { remote, readOnly } = useSession();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [log, setLog] = useState<"search" | "bot">("search");
  const [logText, setLogText] = useState("");

  const load = useCallback(() => {
    api.get<Overview>("/api/overview").then((d) => (setData(d), setError(null)), (e) => setError(e.message));
    api.get<{ text: string }>(`/api/logs/${log}?lines=150`).then((r) => setLogText(r.text), () => undefined);
  }, [log]);

  useEffect(() => {
    load();
    // Remote visits count against the tunnel's request quota: refresh less often.
    const timer = setInterval(load, remote ? 60_000 : 15_000);
    return () => clearInterval(timer);
  }, [load, remote]);

  const kick = async (key: "search" | "bot") => {
    try {
      await api.post(`/api/agents/${key}/kickstart`);
      setNotice(key === "search" ? "Búsqueda iniciada; revisa el log en unos segundos." : "Bot reiniciado.");
      setTimeout(load, 2000);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <h1>Estado</h1>
      <ReadOnlyNotice />
      {error && <div className="error">{error}</div>}
      {notice && <div className="success">{notice}</div>}
      {data && (
        <>
          <div className="grid">
            <div className="card">
              <div className="muted small">Última búsqueda</div>
              <div className="stat">{timeAgo(data.lastRunAt)}</div>
            </div>
            <div className="card">
              <div className="muted small">Coincidencias / enviadas</div>
              <div className="stat">{data.totals.matches} / {data.totals.sent}</div>
              {data.totals.waiting > 0 && <div className="small">{data.totals.waiting} esperando el horario de envío</div>}
            </div>
            <div className="card">
              <div className="muted small">Ofertas vistas / calificadas</div>
              <div className="stat">{data.totals.jobs} / {data.totals.scored}</div>
            </div>
            <div className="card">
              <div className="muted small">Crédito de Claude estimado</div>
              <div className="stat">{usd(data.credit.remaining)}</div>
              <div className="small muted">Hoy {usd(data.spend.today)} · 30 días {usd(data.spend.last30Days)}</div>
            </div>
          </div>

          <div className="card">
            <h2>Servicios (launchd)</h2>
            <table className="stack">
              <tbody>
                {data.agents.map((agent) => (
                  <tr key={agent.label} style={{ cursor: "default" }}>
                    <td>
                      <strong>{agentName(agent.label)}</strong>
                      <div className="small muted">{agent.label}</div>
                    </td>
                    <td>
                      {!agent.loaded ? (
                        <span className="pill bad">No instalado</span>
                      ) : agent.running ? (
                        <span className="pill good">Corriendo (pid {agent.pid})</span>
                      ) : (
                        <span className="pill">En espera · último exit {agent.lastExitCode}</span>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {agent.label.endsWith(".bot") ? (
                        <button disabled={readOnly} onClick={() => kick("bot")}>Reiniciar bot</button>
                      ) : (
                        <button disabled={readOnly} onClick={() => kick("search")}>Buscar ahora</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h2>Fuentes</h2>
            <table className="compact">
              <thead>
                <tr><th>Fuente</th><th>Estado</th><th>Ofertas</th><th>Último OK</th></tr>
              </thead>
              <tbody>
                {data.sources.map((s) => (
                  <tr key={s.source} style={{ cursor: "default" }}>
                    <td>{s.source}</td>
                    <td>{s.last_error ? <span className="pill bad" title={s.last_error}>Error: {s.last_error}</span> : <span className="pill good">OK</span>}</td>
                    <td>{s.last_count ?? "—"}</td>
                    <td className="muted">{timeAgo(s.last_ok_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Log</h2>
          <span className="spacer" />
          <select value={log} onChange={(e) => setLog(e.target.value as "search" | "bot")}>
            <option value="search">Búsquedas (job-radar.log)</option>
            <option value="bot">Bot (job-radar-bot.log)</option>
          </select>
          <button onClick={load}>Actualizar</button>
        </div>
        <Console text={logText || "(vacío)"} />
      </div>
    </>
  );
}
