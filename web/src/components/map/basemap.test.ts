import { describe, expect, it } from "vitest";

import { proxyBasemapRequest } from "./basemap";

describe("proxyBasemapRequest", () => {
  it("routes OpenFreeMap requests through our origin", () => {
    expect(proxyBasemapRequest("https://tiles.openfreemap.org/styles/positron", "https://x.pl").url).toBe(
      "https://x.pl/basemap/styles/positron",
    );
    expect(proxyBasemapRequest("https://tiles.openfreemap.org/planet/2025/3/4/5.pbf", "https://x.pl").url).toBe(
      "https://x.pl/basemap/planet/2025/3/4/5.pbf",
    );
  });

  it("leaves other URLs alone", () => {
    expect(proxyBasemapRequest("https://x.pl/tiles/lines/1/2/3", "https://x.pl").url).toBe(
      "https://x.pl/tiles/lines/1/2/3",
    );
  });
});
