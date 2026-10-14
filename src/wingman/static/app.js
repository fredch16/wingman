(() => {
  const root = document.documentElement;
  const developerToggle = document.querySelector("#developer-mode");
  const detailPanel = document.querySelector("#conversation-panel");
  const cards = [...document.querySelectorAll(".conversation-card")];
  const conversationCount = document.querySelector("#conversation-count");
  const jobProgress = document.querySelector("#job-progress");
  const jobStorageKey = "wingman-active-job";
  const generationJobStorageKey = "wingman-active-generation-job";
  const themeStorageKey = "wingman-theme";
  const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
  const themeOptions = [...document.querySelectorAll("[data-theme-option]")];
  const autosaveTimers = new Map();
  const autosaveRequests = new Set();
  const activeGenerationRequests = new Map();

  function createGenerationOperationId() {
    if (typeof globalThis.crypto?.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  function applyTheme(preference) {
    const selected = ["light", "dark", "system"].includes(preference)
      ? preference
      : "system";
    const resolved = selected === "system"
      ? (themeMedia.matches ? "dark" : "light")
      : selected;
    root.dataset.theme = resolved;
    root.dataset.themePreference = selected;
    root.style.colorScheme = resolved;
    themeOptions.forEach((option) => {
      option.checked = option.value === selected;
    });
    document.querySelectorAll("[data-system-theme-label]").forEach((label) => {
      label.textContent = `Currently ${themeMedia.matches ? "dark" : "light"}`;
    });
  }

  applyTheme(localStorage.getItem(themeStorageKey) || "system");
  themeOptions.forEach((option) => {
    option.addEventListener("change", () => {
      if (!option.checked) return;
      localStorage.setItem(themeStorageKey, option.value);
      applyTheme(option.value);
    });
  });
  themeMedia.addEventListener("change", () => {
    if ((localStorage.getItem(themeStorageKey) || "system") === "system") {
      applyTheme("system");
    }
  });

  function updateSaveHint(textarea, message, isError = false) {
    const hint = textarea.closest(".reply-editor")?.querySelector(".save-hint");
    if (!hint) return;
    if (!hint.dataset.defaultText) hint.dataset.defaultText = hint.textContent;
    hint.textContent = message || hint.dataset.defaultText;
    hint.classList.toggle("save-hint-error", isError);
  }

  function persistTextarea(textarea) {
    const url = textarea.dataset.autosaveUrl;
    const field = textarea.dataset.autosaveField;
    if (!url || !field) return Promise.resolve();
    const body = new FormData();
    body.set(field, textarea.value);
    updateSaveHint(textarea, "Saving…");
    const request = fetch(url, { method: "POST", body })
      .then((response) => {
        if (!response.ok) throw new Error(`Autosave failed (${response.status})`);
        updateSaveHint(textarea, field === "reply_direction" ? "Reply direction saved" : "Draft saved");
      })
      .catch((error) => {
        updateSaveHint(textarea, error.message, true);
        throw error;
      })
      .finally(() => autosaveRequests.delete(request));
    autosaveRequests.add(request);
    return request;
  }

  function scheduleAutosave(textarea) {
    const existing = autosaveTimers.get(textarea);
    if (existing) clearTimeout(existing);
    updateSaveHint(textarea, "Unsaved changes…");
    autosaveTimers.set(textarea, setTimeout(() => {
      autosaveTimers.delete(textarea);
      persistTextarea(textarea).catch(() => {});
    }, 450));
  }

  function mirrorTextareaInTemplate(textarea) {
    const shell = textarea.closest(".conversation-shell");
    const commentId = shell?.dataset.detailId;
    if (!commentId) return;
    const template = document.querySelector(`#detail-${CSS.escape(commentId)}`);
    const mirrored = template?.content.querySelector(`#${CSS.escape(textarea.id)}`);
    if (!mirrored) return;
    mirrored.value = textarea.value;
    mirrored.textContent = textarea.value;
  }

  async function flushPendingAutosaves() {
    const started = [];
    for (const [textarea, timer] of autosaveTimers) {
      clearTimeout(timer);
      autosaveTimers.delete(textarea);
      started.push(persistTextarea(textarea));
    }
    await Promise.all([...autosaveRequests, ...started]);
  }

  function renderJob(job) {
    if (!jobProgress) return;
    const total = Math.max(Number(job.total) || 1, 1);
    const completed = Math.min(Number(job.completed) || 0, total);
    const percent = Math.round((completed / total) * 100);
    jobProgress.hidden = false;
    jobProgress.querySelector("[data-job-label]").textContent = job.label;
    jobProgress.querySelector("[data-job-count]").textContent = `${completed} / ${total}`;
    jobProgress.querySelector("[data-job-current]").textContent = job.error || job.current;
    jobProgress.querySelector("[data-job-bar]").style.width = `${percent}%`;
    jobProgress.classList.toggle("job-failed", job.status === "failed");
    jobProgress.classList.toggle("job-cancelled", job.status === "cancelled");
  }

  function setJobButtonsDisabled(disabled) {
    document.querySelectorAll(".background-job-form button").forEach((button) => {
      button.disabled = disabled;
    });
  }

  async function pollJob(jobId) {
    try {
      const response = await fetch(`/jobs/${encodeURIComponent(jobId)}`);
      if (!response.ok) throw new Error(`Progress unavailable (${response.status})`);
      const job = await response.json();
      renderJob(job);
      if (job.status === "running") {
        setTimeout(() => pollJob(jobId), 500);
        return;
      }
      sessionStorage.removeItem(jobStorageKey);
      if (sessionStorage.getItem(generationJobStorageKey) === jobId) {
        sessionStorage.removeItem(generationJobStorageKey);
      }
      setJobButtonsDisabled(false);
      if (job.status === "complete") {
        setTimeout(() => location.reload(), 450);
      }
    } catch (error) {
      sessionStorage.removeItem(jobStorageKey);
      setJobButtonsDisabled(false);
      renderJob({
        label: "Workflow failed",
        completed: 0,
        total: 1,
        current: "Failed",
        status: "failed",
        error: error.message,
      });
    }
  }

  document.querySelectorAll(".background-job-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await flushPendingAutosaves();
      } catch (_error) {
        return;
      }
      setJobButtonsDisabled(true);
      renderJob({
        label: event.submitter?.textContent.trim() || "Working",
        completed: 0,
        total: 1,
        current: "Starting…",
        status: "running",
      });
      try {
        const response = await fetch(form.action, {
          method: form.method,
          body: new FormData(form),
        });
        if (!response.ok) throw new Error(`Could not start (${response.status})`);
        const job = await response.json();
        sessionStorage.setItem(jobStorageKey, job.job_id);
        if (form.matches(".generate-batch-form")) {
          sessionStorage.setItem(generationJobStorageKey, job.job_id);
        }
        renderJob(job);
        pollJob(job.job_id);
      } catch (error) {
        setJobButtonsDisabled(false);
        renderJob({
          label: "Workflow failed",
          completed: 0,
          total: 1,
          current: "Failed",
          status: "failed",
          error: error.message,
        });
      }
    });
  });

  const activeJobId = sessionStorage.getItem(jobStorageKey);
  if (activeJobId) {
    setJobButtonsDisabled(true);
    pollJob(activeJobId);
  }

  async function cancelActiveGenerations() {
    const operations = [...activeGenerationRequests.entries()];
    const generationJobId = sessionStorage.getItem(generationJobStorageKey);
    if (!operations.length && !generationJobId) return false;

    const cancellations = [];
    if (operations.length) {
      cancellations.push(fetch("/generations/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_ids: operations.map(([operationId]) => operationId),
        }),
      }));
    }
    if (generationJobId) {
      cancellations.push(fetch(
        `/jobs/${encodeURIComponent(generationJobId)}/cancel`,
        { method: "POST" },
      ));
    }

    operations.forEach(([, operation]) => {
      operation.controller.abort();
      if (operation.commentId) {
        updateSingleGenerationCard(operation.commentId, "cancelled");
      }
    });
    activeGenerationRequests.clear();
    sessionStorage.removeItem(generationJobStorageKey);
    sessionStorage.removeItem(jobStorageKey);
    setJobButtonsDisabled(false);
    if (generationJobId) {
      renderJob({
        label: "Generate replies",
        completed: 0,
        total: 1,
        current: "Cancelled",
        status: "cancelled",
        error: null,
      });
    }
    showWorkflowToast("Generation cancelled");
    await Promise.allSettled(cancellations);
    return true;
  }

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

  function revealCard(card) {
    const listPanel = card.closest(".inbox-list-panel");
    if (!listPanel) return;
    const toolbar = listPanel.querySelector(".inbox-toolbar");
    const panelBounds = listPanel.getBoundingClientRect();
    const cardBounds = card.getBoundingClientRect();
    const visibleTop = Math.max(
      panelBounds.top,
      toolbar?.getBoundingClientRect().bottom || panelBounds.top,
    );
    if (cardBounds.top < visibleTop) {
      listPanel.scrollTop += cardBounds.top - visibleTop;
    } else if (cardBounds.bottom > panelBounds.bottom) {
      listPanel.scrollTop += cardBounds.bottom - panelBounds.bottom;
    }
  }

  function selectCard(card) {
    flushPendingAutosaves().catch(() => {});
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
    revealCard(card);
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

  function submitDetailAction(selector) {
    const target = detailPanel?.querySelector(selector);
    if (target instanceof HTMLButtonElement && target.form) {
      target.form.requestSubmit(target);
      return;
    }
    if (!(target instanceof HTMLFormElement)) return;
    const submitButton = target.querySelector('button[type="submit"]');
    target.requestSubmit(submitButton || undefined);
  }

  function updateSingleGenerationCard(commentId, state) {
    const card = cards.find((item) => item.dataset.commentId === commentId);
    const progress = card?.querySelector("[data-generation-progress]");
    const label = progress?.querySelector("[data-generation-label]");
    if (!card || !progress) return;

    const failed = state === "failed";
    const cancelled = state === "cancelled";
    card.classList.toggle("is-generating", state === "generating");
    card.classList.toggle("generation-failed", failed);
    progress.hidden = state === "ready" || cancelled;
    if (label) {
      label.textContent = failed ? "Generation failed" : "Generating reply…";
    }

    if (state === "ready") {
      const replyState = card.querySelector(".reply-state");
      if (replyState) {
        replyState.classList.remove("reply-needed", "reply-approved");
        replyState.classList.add("reply-draft");
        replyState.innerHTML = '<span class="reply-state-dot"></span>Draft ready';
      }
    }
  }

  function updateConversationCount() {
    if (!conversationCount) return;
    const count = cards.length;
    conversationCount.dataset.count = String(count);
    conversationCount.textContent = `${count} ${count === 1 ? "conversation" : "conversations"} waiting for you`;
  }

  function showWorkflowToast(message, persistent = false) {
    const toast = document.createElement("div");
    toast.className = "workflow-toast";
    toast.setAttribute("role", "status");
    toast.textContent = message;
    document.body.append(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    if (persistent) {
      toast.setAttribute("role", "alert");
      toast.title = "Click to dismiss";
      toast.addEventListener("click", () => toast.remove());
      return;
    }
    setTimeout(() => {
      toast.classList.remove("visible");
      toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    }, 2800);
  }

  async function dismissCard(card) {
    card.style.height = `${card.getBoundingClientRect().height}px`;
    card.style.minHeight = "0";
    card.getBoundingClientRect();
    card.classList.add("is-dismissing");
    card.style.height = "0";
    await new Promise((resolve) => {
      const fallback = setTimeout(resolve, 350);
      card.addEventListener("transitionend", (event) => {
        if (event.propertyName !== "height") return;
        clearTimeout(fallback);
        resolve();
      }, { once: true });
    });
  }

  async function watchQueuedReply(commentId) {
    try {
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 850));
        const response = await fetch(`/comments/${encodeURIComponent(commentId)}/reply-status`);
        if (!response.ok) throw new Error(`Reply status unavailable (${response.status})`);
        const result = await response.json();
        if (result.status === "sent") {
          showWorkflowToast("Reply posted successfully");
          return;
        }
        if (result.status === "failed") {
          showWorkflowToast(`Reply failed: ${result.error || "please check the comment before retrying"}. Check the platform before retrying.`, true);
          return;
        }
      }
    } catch (error) {
      showWorkflowToast(`Could not confirm reply: ${error.message}. Check the platform before retrying.`, true);
    }
  }

  function showCompletedInbox() {
    if (!detailPanel) return;
    const empty = document.createElement("div");
    empty.className = "detail-empty";
    const mark = document.createElement("span");
    mark.className = "empty-state-mark";
    mark.textContent = "✓";
    const heading = document.createElement("h2");
    heading.textContent = "You're all caught up";
    const copy = document.createElement("p");
    copy.textContent = "No conversations need your attention right now.";
    empty.append(mark, heading, copy);
    detailPanel.replaceChildren(empty);
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

    try {
      await flushPendingAutosaves();
    } catch (_error) {
      return;
    }

    const submitter = event.submitter;
    const activeCommentId = form.closest(".conversation-shell")?.dataset.detailId;
    const cardIndexById = cards.findIndex(
      (card) => card.dataset.commentId === activeCommentId,
    );
    const activeCardIndex = cardIndexById >= 0
      ? cardIndexById
      : selectedCardIndex();
    let progressTimer = null;
    let generatingCommentId = null;
    let generationOperationId = null;
    let generationController = null;
    const feedbackButtons = submitter ? [submitter] : [];
    if (submitter?.matches(".shortcut-approve-post")) {
      const visiblePostButton = form.querySelector(".editor-actions .primary");
      if (visiblePostButton) feedbackButtons.push(visiblePostButton);
    }
    for (const button of feedbackButtons) {
      button.disabled = true;
      button.classList.add("is-working");
      button.dataset.label = button.textContent;
      button.textContent = button.matches(".post-button")
        ? "Posting…"
        : "Working…";
    }

    try {
      const actionOverride = submitter?.getAttribute("formaction");
      const methodOverride = submitter?.getAttribute("formmethod");
      const action = actionOverride
        ? new URL(actionOverride, document.baseURI).href
        : form.action;
      const method = methodOverride || form.method;
      const actionPath = new URL(action, document.baseURI).pathname;
      const isIgnoreAction = actionPath.endsWith("/ignore");
      const isPostAction = actionPath.endsWith("/reply")
        || actionPath.endsWith("/approve-and-post");
      const isGenerateAction = actionPath.endsWith("/generate-reply");
      if (isGenerateAction && activeCommentId) {
        generatingCommentId = activeCommentId;
        generationOperationId = createGenerationOperationId();
        generationController = new AbortController();
        activeGenerationRequests.set(generationOperationId, {
          controller: generationController,
          commentId: activeCommentId,
        });
        updateSingleGenerationCard(activeCommentId, "generating");
      }
      if (isGenerateAction && submitter) {
        let dotCount = 1;
        const updateProgress = () => {
          submitter.textContent = `Regenerating${".".repeat(dotCount)}`;
          dotCount = dotCount === 3 ? 1 : dotCount + 1;
        };
        submitter.classList.add("is-regenerating");
        updateProgress();
        progressTimer = setInterval(updateProgress, 420);
      }
      const headers = isPostAction ? { Accept: "application/json" } : {};
      if (generationOperationId) {
        headers["X-Wingman-Generation-Id"] = generationOperationId;
      }
      const response = await fetch(action, {
        method,
        body: new FormData(form),
        headers,
        signal: generationController?.signal,
      });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      if (isPostAction && response.status === 202) {
        showWorkflowToast("Reply on its way");
        watchQueuedReply(activeCommentId);
        if (activeCardIndex >= 0) {
          const completedCard = cards[activeCardIndex];
          await dismissCard(completedCard);
          cards.splice(activeCardIndex, 1);
          completedCard.remove();
          updateConversationCount();
          if (cards.length) selectCard(cards[Math.min(activeCardIndex, cards.length - 1)]);
          else showCompletedInbox();
        }
        return;
      }
      const finalResponse = await fetch(response.url);
      if (!finalResponse.ok) throw new Error(`Refresh failed (${finalResponse.status})`);
      const documentText = await finalResponse.text();
      const parsed = new DOMParser().parseFromString(documentText, "text/html");
      const refreshed = parsed.querySelector(".conversation-shell");
      if (!refreshed || !detailPanel) throw new Error("Updated conversation was unavailable");
      if (progressTimer) clearInterval(progressTimer);
      if (isGenerateAction && generatingCommentId) {
        const failed = refreshed.querySelector(".reply-workspace > .form-error");
        updateSingleGenerationCard(
          generatingCommentId,
          failed ? "failed" : "ready",
        );
      }
      const refreshedTemplate = activeCommentId
        ? document.querySelector(`#detail-${CSS.escape(activeCommentId)}`)
        : null;
      if (refreshedTemplate instanceof HTMLTemplateElement) {
        refreshedTemplate.content.replaceChildren(refreshed.cloneNode(true));
      }
      const selectedCommentId = cards.find((card) =>
        card.classList.contains("selected"),
      )?.dataset.commentId;
      const originatingCommentIsVisible = !activeCommentId
        || !selectedCommentId
        || selectedCommentId === activeCommentId;
      const completedPost = isPostAction && refreshed.querySelector(".reply-sent");
      if ((isIgnoreAction || completedPost) && activeCardIndex >= 0) {
        const completedCard = cards[activeCardIndex];
        if (completedPost) showWorkflowToast("Reply posted successfully");
        if (isIgnoreAction) showWorkflowToast("Conversation hidden");
        await dismissCard(completedCard);
        cards.splice(activeCardIndex, 1);
        completedCard.remove();
        updateConversationCount();
        if (cards.length) {
          selectCard(cards[Math.min(activeCardIndex, cards.length - 1)]);
        } else {
          showCompletedInbox();
        }
      } else if (originatingCommentIsVisible) {
        detailPanel.replaceChildren(refreshed);
      }
      if (generationOperationId) {
        activeGenerationRequests.delete(generationOperationId);
      }
    } catch (error) {
      if (progressTimer) clearInterval(progressTimer);
      if (generationOperationId) {
        activeGenerationRequests.delete(generationOperationId);
      }
      const wasCancelled = error.name === "AbortError";
      if (generatingCommentId) {
        updateSingleGenerationCard(
          generatingCommentId,
          wasCancelled ? "cancelled" : "failed",
        );
      }
      if (wasCancelled) {
        for (const button of feedbackButtons) {
          button.disabled = false;
          button.classList.remove("is-working", "is-regenerating");
          button.textContent = button.dataset.label;
        }
        return;
      }
      const message = document.createElement("p");
      message.className = "form-error";
      message.textContent = error.message;
      form.prepend(message);
      for (const button of feedbackButtons) {
        button.disabled = false;
        button.classList.remove("is-working", "is-regenerating");
        button.textContent = button.dataset.label;
      }
    }
  });

  document.addEventListener("input", (event) => {
    const textarea = event.target.closest(".reply-editor textarea");
    if (!textarea) return;
    if (textarea.dataset.autosaveUrl) {
      mirrorTextareaInTemplate(textarea);
      scheduleAutosave(textarea);
    }
    const form = textarea.closest(".reply-editor");
    const learnButton = form?.querySelector(".learn-button");
    if (!learnButton) return;
    const original = textarea.dataset.originalDraft?.trim() || "";
    learnButton.disabled = !original || textarea.value.trim() === original;
  });

  document.addEventListener("focusout", (event) => {
    const textarea = event.target.closest(".reply-editor textarea[data-autosave-url]");
    if (!textarea || !autosaveTimers.has(textarea)) return;
    const timer = autosaveTimers.get(textarea);
    clearTimeout(timer);
    autosaveTimers.delete(textarea);
    persistTextarea(textarea).catch(() => {});
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const isEditing = target instanceof HTMLTextAreaElement
      || target instanceof HTMLInputElement
      || target instanceof HTMLSelectElement
      || target?.isContentEditable;

    if (event.key.toLowerCase() === "x" && event.ctrlKey) {
      if (activeGenerationRequests.size
          || sessionStorage.getItem(generationJobStorageKey)) {
        event.preventDefault();
        cancelActiveGenerations();
      }
      return;
    }

    if (event.key === "Escape" && isEditing) {
      event.preventDefault();
      target.blur();
      return;
    }

    if (event.key === "Enter" && event.ctrlKey) {
      const form = detailPanel?.querySelector(".reply-editor");
      const approveAndPost = form?.querySelector(".shortcut-approve-post");
      if (form && approveAndPost && !approveAndPost.disabled) {
        event.preventDefault();
        form.requestSubmit(approveAndPost);
        return;
      }
    }

    if (event.key.toLowerCase() === "g" && event.ctrlKey) {
      const form = document.querySelector(".generate-batch-form");
      const generateButton = form?.querySelector('button[type="submit"]');
      if (form && generateButton && !generateButton.disabled) {
        event.preventDefault();
        form.requestSubmit(generateButton);
      }
      return;
    }

    if (isEditing || event.ctrlKey || event.metaKey || event.altKey) return;

    if (event.key === "j") {
      event.preventDefault();
      moveSelection(1);
    } else if (event.key === "k") {
      event.preventDefault();
      moveSelection(-1);
    } else if (event.key === "r" || event.key === "g") {
      event.preventDefault();
      submitDetailAction(
        'form[action$="/generate-reply"], button[formaction$="/generate-reply"]',
      );
    } else if (event.key === "e" || event.key === "a") {
      const editor = detailPanel?.querySelector(".reply-editor textarea");
      if (editor) {
        event.preventDefault();
        editor.focus();
        editor.setSelectionRange(editor.value.length, editor.value.length);
      }
    } else if (event.key === "o" || event.key === "O") {
      const commentLink = detailPanel?.querySelector(".video-link");
      if (commentLink) {
        event.preventDefault();
        commentLink.click();
      }
    } else if (event.key === "h") {
      event.preventDefault();
      submitDetailAction('form[action$="/ignore"]');
    }
  });
})();
