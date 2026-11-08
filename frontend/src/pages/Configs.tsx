import { Anchor, Card, Code, Group, Stack, Table, Text, Title } from "@mantine/core";
import { Page } from "../layout/AppShell";

type ConfigEntry = {
  id: string;
  title: string;
  host: string;
  radio: string;
  interface: string;
  board: string;
  ptt: string;
  cos: string;
  levels: string;
  notes: string[];
};

/** Keep in sync with docs/known-working-configs.md */
const ENTRIES: ConfigEntry[] = [
  {
    id: "digirig-baofeng-pi4",
    title: "Digirig Lite + Baofeng HT + Raspberry Pi 4",
    host: "Raspberry Pi 4, Raspberry Pi OS Lite 64-bit",
    radio: "Baofeng / AR-5RM class HT",
    interface: "Digirig Lite (CM108 audio + GPIO3 PTT)",
    board: "Digirig Lite",
    ptt: "CM108 GPIO3",
    cos: "Audio level (VOX); wire COS pad for CM108 COS if preferred",
    levels: "Speaker ~75%, Mic ~18 dB, Auto Gain off",
    notes: [
      "No USB-serial on Digirig Lite — do not look for RTS/DTR PTT.",
      "One HT is half-duplex: use Timing → Simplex node for live local use without a separate TX radio.",
      "USB drops on TX near the Pi are usually RFI into USB.",
    ],
  },
  {
    id: "digirig-dual",
    title: "Digirig Lite local + Digirig Lite UHF link",
    host: "Raspberry Pi 4",
    radio: "Baofeng VHF local + Baofeng UHF link",
    interface: "Two Digirig Lite dongles",
    board: "Digirig Lite (both)",
    ptt: "CM108 GPIO3 on each dongle",
    cos: "VOX on each dongle",
    levels: "Per-dongle Mic/Speaker as above",
    notes: [
      "Pick each Digirig by PortAudio name after USB re-enumeration.",
      "Configure the second pair under Audio & tones → Link radio.",
    ],
  },
  {
    id: "dmk-uri",
    title: "DMK URI / USB-RIM Lite + Raspberry Pi",
    host: "Raspberry Pi",
    radio: "Repeater pair (full duplex)",
    interface: "DMK URI or USB-RIM Lite",
    board: "DMK URI or USB-RIM Lite",
    ptt: "CM108 GPIO3",
    cos: "CM108 VOL_DN, active low",
    levels: "Speaker 75%, Mic 18 dB",
    notes: ["Prefer hardware COS over VOX when VOL_DN is wired."],
  },
  {
    id: "cm108-homebrew",
    title: "Generic CM108 / CM119 USB dongle",
    host: "Raspberry Pi",
    radio: "Any radio with audio + PTT/COS lines",
    interface: "Homebrew CM108-family dongle",
    board: "Generic CM108/CM119",
    ptt: "CM108 GPIO3 (chip pin 13 via transistor)",
    cos: "CM108 VOL_DN (chip pin 48), active low",
    levels: "Set by ear; save from Sound card levels",
    notes: ["See docs/cm108-wiring.md for the soldering guide."],
  },
  {
    id: "aioc",
    title: "AIOC",
    host: "Raspberry Pi",
    radio: "HT or mobile with AIOC cable",
    interface: "AIOC",
    board: "AIOC",
    ptt: "CM108 GPIO3",
    cos: "Virtual COS on VOL_DN",
    levels: "Leave alone unless the meter shows clipping or silence",
    notes: [],
  },
  {
    id: "svxlink-card",
    title: "SVXLink Card (F5UII/F8ASB)",
    host: "Raspberry Pi with header GPIO",
    radio: "Station radio(s) on the SVXLink card",
    interface: "SVXLink Card",
    board: "SVXLink Card",
    ptt: "GPIO16 (port 2: GPIO17)",
    cos: "GPIO19 active low (port 2: GPIO18)",
    levels: "Speaker 75%, Mic 69%",
    notes: ["Port 2 becomes the link radio when its sound devices are chosen."],
  },
  {
    id: "svxlink-basic",
    title: "SVXLink Basic Board",
    host: "Raspberry Pi with header GPIO",
    radio: "Station radio(s) on the basic board",
    interface: "SVXLink Basic Board",
    board: "SVXLink Basic Board",
    ptt: "GPIO24 (port 2: GPIO18)",
    cos: "GPIO23 active high (port 2: GPIO25)",
    levels: "Speaker 75%, Mic 69%",
    notes: [],
  },
];

const DOCS_URL =
  "https://github.com/gmisner/moreopenrepeater/blob/main/docs/known-working-configs.md";

export function Configs() {
  return (
    <Page
      title="Known configs"
    >
      <Card withBorder padding="lg" radius="md">
        <Stack gap="sm">
          <Text size="sm">
            These match <Code>docs/known-working-configs.md</Code>. Full wiring notes live in{" "}
            <Code>docs/raspberry-pi.md</Code>, <Code>docs/cm108-wiring.md</Code>, and{" "}
            <Code>docs/troubleshooting-digirig-ht.md</Code>.
          </Text>
          <Text size="sm">
            Got a setup that works and isn’t listed?{" "}
            <Anchor href={DOCS_URL} target="_blank" rel="noreferrer">
              Submit a PR
            </Anchor>{" "}
            with host, radio, interface, board preset, PTT, COS, levels, and any gotchas — and update
            this page’s list so the gallery stays in sync.
          </Text>
        </Stack>
      </Card>

      <Stack gap="md">
        {ENTRIES.map((entry) => (
          <Card key={entry.id} withBorder padding="lg" radius="md" id={entry.id}>
            <Stack gap="sm">
              <Group justify="space-between" align="flex-start" wrap="wrap">
                <Title order={2} size="h4">
                  {entry.title}
                </Title>
                <Text size="sm" c="dimmed">
                  Board: {entry.board}
                </Text>
              </Group>
              <Table withRowBorders={false} verticalSpacing={4}>
                <Table.Tbody>
                  <Row label="Host" value={entry.host} />
                  <Row label="Radio" value={entry.radio} />
                  <Row label="Interface" value={entry.interface} />
                  <Row label="PTT" value={entry.ptt} />
                  <Row label="COS" value={entry.cos} />
                  <Row label="Levels" value={entry.levels} />
                </Table.Tbody>
              </Table>
              {entry.notes.length ? (
                <Stack gap={4}>
                  {entry.notes.map((note) => (
                    <Text key={note} size="sm" c="dimmed">
                      {note}
                    </Text>
                  ))}
                </Stack>
              ) : null}
            </Stack>
          </Card>
        ))}
      </Stack>
    </Page>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <Table.Tr>
      <Table.Td w={110} style={{ verticalAlign: "top" }}>
        <Text size="sm" c="dimmed" fw={600}>
          {label}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">{value}</Text>
      </Table.Td>
    </Table.Tr>
  );
}
