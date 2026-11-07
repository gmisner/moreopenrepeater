import {
  Accordion,
  Card,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { ConfigActions } from "../components/ConfigActions";
import { useConfigSlice } from "../config";
import { Page } from "../layout/AppShell";
import { labeled } from "../components/FieldHint";

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
  tts_speed: number;
  id_phonetic: boolean;
  long_id_mode: string;
  long_id_interval: number;
  long_id_text: string;
  long_id_asset_id: string;
};

type TTSVoice = {
  id: string;
  label: string;
  locale: string;
  locale_label: string;
  gender: string;
};

type TTSInfo = {
  engine: string | null;
  voices: TTSVoice[];
  min_speed: number;
  max_speed: number;
  default_speed: number;
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
  "tts_speed",
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
  tts_speed: 1,
  id_phonetic: false,
  long_id_mode: "off",
  long_id_interval: 3600,
  long_id_text: "",
  long_id_asset_id: "",
};

const SPEED_OPTIONS = [
  { value: "0.8", label: "Slower (0.8×)" },
  { value: "0.9", label: "Slightly slow (0.9×)" },
  { value: "1", label: "Normal (1×)" },
  { value: "1.1", label: "Slightly fast (1.1×)" },
  { value: "1.25", label: "Faster (1.25×)" },
  { value: "1.5", label: "Fast (1.5×)" },
];

export function Identification() {
  const { values, setField, dirty, reset, submit, saving } = useConfigSlice<IdValues>(KEYS, DEFAULTS);
  const [tts, setTts] = useState<TTSInfo | null>(null);
  const isCw = values.id_mode === "cw";
  const isVoice = values.id_mode === "voice";
  const longOn = values.long_id_mode !== "off";
  const kokoro = tts?.engine === "kokoro";

  useEffect(() => {
    let cancelled = false;
    void api<TTSInfo>("/api/audio/tts")
      .then((info) => {
        if (!cancelled) setTts(info);
      })
      .catch(() => {
        if (!cancelled) setTts({ engine: null, voices: [], min_speed: 0.5, max_speed: 2, default_speed: 1 });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const localeOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const voice of tts?.voices || []) {
      if (voice.locale && !seen.has(voice.locale)) seen.set(voice.locale, voice.locale_label);
    }
    return [...seen.entries()].map(([value, label]) => ({ value, label }));
  }, [tts]);

  const selectedLocale = useMemo(() => {
    if (!kokoro || !tts?.voices.length) return "";
    if (values.tts_voice) {
      const match = tts.voices.find((voice) => voice.id === values.tts_voice);
      if (match) return match.locale;
    }
    return localeOptions[0]?.value || "";
  }, [kokoro, tts, values.tts_voice, localeOptions]);

  const voiceOptions = useMemo(() => {
    const voices = (tts?.voices || []).filter((voice) => !selectedLocale || voice.locale === selectedLocale);
    return voices.map((voice) => ({ value: voice.id, label: voice.label }));
  }, [tts, selectedLocale]);

  const speedValue = String(values.tts_speed || 1);
  const speedData = SPEED_OPTIONS.some((option) => option.value === speedValue)
    ? SPEED_OPTIONS
    : [...SPEED_OPTIONS, { value: speedValue, label: `Custom (${speedValue}×)` }];

  return (
    <Page title="Identification" description="Callsign and how often the station identifies.">
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
            label={labeled("GMRS mode", "Uses GMRS channel rules and wording where they differ from amateur")}
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
              description="While the repeater is in use"
              suffix=" s"
              min={1}
              value={values.id_interval}
              onChange={(value) => setField("id_interval", Number(value) || 0)}
              required
            />
            <Switch
              label={labeled("ID when idle", "Also ID on the interval when nobody is using the repeater")}
              checked={values.idle_id}
              onChange={(event) => setField("idle_id", event.currentTarget.checked)}
              style={{ alignSelf: "end" }}
            />
          </SimpleGrid>

          {isCw ? (
            <>
              <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
                CW
              </Title>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <NumberInput
                  label="Speed"
                  description="20 WPM typical"
                  suffix=" WPM"
                  min={1}
                  max={60}
                  value={values.cw_wpm}
                  onChange={(value) => setField("cw_wpm", Number(value) || 0)}
                  required
                />
                <NumberInput
                  label="Tone"
                  description="600 Hz typical"
                  suffix=" Hz"
                  min={100}
                  max={3000}
                  value={values.cw_tone_hz}
                  onChange={(value) => setField("cw_tone_hz", Number(value) || 0)}
                  required
                />
                <TextInput
                  label="Suffix"
                  description="Optional · e.g. /R"
                  placeholder=""
                  value={values.cw_id_suffix}
                  onChange={(event) => setField("cw_id_suffix", event.currentTarget.value)}
                />
              </SimpleGrid>
            </>
          ) : null}

          {isVoice ? (
            <>
              <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
                Voice
              </Title>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                <TextInput
                  label="Spoken ID text"
                  description="Use {callsign} and {time}"
                  placeholder="e.g. {callsign} repeater"
                  value={values.voice_id_text}
                  onChange={(event) => setField("voice_id_text", event.currentTarget.value)}
                />
                {kokoro ? (
                  <>
                    <Select
                      label="Accent / locale"
                      description="Filters the Kokoro voices below"
                      data={localeOptions}
                      value={selectedLocale || null}
                      onChange={(value) => {
                        const nextLocale = value || "";
                        const current = (tts?.voices || []).find((voice) => voice.id === values.tts_voice);
                        if (current && current.locale === nextLocale) return;
                        const first = (tts?.voices || []).find((voice) => voice.locale === nextLocale);
                        setField("tts_voice", first?.id || "");
                      }}
                      searchable
                    />
                    <Select
                      label="Voice"
                      description="Kokoro speaker for IDs and announcements · clear for default"
                      data={voiceOptions}
                      value={values.tts_voice || null}
                      onChange={(value) => setField("tts_voice", value || "")}
                      searchable
                      clearable
                      placeholder="Default (af_heart)"
                    />
                    <Select
                      label="Speech speed"
                      description="How quickly Kokoro reads the text"
                      data={speedData}
                      value={speedValue}
                      onChange={(value) => setField("tts_speed", Number(value) || 1)}
                    />
                  </>
                ) : (
                  <TextInput
                    label="TTS voice"
                    description={
                      tts?.engine
                        ? `Optional · ${tts.engine} is active. Install kokoro-cli for locale and speed controls.`
                        : "Optional · install kokoro-cli for selectable voices"
                    }
                    placeholder=""
                    value={values.tts_voice}
                    onChange={(event) => setField("tts_voice", event.currentTarget.value)}
                  />
                )}
              </SimpleGrid>
              <Switch
                label="Spell callsign phonetically"
                checked={values.id_phonetic}
                onChange={(event) => setField("id_phonetic", event.currentTarget.checked)}
              />
            </>
          ) : null}

          <Title order={2} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.06em" }}>
            Long ID
          </Title>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <Select
              label="Long ID"
              description="Longer announcement on a slower interval"
              data={[
                { value: "off", label: "Off" },
                { value: "voice", label: "Voice" },
                { value: "clip", label: "Uploaded clip" },
              ]}
              value={values.long_id_mode}
              onChange={(value) => setField("long_id_mode", value || "off")}
            />
            {longOn ? (
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
            ) : null}
            {values.long_id_mode === "voice" ? (
              <TextInput
                label="Long ID text"
                description="Use {callsign} and {time}"
                placeholder="e.g. This is {callsign}. The time is {time}."
                value={values.long_id_text}
                onChange={(event) => setField("long_id_text", event.currentTarget.value)}
              />
            ) : null}
            {values.long_id_mode === "clip" ? (
              <TextInput
                label="Clip asset ID"
                description="Upload clips under Audio & tones"
                placeholder=""
                value={values.long_id_asset_id}
                onChange={(event) => setField("long_id_asset_id", event.currentTarget.value)}
              />
            ) : null}
          </SimpleGrid>

          <Accordion variant="separated" radius="md">
            <Accordion.Item value="advanced">
              <Accordion.Control>Advanced · timing details</Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <NumberInput
                    label="ID audio duration"
                    description="Reserved airtime for the ID clip"
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
                    description="0 = always ID after the interval"
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
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>

          <ConfigActions dirty={dirty} saving={saving} onReset={reset} />
        </Stack>
      </Card>
    </Page>
  );
}
