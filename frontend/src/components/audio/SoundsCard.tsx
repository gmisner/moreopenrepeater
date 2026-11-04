import {
  Accordion,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { ConfigActions } from "../ConfigActions";
import { labeled } from "../FieldHint";
import { TONE_STYLES, TONE_VARIANT_STYLES } from "./constants";
import { assetOptions } from "./helpers";
import type { Asset, ConfigSlice, SoundValues } from "./types";

export function SoundsCard({
  sounds,
  assets,
  customParts,
  previewBusy,
  preview,
  setCustomPart,
}: {
  sounds: ConfigSlice<SoundValues>;
  assets: Asset[];
  customParts: [string, string][];
  previewBusy: string | null;
  preview: (clip: string, label: string, override?: Record<string, unknown>) => void;
  setCustomPart: (index: number, which: 0 | 1, value: string | number) => void;
}) {
  return (
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void sounds.submit();
        }}
      >
        <Stack gap="md">
          <Title order={2}>Sounds</Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Built-in courtesy tone"
              data={TONE_STYLES}
              value={sounds.values.courtesy_tone_style}
              onChange={(value) => sounds.setField("courtesy_tone_style", value || "beep")}
              allowDeselect={false}
            />
            <Select
              label="Courtesy tone clip"
              data={assetOptions(assets, "courtesy_tone")}
              value={sounds.values.courtesy_tone_asset_id || null}
              onChange={(value) => sounds.setField("courtesy_tone_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
            <Select
              label="Voice ID"
              data={assetOptions(assets, "id")}
              value={sounds.values.id_asset_id || null}
              onChange={(value) => sounds.setField("id_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
            <Select
              label="Timeout tone"
              data={assetOptions(assets, "timeout_tone")}
              value={sounds.values.timeout_tone_asset_id || null}
              onChange={(value) => sounds.setField("timeout_tone_asset_id", value || "")}
              clearable
              searchable
              placeholder="(built-in)"
            />
          </SimpleGrid>
          <Text c="dimmed" size="sm" maw="75ch">
            The courtesy tone above follows local users. A different one after a linked station or a phone call tells listeners who just spoke.
          </Text>

          <Accordion variant="separated" radius="md">
            <Accordion.Item value="link">
              <Accordion.Control>After a linked station</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Built-in tone"
                    data={TONE_VARIANT_STYLES}
                    value={sounds.values.courtesy_tone_link_style}
                    onChange={(value) => sounds.setField("courtesy_tone_link_style", value || "same")}
                    allowDeselect={false}
                  />
                  <Select
                    label={labeled("Clip", "Plays instead of the built-in tone.")}
                    data={assetOptions(assets, "courtesy_tone")}
                    value={sounds.values.courtesy_tone_link_asset_id || null}
                    onChange={(value) => sounds.setField("courtesy_tone_link_asset_id", value || "")}
                    clearable
                    searchable
                    placeholder="(none)"
                  />
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "link"}
                  onClick={() => void preview("courtesy_tone_link", "link")}
                >
                  Linked station tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="patch">
              <Accordion.Control>After a phone call</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label="Built-in tone"
                    data={TONE_VARIANT_STYLES}
                    value={sounds.values.courtesy_tone_patch_style}
                    onChange={(value) => sounds.setField("courtesy_tone_patch_style", value || "same")}
                    allowDeselect={false}
                  />
                  <Select
                    label={labeled("Clip", "Plays instead of the built-in tone.")}
                    data={assetOptions(assets, "courtesy_tone")}
                    value={sounds.values.courtesy_tone_patch_asset_id || null}
                    onChange={(value) => sounds.setField("courtesy_tone_patch_asset_id", value || "")}
                    clearable
                    searchable
                    placeholder="(none)"
                  />
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "patch"}
                  onClick={() => void preview("courtesy_tone_patch", "patch")}
                >
                  Phone call tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="custom">
              <Accordion.Control>Custom tone</Accordion.Control>
              <Accordion.Panel>
                <Text c="dimmed" size="sm" mb="md" maw="75ch">
                  Up to four parts, played in order. Pick &quot;Custom&quot; in any tone list above (or for nets) to use it. A pitch of 0 is a pause, and a part left empty is skipped.
                </Text>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  {customParts.map((part, index) => (
                    <Group key={index} grow align="flex-end">
                      <Text size="sm" c="dimmed" style={{ flex: "0 0 auto" }}>
                        Part {index + 1}
                      </Text>
                      <NumberInput
                        label="Pitch"
                        suffix=" Hz"
                        min={0}
                        max={3000}
                        step={10}
                        value={part[0] === "" ? undefined : Number(part[0])}
                        onChange={(value) => setCustomPart(index, 0, value)}
                      />
                      <NumberInput
                        label="Length"
                        suffix=" ms"
                        min={20}
                        max={1000}
                        step={10}
                        value={part[1] === "" ? undefined : Number(part[1])}
                        onChange={(value) => setCustomPart(index, 1, value)}
                      />
                    </Group>
                  ))}
                </SimpleGrid>
                <Button
                  mt="md"
                  variant="subtle"
                  loading={previewBusy === "custom"}
                  onClick={() =>
                    void preview("courtesy_tone", "custom", {
                      courtesy_tone_style: "custom",
                      clear_courtesy_tone_asset_id: true,
                    })
                  }
                >
                  Custom tone
                </Button>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <Group gap="sm" wrap="wrap">
            <Button variant="subtle" loading={previewBusy === "courtesy"} onClick={() => void preview("courtesy_tone", "courtesy")}>
              Courtesy tone
            </Button>
            <Button variant="subtle" loading={previewBusy === "id"} onClick={() => void preview("id", "id")}>
              ID
            </Button>
            <Button variant="subtle" loading={previewBusy === "timeout"} onClick={() => void preview("timeout_tone", "timeout")}>
              Timeout tone
            </Button>
          </Group>
          <ConfigActions dirty={sounds.dirty} saving={sounds.saving} onReset={sounds.reset} />
        </Stack>
      </Card>
  );
}
