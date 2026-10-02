import { api } from "./api.js";
import { currentView, router } from "./router.js";
import { session } from "./session.js";
import { escapeHtml, formatTimestamp, toast, toastError, withBusy } from "./ui.js";

const boxesTbody = document.getElementById("mailbox-boxes-tbody");
const boxesEmpty = document.getElementById("mailbox-boxes-empty");
const messagesTbody = document.getElementById("mailbox-messages-tbody");
const messagesEmpty = document.getElementById("mailbox-messages-empty");
const form = document.getElementById("mailbox-box-form");
const submit = document.getElementById("mailbox-box-submit");
const cancel = document.getElementById("mailbox-box-cancel");
const pinNote = document.getElementById("mailbox-pin-note");

let editing = null;
let shownMessages = "";

function formatLength(seconds) {
  return seconds < 60 ? `${Math.round(seconds)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function render({ boxes, messages }) {
  const names = Object.fromEntries(boxes.map((b) => [b.box, b.name]));
  boxesTbody.innerHTML = "";
  boxesEmpty.hidden = boxes.length > 0;
  for (const box of boxes) {
    const row = document.createElement("tr");
    const noPin = box.pin_set ? "" : ' <span class="tag tag-danger">no PIN</span>';
    row.innerHTML = `
      <td>${escapeHtml(box.box)}${noPin}</td>
      <td>${escapeHtml(box.name)}</td>
      <td>${box.messages}</td>
      <td class="row-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-edit>Edit</button>
        <button type="button" class="btn btn-danger btn-sm" data-delete>Delete</button>
      </td>`;
    row.querySelector("[data-edit]").addEventListener("click", () => startEdit(box));
    row.querySelector("[data-delete]").addEventListener("click", () => removeBox(box));
    boxesTbody.appendChild(row);
  }

  // Re-rendering would stop a message that's playing, so only redraw on change.
  const ids = messages.map((m) => m.id).join(",");
  if (ids === shownMessages) return;
  shownMessages = ids;
  messagesTbody.innerHTML = "";
  messagesEmpty.hidden = messages.length > 0;
  for (const message of messages) {
    const row = document.createElement("tr");
    const whose = names[message.box] ? ` <small class="muted">${escapeHtml(names[message.box])}</small>` : "";
    row.innerHTML = `
      <td>${escapeHtml(message.box)}${whose}</td>
      <td>${escapeHtml(formatTimestamp(message.left_at))}</td>
      <td>${formatLength(message.duration)}</td>
      <td><audio controls preload="none" src="/api/mailbox/messages/${encodeURIComponent(message.id)}/audio"></audio></td>
      <td class="row-actions"><button type="button" class="btn btn-danger btn-sm">Delete</button></td>`;
    row.querySelector("button").addEventListener("click", () => removeMessage(message));
    messagesTbody.appendChild(row);
  }
}

function startEdit(box) {
  editing = box.box;
  form.elements.box.value = box.box;
  form.elements.box.readOnly = true;
  form.elements.name.value = box.name;
  form.elements.pin.value = "";
  pinNote.textContent = box.pin_set ? "Leave blank to keep the current PIN." : "4 to 8 digits.";
  submit.textContent = "Save mailbox";
  cancel.hidden = false;
  form.elements.name.focus();
}

function stopEdit() {
  editing = null;
  form.reset();
  form.elements.box.readOnly = false;
  pinNote.textContent = "4 to 8 digits.";
  submit.textContent = "Add mailbox";
  cancel.hidden = true;
}

async function saveBox() {
  const box = form.elements.box.value.trim();
  const pin = form.elements.pin.value.trim();
  if (!editing && !pin) {
    form.elements.pin.focus();
    toast("A new mailbox needs a PIN", "error");
    return;
  }
  const json = { name: form.elements.name.value.trim() };
  if (pin) json.pin = pin;
  try {
    render(await api(`/api/mailbox/boxes/${encodeURIComponent(box)}`, { method: "PUT", json }));
    toast(editing ? `Mailbox ${box} saved` : `Mailbox ${box} added`);
    stopEdit();
  } catch (error) {
    toastError(error);
  }
}

async function removeBox(box) {
  const lost = box.messages ? ` Its ${box.messages === 1 ? "message goes" : `${box.messages} messages go`} too.` : "";
  if (!confirm(`Delete mailbox ${box.box}?${lost}`)) return;
  try {
    render(await api(`/api/mailbox/boxes/${encodeURIComponent(box.box)}`, { method: "DELETE" }));
    if (editing === box.box) stopEdit();
    toast(`Mailbox ${box.box} deleted`);
  } catch (error) {
    toastError(error);
  }
}

async function removeMessage(message) {
  if (!confirm(`Delete the message for mailbox ${message.box} from ${formatTimestamp(message.left_at)}?`)) return;
  try {
    render(await api(`/api/mailbox/messages/${encodeURIComponent(message.id)}`, { method: "DELETE" }));
    toast("Message deleted");
  } catch (error) {
    toastError(error);
  }
}

async function load() {
  if (session?.role !== "admin") return;
  render(await api("/api/mailbox"));
}

export function initMailbox() {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    withBusy(submit, saveBox);
  });
  cancel.addEventListener("click", stopEdit);
  router.addEventListener("change", ({ detail }) => {
    if (detail === "mailbox") load().catch(toastError);
  });
  if (currentView === "mailbox") load().catch(toastError);
}
