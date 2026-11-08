import { Button, TextInput, Text } from "@mantine/core";
import { type FormEvent, useState } from "react";
import "./allstar.css";

type Chip = { node: string; name?: string; monitor?: boolean };

function chipLabel(chip: Chip) {
  return chip.name?.trim() ? `${chip.name.trim()} (${chip.node})` : chip.node;
}

export function ConnectBar({
  favorites,
  recent,
  linked,
  onConnect,
}: {
  favorites: Chip[];
  recent: Chip[];
  linked: string[];
  onConnect: (node: string, monitor: boolean) => Promise<void> | void;
}) {
  const [node, setNode] = useState("");
  const [monitor, setMonitor] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const target = node.trim();
    if (!target) return;
    setBusy(true);
    try {
      await onConnect(target, monitor);
      setNode("");
    } finally {
      setBusy(false);
    }
  };

  const chip = (item: Chip) => {
    const isLinked = linked.includes(item.node);
    return (
      <Button
        key={item.node}
        type="button"
        size="xs"
        variant={isLinked ? "light" : "default"}
        disabled={isLinked}
        title={item.monitor ? "Connects monitor only" : undefined}
        onClick={() => void onConnect(item.node, Boolean(item.monitor))}
      >
        {isLinked ? "Linked · " : ""}
        {chipLabel(item)}
        {item.monitor && !isLinked ? " · mon" : ""}
      </Button>
    );
  };

  return (
    <div className="mor-as mor-connect">
      <form className="mor-connect-form" onSubmit={submit}>
        <TextInput
          aria-label="Node number to connect"
          placeholder="Node number, like 29332"
          inputMode="numeric"
          autoComplete="off"
          classNames={{ input: "mor-connect-input" }}
          value={node}
          onChange={(event) => setNode(event.currentTarget.value.replace(/[^\d]/g, ""))}
        />
        <Button
          type="button"
          variant={monitor ? "light" : "default"}
          onClick={() => setMonitor((v) => !v)}
          aria-pressed={monitor}
          title="Listen without transmitting into that node"
        >
          Monitor only
        </Button>
        <Button type="submit" loading={busy} disabled={!node.trim()}>
          Connect
        </Button>
      </form>
      {favorites.length ? (
        <div className="mor-chips">
          <span className="mor-chips-label">Favorites</span>
          {favorites.map(chip)}
        </div>
      ) : null}
      {recent.length ? (
        <div className="mor-chips">
          <span className="mor-chips-label">Recent</span>
          {recent.map(chip)}
        </div>
      ) : null}
      {!favorites.length && !recent.length ? (
        <Text size="sm" c="dimmed">
          Favorites and recently used nodes will show up here as one-click buttons.
        </Text>
      ) : null}
    </div>
  );
}
