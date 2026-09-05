import { afterEach, expect, it, vi } from "vitest";
import { upsertContact } from "./api";

afterEach(() => vi.unstubAllGlobals());

it("does not replay a create when its response is lost", async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError("response lost"));
  vi.stubGlobal("fetch", fetch);
  await expect(upsertContact("", { name: "Synthetic contact" })).rejects.toThrow("may have completed");
  expect(fetch).toHaveBeenCalledTimes(1);
});
