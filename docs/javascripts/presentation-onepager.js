(function () {
  const sectionIds = [
    "gd-step-01",
    "gd-step-02",
    "gd-step-03",
    "gd-step-04",
    "gd-step-05",
    "gd-step-06",
    "gd-step-07",
  ];
  let cleanupPresentationPage = null;

  const initializePresentationPage = () => {
    if (cleanupPresentationPage) {
      cleanupPresentationPage();
      cleanupPresentationPage = null;
    }

    const isPresentationPage = window.location.pathname.includes("/presentation-decouverte/");
    if (!isPresentationPage) {
      document.body.classList.remove("gp-onepager");
      document.body.removeAttribute("data-gp-scheme");
      return;
    }

    document.body.classList.add("gp-onepager");

    const syncThemeState = () => {
      const scheme = document.body.getAttribute("data-md-color-scheme")
        || document.documentElement.getAttribute("data-md-color-scheme")
        || "default";

      document.body.setAttribute("data-gp-scheme", scheme);
    };

    const themeObservers = [document.body, document.documentElement]
      .filter(Boolean)
      .map((target) => {
        const observer = new MutationObserver(() => {
          syncThemeState();
        });

        observer.observe(target, {
          attributes: true,
          attributeFilter: ["data-md-color-scheme"],
        });

        return observer;
      });

    syncThemeState();

    const panels = Array.from(document.querySelectorAll(".md-typeset .admonition.onepager"));
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const heroTitle = document.querySelector(".gp-hero-title") || document.querySelector(".md-content h2");
    let activeIndex = 0;

    if (!panels.length) {
      themeObservers.forEach((observer) => observer.disconnect());
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
    sectionNav.setAttribute("aria-label", "Sections de la présentation");

    const sectionList = document.createElement("ol");
    sectionNav.appendChild(sectionList);

    const navLinks = [];

    const setNavResting = () => {
      sectionNav.classList.toggle("is-resting", window.scrollY <= 48);
    };

    const setActive = (index) => {
      activeIndex = index;

      navLinks.forEach((link, current) => {
        const isActive = current === index;

        link.classList.toggle("is-active", isActive);

        if (isActive) {
          link.setAttribute("aria-current", "step");
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
      setNavResting();

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

    const revealPanelsInViewport = () => {
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
      const minVisibleHeight = Math.min(220, viewportHeight * 0.22);

      panels.forEach((panel) => {
        const rect = panel.getBoundingClientRect();
        const visibleHeight = Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0);

        if (visibleHeight >= minVisibleHeight) {
          panel.classList.add("is-visible");
        }
      });
    };

    panels.forEach((panel, index) => {
      const step = String(index + 1).padStart(2, "0");
      const title = panel.querySelector(".admonition-title");
      const fullTitle = title ? title.textContent.trim() : "Acte " + step;
      const label = fullTitle.includes("—") ? fullTitle.split("—").pop().trim() : fullTitle;

      panel.dataset.step = step;
      panel.dataset.scene = String(index + 1);
      panel.classList.add("gp-scene-" + step);

      if (title) {
        title.dataset.step = step;
      }

      if (!panel.id) {
        panel.id = sectionIds[index] || "gd-step-" + step;
      }

      const listItem = document.createElement("li");
      const link = document.createElement("button");
      link.type = "button";
      link.textContent = label;
      link.setAttribute("aria-label", fullTitle);
      link.setAttribute("aria-controls", panel.id);

      link.addEventListener("click", () => {
        setActive(index);
        panel.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
        window.history.replaceState(null, "", "#" + panel.id);
        requestAnimationFrame(syncActiveFromScroll);
      });

      listItem.appendChild(link);
      sectionList.appendChild(listItem);
      navLinks.push(link);
    });

    const heroSignals = document.querySelector(".gp-hero-signals");
    const newsStrip = document.querySelector(".gp-news-strip");
    if (newsStrip) {
      newsStrip.insertAdjacentElement("afterend", sectionNav);
    } else if (heroSignals) {
      heroSignals.insertAdjacentElement("afterend", sectionNav);
    } else {
      panels[0].insertAdjacentElement("beforebegin", sectionNav);
    }

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

        revealPanelsInViewport();
      },
      {
        threshold: [0.08, 0.18, 0.4, 0.66],
        rootMargin: "-8% 0px -18% 0px",
      }
    );

    panels.forEach((panel) => observer.observe(panel));

    const onScroll = () => {
      revealPanelsInViewport();
      syncActiveFromScroll();
    };
    const onHashChange = () => {
      revealPanelsInViewport();
      syncActiveFromScroll();
    };
    const initialSyncFrame = requestAnimationFrame(() => {
      revealPanelsInViewport();
      syncActiveFromScroll();
    });

    setNavResting();

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("hashchange", onHashChange);

    cleanupPresentationPage = () => {
      themeObservers.forEach((observer) => observer.disconnect());
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("hashchange", onHashChange);
      cancelAnimationFrame(initialSyncFrame);

      if (sectionNav.isConnected) {
        sectionNav.remove();
      }

      panels.forEach((panel) => {
        panel.classList.remove("is-current", "is-visible");
      });
    };
  };

  const scheduleInitialization = () => {
    requestAnimationFrame(() => {
      initializePresentationPage();
    });
  };

  if (typeof document$ !== "undefined" && document$ && typeof document$.subscribe === "function") {
    document$.subscribe(() => {
      scheduleInitialization();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleInitialization, { once: true });
  } else {
    scheduleInitialization();
  }
})();