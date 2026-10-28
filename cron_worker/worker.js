// Wakes the hourly GitHub run on time: GitHub's own schedule is late or skipped on some days
// (2026-10-28 it never fired). Cloudflare's cron is free and on the minute.
export default {
  async scheduled(event, env, ctx) {
    const r = await fetch("https://api.github.com/repos/JohnnGamil/naharak-salata-pipeline/actions/workflows/tick.yml/dispatches", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
                 "User-Agent": "naharak-cron", "X-GitHub-Api-Version": "2022-11-28" },
      body: JSON.stringify({ ref: "main", inputs: { budget: "40", dry: "false", source: "cron" } }),
    });
    if (r.status !== 204) console.log("dispatch failed", r.status, await r.text());
  },
};
