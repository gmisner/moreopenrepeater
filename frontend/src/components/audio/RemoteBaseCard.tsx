import { Accordion, NumberInput, Select, SimpleGrid, TextInput } from "@mantine/core";
import { labeled } from "../FieldHint";
import { CtcssToneSelect } from "./CtcssToneSelect";
import type { ConfigSlice, LinkValues } from "./types";

export function RemoteBaseCard({
  link,
  remoteOffset,
  remoteTone,
}: {
  link: ConfigSlice<LinkValues>;
  remoteOffset: number | "";
  remoteTone: string | null;
}) {
  return (
              <Accordion.Item value="remote">
                <Accordion.Control>Remote base</Accordion.Control>
                <Accordion.Panel>
                  <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                    <TextInput
                      label={labeled(
                        "Radio control (rigctld)",
                        "Hamlib rigctld for the radio. Blank: no CAT, so DTMF tuning is off.",
                      )}
                      placeholder="localhost:4532"
                      spellCheck={false}
                      autoComplete="off"
                      value={link.values.remote_base_rigctld}
                      onChange={(event) => link.setField("remote_base_rigctld", event.currentTarget.value)}
                    />
                    <NumberInput
                      label="Frequency"
                      suffix=" MHz"
                      min={1}
                      max={10000}
                      step={0.0001}
                      decimalScale={4}
                      value={link.values.remote_base_mhz}
                      onChange={(value) => link.setField("remote_base_mhz", Number(value) || 0)}
                      required
                    />
                    <Select
                      label="Shift"
                      data={[
                        { value: "simplex", label: "Simplex" },
                        { value: "minus", label: "Minus" },
                        { value: "plus", label: "Plus" },
                      ]}
                      value={link.values.remote_base_shift}
                      onChange={(value) => link.setField("remote_base_shift", value || "simplex")}
                      allowDeselect={false}
                    />
                    <NumberInput
                      label={labeled("Offset", "Blank uses the band's usual offset.")}
                      suffix=" MHz"
                      min={0.001}
                      max={100}
                      step={0.001}
                      decimalScale={3}
                      value={remoteOffset === "" ? undefined : remoteOffset}
                      onChange={(value) =>
                        link.setField(
                          "remote_base_offset_mhz",
                          value === "" || value === undefined || value === null ? "" : Number(value),
                        )
                      }
                      placeholder="band's usual"
                    />
                    <CtcssToneSelect
                      value={remoteTone}
                      onChange={(value) =>
                        link.setField("remote_base_tone_hz", value == null || value === "" ? "" : Number(value))
                      }
                    />
                    <Select
                      label="Mode"
                      data={[
                        { value: "FM", label: "FM" },
                        { value: "AM", label: "AM" },
                        { value: "USB", label: "USB" },
                        { value: "LSB", label: "LSB" },
                      ]}
                      value={link.values.remote_base_rig_mode}
                      onChange={(value) => link.setField("remote_base_rig_mode", value || "FM")}
                      allowDeselect={false}
                    />
                    <TextInput
                      label={labeled("Allowed bands", "MHz ranges it may be tuned to. Keep them inside your license.")}
                      placeholder="144-148, 222-225, 420-450"
                      spellCheck={false}
                      value={link.values.remote_base_ranges}
                      onChange={(event) => link.setField("remote_base_ranges", event.currentTarget.value)}
                    />
                    <NumberInput
                      label={labeled("Turn off after", 'Unused this long → "Remote base off". 0 = stays on.')}
                      suffix=" min"
                      min={0}
                      max={1440}
                      value={link.values.remote_base_idle_minutes}
                      onChange={(value) => link.setField("remote_base_idle_minutes", Number(value) || 0)}
                      required
                    />
                  </SimpleGrid>
                </Accordion.Panel>
              </Accordion.Item>
  );
}
