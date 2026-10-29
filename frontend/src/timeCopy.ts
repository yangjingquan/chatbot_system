const ISO_TIMESTAMP =
  /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})\b/g;

const beijingDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function formatBeijingDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = Object.fromEntries(
    beijingDateTime
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}（北京时间）`;
}

/** Normalize legacy ISO timestamps embedded in generated follow-up notes. */
export function followupCopy(value: string): string {
  const customerCopy = value.startsWith("【试听·自动待办】")
    ? value.replaceAll("演示基础班", "基础班").replaceAll("（示例）", "")
    : value;
  return customerCopy.replace(ISO_TIMESTAMP, formatBeijingDateTime);
}
