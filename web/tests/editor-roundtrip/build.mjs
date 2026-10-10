// 用 vite 自带的 esbuild 打成 IIFE，供 server/tests/test_editor_roundtrip.py 在 Chromium 中加载。
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
const here = fileURLToPath(new URL(".", import.meta.url));
await build({ entryPoints: [here + "entry.ts"], bundle: true, format: "iife", outfile: process.argv[2] || here + "dist/roundtrip.js", logLevel: "warning" });
