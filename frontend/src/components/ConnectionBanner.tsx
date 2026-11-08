import { Alert } from "@mantine/core";
import { useEffect, useState } from "react";
import { useReachable } from "../connection";
import { useStatus } from "../status";

const GRACE_MS = 4000;

/** Appears when the controller stops answering. Waits a few seconds so a page load or a blip stays quiet. */
export function ConnectionBanner() {
  const reachable = useReachable();
  const { connected } = useStatus();
  const down = !reachable || !connected;
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (!down) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), GRACE_MS);
    return () => window.clearTimeout(timer);
  }, [down]);

  if (!shown) return null;
  return (
    <Alert color="amber" variant="light" mb="md" role="status" title="Can't reach the repeater right now">
      What you see may be out of date. This page keeps trying and will catch up on its own when the repeater is back.
    </Alert>
  );
}
