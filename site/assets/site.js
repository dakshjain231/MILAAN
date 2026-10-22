(function () {
  // Fill ownership/legal fields from config.json. Any field left blank in the
  // config keeps the conspicuous pre-launch blocker already present in the
  // HTML, so no operator-specific detail is ever fabricated.
  function applyLegalConfig() {
    const slots = document.querySelectorAll("[data-config]");
    if (!slots.length) {
      return;
    }
    fetch("config.json", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("config unavailable");
        }
        return response.json();
      })
      .then(function (config) {
        slots.forEach(function (slot) {
          const key = slot.getAttribute("data-config");
          const value = config[key];
          if (typeof value === "string" && value.trim() !== "") {
            slot.textContent = value.trim();
            slot.classList.remove("is-blocked");
          } else if (slot.classList.contains("needs-review")) {
            slot.classList.add("is-blocked");
          }
        });
      })
      .catch(function () {
        slots.forEach(function (slot) {
          if (slot.classList.contains("needs-review")) {
            slot.classList.add("is-blocked");
          }
        });
      });
  }

  applyLegalConfig();

  const toggle = document.querySelector("[data-nav-toggle]");
  const nav = document.getElementById("site-nav");

  if (!toggle || !nav) {
    return;
  }

  const label = toggle.querySelector(".sr-only");
  const mobile = window.matchMedia("(max-width: 760px)");

  function setOpen(open) {
    nav.classList.toggle("is-open", open);
    document.body.classList.toggle("nav-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (label) {
      label.textContent = open ? "Close menu" : "Open menu";
    }
  }

  toggle.addEventListener("click", function () {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  nav.addEventListener("click", function (event) {
    if (event.target.closest("a")) {
      setOpen(false);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && nav.classList.contains("is-open")) {
      setOpen(false);
      toggle.focus();
    }
  });

  document.addEventListener("click", function (event) {
    if (
      nav.classList.contains("is-open") &&
      !nav.contains(event.target) &&
      !toggle.contains(event.target)
    ) {
      setOpen(false);
    }
  });

  if (mobile.addEventListener) {
    mobile.addEventListener("change", function (event) {
      if (!event.matches) {
        setOpen(false);
      }
    });
  }
})();
