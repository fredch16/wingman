(() => {
  const form = document.querySelector('#automation-create-form');
  if (!form) return;

  const search = form.querySelector('#automation-video-search');
  const videoOptions = [...form.querySelectorAll('.video-option')];
  const instagramFields = form.querySelector('[data-instagram-only]');
  const initialDm = form.elements.namedItem('initial_dm');
  const followupDm = form.elements.namedItem('followup_dm');
  const publicReply = form.elements.namedItem('reply_options');
  const keywords = form.elements.namedItem('keywords');
  const exactMatch = form.elements.namedItem('match_type');
  const buttonLabel = form.elements.namedItem('opt_in_button_label');

  function refreshMode(targetForm) {
    const mode = targetForm.querySelector('input[name="mode"]:checked, select[name="mode"]')?.value || 'prefill';
    const isInstagram = mode === 'instagram_dm';
    const dmFields = targetForm.querySelector('[data-instagram-only]');
    if (dmFields) dmFields.hidden = !isInstagram;
    for (const name of ['initial_dm', 'followup_dm', 'opt_in_button_label']) {
      const input = targetForm.elements.namedItem(name);
      if (input) input.required = isInstagram;
    }
    const title = targetForm.querySelector('[data-public-reply-label]');
    if (title) title.textContent = isInstagram ? 'Public comment replies' : 'Inbox replies to prefill';
    const links = targetForm.querySelector('[data-link-editor]');
    if (links) refreshLinkEditor(links);
    return isInstagram;
  }

  function refreshLinkEditor(editor) {
    const rows = [...editor.querySelectorAll('.automation-link-row')];
    editor.querySelector('[data-add-link]').disabled = rows.length >= 3;
    for (const row of rows) {
      row.querySelector('[data-remove-link]').disabled = rows.length === 1;
      const label = row.querySelector('[name="link_label[]"]');
      const url = row.querySelector('[name="link_url[]"]');
      const active = !editor.closest('[data-instagram-only]')?.hidden;
      label.required = active && Boolean(url.value.trim());
      url.required = active && Boolean(label.value.trim());
    }
  }

  for (const editor of document.querySelectorAll('[data-link-editor]')) {
    editor.addEventListener('click', (event) => {
      if (event.target.closest('[data-add-link]')) {
        const first = editor.querySelector('.automation-link-row');
        const copy = first.cloneNode(true);
        copy.querySelectorAll('input').forEach((input) => { input.value = ''; input.required = false; });
        editor.querySelector('[data-link-rows]').append(copy);
      } else if (event.target.closest('[data-remove-link]')) {
        event.target.closest('.automation-link-row').remove();
      }
      refreshLinkEditor(editor);
      if (editor.closest('#automation-create-form')) refresh();
    });
    editor.addEventListener('input', () => {
      refreshLinkEditor(editor);
      if (editor.closest('#automation-create-form')) refresh();
    });
    refreshLinkEditor(editor);
  }

  function refresh() {
    const isInstagram = refreshMode(form);
    const query = search.value.trim().toLocaleLowerCase();
    let visibleVideos = 0;

    instagramFields.hidden = !isInstagram;
    form.querySelector('[data-public-reply-help]').textContent = isInstagram
      ? 'One complete reply per line. Wingman rotates through them. Posted after the opening DM succeeds.'
      : 'One complete reply per line. Wingman prepares the first match for your review.';

    for (const option of videoOptions) {
      const visible = (!isInstagram || option.dataset.platform === 'instagram')
        && (`${option.dataset.title} ${option.dataset.platform}`).toLocaleLowerCase().includes(query);
      option.hidden = !visible;
      if (!visible) option.querySelector('input').checked = false;
      if (visible) visibleVideos += 1;
    }
    form.querySelector('#automation-video-empty').hidden = visibleVideos !== 0 || videoOptions.length === 0;

    const selectedVideo = form.querySelector('input[name="video_id"]:checked')?.closest('.video-option');
    form.querySelector('[data-preview-title]').textContent = selectedVideo?.querySelector('strong')?.textContent || 'Choose a post';
    const words = keywords.value.split(',').map((word) => word.trim()).filter(Boolean);
    form.querySelector('[data-preview-trigger]').textContent = words.length
      ? `When a comment ${exactMatch.value === 'exact' ? 'matches exactly' : 'contains'}: ${words.join(' · ')}`
      : 'A matching comment starts the workflow.';
    form.querySelector('[data-preview-instagram]').hidden = !isInstagram;
    form.querySelector('[data-preview-prefill]').hidden = isInstagram;
    form.querySelector('[data-preview-initial]').textContent = initialDm.value.trim() || 'Ask if they want the resource.';
    const replies = publicReply.value.split('\n').map((reply) => reply.trim()).filter(Boolean);
    form.querySelector('[data-preview-public]').textContent = replies[0] || 'Acknowledge the comment after the DM succeeds.';
    form.querySelector('[data-preview-followup]').textContent = followupDm.value.trim() || 'Send the resource link.';
    form.querySelector('[data-preview-button-label]').textContent = buttonLabel.value.trim() || 'Yes please';
    form.querySelector('[data-preview-links]').textContent = [...form.querySelectorAll('.automation-link-row [name="link_label[]"]')]
      .map((input) => input.value.trim()).filter(Boolean).join(' · ');
    form.querySelector('[data-preview-prefill-text]').textContent = replies[0] || 'Wingman prepares a reply for your review.';
  }

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  refresh();

  for (const editForm of document.querySelectorAll('.automation-edit-form')) {
    editForm.addEventListener('change', () => refreshMode(editForm));
    refreshMode(editForm);
  }

  const filterButtons = [...document.querySelectorAll('[data-automation-filter]')];
  for (const button of filterButtons) {
    button.addEventListener('click', () => {
      const selected = button.dataset.automationFilter;
      for (const item of filterButtons) {
        item.classList.toggle('active', item === button);
        item.setAttribute('aria-pressed', String(item === button));
      }
      for (const card of document.querySelectorAll('[data-automation-platform]')) {
        card.hidden = selected !== 'all' && card.dataset.automationPlatform !== selected;
      }
    });
  }

  for (const deleteForm of document.querySelectorAll('[data-confirm-delete]')) {
    deleteForm.addEventListener('submit', (event) => {
      if (!window.confirm('Delete this automation? This cannot be undone.')) event.preventDefault();
    });
  }
})();
