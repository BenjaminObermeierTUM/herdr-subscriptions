importScripts("usage.js");

const retryAlarm = "reconnect-herdr";
let port;

async function inventory(workspace) {
  if (!/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(workspace)) return {available: null, reason: "workspace unavailable"};
  const url = new URL(`/api/organizations/${workspace}/usage`, "https://claude.ai");
  url.search = "at_wall=1&skip_spend=1";
  const response = await fetch(url, {
    credentials: "include", cache: "no-store", redirect: "error",
    headers: {Accept: "application/json"}, signal: AbortSignal.timeout(12000)
  });
  if (response.status === 429) {
    const retry = response.headers.get("Retry-After");
    const until = /^\d+$/.test(retry || "") ? Date.now() / 1000 + Number(retry) : Date.parse(retry) / 1000;
    return {available: null, reason: "rate limited", retry_after: Number.isFinite(until) ? until : 0};
  }
  if (!response.ok) return {available: null, reason: [401, 403].includes(response.status)
    ? "check Chrome sign-in and site access" : "provider request failed"};
  const sample = readInventory(await response.json());
  if (sample.available === null) sample.reason = "inventory unavailable from provider";
  return sample;
}

function connect() {
  if (port) return;
  const connection = chrome.runtime.connectNative("dev.herdr.subscriptions");
  port = connection;
  connection.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    port = null;
    chrome.alarms.create(retryAlarm, {delayInMinutes: 1});
  });
  connection.onMessage.addListener(async ({workspace}) => {
    const result = await inventory(workspace).catch(() => ({available: null, reason: "provider request failed"}));
    if (port === connection) connection.postMessage(result);
  });
  connection.postMessage({request: "watch"});
}

chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === retryAlarm) connect(); });
chrome.runtime.onInstalled.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
connect();
