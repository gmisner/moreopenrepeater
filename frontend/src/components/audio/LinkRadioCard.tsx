import {
  Accordion,
  Badge,
  Button,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from "@mantine/core";
import { useState } from "react";
import { ConfigActions } from "../ConfigActions";
import { ConfigEditDrawer } from "../ConfigEditDrawer";
import { ConfigSummaryCard } from "../ConfigSummaryCard";
import { labeled, InfoNote } from "../FieldHint";
import {
  serialPortOptions,
  serialSelectValue,
  soundDeviceOptions,
  type DeviceAssignment,
} from "../../deviceUsage";
import { COS_METHOD_LABELS, METER_FLOOR_DB, PTT_METHOD_LABELS } from "./constants";
import { CtcssToneSelect } from "./CtcssToneSelect";
import { LevelMeter, shortLabel } from "./helpers";
import { RemoteBaseCard } from "./RemoteBaseCard";
import type { AudioDevice, ConfigSlice, LinkRadioLiveStatus, LinkValues, SerialPort } from "./types";

export function LinkRadioCard({
  link,
  devices,
  devicesError,
  onReloadDevices,
  serialPorts,
  soundUsage,
  serialUsage,
  linkStatus,
  linkRemoteBase,
  linkCosHardware,
  linkTxCtcss,
  remoteOffset,
  remoteTone,
}: {
  link: ConfigSlice<LinkValues>;
  devices: AudioDevice[];
  devicesError?: string | null;
  onReloadDevices?: () => void;
  serialPorts: SerialPort[];
  soundUsage: DeviceAssignment[];
  serialUsage: DeviceAssignment[];
  linkStatus: LinkRadioLiveStatus | null;
  linkRemoteBase: boolean;
  linkCosHardware: boolean;
  linkTxCtcss: string | null;
  remoteOffset: number | "";
  remoteTone: string | null;
}) {
  const [editing, setEditing] = useState(false);
  const modeLabel = linkRemoteBase ? "Remote base" : "Far link";
  const displayName = link.values.link_radio_name.trim() || modeLabel;
  const hasNotes = Boolean(
    link.values.link_radio_model.trim() ||
      link.values.link_radio_frequency.trim() ||
      link.values.link_radio_notes.trim(),
  );

  return (
    <>
      <ConfigSummaryCard
        title="Linked radio"
        badge={link.values.link_radio_enabled ? "on" : "off"}
        badgeColor={link.values.link_radio_enabled ? "green" : "gray"}
        dirty={link.dirty}
        blurb="Second radio for a cross-band or far link. Uses its own USB sound interface."
        onEdit={() => setEditing(true)}
        rows={[
          { label: "Name", value: displayName },
          { label: "Mode", value: modeLabel },
          { label: "Input", value: shortLabel(link.values.link_radio_input_device) },
          { label: "Output", value: shortLabel(link.values.link_radio_output_device) },
          {
            label: "COS / PTT",
            value: `${COS_METHOD_LABELS[link.values.link_radio_cos] ?? link.values.link_radio_cos} · ${PTT_METHOD_LABELS[link.values.link_radio_ptt] ?? link.values.link_radio_ptt}`,
          },
          ...(hasNotes
            ? [
                {
                  label: "Notes",
                  value:
                    [link.values.link_radio_model, link.values.link_radio_frequency].filter(Boolean).join(" · ") ||
                    "Set",
                },
              ]
            : []),
        ]}
      >
        <LevelMeter
          db={linkStatus?.rx_level_db ?? METER_FLOOR_DB}
          running={Boolean(linkStatus?.running)}
          open={Boolean(linkStatus?.running && linkStatus.receiving)}
          thresholdDb={link.values.link_radio_vox_threshold_db}
          showThreshold={link.values.link_radio_cos === "vox"}
        />
        <Group gap="sm" wrap="wrap">
          <Text size="sm">
            RX{" "}
            <Text span fw={700}>
              {linkStatus?.running ? `${linkStatus.rx_level_db.toFixed(0)} dBFS` : "–"}
            </Text>
          </Text>
          <Badge color={linkStatus?.running && linkStatus.receiving ? "green" : "gray"} variant="light">
            carrier
          </Badge>
          <Badge
            color={linkStatus?.running && linkStatus.transmitting ? "red" : "gray"}
            variant="light"
          >
            {linkStatus?.timed_out ? "timeout" : "TX"}
          </Badge>
        </Group>
        {linkStatus?.error || linkStatus?.rig_error ? (
          <Text c="red" size="sm">
            {linkStatus.error || linkStatus.rig_error}
          </Text>
        ) : null}
      </ConfigSummaryCard>

      <ConfigEditDrawer
        opened={editing}
        onClose={() => setEditing(false)}
        title="Linked radio"
        subtitle="Cross-band or remote-base radio on its own Digirig / sound device."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void link.submit().then((ok) => {
              if (ok) setEditing(false);
            });
          }}
        >
          <Stack gap="md">
            <Switch
              label={linkRemoteBase ? "Turn the remote base on" : "Connect the linked radio"}
              checked={link.values.link_radio_enabled}
              onChange={(event) => link.setField("link_radio_enabled", event.currentTarget.checked)}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              <Select
                label={labeled(
                  "Use it as",
                  'A link stays on while connected. A remote base stays off until a "Linked Radio on" macro; users tune it by DTMF.',
                )}
                data={[
                  { value: "link", label: "A link to a far station" },
                  { value: "remote_base", label: "A remote base (tuned by DTMF)" },
                ]}
                renderOption={({ option }) => (
                  <Stack gap={2}>
                    <Text size="sm">{option.label}</Text>
                    <Text size="xs" c="dimmed" maw={280}>
                      {option.value === "remote_base"
                        ? 'Off until a "Linked Radio on" macro; users tune it by DTMF; turns off when idle.'
                        : "Always-on cross-band path to a fixed far station or repeater."}
                    </Text>
                  </Stack>
                )}
                value={link.values.link_radio_mode}
                onChange={(value) => link.setField("link_radio_mode", value || "link")}
                allowDeselect={false}
              />
              <TextInput
                label={labeled("Name", "Shown on the dashboard and in spoken prompts.")}
                placeholder="e.g. Link to club"
                maxLength={40}
                value={link.values.link_radio_name}
                onChange={(event) => link.setField("link_radio_name", event.currentTarget.value)}
              />
            </SimpleGrid>

            <Accordion multiple defaultValue={["devices"]} variant="separated" radius="md">
              <Accordion.Item value="devices">
                <Accordion.Control>Sound devices</Accordion.Control>
                <Accordion.Panel>
                  {devicesError ? (
                    <Text c="red" size="sm" mb="sm">
                      Couldn&apos;t list sound devices: {devicesError}
                      {onReloadDevices ? (
                        <>
                          {" "}
                          <Button variant="subtle" size="compact-xs" onClick={onReloadDevices}>
                            Retry
                          </Button>
                        </>
                      ) : null}
                    </Text>
                  ) : null}
                  {!devicesError && soundDeviceOptions(devices, "inputs", "", []).length === 0 ? (
                    <Text c="dimmed" size="sm" mb="sm">
                      No capture devices yet. Plug in the linked radio&apos;s USB interface, then{" "}
                      {onReloadDevices ? (
                        <Button variant="subtle" size="compact-xs" onClick={onReloadDevices}>
                          refresh the list
                        </Button>
                      ) : (
                        "refresh this page"
                      )}
                      .
                    </Text>
                  ) : null}
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <Select
                      label={labeled(
                        "Input (link receiver)",
                        "Its own sound device, not the repeater's.",
                      )}
                      data={soundDeviceOptions(devices, "inputs", link.values.link_radio_input_device, soundUsage)}
                      value={link.values.link_radio_input_device || null}
                      onChange={(value) => link.setField("link_radio_input_device", value || "")}
                      clearable
                      placeholder="System default"
                      nothingFoundMessage="No input devices"
                    />
                    <Select
                      label="Output (link transmitter)"
                      data={soundDeviceOptions(devices, "outputs", link.values.link_radio_output_device, soundUsage)}
                      value={link.values.link_radio_output_device || null}
                      onChange={(value) => link.setField("link_radio_output_device", value || "")}
                      clearable
                      placeholder="System default"
                      nothingFoundMessage="No output devices"
                    />
                  </SimpleGrid>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item value="cos">
                <Accordion.Control>Carrier detect</Accordion.Control>
                <Accordion.Panel>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <Select
                      label={labeled(
                        "Detect a carrier by",
                        "CM108 COS is on this linked radio's Digirig/URI — not the repeater's.",
                      )}
                      data={[
                        { value: "vox", label: "Audio level (VOX)" },
                        { value: "ctcss", label: "CTCSS tone present" },
                        { value: "cm108", label: "This linked radio's CM108 COS" },
                        { value: "gpio", label: "Raspberry Pi GPIO pin" },
                        { value: "serial", label: "Serial port (CTS, DSR or DCD)" },
                      ]}
                      value={link.values.link_radio_cos}
                      onChange={(value) => link.setField("link_radio_cos", value || "vox")}
                      allowDeselect={false}
                    />
                    {linkCosHardware ? (
                      <Select
                        label={labeled(
                          "COS polarity",
                          "On a serial port, active high means the line is on (asserted) during a carrier.",
                        )}
                        data={[
                          { value: "low", label: "Active low (pulled to ground on carrier)" },
                          { value: "high", label: "Active high" },
                        ]}
                        value={link.values.link_radio_cos_polarity}
                        onChange={(value) => link.setField("link_radio_cos_polarity", value || "low")}
                        allowDeselect={false}
                      />
                    ) : null}
                    {link.values.link_radio_cos === "gpio" ? (
                      <NumberInput
                        label={labeled("COS pin", "BCM number; GPIO23 is header pin 16. 3.3 V only.")}
                        suffix=" GPIO"
                        min={2}
                        max={27}
                        value={link.values.link_radio_cos_gpio_pin}
                        onChange={(value) => link.setField("link_radio_cos_gpio_pin", Number(value) || 0)}
                        required
                      />
                    ) : null}
                    {link.values.link_radio_cos === "serial" ? (
                      <>
                        <Select
                          label={labeled(
                            "COS serial port",
                            "A /dev/serial/by-id name stays the same when adapters are plugged in a different order.",
                          )}
                          data={serialPortOptions(serialPorts, link.values.link_radio_cos_serial_device, serialUsage)}
                          value={serialSelectValue(serialPorts, link.values.link_radio_cos_serial_device)}
                          onChange={(value) => link.setField("link_radio_cos_serial_device", value || "")}
                          searchable
                          clearable
                          placeholder="/dev/serial/by-id/…"
                        />
                        <Select
                          label="COS line"
                          data={[
                            { value: "cts", label: "CTS" },
                            { value: "dsr", label: "DSR" },
                            { value: "dcd", label: "DCD" },
                          ]}
                          value={link.values.link_radio_cos_serial_line}
                          onChange={(value) => link.setField("link_radio_cos_serial_line", value || "cts")}
                          allowDeselect={false}
                        />
                      </>
                    ) : null}
                    {link.values.link_radio_cos === "vox" ? (
                      <NumberInput
                        label={labeled("VOX threshold", "Typical around −40 to −25 dBFS")}
                        suffix=" dBFS"
                        min={-90}
                        max={0}
                        value={link.values.link_radio_vox_threshold_db}
                        onChange={(value) => link.setField("link_radio_vox_threshold_db", Number(value) || 0)}
                        required
                      />
                    ) : null}
                    <NumberInput
                      label={labeled("Squelch-tail removal", "Cut this much from the end of each transmission. 0 = off.")}
                      suffix=" ms"
                      min={0}
                      max={300}
                      step={10}
                      value={link.values.link_radio_squelch_tail_ms}
                      onChange={(value) => link.setField("link_radio_squelch_tail_ms", Number(value) || 0)}
                      required
                    />
                  </SimpleGrid>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item value="ptt">
                <Accordion.Control>PTT</Accordion.Control>
                <Accordion.Panel>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <Select
                      label={labeled(
                        "Key the transmitter with",
                        "CM108 PTT is on this linked radio's own Digirig/URI — not the repeater's.",
                      )}
                      data={[
                        { value: "cm108", label: "This linked radio's CM108 (its Digirig / URI)" },
                        { value: "gpio", label: "Raspberry Pi GPIO pin" },
                        { value: "serial", label: "Serial port (RTS or DTR)" },
                        { value: "none", label: "Nothing (the radio's own VOX keys it)" },
                      ]}
                      value={link.values.link_radio_ptt}
                      onChange={(value) => link.setField("link_radio_ptt", value || "cm108")}
                      allowDeselect={false}
                    />
                    {link.values.link_radio_ptt === "gpio" ? (
                      <NumberInput
                        label={labeled("PTT pin", "BCM number; GPIO24 is header pin 18. Drive the radio through a transistor.")}
                        suffix=" GPIO"
                        min={2}
                        max={27}
                        value={link.values.link_radio_ptt_gpio_pin}
                        onChange={(value) => link.setField("link_radio_ptt_gpio_pin", Number(value) || 0)}
                        required
                      />
                    ) : null}
                    {link.values.link_radio_ptt === "serial" ? (
                      <>
                        <Select
                          label={labeled(
                            "PTT serial port",
                            "PTT and COS can share one adapter on different lines.",
                          )}
                          data={serialPortOptions(serialPorts, link.values.link_radio_ptt_serial_device, serialUsage)}
                          value={serialSelectValue(serialPorts, link.values.link_radio_ptt_serial_device)}
                          onChange={(value) => link.setField("link_radio_ptt_serial_device", value || "")}
                          searchable
                          clearable
                          placeholder="/dev/serial/by-id/…"
                        />
                        <Select
                          label="PTT line"
                          data={[
                            { value: "rts", label: "RTS" },
                            { value: "dtr", label: "DTR" },
                          ]}
                          value={link.values.link_radio_ptt_serial_line}
                          onChange={(value) => link.setField("link_radio_ptt_serial_line", value || "rts")}
                          allowDeselect={false}
                        />
                      </>
                    ) : null}
                    {link.values.link_radio_ptt === "gpio" || link.values.link_radio_ptt === "serial" ? (
                      <Select
                        label={labeled(
                          "PTT polarity",
                          "On a serial port, active high turns the line on (asserts it) to transmit.",
                        )}
                        data={[
                          { value: "high", label: "Active high (pin goes high to transmit)" },
                          { value: "low", label: "Active low" },
                        ]}
                        value={link.values.link_radio_ptt_polarity}
                        onChange={(value) => link.setField("link_radio_ptt_polarity", value || "high")}
                        allowDeselect={false}
                      />
                    ) : null}
                    {link.values.link_radio_ptt !== "none" ? (
                      <NumberInput
                        label={labeled("Transmit delay", <>Key up, then wait this long before any audio. 0 = off.<br />300 ms typical</>)}
                        suffix=" ms"
                        min={0}
                        max={500}
                        step={10}
                        value={link.values.link_radio_tx_delay_ms}
                        onChange={(value) => link.setField("link_radio_tx_delay_ms", Number(value) || 0)}
                        required
                      />
                    ) : null}
                  </SimpleGrid>
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item value="tx">
                <Accordion.Control>Transmit audio</Accordion.Control>
                <Accordion.Panel>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <NumberInput
                      label={labeled("Transmit gain", "Applied to audio sent out the linked radio.")}
                      suffix=" dB"
                      min={-40}
                      max={20}
                      step={0.5}
                      decimalScale={1}
                      value={link.values.link_radio_tx_gain_db}
                      onChange={(value) => link.setField("link_radio_tx_gain_db", Number(value) || 0)}
                      required
                    />
                    <CtcssToneSelect
                      label={labeled("Transmit CTCSS tone", "For a far repeater that needs a tone to open.")}
                      value={linkTxCtcss}
                      onChange={(value) =>
                        link.setField("link_radio_tx_ctcss_hz", value == null || value === "" ? "" : Number(value))
                      }
                    />
                    <NumberInput
                      label={labeled("Link timeout", "Longest one transmission out the linked radio.")}
                      suffix=" s"
                      min={30}
                      max={1800}
                      value={link.values.link_radio_timeout}
                      onChange={(value) => link.setField("link_radio_timeout", Number(value) || 0)}
                      required
                    />
                  </SimpleGrid>
                  <Switch
                    mt="md"
                    label="Courtesy tone on the link when a local user unkeys"
                    checked={link.values.link_radio_courtesy_tone}
                    onChange={(event) => link.setField("link_radio_courtesy_tone", event.currentTarget.checked)}
                  />
                </Accordion.Panel>
              </Accordion.Item>

              {linkRemoteBase ? (
                <RemoteBaseCard link={link} remoteOffset={remoteOffset} remoteTone={remoteTone} />
              ) : null}

              <Accordion.Item value="notes">
                <Accordion.Control>Station notes · optional</Accordion.Control>
                <Accordion.Panel>
                  <InfoNote label="Operator fields">
                    For operators only — the controller does not use these fields.
                  </InfoNote>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <TextInput
                      label={labeled("Radio model", "Optional")}
                      placeholder="e.g. model"
                      maxLength={80}
                      value={link.values.link_radio_model}
                      onChange={(event) => link.setField("link_radio_model", event.currentTarget.value)}
                    />
                    <TextInput
                      label={labeled("Frequency", "Optional note for a fixed link")}
                      placeholder="e.g. 146.94 / 146.34"
                      maxLength={80}
                      value={link.values.link_radio_frequency}
                      onChange={(event) => link.setField("link_radio_frequency", event.currentTarget.value)}
                    />
                  </SimpleGrid>
                  <Textarea
                    mt="md"
                    label={labeled("Notes", "Optional free-form note")}
                    placeholder="Optional note for operators"
                    maxLength={500}
                    minRows={2}
                    autosize
                    value={link.values.link_radio_notes}
                    onChange={(event) => link.setField("link_radio_notes", event.currentTarget.value)}
                  />
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <ConfigActions dirty={link.dirty} saving={link.saving} onReset={link.reset} />
          </Stack>
        </form>
      </ConfigEditDrawer>
    </>
  );
}
