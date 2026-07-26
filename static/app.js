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
    card.scrollIntoView({ block: "nearest" });
  }

  cards.forEach((card) => card.addEventListener("click", () => selectCard(card)));

  function selectedCardIndex() {
    return cards.findIndex((card) => card.classList.contains("selected"));
  }

  function moveSelection(offset) {
    if (!cards.length) return;
    const current = selectedCardIndex();
    const next = Math.min(
      cards.length - 1,
      Math.max(0, (current < 0 ? 0 : current) + offset),
    );
    selectCard(cards[next]);
    cards[next].focus({ preventScroll: true });
  }

  function submitDetailForm(selector) {
    const form = detailPanel?.querySelector(selector);
    if (form instanceof HTMLFormElement) form.requestSubmit();
  }

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
    const isIgnoreAction = new URL(form.action, document.baseURI).pathname.endsWith("/ignore");
    const ignoredCardIndex = selectedCardIndex();
    if (submitter) {
      submitter.disabled = true;
      submitter.classList.add("is-working");
      submitter.dataset.label = submitter.textContent;
      submitter.textContent = "Working…";
    }

    try {
      const actionOverride = submitter?.getAttribute("formaction");
      const methodOverride = submitter?.getAttribute("formmethod");
      const action = actionOverride
        ? new URL(actionOverride, document.baseURI).href
        : form.action;
      const method = methodOverride || form.method;
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
      if (isIgnoreAction && ignoredCardIndex >= 0) {
        const [ignoredCard] = cards.splice(ignoredCardIndex, 1);
        ignoredCard.remove();
        if (cards.length) {
          selectCard(cards[Math.min(ignoredCardIndex, cards.length - 1)]);
        } else {
          detailPanel.replaceChildren();
        }
      } else {
        detailPanel.replaceChildren(refreshed);
      }
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

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const isEditing = target instanceof HTMLTextAreaElement
      || target instanceof HTMLInputElement
      || target instanceof HTMLSelectElement
      || target?.isContentEditable;

    if (event.key === "Escape" && isEditing) {
      event.preventDefault();
      target.blur();
      return;
    }

    if (
      event.key === "Enter"
      && event.ctrlKey
      && target instanceof HTMLTextAreaElement
      && target.closest(".reply-editor")
    ) {
      event.preventDefault();
      const form = target.closest(".reply-editor");
      const approveAndPost = form.querySelector(".shortcut-approve-post");
      form.requestSubmit(approveAndPost);
      return;
    }

    if (isEditing || event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === "j") {
      event.preventDefault();
      moveSelection(1);
    } else if (event.key === "k") {
      event.preventDefault();
      moveSelection(-1);
    } else if (event.key === "r") {
      event.preventDefault();
      submitDetailForm('form[action$="/generate-reply"]');
    } else if (event.key === "e") {
      const editor = detailPanel?.querySelector(".reply-editor textarea");
      if (editor) {
        event.preventDefault();
        editor.focus();
        editor.setSelectionRange(editor.value.length, editor.value.length);
      }
    } else if (event.key === "h") {
      event.preventDefault();
      submitDetailForm('form[action$="/ignore"]');
    }
  });
})();
