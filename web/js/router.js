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

// Moves screen readers and keyboard users to the new page, the way a real
// page load would.
function focusHeading() {
  const heading = views.get(currentView).querySelector("h1");
  if (!heading) return;
  heading.tabIndex = -1;
  heading.focus({ preventScroll: true });
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
  document.getElementById("skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    focusHeading();
  });
  window.addEventListener("hashchange", () => {
    show(location.hash.replace(/^#\/?/, ""));
    window.scrollTo(0, 0);
    focusHeading();
  });
  show(location.hash.replace(/^#\/?/, ""));
}
