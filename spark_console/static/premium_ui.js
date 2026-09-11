(() => {
  "use strict";

  const root = document.documentElement;
  const body = document.body;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const numberFormatter = new Intl.NumberFormat("zh-CN");

  body.classList.toggle("premium-reduced-motion", reduceMotion);
  window.requestAnimationFrame(() => body.classList.add("premium-ready"));

  const updateClock = () => {
    const value = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date());
    document.querySelectorAll("[data-live-clock]").forEach((clock) => {
      clock.textContent = value;
      clock.dateTime = new Date().toISOString();
    });
  };
  if (document.querySelector("[data-live-clock]")) {
    updateClock();
    window.setInterval(updateClock, 1000);
  }

  const revealTargets = document.querySelectorAll([
    ".page-head",
    ".membership-strip",
    ".onboarding-panel",
    ".stats > article",
    ".content-shell > .panel",
    ".content-shell > .split",
    ".content-shell > .subscription-layout",
    ".content-shell > .subscription-help",
    ".security-page",
    ".auth-experience",
    ".admin-page-header",
    ".admin-stats > *",
    ".admin-filterbar",
    ".admin-content > .admin-panel",
    ".admin-content > .admin-grid",
  ].join(","));

  revealTargets.forEach((element, index) => {
    element.classList.add("premium-reveal");
    element.style.setProperty("--reveal-delay", `${Math.min(index * 45, 360)}ms`);
  });

  if ("IntersectionObserver" in window && !reduceMotion) {
    const revealObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-revealed");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.06, rootMargin: "0px 0px -24px" });
    revealTargets.forEach((element) => revealObserver.observe(element));
  } else {
    revealTargets.forEach((element) => element.classList.add("is-revealed"));
  }

  const animateNumber = (element) => {
    if (element.dataset.countupComplete === "true") return;
    const raw = element.textContent.trim().replace(/,/g, "");
    if (!/^\d+$/.test(raw)) return;
    const target = Number(raw);
    element.dataset.countupComplete = "true";
    if (reduceMotion || target === 0) {
      element.textContent = numberFormatter.format(target);
      return;
    }

    const duration = Math.min(1100, 560 + target * 22);
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - progress, 4);
      element.textContent = numberFormatter.format(Math.round(target * eased));
      if (progress < 1) window.requestAnimationFrame(step);
    };
    window.requestAnimationFrame(step);
  };

  const countTargets = document.querySelectorAll("[data-countup], .admin-stat-card > strong");
  if ("IntersectionObserver" in window) {
    const countObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        animateNumber(entry.target);
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.5 });
    countTargets.forEach((element) => countObserver.observe(element));
  } else {
    countTargets.forEach(animateNumber);
  }

  document.querySelectorAll("progress").forEach((progress) => {
    const max = Number(progress.max) || 1;
    const value = Math.min(max, Math.max(0, Number(progress.value) || 0));
    progress.style.setProperty("--progress-value", `${(value / max) * 100}%`);
  });

  if (finePointer && !reduceMotion) {
    let globalFrame = 0;
    document.addEventListener("pointermove", (event) => {
      window.cancelAnimationFrame(globalFrame);
      globalFrame = window.requestAnimationFrame(() => {
        root.style.setProperty("--cursor-x", `${event.clientX}px`);
        root.style.setProperty("--cursor-y", `${event.clientY}px`);
      });
    }, { passive: true });

    const spotlightSelector = [
      ".panel",
      ".stats > article",
      ".task-card",
      ".account-item",
      ".subscription-help-card",
      ".auth-card",
      ".auth-showcase",
      ".admin-panel",
      ".admin-stat-card",
      ".admin-list-row",
      ".admin-filterbar",
    ].join(",");

    document.querySelectorAll(spotlightSelector).forEach((element) => {
      if (element.closest(".scan-qr-shell")) return;
      element.classList.add("premium-spotlight");
      let frame = 0;
      element.addEventListener("pointermove", (event) => {
        window.cancelAnimationFrame(frame);
        frame = window.requestAnimationFrame(() => {
          const rect = element.getBoundingClientRect();
          element.style.setProperty("--spot-x", `${event.clientX - rect.left}px`);
          element.style.setProperty("--spot-y", `${event.clientY - rect.top}px`);
          element.style.setProperty("--spot-opacity", "1");
        });
      }, { passive: true });
      element.addEventListener("pointerleave", () => {
        element.style.setProperty("--spot-opacity", "0");
      }, { passive: true });
    });

    document.querySelectorAll([
      ".page-head > .button",
      ".hero-actions > .button",
      ".scan-entry > button",
      ".task-create-panel form > button",
      ".subscription-redeem form > button",
      ".auth-card form > button",
      ".admin-page-header .admin-button-primary",
    ].join(",")).forEach((button) => {
      button.classList.add("premium-magnetic");
      let frame = 0;
      button.addEventListener("pointermove", (event) => {
        window.cancelAnimationFrame(frame);
        frame = window.requestAnimationFrame(() => {
          const rect = button.getBoundingClientRect();
          const x = ((event.clientX - rect.left) / rect.width - 0.5) * 8;
          const y = ((event.clientY - rect.top) / rect.height - 0.5) * 6;
          button.style.setProperty("--mag-x", `${x}px`);
          button.style.setProperty("--mag-y", `${y}px`);
        });
      }, { passive: true });
      button.addEventListener("pointerleave", () => {
        button.style.setProperty("--mag-x", "0px");
        button.style.setProperty("--mag-y", "0px");
      }, { passive: true });
    });
  }

  document.addEventListener("pointerdown", (event) => {
    if (reduceMotion || event.button !== 0) return;
    const target = event.target.closest("button:not(:disabled), a.button");
    if (!target || target.closest(".scan-qr-shell")) return;
    const rect = target.getBoundingClientRect();
    const spark = document.createElement("span");
    spark.className = "premium-click-spark";
    spark.style.left = `${event.clientX - rect.left}px`;
    spark.style.top = `${event.clientY - rect.top}px`;
    target.appendChild(spark);
    window.setTimeout(() => spark.remove(), 650);
  });
})();
