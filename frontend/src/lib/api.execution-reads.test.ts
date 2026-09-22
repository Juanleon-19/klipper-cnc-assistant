import { afterEach, expect, test, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

function deferredResponse() {
  let resolve!: (value: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve: (value = {}) => resolve(new Response(JSON.stringify(value), { status: 200 })) };
}

test("overlapping plan and live reads share requests, settled reads stay fresh", async () => {
  const plan = deferredResponse();
  const live = deferredResponse();
  const fetch = vi.fn().mockReturnValueOnce(plan.promise).mockReturnValueOnce(live.promise)
    .mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  const a = api.getJobPlan("p", "s", "superior");
  const b = api.getJobPlan("p", "s", "superior");
  const c = api.getLiveExecution("p", "s", "superior");
  const d = api.getLiveExecution("p", "s", "superior");
  expect(fetch).toHaveBeenCalledTimes(2);
  plan.resolve(); live.resolve();
  await Promise.all([a, b, c, d]);
  await api.getJobPlan("p", "s", "superior");
  expect(fetch).toHaveBeenCalledTimes(3);
});

test("post-mutation reads never join a pending pre-mutation response", async () => {
  const old = deferredResponse();
  const fresh = deferredResponse();
  const fetch = vi.fn().mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(new Response("{}", { status: 200 })).mockReturnValueOnce(fresh.promise);
  vi.stubGlobal("fetch", fetch);
  const first = api.getJobPlan("p", "s", "superior");
  await api.prepareJobRun("p", "s", "superior");
  const next = api.getJobPlan("p", "s", "superior");
  old.resolve({ version: 1 });
  await first;
  const joined = api.getJobPlan("p", "s", "superior");
  expect(fetch).toHaveBeenCalledTimes(3);
  fresh.resolve({ version: 2 });
  expect(await next).toEqual({ version: 2 });
  expect(await joined).toEqual({ version: 2 });
});

test("a failed read is removed and retry reaches the server", async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  await expect(api.getJobPlan("p", "s", "superior")).rejects.toThrow("offline");
  await api.getJobPlan("p", "s", "superior");
  expect(fetch).toHaveBeenCalledTimes(2);
});
