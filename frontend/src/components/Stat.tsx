import { Card, Text } from "@mantine/core";

/** A labeled figure: the number leads, the label and note support it. */
export function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card withBorder padding="md" radius="md" className="mor-stat-card">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text className="mor-num" fw={700} style={{ fontSize: "1.5rem" }}>
        {value}
      </Text>
      {note ? (
        <Text size="xs" c="dimmed">
          {note}
        </Text>
      ) : null}
    </Card>
  );
}
