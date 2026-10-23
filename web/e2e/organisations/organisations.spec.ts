// Slice O's screens against the live local API and database (no stubs). Needs E2E_LIVE_ADMIN_DB (the local test
// database's admin URL) and the API running behind the preview server (API_DEV_ORIGIN); without them the file skips.
// seed.py arranges a team, its people, cases and signed-in sessions; each test signs in as one of them.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

const ADMIN_DB = process.env.E2E_LIVE_ADMIN_DB;
// Invite links must be https or http://localhost (the mail template checks), so the app is opened as localhost.
const BASE = "http://localhost:4173";
type World = { team: string; team_name: string; team_case: string; leaver_case: string; own_case: string; sessions: Record<string, { token: string; csrf: string; person: string }> };

test.skip(!ADMIN_DB, "needs the live local API and database (E2E_LIVE_ADMIN_DB)");
test.describe.configure({ mode: "serial", timeout: 90_000 });
test.use({ baseURL: BASE });
// Today can take several seconds where the file store is unreachable, so the Leave page (which lists named cases from it) gets longer.

let world: World;
test.beforeAll(() => {
  const out = execFileSync("poetry", ["run", "python", resolve(HERE, "seed.py")], {
    cwd: resolve(HERE, "../../../api"),
    env: { ...process.env, TEST_DATABASE_ADMIN_URL: ADMIN_DB },
    encoding: "utf8",
  });
  world = JSON.parse(out.trim().split("\n").pop()!) as World;
});

async function signInAs(context: BrowserContext, who: string) {
  const s = world.sessions[who]!;
  await context.addCookies([
    { name: "av_session", value: s.token, url: BASE, httpOnly: true, sameSite: "Strict" },
    { name: "av_csrf", value: s.csrf, url: BASE, sameSite: "Strict" },
  ]);
}

async function accessible(page: Page) {
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
  const small = await page.$$eval("main button, .av-page button", (buttons) =>
    buttons.filter((b) => (b as HTMLElement).offsetParent !== null && b.getBoundingClientRect().height < 51.5).map((b) => b.textContent),
  );
  expect(small).toEqual([]);
  const body = await page.$$eval(".av-page p", (ps) => ps.filter((p) => (p as HTMLElement).offsetParent !== null && !p.classList.contains("av-small")).map((p) => parseFloat(getComputedStyle(p).fontSize)));
  expect(Math.min(...body)).toBeGreaterThanOrEqual(17);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
}

test("S27: an owner sees the people, invites someone, cancels the invite and changes a role", async ({ page, context }) => {
  await signInAs(context, "owner");
  await page.goto(`/organisations/${world.team}`);
  await expect(page.getByRole("heading", { level: 1, name: world.team_name })).toBeVisible();
  await expect(page.getByRole("region", { name: /^People \(4\)$/ })).toBeVisible();
  await expect(page.getByText("Eve Expert")).toBeVisible();

  await page.getByLabel("Invite someone by email").fill("new.person@example.test");
  await page.getByRole("button", { name: "Show waiting invites" }).click();
  await page.getByLabel("Their role").selectOption("assistant");
  await page.getByRole("button", { name: "Send invite" }).click();
  await expect(page.getByText("We've emailed an invite to new.person@example.test.")).toBeVisible();
  await expect(page.getByText("new.person@example.test", { exact: true })).toBeVisible();
  await accessible(page);

  await page.getByRole("button", { name: "Cancel the invite to new.person@example.test" }).click();
  await expect(page.getByText("The invite to new.person@example.test has been cancelled.")).toBeVisible();

  await page.getByRole("button", { name: "Change role for Eve Expert" }).click();
  await page.getByLabel("New role for Eve Expert").selectOption("viewer");
  await page.getByRole("button", { name: "Save role" }).click();
  await expect(page.getByText("Eve Expert is now Viewer.")).toBeVisible();
  await page.getByRole("button", { name: "Change role for Eve Expert" }).click();
  await page.getByLabel("New role for Eve Expert").selectOption("expert");
  await page.getByRole("button", { name: "Save role" }).click();
  await expect(page.getByText("Eve Expert is now Expert.")).toBeVisible();

  await page.getByRole("button", { name: "Change", exact: true }).click();
  await page.getByLabel("Years to keep records, at most").fill("8");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("8 years")).toBeVisible();
});

test("S27: a team's closing refuses while a case is open", async ({ page, context }) => {
  await signInAs(context, "owner");
  await page.goto(`/organisations/${world.team}`);
  await page.getByRole("button", { name: "Close this team" }).click();
  await page.getByRole("button", { name: "Yes, close it" }).click();
  await expect(page.getByRole("alert")).toContainText("still being worked on");
});

test("Leave: lists the named cases the person keeps, then leaves", async ({ page, context }) => {
  await signInAs(context, "leaver");
  await page.goto(`/organisations/${world.team}/leave`);
  await expect(page.getByRole("heading", { level: 1, name: `Leave ${world.team_name}?` })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Case Z")).toBeVisible();
  await accessible(page);
  await page.getByRole("button", { name: `Leave ${world.team_name}` }).click();
  await expect(page).toHaveURL(/\/account$/);
});

test("Invite accept: a link that cannot be used says so", async ({ page, context }) => {
  await signInAs(context, "expert");
  await page.goto("/invites/abcdefghijklmnopqrstuvwxyz0123456789");
  await expect(page.getByRole("heading", { level: 1, name: "Join a team on Avodeck" })).toBeVisible();
  await page.getByRole("button", { name: "Join the team" }).click();
  await expect(page.getByRole("alert")).toContainText("This invite can't be used.");
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations).toEqual([]);
});

test("Case: an admin can change the named expert", async ({ page, context }) => {
  await signInAs(context, "admin");
  await page.goto(`/cases/${world.team_case}/team`);
  await page.getByRole("button", { name: "Change named expert" }).click();
  await accessible(page);
  await page.getByLabel("New named expert").selectOption({ label: "Olivia Owner" });
  await page.getByRole("button", { name: "Move the case" }).click();
  await expect(page.getByText("The case now belongs to Olivia Owner.")).toBeVisible();
});

test("Case: the named expert lets support open the case and stops it again", async ({ page, context }) => {
  await signInAs(context, "leaver");
  // The leaver has left; the named-expert case they keep still works for them (F31).
  await page.goto(`/cases/${world.leaver_case}/team`);
  await page.getByLabel("Why do you want help?").selectOption("help");
  await page.getByRole("button", { name: "Let support open this case" }).click();
  await expect(page.getByText(/Time left: 23 hours/)).toBeVisible();
  await accessible(page);
  await page.getByRole("button", { name: "Stop support opening this case" }).click();
  await expect(page.getByText("Support can no longer open this case.")).toBeVisible();
});

test("Support console: staff find a case and see its details only; others see not found", async ({ page, context }) => {
  await signInAs(context, "staff");
  await page.goto("/support");
  await page.getByLabel("Case number or email").fill(world.team_case);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("list", { name: "Cases found" })).toContainText("No access given");
  await accessible(page);
  await expect(page.getByRole("link", { name: "Open the case" })).toHaveCount(0);
});

test("Support console: a person who is not staff gets the not found page", async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE });
  await signInAs(context, "owner");
  const page = await context.newPage();
  await page.goto("/support");
  await page.getByLabel("Case number or email").fill(world.team_case);
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "We can't find that page" })).toBeVisible();
  await context.close();
});

test("Account: the custodian is saved, and closing lists the unserved cases", async ({ page, context }) => {
  await signInAs(context, "expert");
  await page.goto("/account/custodian");
  await page.getByLabel("Their name").fill("Dr Amira Bello");
  await page.getByLabel("Their email").fill("a.bello@example.test");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved. Dr Amira Bello is your records custodian.")).toBeVisible();
  await accessible(page);

  await page.goto("/account/close");
  await expect(page.getByRole("list", { name: "Cases in your own practice that will be deleted" })).toContainText("Case Z");
  await accessible(page);
  await page.getByRole("button", { name: "Close account" }).click();
  await page.getByRole("button", { name: "Yes, close my account" }).click();
  await expect(page.getByRole("alert")).toContainText("Export the cases marked below");
  await expect(page.getByText("Not exported yet")).toBeVisible();
});
