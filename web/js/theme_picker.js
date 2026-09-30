export function initThemePicker() {
  const picker = document.getElementById("theme-picker");
  const buttons = [...picker.querySelectorAll("button[data-theme-choice]")];
  const sync = () => {
    const current = document.documentElement.dataset.themeChoice || "auto";
    for (const button of buttons) {
      button.setAttribute("aria-pressed", String(button.dataset.themeChoice === current));
    }
  };
  for (const button of buttons) {
    button.addEventListener("click", () => {
      window.setTheme(button.dataset.themeChoice);
      sync();
    });
  }
  new MutationObserver(sync).observe(document.documentElement, { attributeFilter: ["data-theme-choice"] });
  sync();
}
