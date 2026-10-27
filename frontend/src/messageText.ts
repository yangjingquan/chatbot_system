/** Show model Markdown as plain text, without interpreting HTML or clickable links. */
export function plainAssistantText(value: string): string {
  return value
    .replace(/!\[([^\]]*)\]\([^\n)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\(([^\n)]+)\)/g, "$1 ($2)")
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|[，。！？,.!?：:]|$)/g, "$1$2")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/`([^`\n]+)`/g, "$1");
}
