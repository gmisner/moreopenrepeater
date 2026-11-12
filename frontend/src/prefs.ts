/** Browser-local UI preferences (not server config). */

const DENSITY_KEY = "mor-ui-density";
const POLL_KEY = "mor-ui-poll";
const VERSION_ACK_KEY = "mor-acked-version-sha";
const VERSION_SEEN_KEY = "mor-seen-version-sha";

export type UiDensity = "comfortable" | "compact" | "large";

/** How hard Audio & tones (and similar live pages) hit the API from this browser. */
export type PollAggressiveness = "smooth" | "balanced" | "light";

const DENSITY_SCALE: Record<UiDensity, string> = {
  comfortable: "1",
  compact: "0.92",
  large: "1.12",
};

/** Live meter polls on Audio & tones. */
export const POLL_MS_BY: Record<PollAggressiveness, number> = {
  smooth: 500,
  balanced: 1000,
  light: 2500,
};

/** GPIO / monitor-receiver polls on the Audio pins tab. */
export const HARDWARE_POLL_MS_BY: Record<PollAggressiveness, number> = {
  smooth: 1000,
  balanced: 3000,
  light: 5000,
};

export function readDensity(): UiDensity {
  const raw = localStorage.getItem(DENSITY_KEY);
  if (raw === "compact" || raw === "large" || raw === "comfortable") return raw;
  return "comfortable";
}

export function applyDensity(density: UiDensity) {
  document.documentElement.style.setProperty("--mor-ui-scale", DENSITY_SCALE[density]);
  document.documentElement.dataset.density = density;
  localStorage.setItem(DENSITY_KEY, density);
}

export function readPollAggressiveness(): PollAggressiveness {
  const raw = localStorage.getItem(POLL_KEY);
  if (raw === "smooth" || raw === "balanced" || raw === "light") return raw;
  return "balanced";
}

export function writePollAggressiveness(value: PollAggressiveness) {
  localStorage.setItem(POLL_KEY, value);
}

/** ~5 s of silence before the "check the sound card" hint. */
export function silentPollsBeforeHint(pollMs: number): number {
  return Math.max(1, Math.ceil(5000 / pollMs));
}

export function readAckedVersionSha(): string | null {
  return localStorage.getItem(VERSION_ACK_KEY);
}

export function ackVersionSha(sha: string) {
  localStorage.setItem(VERSION_ACK_KEY, sha);
  localStorage.setItem(VERSION_SEEN_KEY, sha);
}

export function readSeenVersionSha(): string | null {
  return sessionStorage.getItem(VERSION_SEEN_KEY) ?? localStorage.getItem(VERSION_SEEN_KEY);
}

export function markVersionSeen(sha: string) {
  sessionStorage.setItem(VERSION_SEEN_KEY, sha);
}
