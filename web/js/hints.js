// Descriptions written as a label's <small>, a switch's <small> note, or a
// muted paragraph right under a heading become an (i) icon that shows them on
// hover, focus or tap. One with an id (filled in by code) or data-keep stays
// on the page.

const CLOSE_DELAY_MS = 150;
let counter = 0;
let current = null;
let closeTimer = null;

export function initHints(root = document) {
  for (const small of root.querySelectorAll("label:not(.switch) > small:not([id]):not([data-keep])")) {
    if (small.querySelector("[id]")) continue;
    const title = labelTitle(small.parentElement);
    if (title) attach(title, small);
  }
  for (const small of root.querySelectorAll(".switch > span > small:not([id]):not([data-keep])")) {
    attach(small.parentElement, small);
  }
  for (const note of root.querySelectorAll("p.muted.small:not([id]):not([data-keep])")) {
    const host = headingBefore(note);
    if (host) attach(host, note);
  }
  if (root === document) {
    document.addEventListener("click", (event) => {
      if (current && !current.contains(event.target)) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && current) {
        const button = current.querySelector(".hint-icon");
        close();
        button.focus();
      }
    });
    window.addEventListener("resize", () => close());
    document.addEventListener("scroll", () => close(), true);
  }
}

// A label's field name is its first text; it's wrapped so the icon sits beside it.
function labelTitle(label) {
  const text = [...label.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
  if (!text) return null;
  const title = document.createElement("span");
  title.className = "label-title";
  title.textContent = text.textContent.trim();
  label.replaceChild(title, text);
  return title;
}

function headingBefore(note) {
  const previous = note.previousElementSibling;
  if (!previous) return null;
  if (previous.matches("h2")) return previous;
  if (previous.matches(".card-header")) return previous.querySelector("h2");
  if (previous.matches(".switch")) return previous.querySelector(":scope > span:last-child");
  return null;
}

function attach(host, source) {
  if (source.querySelector("[id]")) return;
  const existing = host.querySelector(":scope > .hint > .hint-tip");
  if (existing) {
    const more = document.createElement("span");
    more.className = "hint-more";
    more.append(...source.childNodes);
    existing.append(more);
    source.remove();
    return;
  }
  const hint = document.createElement("span");
  hint.className = "hint";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "hint-icon";
  button.setAttribute("aria-label", `About ${host.textContent.trim()}`);
  button.setAttribute("aria-expanded", "false");
  button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-info" /></svg>';
  const tip = document.createElement("span");
  tip.className = "hint-tip";
  tip.id = `hint-${++counter}`;
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  tip.append(...source.childNodes);
  tidy(tip);
  button.setAttribute("aria-describedby", tip.id);
  hint.append(button, tip);
  host.append(hint);
  source.remove();

  hint.addEventListener("mouseenter", () => open(hint, false));
  hint.addEventListener("mouseleave", () => {
    if (!hint.classList.contains("pinned")) closeSoon();
  });
  button.addEventListener("focus", () => open(hint, false));
  hint.addEventListener("focusout", (event) => {
    if (!hint.contains(event.relatedTarget) && !hint.classList.contains("pinned")) close();
  });
  button.addEventListener("click", (event) => {
    event.preventDefault(); // inside a <label>, don't toggle or focus its control
    if (hint.classList.contains("pinned")) close();
    else open(hint, true);
  });
  tip.addEventListener("click", (event) => {
    if (!event.target.closest("a")) event.preventDefault();
  });
}

// Notes written for inline use: "— off stops...", "(off: ...)".
function tidy(tip) {
  const first = tip.firstChild;
  if (first?.nodeType === Node.TEXT_NODE) first.textContent = first.textContent.replace(/^\s*(—|–|-)\s*/, "").replace(/^\s+/, "");
  const text = tip.textContent.trim();
  if (text.startsWith("(") && text.endsWith(")")) {
    const head = tip.firstChild;
    const tail = tip.lastChild;
    if (head?.nodeType === Node.TEXT_NODE) head.textContent = head.textContent.replace(/^\s*\(/, "");
    if (tail?.nodeType === Node.TEXT_NODE) tail.textContent = tail.textContent.replace(/\)\s*$/, "");
  }
  const start = tip.firstChild;
  if (start?.nodeType === Node.TEXT_NODE) start.textContent = start.textContent.charAt(0).toUpperCase() + start.textContent.slice(1);
  const end = tip.lastChild;
  if (end?.nodeType === Node.TEXT_NODE && /[A-Za-z0-9)]\s*$/.test(end.textContent)) end.textContent = `${end.textContent.trimEnd()}.`;
}

function open(hint, pinned) {
  clearTimeout(closeTimer);
  if (current && current !== hint) close();
  current = hint;
  hint.classList.toggle("pinned", pinned || hint.classList.contains("pinned"));
  const button = hint.querySelector(".hint-icon");
  const tip = hint.querySelector(".hint-tip");
  button.setAttribute("aria-expanded", "true");
  tip.hidden = false;
  place(button, tip);
}

function closeSoon() {
  clearTimeout(closeTimer);
  closeTimer = setTimeout(close, CLOSE_DELAY_MS);
}

function close() {
  clearTimeout(closeTimer);
  if (!current) return;
  current.classList.remove("pinned");
  current.querySelector(".hint-icon").setAttribute("aria-expanded", "false");
  current.querySelector(".hint-tip").hidden = true;
  current = null;
}

// Fixed, so no card or fold clips it: below the icon, or above if it won't fit.
function place(button, tip) {
  const margin = 8;
  const gap = 6;
  const anchor = button.getBoundingClientRect();
  tip.style.left = "0px";
  tip.style.top = "0px";
  const { width, height } = tip.getBoundingClientRect();
  const left = Math.min(Math.max(anchor.left + anchor.width / 2 - width / 2, margin), window.innerWidth - width - margin);
  let top = anchor.bottom + gap;
  if (top + height > window.innerHeight - margin) top = Math.max(margin, anchor.top - gap - height);
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}
