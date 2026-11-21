import { Accordion, Anchor, Stack } from "@mantine/core";
import type { ReactNode } from "react";

/** Where the guides live. The app has no docs of its own, so "Read the guide" goes to the project's copy. */
export const GUIDES_URL = "https://github.com/gmisner/moreopenrepeater/blob/main/docs";

/**
 * The one collapsed "How to set this up" section on an install page. Commands someone has to run on the repeater's computer go
 * in here and nowhere else on the page, so the main reading flow stays about what the operator can do.
 * `data-setup-section` lets tests tell this section apart from the rest of the page.
 */
export function SetupSection({
  opened,
  onToggle,
  children,
  guide,
  title = "How to set this up",
}: {
  opened: boolean;
  onToggle: (opened: boolean) => void;
  children: ReactNode;
  /** The guide's file name under docs/, for a "Read the guide" link. */
  guide?: string;
  title?: string;
}) {
  return (
    <div data-setup-section id="setup">
      <Accordion variant="separated" radius="md" value={opened ? "setup" : null} onChange={(value) => onToggle(value === "setup")}>
        <Accordion.Item value="setup">
          <Accordion.Control>{title}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap="sm" maw="75ch">
              {children}
              {guide ? (
                <Anchor href={`${GUIDES_URL}/${guide}`} target="_blank" rel="noreferrer" size="sm">
                  Read the guide
                </Anchor>
              ) : null}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </div>
  );
}

/** Open the setup section and bring it into view, for an empty state's "Show the steps" button. */
export function revealSetup(onToggle: (opened: boolean) => void) {
  onToggle(true);
  window.requestAnimationFrame(() => {
    document.getElementById("setup")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  });
}
