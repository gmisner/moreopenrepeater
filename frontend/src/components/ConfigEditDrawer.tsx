import { Drawer, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";

/** Slide-over editor for dense config. Keep overview cards scannable. */
export function ConfigEditDrawer({
  opened,
  onClose,
  title,
  subtitle,
  children,
  size = "xl",
}: {
  opened: boolean;
  onClose: () => void;
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  size?: string | number;
}) {
  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="right"
      size={size}
      title={
        <Stack gap={2}>
          <Text fw={700} size="lg">
            {title}
          </Text>
          {subtitle ? (
            <Text size="sm" c="dimmed" fw={400}>
              {subtitle}
            </Text>
          ) : null}
        </Stack>
      }
      padding="md"
    >
      {children}
    </Drawer>
  );
}
