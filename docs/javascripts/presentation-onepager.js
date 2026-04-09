(function () {
  const isPresentationPage = window.location.pathname.includes("/presentation-decouverte/");

  if (!isPresentationPage) {
    return;
  }

  document.body.classList.add("gd-onepager");

  const panels = Array.from(document.querySelectorAll(".md-typeset .admonition.onepager"));

  if (!panels.length) {
    return;
  }

  const progressRail = document.createElement("nav");
  progressRail.className = "gd-progress-rail";
  progressRail.setAttribute("aria-label", "Progression onepager");

  const railList = document.createElement("ol");
  progressRail.appendChild(railList);

  const railButtons = [];

  panels.forEach((panel, index) => {
    const step = String(index + 1).padStart(2, "0");
    panel.dataset.step = step;

    const title = panel.querySelector(".admonition-title");
    if (!panel.id) {
      panel.id = "gd-step-" + step;
    }

    const listItem = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = step;
    button.setAttribute("aria-label", title ? title.textContent.trim() : "Chapitre " + step);

    button.addEventListener("click", () => {
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    listItem.appendChild(button);
    railList.appendChild(listItem);
    railButtons.push(button);
  });

  document.body.appendChild(progressRail);

  const setActive = (index) => {
    railButtons.forEach((button, current) => {
      button.classList.toggle("is-active", current === index);
      button.classList.toggle("is-passed", current < index);
    });
  };

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const index = panels.indexOf(entry.target);

          if (index >= 0) {
            entry.target.classList.add("is-visible");
            setActive(index);
          }
        }
      });
    },
    {
      threshold: 0.42,
      rootMargin: "-10% 0px -28% 0px",
    }
  );

  panels.forEach((panel) => observer.observe(panel));
})();
