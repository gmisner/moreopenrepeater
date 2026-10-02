import { apiBlob } from "./api.js";

let current = null;

// Renders `clip` on the server (with `config` overrides, and as during a
// net if `net`) and plays it, stopping whatever preview was already playing.
export async function playClip(clip, config = {}, net = false) {
  const blob = await apiBlob("/api/audio/preview", { method: "POST", json: { clip, config, net } });
  if (current) {
    current.pause();
    URL.revokeObjectURL(current.src);
  }
  current = new Audio(URL.createObjectURL(blob));
  await current.play();
}
