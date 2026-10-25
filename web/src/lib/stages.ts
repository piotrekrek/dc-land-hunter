import type { components } from "@/api/schema";

export type ProjectStage = components["schemas"]["ProjectBody"]["stage"];

/** The deal pipeline in board order (brief 8.1 "Moje projekty"). The one place the Polish labels live; Step 5b reads it. */
export const STAGES: ReadonlyArray<{ value: ProjectStage; label: string }> = [
  { value: "new", label: "Nowe" },
  { value: "watching", label: "Obserwuję" },
  { value: "analysis", label: "Analiza" },
  { value: "owner_contact", label: "Kontakt z właścicielem" },
  { value: "negotiation", label: "Negocjacje" },
  { value: "rejected", label: "Odrzucone" },
  { value: "secured", label: "Kupione/Zabezpieczone" },
];

export const stageLabel = (stage: ProjectStage): string => STAGES.find((s) => s.value === stage)?.label ?? stage;
