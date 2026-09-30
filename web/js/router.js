const DEFAULT_VIEW = "dashboard";
const views = new Map([...document.querySelectorAll("[data-view]")].map((el) => [el.dataset.view, el]));
const navLinks = document.querySelectorAll("[data-nav]");
const sidebar = document.getElementById("sidebar");
const navToggle = document.getElementById("nav-toggle");

export const router = new EventTarget();
export let currentView = null;

function show(name) {
  if (!views.has(name)) name = DEFAULT_VIEW;
  currentView = name;
  for (const [viewName, el] of views) el.hidden = viewName !== name;
  for (const link of navLinks) {
    const active = link.dataset.nav === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${views.get(name).dataset.title} · moreopenrepeater`;
  setMenuOpen(false);
  router.dispatchEvent(new CustomEvent("change", { detail: name }));
}

function setMenuOpen(open) {
  sidebar.classList.toggle("open", open);
  navToggle.setAttribute("aria-expanded", String(open));
}

export function initRouter() {
  navToggle.addEventListener("click", () => setMenuOpen(!sidebar.classList.contains("open")));
  for (const link of navLinks) link.addEventListener("click", () => setMenuOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sidebar.classList.contains("open")) {
      setMenuOpen(false);
      navToggle.focus();
    }
  });
  window.addEventListener("hashchange", () => show(location.hash.replace(/^#\/?/, "")));
  show(location.hash.replace(/^#\/?/, ""));
}
