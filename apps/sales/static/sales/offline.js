/* Offline order capture for the salesman's phone.

   Flow: submit -> the order is written to a local queue first -> we try to send it -> on success it is
   removed from the queue. If the network is down it stays queued and is retried when the phone is back
   online, when the page is opened, and every 20 s while the page is open.
   The server treats the order's client_uuid as an idempotency key, so retries never create duplicates.

   The queue/sync logic is plain functions (no DOM) so it can be unit-tested in Node. */

(function (root) {
  "use strict";

  function createQueue(storage, key) {
    function read() {
      try { return JSON.parse(storage.getItem(key) || "[]"); } catch (e) { return []; }
    }
    function write(items) {
      storage.setItem(key, JSON.stringify(items));
    }
    return {
      list: read,
      add: function (item) {
        var items = read();
        if (!items.some(function (i) { return i.client_uuid === item.client_uuid; })) items.push(item);
        write(items);
      },
      remove: function (uuid) {
        write(read().filter(function (i) { return i.client_uuid !== uuid; }));
      },
      update: function (uuid, patch) {
        write(read().map(function (i) { return i.client_uuid === uuid ? Object.assign({}, i, patch) : i; }));
      },
    };
  }

  /* Send one queued order. Returns {state: "sent"|"rejected"|"retry"|"login", order?, error?}.
     retry  = network problem or server error: keep it and try again later
     login  = session expired: keep it, ask the user to log in
     rejected = the server understood and refused (e.g. customer no longer yours): keep for the user to see */
  async function sendOne(item, opts) {
    var payload = {
      client_uuid: item.client_uuid, customer: item.customer, quantities: item.quantities,
      note: item.note || "", captured_at: item.captured_at,
    };
    var response;
    try {
      response = await opts.fetchFn(opts.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": opts.csrf() || "", "Accept": "application/json" },
        body: JSON.stringify(payload),
        credentials: "same-origin",
      });
    } catch (e) {
      return { state: "retry" };
    }
    if (response.status === 401 || response.status === 403) return { state: "login" };
    var data = null;
    try { data = await response.json(); } catch (e) { return { state: "retry" }; }
    if (response.ok && data && data.ok) return { state: "sent", order: data.order };
    if (response.status >= 500) return { state: "retry" };
    return { state: "rejected", error: (data && data.error) || "Rejected by the factory" };
  }

  /* Try every queued order once, oldest first. Only one sync runs at a time. */
  var running = false;
  async function syncAll(queue, opts) {
    if (running) return [];
    running = true;
    var results = [];
    try {
      var items = queue.list().filter(function (i) { return !i.rejected; });
      for (var n = 0; n < items.length; n++) {
        var item = items[n];
        var r = await sendOne(item, opts);
        results.push({ item: item, result: r });
        if (r.state === "sent") queue.remove(item.client_uuid);
        else if (r.state === "rejected") queue.update(item.client_uuid, { rejected: r.error });
        else break; // network or login problem: stop, keep order, retry later
      }
    } finally {
      running = false;
    }
    return results;
  }

  function newUuid() {
    if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  var api = { createQueue: createQueue, sendOne: sendOne, syncAll: syncAll, newUuid: newUuid };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.OASOffline = api;

  /* ---------- browser wiring (skipped under Node) ---------- */
  if (typeof document === "undefined") return;
  var cfgEl = document.getElementById("offline-config");
  var form = document.getElementById("orderForm");
  if (!cfgEl || !form) return;
  var cfg = JSON.parse(cfgEl.textContent);
  var queue = createQueue(root.localStorage, "oas.queue." + cfg.userId);
  var banner = document.getElementById("syncBanner");
  var pendingBox = document.getElementById("pendingOrders");

  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  var sendOpts = { url: cfg.submitUrl, fetchFn: function () { return root.fetch.apply(root, arguments); }, csrf: csrf };

  function say(text, kind) {
    banner.textContent = text;
    banner.className = "sync " + (kind || "");
    banner.hidden = !text;
  }

  function renderPending() {
    var items = queue.list();
    pendingBox.innerHTML = "";
    if (!items.length) return;
    var h = document.createElement("h2");
    h.textContent = "Saved on this phone (" + items.length + ")";
    pendingBox.appendChild(h);
    items.forEach(function (i) {
      var d = document.createElement("div");
      d.className = "card pending";
      var packets = Object.keys(i.quantities).reduce(function (s, k) { return s + i.quantities[k]; }, 0);
      d.textContent = i.customer_name + ": " + packets + " packets, " + new Date(i.captured_at).toLocaleString() + ". ";
      var st = document.createElement("strong");
      st.textContent = i.rejected ? "Not accepted: " + i.rejected : "Waiting to send";
      if (i.rejected) st.className = "error";
      d.appendChild(st);
      if (i.rejected) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "btn small"; b.textContent = "Discard";
        b.onclick = function () { queue.remove(i.client_uuid); renderPending(); };
        d.appendChild(document.createTextNode(" "));
        d.appendChild(b);
      }
      pendingBox.appendChild(d);
    });
  }

  async function sync() {
    if (!queue.list().some(function (i) { return !i.rejected; })) { renderPending(); return []; }
    var results = await syncAll(queue, sendOpts);
    var sent = results.filter(function (r) { return r.result.state === "sent"; });
    var login = results.some(function (r) { return r.result.state === "login"; });
    if (login) say("Your session has expired. Log in again to send the saved orders.", "warn");
    else if (sent.length) say(sent.map(function (r) { return r.result.order.number; }).join(", ") + " sent to the factory.", "ok");
    renderPending();
    return results;
  }

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var customer = form.elements["customer"].value;
    var quantities = {};
    form.querySelectorAll(".qty").forEach(function (q) {
      var n = parseInt(q.value, 10);
      if (n > 0) quantities[q.dataset.sku] = n;
    });
    if (!customer) { say("Choose a customer.", "warn"); return; }
    if (!Object.keys(quantities).length) { say("Add at least one product.", "warn"); return; }
    var sel = form.elements["customer"];
    var item = {
      client_uuid: form.elements["client_uuid"].value, customer: customer, customer_name: sel.options[sel.selectedIndex].text,
      quantities: quantities, note: form.elements["note"].value, captured_at: new Date().toISOString(),
    };
    queue.add(item); // saved first: nothing is lost if the connection drops or the tab closes
    var btn = document.getElementById("submitBtn");
    btn.disabled = true;
    var results = await syncAll(queue, sendOpts);
    btn.disabled = false;
    var mine = results.filter(function (r) { return r.item.client_uuid === item.client_uuid; })[0];
    if (mine && mine.result.state === "sent") { root.location.href = mine.result.order.url; return; }
    if (mine && mine.result.state === "rejected") { say("Order not accepted: " + mine.result.error, "warn"); renderPending(); return; }
    if (mine && mine.result.state === "login") say("Saved on this phone. Log in again to send it.", "warn");
    else say("No connection. The order is saved on this phone and will be sent automatically.", "ok");
    form.reset();
    form.elements["client_uuid"].value = newUuid(); // next order gets its own id
    form.querySelectorAll(".qty").forEach(function (q) { q.value = ""; });
    sel.dispatchEvent(new Event("change")); // lets the running-total code recalculate
    renderPending();
  });

  root.addEventListener("online", sync);
  setInterval(function () { if (root.navigator.onLine !== false) sync(); }, 20000);

  if ("serviceWorker" in root.navigator) {
    root.navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () { /* works without it */ });
  }
  var logout = document.querySelector("form[action$='/logout/']");
  if (logout) logout.addEventListener("submit", function () {  // do not leave one user's page cached for the next
    if (root.caches) root.caches.delete("oas-shell-v1");
  });

  renderPending();
  sync();
})(typeof window !== "undefined" ? window : globalThis);
