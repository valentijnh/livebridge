// LiveBridge site: the hero demo, the install tabs and the copy buttons. No dependencies.
(() => {
  "use strict";
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- hero demo: the prompt is typed, the tool calls appear, the clips fill the grid ----
  const demo = document.querySelector("[data-demo]");
  if (demo) {
    const typed = demo.querySelector("[data-typed]");
    const calls = [...demo.querySelectorAll(".calls li")];
    const targets = [...demo.querySelectorAll("[data-after]")];
    const prompt = typed.dataset.typed;
    let timers = [];

    const reset = () => {
      timers.forEach(clearTimeout);
      timers = [];
      typed.textContent = "";
      calls.forEach((li) => li.classList.remove("on"));
      targets.forEach((el) => el.classList.remove("on", "playing", ...el.dataset.color.split(" ")));
    };
    const show = (step) => {
      if (calls[step]) calls[step].classList.add("on");
      targets.filter((el) => Number(el.dataset.after) === step)
        .forEach((el) => el.classList.add("on", ...el.dataset.color.split(" ")));
    };
    const finish = () => targets.filter((el) => el.classList.contains("clip"))
      .forEach((el) => el.classList.add("playing"));
    const at = (ms, fn) => timers.push(setTimeout(fn, ms));

    const run = () => {
      reset();
      if (reduced) {
        typed.textContent = prompt;
        calls.forEach((_, i) => show(i));
        finish();
        return;
      }
      let t = 250;
      for (let i = 1; i <= prompt.length; i += 1) {
        at(t, () => { typed.textContent = prompt.slice(0, i); });
        t += 16;
      }
      t += 450;
      calls.forEach((_, i) => { at(t, () => show(i)); t += 520; });
      at(t, finish);
    };

    demo.querySelector("[data-replay]").addEventListener("click", run);
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { seen.disconnect(); run(); }
    }, { threshold: 0.3 });
    seen.observe(demo);
  }

  // ---- install tabs ------------------------------------------------------------------------
  document.querySelectorAll("[role=tablist]").forEach((list) => {
    const tabs = [...list.querySelectorAll("[role=tab]")];
    const select = (tab) => tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (!step) return;
        const next = tabs[(i + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      });
    });
    const windows = /Windows/i.test(navigator.userAgent);
    select(tabs.find((t) => t.dataset.os === (windows ? "windows" : "macos")) || tabs[0]);
  });

  // ---- copy buttons ------------------------------------------------------------------------
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = button.dataset.copy
        ? document.getElementById(button.dataset.copy).innerText
        : button.previousElementSibling.innerText;
      const text = source.split("\n").filter((line) => !line.trim().startsWith("#")).join("\n").trim();
      const label = button.textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch (err) {
        button.textContent = "Select and copy";
      }
      setTimeout(() => { button.textContent = label; }, 1400);
    });
  });
})();
