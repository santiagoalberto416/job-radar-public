export default function ScoreBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="score low">—</span>;
  const tone = score >= 70 ? "high" : score >= 40 ? "mid" : "low";
  return <span className={`score ${tone}`}>{score}</span>;
}
