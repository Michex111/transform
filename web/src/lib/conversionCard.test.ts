// The conversion card's central invariant: the file the user handed over and
// the file the worker produced are never confused.
//
// A screenshot cannot show the difference between "the result is labelled with
// the source's name" and "the result is labelled correctly" — both look
// plausible — so the rule is pinned here instead.

import { describe, expect, it } from "vitest";
import type { AssistantArtifact } from "@/api/types";
import { conversionHeadline, conversionIdentity } from "@/lib/conversionCard";

const ARTIFACT: AssistantArtifact = {
  type: "job",
  id: "job-1",
  name: "upei residence.pdf",
  meta: { source_format: "pdf", target_format: "docx" },
};

describe("conversionIdentity", () => {
  it("reduces the output object key to the file's own name", () => {
    // `output_file` is the stored OBJECT KEY, not a filename. Rendering it
    // verbatim would put the bucket's internal layout on screen.
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: "output/user/49/job/ee9e8e74-2b58-4c56-8fc6-ffc395346280/upei residence.docx",
    });
    expect(identity.outputName).toBe("upei residence.docx");
    expect(identity.outputName).not.toContain("output/user");
    // The actions act on the output, so they name it.
    expect(identity.actionName).toBe("upei residence.docx");
  });

  it("accepts a bare output file name too", () => {
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: "upei residence.docx",
    });
    expect(identity.outputName).toBe("upei residence.docx");
  });

  it("never invents an output name by swapping the source's extension", () => {
    // The API has not reported an output yet (the job is still running). The
    // tempting shortcut — turn `.pdf` into `.docx` — is exactly the bug the card
    // must not have, so the name stays absent instead.
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: null,
    });
    expect(identity.outputName).toBeNull();
    expect(identity.outputName).not.toBe("upei residence.docx");
    // With no output to act on, the actions fall back to the display name.
    expect(identity.actionName).toBe("upei residence.pdf");
  });

  it("treats a blank output_file as 'no output yet', not as a filename", () => {
    // The API sends `output_file: ""` rather than `null` for a job with no
    // output. `??` alone does not catch that, and the card rendered an empty
    // "Converted file" row — a blank line where it should have said what it was
    // waiting for.
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: "",
    });
    expect(identity.outputName).toBeNull();
    // And the actions must not end up naming nothing.
    expect(identity.actionName).toBe("upei residence.pdf");
  });

  it("trims a padded output_file rather than showing the padding", () => {
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: "  output/user/49/job/abc/report.docx  ",
    });
    expect(identity.outputName).toBe("report.docx");
  });

  it("reduces a key-shaped input_file to a file name as well", () => {
    // The display name must never be a storage path, whichever field it came
    // from.
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upload/2581d77e/5def72ed.pdf",
      output_file: null,
    });
    expect(identity.sourceName).toBe("5def72ed.pdf");
    expect(identity.sourceName).not.toContain("upload/");
  });

  it("prefers the job's authoritative input_file for the source name", () => {
    // `artifact.name` is a display name the API chose and can drift; the job row
    // is the record of what actually ran.
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "renamed by user.pdf",
      output_file: null,
    });
    expect(identity.sourceName).toBe("renamed by user.pdf");
  });

  it("falls back to the artifact name when the job has not loaded", () => {
    const identity = conversionIdentity(ARTIFACT, null);
    expect(identity.sourceName).toBe("upei residence.pdf");
    expect(identity.name).toBe("upei residence.pdf");
    expect(identity.outputName).toBeNull();
  });

  it("keeps the source name and the result name distinct", () => {
    const identity = conversionIdentity(ARTIFACT, {
      input_file: "upei residence.pdf",
      output_file: "upei residence.docx",
    });
    expect(identity.sourceName).not.toBe(identity.outputName);
  });
});

describe("conversionHeadline", () => {
  it.each([
    ["COMPLETED", "Conversion complete"],
    ["FAILED", "Conversion failed"],
    ["PROCESSING", "Converting"],
    ["PENDING", "Conversion queued"],
    ["AWAITING_UPLOAD", "Conversion queued"],
  ])("maps %s to its own wording", (status, expected) => {
    expect(conversionHeadline(status)).toBe(expected);
  });

  it("uses a neutral verb before the job has loaded", () => {
    expect(conversionHeadline(null)).toBe("Conversion");
  });

  it("does not invent a phase for a status this bundle does not know", () => {
    // An API newer than the SPA is expected (the two deploy independently), so
    // an unknown status must degrade, not be guessed at.
    expect(conversionHeadline("SOMETHING_NEW")).toBe("Conversion");
  });
});
