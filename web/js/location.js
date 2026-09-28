import { toast } from "./ui.js";

const GRID = /^([A-R]{2})(\d{2})([A-X]{2}(\d{2})?)?$/i;
const PAIR = /^(-?\d{1,2}(?:\.\d+)?)\s*°?\s*([NS])?[\s,;]+(-?\d{1,3}(?:\.\d+)?)\s*°?\s*([EW])?$/i;

// Maidenhead locator (2 letters, 2 digits, optionally 2 letters and 2 more
// digits) -> the center of that square.
function fromGrid(match) {
  const [, field, square, sub = "", ext = ""] = match;
  const letter = (c, from) => c.toUpperCase().charCodeAt(0) - from.charCodeAt(0);
  let lon = -180 + letter(field[0], "A") * 20 + Number(square[0]) * 2;
  let lat = -90 + letter(field[1], "A") * 10 + Number(square[1]);
  let width = 2;
  let height = 1;
  if (sub) {
    width /= 24;
    height /= 24;
    lon += letter(sub[0], "A") * width;
    lat += letter(sub[1], "A") * height;
  }
  if (ext) {
    width /= 10;
    height /= 10;
    lon += Number(ext[0]) * width;
    lat += Number(ext[1]) * height;
  }
  return { lat: lat + height / 2, lon: lon + width / 2 };
}

function fromPair(match) {
  const [, latText, ns, lonText, ew] = match;
  let lat = Number(latText);
  let lon = Number(lonText);
  if (ns?.toUpperCase() === "S") lat = -Math.abs(lat);
  if (ew?.toUpperCase() === "W") lon = -Math.abs(lon);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

/** A grid square ("FN31pr") or a coordinate pair ("41.71, -72.73" or
 *  "41.71 N 72.73 W") -> {lat, lon}, or null if it's neither. */
export function parseLocation(text) {
  const value = text.trim().replace(/\s+/g, " ");
  const grid = value.replace(/\s/g, "").match(GRID);
  if (grid) return fromGrid(grid);
  const pair = value.match(PAIR);
  return pair ? fromPair(pair) : null;
}

function fill(form, latField, lonField, { lat, lon }) {
  form.elements[latField].value = lat.toFixed(4);
  form.elements[lonField].value = lon.toFixed(4);
  form.classList.add("dirty");
}

function geolocationError(error) {
  if (error.code === error.PERMISSION_DENIED) {
    if (!window.isSecureContext) {
      return "Browsers only share location with HTTPS pages or localhost. Enter a grid square or coordinates instead.";
    }
    return "Location access is blocked for this page. Allow it in the browser's site settings (on a Mac, also check System Settings > Privacy & Security > Location Services), or enter a grid square or coordinates instead.";
  }
  if (error.code === error.TIMEOUT) return "Timed out waiting for a location. Enter a grid square or coordinates instead.";
  return "This device couldn't work out its location. Enter a grid square or coordinates instead.";
}

/** Wires a form's "Use my location" button and its grid/coordinates box. */
export function initLocationInputs(form, latField, lonField) {
  const lookup = form.querySelector("[data-location-lookup]");
  const apply = () => {
    if (!lookup.value.trim()) return;
    const location = parseLocation(lookup.value);
    if (!location) {
      toast("Enter a grid square like FN31pr, or coordinates like 41.7148, -72.7272", "error");
      return;
    }
    fill(form, latField, lonField, location);
    lookup.value = "";
  };
  lookup.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault(); // don't submit the settings form
    apply();
  });
  form.querySelector("[data-location-apply]").addEventListener("click", apply);

  const locate = form.querySelector("[data-locate]");
  if (!("geolocation" in navigator)) {
    locate.hidden = true;
    return;
  }
  locate.addEventListener("click", () => {
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => fill(form, latField, lonField, { lat: coords.latitude, lon: coords.longitude }),
      (error) => toast(geolocationError(error), "error"),
      { timeout: 15000 },
    );
  });
}
