// Keyboard rules shared by every text field that submits on Enter.
//
// Composing text with an IME (Japanese, Chinese, Korean, and any other
// candidate-based input method) uses Enter to *confirm* the current candidate.
// A field that treats that Enter as "submit" sends a half-finished word, and
// does it on every candidate the user accepts — which makes the assistant
// unusable rather than merely surprising.
//
// This lives in its own module because the rule has to be identical everywhere
// it applies; a second copy that checks only one of the two signals would pass
// review and fail only for users of a real IME.

/**
 * The two signals an input method leaves behind, as recommended by the
 * UI Events spec.
 *
 * `isComposing` is the modern, correct one. `keyCode === 229` is the legacy
 * signal: it is what some IMEs send on the keydown that *starts* composition,
 * where `isComposing` is not yet true. Checking both is the documented advice,
 * not belt-and-braces.
 */
export interface CompositionSignals {
  isComposing?: boolean;
  keyCode?: number;
}

/**
 * True when this key event belongs to an IME composition and must not be
 * interpreted as a command.
 */
export function isImeComposing(event: CompositionSignals | null | undefined): boolean {
  if (!event) return false;
  return event.isComposing === true || event.keyCode === 229;
}
