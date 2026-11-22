import { MantineProvider } from "@mantine/core";
import { act, render, screen } from "@testing-library/react";
import { lazy } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfigContext } from "../config";
import { NAV_PROGRESS_DELAY_MS, NavProgressBar, PageLoading } from "./NavProgress";
import { RouteBoundary } from "./RouteBoundary";

const bar = vi.hoisted(() => ({ start: vi.fn(), complete: vi.fn() }));

vi.mock("@mantine/nprogress", async (original) => ({
  ...(await original<typeof import("@mantine/nprogress")>()),
  nprogress: { ...bar },
}));

beforeEach(() => {
  vi.useFakeTimers();
  bar.start.mockClear();
  bar.complete.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

function wrap(ui: React.ReactNode) {
  return <MantineProvider>{ui}</MantineProvider>;
}

describe("PageLoading", () => {
  it("does not touch the bar when the page arrives right away", () => {
    const { unmount } = render(wrap(<PageLoading />));
    act(() => {
      vi.advanceTimersByTime(NAV_PROGRESS_DELAY_MS - 1);
    });
    unmount();
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(bar.start).not.toHaveBeenCalled();
    expect(bar.complete).not.toHaveBeenCalled();
  });

  it("starts the bar after the delay and finishes it when the page replaces it", () => {
    const { unmount } = render(wrap(<PageLoading />));
    expect(screen.getByRole("status")).toHaveTextContent("Loading page");
    act(() => {
      vi.advanceTimersByTime(NAV_PROGRESS_DELAY_MS);
    });
    expect(bar.start).toHaveBeenCalledOnce();
    expect(bar.complete).not.toHaveBeenCalled();
    unmount();
    expect(bar.complete).toHaveBeenCalledOnce();
  });
});

describe("RouteBoundary with a lazy page", () => {
  it("runs the bar while the page chunk loads and finishes when it renders", async () => {
    let release: (page: { default: () => React.JSX.Element }) => void = () => {};
    const Slow = lazy(() => new Promise<{ default: () => React.JSX.Element }>((resolve) => (release = resolve)));
    const value = { refresh: vi.fn(), save: vi.fn(), config: {}, loading: false } as unknown as Parameters<
      typeof ConfigContext.Provider
    >[0]["value"];
    render(
      wrap(
        <ConfigContext.Provider value={value}>
          <MemoryRouter initialEntries={["/logs"]}>
            <Routes>
              <Route element={<RouteBoundary />}>
                <Route path="*" element={<Slow />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </ConfigContext.Provider>,
      ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading page");
    act(() => {
      vi.advanceTimersByTime(NAV_PROGRESS_DELAY_MS);
    });
    expect(bar.start).toHaveBeenCalledOnce();

    await act(async () => {
      release({ default: () => <p>the page</p> });
    });
    expect(screen.getByText("the page")).toBeInTheDocument();
    expect(bar.complete).toHaveBeenCalledOnce();
  });
});

describe("NavProgressBar", () => {
  it("mounts without starting anything on its own", () => {
    render(wrap(<NavProgressBar />));
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(bar.start).not.toHaveBeenCalled();
  });
});
