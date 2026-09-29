import { describe, expect, it, vi } from "vitest";
import type { FolderResponse } from "@/api/types";
import { buildFolderPath, MAX_FOLDER_DEPTH, type FolderFetcher } from "@/lib/folderPath";

/** A `FolderResponse` is bulky; the walk only reads id / name / parent_id. */
function folder(id: string, parentId: string | null): FolderResponse {
  return {
    id,
    name: `name-${id}`,
    parent_id: parentId,
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
  };
}

/**
 * A fetcher over a flat id→folder map that also carries the API's 404 behaviour:
 * an id the drive does not know rejects, exactly as `getFolderContents` does.
 */
function fetcherFor(records: Record<string, FolderResponse>): FolderFetcher {
  return async (id) => {
    const record = records[id];
    if (!record) throw new Error(`Folder ${id} not found`);
    return record;
  };
}

describe("buildFolderPath", () => {
  it("walks parents and returns the chain root-first", async () => {
    // root ── a ── b ── target
    const fetch = fetcherFor({
      target: folder("target", "b"),
      b: folder("b", "a"),
      a: folder("a", "root"),
      root: folder("root", null),
    });

    const chain = await buildFolderPath("target", fetch);
    expect(chain.map((f) => f.id)).toEqual(["root", "a", "b", "target"]);
  });

  it("returns a single entry for a root-level folder", async () => {
    const fetch = fetcherFor({ solo: folder("solo", null) });

    const chain = await buildFolderPath("solo", fetch);
    expect(chain.map((f) => f.id)).toEqual(["solo"]);
    expect(chain[0].parent_id).toBeNull();
  });

  it("returns no chain and does not fetch for an empty target", async () => {
    const fetch = vi.fn<FolderFetcher>();

    await expect(buildFolderPath(null, fetch)).resolves.toEqual([]);
    await expect(buildFolderPath(undefined, fetch)).resolves.toEqual([]);
    await expect(buildFolderPath("", fetch)).resolves.toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("propagates a rejection for an unknown id (API 404)", async () => {
    const fetch = fetcherFor({ real: folder("real", null) });

    await expect(buildFolderPath("ghost", fetch)).rejects.toThrow("not found");
  });

  it("treats a record whose id does not match the request as unresolvable", async () => {
    // A malformed answer: asked for `asked`, got `other`.
    const fetch = vi.fn<FolderFetcher>(async () => folder("other", null));

    await expect(buildFolderPath("asked", fetch)).resolves.toEqual([]);
  });

  it("terminates and returns nothing for a two-folder parent cycle", async () => {
    // a → b → a …
    const records = { a: folder("a", "b"), b: folder("b", "a") };
    let fetches = 0;
    const fetch: FolderFetcher = async (id) => {
      fetches += 1;
      const record = records[id as keyof typeof records];
      if (!record) throw new Error(`Folder ${id} not found`);
      return record;
    };

    const chain = await buildFolderPath("a", fetch);
    expect(chain).toEqual([]);
    // Bounded work: neither id is ever fetched more than once.
    expect(fetches).toBe(2);
  });

  it("terminates for a self-parented folder", async () => {
    const fetch = fetcherFor({ loop: folder("loop", "loop") });

    await expect(buildFolderPath("loop", fetch)).resolves.toEqual([]);
  });

  it("rejects a chain deeper than MAX_FOLDER_DEPTH", async () => {
    const records: Record<string, FolderResponse> = {};
    // `f0` is the deepest target, each folder's parent is the next id up, and
    // the top-most parent is non-null so only the depth cap can stop the walk.
    const total = MAX_FOLDER_DEPTH + 5;
    for (let i = 0; i < total; i++) {
      records[`f${i}`] = folder(`f${i}`, i + 1 < total ? `f${i + 1}` : "unreachable");
    }

    await expect(buildFolderPath("f0", fetcherFor(records))).resolves.toEqual([]);
  });

  it("caps exactly at MAX_FOLDER_DEPTH levels", async () => {
    const records: Record<string, FolderResponse> = {};
    for (let i = 0; i < MAX_FOLDER_DEPTH; i++) {
      records[`f${i}`] = folder(`f${i}`, i + 1 < MAX_FOLDER_DEPTH ? `f${i + 1}` : null);
    }

    const chain = await buildFolderPath("f0", fetcherFor(records));
    expect(chain).toHaveLength(MAX_FOLDER_DEPTH);
    expect(chain[0].id).toBe(`f${MAX_FOLDER_DEPTH - 1}`);
    expect(chain[chain.length - 1].id).toBe("f0");
  });
});
