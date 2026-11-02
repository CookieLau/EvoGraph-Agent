// Render a scene HTML to an MP4 (and a key still) deterministically, frame by frame.
// Usage:
//   node video/capture.mjs video/scenes/s01_title.html            -> video/out/s01_title.mp4 + video/stills/s01_title.png
//   node video/capture.mjs <scene.html> --fps 30 --still-only      -> only the still
//   node video/capture.mjs <scene.html> --preview 0,1.5,3           -> PNGs at those times in video/out/preview/
import puppeteer from "puppeteer-core";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { basename, resolve, dirname } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const scene = resolve(args[0]);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const fps = Number(opt("--fps", 30));
const stillOnly = args.includes("--still-only");
const preview = opt("--preview", null);
const name = basename(scene).replace(/\.html$/, "");
const outDir = resolve(here, "out"), stillDir = resolve(here, "stills");
mkdirSync(outDir, { recursive: true }); mkdirSync(stillDir, { recursive: true });

const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true,
  args: ["--hide-scrollbars", "--force-device-scale-factor=1", "--disable-gpu-vsync"] });
const page = await browser.newPage();
await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
await page.evaluateOnNewDocument(() => { window.__CAPTURE__ = true; });
await page.goto(pathToFileURL(scene).href, { waitUntil: "networkidle0", timeout: 30000 });
await page.evaluate(async () => { if (document.fonts) await document.fonts.ready; });
const meta = await page.evaluate(() => window.SCENE || { duration: 5, still: 3 });
const seek = (t) => page.evaluate((tt) => window.__seek(tt), t);
const shot = async (path) => page.screenshot({ path, type: "png", clip: { x: 0, y: 0, width: 1920, height: 1080 } });

if (preview) {
  mkdirSync(resolve(outDir, "preview"), { recursive: true });
  for (const t of preview.split(",").map(Number)) { await seek(t); await shot(resolve(outDir, "preview", `${name}_${t}.png`)); }
  console.log("preview written to", resolve(outDir, "preview")); await browser.close(); process.exit(0);
}

await seek(meta.still ?? meta.duration * 0.7);
await shot(resolve(stillDir, `${name}.png`));
console.log("still", resolve(stillDir, `${name}.png`));
if (stillOnly) { await browser.close(); process.exit(0); }

const frames = Math.round(meta.duration * fps);
const out = resolve(outDir, `${name}.mp4`);
const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(fps), "-i", "-",
  "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
  { stdio: ["pipe", "inherit", "inherit"] });
const t0 = Date.now();
for (let i = 0; i < frames; i++) {
  await seek(i / fps);
  const buf = await page.screenshot({ type: "png", clip: { x: 0, y: 0, width: 1920, height: 1080 } });
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
}
ff.stdin.end();
await new Promise((r) => ff.on("close", r));
await browser.close();
console.log(`video ${out} (${frames} frames, ${((Date.now() - t0) / 1000).toFixed(1)}s)`);
