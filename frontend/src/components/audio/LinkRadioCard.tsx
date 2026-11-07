import {
  Accordion,
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { ConfigActions } from "../ConfigActions";
import { labeled } from "../FieldHint";
import {
  serialPortOptions,
  serialSelectValue,
  soundDeviceOptions,
  type DeviceAssignment,
} from "../../deviceUsage";
import { CtcssToneSelect } from "./CtcssToneSelect";
import { RemoteBaseCard } from "./RemoteBaseCard";
import type { AudioDevice, ConfigSlice, LinkValues, SerialPort } from "./types";

export function LinkRadioCard({
  link,
  devices,
  devicesError,
  onReloadDevices,
  serialPorts,
  soundUsage,
  serialUsage,
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
  linkRemoteBase: boolean;
  linkCosHardware: boolean;
  linkTxCtcss: string | null;
  remoteOffset: number | "";
  remoteTone: string | null;
}) {
  return (
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void link.submit();
        }}
      >
        <Stack gap="md">
          <Group justify="space-between" align="center">
            <Title order={2}>Linked Radio</Title>
            <Badge color={link.values.link_radio_enabled ? "green" : "gray"} variant="light">
              {link.values.link_radio_enabled ? "on" : "off"}
            </Badge>
          </Group>
          <Text c="dimmed" size="sm" maw="75ch">
            A second radio on its own sound device for cross-band or a far link. Local users go out the link;
            what it hears is transmitted here. It never transmits over the far end. Give it its own Digirig
            (or other CM108) — separate USB dongle from the repeater radio&apos;s.
          </Text>
          <Switch
            label={linkRemoteBase ? "Turn the remote base on" : "Connect the linked radio"}
            checked={link.values.link_radio_enabled}
            onChange={(event) => link.setField("link_radio_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label={labeled(
                "Use it as",
                'A link to a far station stays on while connected: local users go out that radio, and what it hears is repeated here (fixed frequency on the radio). A remote base stays off until a "Linked Radio on" macro; users tune frequency, shift and tone by DTMF through Hamlib, and it turns itself off when idle.',
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
              label={labeled("Name", "Shown on the dashboard and in spoken prompts for this linked radio.")}
              placeholder="Link to W1XYZ"
              maxLength={40}
              value={link.values.link_radio_name}
              onChange={(event) => link.setField("link_radio_name", event.currentTarget.value)}
            />
            <TextInput
              label={labeled("Radio model", "Operator note only — not used by the controller.")}
              placeholder="Baofeng UV-5R"
              maxLength={80}
              value={link.values.link_radio_model}
              onChange={(event) => link.setField("link_radio_model", event.currentTarget.value)}
            />
            <TextInput
              label={labeled(
                "Frequency",
                "Operator note for the fixed link frequency (or pair). Remote base tune settings are below when that mode is selected.",
              )}
              placeholder="146.94 / 146.34"
              maxLength={80}
              value={link.values.link_radio_frequency}
              onChange={(event) => link.setField("link_radio_frequency", event.currentTarget.value)}
            />
          </SimpleGrid>
          <Textarea
            label={labeled("Notes", "Anything else operators should know about this linked radio.")}
            placeholder="Antenna on the south tower; Digirig Lite on USB3; keep the HT away from the Pi."
            maxLength={500}
            minRows={2}
            autosize
            value={link.values.link_radio_notes}
            onChange={(event) => link.setField("link_radio_notes", event.currentTarget.value)}
          />

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
                    label={labeled("Input (link receiver)", "Its own sound device, not the repeater's. Look for Card 3 vs Card 4 when two Digirigs are plugged in.")}
                    data={soundDeviceOptions(devices, "inputs", link.values.link_radio_input_device, soundUsage)}
                    value={link.values.link_radio_input_device || null}
                    onChange={(value) => link.setField("link_radio_input_device", value || "")}
                    clearable
                    placeholder="System default"
                    nothingFoundMessage="No input devices"
                    comboboxProps={{ withinPortal: true, zIndex: 400 }}
                  />
                  <Select
                    label="Output (link transmitter)"
                    data={soundDeviceOptions(devices, "outputs", link.values.link_radio_output_device, soundUsage)}
                    value={link.values.link_radio_output_device || null}
                    onChange={(value) => link.setField("link_radio_output_device", value || "")}
                    clearable
                    placeholder="System default"
                    nothingFoundMessage="No output devices"
                    comboboxProps={{ withinPortal: true, zIndex: 400 }}
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
                      "CM108 COS is on this linked radio's Digirig/URI — not the repeater's. Digirig Lite usually uses VOX unless you wired the COS pad.",
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
                      label={labeled("COS polarity", "On a serial port, active high means the line is on (asserted) during a carrier.")}
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
                          "A /dev/serial/by-id name stays the same when adapters are plugged in a different order. Labels show if another role already uses the port.",
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
                  <NumberInput
                    label="VOX threshold"
                    suffix=" dBFS"
                    min={-90}
                    max={0}
                    value={link.values.link_radio_vox_threshold_db}
                    onChange={(value) => link.setField("link_radio_vox_threshold_db", Number(value) || 0)}
                    required
                  />
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
                      "CM108 PTT is GPIO3 on this linked radio's own Digirig/URI (the USB interface for the link sound devices above) — not the repeater radio's CM108. Auto-picked as the other CM108, or set MOREOPENREPEATER_LINK_CM108_HIDRAW.",
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
                          "Labels show if another role already uses the port. PTT and COS can share one adapter on different lines.",
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
                      label={labeled("PTT polarity", "On a serial port, active high turns the line on (asserts it) to transmit.")}
                      data={[
                        { value: "high", label: "Active high (pin goes high to transmit)" },
                        { value: "low", label: "Active low" },
                      ]}
                      value={link.values.link_radio_ptt_polarity}
                      onChange={(value) => link.setField("link_radio_ptt_polarity", value || "high")}
                      allowDeselect={false}
                    />
                  ) : null}
                  <NumberInput
                    label={labeled("Transmit delay", "Key up, then wait this long before any audio. 0 = off.")}
                    suffix=" ms"
                    min={0}
                    max={500}
                    step={10}
                    value={link.values.link_radio_tx_delay_ms}
                    onChange={(value) => link.setField("link_radio_tx_delay_ms", Number(value) || 0)}
                    required
                  />
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
          </Accordion>

          <ConfigActions dirty={link.dirty} saving={link.saving} onReset={link.reset} />
        </Stack>
      </Card>
  );
}
