(() => {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const coarsePointer = window.matchMedia("(pointer: coarse)").matches;
  const themeModes = ["light", "dark"];
  const themeLabels = {
    light: "浅色",
    dark: "深色",
  };

  const normalizeThemeMode = (value) => (
    themeModes.includes(value) ? value : "light"
  );

  const readThemeMode = () => {
    try {
      return normalizeThemeMode(localStorage.getItem("spark-console-theme"));
    } catch (_) {
      return "light";
    }
  };

  const resolveTheme = (mode) => (
    normalizeThemeMode(mode)
  );

  const updateThemeControls = (mode) => {
    const currentIndex = themeModes.indexOf(mode);
    const nextMode = themeModes[(currentIndex + 1) % themeModes.length];
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const label = `当前主题：${themeLabels[mode]}。点击切换为${themeLabels[nextMode]}`;
      button.dataset.themeMode = mode;
      button.setAttribute("aria-label", label);
      button.title = label;
    });
  };

  const applyTheme = (requestedMode, persist = true) => {
    const mode = normalizeThemeMode(requestedMode);
    const theme = resolveTheme(mode);
    root.dataset.themeMode = mode;
    root.dataset.theme = theme;
    root.style.colorScheme = theme;

    const surface = root.dataset.appSurface === "admin" ? "admin" : "console";
    const themeColor = theme === "dark"
      ? (root.dataset.themeDarkColor || (surface === "admin" ? "#090b0a" : "#171b18"))
      : (root.dataset.themeLightColor || "#f4f6f3");
    document.querySelector("[data-theme-color]")?.setAttribute("content", themeColor);

    if (persist) {
      try { localStorage.setItem("spark-console-theme", mode); } catch (_) {}
    }
    updateThemeControls(mode);
  };

  const initThemeControls = () => {
    const initialMode = normalizeThemeMode(root.dataset.themeMode || readThemeMode());
    applyTheme(initialMode);

    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const currentMode = normalizeThemeMode(root.dataset.themeMode);
        const currentIndex = themeModes.indexOf(currentMode);
        applyTheme(themeModes[(currentIndex + 1) % themeModes.length]);
      });
    });

  };

  root.classList.toggle("mr-reduced-motion", reducedMotion);
  window.requestAnimationFrame(() => body.classList.add("mr-ready"));

  const initClock = () => {
    const clocks = [...document.querySelectorAll("[data-live-clock]")];
    if (!clocks.length) return;

    const formatter = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });

    const update = () => {
      const now = new Date();
      const value = formatter.format(now);
      clocks.forEach((clock) => {
        clock.textContent = value;
        if (clock.tagName === "TIME") clock.dateTime = now.toISOString();
      });
      window.setTimeout(update, 1000 - (Date.now() % 1000) + 20);
    };

    update();
  };

  const initSpotlights = () => {
    if (coarsePointer || reducedMotion) return;

    const cards = [...document.querySelectorAll(
      ".mr-spot, .panel, .stats article, .admin-panel, .admin-stat-card"
    )].filter((card) => !card.closest(".scan-stage"));

    cards.forEach((card) => {
      let frame = null;
      card.classList.add("mr-spot");
      card.addEventListener("pointermove", (event) => {
        if (frame !== null) return;
        frame = window.requestAnimationFrame(() => {
          frame = null;
          const rect = card.getBoundingClientRect();
          card.style.setProperty("--spot-x", String(event.clientX - rect.left) + "px");
          card.style.setProperty("--spot-y", String(event.clientY - rect.top) + "px");
          card.style.setProperty("--spot-opacity", "1");
          card.classList.add("mr-spot-active");
        });
      });
      card.addEventListener("pointerleave", () => {
        if (frame !== null) {
          window.cancelAnimationFrame(frame);
          frame = null;
        }
        card.style.setProperty("--spot-opacity", "0");
        card.classList.remove("mr-spot-active");
      });
    });
  };

  const initRailFeedback = () => {
    if (coarsePointer || reducedMotion) return;
    const rail = document.querySelector(".sidebar, .admin-sidebar");
    if (!rail) return;

    const links = [...rail.querySelectorAll(".nav-link, .admin-nav > a")];
    let frame = null;
    let pointerY = 0;

    const update = () => {
      frame = null;
      links.forEach((link) => {
        const rect = link.getBoundingClientRect();
        const distance = Math.abs(pointerY - (rect.top + rect.height / 2));
        const proximity = Math.max(0, 1 - distance / 96);
        const eased = proximity * proximity * (3 - 2 * proximity);
        link.style.setProperty("--rail-pull", eased.toFixed(3));
      });
    };

    rail.addEventListener("pointermove", (event) => {
      pointerY = event.clientY;
      if (frame === null) frame = window.requestAnimationFrame(update);
    });

    rail.addEventListener("pointerleave", () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
        frame = null;
      }
      links.forEach((link) => link.style.removeProperty("--rail-pull"));
    });
  };

  const initReveals = () => {
    const items = [...document.querySelectorAll(
      ".content-shell > *, .task-card, .run-entry, .account-item, .admin-content > *"
    )];
    if (!items.length || reducedMotion || !("IntersectionObserver" in window)) {
      items.forEach((item) => item.classList.add("mr-visible"));
      return;
    }

    items.forEach((item, index) => {
      item.classList.add("mr-reveal");
      item.style.setProperty("--reveal-delay", String(Math.min(index % 6, 5) * 28) + "ms");
    });

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("mr-visible");
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -24px", threshold: 0.04 });

    items.forEach((item) => observer.observe(item));
  };

  initThemeControls();
  initClock();
  if (body.dataset.design === "studio") return;
  initSpotlights();
  initRailFeedback();
  initReveals();
})();
