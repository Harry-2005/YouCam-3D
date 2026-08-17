const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const revealItems = [...document.querySelectorAll("[data-reveal]")];

if (reducedMotion || !("IntersectionObserver" in window)) {
  revealItems.forEach((item) => item.classList.add("is-visible"));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8%", threshold: 0.08 },
  );
  revealItems.forEach((item) => observer.observe(item));
}

const clayStage = document.querySelector(".clay-stage");
const clayFigure = clayStage?.querySelector(":scope > img");

if (clayStage && clayFigure && !reducedMotion) {
  clayStage.addEventListener("pointermove", (event) => {
    const bounds = clayStage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) / bounds.width - 0.5;
    const y = (event.clientY - bounds.top) / bounds.height - 0.5;
    clayStage.style.setProperty("--pointer-x", `${x * 12}px`);
    clayStage.style.setProperty("--pointer-y", `${y * 8}px`);
  });

  clayStage.addEventListener("pointerleave", () => {
    clayStage.style.setProperty("--pointer-x", "0px");
    clayStage.style.setProperty("--pointer-y", "0px");
  });
}
