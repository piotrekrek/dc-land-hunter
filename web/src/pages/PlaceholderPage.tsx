import { Construction } from "lucide-react";

import { PageScroll } from "@/components/PageScroll";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

interface Props {
  title: string;
  phase: number;
  description: string;
  bullets: string[];
}

export function PlaceholderPage({ title, phase, description, bullets }: Props) {
  return (
    <PageScroll label={title}>
      <div className="mx-auto max-w-3xl p-4 md:p-6">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold">{title}</h1>
          <Badge variant="accent">Faza {phase}</Badge>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">{description}</p>
        <Card className="mt-4 flex gap-3 p-4">
          <Construction className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
          <div>
            <p className="text-sm font-medium">Ten ekran jest w przygotowaniu. Planowany zakres:</p>
            <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
              {bullets.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        </Card>
      </div>
    </PageScroll>
  );
}
