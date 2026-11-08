import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ConfigContext } from "../config";
import { RouteBoundary } from "./RouteBoundary";

function Boom(): never {
  throw new Error("kaboom");
}

function renderAt(path: string, value: Parameters<typeof ConfigContext.Provider>[0]["value"], page = <p>page body</p>) {
  return render(
    <MantineProvider>
      <ConfigContext.Provider value={value}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<RouteBoundary />}>
              <Route path="*" element={page} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ConfigContext.Provider>
    </MantineProvider>,
  );
}

const base = { refresh: vi.fn(), save: vi.fn() };

describe("RouteBoundary", () => {
  it("shows a loading placeholder, not a default form, while settings load", () => {
    renderAt("/timing", { ...base, config: null, loading: true });
    expect(screen.getByRole("status")).toHaveTextContent("Loading settings");
    expect(screen.queryByText("page body")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Timing");
  });

  it("says the settings could not load and offers a retry", async () => {
    const reload = vi.fn();
    renderAt("/timing", { ...base, config: null, loading: false, error: "Internal error", reload });
    expect(screen.getByRole("alert")).toHaveTextContent("Internal error. Nothing has been changed.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("shows the page once settings are there", () => {
    renderAt("/timing", { ...base, config: { callsign: "W7HYE" }, loading: false });
    expect(screen.getByText("page body")).toBeInTheDocument();
  });

  it("does not hold back pages that do not need settings", () => {
    renderAt("/logs", { ...base, config: null, loading: true });
    expect(screen.getByText("page body")).toBeInTheDocument();
  });

  it("catches a crashing page instead of leaving a blank screen", () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    renderAt("/logs", { ...base, config: {}, loading: false }, <Boom />);
    expect(screen.getByRole("alert")).toHaveTextContent("This page ran into a problem");
    quiet.mockRestore();
  });
});
