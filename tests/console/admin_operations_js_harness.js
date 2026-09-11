const path = require("path");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const helpers = require(path.resolve(
  __dirname,
  "../../spark_console/static/admin_operations.js",
));

assert(helpers.meterLevel(74.9) === "normal", "normal resource usage was escalated");
assert(helpers.meterLevel(75) === "warning", "warning threshold did not begin at 75%");
assert(helpers.meterLevel(89.9) === "warning", "warning usage did not remain warning");
assert(helpers.meterLevel(90) === "danger", "danger threshold did not begin at 90%");

assert(
  helpers.responseRequiresLogin({status: 401, redirected: false, url: ""}),
  "401 responses must expire the operations session",
);
assert(
  helpers.responseRequiresLogin({status: 403, redirected: false, url: ""}),
  "403 responses must expire the operations session",
);
assert(
  helpers.responseRequiresLogin(
    {status: 200, redirected: true, url: "https://example.test/login?next=/admin/operations"},
    "https://example.test/admin/operations",
  ),
  "a followed login redirect must expire the operations session",
);
assert(
  !helpers.responseRequiresLogin({status: 500, redirected: false, url: ""}),
  "ordinary server failures must retain the retry path",
);

process.stdout.write(JSON.stringify({ok: true}));
