import {
  AppShell as MantineAppShell,
  Box,
  Burger,
  Group,
  NavLink,
  SegmentedControl,
  Stack,
  Text,
  Title,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useEffect, type ReactNode } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { NAV, PAGE_TITLES, TABBAR } from "../nav";
import { roleLabel, useSession } from "../session";
import { useStatus } from "../status";
import { BrandMark } from "./BrandMark";
import { ConnectionPill } from "./ConnectionPill";
import { MiniStatus } from "./MiniStatus";

export function AppShell() {
  const { session, logout } = useSession();
  const { status, connected } = useStatus();
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const [opened, { toggle, close }] = useDisclosure(false);
  const isPhone = useMediaQuery("(max-width: 53.75em)");
  const location = useLocation();
  const navigate = useNavigate();
  const view = location.pathname.replace(/^\//, "") || "dashboard";
  const title = PAGE_TITLES[view] ?? "Dashboard";

  useEffect(() => {
    document.title = `${title} · moreopenrepeater`;
    window.scrollTo(0, 0);
  }, [title]);

  const themeValue = colorScheme === "auto" ? "auto" : colorScheme;

  const nav = (
    <Stack gap="xs" p="md">
      {NAV.map((group) => (
        <Box key={group.label ?? "top"}>
          {group.label ? (
            <Text size="xs" c="dimmed" tt="uppercase" fw={600} px="sm" mb={4} style={{ letterSpacing: "0.08em" }}>
              {group.label}
            </Text>
          ) : null}
          {group.items
            .filter((item) => !item.adminOnly || session?.role === "admin")
            .map((item) => (
              <NavLink
                key={item.id}
                component={Link}
                to={`/${item.id}`}
                label={item.label}
                active={view === item.id}
                onClick={close}
              />
            ))}
        </Box>
      ))}
    </Stack>
  );

  return (
    <MantineAppShell
      header={{ height: isPhone ? 56 : 0 }}
      navbar={{ width: 240, breakpoint: "md", collapsed: { mobile: !opened } }}
      footer={{ height: isPhone ? 56 : 0 }}
      padding="lg"
    >
      <MantineAppShell.Header hiddenFrom="md" px="md">
        <Group h="100%" justify="space-between">
          <Group gap="sm">
            <Burger opened={opened} onClick={toggle} size="sm" aria-label="Open menu" />
            <BrandMark size={26} />
            <Text fw={700} visibleFrom="xs">
              moreopenrepeater
            </Text>
          </Group>
          <MiniStatus status={status} />
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Navbar p={0}>
        <Stack gap="md" p="md" style={{ height: "100%" }}>
          <Group gap="sm" visibleFrom="md">
            <BrandMark size={26} />
            <Text fw={700}>moreopenrepeater</Text>
          </Group>
          <Box
            p="sm"
            style={{
              border: "1px solid var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-md)",
              background: "var(--mantine-color-body)",
            }}
          >
            <Text size="sm" c="dimmed">
              Station
            </Text>
            <Text ff="monospace" fw={700}>
              {(status?.callsign as string) || "(no callsign)"}
            </Text>
          </Box>
          <Box style={{ flex: 1, overflow: "auto" }}>{nav}</Box>
          <Stack gap="sm">
            <SegmentedControl
              size="xs"
              fullWidth
              value={themeValue}
              onChange={(value) => setColorScheme(value as "auto" | "light" | "dark")}
              data={[
                { label: "Auto", value: "auto" },
                { label: "Light", value: "light" },
                { label: "Dark", value: "dark" },
              ]}
            />
            <ConnectionPill connected={connected} />
            {session?.auth_required ? (
              <Box>
                <Text size="sm" c="dimmed">
                  Signed in as <Text span fw={600}>{session.username}</Text>{" "}
                  <Text span size="xs" c="dimmed">
                    {roleLabel(session.role)}
                  </Text>
                </Text>
                <UnstyledButton mt={4} onClick={() => logout()} style={{ color: "var(--mantine-color-dimmed)" }}>
                  Sign out
                </UnstyledButton>
              </Box>
            ) : null}
          </Stack>
        </Stack>
      </MantineAppShell.Navbar>

      <MantineAppShell.Main>
        <Outlet />
      </MantineAppShell.Main>

      <MantineAppShell.Footer hiddenFrom="md" px={0}>
        <Group h="100%" grow gap={0}>
          {TABBAR.map((item) => (
            <UnstyledButton
              key={item.id}
              onClick={() => {
                navigate(`/${item.id}`);
                close();
              }}
              style={{
                height: "100%",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                color: view === item.id ? "var(--mantine-color-accent-4)" : "var(--mantine-color-dimmed)",
                fontSize: "0.72rem",
                fontWeight: 600,
              }}
            >
              {item.label}
            </UnstyledButton>
          ))}
          <UnstyledButton
            onClick={toggle}
            style={{
              height: "100%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              color: opened ? "var(--mantine-color-accent-4)" : "var(--mantine-color-dimmed)",
              fontSize: "0.72rem",
              fontWeight: 600,
            }}
          >
            More
          </UnstyledButton>
        </Group>
      </MantineAppShell.Footer>
    </MantineAppShell>
  );
}

export function Page({ title, children, description }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <Stack gap="md">
      <Box>
        <Title order={1} tabIndex={-1}>
          {title}
        </Title>
        {description ? (
          <Text c="dimmed" maw="75ch">
            {description}
          </Text>
        ) : null}
      </Box>
      {children}
    </Stack>
  );
}
