import { Menu, Stack, Text, UnstyledButton } from "@mantine/core";
import { roleLabel, type Session } from "../session";
import { ThemePicker } from "./ThemePicker";

/** Per-browser display settings and sign out, tucked behind one button. */
export function UserMenu({ session, onSignOut }: { session: Session | null; onSignOut: () => void }) {
  const signedIn = Boolean(session?.auth_required);
  return (
    <Menu position="bottom-end" width={248} withinPortal shadow="md" keepMounted={false}>
      <Menu.Target>
        <UnstyledButton className="mor-usermenu" aria-label="Display settings and account">
          <span className="mor-usermenu-name">{signedIn ? session?.username : "Display"}</span>
          {signedIn ? <span className="mor-usermenu-role">{roleLabel(session?.role)}</span> : null}
          <svg className="mor-usermenu-chevron" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">
            <path d="m6 15 6-6 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown p="md">
        <Stack gap="md">
          <ThemePicker />
          {signedIn ? (
            <>
              <Menu.Divider />
              <Text size="sm" c="dimmed">
                Signed in as <Text span fw={600} c="var(--mantine-color-text)">{session?.username}</Text>
              </Text>
              <Menu.Item onClick={onSignOut} style={{ marginInline: "calc(var(--mantine-spacing-md) * -1)", width: "auto" }}>
                Sign out
              </Menu.Item>
            </>
          ) : null}
        </Stack>
      </Menu.Dropdown>
    </Menu>
  );
}
