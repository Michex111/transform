// The shortlist a workflow editor offers as target formats.
//
// This decides what a user is permitted to save into a workflow, and both ways
// it can be wrong are quiet: too narrow and valid formats simply never appear;
// mis-cased and the picker renders *disabled* for a workflow that is entirely
// valid, which reads as a broken page rather than as a bug in a list.

import { describe, expect, it } from "vitest";
import { workflowTargetOptions } from "@/lib/workflowTargets";

describe("workflowTargetOptions", () => {
  it("keeps every format the graph can produce", () => {
    const targets = ["pdf", "docx", "png"];
    expect(workflowTargetOptions(targets)).toEqual(["pdf", "docx", "png"]);
  });

  it("lowercases, so a mis-cased stored value still matches the picker", () => {
    // The picker compares the selected value against this list exactly. Without
    // lowercasing here, a workflow row holding "PDF" would disable the picker.
    expect(workflowTargetOptions(["PDF", "DocX"])).toEqual(["pdf", "docx"]);
  });

  it("de-duplicates", () => {
    // Defensive: the hook already de-duplicates, but a boundary that trusts its
    // caller's promise-of-the-day is how duplicated picker entries appear.
    expect(workflowTargetOptions(["pdf", "pdf", "PDF", "png"])).toEqual(["pdf", "png"]);
  });

  it("trims surrounding whitespace", () => {
    expect(workflowTargetOptions(["  pdf  "])).toEqual(["pdf"]);
  });

  it("drops empty entries rather than offering a blank option", () => {
    expect(workflowTargetOptions(["", "   ", "pdf"])).toEqual(["pdf"]);
  });

  it("preserves the input order", () => {
    // The picker groups by the catalogue's own categories, so this list does not
    // control layout — but reordering it here would be a change nobody asked
    // for, and a sort would imply an ordering this function does not own.
    expect(workflowTargetOptions(["zip", "aac", "pdf"])).toEqual(["zip", "aac", "pdf"]);
  });

  it("returns an empty list for an unloaded graph", () => {
    // The picker treats an empty `allowed` as "nothing may be picked yet" and
    // says so, rather than falling back to the whole catalogue and offering
    // formats the server would reject.
    expect(workflowTargetOptions([])).toEqual([]);
  });

  it("does not mutate its input", () => {
    const input = ["PDF", "pdf"];
    workflowTargetOptions(input);
    expect(input).toEqual(["PDF", "pdf"]);
  });
});
