export type Credential = { id: string; token: string };

export async function restoreSession<T>(
  saved: Credential | null,
  load: (credential: Credential) => Promise<T>,
  create: () => Promise<Credential>,
): Promise<{ credential: Credential; history: T | null }> {
  if (saved) {
    try {
      return { credential: saved, history: await load(saved) };
    } catch (error) {
      const status = (error as { status?: number }).status;
      if (!status || ![401, 403, 404, 410].includes(status)) throw error;
    }
  }
  return { credential: await create(), history: null };
}

export async function consumeEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (kind: string, data: any) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const kind = frame
          .split("\n")
          .find((line) => line.startsWith("event: "))
          ?.slice(7);
        const raw = frame
          .split("\n")
          .find((line) => line.startsWith("data: "))
          ?.slice(6);
        if (!kind || !raw) continue;
        if (kind === "done" || kind === "error") terminal = true;
        onEvent(kind, JSON.parse(raw));
      }
      if (done) break;
    }
    if (!terminal) throw new Error("回答未完成，连接已中断；请重试或转人工。");
  } finally {
    reader.releaseLock();
  }
}
