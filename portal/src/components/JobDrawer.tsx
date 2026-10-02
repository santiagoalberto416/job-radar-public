import { useEffect, useRef, useState } from "react";
import type { JobDetail } from "../../shared/types";
import { api, timeAgo, usdMonth } from "../api";
import { APPLICATION_LABELS } from "./jobStatus";
import ScoreBadge from "./ScoreBadge";

export default function JobDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    setJob(null);
    setError(null);
    api.get<JobDetail>(`/api/jobs/${id}`).then((j) => (setJob(j), setNotes(j.notes ?? "")), (e) => setError(e.message));
  }, [id]);

  const track = async (change: Record<string, string | null>) => {
    try {
      setJob(await api.patch<JobDetail>(`/api/jobs/${id}/tracking`, change));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Detalle de la oferta">
        <div className="row">
          <span className="spacer" />
          <button onClick={onClose} aria-label="Cerrar">✕</button>
        </div>
        {error && <div className="error">{error}</div>}
        {!job && !error && <p className="muted">Cargando…</p>}
        {job && (
          <>
            <h1>{job.title}</h1>
            <p className="row">
              <ScoreBadge score={job.score} />
              {job.fits_location !== null && (
                <span className={`pill ${job.fits_location ? "good" : "bad"}`}>
                  {job.fits_location ? "Ubicación OK" : "Ubicación no encaja"}
                </span>
              )}
              {job.seniority && <span className="pill">{job.seniority}</span>}
              <a href={job.url} target="_blank" rel="noreferrer noopener">Abrir oferta ↗</a>
            </p>
            <div className="card tracking">
              <div className="row">
                <button className={job.feedback === "like" ? "active" : ""}
                  onClick={() => track({ feedback: job.feedback === "like" ? null : "like" })}>👍 Me interesa</button>
                <button className={job.feedback === "dislike" ? "active" : ""}
                  onClick={() => track({ feedback: job.feedback === "dislike" ? null : "dislike" })}>👎 No</button>
                <select value={job.status ?? ""} onChange={(e) => track({ status: e.target.value || null })}>
                  <option value="">Sin postulación</option>
                  {Object.entries(APPLICATION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                {job.status_at && <span className="small muted">{timeAgo(job.status_at)}</span>}
              </div>
              <textarea rows={3} placeholder="Notas (contacto, fechas, salario ofrecido…)" value={notes}
                onChange={(e) => setNotes(e.target.value)} style={{ marginTop: 8 }} />
              {notes !== (job.notes ?? "") && (
                <div className="row" style={{ marginTop: 6 }}>
                  <span className="spacer" />
                  <button className="primary" onClick={() => track({ notes })}>Guardar notas</button>
                </div>
              )}
              <div className="small muted" style={{ marginTop: 6 }}>
                Tus 👍/👎 y postulaciones ayudan a Claude a calificar las siguientes ofertas.
              </div>
            </div>
            <table className="stack kv">
              <tbody>
                {[
                  ["Empresa", job.company],
                  ["Ubicación", job.location],
                  ["Salario", job.salary],
                  ["Salario en USD", job.salary_usd_month ? usdMonth(job.salary_usd_low, job.salary_usd_month) : null],
                  ["Cerrada", job.closed_at ? `Sí, detectada ${timeAgo(job.closed_at)}` : null],
                  ["Fuente", job.source],
                  ["Publicada", job.posted_at ? new Date(job.posted_at).toLocaleDateString() : null],
                  ["Vista por primera vez", `${new Date(job.first_seen_at).toLocaleString()} (${timeAgo(job.first_seen_at)})`],
                  ["Enviada a Telegram", job.notified_at ? new Date(job.notified_at).toLocaleString() : "No"],
                  ["Filtro de palabras", `${job.prefilter_passed ? "Pasó" : "Descartada"}: ${job.prefilter_reason ?? ""}`],
                  ["Modelo", job.score_model],
                  ["Error al calificar", job.last_score_error],
                ]
                  .filter(([, value]) => value)
                  .map(([label, value]) => (
                    <tr key={label} style={{ cursor: "default" }}>
                      <th style={{ width: 180 }}>{label}</th>
                      <td>{value}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {job.reason && (
              <>
                <h2 style={{ marginTop: 20 }}>Por qué (Claude)</h2>
                <p>{job.reason}</p>
              </>
            )}
            {job.red_flags.length > 0 && (
              <>
                <h2>Alertas</h2>
                <ul>{job.red_flags.map((flag) => <li key={flag}>{flag}</li>)}</ul>
              </>
            )}
            <h2 style={{ marginTop: 20 }}>Descripción</h2>
            <div className="description">{job.description || "(sin descripción)"}</div>
          </>
        )}
      </aside>
    </>
  );
}
