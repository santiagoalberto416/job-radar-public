import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import CommandsPage from "./pages/CommandsPage";
import EnvPage from "./pages/EnvPage";
import JobsPage from "./pages/JobsPage";
import SettingsPage from "./pages/SettingsPage";
import StatusPage from "./pages/StatusPage";
import { useSession } from "./session";

const NAV = [
  { to: "/ofertas", label: "Ofertas", short: "Ofertas", icon: "📋" },
  { to: "/estado", label: "Estado", short: "Estado", icon: "📊" },
  { to: "/comandos", label: "Comandos", short: "Comandos", icon: "⌘" },
  { to: "/configuracion", label: "Configuración", short: "Config", icon: "⚙️" },
  { to: "/entorno", label: "Entorno (.env)", short: ".env", icon: "🔑" },
];

type Theme = "dark" | "light";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    if (next === "light") document.documentElement.dataset.theme = "light";
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* storage unavailable: the choice just won't persist */
    }
    setTheme(next);
  };
  return [theme, toggle];
}

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const { remote } = useSession();
  return (
    <div className="layout">
      <header className="topbar">
        <span className="brand">🛰️ job-radar</span>
        <nav className="top-nav">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <span className="spacer" />
        <button
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
          aria-label={theme === "dark" ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
        >
          {theme === "dark" ? "☀️" : "🌙"}
        </button>
        {remote ? (
          <span className="remote-badge" title="Conectado por el túnel (ngrok): solo lectura">
            remoto<span className="hide-sm"> · solo lectura</span>
          </span>
        ) : (
          <span className="local-badge" title="Conectado desde la Mac">local</span>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/ofertas" replace />} />
          <Route path="/ofertas" element={<JobsPage />} />
          <Route path="/estado" element={<StatusPage />} />
          <Route path="/comandos" element={<CommandsPage />} />
          <Route path="/configuracion" element={<SettingsPage />} />
          <Route path="/entorno" element={<EnvPage />} />
          <Route path="*" element={<Navigate to="/ofertas" replace />} />
        </Routes>
      </main>
      <nav className="bottom-nav" aria-label="Secciones">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="icon" aria-hidden>{item.icon}</span>
            <span>{item.short}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
