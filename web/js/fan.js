import { store } from "./store.js";

const form = document.getElementById("fan-form");
const stateTag = document.getElementById("fan-state");
const errorLine = document.getElementById("fan-error");

const REASONS = { transmitting: "transmitting", hot: "CPU hot" };

export function fanText(fan) {
  if (!fan || fan.on === null) return "–";
  if (!fan.on) return "Off";
  if (fan.reason === "run-on" && fan.run_on_left !== null) {
    const left = Math.ceil(fan.run_on_left);
    return `On · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
  }
  return fan.reason in REASONS ? `On · ${REASONS[fan.reason]}` : "On";
}

function renderFields() {
  const output = form.elements.fan_output.value;
  for (const label of form.querySelectorAll("[data-fan]")) label.hidden = !label.dataset.fan.split(" ").includes(output);
}

function render() {
  const fan = store.state.gpio?.fan;
  if (!fan) return;
  errorLine.hidden = !fan.error;
  errorLine.textContent = fan.error ?? "";
  if (fan.output === "none") {
    stateTag.className = "tag";
    stateTag.textContent = "off";
  } else if (fan.error) {
    stateTag.className = "tag tag-danger";
    stateTag.textContent = "error";
  } else {
    stateTag.className = `tag ${fan.on ? "tag-on" : ""}`;
    stateTag.textContent = fanText(fan);
  }
}

export function initFan() {
  store.addEventListener("config", renderFields);
  store.addEventListener("gpio", render);
  form.addEventListener("input", renderFields);
  form.querySelector("[data-reset]").addEventListener("click", () => setTimeout(renderFields));
  if (store.state.config) renderFields();
}
