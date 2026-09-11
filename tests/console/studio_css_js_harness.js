const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");
const css = require(path.join(root, ".artifacts/ui-check/node_modules/css-tree"));
const source = fs.readFileSync(path.join(root, "spark_console/static/console_studio.css"), "utf8");
const ast = css.parse(source, {positions: true});
const errors = [];
const palettes = [];
css.walk(ast, {
  visit: "Declaration",
  enter(node) {
    if (node.property.startsWith("--") || this.atrule?.name === "font-face") return;
    const value = css.generate(node.value);
    const result = css.lexer.matchProperty(node.property, node.value);
    if (result.error && !value.includes("var(")) errors.push(`${node.loc.start.line}: ${node.property}: ${value}`);
    if (node.property === "font-size") assert(!/[0-9](vw|vh|vmin|vmax)/.test(value));
  },
});
assert.deepEqual(errors, [], "Invalid CSS declarations");
css.walk(ast, {
  visit: "Rule",
  enter(node) {
    const colors = {};
    node.block.children.forEach(declaration => {
      if (declaration.type === "Declaration" && declaration.property.startsWith("--studio-")) {
        colors[declaration.property] = css.generate(declaration.value);
      }
    });
    if (colors["--studio-ink"] && colors["--studio-sheet"]) palettes.push(colors);
  },
});
assert.equal(palettes.length, 2);
assert(source.includes("--studio-canvas: #f6f6f7"));
assert(source.includes("--studio-canvas: #17181a"));
assert(source.includes("prefers-reduced-motion"));
assert(source.includes("env(safe-area-inset-bottom)"));
assert(source.includes('[hidden] { display: none !important; }'));
assert(!source.includes('.studio-metric:first-child { padding-left: 0; }'));
assert(!source.includes('.studio-metric:nth-child(odd) { padding-left: 0;'));
assert(source.includes('@container account-list (min-width: 900px)'));
assert(source.includes('@container account-list (max-width: 520px)'));
assert(source.includes('.account-item:hover { box-shadow: none;'));
process.stdout.write(JSON.stringify({ok: true, themes: palettes.length}));
