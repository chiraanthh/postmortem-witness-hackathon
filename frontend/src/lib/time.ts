/** mm:ss from incident-audio milliseconds. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** "+12.4s" style relative marker for a timeline row. */
export function formatOffset(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Round latency to a whole millisecond for display. */
export function ms(n: number): string {
  return Math.round(n).toString();
}
