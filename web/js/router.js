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
  for (const link of navLinks) link.classList.toggle("active", link.dataset.nav === name);
  document.title = `${views.get(name).dataset.title} · moreopenrepeater`;
  sidebar.classList.remove("open");
  navToggle.setAttribute("aria-expanded", "false");
  router.dispatchEvent(new CustomEvent("change", { detail: name }));
}

export function initRouter() {
  navToggle.addEventListener("click", () => {
    const open = sidebar.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", String(open));
  });
  window.addEventListener("hashchange", () => show(location.hash.replace(/^#\/?/, "")));
  show(location.hash.replace(/^#\/?/, ""));
}
