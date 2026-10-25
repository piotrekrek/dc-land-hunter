import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, FolderPlus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { Button } from "@/components/ui/button";
import type { SiteDetail } from "@/lib/sites";
import { STAGES, type ProjectStage } from "@/lib/stages";

type Props = Pick<SiteDetail, "site_id" | "watched" | "stage" | "project_note">;

/** Watch, add to the pipeline, pick the stage, and keep the owner note (personal data: brief 9.6). */
export function ProjectActions({ site_id, watched = false, stage = null, project_note = null }: Props) {
  const client = useQueryClient();
  const path = { params: { path: { site_id } } };
  const refresh = () => client.invalidateQueries({ queryKey: ["site", site_id] });
  const watch = useMutation({
    mutationFn: () => unwrap(watched ? api.DELETE("/api/sites/{site_id}/watch", path) : api.PUT("/api/sites/{site_id}/watch", path)),
    onSuccess: refresh,
  });
  const put = useMutation({
    mutationFn: (body: { stage: ProjectStage; owner_note?: string | null }) =>
      unwrap(api.PUT("/api/sites/{site_id}/project", { ...path, body })),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: async () => {
      const { response } = await api.DELETE("/api/sites/{site_id}/project", path);
      if (!response.ok) throw new Error(`API ${response.status}`);
    },
    onSuccess: refresh,
  });
  const [note, setNote] = useState<string | null>(null);
  const draft = note ?? project_note ?? "";
  const failed = watch.isError || put.isError || remove.isError;

  return (
    <div className="mt-3" data-testid="project-actions">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" aria-pressed={watched} disabled={watch.isPending} onClick={() => watch.mutate()}>
          {watched ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
          {watched ? "Przestań obserwować" : "Obserwuj"}
        </Button>
        {stage === null ? (
          <Button size="sm" disabled={put.isPending} onClick={() => put.mutate({ stage: "new" })}>
            <FolderPlus aria-hidden />
            Dodaj do projektu
          </Button>
        ) : (
          <label className="flex items-center gap-2 text-sm">
            Etap projektu
            <select
              value={stage}
              disabled={put.isPending}
              onChange={(e) => put.mutate({ stage: e.target.value as ProjectStage })}
              className="h-8 rounded-md border bg-surface px-2 text-sm"
            >
              {STAGES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {failed && (
        <p role="alert" className="mt-2 text-sm text-bad">
          Nie udało się zapisać zmiany. Spróbuj ponownie.
        </p>
      )}
      {stage !== null && (
        <div className="mt-3">
          <label className="block text-sm font-medium" htmlFor="project-note">
            Notatka
          </label>
          <textarea
            id="project-note"
            value={draft}
            maxLength={2000}
            rows={3}
            onChange={(e) => setNote(e.target.value)}
            className="mt-1 w-full rounded-md border bg-surface p-2 text-sm"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Notatka może zawierać dane osobowe (RODO). Wpisuj tylko to, co niezbędne, bez numerów telefonów i adresów
            prywatnych. Usunięcie projektu usuwa notatkę na stałe.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={put.isPending || draft === (project_note ?? "")}
              onClick={() => put.mutate({ stage, owner_note: draft }, { onSuccess: () => setNote(null) })}
            >
              Zapisz notatkę
            </Button>
            <Button variant="ghost" size="sm" disabled={remove.isPending} onClick={() => remove.mutate()}>
              <Trash2 aria-hidden />
              Usuń z projektów i skasuj notatkę
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
