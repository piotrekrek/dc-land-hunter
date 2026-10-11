import AxeBuilder from "@axe-core/playwright";
import { expect, test as base, type Page } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "e2e-admin@example.org";
const PASSWORD = process.env.E2E_PASSWORD ?? "e2e-password-123";

/** Every test except the login tests starts signed in (through the real login form). */
const test = base.extend({
  page: async ({ page }, use) => {
    await page.goto("/logowanie");
    await page.getByLabel("E-mail").fill(EMAIL);
    await page.getByLabel("Hasło").fill(PASSWORD);
    await page.getByRole("button", { name: "Zaloguj się" }).click();
    await expect(page.getByRole("heading", { name: "Lokalizacje" })).toBeVisible();
    await use(page);
  },
});

const anonymous = base;

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .exclude(".maplibregl-canvas") // WebGL canvas: covered by the map region's label
    .analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
}

test("home shows the Polish shell, the map and an empty list", async ({ page, isMobile }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("DC Land Hunter");
  if (isMobile) {
    await expect(page.getByText("Brak lokalizacji dla tych filtrów")).toBeVisible();
    await page.getByRole("button", { name: "Mapa" }).click();
  } else {
    await expect(page.getByRole("navigation", { name: "Główna nawigacja" })).toContainText("Źródła danych");
    await expect(page.getByText("Brak lokalizacji dla tych filtrów")).toBeVisible();
    await expect(page.getByTestId("api-status")).toContainText("Połączono z API");
  }
  await expect(page.locator(".maplibregl-canvas")).toBeVisible();
  await expect(page).toHaveURL(/#mapa=/); // map position is kept in the URL
  await expectNoSeriousA11yViolations(page);
});

test("sources page lists every configured source", async ({ page, isMobile }) => {
  await page.goto("/zrodla");
  await expect(page.getByRole("heading", { name: "Źródła danych" })).toBeVisible();
  if (!isMobile) {
    await expect(page.getByTestId("source-row")).toHaveCount(28);
    await expect(page.getByTestId("source-row").filter({ hasText: "egib" })).toContainText("kluczowe");
  }
  await expectNoSeriousA11yViolations(page);
});

test("settings page shows the scoring weights", async ({ page }) => {
  await page.goto("/ustawienia");
  await expect(page.getByText("Sieć elektroenergetyczna")).toBeVisible();
  await expect(page.getByText("60 MW").first()).toBeVisible();
});

test("theme toggle switches to dark mode", async ({ page }) => {
  await page.goto("/zrodla");
  const html = page.locator("html");
  const wasDark = (await html.getAttribute("class"))?.includes("dark") ?? false;
  await page.getByRole("button", { name: wasDark ? "Włącz jasny motyw" : "Włącz ciemny motyw" }).click();
  await expect(html).toHaveClass(wasDark ? /^(?!.*dark)/ : /dark/);
  await expectNoSeriousA11yViolations(page);
});


test("admin sees 'Uruchom teraz' and can queue a check", async ({ page }) => {
  await page.goto("/zrodla");
  await page.getByRole("button", { name: "Uruchom teraz" }).click();
  await expect(page.getByRole("status")).toContainText("dodane do kolejki");
});

test("logging out returns to the login page and protects the app", async ({ page }) => {
  await page.getByRole("button", { name: "Wyloguj się" }).click();
  await expect(page.getByRole("heading", { name: "Logowanie" })).toBeVisible();
  await page.goto("/zrodla");
  await expect(page).toHaveURL(/\/logowanie$/);
  const api = await page.request.get("/api/sources");
  expect(api.status()).toBe(401);
});

anonymous("anonymous visitors are redirected to the login page, which has no a11y violations", async ({ page }) => {
  await page.goto("/ustawienia");
  await expect(page).toHaveURL(/\/logowanie$/);
  await expect(page.getByRole("heading", { name: "Logowanie" })).toBeVisible();
  await expectNoSeriousA11yViolations(page);
});

anonymous("a wrong password shows an error and does not log in", async ({ page }) => {
  await page.goto("/logowanie");
  await page.getByLabel("E-mail").fill(EMAIL);
  await page.getByLabel("Hasło").fill("definitely-wrong-password");
  await page.getByRole("button", { name: "Zaloguj się" }).click();
  await expect(page.getByRole("alert")).toContainText("Nieprawidłowy e-mail lub hasło");
  await expect(page).toHaveURL(/\/logowanie$/);
});
