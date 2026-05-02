import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Pin port 3000 + strictPort so dev/ tooling lines up. See
// dev/ memory `feedback_vite_dev_port` — vite's default :5173 collides
// with project assumptions everywhere else.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        // changeOrigin: false today; flip to true or extend Origin allowlist when Step 8 adds ws auth.
        changeOrigin: false,
      },
      "/ws": {
        target: "http://localhost:8000",
        ws: true,
        // changeOrigin: false today; flip to true or extend Origin allowlist when Step 8 adds ws auth.
        changeOrigin: false,
      },
    },
  },
});
