import {
  AppShell as MantineAppShell,
  Box,
  Burger,
  Collapse,
  Group,
  NavLink,
  Stack,
  Text,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "../icons/Icon";
import { NAV, PAGE_TITLES, TABBAR } from "../nav";
import { useSession } from "../session";
import { useStatusCallsign } from "../status";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ServerUpdatedBanner } from "../components/ServerUpdatedBanner";
import { BrandLockup, BrandMark } from "./BrandMark";
import { ConnectionStatus } from "./ConnectionStatus";
import { MiniStatus } from "./MiniStatus";
import { RouteBoundary } from "./RouteBoundary";
import { UserMenu } from "./UserMenu";
import "./shell.css";
import "./mor-brand.css";

export function AppShell() {
  const { session, logout } = useSession();
  const callsign = useStatusCallsign();
  const [opened, { toggle, close }] = useDisclosure(false);
  const isPhone = useMediaQuery("(max-width: 53.75em)");
  const location = useLocation();
  const navigate = useNavigate();
  const view = location.pathname.replace(/^\//, "") || "dashboard";
  const title = PAGE_TITLES[view] ?? "Dashboard";

  useEffect(() => {
    document.title = `${title} · MOR | MoreOpenRepeater`;
    window.scrollTo(0, 0);
  }, [title]);

  // Operate and Repeater start open; a group also opens when the current page is in it.
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(["Operate", "Repeater"]));
  const toggleGroup = (label: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  const nav = (
    <Stack gap={2} px="xs">
      {NAV.map((group) => {
        const items = group.items.filter((item) => !item.adminOnly || session?.role === "admin");
        if (!items.length) return null;
        const hasActive = items.some((item) => item.id === view);
        const open = hasActive || openGroups.has(group.label);
        const panelId = `nav-group-${group.label.toLowerCase()}`;
        return (
          <Box key={group.label} mb={4}>
            <UnstyledButton
              className="mor-nav-group"
              onClick={() => toggleGroup(group.label)}
              aria-expanded={open}
              aria-controls={panelId}
              disabled={hasActive}
            >
              <span>{group.label}</span>
              <svg className="mor-nav-group-chevron" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" data-open={open || undefined}>
                <path d="m9 6 6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </UnstyledButton>
            <Collapse expanded={open} id={panelId}>
              {items.map((item) => (
                <NavLink
                  key={item.id}
                  className="mor-nav-link"
                  component={Link}
                  to={`/${item.id}`}
                  label={item.label}
                  leftSection={<Icon name={item.icon} size={18} />}
                  active={view === item.id}
                  onClick={close}
                  variant="subtle"
                />
              ))}
            </Collapse>
          </Box>
        );
      })}
    </Stack>
  );

  return (
    <MantineAppShell
      className="mor-shell"
      layout="alt"
      header={{ height: isPhone ? 56 : 72 }}
      navbar={{ width: 248, breakpoint: "md", collapsed: { mobile: !opened } }}
      footer={{ height: isPhone ? 56 : 0 }}
      padding="lg"
    >
      <MantineAppShell.Header className="mor-shell-header" px="md">
        <Group h="100%" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap" hiddenFrom="md">
            <Burger opened={opened} onClick={toggle} size="sm" aria-label="Open menu" />
            <BrandMark size={32} />
            <Text fw={900} visibleFrom="xs" className="mor-brand-name">
              MOR
            </Text>
          </Group>
          <Box visibleFrom="md" className="mor-shell-title">
            <Text className="mor-shell-eyebrow">MOR · MoreOpenRepeater</Text>
            <Title order={2} className="mor-shell-heading">
              {title}
            </Title>
          </Box>
          <Group gap="sm" wrap="nowrap">
            <Box hiddenFrom="md">
              <MiniStatus />
            </Box>
            <Box visibleFrom="md" className="mor-shell-header-actions">
              <UserMenu session={session} onSignOut={() => void logout()} />
            </Box>
          </Group>
        </Group>
      </MantineAppShell.Header>

      <MantineAppShell.Navbar p={0} className="mor-shell-nav">
        <Stack gap="md" p="md" style={{ height: "100%" }}>
          <Box visibleFrom="md">
            <BrandLockup />
          </Box>
          <Box className="mor-station">
            {callsign ? (
              <Text ff="monospace" fw={700} size="lg" component={Link} to="/identification" className="mor-station-call">
                {callsign}
              </Text>
            ) : (
              <Text size="sm" component={Link} to="/identification" className="mor-station-call">
                Set a callsign
              </Text>
            )}
          </Box>
          <Box style={{ flex: 1, overflow: "auto" }}>{nav}</Box>
          <Stack gap="xs">
            <ConnectionStatus />
            <Text size="xs" c="dimmed" className="mor-shell-version">
              RADIO. REIMAGINED.
            </Text>
            <Box hiddenFrom="md">
              <UserMenu session={session} onSignOut={() => void logout()} />
            </Box>
          </Stack>
        </Stack>
      </MantineAppShell.Navbar>

      <MantineAppShell.Main className="mor-shell-main">
        <ServerUpdatedBanner />
        <ConnectionBanner />
        <RouteBoundary />
      </MantineAppShell.Main>

      <MantineAppShell.Footer hiddenFrom="md" px={0}>
        <Group h="100%" grow gap={0}>
          {TABBAR.map((item) => (
            <UnstyledButton
              key={item.id}
              className="mor-tabbar-btn"
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
                color: view === item.id ? "var(--mor-brand-blue)" : "var(--mantine-color-dimmed)",
                fontSize: "0.72rem",
                fontWeight: 600,
              }}
            >
              <Icon name={item.icon} size={24} />
              {item.label}
            </UnstyledButton>
          ))}
          <UnstyledButton
            className="mor-tabbar-btn"
            onClick={toggle}
            style={{
              height: "100%",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              color: opened ? "var(--mor-brand-blue)" : "var(--mantine-color-dimmed)",
              fontSize: "0.72rem",
              fontWeight: 600,
            }}
          >
            <Icon name="more" size={24} />
            More
          </UnstyledButton>
        </Group>
      </MantineAppShell.Footer>
    </MantineAppShell>
  );
}

export function Page({
  title,
  children,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  // Desktop title lives in the shell header. Skip an empty page-header row so
  // pages without description/actions (e.g. Dashboard) don't leave a dead gap.
  const desktopHeader = Boolean(description || actions);
  return (
    <Stack gap="md" className="mor-page">
      <Group className="mor-page-header" justify="space-between" align="flex-end" wrap="wrap" gap="sm" hiddenFrom="md">
        <Box>
          <Title order={1} className="mor-page-title" tabIndex={-1}>
            {title}
          </Title>
          {description ? (
            <Text c="dimmed" maw="75ch">
              {description}
            </Text>
          ) : null}
        </Box>
        {actions}
      </Group>
      {desktopHeader ? (
        <Group className="mor-page-header mor-page-header-desktop" justify="space-between" align="flex-end" wrap="wrap" gap="sm" visibleFrom="md">
          {description ? (
            <Text c="dimmed" maw="75ch">
              {description}
            </Text>
          ) : (
            <span />
          )}
          {actions}
        </Group>
      ) : null}
      {children}
    </Stack>
  );
}
