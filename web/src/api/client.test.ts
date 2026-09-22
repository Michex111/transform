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

/* ------------------------------------------------------------------ *
 * Email verification
 * ------------------------------------------------------------------ */

/** A minimal `Response` stand-in; only the fields the client reads. */
function jsonResponse(status: number, body: unknown, statusText = ""): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: async () => body,
  } as unknown as Response;
}

/** `api.request` reads the token from localStorage on every call. */
function stubLocalStorage() {
  vi.stubGlobal("localStorage", {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  });
}

describe("email verification client", () => {
  it("POSTs the token to /users/verify-email", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(200, { ok: true, already_verified: false, username: "ada", message: "Done." }),
      );
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const result = await api.verifyEmail("tok-abc");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/users/verify-email");
    expect((options as RequestInit).method).toBe("POST");
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({ token: "tok-abc" });
    expect(result.ok).toBe(true);
    expect(result.username).toBe("ada");
  });

  it("POSTs the address to /users/resend-verification", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(202, { message: "On its way." }));
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const result = await api.resendVerification("ada@example.com");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/users/resend-verification");
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({
      email: "ada@example.com",
    });
    // 202 is a success: the endpoint must not reveal whether the address exists.
    expect(result.message).toBe("On its way.");
  });

  it("turns a structured detail into an ApiError carrying the code and fields", async () => {
    // Without this the sign-in page cannot tell "unverified" apart from a bad
    // password, and has no address to offer a resend to.
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(403, {
        detail: {
          code: "EMAIL_NOT_VERIFIED",
          email: "ada@example.com",
          message: "Your email address is not verified yet.",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api, ApiError } = await loadClient();
    await expect(api.login("ada", "Sup3rSecret!")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      code: "EMAIL_NOT_VERIFIED",
    });

    const err = await api.login("ada", "Sup3rSecret!").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as Error).message).toBe("Your email address is not verified yet.");
    expect((err as { details?: Record<string, unknown> }).details?.email).toBe("ada@example.com");
  });

  it("never renders a structured detail as '[object Object]'", async () => {
    // The regression this guards: reading only the string shape turned a
    // structured error into the literal text "[object Object]".
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(400, { detail: { code: "SOMETHING", message: "Readable text." } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const err = await api.verifyEmail("tok").catch((e: unknown) => e);

    expect((err as Error).message).toBe("Readable text.");
    expect((err as Error).message).not.toContain("object Object");
  });

  it("keeps a plain string detail readable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(400, { detail: "Plain message." }));
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api, ApiError } = await loadClient();
    const err = await api.verifyEmail("tok").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as Error).message).toBe("Plain message.");
    expect((err as { code?: string }).code).toBeUndefined();
  });

  it("joins a 422 validation detail array into one message", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(422, { detail: [{ msg: "token must not be empty" }, { msg: "too long" }] }),
      );
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const err = await api.verifyEmail("").catch((e: unknown) => e);

    expect((err as Error).message).toBe("token must not be empty, too long");
  });

  it("falls back to the status text when the error body is not JSON", async () => {
    // A proxy's HTML 502 must not surface as a JSON parse error.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const err = await api.verifyEmail("tok").catch((e: unknown) => e);

    expect((err as Error).message).toBe("Bad Gateway");
  });

  it("falls back to the supplied default when there is no detail or status text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, {}, ""));
    vi.stubGlobal("fetch", fetchMock);
    stubLocalStorage();

    const { api } = await loadClient();
    const err = await api.login("ada", "wrong").catch((e: unknown) => e);

    expect((err as Error).message).toBe("Invalid credentials");
  });
});

