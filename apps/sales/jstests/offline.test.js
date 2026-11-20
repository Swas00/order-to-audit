// Run with: node --test apps/sales/jstests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { createQueue, sendOne, syncAll } = require("../static/sales/offline.js");

function memoryStorage() {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); } };
}
const item = (id, extra) => Object.assign({ client_uuid: id, customer: "1", customer_name: "Retailer A", quantities: { 1: 150 }, captured_at: "2026-11-20T10:00:00Z" }, extra);
const reply = (status, body) => async () => ({ status, ok: status >= 200 && status < 300, json: async () => body });
const opts = (fetchFn) => ({ url: "/orders/api/submit/", fetchFn, csrf: () => "tok" });

test("queue add / list / remove / update, no duplicates", () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a")); q.add(item("a")); q.add(item("b"));
  assert.deepEqual(q.list().map((i) => i.client_uuid), ["a", "b"]);
  q.update("a", { rejected: "no" });
  assert.equal(q.list()[0].rejected, "no");
  q.remove("a");
  assert.deepEqual(q.list().map((i) => i.client_uuid), ["b"]);
});

test("corrupt storage yields an empty queue instead of throwing", () => {
  const s = memoryStorage(); s.setItem("k", "{not json");
  assert.deepEqual(createQueue(s, "k").list(), []);
});

test("queues are separate per user key", () => {
  const s = memoryStorage();
  createQueue(s, "oas.queue.1").add(item("a"));
  assert.equal(createQueue(s, "oas.queue.2").list().length, 0);
});

test("sendOne: success sends csrf header and idempotency key", async () => {
  let seen;
  const f = async (url, init) => { seen = { url, init }; return { status: 200, ok: true, json: async () => ({ ok: true, order: { number: "ORD-0001" } }) }; };
  const r = await sendOne(item("a"), opts(f));
  assert.equal(r.state, "sent");
  assert.equal(seen.init.headers["X-CSRFToken"], "tok");
  assert.equal(JSON.parse(seen.init.body).client_uuid, "a");
});

test("sendOne classifies failures", async () => {
  assert.equal((await sendOne(item("a"), opts(async () => { throw new TypeError("offline"); }))).state, "retry");
  assert.equal((await sendOne(item("a"), opts(reply(401, { ok: false })))).state, "login");
  assert.equal((await sendOne(item("a"), opts(reply(403, { ok: false })))).state, "login");
  assert.equal((await sendOne(item("a"), opts(reply(500, {})))).state, "retry");
  assert.equal((await sendOne(item("a"), opts(async () => ({ status: 200, ok: true, json: async () => { throw new Error("html"); } })))).state, "retry");
  const r = await sendOne(item("a"), opts(reply(400, { ok: false, error: "This customer is not on your list" })));
  assert.deepEqual([r.state, r.error], ["rejected", "This customer is not on your list"]);
});

test("syncAll: offline keeps everything queued and stops at the first failure", async () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a")); q.add(item("b"));
  let calls = 0;
  const results = await syncAll(q, opts(async () => { calls++; throw new TypeError("offline"); }));
  assert.equal(calls, 1);                       // did not hammer the network for every order
  assert.equal(results.length, 1);
  assert.deepEqual(q.list().map((i) => i.client_uuid), ["a", "b"]);
});

test("syncAll: sends in order, removes sent, marks rejected, continues after a rejection", async () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a")); q.add(item("b")); q.add(item("c"));
  const order = [];
  const f = async (url, init) => {
    const id = JSON.parse(init.body).client_uuid; order.push(id);
    if (id === "b") return { status: 400, ok: false, json: async () => ({ ok: false, error: "bad" }) };
    return { status: 200, ok: true, json: async () => ({ ok: true, order: { number: "ORD-" + id } }) };
  };
  await syncAll(q, opts(f));
  assert.deepEqual(order, ["a", "b", "c"]);
  assert.deepEqual(q.list().map((i) => [i.client_uuid, i.rejected]), [["b", "bad"]]);
  await syncAll(q, opts(f));                    // rejected orders are not retried
  assert.deepEqual(order, ["a", "b", "c"]);
});

test("syncAll: retry after reconnect delivers once; resending the same id is safe because the server dedupes", async () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a"));
  await syncAll(q, opts(async () => { throw new TypeError("offline"); }));
  assert.equal(q.list().length, 1);
  const f = reply(200, { ok: true, order: { number: "ORD-0001" } });
  const r = await syncAll(q, opts(f));
  assert.equal(r[0].result.state, "sent");
  assert.equal(q.list().length, 0);
});

test("syncAll: concurrent calls do not double-send", async () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a"));
  let calls = 0;
  const f = async () => { calls++; await new Promise((r) => setTimeout(r, 20)); return { status: 200, ok: true, json: async () => ({ ok: true, order: { number: "X" } }) }; };
  await Promise.all([syncAll(q, opts(f)), syncAll(q, opts(f))]);
  assert.equal(calls, 1);
});

test("session expiry keeps orders queued", async () => {
  const q = createQueue(memoryStorage(), "k");
  q.add(item("a"));
  const r = await syncAll(q, opts(reply(401, { ok: false })));
  assert.equal(r[0].result.state, "login");
  assert.equal(q.list().length, 1);
});
