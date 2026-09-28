import { apiBlob } from "./api.js";

let current = null;

// Renders `clip` on the server (with `config` overrides) and plays it,
// stopping whatever preview was already playing.
export async function playClip(clip, config = {}) {
  const blob = await apiBlob("/api/audio/preview", { method: "POST", json: { clip, config } });
  if (current) {
    current.pause();
    URL.revokeObjectURL(current.src);
  }
  current = new Audio(URL.createObjectURL(blob));
  await current.play();
}
