// Thin client for the portal API. Writes carry X-Portal so the server can reject cross-site requests.

export class ApiError extends Error {}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: {
      // Skips ngrok's free-plan browser warning on API calls when the portal is opened remotely.
      "ngrok-skip-browser-warning": "1",
      ...(body === undefined ? {} : { "content-type": "application/json", "x-portal": "1" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error ?? `HTTP ${response.status}`);
  return data as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  put: <T>(url: string, body: unknown) => request<T>("PUT", url, body),
  patch: <T>(url: string, body: unknown) => request<T>("PATCH", url, body),
  post: <T>(url: string, body: unknown = {}) => request<T>("POST", url, body),
};

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "nunca";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "ahora";
  if (minutes < 60) return `hace ${minutes} min`;
  if (minutes < 48 * 60) return `hace ${Math.round(minutes / 60)} h`;
  return `hace ${Math.round(minutes / 1440)} días`;
}

/** "~US$1.9k–2.5k/mes", like the Telegram digest. */
export function usdMonth(low: number | null | undefined, high: number | null | undefined): string {
  if (!high) return "";
  const k = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1).replace(/\.0$/, "")}k` : `${Math.round(v)}`);
  return !low || Math.abs(high - low) < 50 ? `~US$${k(high)}/mes` : `~US$${k(low)}–${k(high)}/mes`;
}

export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Math.abs(value) >= 0.1 || value === 0 ? `$${value.toFixed(2)}` : `$${value.toFixed(3)}`;
}
