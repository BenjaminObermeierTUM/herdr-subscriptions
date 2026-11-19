const {test} = require("node:test");
const assert = require("node:assert/strict");
const {readInventory} = require("../browser/usage");

test("only an explicit empty inventory or no-grant status reports zero", () => {
  for (const data of [{}, {cedar_ember: null}, {cedar_ember: {eligible: true}},
                       {cedar_ember: {eligible: false, ineligible_reason: "unavailable"}}]) {
    assert.equal(readInventory(data).available, null);
  }
  assert.equal(readInventory({cedar_ember: {eligible: true, grants: []}}).available, 0);
  assert.equal(readInventory({cedar_ember: {eligible: false, ineligible_reason: "no_grant"}}).available, 0);
});

test("typed coverage, multiple uses, paused cards and dates are independent", () => {
  const full = {id: "private", resets_left: 2, clears: ["five_hour", "seven_day"], usable_now: true};
  const fable = {resets_left: 1, label: "Fable reset", type: "full", gives_back_weekly: true,
                 clears: ["five_hour", "seven_day_overage_included"], paused: true};
  const grants = [full, fable, {...full, ends_at: "2020-01-01"}, {...full, starts_at: "2040-01-01"}];
  const result = readInventory({cedar_ember: {eligible: true, grants, exhausted: ["seven_day"]}}, Date.UTC(2030, 0));
  assert.deepEqual(result, {available: 3, offers: ["full", "full", "fable"], usable: ["full"],
                            expirations: [], expiring: [], exhausted: ["weekly"]});
  assert.equal(readInventory({cedar_ember: {eligible: true, grants: [{...full, resets_left: "2"}]}}).available, null);
});

test("independently offered five-hour reset covers the session", () => {
  const result = readInventory({cedar_ember: {eligible: false, ineligible_reason: "no_grant"},
    juniper_tide: {eligible: true, available: true}, five_hour: {utilization: 100}});
  assert.deepEqual(result, {available: 1, offers: ["five_hour"], usable: ["five_hour"],
                            expirations: [], expiring: [], exhausted: ["session"]});
});
