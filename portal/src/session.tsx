import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

export interface Session {
  remote: boolean;
  readOnly: boolean;
}

// Until the server answers, assume the most restrictive case.
const SessionContext = createContext<Session>({ remote: true, readOnly: true });

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ remote: true, readOnly: true });
  useEffect(() => {
    api.get<Session>("/api/session").then(setSession, () => undefined);
  }, []);
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}

export const useSession = () => useContext(SessionContext);

export function ReadOnlyNotice() {
  const { readOnly } = useSession();
  if (!readOnly) return null;
  return (
    <div className="notice">
      🔒 Acceso remoto en <strong>modo solo lectura</strong>: puedes ver todo, pero los cambios y comandos solo se
      pueden hacer desde la Mac (http://127.0.0.1:4747).
    </div>
  );
}
