import { test } from "node:test";
import assert from "node:assert/strict";
import { restoreSession, consumeEvents } from "../src/chatTransport.ts";

const credential = { id: "session-1", token: "private-token" };
test("temporary restore failure preserves credentials and does not create another session", async () => {
  let created = false;
  await assert.rejects(
    restoreSession(
      credential,
      async () => {
        throw Object.assign(Error("temporary"), { status: 503 });
      },
      async () => {
        created = true;
        return { id: "other", token: "other" };
      },
    ),
  );
  assert.equal(created, false);
});
test("expired credentials allow an explicit fresh session", async () => {
  const result = await restoreSession(
    credential,
    async () => {
      throw Object.assign(Error("expired"), { status: 410 });
    },
    async () => ({ id: "new", token: "new-token" }),
  );
  assert.equal(result.credential.id, "new");
});
function stream(text) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
}
test("stream ending without a terminal event is rejected", async () => {
  await assert.rejects(
    consumeEvents(
      stream('event: delta\ndata: {"text":"partial"}\n\n'),
      () => {},
    ),
    /未完成/,
  );
});
test("terminal frame can be split across network packets", async () => {
  const seen = [];
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const part of [
        'event: delta\ndata: {"text":"完整"}\n',
        '\nevent: done\ndata: {"status":"bot"}\n\n',
      ])
        controller.enqueue(encoder.encode(part));
      controller.close();
    },
  });
  await consumeEvents(body, (type, data) => seen.push([type, data]));
  assert.equal(seen[1][0], "done");
});
