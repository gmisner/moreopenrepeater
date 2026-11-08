import {
  Accordion,
  Alert,
  Badge,
  Button,
  FileButton,
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
import { Link } from "react-router-dom";
import { ConfigActions } from "../ConfigActions";
import { ConfigEditDrawer } from "../ConfigEditDrawer";
import { ConfigSummaryCard } from "../ConfigSummaryCard";
import { labeled } from "../FieldHint";
import {
  serialPortOptions,
  serialSelectValue,
  soundDeviceOptions,
  type DeviceAssignment,
} from "../../deviceUsage";
import { BOARD_NAMES, COS_METHOD_LABELS, METER_FLOOR_DB, PTT_METHOD_LABELS } from "./constants";
import { CtcssToneSelect } from "./CtcssToneSelect";
import { LevelMeter, shortLabel } from "./helpers";
import type { AudioDevice, AudioScore, ConfigSlice, EngineStatus, EngineValues, SerialPort } from "./types";

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
  onTestId,
  testIdBusy,
  score,
  scoreBusy,
  onRecordScore,
  onScoreWav,
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
  onTestId?: () => void;
  testIdBusy?: boolean;
  score?: AudioScore | null;
  scoreBusy?: boolean;
  onRecordScore?: () => void;
  onScoreWav?: (file: File | null) => void;
}) {
  const [editing, setEditing] = useState(false);
  const halfDuplexWarn =
    nodeMode !== "simplex" &&
    (HALF_DUPLEX_BOARDS.has(boardPreset) ||
      (engine.values.ptt_output === "cm108" && engine.values.cos_source === "vox"));
  const boardName = BOARD_NAMES[boardPreset] ?? (boardPreset || "set up by hand");
  const hasNotes = Boolean(
    engine.values.radio_model.trim() || engine.values.radio_frequency.trim() || engine.values.radio_notes.trim(),
  );

  return (
    <>
      <ConfigSummaryCard
        title="Radio interface"
        badge={state.label}
        badgeColor={state.color}
        dirty={engine.dirty}
        blurb="Receive audio in, repeated audio and IDs out. Edit hardware wiring here."
        onEdit={isViewer ? undefined : () => setEditing(true)}
        secondaryAction={
          !isViewer ? (
            <Group gap="xs">
              <Button variant="default" onClick={openWizard}>
                Setup wizard
              </Button>
              {onTestId ? (
                <Button variant="light" loading={testIdBusy} onClick={onTestId}>
                  Test ID
                </Button>
              ) : null}
            </Group>
          ) : undefined
        }
        rows={[
          { label: "Board", value: boardName },
          { label: "Live audio", value: engine.values.audio_enabled ? "On" : "Off" },
          { label: "Input", value: shortLabel(engine.values.audio_input_device) },
          { label: "Output", value: shortLabel(engine.values.audio_output_device) },
          {
            label: "COS / PTT",
            value: `${COS_METHOD_LABELS[engine.values.cos_source] ?? engine.values.cos_source} · ${PTT_METHOD_LABELS[engine.values.ptt_output] ?? engine.values.ptt_output}`,
          },
          ...(hasNotes
            ? [
                {
                  label: "Notes",
                  value: [engine.values.radio_model, engine.values.radio_frequency].filter(Boolean).join(" · ") || "Set",
                },
              ]
            : []),
        ]}
      >
        {halfDuplexWarn ? (
          <Alert color="yellow" title="One radio can’t live-repeat">
            Digirig Lite / HT-style interfaces are half-duplex. Use{" "}
            <Text span fw={600} component={Link} to="/timing" style={{ color: "inherit" }}>
              Timing → Simplex node
            </Text>{" "}
            or separate RX and TX radios.
          </Alert>
        ) : null}
        <LevelMeter
          db={engineStatus?.rx_level_db ?? METER_FLOOR_DB}
          running={Boolean(engineStatus?.running)}
          open={Boolean(engineStatus?.running && engineStatus.cos_open)}
          thresholdDb={engine.values.vox_threshold_db}
          showThreshold={engine.values.cos_source === "vox"}
        />
        <Group gap="sm" wrap="wrap">
          <Text size="sm">
            RX{" "}
            <Text span fw={700}>
              {engineStatus?.running ? `${engineStatus.rx_level_db.toFixed(0)} dBFS` : "–"}
            </Text>
          </Text>
          <Badge color={engineStatus?.running && engineStatus.cos_open ? "green" : "gray"} variant="light">
            carrier
          </Badge>
          <Badge color={engineStatus?.running && engineStatus.ctcss_hz != null ? "green" : "gray"} variant="light">
            {engineStatus?.ctcss_hz != null ? `${engineStatus.ctcss_hz} Hz` : "no tone"}
          </Badge>
          <Badge
            color={engineStatus?.ptt_error || (engineStatus?.running && engineStatus.transmitting) ? "red" : "gray"}
            variant="light"
          >
            {engineStatus?.ptt_error ? "PTT unknown" : "TX"}
          </Badge>
          {health ? (
            <Text size="sm" c="dimmed">
              {health}
            </Text>
          ) : null}
        </Group>
        {!isViewer && (onRecordScore || onScoreWav) ? (
          <Stack gap={6}>
            <Group gap="xs" wrap="wrap" justify="space-between" align="flex-start">
              <Group gap="xs">
                {onRecordScore ? (
                  <Button size="compact-sm" loading={scoreBusy} onClick={onRecordScore}>
                    Record 5 s
                  </Button>
                ) : null}
                {onScoreWav ? (
                  <FileButton onChange={onScoreWav} accept="audio/wav,.wav">
                    {(props) => (
                      <Button {...props} size="compact-sm" variant="default" loading={scoreBusy}>
                        Score WAV
                      </Button>
                    )}
                  </FileButton>
                ) : null}
              </Group>
              {score ? (
                <Group gap="md" wrap="wrap">
                  <Text size="sm">
                    Peak{" "}
                    <Text span fw={700}>
                      {score.peak_dbfs.toFixed(1)} dBFS
                    </Text>
                  </Text>
                  <Text size="sm">
                    RMS{" "}
                    <Text span fw={700}>
                      {score.rms_dbfs.toFixed(1)} dBFS
                    </Text>
                  </Text>
                  <Text size="sm">
                    Median{" "}
                    <Text span fw={700}>
                      {score.median_block_dbfs.toFixed(1)} dBFS
                    </Text>
                  </Text>
                  <Badge color={score.clipping ? "red" : "gray"} variant="light">
                    {score.clipping ? "clipping" : "no clip"}
                  </Badge>
                  <Badge variant="light">
                    {score.ctcss_hz != null ? `${score.ctcss_hz} Hz CTCSS` : "no CTCSS"}
                  </Badge>
                  {score.strongest_hz != null ? (
                    <Badge variant="outline">
                      ~{score.strongest_hz.toFixed(0)} Hz ({(score.tone_share * 100).toFixed(0)}%)
                    </Badge>
                  ) : null}
                </Group>
              ) : (
                <Text size="sm" c="dimmed" maw="52ch">
                  Capture RX while keyed (or hiss) to A/B Mic gain — peak / RMS / CTCSS without waiting for a new opening.
                </Text>
              )}
            </Group>
            {score?.notes.length ? (
              <Stack gap={2}>
                {score.notes.map((note) => (
                  <Text key={note} size="sm" c="dimmed">
                    {note}
                  </Text>
                ))}
              </Stack>
            ) : null}
            {score ? (
              <Text size="xs" c="dimmed">
                {score.duration_seconds.toFixed(1)} s @ {score.sample_rate} Hz — idle a few dB below VOX; keyed peaks
                well under 0 dBFS.
              </Text>
            ) : null}
          </Stack>
        ) : null}
        {showSilenceHint ? (
          <Text size="sm" c="dimmed">
            The input has been pure digital silence. If you expected a signal: on macOS, the app running the server needs
            Microphone permission in System Settings › Privacy &amp; Security › Microphone, then a restart.
          </Text>
        ) : null}
        {engineStatus?.error ? (
          <Text c="red" size="sm">
            {engineStatus.error}
          </Text>
        ) : null}
        {engineStatus?.ptt_error && engineStatus.ptt_error !== engineStatus.error ? (
          <Text c="red" size="sm">
            {engineStatus.ptt_error}
          </Text>
        ) : null}
        {engineStatus?.retry_in_seconds != null ? (
          <Text c="dimmed" size="sm">
            Retrying live audio in {Math.ceil(engineStatus.retry_in_seconds)} s.
          </Text>
        ) : null}
      </ConfigSummaryCard>

      <ConfigEditDrawer
        opened={editing}
        onClose={() => setEditing(false)}
        title="Radio interface"
        subtitle="Sound devices, carrier detect, and PTT for the main repeater radio."
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void engine.submit().then((ok) => {
              if (ok) setEditing(false);
            });
          }}
        >
          <Stack gap="md">
            <Switch
              label="Enable live audio"
              checked={engine.values.audio_enabled}
              onChange={(event) => engine.setField("audio_enabled", event.currentTarget.checked)}
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
                    />
                    <Select
                      label="Output (transmitter)"
                      data={soundDeviceOptions(devices, "outputs", engine.values.audio_output_device, soundUsage)}
                      value={engine.values.audio_output_device || null}
                      onChange={(value) => engine.setField("audio_output_device", value || "")}
                      clearable
                      placeholder="System default"
                      nothingFoundMessage="No output devices"
                    />
                  </SimpleGrid>
                  <Stack gap="sm" mt="md">
                    <Switch
                      label={labeled(
                        "De-emphasize receive audio",
                        "For flat audio taken from the discriminator, which sounds tinny without it (6 dB per octave, 300–3000 Hz)",
                      )}
                      checked={engine.values.rx_deemphasis}
                      onChange={(event) => engine.setField("rx_deemphasis", event.currentTarget.checked)}
                    />
                    <Switch
                      label={labeled(
                        "Pre-emphasize transmit audio",
                        "For a flat transmitter input that skips the radio's own pre-emphasis; turn transmit gain down if the highs clip",
                      )}
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
                        "CM108 COS is the VOL_DN / COS line on this radio's USB interface (the same Digirig, URI, or CM108 as its sound card) — not the linked radio's.",
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
                        label={labeled(
                          "COS polarity",
                          "On a serial port, active high means the line is on (asserted) during a carrier.",
                        )}
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
                    {engine.values.cos_source === "vox" ? (
                      <>
                        <NumberInput
                          label="VOX threshold"
                          description="Typical around −40 to −25 dBFS"
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
                          label={labeled(
                            "VOX hold after a DTMF digit",
                            "The hold when the quiet comes right after a keypad tone. The longer of the two holds applies.",
                          )}
                          suffix=" s"
                          min={0}
                          max={10}
                          step={0.5}
                          decimalScale={1}
                          value={engine.values.vox_dtmf_hold}
                          onChange={(value) => engine.setField("vox_dtmf_hold", Number(value) || 0)}
                          required
                        />
                      </>
                    ) : null}
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
                        "CM108 PTT is GPIO3 on this radio's USB interface (Digirig Lite, DMK URI, USB-RIM, …) — the same dongle as its sound card. Not the linked radio's Digirig.",
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
                        label={labeled(
                          "PTT polarity",
                          "On a serial port, active high turns the line on (asserts it) to transmit.",
                        )}
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
                      description="300 ms typical"
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
                    <CtcssToneSelect
                      label={labeled("Transmit CTCSS tone", "Added under everything the repeater sends. Leave blank for none.")}
                      value={engine.values.tx_ctcss_hz === "" ? null : String(engine.values.tx_ctcss_hz)}
                      onChange={(value) =>
                        engine.setField("tx_ctcss_hz", value == null || value === "" ? "" : Number(value))
                      }
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
                    label={labeled(
                      "Mute DTMF tones",
                      "Commands are still decoded, but the tones aren't transmitted, sent to links or recorded",
                    )}
                    checked={engine.values.dtmf_mute}
                    onChange={(event) => engine.setField("dtmf_mute", event.currentTarget.checked)}
                  />
                </Accordion.Panel>
              </Accordion.Item>

              <Accordion.Item value="notes">
                <Accordion.Control>Station notes · optional</Accordion.Control>
                <Accordion.Panel>
                  <Text c="dimmed" size="sm" mb="md" maw="75ch">
                    For operators only — the controller does not use these fields.
                  </Text>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <TextInput
                      label="Radio model"
                      description="Optional"
                      placeholder="e.g. model or setup"
                      maxLength={80}
                      value={engine.values.radio_model}
                      onChange={(event) => engine.setField("radio_model", event.currentTarget.value)}
                    />
                    <TextInput
                      label="Frequency"
                      description="Optional pair or simplex"
                      placeholder="e.g. 146.94 / 146.34"
                      maxLength={80}
                      value={engine.values.radio_frequency}
                      onChange={(event) => engine.setField("radio_frequency", event.currentTarget.value)}
                    />
                  </SimpleGrid>
                  <Textarea
                    mt="md"
                    label="Notes"
                    description="Optional free-form note"
                    placeholder="Optional note for operators"
                    maxLength={500}
                    minRows={2}
                    autosize
                    value={engine.values.radio_notes}
                    onChange={(event) => engine.setField("radio_notes", event.currentTarget.value)}
                  />
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>

            <Group justify="space-between" align="center">
              <Text size="sm" c="dimmed">
                Board: <Text span fw={600}>{boardName}</Text>
              </Text>
              {!isViewer ? (
                <Button variant="default" size="compact-sm" onClick={openWizard}>
                  Run setup wizard
                </Button>
              ) : null}
            </Group>

            <ConfigActions dirty={engine.dirty} saving={engine.saving} onReset={engine.reset} />
          </Stack>
        </form>
      </ConfigEditDrawer>
    </>
  );
}
