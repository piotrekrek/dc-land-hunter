import { Link } from "react-router";

import { PageScroll } from "@/components/PageScroll";

export function NotFoundPage() {
  return (
    <PageScroll label="Nie znaleziono strony">
      <div className="mx-auto flex max-w-3xl flex-col gap-2 p-4 md:p-6">
        <h1 className="text-xl font-semibold">Nie znaleziono strony</h1>
        <p className="text-sm text-muted-foreground">Ten adres nie istnieje.</p>
        <Link to="/" className="mt-2 w-fit text-sm text-accent underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-accent">
          Wróć do: Mapa i lista
        </Link>
      </div>
    </PageScroll>
  );
}
