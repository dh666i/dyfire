const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return {promise, resolve};
}

function response(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    redirected: false,
    async json() { return body; },
  };
}

class Element {
  constructor() {
    this.listeners = new Map();
    this.listenerAddCount = new Map();
    this.dataset = {};
    this.attributes = new Map();
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.closeCount = 0;
    this.srcSetCount = 0;
    this._src = "";
    this.value = "";
    this.focused = false;
    this.naturalWidth = 1280;
    this.naturalHeight = 720;
    this.replaceHandler = null;
  }

  set src(value) {
    this._src = value;
    this.attributes.set("src", value);
    this.srcSetCount += 1;
  }

  get src() { return this._src; }

  addEventListener(name, listener) {
    this.listenerAddCount.set(name, (this.listenerAddCount.get(name) || 0) + 1);
    this.listeners.set(name, listener);
  }

  emit(name, supplied = {}) {
    const listener = this.listeners.get(name);
    if (!listener) return undefined;
    const event = {preventDefault() { this.defaultPrevented = true; }, ...supplied};
    return listener(event);
  }

  getBoundingClientRect() {
    return {left: 10, top: 20, width: 800, height: 450};
  }

  setAttribute(name, value) { this.attributes.set(name, value); }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "src") this._src = "";
  }

  replaceWith(replacement) {
    if (this.replaceHandler) this.replaceHandler(replacement);
  }

  showModal() { this.open = true; }

  close() {
    this.open = false;
    this.closeCount += 1;
  }

  focus() { this.focused = true; }
}

function createEnvironment(
  fetchImpl,
  {
    codeControls = true,
    confirmResult = true,
    newAccountControl = true,
    reloginAccounts = [],
  } = {},
) {
  const selectors = [
    "[data-account-scan]",
    "#scan-dialog",
    "#scan-dialog-title",
    "#scan-close",
    "#scan-cancel",
    "#scan-stage",
    "#scan-status",
    "#scan-countdown",
    "#scan-qr-panel",
    "#scan-qr-shell",
    "#scan-qr",
    "#scan-qr-loading",
    "#scan-browser-hint",
    "#scan-result",
    "#scan-result-title",
    "#scan-interaction-status",
  ];
  if (newAccountControl) selectors.push("#scan-start");
  if (codeControls) selectors.push("#scan-text", "#scan-type");
  const elements = Object.fromEntries(
    selectors.map((selector) => [selector, new Element()]),
  );
  if (elements["#scan-start"]) {
    elements["#scan-start"].textContent = "扫码绑定抖音账号";
  }
  elements["#scan-qr-panel"].hidden = true;
  elements["#scan-result"].hidden = true;
  elements["#scan-interaction-status"].hidden = true;
  elements["#scan-qr-shell"].dataset.loading = "true";
  elements["[data-account-scan]"].dataset.csrfToken = "csrf-fixture";
  const reloginButtons = reloginAccounts.map((account) => {
    const button = new Element();
    button.textContent = "重新登录";
    button.dataset.scanAccountId = account.id;
    button.dataset.scanAccountName = account.name || "";
    return button;
  });

  const images = [];
  function bindQrReplacement(node) {
    node.replaceHandler = (replacement) => {
      if (elements["#scan-qr"] !== node) return;
      elements["#scan-qr"] = replacement;
      bindQrReplacement(replacement);
    };
  }
  bindQrReplacement(elements["#scan-qr"]);

  class FakeImage extends Element {
    constructor() {
      super();
      images.push(this);
      bindQrReplacement(this);
    }
  }

  const timers = new Map();
  const windowListeners = new Map();
  const beacons = [];
  const confirmations = [];
  let nextTimer = 1;
  let reloadCount = 0;
  let now = 1_000;
  let uuidSequence = 0;

  global.document = {
    querySelector(selector) { return elements[selector] || null; },
    querySelectorAll(selector) {
      return selector === "[data-scan-account-id]" ? reloginButtons : [];
    },
  };
  global.fetch = fetchImpl;
  Object.defineProperty(global, "navigator", {
    configurable: true,
    value: {
      sendBeacon(url, body) {
        beacons.push({url, body: String(body)});
        return true;
      },
    },
  });
  const windowObject = {
    Image: FakeImage,
    addEventListener(name, listener) { windowListeners.set(name, listener); },
    clearTimeout(id) { timers.delete(id); },
    confirm(message) {
      confirmations.push(message);
      return typeof confirmResult === "function"
        ? confirmResult(message, confirmations.length)
        : confirmResult;
    },
    crypto: {
      randomUUID() {
        uuidSequence += 1;
        return `00000000-0000-4000-a000-${String(uuidSequence).padStart(12, "0")}`;
      },
    },
    getComputedStyle() { return {objectFit: "fill"}; },
    location: {
      assign() {},
      reload() { reloadCount += 1; },
    },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, {callback, delay});
      return id;
    },
  };
  global.window = windowObject;
  const originalDateNow = Date.now;
  Date.now = () => ++now;
  const source = fs.readFileSync(
    path.resolve(__dirname, "../../spark_console/static/account_scan.js"),
    "utf8",
  );
  const evaluateScript = () => vm.runInThisContext(source, {filename: "account_scan.js"});
  evaluateScript();
  Date.now = originalDateNow;
  return {
    beacons,
    confirmations,
    elements,
    evaluateScript,
    images,
    reloginButtons,
    reloadCount: () => reloadCount,
    timers,
    windowObject,
    windowListeners,
  };
}

async function flush() {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

function takeTimer(timers, delay) {
  const match = [...timers.entries()].find(([, timer]) => timer.delay === delay);
  assert(match, `no ${delay}ms timer was scheduled`);
  timers.delete(match[0]);
  return match[1].callback;
}

function takeLastTimer(timers, delay) {
  const matches = [...timers.entries()].filter(([, timer]) => timer.delay === delay);
  const match = matches[matches.length - 1];
  assert(match, `no ${delay}ms timer was scheduled`);
  timers.delete(match[0]);
  return match[1].callback;
}

function loadImage(images, index) {
  const image = images[index];
  assert(image, `image ${index} was not requested`);
  assert(typeof image.onload === "function", `image ${index} has no load handler`);
  image.onload();
  return image;
}

function failImage(images, index) {
  const image = images[index];
  assert(image, `image ${index} was not requested`);
  assert(typeof image.onerror === "function", `image ${index} has no error handler`);
  image.onerror();
  return image;
}

async function pendingIntent(controlSelector) {
  const start = deferred();
  const cancel = deferred();
  const calls = [];
  const {elements, timers} = createEnvironment((url) => {
    calls.push(url);
    return url === "/accounts/scan" ? start.promise : cancel.promise;
  });
  const startTask = elements["#scan-start"].emit("click");
  await flush();
  const intentTask = elements[controlSelector].emit("click");
  await flush();
  assert(elements["#scan-dialog"].closeCount === 0, "dialog closed before scan id existed");
  start.resolve(response(201, {
    id: "scan-pending",
    status: "queued",
    remaining_seconds: 300,
    error: null,
    message: "等待云端通道",
    account_id: null,
  }));
  await flush();
  assert(calls.includes("/accounts/scan/scan-pending/cancel"), "returned scan was not cancelled");
  cancel.resolve(response(200, {
    id: "scan-pending",
    status: "cancelled",
    remaining_seconds: 0,
    error: "cancelled",
    message: "扫码已取消",
    account_id: null,
  }));
  await Promise.all([startTask, intentTask]);
  await flush();
  assert(elements["#scan-dialog"].closeCount === 1, "dialog did not close after cancellation");
  assert(timers.size === 0, "polling remained scheduled after cancellation");
}

async function activeCancelFailure() {
  let cancelAttempt = 0;
  const {elements, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-cancel",
      status: "queued",
      remaining_seconds: 300,
      error: null,
      message: "等待云端通道",
      account_id: null,
    });
    cancelAttempt += 1;
    if (cancelAttempt === 1) {
      return response(503, {error: "internal", message: "sensitive detail"});
    }
    return response(200, {
      id: "scan-cancel",
      status: "cancelled",
      remaining_seconds: 0,
      error: "cancelled",
      message: "扫码已取消",
      account_id: null,
    });
  });
  await elements["#scan-start"].emit("click");
  assert(timers.size === 1, "active scan did not schedule polling");
  await elements["#scan-cancel"].emit("click");
  assert(elements["#scan-dialog"].open, "failed cancellation hid dialog");
  assert(elements["#scan-status"].textContent === "取消失败，请重试", "fixed cancel error missing");
  assert(!elements["#scan-status"].textContent.includes("sensitive"), "server detail leaked to UI");
  assert(!elements["#scan-cancel"].disabled, "cancel retry stayed disabled");
  assert(
    [...timers.values()].filter((timer) => timer.delay === 1000).length === 1,
    "failed cancellation did not restore status polling",
  );
  await elements["#scan-cancel"].emit("click");
  assert(elements["#scan-dialog"].closeCount === 1, "successful retry did not close dialog");
}

async function pendingStartFailure() {
  const start = deferred();
  const {elements} = createEnvironment(() => start.promise);
  const startTask = elements["#scan-start"].emit("click");
  await flush();
  await elements["#scan-close"].emit("click");
  assert(elements["#scan-close"].disabled, "close button was not locked while start was pending");
  start.resolve(response(503, {error: "unavailable", message: "暂时无法创建扫码会话"}));
  await startTask;
  assert(elements["#scan-dialog"].closeCount === 1, "failed pending start did not close dialog");
  assert(!elements["#scan-close"].disabled, "close button remained disabled");
}

async function liveViewSkipsUnchangedRevision() {
  const calls = [];
  const {elements, images, timers} = createEnvironment(async (url) => {
    calls.push(url);
    return response(url === "/accounts/scan" ? 201 : 200, {
      id: "scan-live",
      status: "awaiting_scan",
      remaining_seconds: 250,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
      frame_revision: "same-frame",
    });
  });
  const originalImage = elements["#scan-qr"];
  await elements["#scan-start"].emit("click");
  assert(images.length === 1, "initial live frame was not requested offscreen");
  assert(elements["#scan-dialog"].dataset.frameReady === "false", "loading modal expanded before a frame loaded");
  assert(elements["#scan-qr"] === originalImage, "pending frame replaced the old image");
  assert(originalImage.src === "", "pending frame changed the old image source");
  assert(images[0].src.includes("/accounts/scan/scan-live/qr?v="), "initial frame omitted cache-buster");
  loadImage(images, 0);
  assert(elements["#scan-qr"] === images[0], "loaded frame did not replace the old image");
  assert(elements["#scan-dialog"].dataset.frameReady === "true", "loaded frame did not expand the modal");
  takeTimer(timers, 1000)();
  await flush();
  assert(calls.length === 2, "one status poll was not issued");
  assert(images.length === 1, "unchanged frame revision requested another image");
  assert(elements["#scan-qr"] === images[0], "unchanged frame revision replaced the visible frame");
}

async function liveViewRefreshesWhenRevisionChanges() {
  let polls = 0;
  const {elements, images, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-revision",
      status: "awaiting_scan",
      remaining_seconds: 250,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
      frame_revision: "frame-1",
    });
    polls += 1;
    return response(200, {
      id: "scan-revision",
      status: "awaiting_scan",
      remaining_seconds: 249,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
      frame_revision: "frame-2",
    });
  });
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  takeTimer(timers, 1000)();
  await flush();
  assert(polls === 1, "status poll was not issued");
  assert(images.length === 2, "changed frame revision did not request a new image");
  assert(images[1].src.includes("v=frame-2"), "new frame revision was not used as cache key");
  assert(elements["#scan-qr"] === images[0], "changed frame replaced visible image before load");
  loadImage(images, 1);
  assert(elements["#scan-qr"] === images[1], "changed frame did not replace visible image after load");
  takeTimer(timers, 1000)();
  await flush();
  assert(images.length === 2, "same follow-up revision requested another image");
}

async function liveViewKeepsLatestQueuedRevision() {
  let polls = 0;
  const {elements, images, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-queued-revision",
      status: "awaiting_scan",
      remaining_seconds: 250,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
      frame_revision: "frame-1",
    });
    polls += 1;
    return response(200, {
      id: "scan-queued-revision",
      status: "awaiting_scan",
      remaining_seconds: 250 - polls,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
      frame_revision: polls === 1 ? "frame-2" : "frame-3",
    });
  });
  await elements["#scan-start"].emit("click");
  assert(images[0].src.includes("v=frame-1"), "initial revision was not used");

  takeTimer(timers, 1000)();
  await flush();
  takeTimer(timers, 1000)();
  await flush();
  assert(images.length === 1, "queued revisions created parallel image requests");

  loadImage(images, 0);
  assert(
    [...timers.values()].filter((timer) => timer.delay === 0).length === 1,
    "queued revision did not schedule a follow-up refresh",
  );
  takeTimer(timers, 0)();
  assert(images.length === 2, "queued revision did not request a follow-up image");
  assert(images[1].src.includes("v=frame-3"), "follow-up lost the newest revision");
  loadImage(images, 1);
  takeTimer(timers, 1000)();
  await flush();
  assert(images.length === 2, "latest queued revision was not retained after load");
}

async function liveViewCoalescesQueuedRefreshes() {
  const {elements, images, timers} = createEnvironment(async (url) => response(
    url === "/accounts/scan" ? 201 : 200,
    {
      id: "scan-coalesced",
      status: "awaiting_scan",
      remaining_seconds: 250,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
    },
  ));
  await elements["#scan-start"].emit("click");
  assert(images.length === 1, "initial offscreen frame was not requested");
  for (let index = 0; index < 3; index += 1) {
    takeTimer(timers, 1000)();
    await flush();
  }
  assert(images.length === 1, "refreshes created parallel image requests");
  assert(
    [...timers.values()].filter((timer) => timer.delay === 0).length === 0,
    "queued refresh ran before the pending image completed",
  );
  loadImage(images, 0);
  assert(
    [...timers.values()].filter((timer) => timer.delay === 0).length === 1,
    "pending refreshes were not coalesced into one follow-up",
  );
  takeTimer(timers, 0)();
  assert(images.length === 2, "coalesced follow-up did not request one fresh frame");
  assert(elements["#scan-qr"] === images[0], "follow-up replaced the visible frame before loading");
}

async function liveViewFailureKeepsOldFrame() {
  const {elements, images, timers} = createEnvironment(async (url) => response(
    url === "/accounts/scan" ? 201 : 200,
    {
      id: "scan-frame-failure",
      status: "awaiting_scan",
      remaining_seconds: 250,
      error: null,
      message: "请使用抖音 App 扫码",
      account_id: null,
    },
  ));
  await elements["#scan-start"].emit("click");
  const visibleImage = loadImage(images, 0);
  const visibleSource = visibleImage.src;
  takeTimer(timers, 1000)();
  await flush();
  assert(images.length === 2, "refresh frame was not requested");
  failImage(images, 1);
  assert(elements["#scan-qr"] === visibleImage, "failed refresh replaced the old frame");
  assert(elements["#scan-qr"].src === visibleSource, "failed refresh cleared the old frame source");
}

async function confirmingKeepsCloudPageVisible() {
  const {elements, images, timers} = createEnvironment(async (url) => response(
    url === "/accounts/scan" ? 201 : 200,
    {
      id: "scan-confirming",
      status: url === "/accounts/scan" ? "awaiting_scan" : "confirming",
      remaining_seconds: 250,
      error: url === "/accounts/scan" ? null : "needs_app_verification",
      needs_verification: url !== "/accounts/scan",
      message: "请继续完成验证",
      account_id: null,
    },
  ));
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  takeTimer(timers, 1000)();
  await flush();
  loadImage(images, 1);
  assert(!elements["#scan-qr-panel"].hidden, "confirming state hid full cloud page");
  assert(elements["#scan-qr"].src.includes("/accounts/scan/scan-confirming/qr"), "live page stopped refreshing");
  assert(!elements["#scan-text"].disabled, "numeric input was gated by structured verification state");
  assert(elements["#scan-browser-hint"].textContent.includes("云端页面"), "verification guidance missing");
}

async function browserClick() {
  const calls = [];
  const {elements, images} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-click",
      status: "awaiting_scan",
      remaining_seconds: 300,
      error: null,
      message: "请扫码",
      account_id: null,
      frame_revision: "frame-click",
    });
    return response(202, {accepted: true});
  });
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  assert(elements["#scan-qr"].listeners.has("click"), "cloud page has no click forwarding listener");
  await elements["#scan-qr"].emit("click", {clientX: 210, clientY: 245});
  const call = calls.find((entry) => entry.url.includes("/interact"));
  assert(call, "click did not reach interaction endpoint");
  const body = String(call.options.body);
  assert(body.includes("kind=click"), "click action kind missing");
  assert(body.includes("x=0.25"), "x coordinate was not normalized");
  assert(body.includes("y=0.5"), "y coordinate was not normalized");
  assert(body.includes("csrf_token=csrf-fixture"), "click omitted CSRF");
  assert(elements["#scan-text"].focused, "cloud click did not activate direct keyboard input");
  assert(
    elements["#scan-browser-hint"].textContent.includes("直接键入"),
    "direct keyboard guidance was not shown",
  );
}

async function browserText() {
  const calls = [];
  const {elements, timers} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-text",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    return response(202, {
      accepted: true,
      interaction_id: `interaction-${calls.length}`,
      status: "delivered",
    });
  });
  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "123456";
  await elements["#scan-type"].emit("click");
  let interactions = calls.filter((entry) => entry.url.includes("/interact"));
  assert(interactions.length === 1, "code was not submitted once");
  const firstParams = new URLSearchParams(String(interactions[0].options.body));
  assert(firstParams.get("kind") === "text", "text kind missing");
  assert(firstParams.get("text") === "123456", "code missing");
  assert(firstParams.get("request_id"), "text request id missing");
  assert(elements["#scan-text"].value === "", "successful input was not cleared");
  assert(elements["#scan-type"].disabled, "settle lock was not applied after delivery");
  takeTimer(timers, 1500)();
  elements["#scan-text"].value = "654321";
  await elements["#scan-text"].emit("keydown", {key: "Enter"});
  await flush();
  interactions = calls.filter((entry) => entry.url.includes("/interact"));
  assert(interactions.length === 2, "Enter did not submit code");
  const secondParams = new URLSearchParams(String(interactions[1].options.body));
  assert(secondParams.get("request_id"), "second request id missing");
  assert(
    secondParams.get("request_id") !== firstParams.get("request_id"),
    "a new explicit submission reused the previous request id",
  );
}

async function browserPassword() {
  const calls = [];
  const {elements, images} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-password",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
      frame_revision: "frame-password",
    });
    return response(202, {accepted: true});
  });
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  const password = ` 密码-P@ss-${"x".repeat(180)} `;
  elements["#scan-text"].value = password;
  await elements["#scan-type"].emit("click");
  const interaction = calls.find((entry) => entry.url.includes("/interact"));
  assert(interaction, "password was not submitted");
  const params = new URLSearchParams(String(interaction.options.body));
  assert(params.get("kind") === "text", "password action kind missing");
  assert(params.get("text") === password, "password was changed before forwarding");
  assert(params.get("request_id"), "password request id missing");
  assert(elements["#scan-text"].value === "", "successful password input was not cleared");
}

async function browserTextFailure() {
  const {elements, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-text-failure",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    return response(503, {error: "unavailable", message: "验证码提交失败，请重试"});
  });
  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "123456";
  await elements["#scan-type"].emit("click");
  assert(elements["#scan-text"].value === "123456", "failed input was erased");
  assert(elements["#scan-text"].disabled, "failed input was not briefly locked");
  takeTimer(timers, 1500)();
  assert(!elements["#scan-text"].disabled, "failed input stayed locked after cooldown");
  assert(elements["#scan-text"].focused, "failed input did not regain focus");
}

async function browserTextUsesOneSharedLock() {
  const firstPost = deferred();
  const calls = [];
  let interactionPosts = 0;
  let receiptPolls = 0;
  const statusUrl = "/accounts/scan/scan-text-lock/interactions/17";
  const {elements, timers} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-text-lock",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    if (url === statusUrl) {
      receiptPolls += 1;
      return response(200, {
        interaction_id: 17,
        delivery_status: receiptPolls === 1 ? "claimed" : "delivered",
        session_text_attempt_number: 1,
      });
    }
    if (url.includes("/interact")) {
      interactionPosts += 1;
      if (interactionPosts === 1) return firstPost.promise;
      return response(202, {
        accepted: true,
        interaction_id: 17 + interactionPosts,
        delivery_status: "delivered",
        session_text_attempt_number: interactionPosts,
      });
    }
    throw new Error(`unexpected URL: ${url}`);
  });

  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "first-secret";
  const firstTask = elements["#scan-type"].emit("click");
  await flush();
  await elements["#scan-text"].emit("keydown", {key: "Enter"});
  await elements["#scan-type"].emit("click");
  assert(interactionPosts === 1, "button and Enter bypassed the shared pending lock");
  const firstCall = calls.find((entry) => entry.url.includes("/interact"));
  const firstRequestId = new URLSearchParams(String(firstCall.options.body)).get("request_id");
  assert(firstRequestId, "locked submission omitted request id");

  firstPost.resolve(response(202, {
    accepted: true,
    interaction_id: 17,
    delivery_status: "queued",
    session_text_attempt_number: 1,
  }));
  await firstTask;
  await flush();
  assert(elements["#scan-type"].disabled, "queued interaction did not keep controls locked");
  assert(
    elements["#scan-interaction-status"].textContent.includes("已排队"),
    "queued receipt feedback missing",
  );
  await elements["#scan-type"].emit("click");
  await elements["#scan-text"].emit("keydown", {key: "Enter"});
  assert(interactionPosts === 1, "queued interaction was submitted again");

  takeTimer(timers, 750)();
  await flush();
  assert(
    elements["#scan-interaction-status"].textContent.includes("正在由云端处理"),
    "processing receipt feedback missing",
  );
  assert(elements["#scan-type"].disabled, "processing interaction unlocked controls");
  takeTimer(timers, 750)();
  await flush();
  assert(
    elements["#scan-interaction-status"].textContent.includes("已送达一次"),
    "delivered receipt feedback missing",
  );
  takeTimer(timers, 1500)();
  elements["#scan-text"].value = "second-secret";
  await elements["#scan-text"].emit("keydown", {key: "Enter"});
  await flush();
  assert(interactionPosts === 2, "new explicit text candidate was not accepted after final receipt");
  const interactionCalls = calls.filter((entry) => entry.url.includes("/interact"));
  const secondRequestId = new URLSearchParams(String(interactionCalls[1].options.body)).get("request_id");
  assert(secondRequestId !== firstRequestId, "new explicit candidate reused the old request id");
}

async function legacyReceiptBecomesExplicitUncertainRetry() {
  let interactionPosts = 0;
  const calls = [];
  const {confirmations, elements, timers} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-legacy-receipt",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    interactionPosts += 1;
    return response(202, interactionPosts === 1
      ? {accepted: true}
      : {accepted: true, interaction_id: "second", status: "delivered"});
  }, {
    confirmResult: (_message, count) => count > 1,
  });

  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "candidate-one";
  await elements["#scan-type"].emit("click");
  assert(interactionPosts === 1, "legacy submission was not sent exactly once");
  assert(
    elements["#scan-interaction-status"].textContent.includes("等待云端回执"),
    "legacy response was incorrectly presented as delivered",
  );
  assert(
    !elements["#scan-interaction-status"].textContent.includes("已输入"),
    "legacy response falsely claimed that text was entered",
  );
  takeTimer(timers, 6000)();
  assert(
    elements["#scan-interaction-status"].textContent.includes("结果无法确认"),
    "missing receipt did not become uncertain",
  );
  takeTimer(timers, 1500)();
  elements["#scan-text"].value = "candidate-two";
  await elements["#scan-type"].emit("click");
  assert(interactionPosts === 1, "cancelled uncertain confirmation still sent text");
  assert(confirmations.length === 1, "uncertain retry did not require confirmation");
  await elements["#scan-type"].emit("click");
  assert(interactionPosts === 2, "confirmed uncertain retry was not sent");
  assert(confirmations.length === 2, "confirmed retry did not use the explicit confirmation gate");
  assert(confirmations[1].includes("上一次文字输入结果无法确认"), "uncertain warning was unclear");
  const requestIds = calls
    .filter((entry) => entry.url.includes("/interact"))
    .map((entry) => new URLSearchParams(String(entry.options.body)).get("request_id"));
  assert(requestIds[0] !== requestIds[1], "explicit uncertain retry reused the previous request id");
}

async function thirdTextAttemptShowsStrongWarning() {
  let interactionPosts = 0;
  const {confirmations, elements, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-three-attempts",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    interactionPosts += 1;
    return response(202, {
      accepted: true,
      interaction_id: `interaction-${interactionPosts}`,
      status: "delivered",
    });
  });

  await elements["#scan-start"].emit("click");
  for (const value of ["one", "two"]) {
    elements["#scan-text"].value = value;
    await elements["#scan-type"].emit("click");
    takeTimer(timers, 1500)();
  }
  assert(confirmations.length === 0, "first two final attempts unexpectedly required a warning");
  elements["#scan-text"].value = "three";
  await elements["#scan-type"].emit("click");
  assert(interactionPosts === 3, "third confirmed candidate was not submitted");
  assert(confirmations.length === 1, "third attempt did not require a strong warning");
  assert(confirmations[0].includes("第 3 次文字尝试"), "third-attempt warning omitted the attempt count");
  assert(confirmations[0].includes("安全冷却"), "third-attempt warning omitted the cooldown risk");
}

async function wrongPasswordCanBeFollowedBySecondCandidate() {
  const calls = [];
  let posts = 0;
  const {confirmations, elements, timers} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    if (url === "/accounts/scan") return response(201, {
      id: "scan-password-retry",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "请输入登录密码",
      account_id: null,
    });
    if (url.includes("/interactions/")) {
      const interactionId = Number(url.split("/").pop());
      return response(200, {
        interaction_id: interactionId,
        delivery_status: "delivered",
        session_text_attempt_number: interactionId === 51 ? 1 : 2,
      });
    }
    posts += 1;
    return response(202, {
      accepted: true,
      interaction_id: posts === 1 ? 51 : 52,
      delivery_status: "queued",
      session_text_attempt_number: posts,
    });
  });

  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "wrong-password";
  await elements["#scan-type"].emit("click");
  takeTimer(timers, 750)();
  await flush();
  takeTimer(timers, 1500)();
  assert(!elements["#scan-text"].disabled, "first final receipt did not allow another password");
  elements["#scan-text"].value = "second-password-candidate";
  await elements["#scan-type"].emit("click");
  assert(posts === 2, "second password candidate was not submitted exactly once");
  assert(confirmations.length === 0, "normal second password candidate required an unnecessary warning");
  const requests = calls.filter((entry) => entry.url.endsWith("/interact"));
  const first = new URLSearchParams(String(requests[0].options.body));
  const second = new URLSearchParams(String(requests[1].options.body));
  assert(first.get("text") === "wrong-password", "first password candidate changed");
  assert(second.get("text") === "second-password-candidate", "second password candidate changed");
  assert(first.get("request_id") !== second.get("request_id"), "second password reused first request id");
}

async function inProgressConflictResumesOriginalReceipt() {
  const statusUrl = "/accounts/scan/scan-conflict/interactions/41";
  let posts = 0;
  const {elements, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-conflict",
      status: "confirming",
      remaining_seconds: 300,
      error: null,
      message: "继续验证",
      account_id: null,
    });
    if (url === statusUrl) {
      return response(200, {
        interaction_id: 41,
        delivery_status: "delivered",
        session_text_attempt_number: 1,
      });
    }
    posts += 1;
    return response(409, {
      error: "interaction_in_progress",
      message: "上一条仍在处理",
      interaction_id: 41,
      session_text_attempt_number: 1,
      delivery_status: "claimed",
    });
  });

  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "new-candidate";
  await elements["#scan-type"].emit("click");
  assert(posts === 1, "conflicting candidate made multiple POST requests");
  assert(elements["#scan-text"].value === "new-candidate", "unaccepted candidate was erased");
  assert(elements["#scan-type"].disabled, "resumed original interaction did not lock controls");
  assert(
    elements["#scan-interaction-status"].textContent.includes("没有再次发送"),
    "in-progress conflict did not explain that no duplicate was sent",
  );
  takeTimer(timers, 750)();
  await flush();
  assert(
    elements["#scan-interaction-status"].textContent.includes("已送达一次"),
    "resumed interaction did not reach its final receipt",
  );
  takeTimer(timers, 1500)();
  assert(!elements["#scan-text"].disabled, "final original receipt did not unlock the next candidate");
  assert(elements["#scan-text"].value === "new-candidate", "next candidate changed while waiting");
}

function scriptInitializesOnlyOnce() {
  const {elements, evaluateScript, windowObject} = createEnvironment(async () => response(500, {}));
  const startAdds = elements["#scan-start"].listenerAddCount.get("click");
  const textAdds = elements["#scan-type"].listenerAddCount.get("click");
  evaluateScript();
  assert(windowObject.__douyinSparkAccountScanInitialized === true, "global init guard missing");
  assert(
    elements["#scan-start"].listenerAddCount.get("click") === startAdds,
    "second script execution rebound the scan launcher",
  );
  assert(
    elements["#scan-type"].listenerAddCount.get("click") === textAdds,
    "second script execution rebound the text submit button",
  );
}

async function invalidText() {
  let interactions = 0;
  const {elements} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-invalid",
      status: "awaiting_scan",
      remaining_seconds: 300,
      error: null,
      message: "请扫码",
      account_id: null,
    });
    interactions += 1;
    return response(202, {accepted: true});
  });
  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "a".repeat(257);
  await elements["#scan-type"].emit("click");
  assert(interactions === 0, "oversized input reached server");
  assert(elements["#scan-text"].value.length === 257, "invalid input was cleared");
  assert(elements["#scan-text"].focused, "invalid input did not regain focus");
}

async function pollNetworkRetry() {
  let polls = 0;
  const {elements, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-network",
      status: "awaiting_scan",
      remaining_seconds: 300,
      error: null,
      message: "请扫码",
      account_id: null,
    });
    polls += 1;
    if (polls === 1) throw new Error("temporary");
    return response(200, {
      id: "scan-network",
      status: "confirming",
      remaining_seconds: 299,
      error: null,
      message: "已扫码，请在手机确认",
      account_id: null,
    });
  });
  await elements["#scan-start"].emit("click");
  takeTimer(timers, 1000)();
  await flush();
  assert(elements["#scan-result"].hidden, "temporary network error became terminal");
  assert(elements["#scan-status"].textContent.includes("自动重试"), "retry status missing");
  takeTimer(timers, 1000)();
  await flush();
  assert(polls === 2, "polling did not recover");
  assert(!elements["#scan-qr-panel"].hidden, "recovery did not keep cloud page visible");
}

async function terminalClearsSensitiveState() {
  const {elements, images, timers} = createEnvironment(async (url) => {
    if (url === "/accounts/scan") return response(201, {
      id: "scan-terminal",
      status: "confirming",
      remaining_seconds: 100,
      error: null,
      message: "验证中",
      account_id: null,
    });
    return response(200, {
      id: "scan-terminal",
      status: "failed",
      remaining_seconds: 0,
      error: "login_timeout",
      message: "扫码已超时，请重试",
      account_id: null,
    });
  });
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  elements["#scan-text"].value = "123456";
  takeLastTimer(timers, 1000)();
  await flush();
  assert(!elements["#scan-result"].hidden, "terminal result was not shown");
  assert(elements["#scan-qr"].src === "", "terminal state kept live screenshot URL");
  assert(elements["#scan-text"].value === "", "terminal state kept the verification code");
  assert(elements["#scan-text"].disabled, "terminal state left verification input enabled");
}

async function newSessionClearsVerificationCode() {
  let starts = 0;
  const {elements} = createEnvironment(async (url) => {
    assert(url === "/accounts/scan", "new session issued an unexpected request");
    starts += 1;
    if (starts === 1) return response(201, {
      id: "scan-old-session",
      status: "failed",
      remaining_seconds: 0,
      error: "login_timeout",
      message: "扫码已超时，请重试",
      account_id: null,
    });
    return response(201, {
      id: "scan-new-session",
      status: "queued",
      remaining_seconds: 300,
      error: null,
      message: "等待云端通道",
      account_id: null,
    });
  });
  await elements["#scan-start"].emit("click");
  elements["#scan-text"].value = "123456";
  await elements["#scan-start"].emit("click");
  assert(starts === 2, "new scan session was not created");
  assert(elements["#scan-text"].value === "", "new session kept the previous verification code");
}

async function pagehideCancel() {
  const {beacons, elements, images, windowListeners} = createEnvironment(async () => response(201, {
    id: "scan-leaving",
    status: "queued",
    remaining_seconds: 300,
    error: null,
    message: "等待云端通道",
    account_id: null,
  }));
  await elements["#scan-start"].emit("click");
  loadImage(images, 0);
  elements["#scan-text"].value = "123456";
  windowListeners.get("pagehide")();
  assert(beacons.length === 1, "page exit did not send cancellation beacon");
  assert(beacons[0].url === "/accounts/scan/scan-leaving/cancel", "wrong scan cancelled");
  assert(beacons[0].body.includes("csrf_token=csrf-fixture"), "beacon omitted CSRF");
  assert(elements["#scan-text"].value === "", "page exit kept the verification code");
  assert(elements["#scan-qr"].src === "", "page exit kept the live screenshot URL");
}

async function successClose() {
  const {elements, reloadCount, timers} = createEnvironment(async () => response(201, {
    id: "scan-success",
    status: "succeeded",
    remaining_seconds: 0,
    error: null,
    message: "绑定成功",
    account_id: null,
  }));
  await elements["#scan-start"].emit("click");
  assert(elements["#scan-status"].textContent === "登录成功，正在刷新账号列表", "success copy missing");
  const scheduled = [...timers.values()];
  assert(scheduled.length === 1, "success close was not scheduled");
  scheduled[0].callback();
  assert(elements["#scan-dialog"].closeCount === 1, "success did not close dialog");
  assert(reloadCount() === 1, "success did not refresh account list");
}

async function noCodeControls() {
  const {elements} = createEnvironment(async () => response(201, {
    id: "scan-no-code",
    status: "queued",
    remaining_seconds: 300,
    error: null,
    message: "等待云端通道",
    account_id: null,
  }), {codeControls: false});
  await elements["#scan-start"].emit("click");
  assert(elements["#scan-dialog"].open, "scan failed without optional code controls");
}

async function reloginExistingAccount() {
  const calls = [];
  const {elements, reloginButtons} = createEnvironment(async (url, options = {}) => {
    calls.push({url, options});
    return response(201, {
      id: "scan-relogin",
      status: "queued",
      remaining_seconds: 300,
      error: null,
      message: "等待云端通道",
      account_id: "account-existing",
    });
  }, {
    newAccountControl: false,
    reloginAccounts: [{id: "account-existing", name: "旧主账号"}],
  });

  assert(!elements["#scan-start"], "quota-full fixture unexpectedly exposed new-account control");
  await reloginButtons[0].emit("click");
  assert(calls.length === 1 && calls[0].url === "/accounts/scan", "relogin did not start one scan");
  const body = new URLSearchParams(String(calls[0].options.body));
  assert(body.get("csrf_token") === "csrf-fixture", "relogin omitted CSRF");
  assert(body.get("account_id") === "account-existing", "relogin omitted target account id");
  assert(elements["#scan-dialog"].open, "relogin did not open the shared dialog");
  assert(
    elements["#scan-dialog-title"].textContent === "重新登录“旧主账号”",
    "shared dialog did not switch to relogin copy",
  );
  assert(elements["#scan-cancel"].textContent === "取消重新登录", "relogin cancel copy missing");
}

async function responseTargetMismatch() {
  let calls = 0;
  const {elements, images, reloginButtons, timers} = createEnvironment(async () => {
    calls += 1;
    return response(200, {
      id: "scan-wrong-target",
      status: "queued",
      remaining_seconds: 300,
      error: null,
      message: "等待云端通道",
      account_id: "account-other",
    });
  }, {
    newAccountControl: false,
    reloginAccounts: [{id: "account-expected", name: "目标账号"}],
  });

  await reloginButtons[0].emit("click");

  assert(calls === 1, "target mismatch unexpectedly made another request");
  assert(images.length === 0, "target mismatch adopted the returned scan view");
  assert(timers.size === 0, "target mismatch started polling the returned scan");
  assert(
    elements["#scan-status"].textContent === "扫码会话目标不一致，请关闭其他登录窗口后重试",
    "target mismatch did not show a clear error",
  );
  assert(!reloginButtons[0].disabled, "target mismatch did not restore the relogin button");
  assert(reloginButtons[0].textContent === "重新登录", "target mismatch kept a busy label");
  assert(elements["#scan-result"].hidden === false, "target mismatch did not show the error state");
  assert(elements["#scan-qr-panel"].hidden === true, "target mismatch kept the browser panel visible");
}

function templateHasNoLegacyPlaceholder() {
  const template = fs.readFileSync(
    path.resolve(__dirname, "../../spark_console/templates/accounts.html"),
    "utf8",
  );
  assert(!/id=["']scan-placeholder["']/.test(template), "legacy scan placeholder remains in template");
  assert(!/id=["']scan-verification["']/.test(template), "legacy verification panel remains in template");
  assert(!/id=["']scan-resend["']/.test(template), "legacy resend control remains in template");
  assert((template.match(/id=["']scan-dialog["']/g) || []).length === 1, "scan dialog was duplicated");
  assert(/data-scan-account-id=/.test(template), "account relogin launcher is missing");
}

async function main() {
  const scenario = process.argv[2];
  if (scenario === "pending-close") await pendingIntent("#scan-close");
  else if (scenario === "pending-cancel") await pendingIntent("#scan-cancel");
  else if (scenario === "active-cancel-failure") await activeCancelFailure();
  else if (scenario === "pending-start-failure") await pendingStartFailure();
  else if (scenario === "live-view-refresh") await liveViewSkipsUnchangedRevision();
  else if (scenario === "live-view-revision-change") await liveViewRefreshesWhenRevisionChanges();
  else if (scenario === "live-view-queued-revision") await liveViewKeepsLatestQueuedRevision();
  else if (scenario === "live-view-coalesces") await liveViewCoalescesQueuedRefreshes();
  else if (scenario === "live-view-failure-keeps-old") await liveViewFailureKeepsOldFrame();
  else if (scenario === "confirming-keeps-live-page") await confirmingKeepsCloudPageVisible();
  else if (scenario === "browser-click") await browserClick();
  else if (scenario === "browser-text") await browserText();
  else if (scenario === "browser-password") await browserPassword();
  else if (scenario === "browser-text-failure") await browserTextFailure();
  else if (scenario === "browser-text-shared-lock") await browserTextUsesOneSharedLock();
  else if (scenario === "legacy-receipt-uncertain") await legacyReceiptBecomesExplicitUncertainRetry();
  else if (scenario === "third-text-attempt-warning") await thirdTextAttemptShowsStrongWarning();
  else if (scenario === "wrong-password-second-candidate") await wrongPasswordCanBeFollowedBySecondCandidate();
  else if (scenario === "interaction-in-progress") await inProgressConflictResumesOriginalReceipt();
  else if (scenario === "single-initialization") scriptInitializesOnlyOnce();
  else if (scenario === "invalid-text") await invalidText();
  else if (scenario === "poll-network-retry") await pollNetworkRetry();
  else if (scenario === "terminal-clears") await terminalClearsSensitiveState();
  else if (scenario === "new-session-clears-code") await newSessionClearsVerificationCode();
  else if (scenario === "pagehide-cancel") await pagehideCancel();
  else if (scenario === "success-close") await successClose();
  else if (scenario === "no-code-controls") await noCodeControls();
  else if (scenario === "relogin-account") await reloginExistingAccount();
  else if (scenario === "response-target-mismatch") await responseTargetMismatch();
  else if (scenario === "template-no-placeholder") templateHasNoLegacyPlaceholder();
  else throw new Error(`unknown scenario: ${scenario}`);
  process.stdout.write(JSON.stringify({scenario, ok: true}));
}

main().catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
