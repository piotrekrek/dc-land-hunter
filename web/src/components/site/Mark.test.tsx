import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { markForConfidence } from "@/lib/sites";

import { Mark, MarkLegend } from "./Mark";

describe("Mark", () => {
  it.each([
    ["ok", "sprawdzone w źródle"],
    ["est", "szacunek"],
    ["unk", "brak danych"],
    ["bad", "nie spełnia warunku"],
  ] as const)("%s is announced as a word, not only a colour", (kind, label) => {
    render(<Mark kind={kind} />);
    expect(screen.getByRole("img", { name: label })).toBeInTheDocument();
  });

  it("maps the confidence of a fact to a mark", () => {
    expect(markForConfidence("verified")).toBe("ok");
    expect(markForConfidence("estimated")).toBe("est");
    expect(markForConfidence("missing")).toBe("unk");
  });

  it("explains all four marks in a legend", () => {
    render(<MarkLegend />);
    const legend = screen.getByLabelText("Znaczenie znaków");
    expect(within(legend).getAllByRole("img")).toHaveLength(4);
  });
});
