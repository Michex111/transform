// Tests for the API client's read cache.
//
// The cache exists to remove the redundant refetching every navigation used to
// do (measured: four return visits to the Dashboard issued four identical
// `/user/dashboard` requests). What matters here is the other half of that
// trade — the cache must never be the reason a screen shows something the
// server would not say. So these cases are all about *not* reusing a value:
// after a mutation, after an identity change, and after the window closes.

import { afterEach, describe, expect, it, vi } from "vitest";

/** Load a fresh client module so each case gets its own cache. */
async function loadClient() {
  vi.resetModules()
  vi.unstubAllEnvs()
  return await import("./client")
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as unknown as Response
}

/** A localStorage stub whose token can be swapped mid-test. */
function stubStorage(initialToken: string | null = "tok") {
  let token = initialToken
  vi.stubGlobal("localStorage", {
    getItem: () => token,
    setItem: () => {},
    removeItem: () => {},
  })
  return { setToken: (next: string | null) => (token = next) }
}

/** Count requests per path and answer each with a numbered payload. */
function stubCounting() {
  const calls: string[] = []
  let n = 0
  const fetchMock = vi.fn(async (url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "")
    calls.push(path)
    return jsonResponse({ generation: ++n, storage_stats: {}, credit_summary: {}, conversion_stats: {} })
  })
  vi.stubGlobal("fetch", fetchMock)
  stubStorage()
  return { calls, fetchMock }
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.resetModules()
})

describe("read cache — reuse", () => {
  it("serves a repeat read from the cache instead of the network", async () => {
    const { api } = await loadClient()
    const { calls, fetchMock } = stubCounting()

    await api.dashboard()
    await api.dashboard()
    await api.dashboard()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(calls).toHaveLength(1)
  })

  it("coalesces the concurrent callers a single page load produces", async () => {
    // Three components ask for the dashboard on mount; they must share one
    // round trip rather than racing three.
    const { api } = await loadClient()
    const { fetchMock } = stubCounting()

    await Promise.all([api.dashboard(), api.dashboard(), api.dashboard()])

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("does not cache an endpoint with no declared window", async () => {
    // Opt-in by design: a GET without a TTL keeps its previous behaviour, so
    // the change is reviewable endpoint by endpoint.
    const { api } = await loadClient()
    const { fetchMock } = stubCounting()

    await api.getJob("j1")
    await api.getJob("j1")

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("coalesces /users/me without retaining it", async () => {
    // TTL 0: concurrent callers share one request, but nothing is remembered —
    // an identity read must not be answered from a stored value later.
    const { api } = await loadClient()
    const { fetchMock } = stubCounting()

    await Promise.all([api.me(), api.me()])
    await api.me()

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe("read cache — accuracy", () => {
  it("drops cached reads after a successful mutation", async () => {
    // The core guarantee: create a folder, and the next file listing must come
    // from the server rather than from the listing cached before it existed.
    const { api } = await loadClient()
    const { calls, fetchMock } = stubCounting()

    await api.dashboard()
    await api.dashboard()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await api.createFolder("Reports")
    await api.dashboard()

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(calls.filter((c) => c.includes("dashboard"))).toHaveLength(2)
  })

  it("keeps a failed mutation from clearing the cache", async () => {
    // Only a *successful* write invalidates. A rejected request changed
    // nothing, so a cached read is still valid and should survive.
    const { api } = await loadClient()
    let n = 0
    const fetchMock = vi.fn(async (url: string) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, "")
      if (path.endsWith("/v1/files/folders")) return jsonResponse({ detail: "nope" }, 500)
      return jsonResponse({ generation: ++n, storage_stats: {}, credit_summary: {}, conversion_stats: {} })
    })
    vi.stubGlobal("fetch", fetchMock)
    stubStorage()

    await api.dashboard()
    await expect(api.createFolder("Reports")).rejects.toThrow()

    // Still cached, so no new dashboard request.
    await api.dashboard()
    expect(fetchMock.mock.calls.filter(([u]) => String(u).includes("dashboard"))).toHaveLength(1)
  })

  it("drops cached reads when the token changes", async () => {
    // One account's listing must never answer for the next account.
    const { api } = await loadClient()
    const { fetchMock } = stubCounting()
    const storage = stubStorage("token-a")

    await api.dashboard()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    api.setTokens({ access_token: "token-b", refresh_token: "r" } as never)
    storage.setToken("token-b")

    await api.dashboard()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("drops cached reads when the tokens are cleared", async () => {
    const { api } = await loadClient()
    const { fetchMock } = stubCounting()

    await api.dashboard()
    api.clearTokens()
    await api.dashboard()

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("does not let a mutation leave a pre-mutation read behind", async () => {
    // A read that was already in flight when the mutation landed must not
    // settle into the cache afterwards, or the stale listing comes back and the
    // user never sees the folder they just created.
    const { api } = await loadClient()
    let release!: (v: Response) => void
    const listing = new Promise<Response>((resolve) => (release = resolve))
    const gets: string[] = []
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = String(url).replace(/^https?:\/\/[^/]+/, "")
      const method = init?.method ?? "GET"
      if (method !== "GET") return Promise.resolve(jsonResponse({}))
      gets.push(path)
      return listing
    })
    vi.stubGlobal("fetch", fetchMock)
    stubStorage()

    const inFlight = api.listFolders()
    // The mutation lands while that listing is still open.
    await api.createFolder("Reports")
    release(jsonResponse({ folders: [], total: 0 }))
    await inFlight

    expect(gets).toHaveLength(1)
    await api.listFolders()

    // The pre-mutation response was not kept, so this had to ask again.
    expect(gets).toHaveLength(2)
  })

  it("does not remember a failed read", async () => {
    // A transient 500 must not be served for the rest of the window.
    const { api } = await loadClient()
    let attempt = 0
    const fetchMock = vi.fn(async () => {
      attempt += 1
      if (attempt === 1) return jsonResponse({ detail: "boom" }, 500)
      return jsonResponse({ storage_stats: {}, credit_summary: {}, conversion_stats: {} })
    })
    vi.stubGlobal("fetch", fetchMock)
    stubStorage()

    await expect(api.dashboard()).rejects.toThrow()
    await expect(api.dashboard()).resolves.toBeDefined()

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
