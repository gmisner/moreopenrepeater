import {
  Accordion,
  Button,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from "@mantine/core";
import { useState } from "react";
import { ConfigActions } from "../ConfigActions";
import { ConfigEditDrawer } from "../ConfigEditDrawer";
import { ConfigSummaryCard } from "../ConfigSummaryCard";
import { labeled, InfoNote } from "../FieldHint";
import { TONE_STYLES, TONE_VARIANT_STYLES } from "./constants";
import { assetOptions } from "./helpers";
import type { Asset, ConfigSlice, SoundValues } from "./types";

function toneLabel(style: string) {
  return TONE_STYLES.find((item) => item.value === style)?.label ?? style;
}

function variantLabel(style: string) {
  return TONE_VARIANT_STYLES.find((item) => item.value === style)?.label ?? style;
}

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
  const [editing, setEditing] = useState(false);
  const courtesyClip = assets.find((a) => a.id === sounds.values.courtesy_tone_asset_id)?.filename;
  const idClip = assets.find((a) => a.id === sounds.values.id_asset_id)?.filename;
  const timeoutClip = assets.find((a) => a.id === sounds.values.timeout_tone_asset_id)?.filename;

  return (
    <>
      <ConfigSummaryCard
        title="Sounds"
        dirty={sounds.dirty}
        blurb="Courtesy tone, ID clip, and timeout tone. Preview before saving."
        onEdit={() => setEditing(true)}
        rows={[
          {
            label: "Courtesy",
            value: courtesyClip || toneLabel(sounds.values.courtesy_tone_style),
          },
          {
            label: "After link",
            value: variantLabel(sounds.values.courtesy_tone_link_style),
          },
          {
            label: "After phone",
            value: variantLabel(sounds.values.courtesy_tone_patch_style),
          },
          { label: "Voice ID clip", value: idClip || "Built-in" },
          { label: "Timeout", value: timeoutClip || "Built-in" },
        ]}
      >
        <Group gap="sm" wrap="wrap">
          <Button variant="subtle" loading={previewBusy === "courtesy"} onClick={() => void preview("courtesy_tone", "courtesy")}>
            Preview courtesy
          </Button>
          <Button variant="subtle" loading={previewBusy === "id"} onClick={() => void preview("id", "id")}>
            Preview ID
          </Button>
          <Button variant="subtle" loading={previewBusy === "timeout"} onClick={() => void preview("timeout_tone", "timeout")}>
            Preview timeout
          </Button>
        </Group>
      </ConfigSummaryCard>

      <ConfigEditDrawer
        opened={editing}
        onClose={() => setEditing(false)}
        title="Sounds"
        subtitle="What the repeater transmits after a user unkeys, and for ID / timeout."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void sounds.submit().then((ok) => {
              if (ok) setEditing(false);
            });
          }}
        >
          <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <Select
                label="Built-in courtesy tone"
                data={TONE_STYLES}
                value={sounds.values.courtesy_tone_style}
                onChange={(value) => sounds.setField("courtesy_tone_style", value || "beep")}
                allowDeselect={false}
              />
              <Select
                label={labeled("Courtesy tone clip", "Overrides the built-in tone when set")}
                data={assetOptions(assets, "courtesy_tone")}
                value={sounds.values.courtesy_tone_asset_id || null}
                onChange={(value) => sounds.setField("courtesy_tone_asset_id", value || "")}
                clearable
                searchable
                placeholder="Built-in"
              />
              <Select
                label="Voice ID"
                data={assetOptions(assets, "id")}
                value={sounds.values.id_asset_id || null}
                onChange={(value) => sounds.setField("id_asset_id", value || "")}
                clearable
                searchable
                placeholder="Built-in"
              />
              <Select
                label="Timeout tone"
                data={assetOptions(assets, "timeout_tone")}
                value={sounds.values.timeout_tone_asset_id || null}
                onChange={(value) => sounds.setField("timeout_tone_asset_id", value || "")}
                clearable
                searchable
                placeholder="Built-in"
              />
            </SimpleGrid>

            <Accordion variant="separated" radius="md">
              <Accordion.Item value="link">
                <Accordion.Control>After a linked station</Accordion.Control>
                <Accordion.Panel>
                  <InfoNote>
                    Optional different tone so listeners know a linked station just spoke.
                  </InfoNote>
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
                      placeholder="None"
                    />
                  </SimpleGrid>
                  <Button
                    mt="md"
                    variant="subtle"
                    loading={previewBusy === "link"}
                    onClick={() => void preview("courtesy_tone_link", "link")}
                  >
                    Preview linked tone
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
                      placeholder="None"
                    />
                  </SimpleGrid>
                  <Button
                    mt="md"
                    variant="subtle"
                    loading={previewBusy === "patch"}
                    onClick={() => void preview("courtesy_tone_patch", "patch")}
                  >
                    Preview phone tone
                  </Button>
                </Accordion.Panel>
              </Accordion.Item>
              <Accordion.Item value="custom">
                <Accordion.Control>Custom tone</Accordion.Control>
                <Accordion.Panel>
                  <InfoNote>
                    Up to four parts. Pick &quot;Custom&quot; in a tone list to use it. Pitch 0 is a pause; empty parts are
                    skipped.
                  </InfoNote>
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
                    Preview custom tone
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
        </form>
      </ConfigEditDrawer>
    </>
  );
}
