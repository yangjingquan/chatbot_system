// Radix reserves the empty string for its placeholder. Prefix every option so
// an empty filter remains a real, selectable value without colliding with IDs.
export function encodeSelectValue(value: string | number): string {
  return `option:${value}`;
}

export function decodeSelectValue(value: string): string {
  return value.startsWith("option:") ? value.slice("option:".length) : value;
}
