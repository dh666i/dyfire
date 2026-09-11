const path = require("path");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const helpers = require(path.resolve(__dirname, "../../spark_console/static/tasks.js"));

assert(
  helpers.targetIdentityKey({name: "旧昵称", sec_uid: "stable-user"}) ===
    helpers.targetIdentityKey({name: "新昵称", sec_uid: "stable-user"}),
  "a renamed stable identity must keep one selection key",
);
assert(
  helpers.targetIdentityKey({name: "Friend", sec_uid: ""}) ===
    helpers.targetIdentityKey({name: "friend", sec_uid: ""}),
  "fallback nickname keys must be case-insensitive",
);

const deduped = helpers.dedupeTargetItems([
  {name: "旧昵称", sec_uid: "stable-user"},
  {name: "新昵称", sec_uid: "stable-user"},
  {name: "FRIEND", sec_uid: ""},
  {name: "friend", sec_uid: ""},
]);
assert(deduped.items.length === 2, "stable ids and folded names were not deduplicated");
assert(deduped.items[0].name === "新昵称", "the newest stable-identity display name was not retained");

const overlong = "好".repeat(helpers.DEFAULT_TARGET_NAME_MAX + 1);
const limited = helpers.dedupeTargetItems([
  {name: "可用", sec_uid: "ok"},
  {name: overlong, sec_uid: "too-long"},
]);
assert(
  limited.items.length === 1 && limited.rejected.length === 1,
  "overlong contacts must not enter a batch submission",
);

const entries = helpers.targetSubmissionEntries([
  {name: "好友甲", sec_uid: "stable-a"},
  {name: "好友乙", sec_uid: ""},
]);
assert(
  JSON.stringify(entries) === JSON.stringify([
    ["target_name", "好友甲"],
    ["target_sec_uid", "stable-a"],
    ["target_name", "好友乙"],
    ["target_sec_uid", ""],
  ]),
  "target names and stable ids must keep aligned submission order",
);

const avatarUrl = "https://p3.douyinpic.com/avatar.jpeg";
const withAvatar = {name: "Friend", sec_uid: "stable", avatar_url: avatarUrl};
assert(helpers.normalizeTargetItem(withAvatar).avatar_url === avatarUrl, "avatar lost in normalization");
assert(helpers.dedupeTargetItems([withAvatar]).items[0].avatar_url === avatarUrl, "avatar lost in deduplication");
assert(helpers.targetIdentityKey(withAvatar) === helpers.targetIdentityKey({...withAvatar, avatar_url: ""}), "avatar must not change identity");
assert(helpers.targetSubmissionEntries([withAvatar]).length === 2, "avatar must not be submitted as a task field");
for (const unsafe of ["javascript:alert(1)", "http://p3.douyinpic.com/a", "https://douyinpic.com.evil.test/a", "https://user@p3.douyinpic.com/a", "https://p3.douyinpic.com:8080/a", "https://127.0.0.1/a"]) {
  assert(helpers.safeAvatarUrl(unsafe) === "", "unsafe avatar accepted");
  assert(!helpers.normalizeTargetItem({...withAvatar, avatar_url: unsafe}).avatar_url, "unsafe avatar retained");
}
process.stdout.write(JSON.stringify({ok: true}));
