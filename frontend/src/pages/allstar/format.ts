export function shortTime(value: string | number) {
  const when = new Date(value);
  const time = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (when.toDateString() === new Date().toDateString()) return time;
  const farOff = when.getTime() - Date.now() > 6 * 24 * 3600 * 1000;
  const day = when.toLocaleDateString([], farOff ? { weekday: "short", month: "short", day: "numeric" } : { weekday: "short" });
  return `${day} ${time}`;
}
