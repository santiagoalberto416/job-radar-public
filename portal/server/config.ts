import fs from "node:fs";
import YAML, { isScalar } from "yaml";

export type FieldType = "int" | "number" | "bool" | "time" | "text" | "enum";

export interface FieldSpec {
  path: (string | number)[];
  label: string;
  type: FieldType;
  help?: string;
  min?: number;
  max?: number;
  options?: string[];
  nullable?: boolean;
  group: string;
}

export const MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5", "claude-opus-5-5", "claude-fable-5-1"];

const BASE_FIELDS: FieldSpec[] = [
  { group: "Notificaciones", path: ["min_score"], label: "Puntaje mínimo para enviar", type: "int", min: 0, max: 100 },
  { group: "Notificaciones", path: ["require_location_fit"], label: "Exigir que la ubicación encaje", type: "bool" },
  { group: "Notificaciones", path: ["notify_window", "start"], label: "Enviar desde", type: "time" },
  { group: "Notificaciones", path: ["notify_window", "end"], label: "Enviar hasta (inclusive)", type: "time" },
  { group: "Notificaciones", path: ["max_job_age_days"], label: "Ignorar ofertas con más de (días)", type: "int", min: 1, max: 365 },
  { group: "Claude", path: ["llm", "model"], label: "Modelo", type: "enum", options: MODELS,
    help: "Sonnet 5 ≈ $0.0045/oferta, Opus 5 ≈ $0.0085, Haiku 4.5 ≈ $0.0025" },
  { group: "Claude", path: ["llm", "effort"], label: "Esfuerzo", type: "enum", options: ["low", "medium", "high"] },
  { group: "Claude", path: ["llm", "max_jobs_per_run"], label: "Máximo de ofertas por corrida", type: "int", min: 1, max: 200 },
  { group: "Crédito", path: ["credit", "balance_usd"], label: "Saldo leído en la Console (USD)", type: "number", min: 0, max: 100000, nullable: true },
  { group: "Crédito", path: ["credit", "as_of"], label: "Fecha/hora de esa lectura", type: "text",
    help: "\"YYYY-MM-DD\" o \"YYYY-MM-DD HH:MM\" (hora local)" },
  { group: "Crédito", path: ["credit", "warn_below_usd"], label: "Avisar debajo de (USD)", type: "number", min: 0, max: 100000 },
];

export function readText(file: string): string {
  return fs.readFileSync(file, "utf8");
}

function atomicWrite(file: string, text: string): void {
  if (fs.existsSync(file)) fs.copyFileSync(file, `${file}.bak`);
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, text);
  fs.renameSync(tmp, file);
}

/** Parse config.yaml text; returns an error message if it isn't a usable job-radar config. */
export function validateConfigText(text: string): string | null {
  const doc = YAML.parseDocument(text);
  if (doc.errors.length) return `YAML inválido: ${doc.errors[0].message}`;
  const data = doc.toJS();
  if (!data || typeof data !== "object" || Array.isArray(data)) return "config.yaml debe ser un mapa de claves.";
  if (typeof data.sources !== "object" || data.sources === null) return "Falta la sección `sources`.";
  if (data.min_score !== undefined && typeof data.min_score !== "number") return "`min_score` debe ser un número.";
  return null;
}

export function writeConfigText(file: string, text: string): void {
  const error = validateConfigText(text);
  if (error) throw new Error(error);
  atomicWrite(file, text.endsWith("\n") ? text : `${text}\n`);
}

export function fieldSpecs(file: string): FieldSpec[] {
  const data = YAML.parse(readText(file)) ?? {};
  const sources = Object.keys(data.sources ?? {});
  const perSource: FieldSpec[] = sources.flatMap((name) => [
    { group: "Fuentes", path: ["sources", name, "enabled"], label: `${name}: activa`, type: "bool" as const },
    { group: "Fuentes", path: ["sources", name, "min_interval_hours"], label: `${name}: cada (horas)`,
      type: "number" as const, min: 0.5, max: 168 },
  ]);
  return [...BASE_FIELDS, ...perSource];
}

export function readSettings(file: string): { fields: FieldSpec[]; values: Record<string, unknown> } {
  const doc = YAML.parseDocument(readText(file));
  const fields = fieldSpecs(file);
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    const value = doc.getIn(field.path);
    values[field.path.join(".")] = value === undefined ? (field.type === "bool" ? true : null) : value;
  }
  return { fields, values };
}

export function coerce(field: FieldSpec, value: unknown): unknown {
  const fail = (why: string): never => {
    throw new Error(`${field.label}: ${why}`);
  };
  if ((value === null || value === "") && field.nullable) return null;
  switch (field.type) {
    case "bool":
      if (typeof value !== "boolean") fail("debe ser verdadero o falso");
      return value;
    case "int":
    case "number": {
      const n = typeof value === "number" ? value : Number(value);
      if (!Number.isFinite(n)) fail("debe ser un número");
      if (field.type === "int" && !Number.isInteger(n)) fail("debe ser un entero");
      if ((field.min !== undefined && n < field.min) || (field.max !== undefined && n > field.max)) {
        fail(`debe estar entre ${field.min} y ${field.max}`);
      }
      return n;
    }
    case "time":
      if (typeof value !== "string" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) fail("usa el formato HH:MM");
      return value;
    case "enum":
      if (typeof value !== "string" || !field.options?.includes(value)) fail(`opciones: ${field.options?.join(", ")}`);
      return value;
    default: {
      if (typeof value !== "string" || value.length > 200 || /[\r\n]/.test(value)) return fail("texto inválido");
      const text: string = value;
      if (field.path.join(".") === "credit.as_of" && text && !/^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$/.test(text)) {
        fail('usa "YYYY-MM-DD" o "YYYY-MM-DD HH:MM"');
      }
      return text;
    }
  }
}

/** How a new value is written in place of an existing scalar, keeping its quote style. */
export function renderScalar(value: unknown, originalFirstChar: string): string {
  if (value === null) return "null";
  if (typeof value !== "string") return String(value);
  if (originalFirstChar === "'") return `'${value.replace(/'/g, "''")}'`;
  if (originalFirstChar === '"' || YAML.parse(value) !== value || value === "") return JSON.stringify(value);
  return value;
}

/**
 * Update some settings. Existing values are replaced in the text at their exact position, so every comment,
 * alignment and flow mapping in config.yaml stays byte-for-byte the same. Keys that don't exist yet are added
 * through the YAML document (that path re-serializes the file, which is rare).
 */
export function updateSettings(file: string, changes: Record<string, unknown>): void {
  const text = readText(file);
  const doc = YAML.parseDocument(text);
  const fields = new Map(fieldSpecs(file).map((f) => [f.path.join("."), f]));
  const edits: { start: number; end: number; text: string }[] = [];
  let reserialize = false;
  for (const [key, raw] of Object.entries(changes)) {
    const field = fields.get(key);
    if (!field) throw new Error(`Campo desconocido: ${key}`);
    const value = coerce(field, raw);
    const node = doc.getIn(field.path, true);
    if (isScalar(node) && node.range && node.value !== null) {
      const [start, end] = node.range;
      edits.push({ start, end, text: renderScalar(value, text[start]) });
      node.value = value;
    } else {
      doc.setIn(field.path, value);
      reserialize = true;
    }
  }
  let updated = text;
  if (reserialize) {
    updated = doc.toString();
  } else {
    for (const edit of edits.sort((a, b) => b.start - a.start)) {
      updated = updated.slice(0, edit.start) + edit.text + updated.slice(edit.end);
    }
  }
  writeConfigText(file, updated);
}

export function writeProfile(file: string, text: string): void {
  if (!text.trim()) throw new Error("El perfil no puede estar vacío.");
  if (text.length > 50_000) throw new Error("El perfil es demasiado largo (máx. 50,000 caracteres).");
  atomicWrite(file, text.endsWith("\n") ? text : `${text}\n`);
}
