import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ReadOnlyNotice, useSession } from "../session";

interface FieldSpec {
  path: (string | number)[];
  label: string;
  type: "int" | "number" | "bool" | "time" | "text" | "enum";
  help?: string;
  min?: number;
  max?: number;
  options?: string[];
  nullable?: boolean;
  group: string;
}
interface Settings {
  fields: FieldSpec[];
  values: Record<string, unknown>;
}
type Tab = "general" | "yaml" | "profile";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("general");
  return (
    <>
      <h1>Configuración</h1>
      <ReadOnlyNotice />
      <div className="tabs">
        <button className={tab === "general" ? "active" : ""} onClick={() => setTab("general")}>General</button>
        <button className={tab === "yaml" ? "active" : ""} onClick={() => setTab("yaml")}>config.yaml completo</button>
        <button className={tab === "profile" ? "active" : ""} onClick={() => setTab("profile")}>Perfil (profile.md)</button>
      </div>
      {tab === "general" && <GeneralForm />}
      {tab === "yaml" && (
        <TextFileEditor
          url="/api/config/raw"
          rows={32}
          help="Todo lo que no está en «General»: búsquedas, empresas, palabras del filtro, etc. Se valida antes de guardar y queda un respaldo en config.yaml.bak."
        />
      )}
      {tab === "profile" && (
        <TextFileEditor url="/api/profile" rows={28} help="Claude usa este texto para calificar cada oferta. Los cambios aplican desde la próxima corrida." />
      )}
    </>
  );
}

function GeneralForm() {
  const { readOnly } = useSession();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.get<Settings>("/api/settings").then((s) => (setSettings(s), setDraft(s.values)), (e) => setError(e.message));
  }, []);

  const changes = useMemo(() => {
    if (!settings) return {};
    return Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== settings.values[key]));
  }, [draft, settings]);
  const dirty = Object.keys(changes).length > 0;

  const save = async () => {
    setError(null);
    try {
      const saved = await api.patch<Settings>("/api/settings", { changes });
      setSettings(saved);
      setDraft(saved.values);
      setNotice("Guardado en config.yaml (los comentarios del archivo se conservan). Aplica desde la próxima corrida.");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (!settings) return error ? <div className="error">{error}</div> : <p className="muted">Cargando…</p>;
  const groups = [...new Set(settings.fields.map((f) => f.group))];
  return (
    <>
      {error && <div className="error">{error}</div>}
      {notice && !dirty && <div className="success">{notice}</div>}
      {groups.map((group) => (
        <div className="card" key={group}>
          <h2>{group}</h2>
          <div className="form-grid">
            {settings.fields.filter((f) => f.group === group).map((field) => {
              const key = field.path.join(".");
              return (
                <Field key={key} field={field} value={draft[key]} onChange={(v) => setDraft({ ...draft, [key]: v })} />
              );
            })}
          </div>
        </div>
      ))}
      {!readOnly && (
        <div className={`row save-bar ${dirty ? "visible" : ""}`}>
          <span className="spacer" />
          {dirty && <span className="muted small">{Object.keys(changes).length} cambio(s) sin guardar</span>}
          <button disabled={!dirty} onClick={() => setDraft(settings.values)}>Descartar</button>
          <button className="primary" disabled={!dirty} onClick={save}>Guardar</button>
        </div>
      )}
    </>
  );
}

function Field({ field, value, onChange }: { field: FieldSpec; value: unknown; onChange: (v: unknown) => void }) {
  const id = `f-${field.path.join("-")}`;
  let input;
  switch (field.type) {
    case "bool":
      input = <input id={id} type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
      break;
    case "enum":
      input = (
        <select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {field.options?.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
      break;
    case "time":
      input = <input id={id} type="time" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "int":
    case "number":
      input = (
        <input
          id={id}
          type="number"
          step={field.type === "int" ? 1 : "any"}
          min={field.min}
          max={field.max}
          value={value === null || value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? (field.nullable ? null : "") : Number(e.target.value))}
          style={{ width: 140 }}
        />
      );
      break;
    default:
      input = <input id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} style={{ width: 240 }} />;
  }
  return (
    <>
      <label htmlFor={id}>
        {field.label}
        {field.help && <div className="small muted">{field.help}</div>}
      </label>
      <div>{input}</div>
    </>
  );
}

function TextFileEditor({ url, help, rows }: { url: string; help: string; rows: number }) {
  const { readOnly } = useSession();
  const [original, setOriginal] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ text: string }>(url).then((r) => (setOriginal(r.text), setText(r.text)), (e) => setError(e.message));
  }, [url]);

  const save = async () => {
    setError(null);
    try {
      const saved = await api.put<{ text: string }>(url, { text });
      setOriginal(saved.text);
      setText(saved.text);
      setNotice("Guardado.");
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const dirty = original !== null && text !== original;
  return (
    <div className="card">
      <p className="muted small">{help}</p>
      {error && <div className="error">{error}</div>}
      {notice && !dirty && <div className="success">{notice}</div>}
      <textarea rows={rows} value={text} spellCheck={false} readOnly={readOnly} onChange={(e) => setText(e.target.value)} />
      <div className="row" style={{ marginTop: 8 }}>
        <span className="spacer" />
        <button disabled={!dirty} onClick={() => setText(original ?? "")}>Descartar</button>
        <button className="primary" disabled={!dirty || readOnly} onClick={save}>Guardar</button>
      </div>
    </div>
  );
}
