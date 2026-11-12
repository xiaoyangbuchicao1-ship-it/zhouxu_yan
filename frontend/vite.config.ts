import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = {
    "/api": {
      target: process.env.ZHOUXU_API_TARGET || env.ZHOUXU_API_TARGET || "http://127.0.0.1:8010",
      changeOrigin: true,
      rewrite: (url: string) => url.replace(/^\/api/, ""),
    },
  };
  return {
    server: {
      host: "::",
      port: Number(process.env.ZHOUXU_WEB_PORT || env.ZHOUXU_WEB_PORT || 4173),
      strictPort: true,
      proxy,
      hmr: {
        overlay: false,
      },
    },
    preview: { proxy },
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
  };
});
