import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // the end-to-end test uploads the synthetic payslip from the backend test fixtures
    fs: { allow: [".", "../backend/tests/fixtures"] },
  },
  test: {
    environment: "jsdom",
    globals: false,
    testTimeout: 20000,
  },
});
