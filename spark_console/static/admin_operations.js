(function () {
  "use strict";

  function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function meterLevel(value) {
    var percent = finiteNumber(value);
    if (percent !== null && percent >= 90) return "danger";
    if (percent !== null && percent >= 75) return "warning";
    return "normal";
  }

  function responseRequiresLogin(response, currentHref) {
    if (!response) return false;
    if (response.status === 401 || response.status === 403) return true;
    if (!response.redirected || !response.url) return false;
    try {
      return new URL(response.url, currentHref || "http://localhost/").pathname === "/login";
    } catch (_) {
      return false;
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { meterLevel: meterLevel, responseRequiresLogin: responseRequiresLogin };
  }
  if (typeof document === "undefined") return;

  var root = document.querySelector("[data-operations-monitor]");
  if (!root) return;

  var endpoint = root.dataset.endpoint || "/admin/operations/snapshot";
  var refreshSeconds = Math.max(10, Number(root.dataset.refreshSeconds) || 30);
  var refreshButton = root.querySelector("[data-ops-refresh]");
  var refreshStatus = root.querySelector("[data-ops-refresh-status]");
  var refreshPending = false;
  var lastUpdatedAt = 0;

  function setText(selector, value) {
    var node = root.querySelector(selector);
    if (node) node.textContent = value;
  }

  function formatPercent(value) {
    var number = finiteNumber(value);
    return number === null ? "—" : number.toFixed(number % 1 ? 1 : 0) + "%";
  }

  function formatBytes(value) {
    var bytes = finiteNumber(value);
    if (bytes === null || bytes < 0) return "—";
    var units = ["B", "KiB", "MiB", "GiB", "TiB"];
    var unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) {
      bytes /= 1024;
      unit += 1;
    }
    var digits = unit === 0 || bytes >= 100 ? 0 : bytes >= 10 ? 1 : 2;
    return bytes.toFixed(digits) + " " + units[unit];
  }

  function formatDuration(value) {
    var total = finiteNumber(value);
    if (total === null || total < 0) return "—";
    total = Math.floor(total);
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    if (days) return days + "天 " + hours + "小时";
    if (hours) return hours + "小时 " + minutes + "分钟";
    return minutes ? minutes + "分钟" : "不足 1 分钟";
  }

  function formatDate(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function formatRelativeSeconds(value) {
    var seconds = finiteNumber(value);
    if (seconds === null) return "—";
    if (seconds < 5) return "刚刚";
    if (seconds < 60) return Math.floor(seconds) + " 秒前";
    return Math.floor(seconds / 60) + " 分钟前";
  }

  function updateMeter(name, value) {
    var meter = root.querySelector('[data-ops-meter="' + name + '"]');
    if (!meter) return;
    var percent = finiteNumber(value);
    percent = percent === null ? 0 : Math.min(100, Math.max(0, percent));
    var level = meterLevel(percent);
    meter.setAttribute("aria-valuenow", String(percent));
    meter.classList.toggle("is-warning", level === "warning");
    meter.classList.toggle("is-danger", level === "danger");
    var fill = meter.querySelector("i");
    if (fill) fill.style.setProperty("--ops-meter-value", percent + "%");
  }

  function serviceTone(status) {
    if (status === "healthy") return { css: "success", mark: "healthy", label: "正常" };
    if (status === "warning") return { css: "warning", mark: "warning", label: "需关注" };
    if (status === "offline") return { css: "danger", mark: "offline", label: "离线" };
    return { css: "neutral", mark: "unknown", label: "未知" };
  }

  function updateService(key, service) {
    var row = root.querySelector('[data-ops-service="' + key + '"]');
    if (!row || !service) return;
    var tone = serviceTone(service.status);
    var badge = row.querySelector("[data-ops-service-badge]");
    var mark = row.querySelector("[data-ops-service-mark]");
    if (badge) badge.className = "admin-badge admin-badge-" + tone.css;
    if (mark) mark.className = "ops-service-mark " + tone.mark;
    var label = row.querySelector("[data-ops-service-label]");
    var detail = row.querySelector("[data-ops-service-detail]");
    var statusTime = row.querySelector("[data-ops-service-time]");
    if (label) label.textContent = tone.label;
    if (detail) detail.textContent = service.detail || "暂无状态说明";
    if (!statusTime) return;
    if (key === "web") statusTime.textContent = "刚刚检查";
    else if (key === "auth") statusTime.textContent = service.last_seen_at ? "心跳 " + formatRelativeSeconds(service.age_seconds) : "无心跳记录";
    else if (service.status === "healthy") statusTime.textContent = "租约剩余 " + (service.lease_remaining_seconds || 0) + " 秒";
    else statusTime.textContent = service.lease_until ? "租约已过期" : "无租约记录";
  }

  function applySnapshot(snapshot) {
    if (!snapshot || !snapshot.system) return;
    var system = snapshot.system;
    var cpu = system.cpu || {};
    var memory = system.memory || {};
    var disk = system.disk || {};
    var load = system.load || {};

    setText('[data-ops-value="cpu-percent"]', formatPercent(cpu.percent));
    setText("[data-ops-cpu-cores]", cpu.scope === "web_container" && cpu.capacity_cores ? cpu.capacity_cores + " vCPU 配额" : cpu.logical_cores ? cpu.logical_cores + " 逻辑核" : "—");
    setText("[data-ops-cpu-source]", !cpu.available ? "指标不可读取" : cpu.scope === "host" ? "服务器全部 CPU 核心" : cpu.source === "cgroup_v2" ? "当前 Web 容器 cgroup v2" : "当前运行环境 /proc 统计");
    setText('[data-ops-value="memory-percent"]', formatPercent(memory.percent));
    setText("[data-ops-memory-scope]", memory.scope === "host" ? "服务器总内存" : memory.scope === "web_container" ? "Web 容器配额" : "运行环境可见");
    setText('[data-ops-value="memory-detail"]', memory.available ? formatBytes(memory.used_bytes) + " / " + formatBytes(memory.total_bytes) : "当前环境不可读取");
    setText('[data-ops-value="disk-percent"]', formatPercent(disk.percent));
    setText('[data-ops-value="disk-detail"]', disk.available ? formatBytes(disk.used_bytes) + " / " + formatBytes(disk.total_bytes) : "当前环境不可读取");
    setText('[data-ops-value="uptime"]', formatDuration(system.uptime_seconds));
    setText('[data-ops-value="load-one"]', load.one === null || load.one === undefined ? "—" : Number(load.one).toFixed(2));
    setText('[data-ops-value="load-five"]', load.five === null || load.five === undefined ? "—" : Number(load.five).toFixed(2));
    setText('[data-ops-value="load-fifteen"]', load.fifteen === null || load.fifteen === undefined ? "—" : Number(load.fifteen).toFixed(2));
    updateMeter("cpu", cpu.percent);
    updateMeter("memory", memory.percent);
    updateMeter("disk", disk.percent);

    var services = snapshot.services || {};
    ["web", "auth", "worker"].forEach(function (key) { updateService(key, services[key]); });

    var queue = snapshot.queue || {};
    root.querySelectorAll("[data-ops-queue]").forEach(function (node) {
      var key = node.dataset.opsQueue;
      var value = finiteNumber(queue[key]);
      node.textContent = value === null ? "—" : String(value);
      if (["due_tasks", "attention_24h", "invalid_accounts"].indexOf(key) !== -1) {
        node.parentElement.classList.toggle("attention", value !== null && value > 0);
      }
    });

    var database = snapshot.database || {};
    if(snapshot.business){
      var business=snapshot.business;
      root.querySelectorAll('[data-business-value]').forEach(function(node){var key=node.dataset.businessValue;var value=business[key];node.textContent=value==null?'—':String(value)+(key==='unconfirmed_percent'?'%':'');});
      var failures=root.querySelector('[data-business-failures]');
      if(failures){failures.replaceChildren();(business.failures||[]).forEach(function(entry){var li=document.createElement('li');li.textContent=(entry[0]||'未分类')+' · '+entry[1]+' 次';failures.append(li);});if(!failures.children.length){var li=document.createElement('li');li.textContent='最近 24 小时暂无失败记录';failures.append(li);}}
    }
    setText('[data-ops-value="database-size"]', database.size_available ? formatBytes(database.size_bytes) : "不可读取");
    setText('[data-ops-value="database-backend"]', String(database.backend || "unknown").toUpperCase());
    setText("[data-ops-generated]", formatDate(snapshot.generated_at));
    setText("[data-ops-scope]", "资源口径：" + (system.scope_label || "当前运行环境可见范围"));
    lastUpdatedAt = Date.now();
  }

  function setRefreshing(active) {
    if (!refreshButton) return;
    refreshButton.disabled = active;
    refreshButton.setAttribute("aria-busy", active ? "true" : "false");
    refreshButton.classList.toggle("is-loading", active);
  }

  function refresh() {
    if (refreshPending || (document.hidden && lastUpdatedAt)) return;
    refreshPending = true;
    setRefreshing(true);
    if (refreshStatus) refreshStatus.textContent = "正在获取最新状态…";
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timeout = window.setTimeout(function () {
      if (controller) controller.abort();
    }, 8000);
    fetch(endpoint, {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: controller ? controller.signal : undefined
    }).then(function (response) {
      if (responseRequiresLogin(response, window.location.href)) {
        if (refreshStatus) refreshStatus.textContent = "登录状态已失效，正在返回登录页…";
        window.location.assign("/login");
        return null;
      }
      if (!response.ok) throw new Error("operations-refresh-" + response.status);
      return response.json();
    }).then(function (snapshot) {
      if (!snapshot) return;
      applySnapshot(snapshot);
      if (refreshStatus) refreshStatus.textContent = "每 " + refreshSeconds + " 秒自动刷新";
    }).catch(function () {
      if (refreshStatus) refreshStatus.textContent = "刷新失败，已保留上次数据并将自动重试";
    }).finally(function () {
      window.clearTimeout(timeout);
      refreshPending = false;
      setRefreshing(false);
    });
  }

  var initial = root.querySelector("[data-ops-initial]");
  if (initial) {
    try { applySnapshot(JSON.parse(initial.textContent)); } catch (_) {}
  }
  if (refreshButton) refreshButton.addEventListener("click", refresh);
  window.setInterval(refresh, refreshSeconds * 1000);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && Date.now() - lastUpdatedAt >= refreshSeconds * 1000) refresh();
  });
})();
