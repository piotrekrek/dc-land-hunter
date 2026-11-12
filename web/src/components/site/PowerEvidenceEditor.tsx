import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format";
import { EVIDENCE_LEVELS, evidenceLabel, hasExpiry, type PowerEvidence, type PowerEvidenceLevel } from "@/lib/rtb";

const isHttp = (value: string) => /^https?:\/\/\S+$/i.test(value.trim());

/**
 * How firmly the power is documented. Typed in by the team, never derived from data: the RTB annex caps the power
 * workstream at yellow below "Warunki wydane". No free text on purpose (RODO): a level, two dates and a link.
 */
export function PowerEvidenceEditor({ siteId, evidence }: { siteId: string; evidence: PowerEvidence }) {
  const client = useQueryClient();
  const [level, setLevel] = useState<PowerEvidenceLevel>(evidence.level);
  const [documentDate, setDocumentDate] = useState(evidence.document_date ?? "");
  const [expiry, setExpiry] = useState(evidence.conditions_expiry ?? "");
  const [url, setUrl] = useState(evidence.attachment_url ?? "");
  const path = { params: { path: { site_id: siteId } } };
  const refresh = () => client.invalidateQueries({ queryKey: ["site", siteId] });
  const save = useMutation({
    mutationFn: () =>
      unwrap(
        api.PUT("/api/sites/{site_id}/power-evidence", {
          ...path,
          body: {
            level,
            document_date: documentDate || null,
            conditions_expiry: hasExpiry(level) && expiry ? expiry : null,
            attachment_url: url.trim() || null,
          },
        }),
      ),
    onSuccess: refresh,
  });
  const clear = useMutation({
    mutationFn: async () => {
      const { response } = await api.DELETE("/api/sites/{site_id}/power-evidence", path);
      if (!response.ok) throw new Error(`API ${response.status}`);
    },
    onSuccess: () => {
      setLevel("rumour");
      setDocumentDate("");
      setExpiry("");
      setUrl("");
      return refresh();
    },
  });
  const badLink = url.trim() !== "" && !isHttp(url);

  return (
    <section className="mt-6 rounded-md border bg-surface p-4" aria-labelledby="evidence-title" data-testid="power-evidence">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="evidence-title" className="text-base font-semibold">
          Dowód mocy
        </h2>
        <Badge variant={evidence.recorded ? "accent" : "neutral"}>
          {evidence.recorded ? evidenceLabel(evidence.level) : "Plotka (nic nie wpisano)"}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Wpisywane ręcznie, nigdy z danych. Bez wydanych warunków przyłączenia moc w aneksie RTB nie jest lepsza niż żółta.
        {evidence.recorded && evidence.updated_at && ` Zapisano ${formatDate(evidence.updated_at)}.`}
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="block text-sm">
          Poziom dowodu
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as PowerEvidenceLevel)}
            className="mt-1 block h-9 w-full rounded-md border bg-surface px-2 text-sm"
          >
            {EVIDENCE_LEVELS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-sm">
          Data dokumentu
          <input
            type="date"
            value={documentDate}
            onChange={(e) => setDocumentDate(e.target.value)}
            className="mt-1 block h-9 w-full rounded-md border bg-surface px-2 text-sm"
          />
        </label>
        {hasExpiry(level) && (
          <label className="block text-sm">
            Warunki ważne do
            <input
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="mt-1 block h-9 w-full rounded-md border bg-surface px-2 text-sm"
            />
          </label>
        )}
        <label className="block text-sm sm:col-span-2">
          Link do dokumentu
          <input
            type="url"
            inputMode="url"
            value={url}
            maxLength={2000}
            placeholder="https://"
            onChange={(e) => setUrl(e.target.value)}
            aria-invalid={badLink}
            className="mt-1 block h-9 w-full rounded-md border bg-surface px-2 text-sm"
          />
        </label>
      </div>
      {badLink && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Link musi zaczynać się od http:// lub https://.
        </p>
      )}
      {(save.isError || clear.isError) && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Nie udało się zapisać dowodu mocy. Sprawdź daty i link, potem spróbuj ponownie.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" disabled={save.isPending || badLink} onClick={() => save.mutate()}>
          Zapisz dowód mocy
        </Button>
        {evidence.recorded && (
          <Button variant="ghost" size="sm" disabled={clear.isPending} onClick={() => clear.mutate()}>
            Wróć do „Plotka”
          </Button>
        )}
      </div>
    </section>
  );
}
