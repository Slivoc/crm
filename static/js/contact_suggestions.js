document.addEventListener('DOMContentLoaded', () => {
  const salespersonIdInput = document.getElementById('salesPersonId');
  if (!salespersonIdInput) return;

  const salespersonId = salespersonIdInput.value;
  const container = document.getElementById('suggestionsContainer');
  const summary = document.getElementById('suggestionsSummary');
  const loadingState = document.getElementById('suggestionsLoading');
  const refreshBtn = document.getElementById('refreshSuggestions');
  const loadMoreBtn = document.getElementById('loadMoreSuggestions');
  const noContactTargets = document.getElementById('noContactTargets');
  const noContactTargetsCard = document.getElementById('noContactTargetsCard');
  const noContactTargetsCount = document.getElementById('noContactTargetsCount');

  let currentSuggestions = [];
  let currentNoContactTargets = [];
  let templates = [];
  let templatesLoaded = false;
  let totalAvailable = null;
  const pageSize = 8;

  const countryTemplateKey = 'contactSuggestionsTemplateByCountry';
  let countryTemplateDefaults = {};
  try {
    countryTemplateDefaults = JSON.parse(localStorage.getItem(countryTemplateKey) || '{}');
  } catch (err) {
    countryTemplateDefaults = {};
  }

  refreshBtn?.addEventListener('click', () => {
    refreshBtn.disabled = true;
    refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing';
    loadSuggestions().finally(() => {
      refreshBtn.disabled = false;
      refreshBtn.innerHTML = '<i class="bi bi-stars"></i> Refresh Suggestions';
    });
  });

  const formatCurrency = (value) => {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return 'GBP 0';
    return `GBP ${num.toLocaleString('en-GB', { maximumFractionDigits: 0 })}`;
  };

  const escapeHtml = (value) => {
    if (value === undefined || value === null) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  };

  const stripHtml = (value) => {
    if (!value) return '';
    const holder = document.createElement('div');
    holder.innerHTML = value;
    return holder.textContent || holder.innerText || '';
  };

  const sanitizeGraphHtml = (html) => {
    if (!html) return '';
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const removeSelectors = ['script', 'style', 'meta', 'link', 'head', 'title'];
    removeSelectors.forEach((selector) => {
      doc.querySelectorAll(selector).forEach((el) => el.remove());
    });
    const walker = document.createTreeWalker(doc, NodeFilter.SHOW_COMMENT);
    const comments = [];
    while (walker.nextNode()) {
      comments.push(walker.currentNode);
    }
    comments.forEach((node) => node.parentNode?.removeChild(node));
    doc.querySelectorAll('*').forEach((el) => {
      el.removeAttribute('style');
      el.removeAttribute('class');
      el.removeAttribute('id');
    });
    return doc.body ? doc.body.innerHTML : '';
  };

  const renderGraphMessage = (message, output) => {
    const subject = message.subject || '';
    const fromEmail = message.from?.emailAddress?.address || '';
    const received = message.receivedDateTime
      ? new Date(message.receivedDateTime).toLocaleString()
      : '';
    const headerLines = [];
    if (subject) headerLines.push(`Subject: ${subject}`);
    if (fromEmail) headerLines.push(`From: ${fromEmail}`);
    if (received) headerLines.push(`Date: ${received}`);

    const rawBody = message.body?.content || message.bodyPreview || '';
    const sanitized = sanitizeGraphHtml(rawBody);
    const textBody = stripHtml(sanitized || rawBody);

    const headerHtml = headerLines.length
      ? `<div class="graph-email-meta">${escapeHtml(headerLines.join(' · '))}</div>`
      : '';
    const bodyHtml = sanitized || escapeHtml(textBody).replace(/\n/g, '<br>');
    output.innerHTML = `${headerHtml}<div class="graph-email-body">${bodyHtml}</div>`;
    output.setAttribute('data-text', textBody);
  };

  const renderEmailTimeline = (emails, container, onSelect) => {
    if (!emails || !emails.length) {
      container.innerHTML = '<div class="text-muted small">No emails found. Click "Scan" to search for older emails.</div>';
      return;
    }
    const items = emails.map((email, idx) => {
      const direction = email.direction === 'sent' ? 'Sent' : 'Received';
      const badgeClass = email.direction === 'sent' ? 'bg-success' : 'bg-info';
      const date = email.timestamp ? new Date(email.timestamp).toLocaleDateString() : '';
      return `
        <div class="email-timeline-item border-bottom py-1" data-index="${idx}">
          <div class="d-flex justify-content-between align-items-center">
            <span class="badge ${badgeClass} badge-sm">${direction}</span>
            <small class="text-muted">${escapeHtml(date)}</small>
          </div>
          <div class="fw-semibold small mt-1 text-truncate">${escapeHtml(email.subject || '(No subject)')}</div>
        </div>
      `;
    });
    container.innerHTML = items.join('');

    container.querySelectorAll('.email-timeline-item').forEach(item => {
      item.style.cursor = 'pointer';
      item.addEventListener('click', () => {
        container.querySelectorAll('.email-timeline-item').forEach(i => i.classList.remove('active'));
        item.classList.add('active');
        const idx = parseInt(item.dataset.index, 10);
        if (onSelect && emails[idx]) {
          onSelect(emails[idx]);
        }
      });
    });
  };

  const formatScanTime = (timestamp) => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now - date;
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'Scanned today';
    if (diffDays === 1) return 'Scanned yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  const formatNumber = (value) => {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0';
    return num.toLocaleString('en-GB');
  };

  const relativeDate = (iso) => {
    if (!iso) return 'No contact recorded';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    const diffMs = Date.now() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays < 1) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return `${diffDays} days ago`;
  };

  const copyToClipboard = async (text, trigger) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      if (trigger) {
        const original = trigger.innerHTML;
        trigger.innerHTML = '<i class="bi bi-clipboard-check"></i> Copied';
        trigger.classList.remove('btn-outline-secondary');
        trigger.classList.add('btn-success');
        setTimeout(() => {
          trigger.innerHTML = original;
          trigger.classList.remove('btn-success');
          trigger.classList.add('btn-outline-secondary');
        }, 1200);
      }
    } catch (err) {
      console.error('Clipboard error', err);
    }
  };

  const getTemplateIdForSuggestion = (suggestion) => {
    const explicit = suggestion.template_id || '';
    if (explicit) return explicit;
    const country = (suggestion.country || '').trim();
    if (!country) return '';
    return countryTemplateDefaults[country] || '';
  };

  const renderSummary = (data) => {
    if (!currentSuggestions.length) {
      summary.innerHTML = '';
      return;
    }
    const total = Number.isFinite(data?.total_available) ? data.total_available : totalAvailable;
    const shownCount = currentSuggestions.length;
    const generatedAt = data.generated_at
      ? new Date(data.generated_at).toLocaleString()
      : 'just now';
    summary.innerHTML = `
      <div class="alert alert-secondary d-flex justify-content-between align-items-center">
        <div>
          <strong>${shownCount}</strong>${Number.isFinite(total) ? ` of ${total}` : ''} suggestion${shownCount === 1 ? '' : 's'} shown.
        </div>
        <small class="text-muted">Generated at ${generatedAt}</small>
      </div>
    `;
  };

  const renderNews = (newsItems) => {
    if (!newsItems || !newsItems.length) return '<span class="text-muted">No recent news found</span>';
    return newsItems.slice(0, 2).map((item) => `
      <div class="news-chip mb-1">
        <i class="bi bi-newspaper"></i>
        <span>${escapeHtml(item.headline || 'News item')}</span>
      </div>
    `).join('');
  };

  const renderTemplateOptions = (selectedId) => {
    const options = [`<option value="">No template</option>`];
    if (!templatesLoaded) {
      options.push('<option value="" disabled>Loading templates...</option>');
    } else if (!templates.length) {
      options.push('<option value="" disabled>No templates available</option>');
    } else {
      templates.forEach((template) => {
        const id = String(template.id || '');
        const selected = id && id === String(selectedId) ? 'selected' : '';
        options.push(`<option value="${escapeHtml(id)}" ${selected}>${escapeHtml(template.name || 'Unnamed template')}</option>`);
      });
    }
    return options.join('');
  };

  const getSelectedContact = (suggestion) => {
    const contacts = suggestion.contacts || [];
    if (!contacts.length) return null;
    const selectedId = suggestion.selected_contact_id;
    if (selectedId) {
      const match = contacts.find((contact) => String(contact.id) === String(selectedId));
      if (match) return match;
    }
    return contacts[0];
  };

  const renderNoContactTargets = (targets) => {
    if (!noContactTargets || !noContactTargetsCard) return;

    if (!targets || !targets.length) {
      noContactTargetsCard.classList.add('d-none');
      noContactTargets.innerHTML = '';
      if (noContactTargetsCount) noContactTargetsCount.textContent = '';
      return;
    }

    noContactTargetsCard.classList.remove('d-none');
    if (noContactTargetsCount) {
      noContactTargetsCount.textContent = `${targets.length} target${targets.length === 1 ? '' : 's'}`;
    }

    noContactTargets.innerHTML = targets.map((target) => `
      <div class="col-12 col-md-6 col-lg-4">
        <div class="border rounded p-3 h-100 d-flex flex-column">
          <div class="fw-semibold">${escapeHtml(target.customer_name || 'Customer')}</div>
          <div class="text-muted small mb-2">${escapeHtml(target.status || 'No spend')}${target.country ? ` | ${escapeHtml(target.country)}` : ''}</div>
          <div class="d-flex flex-wrap gap-2 mb-2">
            <span class="metric-pill"><i class="bi bi-cash-stack"></i> ${formatCurrency(target.estimated_revenue)}</span>
            <span class="metric-pill"><i class="bi bi-truck"></i> Fleet ${formatNumber(target.fleet_size)}</span>
          </div>
          <div class="text-muted small mb-3">Score ${escapeHtml(target.score ?? '-')}</div>
          <div class="mt-auto d-flex gap-2">
            <button class="btn btn-sm btn-primary" data-open-lead-finder="${escapeHtml(target.customer_id)}" data-edit-url="/customers/${escapeHtml(target.customer_id)}/edit">
              <i class="bi bi-person-plus"></i> Find leads
            </button>
            <a class="btn btn-sm btn-outline-secondary" href="/customers/${escapeHtml(target.customer_id)}/edit">Edit</a>
          </div>
        </div>
      </div>
    `).join('');

    noContactTargets.querySelectorAll('[data-open-lead-finder]').forEach((button) => {
      button.addEventListener('click', () => {
        const customerId = button.getAttribute('data-open-lead-finder');
        const editUrl = button.getAttribute('data-edit-url');
        if (window.showApolloLeadFinder && customerId) {
          window.showApolloLeadFinder(customerId);
          return;
        }
        if (editUrl) {
          window.location.href = editUrl;
        }
      });
    });
  };

  const renderSuggestionCard = (suggestion) => {
    const lastContact = suggestion.last_contact;
    const lastEmail = suggestion.last_email;
    const graphEmail = suggestion.last_graph_email;
    const suggestedEmail = suggestion.suggested_email || {};
    const newsItems = suggestion.news_items || [];
    const contacts = suggestion.contacts || [];
    const selectedContact = getSelectedContact(suggestion);
    const hasSuggestedEmail = Boolean(suggestedEmail?.subject || suggestedEmail?.body);
    const aiGenerated = Boolean(suggestion.ai_generated || hasSuggestedEmail || newsItems.length);
    const aiError = suggestion.ai_error || '';
    const contactAge = lastContact?.date ? relativeDate(lastContact.date) : 'No contact recorded';
    const lastEmailSubject = lastEmail?.subject || lastEmail?.notes || '';
    const lastEmailPreview = lastEmail?.preview || '';
    const latestUpdate = suggestion.latest_update || '';
    const statusLabel = suggestion.status || 'No spend';
    const countryLabel = suggestion.country ? ` | ${suggestion.country}` : '';
    const templateId = getTemplateIdForSuggestion(suggestion);
    const graphEmailAge = graphEmail?.date ? relativeDate(graphEmail.date) : '';
    const graphMessageId = graphEmail?.message_id;

    const scoreBadgeClass = suggestion.score >= 95
      ? 'bg-danger'
      : suggestion.score >= 80
        ? 'bg-warning text-dark'
        : 'bg-info text-dark';

    return `
      <div class="col-12 col-lg-6">
        <div class="card shadow-sm suggestion-card">
          <div class="card-body d-flex flex-column h-100">
            <div class="d-flex justify-content-between align-items-start mb-2">
              <div>
                <h5 class="mb-0">${escapeHtml(suggestion.customer_name || 'Customer')}</h5>
                <div class="text-muted small">${escapeHtml(statusLabel)} | Last touch: ${escapeHtml(contactAge)}${escapeHtml(countryLabel)}</div>
              </div>
              <span class="badge ${scoreBadgeClass} suggestion-score">${suggestion.score}</span>
            </div>

            <div class="d-flex flex-wrap gap-2 mb-2">
              <span class="metric-pill"><i class="bi bi-cash-stack"></i> ${formatCurrency(suggestion.estimated_revenue)}</span>
              <span class="metric-pill"><i class="bi bi-truck"></i> Fleet ${formatNumber(suggestion.fleet_size)}</span>
              <span class="metric-pill"><i class="bi bi-clock-history"></i> ${
                Number.isFinite(suggestion.score_breakdown?.days_since_contact)
                  ? `${suggestion.score_breakdown.days_since_contact} days stale`
                  : 'No communications yet'
              }</span>
            </div>

            <div class="mb-2">
              <label class="form-label small fw-semibold" for="contact-${suggestion.customer_id}">Contact to email</label>
              ${contacts.length ? `
                <select class="form-select form-select-sm" id="contact-${suggestion.customer_id}" data-contact-select data-customer-id="${suggestion.customer_id}">
                  ${contacts.map((contact) => {
                    const id = String(contact.id || '');
                    const selected = selectedContact && String(selectedContact.id) === id ? 'selected' : '';
                    const label = [contact.name, contact.email].filter(Boolean).join(' • ');
                    return `<option value="${escapeHtml(id)}" ${selected}>${escapeHtml(label)}</option>`;
                  }).join('')}
                </select>
                ${selectedContact?.email ? `
                  <div class="small text-muted mt-1">
                    ${escapeHtml(selectedContact.email)}
                    <a class="ms-2" href="mailto:${escapeHtml(selectedContact.email)}">Open email</a>
                    <button class="btn btn-sm btn-link p-0 ms-2" data-copy-email="${escapeHtml(selectedContact.email)}">Copy</button>
                  </div>
                ` : ''}
              ` : '<div class="text-muted small">No contacts with email</div>'}
            </div>

            <div class="mb-2">
              <label class="form-label small fw-semibold" for="template-${suggestion.customer_id}">AI template</label>
              <select class="form-select form-select-sm" id="template-${suggestion.customer_id}" data-template-select data-customer-id="${suggestion.customer_id}">
                ${renderTemplateOptions(templateId)}
              </select>
            </div>

            <div class="d-flex justify-content-between align-items-center mb-2">
              <button class="btn btn-sm btn-outline-primary" data-generate-ai data-customer-id="${suggestion.customer_id}">
                ${hasSuggestedEmail ? 'Regenerate AI draft' : 'Generate AI draft'}
              </button>
              <span class="text-muted small">${aiGenerated ? 'AI draft ready' : 'AI draft not generated'}</span>
            </div>

            ${aiError ? `<div class="alert alert-warning py-1 mb-2 small">${escapeHtml(aiError)}</div>` : ''}

            <div class="mb-2">
              ${aiGenerated
                ? renderNews(newsItems)
                : '<div class="text-muted small">Generate AI draft to pull news.</div>'}
            </div>

            <div class="mb-2">
              <div class="fw-semibold">Latest update</div>
              ${latestUpdate
                ? `<div class="small">${escapeHtml(latestUpdate)}</div>`
                : '<div class="text-muted small">No updates yet</div>'}
            </div>

            <div class="mb-2">
              <div class="fw-semibold">Latest email</div>
              ${lastEmail
                ? `<div class="small text-muted">${escapeHtml(lastEmailSubject || 'Email captured')}</div>
                   ${lastEmailPreview ? `<div class="small">${escapeHtml(lastEmailPreview)}</div>` : ''}`
                : '<div class="text-muted small">No email captured yet</div>'}
              ${graphEmail ? `
                <div class="small mt-2"><span class="fw-semibold">Graph:</span> ${escapeHtml(graphEmail.subject || 'Email')} ${graphEmailAge ? `<span class="text-muted">(${escapeHtml(graphEmailAge)})</span>` : ''}</div>
                ${graphEmail.preview ? `<div class="small">${escapeHtml(graphEmail.preview)}</div>` : ''}
              ` : ''}
              ${selectedContact?.email ? `
                <div class="mt-2">
                  <div class="d-flex gap-2 align-items-center mb-2">
                    <button class="btn btn-sm btn-outline-secondary" data-load-timeline data-email="${escapeHtml(selectedContact.email)}" data-customer-id="${suggestion.customer_id}">
                      <i class="bi bi-envelope"></i> Load
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" data-scan-emails data-email="${escapeHtml(selectedContact.email)}" data-customer-id="${suggestion.customer_id}" title="Scan for older emails">
                      <i class="bi bi-arrow-repeat"></i> Scan
                    </button>
                    <small class="text-muted" data-scan-time></small>
                  </div>
                  <div class="email-timeline small" data-email-timeline style="max-height: 150px; overflow-y: auto;">
                    <div class="text-muted small">Click "Load" to view emails.</div>
                  </div>
                  <div class="graph-email-content small mt-2 d-none" data-graph-content></div>
                </div>
              ` : ''}
            </div>

            ${hasSuggestedEmail ? `
              <div class="mb-2">
                <div class="fw-semibold d-flex justify-content-between align-items-center">
                  <span>Suggested subject</span>
                  <button class="btn btn-sm btn-outline-secondary" data-copy-subject>Copy</button>
                </div>
                <div class="form-control bg-white">${escapeHtml(suggestedEmail.subject || 'Subject not available')}</div>
              </div>

              <div class="mb-3">
                <div class="fw-semibold d-flex justify-content-between align-items-center">
                  <span>Suggested body</span>
                  <button class="btn btn-sm btn-outline-secondary" data-copy-body>Copy</button>
                </div>
                <div class="suggestion-body">${escapeHtml(suggestedEmail.body || 'No draft available').replace(/\n/g, '<br>')}</div>
              </div>
            ` : `
              <div class="text-muted small mb-3">No AI draft yet.</div>
            `}

            <div class="mt-auto d-flex justify-content-between align-items-center text-muted small">
              <span>${hasSuggestedEmail ? `Draft source: ${escapeHtml(suggestedEmail.source || 'openai')}` : ''}</span>
              <span>Score drivers: rev ${escapeHtml(suggestion.score_breakdown?.revenue_component ?? '-')}, fleet ${escapeHtml(suggestion.score_breakdown?.fleet_component ?? '-')}, recency ${escapeHtml(suggestion.score_breakdown?.recency_component ?? '-')}</span>
            </div>
          </div>
        </div>
      </div>
    `;
  };

  const renderSuggestions = (suggestions) => {
    container.innerHTML = '';
    if (!suggestions || !suggestions.length) {
      container.innerHTML = `
        <div class="col-12">
          <div class="alert alert-light text-center mb-0">
            No zero-spend customers with email contacts were found.
          </div>
        </div>
      `;
      if (loadMoreBtn) loadMoreBtn.classList.add('d-none');
      return;
    }

    container.innerHTML = suggestions.map(renderSuggestionCard).join('');

    container.querySelectorAll('[data-copy-subject]').forEach((button) => {
      button.addEventListener('click', () => {
        const subject = button.closest('.card-body').querySelector('.form-control')?.textContent || '';
        copyToClipboard(subject.trim(), button);
      });
    });

    container.querySelectorAll('[data-copy-body]').forEach((button) => {
      button.addEventListener('click', () => {
        const bodyHtml = button.closest('.card-body').querySelector('.suggestion-body')?.innerHTML || '';
        const text = bodyHtml.replace(/<br>/g, '\\n');
        copyToClipboard(text.trim(), button);
      });
    });

    container.querySelectorAll('[data-template-select]').forEach((select) => {
      select.addEventListener('change', () => {
        const customerId = select.getAttribute('data-customer-id');
        const selected = select.value || '';
        const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
        if (suggestion) {
          suggestion.template_id = selected || null;
          const country = (suggestion.country || '').trim();
          if (country && selected) {
            countryTemplateDefaults[country] = selected;
            localStorage.setItem(countryTemplateKey, JSON.stringify(countryTemplateDefaults));
          }
        }
        renderSuggestions(currentSuggestions);
      });
    });

    container.querySelectorAll('[data-contact-select]').forEach((select) => {
      select.addEventListener('change', () => {
        const customerId = select.getAttribute('data-customer-id');
        const selected = select.value || '';
        const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
        if (suggestion) {
          suggestion.selected_contact_id = selected || null;
        }
        renderSuggestions(currentSuggestions);
      });
    });

    container.querySelectorAll('[data-copy-email]').forEach((button) => {
      button.addEventListener('click', () => {
        const email = button.getAttribute('data-copy-email') || '';
        copyToClipboard(email, button);
      });
    });

    // Email timeline handlers
    container.querySelectorAll('[data-load-timeline]').forEach((button) => {
      button.addEventListener('click', async () => {
        const email = button.getAttribute('data-email');
        const customerId = button.getAttribute('data-customer-id');
        const wrapper = button.closest('.card-body');
        const timelineContainer = wrapper ? wrapper.querySelector('[data-email-timeline]') : null;
        const detailContainer = wrapper ? wrapper.querySelector('[data-graph-content]') : null;
        const scanTimeEl = wrapper ? wrapper.querySelector('[data-scan-time]') : null;
        if (!email || !timelineContainer) return;

        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading';
        timelineContainer.innerHTML = '<div class="text-muted small">Loading emails...</div>';
        detailContainer?.classList.add('d-none');

        try {
          const response = await fetch(`/emails/graph/contact-timeline?email=${encodeURIComponent(email)}`);
          const data = await response.json();
          if (!response.ok || !data.success) {
            throw new Error(data?.error?.message || 'Unable to load emails');
          }

          const emails = data.emails || [];
          const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));

          // Store emails for AI context
          if (suggestion) {
            suggestion._cachedEmails = emails;
          }

          const showEmailDetail = (emailItem) => {
            if (!detailContainer) return;
            detailContainer.classList.remove('d-none');

            const subject = emailItem.subject || '';
            const direction = emailItem.direction === 'sent' ? 'To' : 'From';
            const timestamp = emailItem.timestamp ? new Date(emailItem.timestamp).toLocaleString() : '';

            const rawBody = emailItem.body?.content || emailItem.preview || '';
            const sanitized = sanitizeGraphHtml(rawBody);
            const textBody = stripHtml(sanitized || rawBody);

            const headerLines = [];
            if (subject) headerLines.push(`Subject: ${subject}`);
            headerLines.push(`${direction}: Contact`);
            if (timestamp) headerLines.push(`Date: ${timestamp}`);

            const headerHtml = `<div class="graph-email-meta">${escapeHtml(headerLines.join(' · '))}</div>`;
            const bodyHtml = sanitized || escapeHtml(textBody).replace(/\n/g, '<br>');
            detailContainer.innerHTML = `${headerHtml}<div class="graph-email-body">${bodyHtml}</div>`;
            detailContainer.setAttribute('data-text', textBody);

            // Update suggestion with selected email for AI context
            if (suggestion) {
              suggestion.last_graph_email_full = {
                subject: subject,
                body_html: sanitized || rawBody,
                body_text: textBody
              };
            }
          };

          renderEmailTimeline(emails, timelineContainer, showEmailDetail);

          if (data.scan_status && scanTimeEl) {
            scanTimeEl.textContent = formatScanTime(data.scan_status.last_scan_at);
          }

          // Auto-select first email
          if (emails.length > 0) {
            const firstItem = timelineContainer.querySelector('.email-timeline-item');
            if (firstItem) {
              firstItem.classList.add('active');
              showEmailDetail(emails[0]);
            }
          }
        } catch (error) {
          timelineContainer.innerHTML = `<div class="text-muted small">${escapeHtml(error.message || 'Unable to load emails')}</div>`;
        } finally {
          button.disabled = false;
          button.innerHTML = '<i class="bi bi-envelope"></i> Load';
        }
      });
    });

    container.querySelectorAll('[data-scan-emails]').forEach((button) => {
      button.addEventListener('click', async () => {
        const email = button.getAttribute('data-email');
        const customerId = button.getAttribute('data-customer-id');
        const wrapper = button.closest('.card-body');
        const timelineContainer = wrapper ? wrapper.querySelector('[data-email-timeline]') : null;
        const loadBtn = wrapper ? wrapper.querySelector('[data-load-timeline]') : null;
        if (!email || !timelineContainer) return;

        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Scanning';
        timelineContainer.innerHTML = '<div class="text-muted small">Scanning mailbox...</div>';

        try {
          const response = await fetch('/emails/graph/scan-contact', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({ email: email })
          });

          const reader = response.body.getReader();
          const decoder = new TextDecoder();

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value);
            const lines = text.split('\n').filter(line => line.startsWith('data: '));

            for (const line of lines) {
              try {
                const data = JSON.parse(line.slice(6));
                if (data.status === 'scanning') {
                  timelineContainer.innerHTML = `<div class="text-muted small">Scanning ${data.folder}... Found ${data.found}</div>`;
                } else if (data.status === 'completed') {
                  timelineContainer.innerHTML = `<div class="text-muted small">Found ${data.total_found} emails.</div>`;
                  // Trigger load to refresh timeline
                  if (loadBtn) loadBtn.click();
                } else if (data.status === 'error') {
                  throw new Error(data.error || 'Scan failed');
                }
              } catch (parseError) {
                // Ignore parse errors
              }
            }
          }
        } catch (error) {
          timelineContainer.innerHTML = `<div class="text-muted small">${escapeHtml(error.message || 'Scan failed')}</div>`;
        } finally {
          button.disabled = false;
          button.innerHTML = '<i class="bi bi-arrow-repeat"></i> Scan';
        }
      });
    });

    container.querySelectorAll('[data-generate-ai]').forEach((button) => {
      button.addEventListener('click', () => {
        const customerId = button.getAttribute('data-customer-id');
        generateAiDraft(customerId, button);
      });
    });
  };

  const loadTemplates = async () => {
    try {
      const response = await fetch('/api/email-templates');
      if (!response.ok) {
        throw new Error('Failed to load templates');
      }
      const result = await response.json();
      templates = Array.isArray(result) ? result : (result.templates || result.data || []);
      templatesLoaded = true;
    } catch (error) {
      console.error(error);
      templates = [];
      templatesLoaded = true;
    }
    renderSuggestions(currentSuggestions);
  };

  const generateAiDraft = async (customerId, button) => {
    if (!customerId) return;
    if (button) {
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating';
    }

    const suggestion = currentSuggestions.find((item) => String(item.customer_id) === String(customerId));
    const templateId = suggestion ? getTemplateIdForSuggestion(suggestion) : '';

    const payload = { customer_id: customerId };
    if (templateId) payload.template_id = templateId;
    if (suggestion?.last_graph_email_full?.body_text || suggestion?.last_graph_email_full?.body_html) {
      payload.graph_email_subject = suggestion.last_graph_email_full.subject || '';
      payload.graph_email_body = suggestion.last_graph_email_full.body_text || suggestion.last_graph_email_full.body_html;
    }

    try {
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/ai`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        throw new Error('Failed to generate AI draft');
      }
      const data = await response.json();
      const index = currentSuggestions.findIndex((item) => String(item.customer_id) === String(customerId));
      if (index > -1) {
        currentSuggestions[index] = {
          ...currentSuggestions[index],
          suggested_email: data.suggested_email || null,
          news_items: data.news_items || [],
          ai_generated: true,
          ai_error: null
        };
      }
      renderSuggestions(currentSuggestions);
    } catch (error) {
      console.error(error);
      const index = currentSuggestions.findIndex((item) => String(item.customer_id) === String(customerId));
      if (index > -1) {
        currentSuggestions[index] = {
          ...currentSuggestions[index],
          ai_error: error?.message || 'Failed to generate AI draft'
        };
      }
      renderSuggestions(currentSuggestions);
    }
  };

  const loadSuggestions = async () => {
    summary.innerHTML = '';
    if (loadingState) {
      container.innerHTML = '';
      container.appendChild(loadingState);
      loadingState.classList.remove('d-none');
    } else {
      container.innerHTML = '';
    }
    try {
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/data?limit=${pageSize}&offset=0`);
      if (!response.ok) {
        throw new Error('Failed to load suggestions');
      }
      const data = await response.json();
      currentSuggestions = data.suggestions || [];
      currentNoContactTargets = data.targets_without_contacts || [];
      totalAvailable = Number.isFinite(data.total_available) ? data.total_available : null;
      renderSummary(data);
      renderSuggestions(currentSuggestions);
      renderNoContactTargets(currentNoContactTargets);
      if (loadMoreBtn) {
        if (totalAvailable !== null && currentSuggestions.length < totalAvailable) {
          loadMoreBtn.classList.remove('d-none');
        } else {
          loadMoreBtn.classList.add('d-none');
        }
      }
    } catch (error) {
      console.error(error);
      container.innerHTML = `
        <div class="col-12">
          <div class="alert alert-danger">Unable to load suggestions. ${error?.message || ''}</div>
        </div>
      `;
    } finally {
      if (loadingState) loadingState.classList.add('d-none');
    }
  };

  const loadMoreSuggestions = async () => {
    if (!loadMoreBtn) return;
    loadMoreBtn.disabled = true;
    loadMoreBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading';

    try {
      const offset = currentSuggestions.length;
      const response = await fetch(`/salespeople/${salespersonId}/contact-suggestions/data?limit=${pageSize}&offset=${offset}`);
      if (!response.ok) {
        throw new Error('Failed to load more suggestions');
      }
      const data = await response.json();
      const newSuggestions = data.suggestions || [];
      if (Array.isArray(data.targets_without_contacts)) {
        currentNoContactTargets = data.targets_without_contacts;
        renderNoContactTargets(currentNoContactTargets);
      }
      totalAvailable = Number.isFinite(data.total_available) ? data.total_available : totalAvailable;
      currentSuggestions = currentSuggestions.concat(newSuggestions);
      renderSummary(data);
      renderSuggestions(currentSuggestions);
      if (!newSuggestions.length || (totalAvailable !== null && currentSuggestions.length >= totalAvailable)) {
        loadMoreBtn.classList.add('d-none');
      }
    } catch (error) {
      console.error(error);
    } finally {
      loadMoreBtn.disabled = false;
      loadMoreBtn.textContent = 'Load more suggestions';
    }
  };

  loadTemplates();
  loadSuggestions();

  loadMoreBtn?.addEventListener('click', loadMoreSuggestions);

  // Scan All Emails functionality
  const scanAllBtn = document.getElementById('scanAllEmails');
  const scanAllModal = document.getElementById('scanAllModal');
  const scanAllStatus = document.getElementById('scanAllStatus');
  const scanAllProgress = document.getElementById('scanAllProgress');
  const scanAllProgressBar = document.getElementById('scanAllProgressBar');
  const scanAllCurrentContact = document.getElementById('scanAllCurrentContact');
  const scanAllStats = document.getElementById('scanAllStats');
  const scanAllClose = document.getElementById('scanAllClose');
  const scanAllCancel = document.getElementById('scanAllCancel');

  let scanAllAbortController = null;

  if (scanAllBtn && scanAllModal) {
    const modal = new bootstrap.Modal(scanAllModal);

    scanAllBtn.addEventListener('click', async () => {
      // Collect all unique contact emails from current suggestions
      const emails = [];
      currentSuggestions.forEach(suggestion => {
        const contacts = suggestion.contacts || [];
        contacts.forEach(contact => {
          if (contact.email) {
            emails.push(contact.email);
          }
        });
      });

      if (!emails.length) {
        alert('No contact emails found to scan.');
        return;
      }

      // Reset modal state
      scanAllStatus.textContent = 'Starting...';
      scanAllProgress.textContent = `0 / ${emails.length}`;
      scanAllProgressBar.style.width = '0%';
      scanAllCurrentContact.textContent = '';
      scanAllStats.textContent = '';
      scanAllClose.classList.add('d-none');
      scanAllCancel.classList.remove('d-none');
      scanAllCancel.disabled = false;

      modal.show();

      scanAllAbortController = new AbortController();

      try {
        const response = await fetch('/emails/graph/scan-contacts-bulk', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: JSON.stringify({ emails: emails }),
          signal: scanAllAbortController.signal
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text = decoder.decode(value);
          const lines = text.split('\n').filter(line => line.startsWith('data: '));

          for (const line of lines) {
            try {
              const data = JSON.parse(line.slice(6));

              if (data.status === 'starting') {
                scanAllStatus.textContent = 'Scanning...';
                scanAllProgress.textContent = `0 / ${data.total}`;
              } else if (data.status === 'scanning') {
                scanAllCurrentContact.textContent = `Scanning ${data.folder} for ${data.current_email}`;
              } else if (data.status === 'progress') {
                const pct = Math.round((data.processed / data.total) * 100);
                scanAllProgress.textContent = `${data.processed} / ${data.total}`;
                scanAllProgressBar.style.width = `${pct}%`;
                scanAllStats.textContent = `Found ${data.total_emails_found} total emails`;
              } else if (data.status === 'completed') {
                scanAllStatus.textContent = 'Completed!';
                scanAllProgress.textContent = `${data.processed} / ${data.total}`;
                scanAllProgressBar.style.width = '100%';
                scanAllProgressBar.classList.remove('progress-bar-animated');
                scanAllCurrentContact.textContent = '';
                scanAllStats.textContent = `Found ${data.total_emails_found} emails across ${data.processed} contacts`;
                if (data.errors && data.errors.length) {
                  scanAllStats.textContent += ` (${data.errors.length} errors)`;
                }
                scanAllClose.classList.remove('d-none');
                scanAllCancel.classList.add('d-none');
              } else if (data.status === 'error') {
                scanAllStatus.textContent = 'Error';
                scanAllCurrentContact.textContent = data.error || 'An error occurred';
                scanAllClose.classList.remove('d-none');
                scanAllCancel.classList.add('d-none');
              }
            } catch (parseError) {
              // Ignore parse errors
            }
          }
        }
      } catch (error) {
        if (error.name === 'AbortError') {
          scanAllStatus.textContent = 'Cancelled';
          scanAllCurrentContact.textContent = 'Scan was cancelled by user';
        } else {
          scanAllStatus.textContent = 'Error';
          scanAllCurrentContact.textContent = error.message || 'An error occurred';
        }
        scanAllClose.classList.remove('d-none');
        scanAllCancel.classList.add('d-none');
      }
    });

    scanAllCancel.addEventListener('click', () => {
      if (scanAllAbortController) {
        scanAllAbortController.abort();
        scanAllCancel.disabled = true;
        scanAllCancel.textContent = 'Cancelling...';
      }
    });
  }
});
