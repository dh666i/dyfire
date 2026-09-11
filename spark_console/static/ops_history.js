(function () {
  "use strict";
  function chartData(history) {
    const bucket = history.bucket_seconds;
    if (![5, 30, 120].includes(bucket) || !Number.isFinite(history.start) || !Number.isFinite(history.end)
      || history.end < history.start || history.end - history.start > 86400) throw new Error("invalid-history-range");
    const start = Math.floor(history.start / bucket) * bucket;
    const end = Math.floor(history.end / bucket) * bucket;
    const points = new Map(history.points.map(point => [point.at, point]));
    const data = [[], [], [], [], []];
    for (let at = start; at <= end; at += bucket) {
      const point = points.get(at);
      data[0].push(at);
      ["cpu", "memory", "logins", "sends"].forEach((key, index) => {
        data[index + 1].push(point && Number.isFinite(point[key]) ? point[key] : null);
      });
    }
    return data;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { chartData };
  if (typeof document === "undefined") return;
  const root = document.querySelector("[data-ops-history]");
  if (!root) return;
  const chart = root.querySelector("[data-history-chart]");
  const status = root.querySelector("[data-history-status]");
  const empty = root.querySelector("[data-history-empty]");
  const eventList = root.querySelector("[data-history-events]");
  let hours = 1, plot = null, latest = null, controller = null, requestId = 0;
  const formatTime = (seconds, full = false) => new Date(seconds * 1000).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai", hour12: false,
    ...(full ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit", minute: "2-digit", ...(full ? { second: "2-digit" } : {})
  });
  const percent = value => Number.isFinite(value) ? value.toFixed(1) + "%" : "—";
  const states = { succeeded: "已绑定", success: "成功", failed: "失败", expired: "已过期",
    cancelled: "已取消", running: "执行中", queued: "排队中", loading_qr: "加载中",
    awaiting_scan: "等待扫码", confirming: "确认与验证", submitted_unconfirmed: "待核实",
    retrying: "等待重试", skipped: "已跳过" };

  function renderEvents(events) {
    eventList.replaceChildren();
    if (!events.length) {
      const p = document.createElement("p"); p.textContent = "此时间范围暂无扫码或发送明细"; eventList.append(p); return;
    }
    events.forEach(event => {
      const row = document.createElement("div"); row.className = "ops-history-event";
      const name = document.createElement("strong"); name.textContent = event.kind === "login" ? "扫码绑定" : "消息发送";
      const time = document.createElement("time"); time.textContent = formatTime(event.start, true);
      const state = document.createElement("span"); state.textContent = states[event.status] || event.status;
      const duration = document.createElement("span"); duration.textContent = event.duration === null ? "耗时未知" : (event.active ? "已持续 " : "耗时 ") + event.duration + " 秒";
      const focus = document.createElement("button"); focus.type = "button"; focus.className = "admin-icon-button";
      focus.title = "定位此时段"; focus.setAttribute("aria-label", "定位" + name.textContent + "时段");
      focus.append(root.querySelector("[data-history-focus-icon]").content.cloneNode(true));
      focus.addEventListener("click", () => {
        if (!plot || !latest) return;
        const from = Math.max(latest.start, event.start - 120);
        const to = Math.min(latest.end, (event.end || event.start + 120) + 120);
        plot.setScale("x", { min: from, max: Math.max(from + 5, to) });
      });
      row.append(name, time, state, duration, focus); eventList.append(row);
    });
  }

  function renderChart(history) {
    if (typeof window.uPlot !== "function") throw new Error("chart-unavailable");
    if (plot) plot.destroy();
    const style = getComputedStyle(document.documentElement);
    const color = name => style.getPropertyValue(name).trim();
    const text = color("--mr-muted"), line = color("--mr-line");
    plot = new window.uPlot({
      width: Math.max(240, chart.clientWidth), height: 300,
      padding: [12, 6, 0, 0],
      scales: { x: {},
        y: { range: () => [0, 100] }, activity: { range: (u, min, max) => [0, Math.max(2, Math.ceil(max || 0))] } },
      axes: [
        { stroke: text, grid: { show: false }, values: (u, ticks) => ticks.map(at => formatTime(at)), space: 90 },
        { stroke: text, size: 48, grid: { stroke: line, width: 1 }, values: (u, ticks) => ticks.map(v => v + "%") },
        { scale: "activity", side: 1, stroke: text, size: 34, grid: { show: false }, incrs: [1, 2, 5, 10, 20, 50, 100] }
      ],
      series: [
        { label: "时间", value: (u, value) => value == null ? "—" : formatTime(value, true) },
        { label: "CPU 峰值", stroke: color("--mr-mint"), width: 1.7, value: (u, v) => percent(v) },
        { label: "内存峰值", stroke: color("--mr-gold"), width: 1.7, value: (u, v) => percent(v) },
        { label: "扫码数", scale: "activity", stroke: "#6398b8", width: 1.3, dash: [4, 3], value: (u, v) => v == null ? "—" : v + " 个" },
        { label: "发送数", scale: "activity", stroke: color("--mr-coral"), width: 1.3, dash: [4, 3], value: (u, v) => v == null ? "—" : v + " 个" }
      ],
      cursor: { drag: { x: true, y: false } },
      legend: { show: true },
    }, chartData(history), chart);
    plot.setScale("x", { min: history.start, max: history.end });
    chart.setAttribute("aria-label", hours + "小时资源历史，CPU 峰值 " + percent(history.cpu_peak) + "，内存峰值 " + percent(history.memory_peak));
  }

  async function refresh() {
    const id = ++requestId;
    if (controller) controller.abort();
    controller = new AbortController();
    const timeout = setTimeout(() => controller && id === requestId && controller.abort(), 10000);
    try {
      const response = await fetch("/admin/operations/history?hours=" + hours, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (response.status === 401 || response.status === 403 || (response.redirected && new URL(response.url).pathname === "/login")) {
        window.location.assign("/login"); return;
      }
      if (!response.ok) throw new Error("history-response");
      const history = await response.json();
      if (id !== requestId) return;
      latest = history;
      renderChart(history);
      renderEvents(history.events || []);
      root.querySelector("[data-history-cpu]").textContent = percent(history.cpu_peak);
      root.querySelector("[data-history-memory]").textContent = percent(history.memory_peak);
      empty.hidden = history.points.length > 0;
      status.textContent = history.state === "fresh" ? "采集正常 · 更新于 " + formatTime(history.latest_at, true)
        : history.state === "empty" ? "等待首次采集" : history.state === "partial" ? "部分资源指标不可读取" : history.state === "stale" ? "采集中断 · 已保留历史数据" : "历史数据暂不可读取";
    } catch (error) {
      if (id === requestId) status.textContent = "历史加载失败，将自动重试";
    } finally { clearTimeout(timeout); }
  }
  root.querySelectorAll("[data-history-hours]").forEach(button => button.addEventListener("click", () => {
    hours = Number(button.dataset.historyHours);
    root.querySelectorAll("[data-history-hours]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    refresh();
  }));
  if (typeof ResizeObserver === "function") new ResizeObserver(() => {
    if (plot) plot.setSize({ width: Math.max(240, chart.clientWidth), height: 300 });
  }).observe(chart);
  new MutationObserver(() => { if (latest) renderChart(latest); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  root.querySelector(".ops-history-events").addEventListener("toggle", () => {
    if (plot && latest && !root.querySelector(".ops-history-events").open) plot.setScale("x", { min: latest.start, max: latest.end });
  });
  document.querySelector("[data-ops-refresh]")?.addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden && !root.querySelector(".ops-history-events").open) refresh(); }, 30000);
  refresh();
})();
