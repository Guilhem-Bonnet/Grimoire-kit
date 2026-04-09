(function () {
  const isPresentationPage = window.location.pathname.includes("/presentation-decouverte/");
  const sectionIds = [
    "gd-step-01",
    "gd-step-02",
    "gd-step-03",
    "gd-step-04",
    "gd-step-05",
    "gd-step-06",
  ];

  if (!isPresentationPage) {
    return;
  }

  document.body.classList.add("gp-onepager");

  const panels = Array.from(document.querySelectorAll(".md-typeset .admonition.onepager"));
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const heroTitle = document.querySelector(".gp-hero-title") || document.querySelector(".md-content h2");
  let activeIndex = 0;

  if (!panels.length) {
    return;
  }

  if (heroTitle) {
    document.title = heroTitle.textContent.replace(/¶$/, "").trim() + " · Grimoire Kit";
  }

  const existingSectionNav = document.querySelector(".gp-section-nav");
  if (existingSectionNav) {
    existingSectionNav.remove();
  }

  const sectionNav = document.createElement("nav");
  sectionNav.className = "gp-section-nav";
  sectionNav.setAttribute("aria-label", "Sections de la presentation");

  const sectionList = document.createElement("ol");
  sectionNav.appendChild(sectionList);

  const navLinks = [];

  panels.forEach((panel, index) => {
    const step = String(index + 1).padStart(2, "0");
    const title = panel.querySelector(".admonition-title");
    const fullTitle = title ? title.textContent.trim() : "Acte " + step;
    const label = fullTitle.includes("—") ? fullTitle.split("—").pop().trim() : fullTitle;

    panel.dataset.step = step;
    panel.dataset.scene = String(index + 1);
    panel.classList.add("gp-scene-" + step);

    if (!panel.id) {
      panel.id = sectionIds[index] || "gd-step-" + step;
    }

    const listItem = document.createElement("li");
    const link = document.createElement("a");
    link.href = "#" + panel.id;
    link.textContent = label;
    link.setAttribute("aria-label", fullTitle);

    link.addEventListener("click", (event) => {
      event.preventDefault();
      panel.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
      window.history.replaceState(null, "", "#" + panel.id);
    });

    listItem.appendChild(link);
    sectionList.appendChild(listItem);
    navLinks.push(link);
  });

  const heroSignals = document.querySelector(".gp-hero-signals");
  if (heroSignals) {
    heroSignals.insertAdjacentElement("afterend", sectionNav);
  } else {
    panels[0].insertAdjacentElement("beforebegin", sectionNav);
  }

  const setActive = (index) => {
    activeIndex = index;

    navLinks.forEach((link, current) => {
      const isActive = current === index;

      link.classList.toggle("is-active", isActive);

      if (isActive) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    });

    panels.forEach((panel, current) => {
      panel.classList.toggle("is-current", current === index);
    });

    const navScroller = sectionNav.querySelector("ol");
    const activeLink = navLinks[index];

    if (!navScroller || !activeLink) {
      return;
    }

    if (navScroller.scrollWidth <= navScroller.clientWidth + 24) {
      navScroller.scrollTo({ left: 0, behavior: prefersReducedMotion ? "auto" : "smooth" });
      return;
    }

    const navRect = navScroller.getBoundingClientRect();
    const linkRect = activeLink.getBoundingClientRect();
    const padding = 24;

    if (linkRect.left >= navRect.left + padding && linkRect.right <= navRect.right - padding) {
      return;
    }

    const currentLeft = navScroller.scrollLeft;
    const idealLeft = linkRect.left < navRect.left + padding
      ? currentLeft + (linkRect.left - navRect.left) - padding
      : currentLeft + (linkRect.right - navRect.right) + padding;
    const maxLeft = navScroller.scrollWidth - navScroller.clientWidth;
    const nextLeft = Math.max(0, Math.min(maxLeft, idealLeft));

    navScroller.scrollTo({
      left: nextLeft,
      behavior: prefersReducedMotion ? "auto" : "smooth",
    });
  };

  const syncActiveFromScroll = () => {
    if (window.scrollY <= 24) {
      setActive(0);
      return;
    }

    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const visible = panels
      .map((panel, index) => {
        const rect = panel.getBoundingClientRect();
        const visibleHeight = Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0);

        return {
          index,
          rect,
          visibleHeight,
        };
      })
      .filter((entry) => entry.visibleHeight > 0)
      .sort((left, right) => right.visibleHeight - left.visibleHeight);

    if (!visible.length) {
      return;
    }

    if (visible[0].index !== activeIndex) {
      setActive(visible[0].index);
    }
  };

  const observer = new IntersectionObserver(
    (entries) => {
      const visibleEntries = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio);

      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
        }
      });

      if (!visibleEntries.length) {
        return;
      }

      const index = panels.indexOf(visibleEntries[0].target);

      if (index >= 0) {
        setActive(index);
      }
    },
    {
      threshold: [0.18, 0.4, 0.66],
      rootMargin: "-8% 0px -18% 0px",
    }
  );

  panels.forEach((panel) => observer.observe(panel));

  window.addEventListener("scroll", syncActiveFromScroll, { passive: true });
  window.addEventListener("hashchange", syncActiveFromScroll);

  requestAnimationFrame(() => {
    syncActiveFromScroll();
  });
})();
