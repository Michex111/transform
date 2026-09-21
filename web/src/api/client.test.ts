import { afterEach, describe, expect, it, vi } from "vitest";

const ABSOLUTE_API_BASE = "https://transform-api-7b3g.onrender.com/api";

/**
 * Load a fresh copy of the client module with `VITE_API_BASE_URL` stubbed.
 *
 * `API_BASE` — and therefore everything `resolveServerPath` and the SSE
 * helpers build — is computed once at module load, so each case needs its own
 * module instance. This is exactly the same-origin -> cross-origin switch the
 * SPA now makes between local dev (`/api`) and production (absolute origin).
 */
async function loadClient(apiBase?: string) {
  vi.resetModules();
  if (apiBase === undefined) {
    vi.unstubAllEnvs();
  } else {
    vi.stubEnv("VITE_API_BASE_URL", apiBase);
  }
  return await import("./client");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("API_BASE", () => {
  it("defaults to the same-origin /api prefix", async () => {
    const { API_BASE } = await loadClient();
    expect(API_BASE).toBe("/api");
  });

  it("honours an absolute origin and strips a trailing slash", async () => {
    const { API_BASE } = await loadClient(`${ABSOLUTE_API_BASE}/`);
    expect(API_BASE).toBe(ABSOLUTE_API_BASE);
  });
});

describe("resolveServerPath", () => {
  it("keeps a single /api prefix for the relative dev base", async () => {
    const { resolveServerPath } = await loadClient("/api");
    expect(resolveServerPath("/api/v1/files/7/stream")).toBe("/api/v1/files/7/stream");
  });

  it("rewrites server-relative paths onto the absolute API origin", async () => {
    const { resolveServerPath } = await loadClient(ABSOLUTE_API_BASE);
    // The backend emits `/api/...` paths (e.g. the file download stream).
    expect(resolveServerPath("/api/v1/files/7/stream")).toBe(
      `${ABSOLUTE_API_BASE}/v1/files/7/stream`,
    );
    // A path without the `/api` prefix still resolves against the API origin.
    expect(resolveServerPath("v1/files/7/stream")).toBe(
      `${ABSOLUTE_API_BASE}/v1/files/7/stream`,
    );
  });

  it("passes absolute URLs through untouched (presigned B2 downloads)", async () => {
    const { resolveServerPath } = await loadClient(ABSOLUTE_API_BASE);
    const presigned = "https://s3.us-west-004.backblazeb2.com/bucket/key?X-Amz-Signature=abc";
    expect(resolveServerPath(presigned)).toBe(presigned);
  });

  it("tolerates a trailing slash on the configured base", async () => {
    const { resolveServerPath } = await loadClient("/api/");
    expect(resolveServerPath("/api/v1/files/7/stream")).toBe("/api/v1/files/7/stream");
  });
});

describe("jobOutputFilename", () => {
  it("names the download after the produced object's extension", async () => {
    const { jobOutputFilename } = await loadClient();
    // A multi-page pdf -> png job is delivered as a zip of page images.
    expect(
      jobOutputFilename({
        input_file: "uploads/report.pdf",
        target_format: "png",
        output_file: "output/user/1/job/job-1/report.zip",
      }),
    ).toBe("report.zip");
  });

  it("falls back to the target format when no output key is stored yet", async () => {
    const { jobOutputFilename } = await loadClient();
    expect(
      jobOutputFilename({
        input_file: "uploads/report.pdf",
        target_format: "png",
        output_file: null,
      }),
    ).toBe("report.png");
  });

  it("keeps a plain target-format output name", async () => {
    const { jobOutputFilename } = await loadClient();
    expect(
      jobOutputFilename({
        input_file: "uploads/report.pdf",
        target_format: "jpg",
        output_file: "output/user/1/job/job-1/report.jpg",
      }),
    ).toBe("report.jpg");
  });

  it("defaults the stem when the input path is unknown", async () => {
    const { jobOutputFilename } = await loadClient();
    expect(jobOutputFilename({ input_file: null, target_format: "png" })).toBe("converted.png");
  });
});

describe("cross-origin request URLs", () => {
  it("builds the guest SSE URL against the absolute API origin", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, body: null });
    vi.stubGlobal("fetch", fetchMock);

    const { api, API_BASE } = await loadClient(ABSOLUTE_API_BASE);
    api.guestSubscribeToJob("job-1", "guest-token", {
      onProgress: () => {},
      onError: () => {},
      onDone: () => {},
    });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0][0]).toBe(
      `${API_BASE}/guest/events/jobs/job-1?guest_token=guest-token`,
    );
  });

  it("sends the Authorization header to the absolute API origin for authed SSE", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 500, body: null });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => (key === "transform_access_token" ? "tok-123" : null),
      setItem: () => {},
      removeItem: () => {},
    });

    const { api, API_BASE } = await loadClient(ABSOLUTE_API_BASE);
    api.subscribeToJob("job-2", {
      onProgress: () => {},
      onError: () => {},
      onDone: () => {},
    });

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(`${API_BASE}/v1/events/jobs/job-2`);
    expect((options as RequestInit).headers).toEqual({ Authorization: "Bearer tok-123" });
  });
});
