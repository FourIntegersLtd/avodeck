// Renders each canvas artboard and the live page at the same size, then writes
// <id>.artboard.png, <id>.page.png, <id>.diff.png and <id>.json to out/ (git-ignored).
// Usage: node scripts/design-diff/run.mjs [--base URL] [--out DIR] [id ...]
import { chromium } from "@playwright/test";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import { readFileSync, readdirSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { sessionMocks } from "./mocks.mjs";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, "../..");
const canvasDir = join(webRoot, "design/canvas/project");
const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args.splice(i, 2)[1] : fallback;
};
const base = opt("--base", process.env.DESIGN_DIFF_BASE ?? "http://127.0.0.1:5173");
const outDir = resolve(opt("--out", join(here, "out")));
const only = args;
mkdirSync(outDir, { recursive: true });

// screens.json holds the defaults; each screen is its own file in screens/ so owners never edit one list.
const config = JSON.parse(readFileSync(join(here, "screens.json"), "utf8"));
config.screens = readdirSync(join(here, "screens"))
  .filter((name) => name.endsWith(".json"))
  .sort()
  .map((name) => JSON.parse(readFileSync(join(here, "screens", name), "utf8")));
const GRID = 80;
// Next's development overlay and route indicator are not part of any screen.
const DEV_NOISE = ["nextjs-portal", "[data-nextjs-toast]", "[data-nextjs-dev-tools-button]"];
const PROBE_TOLERANCE = 2;

// Canvas fonts, served from the app's own @fontsource packages so both sides use the same files.
const FONT_FILES = [
  ["Atkinson Hyperlegible", 400, "normal", "atkinson-hyperlegible/files/atkinson-hyperlegible-latin-400-normal.woff2"],
  ["Atkinson Hyperlegible", 700, "normal", "atkinson-hyperlegible/files/atkinson-hyperlegible-latin-700-normal.woff2"],
  ["Atkinson Hyperlegible", 400, "italic", "atkinson-hyperlegible/files/atkinson-hyperlegible-latin-400-italic.woff2"],
  ["IBM Plex Mono", 400, "normal", "ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2"],
  ["IBM Plex Mono", 500, "normal", "ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2"],
  ["Outfit", 600, "normal", "outfit/files/outfit-latin-600-normal.woff2"],
];
const fontFaces = FONT_FILES.map(
  ([family, weight, style, file]) =>
    `@font-face{font-family:'${family}';font-weight:${weight};font-style:${style};font-display:block;src:url(http://artboard.local/fonts/${file}) format('woff2')}`,
).join("");

function artboardHtml(file, extraCss = "") {
  const src = readFileSync(join(canvasDir, file), "utf8");
  const helmet = /<helmet>([\s\S]*?)<\/helmet>/.exec(src)?.[1] ?? "";
  const style = [...helmet.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");
  const body = /<x-dc>([\s\S]*?)<\/x-dc>/.exec(src)?.[1].replace(/<helmet>[\s\S]*?<\/helmet>/, "") ?? "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><style>${fontFaces}${style}${extraCss}</style></head><body>${body}</body></html>`;
}

const PROP_NAMES = ["fontFamily", "fontSize", "fontWeight", "lineHeight", "color", "backgroundColor", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "borderTopLeftRadius"];

function measure(page, selector) {
  return page.evaluate(
    ([sel, props]) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const style = {};
      for (const p of props) style[p] = cs[p];
      return { rect: { x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height }, style };
    },
    [selector, PROP_NAMES],
  );
}

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
}

async function strip(page, selectors) {
  for (const sel of selectors) await page.evaluate((s) => document.querySelectorAll(s).forEach((e) => e.remove()), sel);
}

function toPng(buf) {
  return PNG.sync.read(buf);
}

function hotCells(a, b, width, height) {
  const cells = new Map();
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      if (a.data[i] === b.data[i] && a.data[i + 1] === b.data[i + 1] && a.data[i + 2] === b.data[i + 2]) continue;
      const key = `${Math.floor(x / GRID)},${Math.floor(y / GRID)}`;
      cells.set(key, (cells.get(key) ?? 0) + 1);
    }
  return [...cells.entries()]
    .sort((p, q) => q[1] - p[1])
    .slice(0, 8)
    .map(([key, count]) => {
      const [cx, cy] = key.split(",").map(Number);
      return { x: cx * GRID, y: cy * GRID, w: GRID, h: GRID, mismatched: count, ratio: +(count / (GRID * GRID)).toFixed(3) };
    });
}

function compareProbe(probe, a, p) {
  if (!a || !p) return { name: probe.name, missing: !a ? "artboard" : "page" };
  const allowed = new Set(probe.allow ?? []); // deliberate deviations; the reason sits beside them in the screen file
  const findings = [];
  for (const k of ["x", "y", "w", "h"]) {
    const d = +(p.rect[k] - a.rect[k]).toFixed(1);
    if (Math.abs(d) > PROBE_TOLERANCE && !allowed.has(`rect.${k}`)) findings.push({ prop: `rect.${k}`, artboard: +a.rect[k].toFixed(1), page: +p.rect[k].toFixed(1) });
  }
  for (const k of PROP_NAMES) {
    if (allowed.has(k)) continue;
    const av = a.style[k], pv = p.style[k];
    if (k === "fontFamily") {
      const first = (s) => s.split(",")[0].replace(/["']/g, "").trim();
      if (first(av) !== first(pv)) findings.push({ prop: k, artboard: av, page: pv });
    } else if (/px$/.test(av) && /px$/.test(pv)) {
      if (Math.abs(parseFloat(av) - parseFloat(pv)) > PROBE_TOLERANCE) findings.push({ prop: k, artboard: av, page: pv });
    } else if (av !== pv) findings.push({ prop: k, artboard: av, page: pv });
  }
  return findings.length ? { name: probe.name, findings } : null;
}

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const results = [];
let storage;

async function signedInState(account) {
  if (!account) return undefined;
  // Sign in once per run per account and reuse the storage state (the sign-in endpoint is rate limited).
  storage ??= {};
  if (storage[account.email]) return storage[account.email];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(base + "/sign-in");
  await page.fill("input[type=email]", account.email);
  await page.fill("input[type=password]", account.password);
  await page.click("button[type=submit]");
  await page.waitForLoadState("networkidle");
  storage[account.email] = await ctx.storageState();
  await ctx.close();
  return storage[account.email];
}

for (const screen of config.screens) {
  if (only.length && !only.includes(screen.id)) continue;
  const viewport = { width: screen.width, height: screen.height };
  const budget = screen.budget ?? config.budget; // a per-screen budget needs a budgetReason in its file

  const aCtx = await browser.newContext({ viewport, reducedMotion: "reduce" });
  await aCtx.route("http://artboard.local/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.startsWith("/fonts/")) return route.fulfill({ body: readFileSync(join(webRoot, "node_modules/@fontsource", path.slice(7))), contentType: "font/woff2" });
    return route.fulfill({ body: artboardHtml(screen.artboard, `${config.artboardCss ?? ""}${screen.artboardCss ?? ""}`), contentType: "text/html" });
  });
  const aPage = await aCtx.newPage();
  await aPage.goto("http://artboard.local/");
  await settle(aPage);
  await strip(aPage, screen.artboardRemove ?? []);

  const pCtx = await browser.newContext({ viewport, reducedMotion: "reduce", storageState: await signedInState(screen.account) });
  const pPage = await pCtx.newPage();
  // Canned API answers for a state the artboard draws (path -> JSON body); anything else goes through.
  const mocks = { ...(screen.session ? sessionMocks() : {}), ...(screen.mock ?? {}) };
  // A body may be {__status, json} for an error answer, {__sequence: [json, ...]} to answer successive calls
  // with successive bodies (the last repeats), or {"$body", "$type"} for a raw answer such as an image.
  for (const [path, body] of Object.entries(mocks)) {
    let calls = 0;
    await pPage.route(`**${path}`, (route) => {
      if (body?.__sequence) return route.fulfill({ json: body.__sequence[Math.min(calls++, body.__sequence.length - 1)] });
      if (body?.$file) return route.fulfill({ body: readFileSync(join(here, body.$file)), contentType: body.contentType });
      if (body?.$body !== undefined) return route.fulfill({ body: body.$body, contentType: body.$type });
      return route.fulfill(body?.__status ? { status: body.__status, json: body.json ?? {} } : { json: body });
    });
  }
  await pPage.goto(base + screen.route);
  for (const step of screen.setup ?? []) {
    if (step.fill) await pPage.fill(step.fill, step.value);
    else if (step.click) await pPage.click(step.click);
    else if (step.waitFor) await pPage.waitForSelector(step.waitFor);
    else if (step.setFiles) await pPage.setInputFiles(step.setFiles, step.files.map((f) => ({ name: f.name, mimeType: f.mimeType, buffer: Buffer.from(f.text ?? "x") })));
    else if (step.hover) await pPage.hover(step.hover); // moves the pointer off a button a step just clicked
    else if (step.scrollTop) await pPage.evaluate(() => window.scrollTo(0, 0));
    else if (step.goto) await pPage.goto(base + step.goto);
    else if (step.wait) await pPage.waitForTimeout(step.wait);
    else if (step.blur) await pPage.evaluate(() => document.activeElement?.blur());
  }
  await pPage.waitForLoadState("networkidle");
  await settle(pPage);
  await strip(pPage, [...DEV_NOISE, ...(screen.pageRemove ?? [])]);

  const clip = { x: 0, y: 0, width: screen.width, height: screen.height };
  const aShot = toPng(await aPage.screenshot({ clip, animations: "disabled" }));
  const pShot = toPng(await pPage.screenshot({ clip, animations: "disabled" }));
  writeFileSync(join(outDir, `${screen.id}.artboard.png`), PNG.sync.write(aShot));
  writeFileSync(join(outDir, `${screen.id}.page.png`), PNG.sync.write(pShot));
  const diff = new PNG({ width: screen.width, height: screen.height });
  const mismatched = pixelmatch(aShot.data, pShot.data, diff.data, screen.width, screen.height, { threshold: 0.1 });
  writeFileSync(join(outDir, `${screen.id}.diff.png`), PNG.sync.write(diff));

  const probes = [];
  const rects = {};
  for (const probe of screen.probes ?? []) {
    const a = await measure(aPage, probe.artboard);
    const p = await measure(pPage, probe.page);
    const round = (m) => m && Object.fromEntries(Object.entries(m.rect).map(([k, v]) => [k, +v.toFixed(1)]));
    rects[probe.name] = { artboard: round(a), page: round(p) };
    const hit = compareProbe(probe, a, p);
    if (hit) probes.push(hit);
  }
  const ratio = mismatched / (screen.width * screen.height);
  const report = {
    id: screen.id,
    mismatchPercent: +(ratio * 100).toFixed(3),
    budgetPercent: budget,
    hotCells: hotCells(aShot, pShot, screen.width, screen.height),
    probes,
    rects,
    deviations: [...(config.deviations ?? []), ...(screen.deviations ?? [])],
  };
  report.pass = report.mismatchPercent <= budget && probes.length === 0;
  writeFileSync(join(outDir, `${screen.id}.json`), JSON.stringify(report, null, 2));
  results.push(report);
  await aCtx.close();
  await pCtx.close();
}
await browser.close();

for (const r of results) {
  console.log(`${r.pass ? "PASS" : "FAIL"} ${r.id}  mismatch ${r.mismatchPercent}% (budget ${r.budgetPercent}%)`);
  if (!r.pass) {
    for (const c of r.hotCells.slice(0, 4)) console.log(`  hot cell x=${c.x} y=${c.y} ${c.w}x${c.h} ${(c.ratio * 100).toFixed(0)}% differ`);
    for (const p of r.probes) {
      if (p.missing) console.log(`  probe "${p.name}": no element in ${p.missing}`);
      else for (const f of p.findings) console.log(`  probe "${p.name}" ${f.prop}: artboard ${f.artboard} vs page ${f.page}`);
    }
  }
}
process.exit(results.some((r) => !r.pass) ? 1 : 0);
