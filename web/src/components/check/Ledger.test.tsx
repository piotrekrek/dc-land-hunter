import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { step } from "@/test/checkFixtures";

import { Ledger, ProgressBar } from "./Ledger";

describe("ProgressBar", () => {
  it("is steps done over all steps and says so in words, with the estimate", () => {
    const steps = [step("a", "done"), step("b", "done"), step("c", "running"), step("d", "waiting")];
    render(<ProgressBar steps={steps} estimateSeconds={70} />);
    const bar = screen.getByRole("progressbar", { name: "Postęp sprawdzenia" });
    expect(bar).toHaveAttribute("aria-valuenow", "2");
    expect(bar).toHaveAttribute("aria-valuemax", "4");
    expect(bar).toHaveAttribute("aria-valuetext", "2 z 4 kroków, ok. 1 min 10 s");
    expect(screen.getByText(/2 z 4 kroków · ok\. 1 min 10 s/)).toBeInTheDocument();
  });

  it("counts a failed step as done, so the bar never stalls on it", () => {
    render(<ProgressBar steps={[step("a", "failed"), step("b", "done")]} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "2");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuetext")).toBe("2 z 2 kroków");
  });

  it("handles a check that has no steps yet", () => {
    render(<ProgressBar steps={[]} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });
});

describe("Ledger", () => {
  it("shows each step with its mark, finding and time", () => {
    render(
      <Ledger
        steps={[
          step("flood", "done", { label_pl: "Strefy zalewowe", finding_pl: "Działka poza strefami zalewowymi.", seconds: 6.5 }),
          step("mining", "running", { label_pl: "Obszary górnicze", seconds: 0.4 }),
          step("zoning", "waiting", { label_pl: "Plany" }),
        ]}
      />,
    );
    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]!).getByText("Działka poza strefami zalewowymi.")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("6,5 s")).toBeInTheDocument();
    expect(within(rows[0]!).getByRole("img", { name: "gotowe" })).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("img", { name: "w toku" })).toBeInTheDocument();
    expect(within(rows[2]!).getByRole("img", { name: "czeka" })).toBeInTheDocument();
  });

  it("says a failed layer is entered as not checked, never as fine", () => {
    render(<Ledger steps={[step("flood", "failed", { label_pl: "Strefy zalewowe", error: "HTTPStatusError: 504 Gateway Timeout" })]} />);
    const row = screen.getByRole("listitem");
    expect(within(row).getByRole("img", { name: "błąd" })).toBeInTheDocument();
    expect(row).toHaveTextContent("504 Gateway Timeout");
    expect(row).toHaveTextContent("nie sprawdzono");
  });
});
