import { useEffect, useState } from "react";
import type { EnvVar } from "../../shared/types";
import { api } from "../api";
import { ReadOnlyNotice, useSession } from "../session";

export default function EnvPage() {
  const { readOnly } = useSession();
  const [vars, setVars] = useState<EnvVar[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api.get<EnvVar[]>("/api/env").then(setVars, (e) => setError(e.message));
  }, []);

  const save = async (key: string, newValue: string) => {
    setError(null);
    try {
      setVars(await api.put<EnvVar[]>("/api/env", { key, value: newValue }));
      setEditing(null);
      setValue("");
      setNewKey("");
      setNewValue("");
      setNotice(`${key} guardada. Las búsquedas la usan desde la próxima corrida; reinicia el bot para que él también la use.`);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const restartBot = async () => {
    try {
      await api.post("/api/agents/bot/kickstart");
      setNotice("Bot reiniciado con los nuevos valores.");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <>
      <h1>Entorno (.env)</h1>
      <ReadOnlyNotice />
      <p className="muted">
        Los secretos nunca se muestran completos: el portal solo sabe si están puestos y sus últimos 4 caracteres.
        El archivo se guarda con permisos <code>600</code> y no se sube a git.
      </p>
      {error && <div className="error">{error}</div>}
      {notice && (
        <div className="success row">
          {notice}
          <span className="spacer" />
          <button onClick={restartBot}>Reiniciar bot</button>
        </div>
      )}
      <div className="card flush">
        <table className="stack">
          <thead>
            <tr><th>Variable</th><th>Valor</th><th /></tr>
          </thead>
          <tbody>
            {vars.map((v) => (
              <tr key={v.key} style={{ cursor: "default" }}>
                <td>
                  <code>{v.key}</code>
                  {v.description && <div className="small muted">{v.description}</div>}
                </td>
                <td>
                  {editing === v.key ? (
                    <form className="row" onSubmit={(e) => (e.preventDefault(), save(v.key, value))}>
                      <input
                        autoFocus
                        type={v.secret ? "password" : "text"}
                        autoComplete="off"
                        placeholder={v.secret ? "Pega el nuevo valor" : ""}
                        value={value}
                        onChange={(e) => setValue(e.target.value)}
                        style={{ minWidth: 280 }}
                      />
                      <button className="primary" type="submit">Guardar</button>
                      <button type="button" onClick={() => setEditing(null)}>Cancelar</button>
                    </form>
                  ) : v.set ? (
                    <span><span className="pill good">puesta</span> <code>{v.masked}</code></span>
                  ) : (
                    <span className="pill bad">vacía</span>
                  )}
                </td>
                <td style={{ textAlign: "right" }}>
                  {editing !== v.key && !readOnly && (
                    <button onClick={() => (setEditing(v.key), setValue(v.secret ? "" : v.masked))}>Cambiar</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!readOnly && <div className="card">
        <h2>Agregar variable</h2>
        <form className="row" onSubmit={(e) => (e.preventDefault(), save(newKey.trim(), newValue))}>
          <input placeholder="NOMBRE_VARIABLE" value={newKey} onChange={(e) => setNewKey(e.target.value.toUpperCase())} />
          <input type="password" autoComplete="off" placeholder="valor" value={newValue}
            onChange={(e) => setNewValue(e.target.value)} />
          <button className="primary" type="submit" disabled={!newKey.trim()}>Agregar</button>
        </form>
      </div>}
    </>
  );
}
