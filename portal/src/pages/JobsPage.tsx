import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { JobsPage as JobsPageData, JobStatus } from "../../shared/types";
import { api, timeAgo, usdMonth } from "../api";
import JobDrawer from "../components/JobDrawer";
import { JobStatusPill } from "../components/jobStatus";
import ScoreBadge from "../components/ScoreBadge";

// Quick views shown as chips above the list (they set status + sort together).
const VIEWS: { key: string; label: string; status: JobStatus; sort: "score" | "recent" }[] = [
  { key: "best", label: "⭐ Mejores", status: "matches", sort: "score" },
  { key: "latest", label: "🆕 Últimas", status: "relevant", sort: "recent" },
  { key: "sent", label: "📨 Enviadas", status: "sent", sort: "recent" },
  { key: "tracking", label: "🗂️ Postulaciones", status: "tracking", sort: "recent" },
  { key: "all", label: "Todas", status: "all", sort: "recent" },
];

const STATUSES: { value: JobStatus; label: string }[] = [
  { value: "matches", label: "Coincidencias (≥ puntaje mínimo)" },
  { value: "relevant", label: "Relevantes (pasaron el filtro)" },
  { value: "tracking", label: "Postulaciones y 👍" },
  { value: "waiting", label: "Por enviar" },
  { value: "sent", label: "Enviadas" },
  { value: "scored", label: "Calificadas" },
  { value: "unscored", label: "Por calificar" },
  { value: "rejected", label: "Descartadas por el filtro" },
  { value: "all", label: "Todas" },
];

export default function JobsPage() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<JobsPageData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [showFilters, setShowFilters] = useState(false);
  const selected = params.get("id");

  const update = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params);
      for (const [key, value] of Object.entries(changes)) {
        if (value === null || value === "") next.delete(key);
        else next.set(key, value);
      }
      if (!("page" in changes) && !("id" in changes)) next.delete("page");
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  // Debounce the text search.
  useEffect(() => {
    const handle = setTimeout(() => {
      if (search !== (params.get("q") ?? "")) update({ q: search });
    }, 300);
    return () => clearTimeout(handle);
  }, [search, params, update]);

  const query = new URLSearchParams(params);
  query.delete("id");
  if (!query.has("status")) query.set("status", "matches");
  const queryString = query.toString();
  const activeView = VIEWS.find(
    (v) => v.status === (query.get("status") ?? "matches") && v.sort === (params.get("sort") ?? "score"),
  )?.key;

  useEffect(() => {
    let cancelled = false;
    api.get<JobsPageData>(`/api/jobs?${queryString}`).then(
      (result) => !cancelled && (setData(result), setError(null)),
      (e) => !cancelled && setError(e.message),
    );
    return () => {
      cancelled = true;
    };
  }, [queryString]);

  const page = data?.page ?? 1;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  return (
    <>
      <h1>Ofertas guardadas</h1>
      <div className={`card row filters ${showFilters ? "open" : ""}`}>
        <input
          className="search"
          type="search"
          placeholder="Buscar título, empresa o ubicación…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button className="filters-toggle only-mobile" onClick={() => setShowFilters(!showFilters)}>
          Filtros {showFilters ? "▴" : "▾"}
        </button>
        <select value={query.get("status") ?? "matches"} onChange={(e) => update({ status: e.target.value })}>
          {STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        <select value={params.get("source") ?? ""} onChange={(e) => update({ source: e.target.value })}>
          <option value="">Todas las fuentes</option>
          {data?.sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={params.get("days") ?? ""} onChange={(e) => update({ days: e.target.value })}>
          <option value="">Cualquier fecha</option>
          <option value="1">Últimas 24 h</option>
          <option value="7">Últimos 7 días</option>
          <option value="30">Últimos 30 días</option>
        </select>
        <select value={params.get("sort") ?? "score"} onChange={(e) => update({ sort: e.target.value })}>
          <option value="score">Mejor puntaje</option>
          <option value="recent">Más recientes</option>
          <option value="posted">Fecha de publicación</option>
        </select>
      </div>

      <div className="chips" role="tablist" aria-label="Vistas rápidas">
        {VIEWS.map((view) => (
          <button
            key={view.key}
            role="tab"
            aria-selected={activeView === view.key}
            className={`chip ${activeView === view.key ? "active" : ""}`}
            onClick={() => update({ status: view.status, sort: view.sort === "score" ? null : view.sort })}
          >
            {view.label}
          </button>
        ))}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="job-cards only-mobile">
        {data?.jobs.map((job) => (
          <button key={job.id} className="job-card" onClick={() => update({ id: String(job.id) })}>
            <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
              <ScoreBadge score={job.score} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <strong>{job.title}</strong>
                <div className="muted small">{job.company}</div>
              </div>
            </div>
            {job.salary_usd_month ? <div className="small" style={{ marginTop: 6 }}>💰 {usdMonth(job.salary_usd_low, job.salary_usd_month)}</div> : null}
            {job.reason && <div className="small" style={{ marginTop: 6 }}>{job.reason}</div>}
            <div className="row small muted" style={{ marginTop: 8 }}>
              <JobStatusPill job={job} />
              {job.fits_location === 0 && <span className="pill bad">ubicación ✗</span>}
              <span>{job.source}</span>
              <span>· {timeAgo(job.first_seen_at)}</span>
            </div>
            {job.location && <div className="small muted ellipsis">📍 {job.location}</div>}
          </button>
        ))}
        {data && data.jobs.length === 0 && <div className="card muted">No hay ofertas con estos filtros.</div>}
      </div>

      <div className="card only-desktop" style={{ padding: 0, overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Pts</th>
              <th>Oferta</th>
              <th className="hide-sm">Ubicación</th>
              <th className="hide-sm">Fuente</th>
              <th>Vista</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {data?.jobs.map((job) => (
              <tr key={job.id} onClick={() => update({ id: String(job.id) })}>
                <td>
                  <ScoreBadge score={job.score} />
                  {job.fits_location === 0 && <div className="small muted" title="La ubicación no encaja">✗ ubic.</div>}
                </td>
                <td>
                  <strong>{job.title}</strong>
                  <div className="muted small">
                    {job.company}
                    {job.salary_usd_month ? ` · 💰 ${usdMonth(job.salary_usd_low, job.salary_usd_month)}` : ""}
                  </div>
                  {job.reason && <div className="small">{job.reason}</div>}
                </td>
                <td className="hide-sm small">{job.location}</td>
                <td className="hide-sm small">{job.source}</td>
                <td className="small muted" style={{ whiteSpace: "nowrap" }} title={new Date(job.first_seen_at).toLocaleString()}>{timeAgo(job.first_seen_at)}</td>
                <td><JobStatusPill job={job} /></td>
              </tr>
            ))}
            {data && data.jobs.length === 0 && (
              <tr style={{ cursor: "default" }}>
                <td colSpan={6} className="muted" style={{ padding: 24, textAlign: "center" }}>
                  No hay ofertas con estos filtros.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {data && (
        <div className="pager">
          <span className="muted small total">{data.total} ofertas</span>
          <button disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>← Anterior</button>
          <span className="small">{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => update({ page: String(page + 1) })}>Siguiente →</button>
        </div>
      )}
      {selected && <JobDrawer id={Number(selected)} onClose={() => update({ id: null })} />}
    </>
  );
}
