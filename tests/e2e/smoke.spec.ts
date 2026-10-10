import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

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
    await expect(page.getByText("Brak lokalizacji do wyświetlenia")).toBeVisible();
    await page.getByRole("button", { name: "Mapa" }).click();
  } else {
    await expect(page.getByRole("navigation", { name: "Główna nawigacja" })).toContainText("Źródła danych");
    await expect(page.getByText("Brak lokalizacji do wyświetlenia")).toBeVisible();
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
