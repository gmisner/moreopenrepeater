import { Card, NumberInput, Select, SimpleGrid, Stack, Switch, Text, Title } from "@mantine/core";
import { ConfigActions } from "../components/ConfigActions";
import { useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";

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

  return (
    <Page title="Timing" description="Core repeater timers and access control.">
      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <Stack gap="md">
          <Switch
            label="Transmitter enabled"
            description="Off stops all transmissions; DTMF still works"
            checked={values.transmitter_enabled}
            onChange={(event) => setField("transmitter_enabled", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="Courtesy tone duration"
              description="How long the courtesy tone plays after a user unkeys."
              suffix=" s"
              min={0.1}
              step={0.1}
              decimalScale={1}
              value={values.courtesy_tone_duration}
              onChange={(value) => setField("courtesy_tone_duration", Number(value) || 0)}
              required
            />
            <NumberInput
              label="Hang time"
              description="Transmitter stays keyed this long after the courtesy tone. Not used on a simplex node."
              suffix=" s"
              min={0.1}
              step={0.1}
              decimalScale={1}
              value={values.hang_time}
              onChange={(value) => setField("hang_time", Number(value) || 0)}
              required
            />
            <NumberInput
              label="Timeout timer (TOT)"
              description="Maximum continuous transmission before the repeater times out."
              suffix=" s"
              min={1}
              step={1}
              value={values.tot_duration}
              onChange={(value) => setField("tot_duration", Number(value) || 0)}
              required
            />
            <NumberInput
              label="Required CTCSS tone"
              description="Leave blank for carrier access (no tone required)."
              suffix=" Hz"
              min={0}
              step={0.1}
              decimalScale={1}
              placeholder="disabled"
              value={values.require_ctcss_hz === "" ? undefined : values.require_ctcss_hz}
              onChange={(value) => setField("require_ctcss_hz", value === "" || value === undefined ? "" : Number(value))}
            />
            <Select
              label="Access"
              description="Every transmission: carrier, plus the required CTCSS tone if one is set."
              data={[
                { value: "carrier", label: "Every transmission" },
                { value: "ctcss_open", label: "CTCSS opens, then carrier" },
                { value: "tone_burst", label: "1750 Hz burst, then carrier" },
              ]}
              value={values.access_mode}
              onChange={(value) => setField("access_mode", value || "carrier")}
            />
            <NumberInput
              label="Tone burst length"
              description="The shortest 1750 Hz burst that opens the repeater."
              suffix=" ms"
              min={100}
              max={2000}
              step={50}
              value={values.tone_burst_ms}
              onChange={(value) => setField("tone_burst_ms", Number(value) || 0)}
              required
            />
            <NumberInput
              label="Kerchunk filter"
              description="A key-up must last this long before the repeater comes up. 0 turns the filter off."
              suffix=" s"
              min={0}
              max={3}
              step={0.05}
              decimalScale={2}
              value={values.kerchunk_delay}
              onChange={(value) => setField("kerchunk_delay", Number(value) || 0)}
              required
            />
          </SimpleGrid>

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Repeater or simplex node
          </Title>
          <Text c="dimmed" size="sm" maw="75ch">
            A simplex node is one radio on one frequency. Local users go to the links but aren&apos;t repeated, and linked stations go out when nobody local is talking.
          </Text>
          <Select
            label="Mode"
            maw={320}
            data={[
              { value: "repeater", label: "Repeater" },
              { value: "simplex", label: "Simplex node" },
            ]}
            value={values.node_mode}
            onChange={(value) => setField("node_mode", value || "repeater")}
          />
          <Switch
            label="Courtesy tone after a local user"
            description="Simplex node only; tells them the node heard them"
            checked={values.simplex_courtesy_tone}
            onChange={(event) => setField("simplex_courtesy_tone", event.currentTarget.checked)}
          />

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Stuck-carrier lockout
          </Title>
          <Text c="dimmed" size="sm" maw="75ch">
            After enough timeouts the repeater stops repeating until the channel has been quiet. IDs still go out.
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput
              label="Lock out after"
              description="0 turns the lockout off."
              suffix=" timeouts"
              min={0}
              max={20}
              value={values.lockout_timeouts}
              onChange={(value) => setField("lockout_timeouts", Number(value) || 0)}
              required
            />
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
              label="Clear after quiet for"
              description="Or clear it from the dashboard, or with a DTMF macro."
              suffix=" s"
              min={5}
              max={3600}
              value={values.lockout_clear_after}
              onChange={(value) => setField("lockout_clear_after", Number(value) || 0)}
              required
            />
          </SimpleGrid>

          <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
        </Stack>
      </Card>
    </Page>
  );
}
