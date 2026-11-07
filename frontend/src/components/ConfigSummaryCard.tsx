import { Badge, Button, Card, Group, Stack, Text, Title } from "@mantine/core";
import type { ReactNode } from "react";

export type SummaryRow = { label: string; value: ReactNode };

/** Read-only overview card with an Edit action — Graywolf-style summary → form. */
export function ConfigSummaryCard({
  title,
  badge,
  badgeColor = "gray",
  blurb,
  rows,
  dirty = false,
  onEdit,
  editLabel = "Edit…",
  secondaryAction,
  children,
}: {
  title: string;
  badge?: string;
  badgeColor?: string;
  blurb?: ReactNode;
  rows?: SummaryRow[];
  dirty?: boolean;
  onEdit?: () => void;
  editLabel?: string;
  secondaryAction?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <div>
            <Group gap="sm" align="center">
              <Title order={2}>{title}</Title>
              {badge ? (
                <Badge color={badgeColor} variant="light">
                  {badge}
                </Badge>
              ) : null}
              {dirty ? (
                <Badge color="amber" variant="light">
                  Unsaved
                </Badge>
              ) : null}
            </Group>
            {blurb ? (
              <Text c="dimmed" size="sm" maw="75ch" mt={4}>
                {blurb}
              </Text>
            ) : null}
          </div>
          <Group gap="sm">
            {secondaryAction}
            {onEdit ? (
              <Button variant="light" onClick={onEdit}>
                {editLabel}
              </Button>
            ) : null}
          </Group>
        </Group>

        {rows && rows.length > 0 ? (
          <Stack gap={6}>
            {rows.map((row) => (
              <Group key={row.label} justify="space-between" gap="md" wrap="nowrap" align="flex-start">
                <Text size="sm" c="dimmed" style={{ flex: "0 0 auto" }}>
                  {row.label}
                </Text>
                <Text size="sm" ta="right" style={{ minWidth: 0, wordBreak: "break-word" }}>
                  {row.value || "—"}
                </Text>
              </Group>
            ))}
          </Stack>
        ) : null}

        {children}
      </Stack>
    </Card>
  );
}
