import { Badge, Box, Button, Card, Group, Slider, Stack, Text, Title } from "@mantine/core";
import type { Dispatch, SetStateAction } from "react";
import { SIDE_LABELS } from "./constants";
import { soundLevelText } from "./helpers";
import type { SoundLevel } from "./types";

export function SoundLevelsCard({
  levels,
  levelNotes,
  unsavedLevels,
  levelsSaving,
  isViewer,
  setLevels,
  setLevel,
  saveUnsavedLevels,
}: {
  levels: SoundLevel[];
  levelNotes: string[];
  unsavedLevels: boolean;
  levelsSaving: boolean;
  isViewer: boolean;
  setLevels: Dispatch<SetStateAction<SoundLevel[]>>;
  setLevel: (level: SoundLevel, value: number) => void;
  saveUnsavedLevels: () => void;
}) {
  return (
      <Card withBorder padding="lg" radius="md">
        <Stack gap="md">
          <Title order={2}>Sound card levels</Title>
          <Text c="dimmed" size="sm" maw="75ch">
            Each radio&apos;s USB interface volume controls (repeater and link radio when its devices are set): how loud
            receive comes in, and how loud audio goes out to the transmitter. A change takes effect right away and is
            saved here, then set again whenever that radio&apos;s audio starts. A CM108 Mic playback control only loops
            receive audio and isn&apos;t listed.
          </Text>
          {levels.length === 0 ? (
            <Text size="sm" c="dimmed">
              {levelNotes.join(" ") || "No mixer controls found for the selected devices."}
            </Text>
          ) : (
            <Stack gap="lg">
              {levels.map((level) => (
                <Box key={`${level.side}-${level.control}-${level.card}`}>
                  <Group justify="space-between" mb={4}>
                    <Text size="sm">
                      {SIDE_LABELS[level.side]}{" "}
                      <Text span c="dimmed" size="xs">
                        {level.control}, card {level.card}
                      </Text>
                      {!level.saved ? (
                        <Badge ml="xs" color="yellow" variant="light" size="sm">
                          not saved
                        </Badge>
                      ) : null}
                    </Text>
                    <Text size="sm" ff="monospace">
                      {soundLevelText(level)}
                    </Text>
                  </Group>
                  <Slider
                    min={level.min}
                    max={level.max}
                    step={1}
                    value={level.value}
                    disabled={isViewer}
                    onChange={(value) => {
                      setLevels((prev) =>
                        prev.map((item) =>
                          item.side === level.side && item.control === level.control && item.card === level.card
                            ? { ...item, value }
                            : item,
                        ),
                      );
                    }}
                    onChangeEnd={(value) => void setLevel(level, value)}
                  />
                </Box>
              ))}
            </Stack>
          )}
          {levelNotes.length > 0 && levels.length > 0 ? (
            <Text size="sm" c="dimmed">
              {levelNotes.join(" ")}
            </Text>
          ) : null}
          {!isViewer && unsavedLevels ? (
            <Group justify="space-between">
              <Text size="sm" c="dimmed">
                Some levels were set outside the dashboard and aren&apos;t saved yet.
              </Text>
              <Button variant="subtle" loading={levelsSaving} onClick={() => void saveUnsavedLevels()}>
                Save current levels
              </Button>
            </Group>
          ) : null}
        </Stack>
      </Card>
  );
}
