import { Badge, Button, Group, Text } from "@mantine/core";
import { useState } from "react";
import { shortTime } from "./format";
import { curvePath, labelSide, ringPositions, type LinkRow } from "./model";
import "./allstar.css";

const MAX_DRAWN = 12;

function who(link: LinkRow) {
  return [link.name, link.callsign].filter(Boolean).join(", ");
}

export function LinkMap({
  hub,
  callsign,
  hubKeyed,
  links,
  canWrite,
  onDisconnect,
  onDisconnectAll,
}: {
  hub: string;
  callsign: string;
  hubKeyed: boolean;
  links: LinkRow[];
  canWrite: boolean;
  onDisconnect: (node: string) => void;
  onDisconnectAll: () => void;
}) {
  const [hot, setHot] = useState<string | null>(null);
  const drawn = links.slice(0, MAX_DRAWN);
  const spots = ringPositions(drawn.length || 3);
  const anyKeyed = links.some((l) => l.keyed);
  const hubTalking = hubKeyed || anyKeyed;

  return (
    <div className="mor-as mor-as-links" data-empty={links.length ? undefined : ""}>
      <div>
        <div
          className="mor-map"
          role="img"
          aria-label={
            links.length
              ? `Node ${hub} is linked to ${links.length} ${links.length === 1 ? "node" : "nodes"}: ${links.map((l) => l.node).join(", ")}.`
              : `Node ${hub} has no links.`
          }
        >
          <svg className="mor-map-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {drawn.map((link, i) => (
              <path
                key={link.node}
                d={curvePath(spots[i].x, spots[i].y)}
                data-keyed={link.keyed ? "" : undefined}
                data-hot={hot === link.node ? "" : undefined}
                data-mode={link.mode?.toLowerCase().includes("mon") ? "monitor" : undefined}
              />
            ))}
          </svg>

          <div
            className="mor-map-hub"
            data-keyed={hubTalking ? "" : undefined}
            data-idle={hub === "—" ? "" : undefined}
            aria-hidden="true"
          >
            <span className="mor-map-num">{hub}</span>
            {callsign ? <span className="mor-map-hub-call">{callsign}</span> : null}
          </div>

          {links.length === 0
            ? spots.map((spot, i) => (
                <div
                  key={`ghost-${i}`}
                  className="mor-map-node"
                  style={{ left: `${spot.x}%`, top: `${spot.y}%` }}
                  data-ghost=""
                  aria-hidden="true"
                >
                  <span className="mor-map-dot" />
                </div>
              ))
            : drawn.map((link, i) => (
                <div
                  key={link.node}
                  className="mor-map-node"
                  style={{ left: `${spots[i].x}%`, top: `${spots[i].y}%` }}
                  data-side={labelSide(spots[i].y)}
                  data-keyed={link.keyed ? "" : undefined}
                  data-hot={hot === link.node ? "" : undefined}
                  aria-hidden="true"
                >
                  <span className="mor-map-dot" />
                  <span className="mor-map-label">
                    <span className="mor-map-num">{link.node}</span>
                    {who(link) ? <span className="mor-map-sub">{who(link)}</span> : null}
                    {link.keyed ? <span className="mor-map-talk">Talking</span> : null}
                  </span>
                </div>
              ))}

          {links.length === 0 ? <div className="mor-map-empty">No nodes linked. Connect one below.</div> : null}
        </div>
        {links.length > MAX_DRAWN ? (
          <Text size="xs" c="dimmed" mt={6}>
            Showing {MAX_DRAWN} of {links.length}. The list has all of them.
          </Text>
        ) : null}
      </div>

      {links.length ? (
      <div>
        <Group justify="space-between" mb="xs" wrap="nowrap">
          <Text size="sm" c="dimmed">
            {links.length ? `${links.length} linked` : "Nothing linked"}
          </Text>
          {canWrite && links.length >= 2 ? (
            <Button color="red" variant="outline" size="xs" onClick={onDisconnectAll}>
              Disconnect all
            </Button>
          ) : null}
        </Group>
        {links.length ? (
          <ul className="mor-as-list" aria-label="Linked nodes">
            {links.map((link) => (
              <li
                key={link.node}
                className="mor-as-row"
                data-keyed={link.keyed ? "" : undefined}
                data-hot={hot === link.node ? "" : undefined}
                onMouseEnter={() => setHot(link.node)}
                onMouseLeave={() => setHot(null)}
                onFocus={() => setHot(link.node)}
                onBlur={() => setHot(null)}
              >
                <span className="mor-as-row-dot" aria-hidden="true" />
                <div className="mor-as-row-main">
                  <span className="mor-as-row-node">{link.node}</span>
                  <span>{who(link)}</span>
                  {link.keyed ? <span className="mor-as-row-talk">Talking</span> : null}
                  {link.mode ? (
                    <Badge size="sm" variant="outline" color="gray">
                      {link.mode}
                    </Badge>
                  ) : null}
                  {link.until ? <span className="mor-as-row-meta">until {shortTime(link.until)}</span> : null}
                </div>
                {canWrite ? (
                  <Button color="red" variant="subtle" size="compact-sm" onClick={() => onDisconnect(link.node)}>
                    Disconnect
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      ) : null}
    </div>
  );
}
