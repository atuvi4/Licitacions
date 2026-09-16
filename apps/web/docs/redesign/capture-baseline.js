// Baseline capture: logs in against the local demo stack and screenshots the
// current screens at 1440x900 (viewport) and full page. Uses the installed
// Google Chrome via playwright-core (no browser download).
//
// Credentials come from the environment: DEMO_EMAIL (defaults to the seed
// user) and DEMO_PASSWORD (required, no default). See BASELINE.md.
const { chromium } = require("playwright-core");
const fs = require("fs");
const path = require("path");

const BASE = process.env.BASE_URL || "http://localhost:3000";
const OUT = process.env.OUT_DIR;
const EMAIL = process.env.DEMO_EMAIL || "demo@licitacions.dev";
const PASSWORD = process.env.DEMO_PASSWORD;
const PREFIX = process.env.PREFIX || "";
if (!OUT) throw new Error("OUT_DIR is required");
if (!PASSWORD) throw new Error("DEMO_PASSWORD is required (the demo seed password; never hardcode it)");
fs.mkdirSync(OUT, { recursive: true });

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: "ca-ES" });
  const page = await context.newPage();
  const shots = [];

  async function snap(name, url, opts = {}) {
    await page.goto(BASE + url, { waitUntil: "networkidle", timeout: 60000 });
    if (opts.settle) await page.waitForTimeout(opts.settle);
    await page.screenshot({ path: path.join(OUT, `${PREFIX}${name}.png`), fullPage: false });
    await page.screenshot({ path: path.join(OUT, `${PREFIX}${name}-full.png`), fullPage: true });
    shots.push({ name, url: page.url().replace(BASE, "") });
    console.log("captured", name, page.url());
  }

  // 1. Login page (unauthenticated).
  await snap("01-login", "/login", { settle: 500 });
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await Promise.all([
    page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60000 }),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForLoadState("networkidle");

  // Discover ids through the BFF with the session cookie.
  const me = await (await page.request.get(BASE + "/bff/auth/me")).json();
  const companyId = me.memberships[0].company_id;
  const workspaces = await (await page.request.get(`${BASE}/bff/companies/${companyId}/workspaces`)).json();
  const ws = workspaces[0];
  const tenderId = ws.tender_id;
  const tender = await (await page.request.get(`${BASE}/bff/tenders/${tenderId}`)).json();
  const doc = tender.documents.find((d) => d.latest_file && d.latest_file.extraction_id) || tender.documents[0];

  await snap("02-dashboard", "/dashboard", { settle: 1500 });
  await snap("03-licitacions", "/licitacions", { settle: 1500 });
  await snap("04-licitacio-fitxa", `/licitacions/${tenderId}`, { settle: 2500 });
  if (doc) await snap("05-licitacio-plec-text", `/licitacions/${tenderId}/documents/${doc.id}`, { settle: 2000 });
  await snap("06-candidatures", "/candidatures", { settle: 1500 });
  await snap("07-candidatura", `/candidatures/${ws.id}`, { settle: 3000 });
  await snap("08-empresa", "/empresa", { settle: 1500 });
  await snap("09-empresa-solvencia", "/empresa/solvencia", { settle: 1500 });
  await snap("10-experiencia", "/experiencia", { settle: 1500 });
  await snap("11-documents", "/documents", { settle: 1500 });

  fs.writeFileSync(path.join(OUT, `${PREFIX}manifest.json`), JSON.stringify({ base: BASE, capturedAt: new Date().toISOString(), companyId, tenderId, workspaceId: ws.id, documentId: doc ? doc.id : null, shots }, null, 2));
  await browser.close();
}

main().catch((err) => { console.error(err); process.exit(1); });
