const assert = require("assert");
const { chartData } = require("../../spark_console/static/ops_history.js");
const data = chartData({ start: 100, end: 115, bucket_seconds: 5,
  points: [{ at: 100, cpu: 90, memory: 40, logins: 1, sends: 0 },
           { at: 110, cpu: null, memory: 45, logins: 0, sends: 1 }] });
assert.deepStrictEqual(data[0], [100, 105, 110, 115]);
assert.deepStrictEqual(data[1], [90, null, null, null]);
assert.deepStrictEqual(data[2], [40, null, 45, null]);
assert.deepStrictEqual(data[3], [1, null, 0, null]);
assert.deepStrictEqual(data[4], [0, null, 1, null]);
assert.throws(() => chartData({ start: 0, end: 20, bucket_seconds: 0, points: [] }));
process.stdout.write("History chart gap and activity alignment checks passed\n");
