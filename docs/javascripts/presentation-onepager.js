(function () {
  const isPresentationPage = window.location.pathname.includes("/presentation-decouverte/");

  if (!isPresentationPage) {
    return;
  }

  document.body.classList.add("gp-onepager");

  const panels = Array.from(document.querySelectorAll(".md-typeset .admonition.onepager"));
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const heroTitle = document.querySelector(".gp-hero-title") || document.querySelector(".md-content h2");

  if (!panels.length) {
    return;
  }

  if (heroTitle) {
    document.title = heroTitle.textContent.replace(/¶$/, "").trim() + " · Grimoire Kit";
  }

  const existingRail = document.querySelector(".gp-progress");
  if (existingRail) {
    existingRail.remove();
  }

  const progressRail = document.createElement("nav");
  progressRail.className = "gp-progress";
  progressRail.setAttribute("aria-label", "Progression onepager");

  const railList = document.createElement("ol");
  progressRail.appendChild(railList);

  const railButtons = [];

  panels.forEach((panel, index) => {
    const step = String(index + 1).padStart(2, "0");
    panel.dataset.step = step;
    panel.dataset.scene = String(index + 1);
    panel.classList.add("gp-scene-" + step);

    const title = panel.querySelector(".admonition-title");
    if (!panel.id) {
      panel.id = "gd-step-" + step;
    }

    const listItem = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = step;
    button.setAttribute("aria-label", title ? title.textContent.trim() : "Acte " + step);

    button.addEventListener("click", () => {
      panel.scrollIntoView({ behavior: prefersReducedMotion ? "auto" : "smooth", block: "start" });
    });

    listItem.appendChild(button);
    railList.appendChild(listItem);
    railButtons.push(button);
  });

  document.body.appendChild(progressRail);

  const setActive = (index) => {
    railButtons.forEach((button, current) => {
      button.classList.toggle("is-active", current === index);
      button.classList.toggle("is-past", current < index);
    });

    panels.forEach((panel, current) => {
      panel.classList.toggle("is-current", current === index);
    });
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
})();
