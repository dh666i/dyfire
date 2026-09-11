const fs = require("fs");
const path = require("path");
const vm = require("vm");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class Input {
  constructor() {
    this.type = "password";
    this.selectionStart = 2;
    this.selectionEnd = 5;
    this.focused = false;
    this.selection = null;
  }

  focus() { this.focused = true; }

  setSelectionRange(start, end) { this.selection = [start, end]; }
}

class Field {
  constructor(input) { this.input = input; }

  querySelector(selector) {
    return selector === "[data-password-input]" ? this.input : null;
  }
}

class Button {
  constructor(field) {
    this.field = field;
    this.attributes = new Map();
    this.listeners = new Map();
    this.title = "";
  }

  closest(selector) {
    return selector === "[data-password-field]" ? this.field : null;
  }

  setAttribute(name, value) { this.attributes.set(name, value); }

  getAttribute(name) { return this.attributes.get(name); }

  addEventListener(name, listener) { this.listeners.set(name, listener); }

  emit(name) { return this.listeners.get(name)(); }
}

const inputs = [new Input(), new Input()];
const buttons = inputs.map((input) => new Button(new Field(input)));
global.document = {
  querySelectorAll(selector) {
    return selector === "[data-password-toggle]" ? buttons : [];
  },
};

const source = fs.readFileSync(
  path.resolve(__dirname, "../../spark_console/static/password_toggle.js"),
  "utf8",
);
vm.runInThisContext(source, {filename: "password_toggle.js"});

for (const button of buttons) {
  assert(button.getAttribute("aria-pressed") === "false", "initial pressed state is wrong");
  assert(button.getAttribute("aria-label") === "显示密码", "initial accessible label is wrong");
}

buttons[0].emit("click");
assert(inputs[0].type === "text", "first click did not reveal password");
assert(buttons[0].getAttribute("aria-pressed") === "true", "reveal state was not announced");
assert(buttons[0].getAttribute("aria-label") === "隐藏密码", "hide label was not announced");
assert(buttons[0].title === "隐藏密码", "hide tooltip was not updated");
assert(inputs[0].focused, "password input did not regain focus");
assert(String(inputs[0].selection) === "2,5", "text selection was not restored");
assert(inputs[1].type === "password", "toggling one field changed another field");

buttons[0].emit("click");
assert(inputs[0].type === "password", "second click did not hide password");
assert(buttons[0].getAttribute("aria-label") === "显示密码", "show label was not restored");

process.stdout.write(JSON.stringify({ok: true}));
