import {
  Accordion,
  Alert,
  Card,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { Link } from "react-router-dom";
import { CtcssToneSelect } from "../components/audio/CtcssToneSelect";
import { CTCSS_TONES } from "../components/audio/constants";
import { ConfigActions } from "../components/ConfigActions";
import { useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled, InfoNote } from "../components/FieldHint";

type TimingValues = {
  transmitter_enabled: boolean;
  courtesy_tone_duration: number;
  hang_time: number;
  tot_duration: number;
  require_ctcss_hz: number | "";
  access_mode: string;
  tone_burst_ms: number;
  kerchunk_delay: number;
  node_mode: string;
  simplex_courtesy_tone: boolean;
  lockout_timeouts: number;
  lockout_window: number;
  lockout_clear_after: number;
};

const KEYS = Object.keys({
  transmitter_enabled: true,
  courtesy_tone_duration: 0,
  hang_time: 0,
  tot_duration: 0,
  require_ctcss_hz: "",
  access_mode: "",
  tone_burst_ms: 0,
  kerchunk_delay: 0,
  node_mode: "",
  simplex_courtesy_tone: true,
  lockout_timeouts: 0,
  lockout_window: 0,
  lockout_clear_after: 0,
}) as (keyof TimingValues)[];

const DEFAULTS: TimingValues = {
  transmitter_enabled: true,
  courtesy_tone_duration: 0.5,
  hang_time: 3,
  tot_duration: 180,
  require_ctcss_hz: "",
  access_mode: "carrier",
  tone_burst_ms: 500,
  kerchunk_delay: 0,
  node_mode: "repeater",
  simplex_courtesy_tone: false,
  lockout_timeouts: 0,
  lockout_window: 600,
  lockout_clear_after: 30,
};

export function Timing() {
  const { values, setField, dirty, reset, submit, saving } = useConfigSlice<TimingValues>(KEYS, DEFAULTS);
  const lockoutOn = values.lockout_timeouts > 0;
  const isSimplex = values.node_mode === "simplex";
  const needsToneBurst = values.access_mode === "tone_burst";
  const standardTone = values.require_ctcss_hz === "" || CTCSS_TONES.includes(Number(values.require_ctcss_hz));
  const lastSeconds = (values.courtesy_tone_duration + values.hang_time).toFixed(1);

  return (
    <Page title="Timing">
      <Card
        withBorder
        padding="lg"
        radius="md"
        component="form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Stack gap="md">
          <Switch
            label={labeled("Transmitter enabled", <>{"Off is dry-run: the receiver, COS, DTMF and logic keep running, but PTT never keys. Distinct from the Simulator (fake inputs)."}<br />{values.transmitter_enabled
                ? undefined
                : "Dry-run active — PTT stays down. Turn back on before putting the repeater on the air."}</>)}
            checked={values.transmitter_enabled}
            onChange={(event) => setField("transmitter_enabled", event.currentTarget.checked)}
          />

          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label={labeled("Hang time", "Seconds after the courtesy tone")}
              suffix=" s"
              min={0.1}
              step={0.1}
              decimalScale={1}
              value={values.hang_time}
              onChange={(value) => setField("hang_time", Number(value) || 0)}
              required
            />
            <NumberInput
              label={labeled("Time-out timer (TOT)", "Max continuous transmission")}
              suffix=" s"
              min={1}
              step={1}
              value={values.tot_duration}
              onChange={(value) => setField("tot_duration", Number(value) || 0)}
              required
            />
            <NumberInput
              label={labeled("Courtesy tone duration", "How long the courtesy tone plays")}
              suffix=" s"
              min={0.1}
              step={0.1}
              decimalScale={1}
              value={values.courtesy_tone_duration}
              onChange={(value) => setField("courtesy_tone_duration", Number(value) || 0)}
              required
            />
            <Select
              label={labeled("Access", "What opens the repeater")}
              data={[
                { value: "carrier", label: "Every transmission" },
                { value: "ctcss_open", label: "CTCSS opens, then carrier" },
                { value: "tone_burst", label: "1750 Hz burst, then carrier" },
              ]}
              value={values.access_mode}
              onChange={(value) => setField("access_mode", value || "carrier")}
            />
            {standardTone ? (
              <CtcssToneSelect
                label={labeled("Required CTCSS tone", "Optional. Leave empty to accept any signal.")}
                placeholder="Any signal"
                value={values.require_ctcss_hz === "" ? null : String(values.require_ctcss_hz)}
                onChange={(value) => setField("require_ctcss_hz", value ? Number(value) : "")}
              />
            ) : (
              <NumberInput
                label={labeled("Required CTCSS tone", "Custom tone. Clear it to accept any signal.")}
                suffix=" Hz"
                min={0}
                step={0.1}
                decimalScale={1}
                value={values.require_ctcss_hz === "" ? undefined : values.require_ctcss_hz}
                onChange={(value) =>
                  setField("require_ctcss_hz", value === "" || value === undefined ? "" : Number(value))
                }
              />
            )}
            {needsToneBurst ? (
              <NumberInput
                label={labeled("Tone burst length", "Shortest 1750 Hz burst that opens it · 500 ms typical")}
                suffix=" ms"
                min={100}
                max={2000}
                step={50}
                value={values.tone_burst_ms}
                onChange={(value) => setField("tone_burst_ms", Number(value) || 0)}
                required
              />
            ) : null}
          </SimpleGrid>

          <InfoNote label="What these timers add up to">
            When someone stops talking, the repeater plays the courtesy tone for{" "}
            <span className="mor-num">{values.courtesy_tone_duration} s</span>, holds for{" "}
            <span className="mor-num">{values.hang_time} s</span> in case of a reply, then drops the transmitter. That is{" "}
            <span className="mor-num">{lastSeconds} s</span> from the end of the last transmission. A transmission
            longer than <span className="mor-num">{values.tot_duration} s</span> times out.
          </InfoNote>

          <Title order={2}>Mode</Title>
          <Select
            label="This station is a"
            maw={320}
            data={[
              { value: "repeater", label: "Repeater" },
              { value: "simplex", label: "Simplex node" },
            ]}
            value={values.node_mode}
            onChange={(value) => setField("node_mode", value || "repeater")}
          />
          {isSimplex ? (
            <InfoNote>
              One radio on one frequency. Locals go to the links but aren&apos;t repeated; linked stations go out when
              nobody local is talking.
            </InfoNote>
          ) : (
            <Alert color="gray" variant="light" title="Half-duplex radios">
              A single Digirig + HT cannot hear while it transmits. For that hardware use Simplex node, or separate RX
              and TX radios. See{" "}
              <Text span component={Link} to="/audio" fw={600} style={{ color: "inherit" }}>
                Audio &amp; tones
              </Text>
              .
            </Alert>
          )}
          {isSimplex ? (
            <Switch
              label={labeled("Courtesy tone after a local user", "Tells them the node heard them")}
              checked={values.simplex_courtesy_tone}
              onChange={(event) => setField("simplex_courtesy_tone", event.currentTarget.checked)}
            />
          ) : null}

          <Accordion variant="separated" radius="md">
            <Accordion.Item value="advanced">
              <Accordion.Control>Advanced · kerchunk &amp; lockout</Accordion.Control>
              <Accordion.Panel>
                <Stack gap="md">
                  <NumberInput
                    label={labeled("Kerchunk filter", "Key-up must last this long before the repeater comes up · 0 = off")}
                    suffix=" s"
                    min={0}
                    max={3}
                    step={0.05}
                    decimalScale={2}
                    value={values.kerchunk_delay}
                    onChange={(value) => setField("kerchunk_delay", Number(value) || 0)}
                    required
                  />
                  <Title order={3} className="mor-card-title">
                    {labeled("Stuck-carrier lockout", <>After enough timeouts the repeater stops repeating until the channel has been quiet. IDs still go
                    out.</>)}</Title>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <NumberInput
                      label={labeled("Lock out after", "0 turns lockout off")}
                      suffix=" timeouts"
                      min={0}
                      max={20}
                      value={values.lockout_timeouts}
                      onChange={(value) => setField("lockout_timeouts", Number(value) || 0)}
                      required
                    />
                    {lockoutOn ? (
                      <>
                        <NumberInput
                          label="Within"
                          suffix=" s"
                          min={60}
                          max={86400}
                          value={values.lockout_window}
                          onChange={(value) => setField("lockout_window", Number(value) || 0)}
                          required
                        />
                        <NumberInput
                          label={labeled("Clear after quiet for", "Or clear from the dashboard / a DTMF macro")}
                          suffix=" s"
                          min={5}
                          max={3600}
                          value={values.lockout_clear_after}
                          onChange={(value) => setField("lockout_clear_after", Number(value) || 0)}
                          required
                        />
                      </>
                    ) : null}
                  </SimpleGrid>
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
        </Stack>
      </Card>
    </Page>
  );
}
