// The signed-in Claude Usage response. Keep grant IDs and account metadata out of the feed.
function readInventory(data, now = Date.now()) {
  const missing = {available: null};
  const full = data?.cedar_ember;
  const session = data?.juniper_tide;
  if (!full || typeof full.eligible !== "boolean") return missing;
  // Authentication, rollout and transient errors also use ineligible responses.
  if (!full.eligible && full.ineligible_reason !== "no_grant") return missing;
  if (full.eligible && !Array.isArray(full.grants)) return missing;
  const offers = [], usable = [], expirations = [], expiring = [], exhausted = new Set();
  let available = 0;
  for (const grant of full.grants || []) {
    if (!Number.isInteger(grant?.resets_left) || grant.resets_left < 0) return missing;
    const end = grant.ends_at == null ? Infinity : Date.parse(grant.ends_at);
    const start = grant.starts_at == null ? -Infinity : Date.parse(grant.starts_at);
    if (Number.isNaN(end) || Number.isNaN(start)) return missing;
    if (!grant.resets_left || end <= now || start > now) continue;
    available += grant.resets_left;
    if (available > 100) return missing;
    const clears = Array.isArray(grant.clears) ? grant.clears : [];
    const scopes = [];
    if (clears.includes("five_hour")) scopes.push("session");
    if (clears.includes("seven_day")) scopes.push("weekly");
    if (clears.includes("seven_day_overage_included") || /\bfable\b/i.test(grant.label || "")) scopes.push("fable");
    let kind;
    if (grant.gives_back_weekly && !scopes.includes("weekly") && scopes.includes("fable")) kind = "fable";
    else if (scopes.includes("weekly") && scopes.includes("session")) kind = "full";
    else if (scopes.includes("weekly")) kind = "weekly";
    else if (scopes.length === 1 && scopes[0] === "session") kind = "five_hour";
    if (kind) {
      offers.push(...Array(grant.resets_left).fill(kind));
      if (full.eligible && grant.usable_now === true && !grant.paused) {
        usable.push(kind);
        for (const scope of scopes) {
          const key = {session: "five_hour", weekly: "seven_day", fable: "seven_day_overage_included"}[scope];
          if (full.exhausted?.includes(key) || grant.percent_used?.[key] >= 100 ||
              data[key]?.utilization >= 100) exhausted.add(scope);
        }
      }
    }
    if (Number.isFinite(end)) {
      expirations.push(new Date(end).toISOString());
      expiring.push({at: end / 1000, count: grant.resets_left, kind: kind || null,
                     usable: full.eligible && grant.usable_now === true && !grant.paused});
    }
  }
  if (session?.eligible === true) {
    if (typeof session.available !== "boolean") return missing;
    if (session.available) {
      available++;
      offers.push("five_hour");
      usable.push("five_hour");
      if (data.five_hour?.utilization >= 100) exhausted.add("session");
    }
  }
  return {available, usable, offers, expirations, expiring, exhausted: [...exhausted]};
}

if (typeof module !== "undefined") module.exports = {readInventory};
