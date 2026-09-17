// Transversal states for the `after/` set (Sprint 1, lot L15): empty, loading,
// error/404 and permission/disabled, captured against the local demo stack
// without touching the backend or its data. Loading is captured by delaying
// the API responses from the browser side; the others are real screens.
//
// Environment: OUT_DIR (required), DEMO_PASSWORD (required), DEMO_EMAIL,
// OTHER_EMAIL / OTHER_PASSWORD (a second seed user with no data, optional),
// VIEWPORT (default 1440x900), BASE_URL.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE_URL || "http://localhost:3000";
const OUT = process.env.OUT_DIR;
const EMAIL = process.env.DEMO_EMAIL || "demo@licitacions.dev";
const PASSWORD = process.env.DEMO_PASSWORD;
const OTHER_EMAIL = process.env.OTHER_EMAIL || "altre@licitacions.dev";
const OTHER_PASSWORD = process.env.OTHER_PASSWORD;
const [VIEW_W, VIEW_H] = (process.env.VIEWPORT || "1440x900").split("x").map((n) => Number(n));
const IS_PHONE = VIEW_W < 640;
const PREFIX = process.env.PREFIX || "";
if (!OUT) throw new Error("OUT_DIR is required");
if (!PASSWORD) throw new Error("DEMO_PASSWORD is required (never hardcode it)");
fs.mkdirSync(OUT, { recursive: true });

async function login(page, email, password) {
  await page.goto(BASE + "/login", { waitUntil: "load" });
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await Promise.all([page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 }), page.click('button[type="submit"]')]);
  await page.waitForLoadState("networkidle");
}

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const shots = [];
  const newContext = () => browser.newContext({ viewport: { width: VIEW_W, height: VIEW_H }, deviceScaleFactor: 1, locale: "ca-ES", isMobile: IS_PHONE, hasTouch: IS_PHONE });
  const snap = async (page, name, note) => {
    await page.screenshot({ path: path.join(OUT, `${PREFIX}${name}.png`), fullPage: false });
    shots.push({ name, url: page.url().replace(BASE, ""), note });
    console.log("captured", name, note);
  };

  // demo@: ids reals.
  const ctx = await newContext();
  const page = await ctx.newPage();
  await login(page, EMAIL, PASSWORD);
  const me = await (await page.request.get(BASE + "/bff/auth/me")).json();
  const companyId = me.memberships[0].company_id;
  const workspaces = await (await page.request.get(`${BASE}/bff/companies/${companyId}/workspaces`)).json();
  const ws = workspaces[0];

  // S1 · Càrrega (C1): la fitxa amb l'API retinguda des del navegador, en una pàgina pròpia.
  const slow = await ctx.newPage();
  await slow.route("**/bff/tenders/**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 8000));
    await route.continue().catch(() => undefined);
  });
  await slow.goto(BASE + `/licitacions/${ws.tender_id}`, { waitUntil: "commit" });
  await slow.waitForTimeout(1200);
  await snap(slow, "s1-loading", "Càrrega: PageSkeleton de la fitxa (API retinguda 8 s des del navegador)");
  await slow.close();

  // S2 · Error / 404 (E3): una candidatura que no existeix.
  await page.goto(BASE + "/candidatures/00000000-0000-0000-0000-000000000000", { waitUntil: "load" });
  await page.waitForTimeout(2500);
  await snap(page, "s2-404", "No existeix (E3): candidatura inexistent");

  // S3 · Permís / desactivat (E2): Tancar amb l'aprovació desactivada pel contracte real.
  await page.goto(BASE + `/candidatures/${ws.id}?pas=tancar`, { waitUntil: "load" });
  await page.waitForTimeout(3500);
  await snap(page, "s3-disabled", "Acció visible i desactivada amb la raó (aprovació del paquet)");

  // S4 · Buit amb filtre (B4): Oportunitats sense cap resultat.
  await page.goto(BASE + "/licitacions?q=zzzz-cap-resultat-zzzz", { waitUntil: "load" });
  await page.waitForTimeout(2500);
  await snap(page, "s4-empty-filter", "Buit (B4): cerca sense resultats a Oportunitats");
  await ctx.close();

  // S5 · Buit de feina (B2) amb l'altre usuari del seed, si hi és.
  if (OTHER_PASSWORD) {
    const other = await newContext();
    const op = await other.newPage();
    try {
      await login(op, OTHER_EMAIL, OTHER_PASSWORD);
      await op.goto(BASE + "/candidatures", { waitUntil: "load" });
      await op.waitForTimeout(2500);
      await snap(op, "s5-empty-work", `Buit (B2): ${OTHER_EMAIL} sense cap candidatura`);
      await op.goto(BASE + "/avui", { waitUntil: "load" });
      await op.waitForTimeout(2500);
      await snap(op, "s6-empty-config", `Buit (B1): Avui de ${OTHER_EMAIL} sense perfil`);
    } catch (error) {
      console.warn("second user skipped:", error.message);
    }
    await other.close();
  }

  fs.writeFileSync(path.join(OUT, `${PREFIX}states-manifest.json`), JSON.stringify({ base: BASE, capturedAt: new Date().toISOString(), viewport: `${VIEW_W}x${VIEW_H}`, shots }, null, 2));
  await browser.close();
}

main().catch((err) => { console.error(err); process.exit(1); });
