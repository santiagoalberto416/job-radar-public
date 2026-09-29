import type { JobSummary } from "../../shared/types";

export function JobStatusPill({ job, minScore = 70 }: { job: JobSummary; minScore?: number }) {
  if (job.notified_at) return <span className="pill good">Enviada</span>;
  if (job.prefilter_passed === 0) return <span className="pill" title={job.prefilter_reason ?? ""}>Descartada</span>;
  if (job.score === null) {
    return job.last_score_error ? (
      <span className="pill bad" title={job.last_score_error}>Error</span>
    ) : (
      <span className="pill warn">Por calificar</span>
    );
  }
  if (job.score >= minScore && job.fits_location) return <span className="pill warn">Por enviar</span>;
  return <span className="pill">Calificada</span>;
}
