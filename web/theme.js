// Loaded as a plain blocking script in <head>: it has to set data-theme
// before the first paint, or a light-mode visitor sees a dark flash.
(() => {
  const media = window.matchMedia("(prefers-color-scheme: light)");
  const root = document.documentElement;

  function choice() {
    try {
      const saved = localStorage.getItem("theme");
      return saved === "light" || saved === "dark" ? saved : "auto";
    } catch {
      return "auto";
    }
  }

  function apply() {
    const picked = choice();
    root.dataset.themeChoice = picked;
    root.dataset.theme = picked === "auto" ? (media.matches ? "light" : "dark") : picked;
  }

  window.setTheme = (picked) => {
    try {
      if (picked === "auto") localStorage.removeItem("theme");
      else localStorage.setItem("theme", picked);
    } catch {
      // Private browsing can refuse storage; the choice still applies until reload.
    }
    apply();
    if (picked !== "auto") root.dataset.theme = picked;
    root.dataset.themeChoice = picked;
  };

  apply();
  media.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === "theme") apply();
  });
})();
