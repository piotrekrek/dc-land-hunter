import type { components } from "@/api/schema";

export type RtbExport = components["schemas"]["RtbExport"];
export type RtbWorkstream = components["schemas"]["RtbWorkstream"];
export type RtbFact = components["schemas"]["RtbFact"];
export type PowerEvidence = components["schemas"]["PowerEvidenceOut"];
export type PowerEvidenceLevel = PowerEvidence["level"];

/** The five ways the power can be documented, weakest first. Same values as the `power_evidence.level` CHECK. */
export const EVIDENCE_LEVELS: ReadonlyArray<{ value: PowerEvidenceLevel; label: string }> = [
  { value: "rumour", label: "Plotka" },
  { value: "informal_opinion", label: "Nieformalna opinia operatora" },
  { value: "application_filed", label: "Wniosek złożony (opłata i zaliczka)" },
  { value: "conditions_issued", label: "Warunki wydane" },
  { value: "agreement_signed", label: "Umowa podpisana" },
];

export const evidenceLabel = (level: PowerEvidenceLevel): string =>
  EVIDENCE_LEVELS.find((l) => l.value === level)?.label ?? level;

/** Only a granted connection has conditions that expire. */
export const hasExpiry = (level: PowerEvidenceLevel): boolean => level === "conditions_issued" || level === "agreement_signed";

export type Rag = RtbWorkstream["status"];

/** A status is a word as well as a colour, so a black-and-white printout still reads. */
export const RAG_LABEL: Record<Rag, string> = { green: "Zielony", yellow: "Żółty", red: "Czerwony" };
export const RAG_VARIANT: Record<Rag, "ok" | "warn" | "bad"> = { green: "ok", yellow: "warn", red: "bad" };

/** Plain-language values for the enumerated planning facts; anything else prints as stored. */
const VALUE_LABEL: Record<string, string> = {
  mpzp_production: "MPZP produkcyjno-usługowy",
  plan_ogolny_SG: "plan ogólny: strefa gospodarcza",
  procedure_in_progress: "procedura planistyczna w toku",
  unknown: "brak planu w rejestrze",
  other: "przeznaczenie inne",
  mpzp_in_force: "MPZP obowiązuje",
  mpzp_or_zpi: "potrzebny MPZP lub ZPI",
  wz_possible: "WZ możliwa",
  yes: "tak",
  no: "nie",
  partial: "częściowo",
};

const NBSP = "\u00a0";

export function factValue(fact: Pick<RtbFact, "value" | "unit">, format: (n: number) => string): string {
  const { value, unit } = fact;
  if (value === null || value === undefined) return "brak danych";
  if (typeof value === "boolean") return value ? "tak" : "nie";
  if (typeof value === "number") return unit ? `${format(value)}${NBSP}${unit}` : format(value);
  return VALUE_LABEL[value] ?? value;
}

export function downloadJson(name: string, data: unknown): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
