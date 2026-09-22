// Column-alignment invariants for the app's data tables.
//
// The header row and each data row are SEPARATE CSS grid containers, so an
// `auto` track is resolved from each container's own content and the columns
// silently stop lining up. Measured before this was fixed, at 1440px: the Status
// column began at x=833 in the header but x=871 in the rows, and Progress at
// x=1014 vs x=1064 — the header's "Created" track was 58px (sized by the word)
// while a row's was 7px at one point and ~150px at another, depending on
// whether a date and how many action buttons were present.
//
// These tests pin the two rules that keep the tables aligned, so a future edit
// that reintroduces a content-sized track, or that changes one side of a table
// without the other, fails here.

import { describe, expect, it } from "vitest";

import {
  BREAKPOINTS,
  DASHBOARD_ROW_GRID,
  HISTORY_HEADER_GRID,
  HISTORY_ROW_GRID,
  QUEUE_HEADER_GRID,
  QUEUE_ROW_GRID,
  parseGridTemplates,
  tracksAt,
} from "@/lib/tableColumns";

/** Every template a table uses. */
const ALL = {
  QUEUE_HEADER_GRID,
  QUEUE_ROW_GRID,
  HISTORY_HEADER_GRID,
  HISTORY_ROW_GRID,
  DASHBOARD_ROW_GRID,
};

/** Content-sized tracks make a column's width depend on that container's own
 *  content, which is the entire defect class. */
const CONTENT_SIZED = ["auto", "min-content", "max-content", "fit-content"];

describe("table column templates", () => {
  it.each(Object.entries(ALL))("%s declares at least one grid template", (_name, value) => {
    expect(parseGridTemplates(value).length).toBeGreaterThan(0);
  });

  it.each(Object.entries(ALL))("%s has no content-sized tracks", (_name, value) => {
    for (const { breakpoint, tracks } of parseGridTemplates(value)) {
      for (const forbidden of CONTENT_SIZED) {
        expect(
          tracks.split("_"),
          `${breakpoint || "base"} template "${tracks}" uses a ${forbidden} track, which sizes itself from that container's content`,
        ).not.toContain(forbidden);
      }
    }
  });

  // The header is `hidden` below its activation breakpoint (`sm` for the Queue,
  // `md` for History), so the requirement is: from the breakpoint where the
  // header becomes visible, the rows must use exactly the same tracks.
  const cases = [
    { name: "Queue", header: QUEUE_HEADER_GRID, row: QUEUE_ROW_GRID, visibleFrom: "sm" },
    { name: "History", header: HISTORY_HEADER_GRID, row: HISTORY_ROW_GRID, visibleFrom: "md" },
  ] as const;

  it.each(cases)("$name rows match the header at every breakpoint the header is visible", ({ header, row, visibleFrom }) => {
    const headerTemplates = parseGridTemplates(header);
    const rowTemplates = parseGridTemplates(row);
    const startIndex = BREAKPOINTS.indexOf(visibleFrom);
    expect(startIndex).toBeGreaterThanOrEqual(0);

    for (const breakpoint of BREAKPOINTS.slice(startIndex)) {
      const inHeader = tracksAt(headerTemplates, breakpoint);
      const inRow = tracksAt(rowTemplates, breakpoint);
      expect(inHeader, `${breakpoint}: header declares no template`).toBeDefined();
      expect(
        inRow,
        `${breakpoint}: header uses "${inHeader}" but the rows use "${inRow}" — the columns cannot line up`,
      ).toBe(inHeader);
    }
  });

  it("the header becomes visible at the same breakpoint the rows switch template", () => {
    // A `hidden ... sm:grid` header paired with rows that only reach 5 columns
    // at `lg` is what produced a reserved-but-empty column at `sm`.
    expect(QUEUE_HEADER_GRID).toContain("sm:grid");
    expect(HISTORY_HEADER_GRID).toContain("md:grid");
  });

  it("keeps the date in its own track, not bundled with the action buttons", () => {
    // Sharing one track made its width vary with the number of buttons.
    const headerTracks = parseGridTemplates(HISTORY_HEADER_GRID);
    const rowTracks = parseGridTemplates(HISTORY_ROW_GRID);
    expect(tracksAt(headerTracks, "lg")).toBe(tracksAt(rowTracks, "lg"));
    expect(tracksAt(headerTracks, "lg")!.split("_")).toHaveLength(5);
  });

  it("keeps a dashboard action track on every row, even with no action", () => {
    // Five tracks from `md` up, so a row without a download button still lines
    // up. (The band starts at `md` rather than `sm` now that the same
    // expandable card serves both tables below it.)
    const tracks = parseGridTemplates(DASHBOARD_ROW_GRID);
    expect(tracksAt(tracks, "md")!.split("_")).toHaveLength(5);
  });

  it.each([
    ["History", HISTORY_ROW_GRID],
    ["Dashboard", DASHBOARD_ROW_GRID],
  ])("%s reserves no tracks at the phone band", (_name, row) => {
    // Below `md` these rows are flex lines. A base grid template would mean a
    // fixed width the filename has to compete with — the 360px History template
    // it replaced left the name 36px, and the Dashboard's left it 0px.
    expect(parseGridTemplates(row).filter((t) => t.breakpoint === "")).toEqual([]);
    expect(row).toContain("flex-wrap");
  });
});

describe("parseGridTemplates", () => {
  it("reads breakpoint prefixes and tracks", () => {
    expect(parseGridTemplates("grid grid-cols-[1fr_2fr] sm:grid-cols-[3fr_4fr]")).toEqual([
      { breakpoint: "", tracks: "1fr_2fr" },
      { breakpoint: "sm", tracks: "3fr_4fr" },
    ]);
  });

  it("reads only the arbitrary-value form the tables use", () => {
    // `grid-cols-3` is a fixed equal-column grid, which is content-independent
    // by construction, so it needs no parsing (and is not used by these tables).
    expect(parseGridTemplates("grid grid-cols-3 gap-4 px-5")).toEqual([]);
  });
});

describe("tracksAt", () => {
  const templates = parseGridTemplates(
    "grid-cols-[1fr] sm:grid-cols-[1fr_2fr] lg:grid-cols-[1fr_2fr_3fr]",
  );

  it("carries an earlier declaration forward", () => {
    expect(tracksAt(templates, "")).toBe("1fr");
    expect(tracksAt(templates, "sm")).toBe("1fr_2fr");
    expect(tracksAt(templates, "md")).toBe("1fr_2fr");
    expect(tracksAt(templates, "lg")).toBe("1fr_2fr_3fr");
    expect(tracksAt(templates, "2xl")).toBe("1fr_2fr_3fr");
  });

  it("returns undefined for an unknown breakpoint", () => {
    expect(tracksAt(templates, "xl")).toBe("1fr_2fr_3fr");
    expect(tracksAt(parseGridTemplates("grid"), "sm")).toBeUndefined();
  });
});
