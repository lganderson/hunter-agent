import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

function portFromEnv(value: string | undefined, fallback: number) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const frontendPort = portFromEnv(env.VITE_PORT, 5173);
  const apiPort = portFromEnv(env.HUNTER_API_PORT || env.PORT, 8010);

  return {
    plugins: [react()],
    server: {
      port: frontendPort,
      strictPort: true,
      proxy: {
        "/api": `http://127.0.0.1:${apiPort}`
      }
    }
  };
});
