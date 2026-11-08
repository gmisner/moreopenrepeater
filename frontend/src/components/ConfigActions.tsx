import { Button, Group, Text } from "@mantine/core";

/**
 * Save controls for a settings form. Once something changes, the bar sticks to
 * the bottom of the screen so Save is always in reach on a long page.
 */
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
    <Group className="mor-savebar" data-dirty={dirty || undefined} justify="flex-end" gap="sm" wrap="nowrap">
      {dirty ? (
        <Text size="sm" fw={600} className="mor-savebar-note" role="status">
          <span className="mor-savebar-dot" aria-hidden="true" />
          Unsaved changes
        </Text>
      ) : null}
      {dirty ? (
        <Button type="button" variant="subtle" color="gray" onClick={onReset}>
          Discard
        </Button>
      ) : null}
      <Button type="submit" loading={saving} variant={dirty ? "filled" : "default"}>
        Save
      </Button>
    </Group>
  );
}
