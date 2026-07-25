(() => {
  const root = document.documentElement;
  const developerToggle = document.querySelector("#developer-mode");
  const detailPanel = document.querySelector("#conversation-panel");
  const cards = [...document.querySelectorAll(".conversation-card")];

  const developerMode = localStorage.getItem("wingman-developer-mode") === "true";
  root.classList.toggle("developer-mode-on", developerMode);
  if (developerToggle) {
    developerToggle.checked = developerMode;
    developerToggle.addEventListener("change", () => {
      root.classList.toggle("developer-mode-on", developerToggle.checked);
      localStorage.setItem(
        "wingman-developer-mode",
        String(developerToggle.checked),
      );
    });
  }

  function selectCard(card) {
    const template = document.querySelector(`#detail-${CSS.escape(card.dataset.commentId)}`);
    if (!template || !detailPanel) return;
    cards.forEach((item) => {
      const selected = item === card;
      item.classList.toggle("selected", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    detailPanel.replaceChildren(template.content.cloneNode(true));
    detailPanel.classList.add("mobile-open");
    history.replaceState(null, "", `#${encodeURIComponent(card.dataset.commentId)}`);
  }

  cards.forEach((card) => card.addEventListener("click", () => selectCard(card)));

  if (cards.length) {
    const requestedId = decodeURIComponent(location.hash.slice(1));
    const initial = cards.find((card) => card.dataset.commentId === requestedId) || cards[0];
    selectCard(initial);
  }

  document.addEventListener("click", (event) => {
    if (event.target.closest(".mobile-back") && detailPanel) {
      event.preventDefault();
      detailPanel.classList.remove("mobile-open");
      history.replaceState(null, "", location.pathname + location.search);
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (!form.matches(".async-form, .reply-editor")) return;
    event.preventDefault();

    const submitter = event.submitter;
    if (submitter) {
      submitter.disabled = true;
      submitter.classList.add("is-working");
      submitter.dataset.label = submitter.textContent;
      submitter.textContent = "Working…";
    }

    try {
      const action = submitter?.formAction || form.action;
      const method = submitter?.formMethod || form.method;
      const response = await fetch(action, {
        method,
        body: new FormData(form),
      });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      const finalResponse = await fetch(response.url);
      if (!finalResponse.ok) throw new Error(`Refresh failed (${finalResponse.status})`);
      const documentText = await finalResponse.text();
      const parsed = new DOMParser().parseFromString(documentText, "text/html");
      const refreshed = parsed.querySelector(".conversation-shell");
      if (!refreshed || !detailPanel) throw new Error("Updated conversation was unavailable");
      detailPanel.replaceChildren(refreshed);
    } catch (error) {
      const message = document.createElement("p");
      message.className = "form-error";
      message.textContent = error.message;
      form.prepend(message);
      if (submitter) {
        submitter.disabled = false;
        submitter.classList.remove("is-working");
        submitter.textContent = submitter.dataset.label;
      }
    }
  });

  document.addEventListener("input", (event) => {
    const textarea = event.target.closest(".reply-editor textarea");
    if (!textarea) return;
    const form = textarea.closest(".reply-editor");
    const learnButton = form?.querySelector(".learn-button");
    if (!learnButton) return;
    const original = textarea.dataset.originalDraft?.trim() || "";
    learnButton.disabled = !original || textarea.value.trim() === original;
  });
})();
