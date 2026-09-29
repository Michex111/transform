// The example questions shown on an empty conversation.
//
// Every entry maps to a capability the assistant's tools actually have, because
// a suggestion the tools cannot act on is a dead end the user pays for:
//
//   summarize_file          — summarise a document by file id
//   read_file_text          — hand back the text of a document
//   list_files              — search/filter the drive
//   list_supported_targets  — which formats a source format can become
//   start_conversion        — enqueue a conversion
//   create_folder           — make a folder
//   move_file              — move a file into a folder
//   list_recent_conversions — report recent/running conversions
//   usage/credits           — how many credits are left, conversions in a window
//
// Nothing here asks for merge, split, OCR, delete or any other operation the
// tools do not expose.
//
// The pick is seeded so a session's set is stable across re-renders while two
// different sessions get different sets — a fixed four made every new chat look
// identical. Pure, so the seeding is testable without a DOM.

export const SUGGESTED_PROMPTS: readonly string[] = [
  // summarize_file
  "Summarize my latest PDF",
  "Give me the key points of my report",
  "What's in my most recent document?",
  // read_file_text
  "Read me the first part of my contract",
  "Show me the text of my notes file",
  "What does my invoice actually say?",
  // list_files
  "Which of my files are the largest?",
  "List my PDFs",
  "Do I have any spreadsheets?",
  "What files did I add recently?",
  // list_supported_targets
  "What formats can I convert a PDF to?",
  "Which formats can a PNG become?",
  "What can I convert DOCX into?",
  // start_conversion
  "Convert my report to DOCX",
  "Turn my spreadsheet into a PDF",
  "Convert my image to JPG",
  // create_folder
  "Create a folder called Invoices",
  "Make a new folder for this project",
  // move_file
  "Move my report into the Invoices folder",
  // list_recent_conversions
  "What did I convert recently?",
  "Which conversions are still running?",
  // usage/credits
  "How many credits do I have left?",
  "How many PDF conversions did I run in the last 24 hours?",
];

/** Questions that exercise the assistant's usage/credits tool. */
export const USAGE_PROMPTS: readonly string[] = [
  "How many credits do I have left?",
  "How many PDF conversions did I run in the last 24 hours?",
];

/** 32-bit FNV-1a over a stringified seed. Stable across engines. */
function hashSeed(seed: string | number): number {
  const text = typeof seed === "number" ? `n:${seed}` : `s:${seed}`;
  let hash = 2166136261 >>> 0;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash >>> 0;
}

/** mulberry32 — a small, fast, seedable PRNG. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return function next(): number {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Deterministically pick `count` DIFFERENT suggestions for `seed`.
 *
 * A seeded Fisher–Yates shuffle over a copy of the pool is used rather than
 * index stepping: every entry is reachable as a first pick, the result never
 * contains duplicates, and the same seed always yields the same list. `seed`
 * accepts a string or a number so callers can pass a session token or a
 * counter without caring which.
 */
export function pickSuggestions(seed: string | number, count = 4): string[] {
  const pool = [...SUGGESTED_PROMPTS];
  const random = mulberry32(hashSeed(seed));

  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    const swap = pool[i];
    pool[i] = pool[j];
    pool[j] = swap;
  }

  const wanted = Math.max(0, Math.min(Math.floor(count), pool.length));
  return pool.slice(0, wanted);
}

/**
 * Starter questions for the compact mini chat.
 *
 * Always leads with a usage/credits question — the panel is the smallest
 * surface the assistant appears on, and that capability is the easiest one for
 * a user to miss — then fills the rest from the seeded pool, avoiding
 * duplicates.
 */
export function pickMiniSuggestions(seed: string | number, count = 3): string[] {
  const wanted = Math.max(1, Math.floor(count));
  const usage = USAGE_PROMPTS[hashSeed(seed) % USAGE_PROMPTS.length];
  const rest = pickSuggestions(seed, wanted).filter((prompt) => prompt !== usage);
  return [usage, ...rest].slice(0, wanted);
}
