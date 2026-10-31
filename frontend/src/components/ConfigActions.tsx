import { Button, Group, Text } from "@mantine/core";

export function ConfigActions({
  dirty,
  saving,
  onReset,
}: {
  dirty: boolean;
  saving: boolean;
  onReset: () => void;
}) {
  return (
    <Group justify="flex-end" mt="lg" gap="sm">
      {dirty ? (
        <Text size="sm" c="amber">
          Unsaved changes
        </Text>
      ) : null}
      {dirty ? (
        <Button type="button" variant="subtle" onClick={onReset}>
          Discard
        </Button>
      ) : null}
      <Button type="submit" loading={saving}>
        Save
      </Button>
    </Group>
  );
}
