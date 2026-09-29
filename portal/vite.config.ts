import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_PORT = Number(process.env.PORTAL_PORT ?? 4747);

// `npm run dev`: Vite serves the UI on 4748 and proxies /api to the Express server on 4747.
// `npm start`: Express serves the built UI and the API together on 4747.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: API_PORT + 1,
    strictPort: true,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${API_PORT}`,
        // The API only accepts its own origin; present the proxied request as same-origin.
        headers: { origin: `http://127.0.0.1:${API_PORT}`, host: `127.0.0.1:${API_PORT}` },
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
