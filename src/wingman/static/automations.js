(() => {
  const form = document.querySelector('#automation-create-form');
  if (!form) return;

  const search = form.querySelector('#automation-video-search');
  const videoOptions = [...form.querySelectorAll('.video-option')];
  const instagramFields = form.querySelector('[data-instagram-only]');
  const initialDm = form.elements.namedItem('initial_dm');
  const followupDm = form.elements.namedItem('followup_dm');
  const publicReply = form.elements.namedItem('default_reply');
  const keywords = form.elements.namedItem('keywords');
  const exactMatch = form.elements.namedItem('match_type');
  const buttonLabel = form.elements.namedItem('opt_in_button_label');
  const followupLinks = form.elements.namedItem('followup_links');

  function refresh() {
    const mode = form.querySelector('input[name="mode"]:checked')?.value || 'prefill';
    const isInstagram = mode === 'instagram_dm';
    const query = search.value.trim().toLocaleLowerCase();
    let visibleVideos = 0;

    instagramFields.hidden = !isInstagram;
    initialDm.required = isInstagram;
    followupDm.required = isInstagram;
    form.querySelector('[data-public-reply-label]').textContent = isInstagram ? 'Public comment reply' : 'Inbox reply to prefill';
    form.querySelector('[data-public-reply-help]').textContent = isInstagram
      ? 'Posted only after Instagram accepts the opening DM. Do not say the resource has already been sent.'
      : 'Placed in the reply editor for your review. Nothing is posted automatically.';

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
    form.querySelector('[data-preview-public]').textContent = publicReply.value.trim() || 'Acknowledge the comment after the DM succeeds.';
    form.querySelector('[data-preview-followup]').textContent = followupDm.value.trim() || 'Send the resource link.';
    form.querySelector('[data-preview-button-label]').textContent = buttonLabel.value.trim() || 'Yes please';
    form.querySelector('[data-preview-links]').textContent = followupLinks.value.split('\n')
      .map((line) => line.split('|')[0].trim()).filter(Boolean).join(' · ');
    form.querySelector('[data-preview-prefill-text]').textContent = publicReply.value.trim() || 'Wingman prepares a reply for your review.';
  }

  form.addEventListener('input', refresh);
  form.addEventListener('change', refresh);
  refresh();

  for (const deleteForm of document.querySelectorAll('[data-confirm-delete]')) {
    deleteForm.addEventListener('submit', (event) => {
      if (!window.confirm('Delete this automation? This cannot be undone.')) event.preventDefault();
    });
  }
})();
