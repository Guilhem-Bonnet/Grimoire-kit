(function () {
  const isPresentationPage = window.location.pathname.includes("/presentation-decouverte/");

  if (!isPresentationPage) {
    return;
  }

  document.body.classList.add("gd-onepager");

  const renderMermaidDiagrams = () => {
    if (typeof window.mermaid === "undefined") {
      return;
    }

    const codeBlocks = Array.from(document.querySelectorAll(".md-typeset pre > code.language-mermaid"));

    codeBlocks.forEach((codeBlock, index) => {
      const pre = codeBlock.parentElement;
      if (!pre) {
        return;
      }

      const mermaidHost = document.createElement("div");
      mermaidHost.className = "mermaid";
      mermaidHost.id = "gd-mermaid-" + String(index + 1).padStart(2, "0");
      mermaidHost.textContent = codeBlock.textContent || "";

      pre.replaceWith(mermaidHost);
    });

    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: "base",
      themeVariables: {
        primaryColor: "#1f3d4d",
        primaryTextColor: "#f6f2ea",
        primaryBorderColor: "#4fd2c7",
        lineColor: "#ffd166",
        tertiaryColor: "#183446",
        fontFamily: "Space Grotesk",
      },
      flowchart: {
        curve: "basis",
      },
    });

    window.mermaid.run({
      querySelector: ".gd-onepager .mermaid",
    });
  };

  renderMermaidDiagrams();

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
