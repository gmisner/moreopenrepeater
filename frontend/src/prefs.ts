/** Browser-local UI preferences (not server config). */

const DENSITY_KEY = "mor-ui-density";
const VERSION_ACK_KEY = "mor-acked-version-sha";
const VERSION_SEEN_KEY = "mor-seen-version-sha";

export type UiDensity = "comfortable" | "compact" | "large";

const DENSITY_SCALE: Record<UiDensity, string> = {
  comfortable: "1",
  compact: "0.92",
  large: "1.12",
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
