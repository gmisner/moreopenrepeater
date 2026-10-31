import { Card, NumberInput, Select, SimpleGrid, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { ConfigActions } from "../components/ConfigActions";
import { useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";

type IdValues = {
  gmrs_mode: boolean;
  callsign: string;
  id_mode: string;
  id_interval: number;
  idle_id: boolean;
  id_audio_duration: number;
  id_skip_short_seconds: number;
  cw_wpm: number;
  cw_tone_hz: number;
  cw_id_suffix: string;
  voice_id_text: string;
  tts_voice: string;
  id_phonetic: boolean;
  long_id_mode: string;
  long_id_interval: number;
  long_id_text: string;
  long_id_asset_id: string;
};

const KEYS = [
  "gmrs_mode",
  "callsign",
  "id_mode",
  "id_interval",
  "idle_id",
  "id_audio_duration",
  "id_skip_short_seconds",
  "cw_wpm",
  "cw_tone_hz",
  "cw_id_suffix",
  "voice_id_text",
  "tts_voice",
  "id_phonetic",
  "long_id_mode",
  "long_id_interval",
  "long_id_text",
  "long_id_asset_id",
] as (keyof IdValues)[];

const DEFAULTS: IdValues = {
  gmrs_mode: false,
  callsign: "",
  id_mode: "voice",
  id_interval: 600,
  idle_id: false,
  id_audio_duration: 3,
  id_skip_short_seconds: 0,
  cw_wpm: 20,
  cw_tone_hz: 600,
  cw_id_suffix: "",
  voice_id_text: "",
  tts_voice: "",
  id_phonetic: false,
  long_id_mode: "off",
  long_id_interval: 3600,
  long_id_text: "",
  long_id_asset_id: "",
};

export function Identification() {
  const { values, setField, dirty, reset, submit, saving } = useConfigSlice<IdValues>(KEYS, DEFAULTS);

  return (
    <Page title="Identification" description="Station ID timing and CW / voice ID settings.">
      <Card withBorder padding="lg" radius="md" component="form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <Stack gap="md">
          <Switch
            label="GMRS mode"
            description="Uses GMRS channel rules and wording where they differ from amateur"
            checked={values.gmrs_mode}
            onChange={(event) => setField("gmrs_mode", event.currentTarget.checked)}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Callsign"
              placeholder="e.g. W1AW"
              value={values.callsign}
              onChange={(event) => setField("callsign", event.currentTarget.value.toUpperCase())}
            />
            <Select
              label="ID mode"
              data={[
                { value: "voice", label: "Voice" },
                { value: "cw", label: "CW (Morse)" },
              ]}
              value={values.id_mode}
              onChange={(value) => setField("id_mode", value || "voice")}
            />
            <NumberInput
              label="ID interval"
              description="How often the station ID goes out while the repeater is in use."
              suffix=" s"
              min={1}
              value={values.id_interval}
              onChange={(value) => setField("id_interval", Number(value) || 0)}
              required
            />
            <NumberInput
              label="ID audio duration"
              description="Reserved airtime for the ID clip."
              suffix=" s"
              min={0.1}
              step={0.1}
              decimalScale={1}
              value={values.id_audio_duration}
              onChange={(value) => setField("id_audio_duration", Number(value) || 0)}
              required
            />
            <NumberInput
              label="Skip ID after short QSOs"
              description="If the last key-up was shorter than this, wait for the next one before IDing."
              suffix=" s"
              min={0}
              max={10}
              step={0.5}
              decimalScale={1}
              value={values.id_skip_short_seconds}
              onChange={(value) => setField("id_skip_short_seconds", Number(value) || 0)}
              required
            />
          </SimpleGrid>
          <Switch
            label="ID when idle"
            description="Also ID on the interval when nobody is using the repeater"
            checked={values.idle_id}
            onChange={(event) => setField("idle_id", event.currentTarget.checked)}
          />

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            CW
          </Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <NumberInput label="Speed" suffix=" WPM" min={1} max={60} value={values.cw_wpm} onChange={(value) => setField("cw_wpm", Number(value) || 0)} required />
            <NumberInput label="Tone" suffix=" Hz" min={100} max={3000} value={values.cw_tone_hz} onChange={(value) => setField("cw_tone_hz", Number(value) || 0)} required />
            <TextInput label="Suffix" placeholder="none" description="e.g. /R" value={values.cw_id_suffix} onChange={(event) => setField("cw_id_suffix", event.currentTarget.value)} />
          </SimpleGrid>

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Voice
          </Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Spoken ID text"
              placeholder="{callsign} repeater"
              description="Use {callsign} and {time} placeholders."
              value={values.voice_id_text}
              onChange={(event) => setField("voice_id_text", event.currentTarget.value)}
            />
            <TextInput label="TTS voice" placeholder="engine default" value={values.tts_voice} onChange={(event) => setField("tts_voice", event.currentTarget.value)} />
          </SimpleGrid>
          <Switch
            label="Spell callsign phonetically"
            checked={values.id_phonetic}
            onChange={(event) => setField("id_phonetic", event.currentTarget.checked)}
          />

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Long ID
          </Title>
          <Text c="dimmed" size="sm" maw="75ch">
            A longer announcement on a slower interval (club info, time, weather teaser).
          </Text>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Long ID"
              data={[
                { value: "off", label: "Off" },
                { value: "voice", label: "Voice" },
                { value: "clip", label: "Uploaded clip" },
              ]}
              value={values.long_id_mode}
              onChange={(value) => setField("long_id_mode", value || "off")}
            />
            <NumberInput
              label="Long ID interval"
              suffix=" s"
              min={300}
              max={86400}
              step={60}
              value={values.long_id_interval}
              onChange={(value) => setField("long_id_interval", Number(value) || 0)}
              required
            />
            <TextInput
              label="Long ID text"
              placeholder="This is {callsign}. The time is {time}."
              value={values.long_id_text}
              onChange={(event) => setField("long_id_text", event.currentTarget.value)}
            />
          </SimpleGrid>

          <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
        </Stack>
      </Card>
    </Page>
  );
}
