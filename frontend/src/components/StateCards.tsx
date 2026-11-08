import { Button, Card, Group, Skeleton, Stack, Text, VisuallyHidden } from "@mantine/core";
import type { ReactNode } from "react";

/** Quiet placeholder shaped like a settings card, shown while a page's data is on its way. */
export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <Card withBorder padding="lg" radius="md" role="status" aria-busy="true">
      <VisuallyHidden>{label}</VisuallyHidden>
      <Stack gap="md" aria-hidden="true">
        <Skeleton height={14} width="30%" />
        <Group grow gap="md">
          <Skeleton height={36} />
          <Skeleton height={36} />
        </Group>
        <Skeleton height={36} width="60%" />
        <Skeleton height={14} width="45%" />
      </Stack>
    </Card>
  );
}

/** A failed load, said plainly, with a way to try again. */
export function ErrorCard({
  title,
  children,
  onRetry,
  retryLabel = "Try again",
}: {
  title: string;
  children?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <Card withBorder padding="lg" radius="md" role="alert">
      <Stack gap="sm" align="flex-start">
        <Text fw={600}>{title}</Text>
        {children ? (
          <Text size="sm" c="dimmed">
            {children}
          </Text>
        ) : null}
        {onRetry ? (
          <Button variant="default" onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null}
      </Stack>
    </Card>
  );
}
