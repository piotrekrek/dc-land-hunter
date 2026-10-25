import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProfileVerdict } from "@/api/client";

import { ProfileCards } from "./ProfileCards";

const base: ProfileVerdict = { profile: "built", category: "green", score: 82, blockers: [], to_check: [], positives: [] };

const profiles: Record<string, ProfileVerdict> = {
  built: {
    ...base,
    category: "red",
    score: 40,
    blockers: [{ key: "flood", text_pl: "strefa zalewowa 10% lub 1%: 62% terenu", source_id: "isok", as_of: "2026-10-01T00:00:00+00:00" }],
    to_check: [{ key: "road_access", text_pl: "Dostęp do drogi publicznej", blocking: false }],
  },
  container: {
    ...base,
    profile: "container",
    category: "manual",
    score: 60,
    to_check: [{ key: "zoning", text_pl: "Brak planu miejscowego w rejestrze", blocking: true }],
    positives: [{ key: "gpz110", text_pl: "GPZ 110 kV w odległości 0,8 km", source_id: null, as_of: null }],
  },
};

describe("ProfileCards", () => {
  it("renders nothing when the site has no profiles", () => {
    const { container } = render(<ProfileCards profiles={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("leads each card with its deciding reason and a word, not only a colour", () => {
    render(<ProfileCards profiles={profiles} />);
    const built = screen.getByTestId("profile-built");
    expect(built).toHaveAttribute("data-category", "red");
    expect(within(built).getByText("Odpada")).toBeInTheDocument();
    expect(built).toHaveTextContent("strefa zalewowa 10% lub 1%: 62% terenu");
    const container = screen.getByTestId("profile-container");
    expect(within(container).getByText("Do weryfikacji")).toBeInTheDocument();
    expect(container).toHaveTextContent("Brak planu miejscowego w rejestrze");
  });

  it("explains in 'Dlaczego?' with source and date, and says what the result hangs on", () => {
    render(<ProfileCards profiles={profiles} />);
    const built = screen.getByTestId("profile-built");
    expect(built).toHaveTextContent("źródło: isok, stan na 01.10.2026");
    expect(screen.getByTestId("profile-container")).toHaveTextContent("od tego zależy wynik");
  });

  it("warns that power figures are estimates", () => {
    render(<ProfileCards profiles={profiles} />);
    expect(screen.getByTestId("profiles")).toHaveTextContent("wiąże ekspertyza do warunków przyłączenia");
  });
});
