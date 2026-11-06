import {
  Accordion,
  Alert,
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
import { Link } from "react-router-dom";
import { ConfigActions } from "../ConfigActions";
import { labeled } from "../FieldHint";
import {
  serialPortOptions,
  serialSelectValue,
  soundDeviceOptions,
  type DeviceAssignment,
} from "../../deviceUsage";
import { BOARD_NAMES, CTCSS_TONES, METER_FLOOR_DB } from "./constants";
import { LevelMeter } from "./helpers";
import type { AudioDevice, ConfigSlice, EngineStatus, EngineValues, SerialPort } from "./types";

const HALF_DUPLEX_BOARDS = new Set(["digirig-lite", "aioc"]);

export function RadioInterfaceCard({
  engine,
  devices,
  devicesError,
  onReloadDevices,
  serialPorts,
  soundUsage,
  serialUsage,
  engineStatus,
  state,
  boardPreset,
  nodeMode,
  cosHardware,
  health,
  showSilenceHint,
  isViewer,
  openWizard,
}: {
  engine: ConfigSlice<EngineValues>;
  devices: AudioDevice[];
  devicesError?: string | null;
  onReloadDevices?: () => void;
  serialPorts: SerialPort[];
  soundUsage: DeviceAssignment[];
  serialUsage: DeviceAssignment[];
  engineStatus: EngineStatus | null;
  state: { label: string; color: string };
  boardPreset: string;
  nodeMode: string;
  cosHardware: boolean;
  health: string;
  showSilenceHint: boolean;
  isViewer: boolean;
  openWizard: () => void;
}) {
  const halfDuplexWarn =
    nodeMode !== "simplex" &&
    (HALF_DUPLEX_BOARDS.has(boardPreset) ||
      (engine.values.ptt_output === "cm108" && engine.values.cos_source === "vox"));

  return (
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void engine.submit();
        }}
      >
        <Stack gap="md">
          <Group justify="space-between">
            <Title order={2}>Radio interface</Title>
            <Badge color={state.color} variant="light">
              {state.label}
            </Badge>
          </Group>
          <Text c="dimmed" size="sm" maw="75ch">
            Run the repeater on this computer&apos;s sound devices: receive audio comes in on the input, and repeated audio, tones and IDs go out on the output. Without a carrier-detect wire, the repeater can open on audio level or on a CTCSS tone.
          </Text>
          {halfDuplexWarn ? (
            <Alert color="yellow" title="One radio can’t live-repeat">
              Digirig Lite / HT-style interfaces are half-duplex: when this radio keys PTT it stops receiving, so
              voice collapses after the first moment. Use{" "}
              <Text span fw={600} component={Link} to="/timing" style={{ color: "inherit" }}>
                Timing → Simplex node
              </Text>{" "}
              to send locals to the link without keying this HT, or wire separate RX and TX radios.
            </Alert>
          ) : null}
          <Group justify="space-between" align="center">
            <Text size="sm" c="dimmed">
              Interface board: <Text span fw={600}>{BOARD_NAMES[boardPreset] ?? (boardPreset || "set up by hand")}</Text>
            </Text>
            {!isViewer ? (
              <Button variant="default" size="compact-sm" onClick={openWizard}>
                Run setup wizard
              </Button>
            ) : null}
          </Group>
          <Switch
            label="Enable live audio"
            checked={engine.values.audio_enabled}
            onChange={(event) => engine.setField("audio_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label={labeled("Radio model", "Operator note only — not used by the controller.")}
              placeholder="Yaesu DR-1X / two mobiles + AIOC"
              maxLength={80}
              value={engine.values.radio_model}
              onChange={(event) => engine.setField("radio_model", event.currentTarget.value)}
            />
            <TextInput
              label={labeled("Frequency", "Operator note for the repeater pair or simplex frequency.")}
              placeholder="146.94 / 146.34"
              maxLength={80}
              value={engine.values.radio_frequency}
              onChange={(event) => engine.setField("radio_frequency", event.currentTarget.value)}
            />
          </SimpleGrid>
          <Textarea
            label={labeled("Notes", "Anything else operators should know about this radio interface.")}
            placeholder="RX on AIOC #1, TX on AIOC #2; cavity on the south mast; Digirig on USB2."
            maxLength={500}
            minRows={2}
            autosize
            value={engine.values.radio_notes}
            onChange={(event) => engine.setField("radio_notes", event.currentTarget.value)}
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
                    No capture devices yet. Plug in the USB interface, then{" "}
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
                    label="Input (receiver)"
                    data={soundDeviceOptions(devices, "inputs", engine.values.audio_input_device, soundUsage)}
                    value={engine.values.audio_input_device || null}
                    onChange={(value) => engine.setField("audio_input_device", value || "")}
                    clearable
                    placeholder="System default"
                    nothingFoundMessage="No input devices"
                    comboboxProps={{ withinPortal: true, zIndex: 400 }}
                  />
                  <Select
                    label="Output (transmitter)"
                    data={soundDeviceOptions(devices, "outputs", engine.values.audio_output_device, soundUsage)}
                    value={engine.values.audio_output_device || null}
                    onChange={(value) => engine.setField("audio_output_device", value || "")}
                    clearable
                    placeholder="System default"
                    nothingFoundMessage="No output devices"
                    comboboxProps={{ withinPortal: true, zIndex: 400 }}
                  />
                </SimpleGrid>
                <Stack gap="sm" mt="md">
                  <Switch
                    label={labeled("De-emphasize receive audio", "For flat audio taken from the discriminator, which sounds tinny without it (6 dB per octave, 300–3000 Hz)")}
                    checked={engine.values.rx_deemphasis}
                    onChange={(event) => engine.setField("rx_deemphasis", event.currentTarget.checked)}
                  />
                  <Switch
                    label={labeled("Pre-emphasize transmit audio", "For a flat transmitter input that skips the radio\'s own pre-emphasis; turn transmit gain down if the highs clip")}
                    checked={engine.values.tx_preemphasis}
                    onChange={(event) => engine.setField("tx_preemphasis", event.currentTarget.checked)}
                  />
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>

            <Accordion.Item value="cos">
              <Accordion.Control>Carrier detect</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <Select
                    label={labeled(
                      "Detect a carrier by",
                      "CM108 COS is the VOL_DN / COS line on this radio's USB interface (the same Digirig, URI, or CM108 as its sound card) — not the link radio's.",
                    )}
                    data={[
                      { value: "vox", label: "Audio level (VOX)" },
                      { value: "ctcss", label: "CTCSS tone present" },
                      { value: "cm108", label: "This radio's CM108 COS" },
                      { value: "gpio", label: "Raspberry Pi GPIO pin" },
                      { value: "serial", label: "Serial port (CTS, DSR or DCD)" },
                    ]}
                    value={engine.values.cos_source}
                    onChange={(value) => engine.setField("cos_source", value || "vox")}
                    allowDeselect={false}
                  />
                  {cosHardware ? (
                    <Select
                      label={labeled("COS polarity", "On a serial port, active high means the line is on (asserted) during a carrier.")}
                      data={[
                        { value: "low", label: "Active low (pulled to ground on carrier)" },
                        { value: "high", label: "Active high" },
                      ]}
                      value={engine.values.cos_polarity}
                      onChange={(value) => engine.setField("cos_polarity", value || "low")}
                      allowDeselect={false}
                    />
                  ) : null}
                  {engine.values.cos_source === "gpio" ? (
                    <NumberInput
                      label={labeled("COS pin", "BCM number; GPIO27 is header pin 13. 3.3 V only.")}
                      suffix=" GPIO"
                      min={2}
                      max={27}
                      value={engine.values.cos_gpio_pin}
                      onChange={(value) => engine.setField("cos_gpio_pin", Number(value) || 0)}
                      required
                    />
                  ) : null}
                  {engine.values.cos_source === "serial" ? (
                    <>
                      <Select
                        label={labeled(
                          "COS serial port",
                          "A /dev/serial/by-id name stays the same when adapters are plugged in a different order. Labels show if another role already uses the port.",
                        )}
                        data={serialPortOptions(serialPorts, engine.values.cos_serial_device, serialUsage)}
                        value={serialSelectValue(serialPorts, engine.values.cos_serial_device)}
                        onChange={(value) => engine.setField("cos_serial_device", value || "")}
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
                        value={engine.values.cos_serial_line}
                        onChange={(value) => engine.setField("cos_serial_line", value || "cts")}
                        allowDeselect={false}
                      />
                    </>
                  ) : null}
                  <NumberInput
                    label="VOX threshold"
                    suffix=" dBFS"
                    min={-90}
                    max={0}
                    value={engine.values.vox_threshold_db}
                    onChange={(value) => engine.setField("vox_threshold_db", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label={labeled("VOX hold", "How long audio can drop out before the carrier counts as gone.")}
                    suffix=" s"
                    min={0}
                    max={5}
                    step={0.05}
                    decimalScale={2}
                    value={engine.values.vox_hold}
                    onChange={(value) => engine.setField("vox_hold", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label={labeled("VOX hold after a DTMF digit", "The hold when the quiet comes right after a keypad tone. The longer of the two holds applies.")}
                    suffix=" s"
                    min={0}
                    max={10}
                    step={0.5}
                    decimalScale={1}
                    value={engine.values.vox_dtmf_hold}
                    onChange={(value) => engine.setField("vox_dtmf_hold", Number(value) || 0)}
                    required
                  />
                  <NumberInput
                    label={labeled("Squelch-tail removal", "Cut this much from the end of each transmission. 0 = off.")}
                    suffix=" ms"
                    min={0}
                    max={300}
                    step={10}
                    value={engine.values.squelch_tail_ms}
                    onChange={(value) => engine.setField("squelch_tail_ms", Number(value) || 0)}
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
                      "CM108 PTT is GPIO3 on this radio's USB interface (Digirig Lite, DMK URI, USB-RIM, …) — the same dongle as its sound card. Not the link radio's Digirig.",
                    )}
                    data={[
                      { value: "cm108", label: "This radio's CM108 (Digirig, URI, …)" },
                      { value: "gpio", label: "Raspberry Pi GPIO pin" },
                      { value: "serial", label: "Serial port (RTS or DTR)" },
                    ]}
                    value={engine.values.ptt_output}
                    onChange={(value) => engine.setField("ptt_output", value || "cm108")}
                    allowDeselect={false}
                  />
                  {engine.values.ptt_output === "gpio" ? (
                    <NumberInput
                      label={labeled("PTT pin", "BCM number; GPIO17 is header pin 11. Drive the radio through a transistor.")}
                      suffix=" GPIO"
                      min={2}
                      max={27}
                      value={engine.values.ptt_gpio_pin}
                      onChange={(value) => engine.setField("ptt_gpio_pin", Number(value) || 0)}
                      required
                    />
                  ) : null}
                  {engine.values.ptt_output === "serial" ? (
                    <>
                      <Select
                        label={labeled(
                          "PTT serial port",
                          "Labels show if another role already uses the port. PTT and COS can share one adapter on different lines.",
                        )}
                        data={serialPortOptions(serialPorts, engine.values.ptt_serial_device, serialUsage)}
                        value={serialSelectValue(serialPorts, engine.values.ptt_serial_device)}
                        onChange={(value) => engine.setField("ptt_serial_device", value || "")}
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
                        value={engine.values.ptt_serial_line}
                        onChange={(value) => engine.setField("ptt_serial_line", value || "rts")}
                        allowDeselect={false}
                      />
                    </>
                  ) : null}
                  {engine.values.ptt_output === "gpio" || engine.values.ptt_output === "serial" ? (
                    <Select
                      label={labeled("PTT polarity", "On a serial port, active high turns the line on (asserts it) to transmit.")}
                      data={[
                        { value: "high", label: "Active high (pin goes high to transmit)" },
                        { value: "low", label: "Active low" },
                      ]}
                      value={engine.values.ptt_polarity}
                      onChange={(value) => engine.setField("ptt_polarity", value || "high")}
                      allowDeselect={false}
                    />
                  ) : null}
                  <NumberInput
                    label={labeled("Transmit delay", "Key up, then wait this long before any audio. 0 = off.")}
                    suffix=" ms"
                    min={0}
                    max={500}
                    step={10}
                    value={engine.values.tx_delay_ms}
                    onChange={(value) => engine.setField("tx_delay_ms", Number(value) || 0)}
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
                    label={labeled("Transmit gain", "Applied to repeated receive audio.")}
                    suffix=" dB"
                    min={-40}
                    max={20}
                    step={0.5}
                    decimalScale={1}
                    value={engine.values.tx_gain_db}
                    onChange={(value) => engine.setField("tx_gain_db", Number(value) || 0)}
                    required
                  />
                  <Select
                    label={labeled("Transmit CTCSS tone", "Added under everything the repeater sends. Leave blank for none.")}
                    data={CTCSS_TONES.map((hz) => ({ value: String(hz), label: `${hz.toFixed(1)} Hz` }))}
                    value={engine.values.tx_ctcss_hz === "" ? null : String(engine.values.tx_ctcss_hz)}
                    onChange={(value) =>
                      engine.setField("tx_ctcss_hz", value == null || value === "" ? "" : Number(value))
                    }
                    searchable
                    clearable
                    placeholder="off"
                  />
                  <NumberInput
                    label={labeled("CTCSS level", "Adjust for about 500–750 Hz of deviation on the transmitter.")}
                    suffix=" dBFS"
                    min={-40}
                    max={-6}
                    value={engine.values.tx_ctcss_level_db}
                    onChange={(value) => engine.setField("tx_ctcss_level_db", Number(value) || 0)}
                    required
                  />
                </SimpleGrid>
                <Switch
                  mt="md"
                  label={labeled("Mute DTMF tones", "Commands are still decoded, but the tones aren\'t transmitted, sent to links or recorded")}
                  checked={engine.values.dtmf_mute}
                  onChange={(event) => engine.setField("dtmf_mute", event.currentTarget.checked)}
                />
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <LevelMeter
            db={engineStatus?.rx_level_db ?? METER_FLOOR_DB}
            running={Boolean(engineStatus?.running)}
            open={Boolean(engineStatus?.running && engineStatus.cos_open)}
            thresholdDb={engine.values.vox_threshold_db}
            showThreshold={engine.values.cos_source === "vox"}
          />
          <Group gap="sm" wrap="wrap">
            <Text size="sm">
              RX <Text span fw={700}>{engineStatus?.running ? `${engineStatus.rx_level_db.toFixed(0)} dBFS` : "–"}</Text>
            </Text>
            <Badge color={engineStatus?.running && engineStatus.cos_open ? "green" : "gray"} variant="light">
              carrier
            </Badge>
            <Badge color={engineStatus?.running && engineStatus.ctcss_hz != null ? "green" : "gray"} variant="light">
              {engineStatus?.ctcss_hz != null ? `${engineStatus.ctcss_hz} Hz` : "no tone"}
            </Badge>
            <Badge color={engineStatus?.running && engineStatus.transmitting ? "red" : "gray"} variant="light">
              TX
            </Badge>
            {health ? (
              <Text size="sm" c="dimmed">
                {health}
              </Text>
            ) : null}
          </Group>
          {showSilenceHint ? (
            <Text size="sm" c="dimmed">
              The input has been pure digital silence. If you expected a signal: on macOS, the app running the server needs Microphone permission in System Settings › Privacy &amp; Security › Microphone, then a restart.
            </Text>
          ) : null}
          {engineStatus?.error ? (
            <Text c="red" size="sm">
              {engineStatus.error}
            </Text>
          ) : null}
          <ConfigActions dirty={engine.dirty} saving={engine.saving} onReset={engine.reset} />
        </Stack>
      </Card>
  );
}
