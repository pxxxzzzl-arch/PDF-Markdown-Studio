import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import packageMetadata from "./package.json";

const appVersion = packageMetadata.version;

if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(appVersion)) {
  throw new Error(`Invalid frontend package version: ${appVersion}`);
}

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: {
    __APP_VERSION_LABEL__: JSON.stringify(`v${appVersion}`),
  },
  build: {
    assetsDir: "static",
    sourcemap: mode !== "desktop",
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom"],
          markdown: ["react-markdown", "remark-gfm", "rehype-raw", "rehype-sanitize"],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
}));
