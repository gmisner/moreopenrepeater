import { useEffect } from "react";
import { NavigationProgress, nprogress } from "@mantine/nprogress";
import { LoadingBlock } from "../components/StateCards";
import "./nav-progress.css";

/** A page that is already loaded swaps in right away. Only show the bar if the wait is long enough to notice. */
export const NAV_PROGRESS_DELAY_MS = 150;

/** The thin bar at the top of the window. Mount once, inside MantineProvider. Nothing runs until `start`. */
export function NavProgressBar() {
  return <NavigationProgress className="mor-nprogress" color="var(--mor-brand-blue-hover)" size={3} />;
}

/** The Suspense fallback for a lazy page. While it is on screen the bar runs, and it finishes when the page replaces it.
 *  A cached page never mounts this at all, so fast navigations do not flash the bar.
 */
export function PageLoading() {
  useEffect(() => {
    let started = false;
    const timer = setTimeout(() => {
      started = true;
      nprogress.start();
    }, NAV_PROGRESS_DELAY_MS);
    return () => {
      clearTimeout(timer);
      if (started) nprogress.complete();
    };
  }, []);
  return <LoadingBlock label="Loading page" />;
}
