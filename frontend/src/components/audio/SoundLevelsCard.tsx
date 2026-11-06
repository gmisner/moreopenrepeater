import { Badge, Box, Button, Card, Group, Slider, Stack, Switch, Text, Title } from "@mantine/core";
import type { Dispatch, SetStateAction } from "react";
import { SIDE_LABELS } from "./constants";
import { soundLevelText } from "./helpers";
import type { SoundLevel } from "./types";

const SIDE_ORDER: SoundLevel["side"][] = ["input", "output", "link_input", "link_output"];

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
  const groups = SIDE_ORDER.map((side) => ({
    side,
    levels: levels.filter((level) => level.side === side),
  })).filter((group) => group.levels.length > 0);

  const patchLevel = (level: SoundLevel, value: number) => {
    setLevels((prev) =>
      prev.map((item) =>
        item.side === level.side && item.control === level.control && item.card === level.card
          ? { ...item, value }
          : item,
      ),
    );
  };

  return (
    <Card withBorder padding="lg" radius="md">
      <Stack gap="md">
        <Title order={2}>Sound card levels</Title>
        <Text c="dimmed" size="sm" maw="75ch">
          Each radio&apos;s USB interface volume controls (repeater and link radio when its devices are set): how loud
          receive comes in, and how loud audio goes out to the transmitter. A change takes effect right away and is
          saved here, then set again whenever that radio&apos;s audio starts. Auto Gain on a CM108 is listed under
          receive — leave it off for stable Digirig levels. A CM108 Mic playback control only loops receive audio and
          isn&apos;t listed.
        </Text>
        {groups.length === 0 ? (
          <Text size="sm" c="dimmed">
            {levelNotes.join(" ") || "No mixer controls found for the selected devices."}
          </Text>
        ) : (
          <Stack gap="xl">
            {groups.map((group) => (
              <Stack key={group.side} gap="sm">
                <Group gap="xs" justify="space-between" align="baseline">
                  <Title order={3} size="h4" style={{ margin: 0 }}>
                    {SIDE_LABELS[group.side]}
                  </Title>
                  <Text size="xs" c="dimmed">
                    card {group.levels[0]?.card}
                  </Text>
                </Group>
                <Stack gap="md">
                  {group.levels.map((level) => {
                    const key = `${level.side}-${level.control}-${level.card}`;
                    const unsaved = !level.saved ? (
                      <Badge color="yellow" variant="light" size="sm">
                        not saved
                      </Badge>
                    ) : null;
                    if (level.kind === "switch") {
                      return (
                        <Group key={key} justify="space-between" align="center" wrap="nowrap">
                          <Group gap="xs">
                            <Text size="sm">{level.control}</Text>
                            {unsaved}
                          </Group>
                          <Switch
                            checked={level.value > 0}
                            disabled={isViewer}
                            onChange={(event) => {
                              const value = event.currentTarget.checked ? 1 : 0;
                              patchLevel(level, value);
                              void setLevel(level, value);
                            }}
                            label={level.value > 0 ? "on" : "off"}
                            labelPosition="left"
                          />
                        </Group>
                      );
                    }
                    return (
                      <Box key={key}>
                        <Group justify="space-between" mb={6} gap="xs" wrap="nowrap">
                          <Group gap="xs">
                            <Text size="sm" fw={500}>
                              {level.control}
                            </Text>
                            {unsaved}
                          </Group>
                          <Text size="sm" ff="monospace" c="dimmed">
                            {soundLevelText(level)}
                          </Text>
                        </Group>
                        <Slider
                          min={level.min}
                          max={level.max}
                          step={1}
                          value={level.value}
                          disabled={isViewer}
                          size="md"
                          color="blue"
                          onChange={(value) => patchLevel(level, value)}
                          onChangeEnd={(value) => void setLevel(level, value)}
                        />
                      </Box>
                    );
                  })}
                </Stack>
              </Stack>
            ))}
          </Stack>
        )}
        {levelNotes.length > 0 && groups.length > 0 ? (
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
