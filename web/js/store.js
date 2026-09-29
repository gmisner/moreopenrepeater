// Shared client-side copies of server state. Views subscribe with
// store.addEventListener("config" | "assets" | "macros" | "status" | "gpio", ...)
// and read the latest value from store.state.
class Store extends EventTarget {
  state = { config: null, assets: [], macros: [], status: null, gpio: null };

  set(key, value) {
    this.state[key] = value;
    this.dispatchEvent(new CustomEvent(key, { detail: value }));
  }
}

export const store = new Store();
