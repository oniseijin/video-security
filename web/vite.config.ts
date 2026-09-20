import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../src/video_security/web/static",
    emptyOutDir: true,
  },
})
