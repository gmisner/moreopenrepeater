import { Component, Suspense, type ErrorInfo, type ReactNode } from "react";
import { Title } from "@mantine/core";
import { Outlet, useLocation } from "react-router-dom";
import { ErrorCard, LoadingBlock } from "../components/StateCards";
import { useConfig } from "../config";
import { PAGE_TITLES } from "../nav";

/** Pages that are a form over the saved settings. Without those settings they would show made-up defaults. */
const NEEDS_CONFIG = new Set([
  "timing",
  "identification",
  "audio",
  "macros",
  "net",
  "mailbox",
  "weather",
  "aprs",
  "autopatch",
  "allstar",
  "dmr",
  "sensors",
  "recordings",
  "backup",
]);

type Boundary = { children: ReactNode };

class Catcher extends Component<Boundary, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <ErrorCard title="This page ran into a problem" onRetry={() => location.reload()} retryLabel="Reload the page">
        Your settings are safe. Reloading usually fixes it. If it keeps happening, the Logs page may say why.
      </ErrorCard>
    );
  }
}

function sentence(text: string) {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

function ConfigGate({ view, children }: { view: string; children: ReactNode }) {
  const { config, loading, error, reload } = useConfig();
  if (config) return <>{children}</>;
  return (
    <>
      <Title order={1} className="mor-page-title" mb="md">
        {PAGE_TITLES[view] ?? ""}
      </Title>
      {loading ? (
        <LoadingBlock label="Loading settings" />
      ) : (
        <ErrorCard title="Couldn't load the settings" onRetry={reload}>
          {sentence(error ?? "The repeater didn't answer")} Nothing has been changed.
        </ErrorCard>
      )}
    </>
  );
}

/** Wraps every page: catches crashes, and holds back settings pages until their settings have loaded.
 *  Suspense lives here (not around AppShell) so nav/title update immediately while a lazy page loads.
 *  Putting Suspense above the shell leaves the old Dashboard painted when the URL has already changed.
 */
export function RouteBoundary() {
  const { pathname } = useLocation();
  const view = pathname.replace(/^\//, "").split("/")[0] || "dashboard";
  const page = (
    <Suspense fallback={<LoadingBlock label="Loading page" />}>
      <Outlet />
    </Suspense>
  );
  const body = NEEDS_CONFIG.has(view) ? <ConfigGate view={view}>{page}</ConfigGate> : page;
  return <Catcher key={pathname}>{body}</Catcher>;
}

