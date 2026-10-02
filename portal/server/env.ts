import fs from "node:fs";
import type { EnvVar } from "../shared/types";

const KNOWN: Record<string, { secret: boolean; description: string }> = {
  ANTHROPIC_API_KEY: { secret: true, description: "Clave de la API de Claude (console.anthropic.com → API Keys)" },
  TELEGRAM_BOT_TOKEN: { secret: true, description: "Token del bot de Telegram (@BotFather → /mybots → API Token)" },
  TELEGRAM_CHAT_ID: { secret: false, description: "Tu chat_id de Telegram (python -m job_radar telegram-setup)" },
  TZ: { secret: false, description: "Docker: zona horaria (ej. America/Mexico_City)" },
  NGROK_AUTHTOKEN: { secret: true, description: "Docker: token de ngrok para el acceso remoto" },
  NGROK_DOMAIN: { secret: false, description: "Docker: tu dominio estático de ngrok" },
  NGROK_ALLOWED_EMAILS: { secret: false, description: "Docker: correos de Google autorizados (separados por coma)" },
};
const KEY_PATTERN = /^[A-Z][A-Z0-9_]*$/;
const LINE_PATTERN = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$/;

export function parseValue(raw: string): string {
  const value = raw.trim();
  const quote = value[0];
  if ((quote === '"' || quote === "'") && value.endsWith(quote) && value.length >= 2) {
    const inner = value.slice(1, -1);
    return quote === '"' ? inner.replace(/\\"/g, '"').replace(/\\\\/g, "\\") : inner;
  }
  return value.replace(/\s+#.*$/, "");
}

export function readEnvFile(file: string): Map<string, string> {
  const values = new Map<string, string>();
  if (!fs.existsSync(file)) return values;
  for (const line of fs.readFileSync(file, "utf8").split("\n")) {
    const match = LINE_PATTERN.exec(line);
    if (match) values.set(match[1], parseValue(match[2]));
  }
  return values;
}

/** Secrets are never sent to the browser: only whether they're set and their last 4 characters. */
export function mask(value: string, secret: boolean): string {
  if (!value) return "";
  if (!secret) return value;
  return value.length <= 8 ? "••••" : `••••${value.slice(-4)} (${value.length} caracteres)`;
}

export function listEnv(file: string, exampleFile: string): EnvVar[] {
  const values = readEnvFile(file);
  const keys = [...new Set([...Object.keys(KNOWN), ...readEnvFile(exampleFile).keys(), ...values.keys()])];
  return keys.map((key) => {
    const known = KNOWN[key];
    const secret = known?.secret ?? !/(_ID|_URL|_PORT)$/.test(key);
    const value = values.get(key) ?? "";
    return { key, set: value !== "", masked: mask(value, secret), secret, description: known?.description ?? "" };
  });
}

function serialize(value: string): string {
  return /[\s#"'\\]/.test(value) ? `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"` : value;
}

export function validateEntry(key: string, value: string): string | null {
  if (!KEY_PATTERN.test(key)) return "Nombre inválido: usa MAYÚSCULAS, números y _ (ej. MY_KEY).";
  if (/[\r\n]/.test(value)) return "El valor no puede tener saltos de línea.";
  if (value.length > 4096) return "El valor es demasiado largo.";
  return null;
}

/** Set one KEY=value, keeping every other line (and comments) untouched. Atomic write, mode 600. */
export function setEnv(file: string, key: string, value: string): void {
  const lines = fs.existsSync(file) ? fs.readFileSync(file, "utf8").split("\n") : [];
  const entry = `${key}=${serialize(value)}`;
  const index = lines.findIndex((line) => LINE_PATTERN.exec(line)?.[1] === key);
  if (index >= 0) {
    lines[index] = entry;
  } else {
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    lines.push(entry, "");
  }
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, lines.join("\n"), { mode: 0o600 });
  fs.renameSync(tmp, file);
  fs.chmodSync(file, 0o600);
}
