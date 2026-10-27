// A page with work that would be lost (Settings with unsaved edits) holds the
// console here. Leaving by something other than a link or Back (signing out,
// asking a new question from the bar) asks `mayLeave()` first, and calls
// `stayed()` when it did not leave after all (the request failed), so the
// page asks again next time. Links and Back are guarded by the page itself.
interface Guard {
  ask: () => boolean;
  stay: () => void;
}

let guard: Guard | null = null;

/** Hold the console while `ask` must be consulted; returns the release. */
export function holdLeave(ask: () => boolean, stay: () => void): () => void {
  const held = { ask, stay };
  guard = held;
  return () => {
    if (guard === held) guard = null;
  };
}

/** Whether the person may leave the current page; asks them if a page holds it. */
export function mayLeave(): boolean {
  return guard ? guard.ask() : true;
}

/** The person agreed to leave but is still here: guard the page again. */
export function stayed(): void {
  guard?.stay();
}
