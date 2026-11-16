"""Per-module metrics in Prometheus text format.

Each module serves `GET /metrics` on its manifest `health_port` and renders
this registry. Metric and label names are fixed when a metric is created, so a
caller cannot grow the number of series without bound by inventing label values
at runtime. Values are plain numbers updated under a lock that is never held
across I/O.
"""
from __future__ import annotations

import re
import threading
from typing import Mapping

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._meta: dict[str, tuple[str, str, tuple[str, ...]]] = {}  # name -> (kind, help, label names)
        self._values: dict[str, dict[tuple[str, ...], float]] = {}

    def _register(self, name: str, kind: str, help_text: str, labels: tuple[str, ...]) -> None:
        if not _NAME.match(name) or not all(_NAME.match(label) for label in labels):
            raise ValueError(f"bad metric or label name: {name} {labels}")
        with self._lock:
            existing = self._meta.get(name)
            if existing is not None and existing != (kind, help_text, labels):
                raise ValueError(f"metric {name} was registered differently before")
            self._meta[name] = (kind, help_text, labels)
            self._values.setdefault(name, {})

    def counter(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self._register(name, "counter", help_text, labels)

    def gauge(self, name: str, help_text: str, labels: tuple[str, ...] = ()) -> None:
        self._register(name, "gauge", help_text, labels)

    def _key(self, name: str, labels: Mapping[str, str]) -> tuple[str, ...]:
        wanted = self._meta[name][2]
        if set(labels) != set(wanted):
            raise ValueError(f"{name} takes labels {wanted}, got {tuple(labels)}")
        return tuple(str(labels[label]) for label in wanted)

    def inc(self, name: str, amount: float = 1, **labels: str) -> None:
        with self._lock:
            key = self._key(name, labels)
            self._values[name][key] = self._values[name].get(key, 0.0) + amount

    def set(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._values[name][self._key(name, labels)] = float(value)

    def get(self, name: str, **labels: str) -> float:
        with self._lock:
            return self._values[name].get(self._key(name, labels), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._meta):
                kind, help_text, label_names = self._meta[name]
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {kind}")
                series = self._values[name] or ({(): 0.0} if not label_names else {})
                for key, value in sorted(series.items()):
                    labels = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(label_names, key))
                    lines.append(f"{name}{{{labels}}} {value:g}" if labels else f"{name} {value:g}")
        return "\n".join(lines) + "\n"
