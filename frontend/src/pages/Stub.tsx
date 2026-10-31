import { Card, Text } from "@mantine/core";
import { Page } from "../layout/AppShell";

export function Stub({ title, description }: { title: string; description?: string }) {
  return (
    <Page title={title} description={description}>
      <Card withBorder padding="lg" radius="md">
        <Text c="dimmed">This page is stubbed during the Mantine migration and will be ported next.</Text>
      </Card>
    </Page>
  );
}
