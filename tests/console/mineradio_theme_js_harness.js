const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class TokenList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  toggle(name, force) {
    if (force === true) this.values.add(name);
    else if (force === false) this.values.delete(name);
    else if (this.values.has(name)) this.values.delete(name);
    else this.values.add(name);
  }
}

class Button {
  constructor() {
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.title = "";
  }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  emit(name) { return this.listeners.get(name)?.({}); }
}

const root = {
  dataset: {appSurface: "console", themeMode: "system", theme: "dark"},
  style: {},
  classList: new TokenList(),
};
const body = {classList: new TokenList(), dataset: {}};
const buttons = [new Button(), new Button()];
const themeColor = {
  attributes: new Map(),
  setAttribute(name, value) { this.attributes.set(name, value); },
};
const themeMedia = {
  matches: true,
  listener: null,
  addEventListener(name, listener) {
    if (name === "change") this.listener = listener;
  },
  emit() { this.listener?.({matches: this.matches}); },
};
const storage = new Map();

global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, value); },
};
global.document = {
  documentElement: root,
  body,
  querySelector(selector) {
    if (selector === "[data-theme-color]") return themeColor;
    return null;
  },
  querySelectorAll(selector) {
    if (selector === "[data-theme-toggle]") return buttons;
    return [];
  },
};
global.window = {
  matchMedia(query) {
    if (query === "(prefers-color-scheme: dark)") return themeMedia;
    return {matches: false};
  },
  requestAnimationFrame(callback) { callback(); return 1; },
  cancelAnimationFrame() {},
  setTimeout,
};

const source = fs.readFileSync(
  path.resolve(__dirname, "../../spark_console/static/mineradio_ui.js"),
  "utf8",
);
vm.runInThisContext(source, {filename: "mineradio_ui.js"});

assert(root.dataset.themeMode === "light", "legacy system preference must migrate to light");
assert(root.dataset.theme === "light", "default must not follow system dark");
assert(root.style.colorScheme === "light", "native controls must use selected theme");
assert(storage.get("spark-console-theme") === "light", "migration was not persisted");
assert(buttons.every(button => button.dataset.themeMode === "light"), "controls not synchronized");
assert(!buttons[0].attributes.get("aria-label").includes("系统"), "system option still exposed");
buttons[0].emit("click");
assert(root.dataset.themeMode === "dark", "first click must choose dark");
assert(storage.get("spark-console-theme") === "dark", "dark not persisted");
assert(buttons[1].dataset.themeMode === "dark", "other control not synchronized");
themeMedia.matches = false; themeMedia.emit();
assert(root.dataset.theme === "dark", "OS change must not affect chosen theme");
assert(themeMedia.listener === null, "OS listener must not be installed");
buttons[0].emit("click");
assert(root.dataset.themeMode === "light", "second click must choose light");
buttons[0].emit("click");
assert(root.dataset.themeMode === "dark", "third click must choose dark, never system");
process.stdout.write(JSON.stringify({ok: true}));
