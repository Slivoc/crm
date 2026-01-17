// Extracted from templates/salespeople/activity.html
// Consolidated to reduce inline script parsing overhead.

// Call List Management
let callListData = { no_communications: [], has_communications: [] };
window.activityStartMs = window.activityStartMs || performance.now();
const activityStartMs = window.activityStartMs;

function scheduleRender(fn, delay = 0) {
    setTimeout(fn, delay);
}

function logActivityTiming(label) {
    const elapsed = Math.round(performance.now() - activityStartMs);
    console.log(`activity timing ${label}: ${elapsed}ms`);
}

function fetchCallListData() {
    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    logActivityTiming('call_list_fetch_start');
    return fetch(`/salespeople/${salespersonId}/call-list-data`)
        .then(response => response.json())
        .then(data => {
            if (data && data.success === false) {
                throw new Error(data.error || 'Failed to load call list');
            }
            callListData = data;
            scheduleRender(() => {
                displayCallList(data);
                requestAnimationFrame(() => logActivityTiming('call_list_painted'));
            });
            logActivityTiming('call_list_render');
            if (console.timeEnd) {
                console.timeEnd('activity.call_list');
            }
        })
        .catch(error => {
            console.error('Error fetching call list:', error);
            logActivityTiming('call_list_error');
            if (console.timeEnd) {
                console.timeEnd('activity.call_list');
            }
            document.getElementById('callListContent').innerHTML = `
                <div class="text-center text-danger p-4">
                    <i class="bi bi-exclamation-triangle"></i>
                    <p>Unable to load call list</p>
                </div>
            `;
        });
}

let partsListPreviewModal;

function updatePartsListHeader(listId, payload, statusEl, updateUrl) {
    if (!listId) {
        return Promise.resolve();
    }
    if (statusEl) {
        statusEl.textContent = 'Saving...';
    }
    const targetUrl = updateUrl || `/parts_list/parts-lists/${listId}/update`;
    return fetch(targetUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Unable to save parts list');
            }
            if (statusEl) {
                statusEl.textContent = 'Saved';
            }
        })
        .catch(error => {
            console.error('Error updating parts list:', error);
            if (statusEl) {
                statusEl.textContent = 'Error saving';
            }
        });
}

function formatStatusCurrency(value, symbol) {
    if (value === null || value === undefined || value === '') {
        return '-';
    }
    const numeric = parseFloat(value);
    if (Number.isNaN(numeric)) {
        return '-';
    }
    const prefix = symbol || '';
    return `${prefix}${numeric.toFixed(2)}`;
}

function buildPreviewLineStatus(line) {
    const supplierQuoteCount = line.line_supplier_quote_count || 0;
    const supplierQuoteBadge = supplierQuoteCount > 0
        ? `<div><small class="text-muted">Supplier quotes: ${supplierQuoteCount}</small></div>`
        : '';

    if (line.line_quote_price !== null && line.line_quote_price !== undefined && line.line_quote_price !== '') {
        const quoteDisplay = formatStatusCurrency(line.line_quote_price, line.line_quote_currency_symbol || '£');
        return `
            <div>
                <div class="d-flex align-items-center gap-2">
                    <span class="badge bg-success">Quoted</span>
                    <span class="fw-semibold text-primary">${quoteDisplay}</span>
                </div>
                ${supplierQuoteBadge}
            </div>
        `;
    }

    if (line.chosen_cost !== null && line.chosen_cost !== undefined && line.chosen_cost !== '') {
        const costDisplay = formatStatusCurrency(line.chosen_cost, line.chosen_currency_symbol || '£');
        const supplierName = line.chosen_supplier_name ? `<div><small class="text-muted">${line.chosen_supplier_name}</small></div>` : '';
        return `
            <div>
                <div class="d-flex align-items-center gap-2">
                    <span class="badge bg-primary">Costed</span>
                    <span class="fw-semibold text-primary">${costDisplay}</span>
                </div>
                ${supplierName}
                ${supplierQuoteBadge}
            </div>
        `;
    }

    if (supplierQuoteCount > 0) {
        return `
            <div>
                <div><span class="badge bg-info text-dark">Supplier Quote</span></div>
                ${supplierQuoteBadge}
            </div>
        `;
    }

    const contactedCount = line.line_contacted_suppliers_count || 0;
    if (contactedCount > 0) {
        return `
            <div>
                <div><span class="badge bg-warning text-dark">Contacted</span></div>
                <div><small class="text-muted">${contactedCount} supplier${contactedCount !== 1 ? 's' : ''}</small></div>
            </div>
        `;
    }

    return '';
}

function renderPartsListPreviewLines(lines) {
    if (!lines || lines.length === 0) {
        return '<p class="text-muted mb-0">No lines found for this parts list.</p>';
    }

    const rows = lines.map(line => `
        <tr>
            <td>${line.line_number ?? '-'}</td>
            <td>${line.customer_part_number || '-'}</td>
            <td>${buildPreviewLineStatus(line)}</td>
            <td class="text-end">${line.quantity ?? '-'}</td>
        </tr>
    `).join('');

    return `
        <div class="table-responsive">
            <table class="table table-sm align-middle">
                <thead class="table-light">
                    <tr>
                        <th scope="col">Line</th>
                        <th scope="col">Customer Part</th>
                        <th scope="col">Status</th>
                        <th scope="col" class="text-end">Qty</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
    `;
}

async function showPartsListPreview(options) {
    const modalEl = document.getElementById('partsListPreviewModal');
    if (!modalEl) {
        return;
    }
    if (!partsListPreviewModal) {
        partsListPreviewModal = new bootstrap.Modal(modalEl);
    }

    const { listName, customerName, linesUrl } = options;
    const titleEl = document.getElementById('partsListPreviewTitle');
    const subtitleEl = document.getElementById('partsListPreviewSubtitle');
    const contentEl = document.getElementById('partsListPreviewContent');
    const spinnerEl = document.getElementById('partsListPreviewSpinner');

    titleEl.textContent = listName || 'Parts List Preview';
    subtitleEl.textContent = customerName ? `Customer: ${customerName}` : '';
    contentEl.innerHTML = '';
    spinnerEl.classList.remove('d-none');
    partsListPreviewModal.show();

    try {
        const requestUrl = linesUrl
            ? (linesUrl.includes('?') ? `${linesUrl}&include_status=1` : `${linesUrl}?include_status=1`)
            : linesUrl;
        const response = await fetch(requestUrl);
        const data = await response.json();
        if (!data.success) {
            throw new Error(data.message || 'Unable to load parts list lines.');
        }
        contentEl.innerHTML = renderPartsListPreviewLines(data.lines);
    } catch (error) {
        contentEl.innerHTML = `
            <div class="alert alert-danger mb-0">
                ${error.message}
            </div>
        `;
    } finally {
        spinnerEl.classList.add('d-none');
    }
}
function displayCallList(data) {
    const container = document.getElementById('callListContent');
    const noComms = data.no_communications || [];
    const hasComms = data.has_communications || [];

    if (noComms.length === 0 && hasComms.length === 0) {
        container.innerHTML = `
            <div class="text-center p-5 text-muted">
                <i class="bi bi-list-check fs-1"></i>
                <h6 class="mt-3">Your Call List is Empty</h6>
                <p>Add contacts to your call list to track follow-ups</p>
            </div>
        `;
        return;
    }

    let html = '';

    // Needs Contact Section
    if (noComms.length > 0) {
        html += `
            <div class="bg-danger bg-opacity-10 p-2">
                <strong class="text-danger">
                    <i class="bi bi-exclamation-triangle me-1"></i>
                    Needs Contact (${noComms.length})
                </strong>
            </div>
        `;

        noComms.forEach(contact => {
            const daysWaiting = calculateDaysWaiting(contact.added_date);
            const badgeClass = daysWaiting > 7 ? 'danger' : daysWaiting > 3 ? 'warning' : 'info';

            html += `
    <div class="call-list-item p-3">
        <div class="d-flex justify-content-between align-items-start">
            <div class="flex-grow-1">
                <div class="d-flex align-items-center mb-1">
                    <strong>${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}</strong>
                </div>
                <div class="small text-muted">
                    ${contact.customer_name}${contact.job_title ? ' • ' + contact.job_title : ''}
                </div>
                <div class="small text-muted">
                    <i class="bi bi-clock me-1"></i>Added ${formatDate(contact.added_date)}
                </div>
            </div>
                <div class="text-end">
                    <span class="badge bg-${badgeClass} mb-2">${daysWaiting} days</span>
                    <div class="d-flex gap-1 justify-content-end align-items-center">
                        <button class="btn btn-sm btn-outline-primary contact-quick-action-btn"
                            data-contact-id="${contact.contact_id}"
                            data-customer-id="${contact.customer_id}"
                            data-contact-name="${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}"
                            data-customer-name="${contact.customer_name}"
                            data-contact-email="${contact.email || ''}"
                            data-contact-phone="${contact.phone || ''}"
                            data-contact-job-title="${contact.job_title || ''}"
                            data-contact-status="${contact.contact_status || ''}"
                            data-contact-status-color="${contact.status_color || ''}"
                            data-call-list-id="${contact.call_list_id}"
                            data-action="phone"
                            title="Log Phone Call">
                        <i class="bi bi-telephone"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary contact-quick-action-btn"
                            data-contact-id="${contact.contact_id}"
                            data-customer-id="${contact.customer_id}"
                            data-contact-name="${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}"
                            data-customer-name="${contact.customer_name}"
                            data-contact-email="${contact.email || ''}"
                            data-contact-phone="${contact.phone || ''}"
                            data-contact-job-title="${contact.job_title || ''}"
                            data-contact-status="${contact.contact_status || ''}"
                            data-contact-status-color="${contact.status_color || ''}"
                            data-call-list-id="${contact.call_list_id}"
                            data-action="email"
                            title="Log Email">
                        <i class="bi bi-envelope"></i>
                    </button>
                    <button class="btn btn-sm btn-link text-danger p-1"
                        onclick="removeFromCallList(${contact.call_list_id})"
                        title="Remove from call list">
                        <i class="bi bi-x-circle"></i>
                    </button>
                    <div class="dropdown">
                        <button class="btn btn-sm btn-link text-secondary p-1 dropdown-toggle snooze-dropdown-toggle"
                                type="button"
                                data-bs-toggle="dropdown"
                                aria-expanded="false"
                                title="Snooze call list entry"
                                aria-label="Snooze call list entry">
                            <i class="bi bi-moon"></i>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 3)">
                                    Snooze 3 days
                                </button>
                            </li>
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 7)">
                                    Snooze 1 week
                                </button>
                            </li>
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 30)">
                                    Snooze 1 month
                                </button>
                            </li>
                        </ul>
                    </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
`;
        });  // <-- ADD THIS CLOSING BRACE HERE
    }
    // Recently Contacted Section - ONLY show if there are communications since being added
    if (hasComms.length > 0) {
        // Filter to only include contacts with actual communications since being added
        const recentlyContacted = hasComms.filter(contact =>
            contact.latest_communication_since_added != null
        );

        if (recentlyContacted.length > 0) {
            html += `
                <div class="bg-success bg-opacity-10 p-2 mt-2">
                    <strong class="text-success">
                        <i class="bi bi-check-circle me-1"></i>
                        Recently Contacted (${recentlyContacted.length})
                    </strong>
                </div>
            `;

           recentlyContacted.forEach(contact => {
    const priorityBadge = contact.priority == 2 ? 'danger' : contact.priority == 1 ? 'warning' : 'secondary';
    const priorityText = contact.priority == 2 ? 'Urgent' : contact.priority == 1 ? 'High' : 'Normal';
    const commIcon = getCommIcon(contact.latest_communication_type);

    html += `
        <div class="call-list-item p-3">
            <div class="d-flex justify-content-between align-items-start">
                <div class="flex-grow-1">
                    <div class="d-flex align-items-center mb-1">
                        <strong>${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}</strong>
                    </div>
                    <div class="small text-muted mb-1">
                        ${contact.customer_name}
                    </div>
                    <div class="small mb-1">
                        <span class="badge bg-light text-dark">
                            <i class="bi bi-${commIcon} me-1"></i>${contact.latest_communication_type}
                        </span>
                        <span class="text-muted ms-2">${formatDate(contact.latest_communication_since_added)}</span>
                    </div>
                    ${contact.latest_communication_notes ? `
                        <div class="small text-muted fst-italic">
                            "${contact.latest_communication_notes.substring(0, 100)}${contact.latest_communication_notes.length > 100 ? '...' : ''}"
                        </div>
                    ` : ''}
                </div>
                <div class="text-end">
                    <div class="d-flex gap-1 justify-content-end align-items-center">
                        <button class="btn btn-sm btn-outline-primary contact-quick-action-btn"
                                data-contact-id="${contact.contact_id}"
                                data-customer-id="${contact.customer_id}"
                                data-contact-name="${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}"
                                data-customer-name="${contact.customer_name}"
                                data-contact-email="${contact.email || ''}"
                                data-contact-phone="${contact.phone || ''}"
                                data-contact-job-title="${contact.job_title || ''}"
                                data-contact-status="${contact.contact_status || ''}"
                                data-contact-status-color="${contact.status_color || ''}"
                                data-call-list-id="${contact.call_list_id}"
                                data-action="phone"
                                title="Log Phone Call">
                            <i class="bi bi-telephone"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-primary contact-quick-action-btn"
                                data-contact-id="${contact.contact_id}"
                                data-customer-id="${contact.customer_id}"
                                data-contact-name="${contact.name}${contact.second_name ? ' ' + contact.second_name : ''}"
                                data-customer-name="${contact.customer_name}"
                                data-contact-email="${contact.email || ''}"
                                data-contact-phone="${contact.phone || ''}"
                                data-contact-job-title="${contact.job_title || ''}"
                                data-contact-status="${contact.contact_status || ''}"
                                data-contact-status-color="${contact.status_color || ''}"
                                data-call-list-id="${contact.call_list_id}"
                                data-action="email"
                                title="Log Email">
                            <i class="bi bi-envelope"></i>
                        </button>
                    <button class="btn btn-sm btn-link text-danger p-1"
                            onclick="removeFromCallList(${contact.call_list_id})"
                            title="Remove from call list">
                        <i class="bi bi-x-circle"></i>
                    </button>
                    <div class="dropdown">
                        <button class="btn btn-sm btn-link text-secondary p-1 dropdown-toggle snooze-dropdown-toggle"
                                type="button"
                                data-bs-toggle="dropdown"
                                aria-expanded="false"
                                title="Snooze call list entry"
                                aria-label="Snooze call list entry">
                            <i class="bi bi-moon"></i>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 3)">
                                    Snooze 3 days
                                </button>
                            </li>
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 7)">
                                    Snooze 1 week
                                </button>
                            </li>
                            <li>
                                <button class="dropdown-item" type="button"
                                        onclick="snoozeCallList(${contact.call_list_id}, 30)">
                                    Snooze 1 month
                                </button>
                            </li>
                        </ul>
                    </div>
                    </div>
                </div>
            </div>
        </div>
    `;
});
            if (recentlyContacted.length > 5) {
                html += `
                    <div class="text-center p-2 text-muted">
                        <small>Showing 5 of ${recentlyContacted.length} recently contacted entries</small>
                    </div>
                `;
            }
        }
    }

    container.innerHTML = html;
}

function calculateDaysWaiting(addedDate) {
    const added = new Date(addedDate);
    const now = new Date();
    const diffTime = Math.abs(now - added);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    return diffDays;
}

function formatDate(dateString) {
    const date = new Date(dateString);
    const now = new Date();

    // Reset both to start of day for accurate day comparison
    const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const nowOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate());

    const diffTime = nowOnly - dateOnly;
    const diffDays = Math.floor(diffTime / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'today';
    if (diffDays === 1) return 'yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;
    if (diffDays < 30) return `${Math.ceil(diffDays / 7)} weeks ago`;
    return date.toLocaleDateString();
}

function getCommIcon(commType) {
    const icons = {
        'Phone': 'telephone',
        'Email': 'envelope',
        'Meeting': 'calendar-event',
        'Video Call': 'camera-video',
        'Other': 'chat-dots'
    };
    return icons[commType] || 'chat-dots';
}

function logCallListCommunication(callListId, contactId, customerId, contactName, customerName) {
    document.getElementById('callListId').value = callListId;
    document.getElementById('callListContactId').value = contactId;
    document.getElementById('callListCustomerId').value = customerId;
    document.getElementById('callListContactInfo').innerHTML = `
        <strong>${contactName}</strong><br>
        <small class="text-muted">${customerName}</small>
    `;

    const modal = new bootstrap.Modal(document.getElementById('logCallListCommModal'));
    modal.show();
}

function submitCallListCommunication() {
    const form = document.getElementById('logCallListCommForm');
    const formData = new FormData(form);
    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    fetch(`/salespeople/${salespersonId}/add_contact_communication`, {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const modal = bootstrap.Modal.getInstance(document.getElementById('logCallListCommModal'));
            modal.hide();
            form.reset();
            // Refresh both call list and communications
            fetchCallListData();
            fetchRecentCommunications(document.getElementById('commDatePicker').value);
        } else {
            alert('Error logging communication: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error logging communication. Please try again.');
    });
}

function removeFromCallList(callListId) {
    if (!confirm('Remove this contact from your call list?')) return;

    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    fetch(`/salespeople/${salespersonId}/remove-from-call-list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_list_id: callListId })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            fetchCallListData(); // Refresh the call list
        } else {
            alert('Error removing from call list: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error removing from call list. Please try again.');
    });
}

function snoozeCallList(callListId, days) {
    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    fetch(`/salespeople/${salespersonId}/snooze-call-list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ call_list_id: callListId, snooze_days: days })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            fetchCallListData();
        } else {
            alert('Error snoozing call list: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error snoozing call list. Please try again.');
    });
}

// Initialize call list handled in main page initializer.

// Simple fetch timing log for copy/paste diagnostics
(function () {
  const perfLog = [];
  const origFetch = window.fetch;

  function toUrlString(input) {
    if (typeof input === 'string') return input;
    if (input && input.url) return input.url;
    return String(input || '');
  }

  window.fetch = async function (...args) {
    const url = toUrlString(args[0]);
    const start = performance.now();
    try {
      const response = await origFetch.apply(this, args);
      const durationMs = performance.now() - start;
      perfLog.push({
        type: 'fetch',
        url,
        status: response.status,
        duration_ms: Math.round(durationMs),
        ts: new Date().toISOString()
      });
      return response;
    } catch (error) {
      const durationMs = performance.now() - start;
      perfLog.push({
        type: 'fetch',
        url,
        status: 'error',
        duration_ms: Math.round(durationMs),
        error: String(error || 'unknown'),
        ts: new Date().toISOString()
      });
      throw error;
    }
  };

  window.dumpActivityPerfLog = async function () {
    const payload = JSON.stringify(perfLog, null, 2);
    console.log(payload);
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(payload);
        alert('Perf log copied to clipboard.');
      } else {
        alert('Perf log printed to console. Copy from console output.');
      }
    } catch (err) {
      alert('Perf log printed to console. Copy from console output.');
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    perfLog.push({
      type: 'event',
      name: 'DOMContentLoaded',
      ts: new Date().toISOString()
    });
  });

  window.addEventListener('load', () => {
    perfLog.push({
      type: 'event',
      name: 'window_load',
      ts: new Date().toISOString()
    });
  });
})();

// Enhanced Customer News Manager with Progress Tracking
class CustomerNewsManager {
  constructor() {
    this.newsData = [];
    this.isChecking = false;
    this.salespersonId = this.getSalespersonId();
    this.eventSource = null;
    this.customers = [];
    this.customerProgress = new Map();
    this.lastCheckTime = null;
    this.cacheLoaded = false;
    this.streamCompleted = false;
    this.streamHadData = false;
    this.initializeEventListeners();
    this.setConnectionStatus('idle', 'Idle');
  }

  getSalespersonId() {
    const urlParts = window.location.pathname.split('/');
    return urlParts[urlParts.indexOf('salespeople') + 1];
  }

  initializeEventListeners() {
    document.getElementById('checkNewsBtn').addEventListener('click', () => this.checkForNews());
    document.getElementById('refreshNewsBtn').addEventListener('click', () => this.forceRefreshNews());
    document.getElementById('viewAllNewsBtn').addEventListener('click', () => this.showNewsModal());

    // Collapse toggle functionality with icon update
    const newsCardBody = document.getElementById('newsCardBody');
    const toggleIcon = document.getElementById('newsToggleIcon');

    newsCardBody.addEventListener('show.bs.collapse', () => {
      toggleIcon.className = 'bi bi-chevron-down ms-1';
      if (!this.cacheLoaded) {
        this.loadCachedNews();
      }
    });

    newsCardBody.addEventListener('hide.bs.collapse', () => {
      toggleIcon.className = 'bi bi-chevron-right ms-1';
    });
  }

  async loadCachedNews() {
    try {
      const response = await fetch(`/salespeople/${this.salespersonId}/customer_news`);
      const data = await response.json();

      if (data.cached && data.news_items && data.news_items.length > 0) {
        this.newsData = data.news_items;
        this.lastCheckTime = data.last_checked ? new Date(data.last_checked) : null;
        this.updateHeaderMetrics();
        this.displayResults(data);
        this.setConnectionStatus('ready', 'Cached');
      }
      this.cacheLoaded = true;
    } catch (error) {
      console.error('Error loading cached news:', error);
      this.setConnectionStatus('error', 'Error');
    }
  }

  updateHeaderMetrics() {
    const lastCheckMetric = document.getElementById('lastCheckMetric');
    const mostRecentMetric = document.getElementById('mostRecentMetric');
    const newsCountBadge = document.getElementById('newsCountBadge');
    const lastCheckTime = document.getElementById('lastCheckTime');
    const mostRecentNews = document.getElementById('mostRecentNews');

    // Update last check time
    if (this.lastCheckTime) {
      lastCheckMetric.style.display = 'block';
      lastCheckTime.textContent = this.formatRelativeTime(this.lastCheckTime);
    }

    // Update most recent news
    if (this.newsData && this.newsData.length > 0) {
      const mostRecentItem = this.getMostRecentNewsItem(this.newsData);
      if (mostRecentItem && mostRecentItem.published_date) {
        mostRecentMetric.style.display = 'block';
        mostRecentNews.textContent = this.formatRelativeTime(new Date(mostRecentItem.published_date));
      }

      // Update count badge
      newsCountBadge.style.display = 'inline-block';
      newsCountBadge.textContent = this.newsData.length;
    } else {
      mostRecentMetric.style.display = 'none';
      newsCountBadge.style.display = 'none';
    }
  }

  getMostRecentNewsItem(newsItems) {
    if (!newsItems || newsItems.length === 0) return null;

    return newsItems.reduce((mostRecent, item) => {
      if (!item.published_date) return mostRecent;
      if (!mostRecent || !mostRecent.published_date) return item;

      return new Date(item.published_date) > new Date(mostRecent.published_date)
        ? item
        : mostRecent;
    }, null);
  }

  formatRelativeTime(date) {
    const now = new Date();
    const diffTime = Math.abs(now - date);
    const diffMinutes = Math.ceil(diffTime / (1000 * 60));
    const diffHours = Math.ceil(diffTime / (1000 * 60 * 60));
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

    if (diffMinutes < 60) {
      return diffMinutes === 1 ? '1 min ago' : `${diffMinutes} mins ago`;
    } else if (diffHours < 24) {
      return diffHours === 1 ? '1 hour ago' : `${diffHours} hours ago`;
    } else if (diffDays < 7) {
      return diffDays === 1 ? '1 day ago' : `${diffDays} days ago`;
    } else {
      return date.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
      });
    }
  }

  async forceRefreshNews() {
    if (this.isChecking) return;

    this.isChecking = true;
    this.showLoadingState();

    try {
      const response = await fetch(`/salespeople/${this.salespersonId}/customer_news?force_refresh=true`);
      const data = await response.json();

      if (data.requires_streaming) {
        if (data.supports_streaming === false) {
          this.fallbackToSyncRefresh();
          return;
        }
        this.startProgressStream();
      } else {
        this.displayResults(data);
        this.isChecking = false;
      }
    } catch (error) {
      console.error('Error checking news:', error);
      this.showError('Unable to check customer news at this time');
      this.isChecking = false;
    }
  }

  async checkForNews() {
    if (this.isChecking) return;

    this.isChecking = true;
    this.showLoadingState();

    try {
      const response = await fetch(`/salespeople/${this.salespersonId}/customer_news`);
      const data = await response.json();

      if (data.cached) {
        this.newsData = data.news_items || [];
        this.displayResults(data);
        this.isChecking = false;
        return;
      }

      if (data.requires_streaming) {
        if (data.supports_streaming === false) {
          this.fallbackToSyncRefresh();
          return;
        }
        this.startProgressStream();
      } else {
        this.displayResults(data);
        this.isChecking = false;
      }
    } catch (error) {
      console.error('Error checking news:', error);
      this.showError('Unable to check customer news at this time');
      this.isChecking = false;
    }
  }

  async fallbackToSyncRefresh() {
    this.setConnectionStatus('working', 'Connected');
    try {
      const response = await fetch(`/salespeople/${this.salespersonId}/customer_news?force_refresh=true`);
      const data = await response.json();
      this.displayResults(data);
    } catch (error) {
      console.error('Error checking news:', error);
      this.showError('Unable to check customer news at this time');
    } finally {
      this.isChecking = false;
    }
  }

  startProgressStream() {
    if (this.eventSource) {
      this.eventSource.close();
    }

    const url = `/salespeople/${this.salespersonId}/customer_news?stream=true`;
    this.streamCompleted = false;
    this.streamHadData = false;
    this.setConnectionStatus('working', 'Connected');
    this.eventSource = new EventSource(url);

    this.eventSource.onmessage = (event) => {
      try {
        this.streamHadData = true;
        const data = JSON.parse(event.data);
        this.handleProgressUpdate(data);
      } catch (error) {
        console.error('Error parsing SSE data:', error);
      }
    };

    this.eventSource.onerror = (error) => {
      if (this.streamCompleted || !this.isChecking) {
        return;
      }
      if (!this.streamHadData) {
        this.eventSource.close();
        this.fallbackToSyncRefresh();
        return;
      }
      console.error('SSE error:', error);
      this.eventSource.close();
      this.showError('Connection lost during news collection');
      this.isChecking = false;
    };
  }

  handleProgressUpdate(data) {
    const { status } = data;

    switch (status) {
      case 'starting':
        this.initializeProgress(data);
        break;
      case 'processing':
        this.updateCustomerProgress(data.customer_index, 'processing', 'Searching for news...');
        this.updateCurrentActivity(data.current_customer, 'Searching for recent business news...');
        break;
      case 'analyzing':
        this.updateCustomerProgress(data.customer_index, 'processing', 'Analyzing relevance...');
        this.updateCurrentActivity(data.current_customer, 'Analyzing news relevance...');
        break;
      case 'found_news':
        this.updateCustomerProgress(data.customer_index, 'completed', `Found ${data.news_count} news items`);
        this.updateOverallProgress(data.customer_index + 1);
        break;
      case 'no_news':
        this.updateCustomerProgress(data.customer_index, 'no-news', 'No recent news found');
        this.updateOverallProgress(data.customer_index + 1);
        break;
      case 'no_data':
        this.updateCustomerProgress(data.customer_index, 'no-news', 'No data available');
        this.updateOverallProgress(data.customer_index + 1);
        break;
      case 'error':
        this.updateCustomerProgress(data.customer_index, 'error', 'Error occurred');
        this.updateOverallProgress(data.customer_index + 1);
        break;
      case 'completed':
        this.completeProgress(data);
        break;
    }
  }

  initializeProgress(data) {
    this.customers = data.customers || [];
    this.customerProgress.clear();

    const progressList = document.getElementById('customerProgressList');
    progressList.innerHTML = '';

    this.customers.forEach((customerName, index) => {
      this.customerProgress.set(index, { name: customerName, status: 'pending', message: 'Waiting...' });

      const item = document.createElement('div');
      item.className = 'customer-progress-item pending';
      item.id = `customer-progress-${index}`;
      item.innerHTML = `
        <div class="progress-icon">
          <i class="bi bi-clock text-muted"></i>
        </div>
        <div class="flex-grow-1">
          <div class="customer-name">${customerName}</div>
          <small class="status-message text-muted">Waiting...</small>
        </div>
      `;

      progressList.appendChild(item);
    });

    document.getElementById('progressCounter').textContent = `0/${this.customers.length}`;
    document.getElementById('overallProgress').textContent = `Checking ${this.customers.length} customers for business news...`;
    document.getElementById('currentActivityBox').style.display = 'block';
  }

  updateCustomerProgress(customerIndex, status, message) {
    const item = document.getElementById(`customer-progress-${customerIndex}`);
    if (!item) return;

    item.className = `customer-progress-item ${status}`;
    item.querySelector('.status-message').textContent = message;

    const iconElement = item.querySelector('.progress-icon i');
    switch (status) {
      case 'processing':
        iconElement.className = 'bi bi-arrow-clockwise text-info';
        break;
      case 'completed':
        iconElement.className = 'bi bi-check-circle text-success';
        break;
      case 'error':
        iconElement.className = 'bi bi-exclamation-triangle text-danger';
        break;
      case 'no-news':
        iconElement.className = 'bi bi-dash-circle text-muted';
        break;
    }

    item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  updateCurrentActivity(customerName, detail) {
    document.getElementById('currentActivityTitle').textContent = `Processing: ${customerName}`;
    document.getElementById('currentActivityDetail').textContent = detail;
  }

  updateOverallProgress(completed) {
    const total = this.customers.length;
    const safeCompleted = Number.isFinite(completed) ? completed : 0;
    const safeTotal = Number.isFinite(total) && total > 0 ? total : 0;
    const percentage = safeTotal ? Math.round((safeCompleted / safeTotal) * 100) : 0;

    document.getElementById('newsProgress').style.width = percentage + '%';
    document.getElementById('progressCounter').textContent = `${safeCompleted}/${safeTotal}`;
    document.getElementById('overallProgress').textContent = safeTotal
      ? `${safeCompleted} of ${safeTotal} customers checked...`
      : 'Checking 0 customers for business news...';
  }

  completeProgress(data) {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }

    this.streamCompleted = true;
    document.getElementById('currentActivityBox').style.display = 'none';
    this.newsData = data.news_items || [];
    this.displayResults(data);
    this.isChecking = false;
  }

  showLoadingState() {
    document.getElementById('newsInitialState').style.display = 'none';
    document.getElementById('newsResults').style.display = 'none';
    document.getElementById('newsLoadingState').style.display = 'block';
    this.setConnectionStatus('working', 'Connected');

    const loadingMainMessage = document.getElementById('loadingMainMessage');
    if (loadingMainMessage) {
      loadingMainMessage.textContent = 'Scanning watched customers for news...';
    }

    document.getElementById('customerProgressList').innerHTML = '';
    document.getElementById('currentActivityBox').style.display = 'none';
    document.getElementById('newsProgress').style.width = '0%';
    document.getElementById('progressCounter').textContent = '0/0';
  }

  displayResults(data) {
    document.getElementById('newsLoadingState').style.display = 'none';
    document.getElementById('newsResults').style.display = 'block';

    if (data && Array.isArray(data.news_items)) {
      this.newsData = data.news_items;
    }

    // Use the timestamp from data, or set to now if it's a fresh check
    const lastChecked = data.last_checked || data.last_updated;
    if (lastChecked) {
      this.lastCheckTime = new Date(lastChecked);
    } else {
      this.lastCheckTime = new Date();
    }
    this.updateHeaderMetrics();
    this.setConnectionStatus('ready', 'Updated');

    const customersWithNews = this.groupNewsByCustomer(this.newsData);
    const hasNews = Object.keys(customersWithNews).length > 0;

    if (hasNews) {
      this.showSimplifiedNewsPreview(customersWithNews);
      document.getElementById('noNewsFound').style.display = 'none';
      document.getElementById('viewAllNewsBtn').style.display = 'inline-block';
    } else {
      document.getElementById('newsPreviewCards').innerHTML = '';
      document.getElementById('noNewsFound').style.display = 'block';
      document.getElementById('viewAllNewsBtn').style.display = 'none';
    }

    const now = new Date().toLocaleString();
    document.getElementById('newsLastChecked').textContent = `Last checked: ${now}`;
  }

  showSimplifiedNewsPreview(customersWithNews) {
    const previewContainer = document.getElementById('newsPreviewCards');
    let html = '';

    const sortedCustomers = Object.entries(customersWithNews)
      .sort(([,a], [,b]) => {
        const aLatest = this.getMostRecentNewsDate(a);
        const bLatest = this.getMostRecentNewsDate(b);
        if (aLatest && bLatest) {
          return new Date(bLatest) - new Date(aLatest);
        }
        if (aLatest && !bLatest) return -1;
        if (!aLatest && bLatest) return 1;

        const aHighImpact = a.filter(item => item.business_impact === 'High').length;
        const bHighImpact = b.filter(item => item.business_impact === 'High').length;
        return bHighImpact - aHighImpact || b.length - a.length;
      });

    sortedCustomers.forEach(([customerName, newsItems]) => {
      const highImpactCount = newsItems.filter(item => item.business_impact === 'High').length;
      const mediumImpactCount = newsItems.filter(item => item.business_impact === 'Medium').length;
      const topItem = newsItems[0];
      const mostRecentDate = this.getMostRecentNewsDate(newsItems);
      const dateString = mostRecentDate ? this.formatNewsDate(mostRecentDate) : 'Date unknown';

      html += `
        <div class="news-preview-card" onclick="customerNewsManager.showCustomerNews('${customerName}')">
          <div class="d-flex justify-content-between align-items-start">
            <div class="flex-grow-1">
              <div class="fw-semibold text-dark mb-1">${customerName}</div>
              <div class="small text-muted mb-2">${topItem.headline}</div>
              <div class="d-flex align-items-center justify-content-between">
                <div>
                  ${highImpactCount > 0 ? `<span class="impact-indicator-mini impact-high-mini"></span><small class="text-danger me-2">${highImpactCount} high impact</small>` : ''}
                  ${mediumImpactCount > 0 ? `<span class="impact-indicator-mini impact-medium-mini"></span><small class="text-warning me-2">${mediumImpactCount} medium impact</small>` : ''}
                  ${newsItems.length > 1 ? `<small class="text-muted">${newsItems.length} items total</small>` : ''}
                </div>
                <small class="text-muted ms-2">${dateString}</small>
              </div>
            </div>
            <div class="text-end ms-3">
              <span class="badge ${highImpactCount > 0 ? 'bg-danger' : mediumImpactCount > 0 ? 'bg-warning' : 'bg-light text-dark'} rounded-pill">
                ${newsItems.length}
              </span>
            </div>
          </div>
        </div>
      `;
    });

    previewContainer.innerHTML = html;
  }

  getMostRecentNewsDate(newsItems) {
    let mostRecent = null;
    newsItems.forEach(item => {
      if (item.published_date) {
        const itemDate = new Date(item.published_date);
        if (!mostRecent || itemDate > mostRecent) {
          mostRecent = itemDate;
        }
      }
    });
    return mostRecent;
  }

  formatNewsDate(date) {
    const now = new Date();
    const diffTime = Math.abs(now - date);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays <= 7) return `${diffDays} days ago`;
    if (diffDays <= 30) return `${Math.ceil(diffDays / 7)} weeks ago`;

    return date.toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined
    });
  }

  groupNewsByCustomer(newsItems) {
    const grouped = {};
    newsItems.forEach(item => {
      const customerName = item.customer_name;
      if (!grouped[customerName]) {
        grouped[customerName] = [];
      }
      grouped[customerName].push(item);
    });
    return grouped;
  }

  showError(message) {
    document.getElementById('newsLoadingState').style.display = 'none';
    document.getElementById('newsResults').style.display = 'block';
    this.setConnectionStatus('error', 'Error');
    document.getElementById('newsPreviewCards').innerHTML = `
      <div class="alert alert-danger">
        <i class="bi bi-exclamation-triangle me-2"></i>
        ${message}
      </div>
    `;
    document.getElementById('noNewsFound').style.display = 'none';
    document.getElementById('viewAllNewsBtn').style.display = 'none';
  }

  setConnectionStatus(status, label) {
    const indicator = document.getElementById('newsConnectionIndicator');
    const text = document.getElementById('newsConnectionText');
    if (!indicator || !text) return;

    indicator.classList.remove(
      'news-connection--idle',
      'news-connection--working',
      'news-connection--ready',
      'news-connection--error'
    );
    indicator.classList.add(`news-connection--${status}`);
    text.textContent = label;
  }

  showCustomerNews(customerName) {
    const customerNews = this.newsData.filter(item => item.customer_name === customerName);
    this.showNewsModal(customerNews, customerName);
  }

  showNewsModal(filteredNews = null, customerName = null) {
    const modal = new bootstrap.Modal(document.getElementById('customerNewsModal'));
    const modalContent = document.getElementById('modalNewsContent');
    const modalTitle = document.getElementById('customerNewsModalLabel');

    const newsToShow = filteredNews || this.newsData;
    const title = customerName ? `Customer News - ${customerName}` : 'All Customer News';

    modalTitle.innerHTML = `<i class="bi bi-newspaper me-2"></i>${title}`;

    if (newsToShow.length === 0) {
      modalContent.innerHTML = `
        <div class="text-center py-5">
          <i class="bi bi-newspaper fs-1 text-muted"></i>
          <h5 class="mt-3">No News Items</h5>
          <p class="text-muted">No news items found for the selected criteria.</p>
        </div>
      `;
    } else {
      modalContent.innerHTML = this.generateModalHTML(newsToShow);
    }

    const now = new Date().toLocaleString();
    document.getElementById('modalLastUpdated').textContent = `Last updated: ${now}`;
    modal.show();
  }

  generateModalHTML(newsItems) {
    let html = '';
    newsItems.forEach(item => {
      const impactClass = `impact-badge-${item.business_impact.toLowerCase()}`;
      const publishedDate = item.published_date ?
        new Date(item.published_date).toLocaleDateString() : 'Date not available';

      html += `
        <div class="modal-news-item">
          <div class="modal-news-header">
            <div class="d-flex justify-content-between align-items-start">
              <div class="flex-grow-1">
                <h6 class="mb-1">${item.customer_name}</h6>
                <h5 class="mb-2">${item.headline}</h5>
              </div>
              <div class="text-end">
                <span class="impact-badge ${impactClass} mb-2">${item.business_impact}</span>
                <br>
                <span class="relevance-score">${item.relevance_score || 0}% relevant</span>
              </div>
            </div>
          </div>
          <div class="modal-news-body">
            <p class="mb-3">${item.summary || 'No summary available'}</p>
            <div class="d-flex justify-content-between align-items-center">
              <small class="text-muted">
                <i class="bi bi-calendar3 me-1"></i>
                ${publishedDate}
              </small>
              ${item.source_url ? `
                <a href="${item.source_url}" target="_blank" class="btn btn-sm btn-outline-primary">
                  <i class="bi bi-link-45deg me-1"></i>
                  Read Source
                </a>
              ` : ''}
            </div>
          </div>
        </div>
      `;
    });
    return html;
  }
}

// Enhanced Sales Dashboard System with Customer Exclusion
let comparedCustomers = new Map();
let excludedCustomers = new Map();
const customerColors = [
    'rgba(34, 197, 94, 1)', 'rgba(239, 68, 68, 1)', 'rgba(168, 85, 247, 1)',
    'rgba(245, 158, 11, 1)', 'rgba(59, 130, 246, 1)', 'rgba(236, 72, 153, 1)',
    'rgba(14, 165, 233, 1)', 'rgba(156, 163, 175, 1)', 'rgba(139, 69, 19, 1)'
];

document.addEventListener('DOMContentLoaded', function() {
    if (console.time) {
        console.time('activity.init');
    }
    logActivityTiming('init_start');
    // Initialize news manager
    window.customerNewsManager = new CustomerNewsManager();
    window.customerNewsManager.loadCachedNews();
    // Call list + comms initialized later to avoid double fetches.

    const statusSelects = document.querySelectorAll('.parts-list-status-select');
    statusSelects.forEach(select => {
        select.addEventListener('change', () => {
            const listId = select.dataset.listId;
            const statusId = parseInt(select.value, 10);
            const updateUrl = select.dataset.updateUrl;
            const statusEl = document.querySelector(
                `.parts-list-update-status[data-list-id="${listId}"]`
            );
            if (Number.isNaN(statusId)) {
                return;
            }
            updatePartsListHeader(listId, { status_id: statusId }, statusEl, updateUrl);
        });
    });

    const commentInputs = document.querySelectorAll('.parts-list-comment-input');
    commentInputs.forEach(input => {
        let timer = null;
        const listId = input.dataset.listId;
        const updateUrl = input.dataset.updateUrl;
        const statusEl = document.querySelector(
            `.parts-list-update-status[data-list-id="${listId}"]`
        );
        const scheduleSave = () => {
            if (timer) {
                clearTimeout(timer);
            }
            timer = setTimeout(() => {
                updatePartsListHeader(listId, { notes: input.value }, statusEl, updateUrl);
            }, 600);
        };
        input.addEventListener('input', scheduleSave);
        input.addEventListener('blur', () => {
            updatePartsListHeader(listId, { notes: input.value }, statusEl, updateUrl);
        });
    });

    const previewButtons = document.querySelectorAll('.parts-list-preview-btn');
    previewButtons.forEach(button => {
        button.addEventListener('click', () => {
            showPartsListPreview({
                listId: button.dataset.listId,
                listName: button.dataset.listName,
                customerName: button.dataset.customerName,
                linesUrl: button.dataset.linesUrl
            });
        });
    });

    // Chart variables
    const salesChartCtx = document.getElementById('salesChart').getContext('2d');
    let salesChart;
    let currentCustomerFilter = null;
    let customerSalesData = null;
    let allCustomers = [];
    let originalPersonalSalesData = null;
    let originalAccountSalesData = null;

    // Create labels for last 24 months
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const currentDate = new Date();
    const labels = [];
    for (let i = 23; i >= 0; i--) {
        const month = new Date(currentDate);
        month.setMonth(currentDate.getMonth() - i);
        const monthLabel = months[month.getMonth()] + ' ' + month.getFullYear();
        labels.push(monthLabel);
    }

    // Create gradient fills for trendy chart styling
    const createGradient = (ctx, colorStart, colorEnd) => {
        const gradient = ctx.createLinearGradient(0, 0, 0, 400);
        gradient.addColorStop(0, colorStart);
        gradient.addColorStop(1, colorEnd);
        return gradient;
    };

    // Initialize chart data with modern styling
    let personalSalesData = {
        labels: labels,
        datasets: [{
            label: 'Sales Value (£)',
            data: Array(24).fill(0),
            backgroundColor: function(context) {
                const chart = context.chart;
                const {ctx, chartArea} = chart;
                if (!chartArea) return 'rgba(54, 162, 235, 0.1)';
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(54, 162, 235, 0.0)');
                gradient.addColorStop(0.5, 'rgba(54, 162, 235, 0.1)');
                gradient.addColorStop(1, 'rgba(54, 162, 235, 0.3)');
                return gradient;
            },
            borderColor: 'rgb(54, 162, 235)',
            borderWidth: 3,
            tension: 0.4,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 8,
            pointHoverBackgroundColor: 'rgb(54, 162, 235)',
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 3,
            pointHitRadius: 20
        }]
    };

    let accountSalesData = {
        labels: labels,
        datasets: [{
            label: 'Account Sales Value (£)',
            data: Array(24).fill(0),
            backgroundColor: function(context) {
                const chart = context.chart;
                const {ctx, chartArea} = chart;
                if (!chartArea) return 'rgba(255, 159, 64, 0.1)';
                const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                gradient.addColorStop(0, 'rgba(255, 159, 64, 0.0)');
                gradient.addColorStop(0.5, 'rgba(255, 159, 64, 0.1)');
                gradient.addColorStop(1, 'rgba(255, 159, 64, 0.3)');
                return gradient;
            },
            borderColor: 'rgb(255, 159, 64)',
            borderWidth: 3,
            tension: 0.4,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 8,
            pointHoverBackgroundColor: 'rgb(255, 159, 64)',
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 3,
            pointHitRadius: 20
        }]
    };

    // Store customer data for tooltips
    let monthlyCustomerData = {
        personal: {},
        account: {}
    };

    // Initialize sales chart
    logActivityTiming('chart_init_start');
    if (console.time) {
        console.time('activity.chart_init');
    }
    salesChart = new Chart(salesChartCtx, {
        type: 'line',
        data: personalSalesData,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            onClick: function(event, elements) {
                if (elements.length > 0) {
                    const clickedElement = elements[0];
                    const monthIndex = clickedElement.index;
                    const monthLabel = this.data.labels[monthIndex];

                    if (currentCustomerFilter) {
                        showCustomerMonthlyBreakdown(monthIndex, monthLabel, currentCustomerFilter);
                    } else {
                        showMonthlyBreakdown(monthIndex, monthLabel);
                    }
                }
            },
            animation: {
                duration: 750,
                easing: 'easeOutQuart'
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Sales Value (£)',
                        color: '#6b7280',
                        font: { weight: '500' }
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        color: '#6b7280',
                        padding: 10
                    },
                    border: {
                        display: false
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: 'Month (click for breakdown)',
                        color: '#6b7280',
                        font: { weight: '500' }
                    },
                    ticks: {
                        maxTicksLimit: 12,
                        maxRotation: 45,
                        minRotation: 0,
                        color: '#6b7280',
                        padding: 8
                    },
                    grid: {
                        display: false
                    },
                    border: {
                        display: false
                    }
                }
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'circle',
                        padding: 20,
                        color: '#6b7280',
                        font: { size: 12 }
                    }
                },
                tooltip: {
                    enabled: true,
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(17, 24, 39, 0.95)',
                    titleColor: '#f9fafb',
                    bodyColor: '#d1d5db',
                    borderColor: 'rgba(99, 102, 241, 0.3)',
                    borderWidth: 1,
                    cornerRadius: 12,
                    displayColors: true,
                    boxWidth: 8,
                    boxHeight: 8,
                    boxPadding: 4,
                    usePointStyle: true,
                    padding: 16,
                    bodyFont: { size: 13 },
                    titleFont: { size: 14, weight: '600' },
                    callbacks: {
                        title: function(tooltipItems) {
                            let suffix = '';
                            if (currentCustomerFilter) {
                                suffix = ' (customer-specific)';
                            } else if (excludedCustomers.size > 0) {
                                suffix = ` (${excludedCustomers.size} customer${excludedCustomers.size !== 1 ? 's' : ''} excluded)`;
                            }
                            return tooltipItems[0].label + suffix + ' (click for details)';
                        },
                        beforeBody: function(tooltipItems) {
                            if (currentCustomerFilter) {
                                return ['', `Customer: ${currentCustomerFilter.name}`, ''];
                            }

                            const monthIndex = tooltipItems[0].dataIndex;
                            const currentView = document.getElementById('personal-sales-btn').classList.contains('active')
                                ? 'personal' : 'account';

                            if (!monthlyCustomerData[currentView]) {
                                return ['', 'Sales Details:'];
                            }

                            const monthData = monthlyCustomerData[currentView][monthIndex];
                            if (!monthData || !Array.isArray(monthData) || monthData.length === 0) {
                                return ['', 'Sales Details:'];
                            }

                            return ['', 'Top Customers:'];
                        },
                        afterLabel: function(tooltipItem) {
                            const monthIndex = tooltipItem.dataIndex;
                            const salesValue = tooltipItem.parsed.y;

                            if (currentCustomerFilter && customerSalesData) {
                                const monthDetails = customerSalesData.monthly_details[monthIndex];
                                if (!monthDetails || salesValue === 0) {
                                    return ['', 'No sales recorded for this month'];
                                }

                                return ['',
                                    `Total Sales: £${salesValue.toLocaleString('en-US', {
                                        minimumFractionDigits: 0,
                                        maximumFractionDigits: 0
                                    })}`,
                                    `Orders: ${monthDetails.order_count}`,
                                    `Part Numbers: ${monthDetails.part_count}`,
                                    `Total Quantity: ${monthDetails.total_quantity}`
                                ];
                            }

                            const currentView = document.getElementById('personal-sales-btn').classList.contains('active')
                                ? 'personal' : 'account';
                            const monthData = monthlyCustomerData[currentView] && monthlyCustomerData[currentView][monthIndex];

                            if (!monthData || !Array.isArray(monthData) || monthData.length === 0) {
                                if (salesValue > 0) {
                                    return ['', `Total Sales: £${salesValue.toLocaleString('en-US', {
                                        minimumFractionDigits: 0,
                                        maximumFractionDigits: 0
                                    })}`, '', 'Detailed customer breakdown not available'];
                                } else {
                                    return ['', 'No sales recorded for this month'];
                                }
                            }

                            // Filter out excluded customers from tooltip
                            const filteredMonthData = monthData.filter(customer =>
                                !excludedCustomers.has(customer.customer_id.toString())
                            );

                            const topCustomers = filteredMonthData.slice(0, 5);
                            const customerLines = [''];

                            topCustomers.forEach((customer, index) => {
                                const rank = index + 1;
                                const name = customer.customer_name.length > 20
                                    ? customer.customer_name.substring(0, 20) + '...'
                                    : customer.customer_name;
                                const value = `£${customer.total_value.toLocaleString('en-US', {
                                    minimumFractionDigits: 0,
                                    maximumFractionDigits: 0
                                })}`;

                                customerLines.push(`${rank}. ${name} - ${value}`);
                            });

                            if (filteredMonthData.length > 5) {
                                const remaining = filteredMonthData.length - 5;
                                customerLines.push(`   ...and ${remaining} more`);
                            }

                            if (excludedCustomers.size > 0) {
                                customerLines.push('', `(${excludedCustomers.size} customer${excludedCustomers.size !== 1 ? 's' : ''} excluded from view)`);
                            }

                            return customerLines;
                        }
                    }
                },
                legend: {
                    display: true,
                    position: 'top'
                }
            }
        }
    });
    if (console.timeEnd) {
        console.timeEnd('activity.chart_init');
    }
    logActivityTiming('chart_init_end');

    // DOM elements
    const customerFilter = document.getElementById('customerFilter');
    const addCustomerBtn = document.getElementById('addCustomerBtn');
    const excludeCustomerBtn = document.getElementById('excludeCustomerBtn');
    const clearAllFilters = document.getElementById('clearAllFilters');
    const activeFiltersSection = document.getElementById('activeFiltersSection');
    const comparedCustomersSection = document.getElementById('comparedCustomersSection');
    const excludedCustomersSection = document.getElementById('excludedCustomersSection');
    const comparedCustomersList = document.getElementById('comparedCustomersList');
    const excludedCustomersList = document.getElementById('excludedCustomersList');
    const chartInfoText = document.getElementById('chartInfoText');

    // Event listeners
    customerFilter.addEventListener('change', function() {
        const customerId = this.value;
        if (customerId && customerId !== '') {
            const selectedCustomer = allCustomers.find(c => c.id == customerId);
            if (selectedCustomer) {
                currentCustomerFilter = selectedCustomer;
                loadCustomerSalesData(customerId);
                updateFiltersDisplay();
                document.querySelector('.card-title').textContent =
                    `Sales Performance - ${selectedCustomer.name} (Last 24 Months)`;
            }
        } else {
            clearIndividualCustomerFilter();
        }
    });

    addCustomerBtn.addEventListener('click', function() {
    const customerId = customerFilter.value;
    if (!customerId) {
        alert('Please select a customer to add to comparison');
        return;
    }
    if (comparedCustomers.has(customerId)) {
        alert('This customer is already in the comparison');
        return;
    }
    if (excludedCustomers.has(customerId)) {
        alert('This customer is currently excluded. Remove from exclusions first.');
        return;
    }
    if (comparedCustomers.size >= 5) {
        alert('Maximum 5 customers can be compared at once');
        return;
    }

    const customer = allCustomers.find(c => c.id == customerId);
    if (customer) {
        addCustomerToComparison(customer);
        customerFilter.value = ''; // Clear selection after adding
    }
});

    excludeCustomerBtn.addEventListener('click', function() {
    const customerId = customerFilter.value;
    if (!customerId) {
        alert('Please select a customer to exclude');
        return;
    }
    if (excludedCustomers.has(customerId)) {
        alert('This customer is already excluded');
        return;
    }
    if (comparedCustomers.has(customerId)) {
        // Remove from comparison first, then add to exclusions
        comparedCustomers.delete(customerId);
    }

    const customer = allCustomers.find(c => c.id == customerId);
    if (customer) {
        addCustomerToExclusions(customer);
        customerFilter.value = ''; // Clear selection after excluding
    }
});

    clearAllFilters.addEventListener('click', function() {
        comparedCustomers.clear();
        excludedCustomers.clear();
        clearIndividualCustomerFilter();
        updateFiltersDisplay();
        updateChart();
        updateChartInfoText();
    });

    document.getElementById('personal-sales-btn').addEventListener('click', function() {
        this.classList.add('active');
        document.getElementById('account-sales-btn').classList.remove('active');
        updateChart();
    });

    document.getElementById('account-sales-btn').addEventListener('click', function() {
        this.classList.add('active');
        document.getElementById('personal-sales-btn').classList.remove('active');
        updateChart();
    });

    document.getElementById('refreshCommBtn').addEventListener('click', function() {
        const selectedDate = document.getElementById('commDatePicker').value;
        fetchRecentCommunications(selectedDate);
    });

    // Global functions
    window.showCustomerDetails = function(customerId) {
        const customerDetailsModal = new bootstrap.Modal(document.getElementById('customerDetailsModal'));
        const customerDetailsContent = document.getElementById('customerDetailsContent');

        customerDetailsContent.innerHTML = `
            <div class="text-center">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
        `;

        customerDetailsModal.show();

        fetch(`/salespeople/customer_details/${customerId}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.text())
        .then(html => {
            customerDetailsContent.innerHTML = html;
        });
    };

    window.removeCustomerFromComparison = function(customerId) {
        comparedCustomers.delete(customerId);
        updateFiltersDisplay();
        updateChart();
        updateChartInfoText();
    };

    window.removeCustomerFromExclusions = function(customerId) {
        excludedCustomers.delete(customerId);
        updateFiltersDisplay();
        updateChart();
        updateChartInfoText();
    };

    // Helper functions
    function clearIndividualCustomerFilter() {
    currentCustomerFilter = null;
    customerSalesData = null;
    customerFilter.value = '';
    document.querySelector('.card-title').textContent = 'Sales Performance (Last 24 Months)';
    // Don't call updateChart here if we're in the middle of adding comparisons
}

    function addCustomerToComparison(customer) {
    // Clear individual customer filter first
    clearIndividualCustomerFilter();

    loadCustomerSalesData(customer.id.toString(), function(customerSalesData) {
        comparedCustomers.set(customer.id.toString(), {
            customer: customer,
            salesData: customerSalesData
        });

        updateFiltersDisplay();
        updateChart();
        updateChartInfoText();
    });
}

    function addCustomerToExclusions(customer) {
    // Clear individual customer filter first
    clearIndividualCustomerFilter();

    excludedCustomers.set(customer.id.toString(), customer);
    updateFiltersDisplay();
    updateChart();
    updateChartInfoText();
}

    function updateFiltersDisplay() {
        const hasCompared = comparedCustomers.size > 0;
        const hasExcluded = excludedCustomers.size > 0;
        const hasIndividual = currentCustomerFilter !== null;
        const hasAnyFilters = hasCompared || hasExcluded || hasIndividual;

        // Show/hide the filters section
        activeFiltersSection.style.display = hasAnyFilters ? 'block' : 'none';
        clearAllFilters.style.display = hasAnyFilters ? 'inline-block' : 'none';

        // Update compared customers display
        comparedCustomersSection.style.display = hasCompared ? 'block' : 'none';
        if (hasCompared) {
            updateComparedCustomersDisplay();
        }

        // Update excluded customers display
        excludedCustomersSection.style.display = hasExcluded ? 'block' : 'none';
        if (hasExcluded) {
            updateExcludedCustomersDisplay();
        }
    }

    function updateComparedCustomersDisplay() {
        comparedCustomersList.innerHTML = '';

        Array.from(comparedCustomers.entries()).forEach(([customerId, data], index) => {
            const customer = data.customer;
            const colorIndex = index % customerColors.length;
            const color = customerColors[colorIndex];

            const customerTag = document.createElement('div');
            customerTag.className = 'badge bg-light text-dark d-flex align-items-center me-2 mb-1';
            customerTag.style.border = `2px solid ${color}`;
            customerTag.innerHTML = `
                <span class="me-2" style="width: 10px; height: 10px; border-radius: 50%; background-color: ${color};"></span>
                ${customer.name}
                <button class="btn-close btn-close-sm ms-2" onclick="removeCustomerFromComparison('${customerId}')" title="Remove customer"></button>
            `;

            comparedCustomersList.appendChild(customerTag);
        });
    }

    function updateExcludedCustomersDisplay() {
        excludedCustomersList.innerHTML = '';

        Array.from(excludedCustomers.entries()).forEach(([customerId, customer]) => {
            const customerTag = document.createElement('div');
            customerTag.className = 'badge bg-warning text-dark d-flex align-items-center me-2 mb-1';
            customerTag.innerHTML = `
                <i class="bi bi-dash-circle me-2"></i>
                ${customer.name}
                <button class="btn-close btn-close-sm ms-2" onclick="removeCustomerFromExclusions('${customerId}')" title="Remove exclusion"></button>
            `;

            excludedCustomersList.appendChild(customerTag);
        });
    }

   function updateChart() {
    if (currentCustomerFilter && customerSalesData) {
        // Show individual customer data
        const customerChartData = {
            labels: customerSalesData.labels,
            datasets: [{
                label: `${customerSalesData.customer_name} Sales (£)`,
                data: customerSalesData.values,
                backgroundColor: function(context) {
                    const chart = context.chart;
                    const {ctx, chartArea} = chart;
                    if (!chartArea) return 'rgba(34, 197, 94, 0.1)';
                    const gradient = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
                    gradient.addColorStop(0, 'rgba(34, 197, 94, 0.0)');
                    gradient.addColorStop(0.5, 'rgba(34, 197, 94, 0.1)');
                    gradient.addColorStop(1, 'rgba(34, 197, 94, 0.3)');
                    return gradient;
                },
                borderColor: 'rgb(34, 197, 94)',
                borderWidth: 3,
                tension: 0.4,
                fill: true,
                pointRadius: 0,
                pointHoverRadius: 8,
                pointHoverBackgroundColor: 'rgb(34, 197, 94)',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 3,
                pointHitRadius: 20
            }]
        };
        salesChart.data = customerChartData;
                scheduleRender(() => {
                    salesChart.update();
                }, 50);
    } else if (comparedCustomers.size > 0) {
        // Show comparison view
        updateMultiCustomerChart();
        // updateMultiCustomerChart handles the chart update
        return;
    } else {
        // Show main view (with exclusions if any)
        const isPersonalView = document.getElementById('personal-sales-btn').classList.contains('active');

        if (excludedCustomers.size > 0) {
            // Calculate excluded sales data
            const baseData = isPersonalView ? originalPersonalSalesData : originalAccountSalesData;
            const excludedData = calculateExcludedSalesData(baseData, isPersonalView);
            salesChart.data = excludedData;
        } else {
            // Show original data
            salesChart.data = isPersonalView ? personalSalesData : accountSalesData;
        }
        salesChart.update();
    }
}

    function calculateExcludedSalesData(baseData, isPersonalView) {
        const currentView = isPersonalView ? 'personal' : 'account';
        const excludedIds = Array.from(excludedCustomers.keys());

        // Clone the base data
        const excludedData = JSON.parse(JSON.stringify(baseData));

        // Calculate new values excluding specified customers
        const newValues = [];

        for (let monthIndex = 0; monthIndex < baseData.datasets[0].data.length; monthIndex++) {
            let monthTotal = baseData.datasets[0].data[monthIndex];

            // Subtract excluded customers' contributions for this month
            const monthData = monthlyCustomerData[currentView] && monthlyCustomerData[currentView][monthIndex];
            if (monthData && Array.isArray(monthData)) {
                const excludedValue = monthData
                    .filter(customer => excludedIds.includes(customer.customer_id.toString()))
                    .reduce((sum, customer) => sum + customer.total_value, 0);

                monthTotal = Math.max(0, monthTotal - excludedValue);
            }

            newValues.push(monthTotal);
        }

        excludedData.datasets[0].data = newValues;
        excludedData.datasets[0].label = excludedData.datasets[0].label +
            ` (${excludedCustomers.size} customer${excludedCustomers.size !== 1 ? 's' : ''} excluded)`;

        return excludedData;
    }

    function updateMultiCustomerChart() {
    const datasets = [];
    const isPersonalView = document.getElementById('personal-sales-btn').classList.contains('active');
    const baselineData = isPersonalView ? personalSalesData : accountSalesData;

    // Check if we have compared customers
    if (comparedCustomers.size === 0) {
        // No compared customers, fall back to regular chart
        updateChart();
        return;
    }

    // Add baseline (potentially with exclusions)
    let baselineDataset;
    if (excludedCustomers.size > 0) {
        const excludedBaseData = calculateExcludedSalesData(baselineData, isPersonalView);
        baselineDataset = excludedBaseData.datasets[0];
    } else {
        baselineDataset = JSON.parse(JSON.stringify(baselineData.datasets[0]));
    }

    baselineDataset.backgroundColor = 'rgba(156, 163, 175, 0.05)';
    baselineDataset.borderColor = 'rgba(156, 163, 175, 0.6)';
    baselineDataset.borderDash = [5, 5];
    baselineDataset.borderWidth = 2;
    baselineDataset.tension = 0.4;
    baselineDataset.fill = true;
    baselineDataset.pointRadius = 0;
    baselineDataset.pointHoverRadius = 6;
    datasets.push(baselineDataset);

    // Modern color palette for compared customers
    const modernColors = [
        'rgb(239, 68, 68)',    // red
        'rgb(34, 197, 94)',    // green
        'rgb(168, 85, 247)',   // purple
        'rgb(14, 165, 233)',   // sky
        'rgb(249, 115, 22)',   // orange
        'rgb(236, 72, 153)'    // pink
    ];

    // Add compared customers
    Array.from(comparedCustomers.entries()).forEach(([customerId, data], index) => {
        const colorIndex = index % modernColors.length;
        const color = modernColors[colorIndex];
        const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
        const [_, r, g, b] = rgbMatch || [null, 99, 102, 241];

        datasets.push({
            label: `${data.customer.name} (£)`,
            data: data.salesData.values,
            backgroundColor: `rgba(${r}, ${g}, ${b}, 0.1)`,
            borderColor: color,
            borderWidth: 3,
            tension: 0.4,
            fill: true,
            pointRadius: 0,
            pointHoverRadius: 8,
            pointHoverBackgroundColor: color,
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 3,
            pointHitRadius: 20
        });
    });

    salesChart.data = {
        labels: labels,
        datasets: datasets
    };

    salesChart.update();
}

    function updateChartInfoText() {
        let infoText = 'Click on any point to see detailed breakdown for that month';

        if (currentCustomerFilter) {
            infoText = `Viewing ${currentCustomerFilter.name} - ` + infoText;
        } else if (comparedCustomers.size > 0) {
            infoText = `Comparing ${comparedCustomers.size} customer${comparedCustomers.size !== 1 ? 's' : ''} - ` + infoText;
        } else if (excludedCustomers.size > 0) {
            infoText = `${excludedCustomers.size} customer${excludedCustomers.size !== 1 ? 's' : ''} excluded - ` + infoText;
        }

        chartInfoText.textContent = infoText;
    }

    function loadCustomerSalesData(customerId, callback) {
    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    fetch(`/salespeople/${salespersonId}/customer_sales_data/${customerId}`)
        .then(response => response.json())
        .then(data => {
            if (callback) {
                callback(data);
            } else {
                customerSalesData = data;
                updateChart();
            }
        })
        .catch(error => {
            console.error('Error fetching customer sales data:', error);
            if (!callback) {
                alert('Unable to load customer sales data. Please try again.');
                clearIndividualCustomerFilter();
            }
        });
}

    function loadCustomerList() {
        const urlParts = window.location.pathname.split('/');
        const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

        logActivityTiming('customer_list_fetch_start');
        fetch(`/salespeople/${salespersonId}/customer_list`)
            .then(response => response.json())
            .then(data => {
                scheduleRender(() => {
                    allCustomers = data.customers;
                    customerFilter.innerHTML = '<option value="">Select a customer...</option>';
                    data.customers.forEach(customer => {
                        const option = document.createElement('option');
                        option.value = customer.id;
                        option.textContent = `${customer.name} (?${customer.total_value.toLocaleString()})`;
                        customerFilter.appendChild(option);
                    });
                });
                logActivityTiming('customer_list_render');
                if (console.timeEnd) {
                    console.timeEnd('activity.customer_list');
                }
            })
            .catch(error => {
                console.error('Error fetching customer list:', error);
                logActivityTiming('customer_list_error');
                if (console.timeEnd) {
                    console.timeEnd('activity.customer_list');
                }
                customerFilter.innerHTML = '<option value="">Error loading customers</option>';
            });
    }

    function fetchSalesData() {
        const urlParts = window.location.pathname.split('/');
        const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

        logActivityTiming('sales_data_fetch_start');
        fetch(`/salespeople/${salespersonId}/sales_data`)
            .then(response => response.json())
            .then(data => {
                // Update stats cards
                if (data.yesterday_sales) {
                    document.querySelector('.card.bg-primary .display-4').textContent =
                        '£' + data.yesterday_sales.total_value.toFixed(2);
                    document.querySelector('.card.bg-primary .mb-0').textContent =
                        `Orders: ${data.yesterday_sales.order_count}`;
                }

                if (data.month_sales) {
                    document.querySelector('.card.bg-success .display-4').textContent =
                        '£' + data.month_sales.total_value.toFixed(2);
                    document.querySelector('.card.bg-success .mb-0').textContent =
                        `Orders: ${data.month_sales.order_count}`;
                }

                // Store original data
                if (data.personal_sales && data.personal_sales.labels && data.personal_sales.values) {
                    personalSalesData.labels = data.personal_sales.labels;
                    personalSalesData.datasets[0].data = data.personal_sales.values;
                    originalPersonalSalesData = JSON.parse(JSON.stringify(personalSalesData));
                    if (data.personal_sales.monthly_customers) {
                        monthlyCustomerData.personal = data.personal_sales.monthly_customers;
                    }
                }

                if (data.account_sales && data.account_sales.labels && data.account_sales.values) {
                    accountSalesData.labels = data.account_sales.labels;
                    accountSalesData.datasets[0].data = data.account_sales.values;
                    originalAccountSalesData = JSON.parse(JSON.stringify(accountSalesData));
                    if (data.account_sales.monthly_customers) {
                        monthlyCustomerData.account = data.account_sales.monthly_customers;
                    }
                }

                scheduleRender(() => {
                    salesChart.update();
                }, 50);
                logActivityTiming('sales_data_render');
                if (console.timeEnd) {
                    console.timeEnd('activity.sales_data');
                }
            })
            .catch(error => {
                console.error('Error fetching sales data:', error);
                logActivityTiming('sales_data_error');
                if (console.timeEnd) {
                    console.timeEnd('activity.sales_data');
                }
            });
    }

    function initializeDatePicker() {
        const datePicker = document.getElementById('commDatePicker');
        const today = new Date();
        let defaultDate = new Date(today);

        if (today.getDay() === 1) {
            defaultDate.setDate(today.getDate() - 3);
        } else if (today.getDay() === 0) {
            defaultDate.setDate(today.getDate() - 2);
        } else {
            defaultDate.setDate(today.getDate() - 1);
        }

        const year = defaultDate.getFullYear();
        const month = String(defaultDate.getMonth() + 1).padStart(2, '0');
        const day = String(defaultDate.getDate()).padStart(2, '0');
        datePicker.value = `${year}-${month}-${day}`;

        datePicker.addEventListener('change', function() {
            fetchRecentCommunications(this.value);
        });
    }

    function fetchRecentCommunications(targetDate = null) {
        const urlParts = window.location.pathname.split('/');
        const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

        document.getElementById('communicationTabs').innerHTML = `
            <div class="text-center text-muted py-4">
                <div class="spinner-border spinner-border-sm" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-2 mb-0">Loading communications...</p>
            </div>
        `;

        let url = `/salespeople/${salespersonId}/recent_communications`;
        if (targetDate) {
            url += `?date=${targetDate}`;
        }

        logActivityTiming('recent_comms_fetch_start');
        return fetch(url)
            .then(response => response.json())
            .then(data => {
                scheduleRender(() => {
                    displayCommunicationsByType(data);
                });
                logActivityTiming('recent_comms_render');
                if (console.timeEnd) {
                    console.timeEnd('activity.recent_comms');
                }
            })
            .catch(error => {
                console.error('Error fetching communications:', error);
                logActivityTiming('recent_comms_error');
                if (console.timeEnd) {
                    console.timeEnd('activity.recent_comms');
                }
                document.getElementById('communicationTabs').innerHTML = `
                    <div class="text-center text-danger py-4">
                        <i class="bi bi-exclamation-triangle"></i>
                        <p>Unable to load recent communications</p>
                        <button class="btn btn-sm btn-outline-primary" onclick="fetchRecentCommunications()">
                            <i class="bi bi-arrow-clockwise me-1"></i>Try Again
                        </button>
                    </div>
                `;
            });
    }

    function displayCommunicationsByType(data) {
        const container = document.getElementById('communicationTabs');
        const communications = data.communications || {};
        const types = Object.keys(communications);

        document.getElementById('recentCommTitle').textContent =
            `Recent Communications - ${data.target_date_formatted}`;

        if (data.total_count === 0) {
            container.innerHTML = `
                <div class="text-center py-5 text-muted">
                    <i class="bi bi-chat-dots fs-1"></i>
                    <h6 class="mt-3">No Communications Found</h6>
                    <p>No communications recorded for ${data.target_date_formatted}</p>
                </div>
            `;
            return;
        }

        let html = '';
        if (types.length > 1) {
            html += `<ul class="nav nav-tabs" role="tablist">
                <li class="nav-item">
                    <button class="nav-link active" data-bs-toggle="tab" data-bs-target="#all-comms">
                        All <span class="badge bg-primary ms-1">${data.total_count}</span>
                    </button>
                </li>`;

            types.forEach(type => {
                const typeCount = Object.values(communications[type] || {})
                    .reduce((sum, comms) => sum + comms.length, 0);
                const safeName = type.toLowerCase().replace(/[^a-z0-9]/g, '-');
                html += `<li class="nav-item">
                    <button class="nav-link" data-bs-toggle="tab" data-bs-target="#${safeName}-comms">
                        ${type} <span class="badge bg-secondary ms-1">${typeCount}</span>
                    </button>
                </li>`;
            });

            html += '</ul><div class="tab-content">';
            html += `<div class="tab-pane fade show active" id="all-comms">
                ${buildCommunicationsTable(communications, true)}
            </div>`;

            types.forEach(type => {
                const safeName = type.toLowerCase().replace(/[^a-z0-9]/g, '-');
                html += `<div class="tab-pane fade" id="${safeName}-comms">
                    ${buildCommunicationsTable({[type]: communications[type]}, false)}
                </div>`;
            });
            html += '</div>';
        } else {
            html = buildCommunicationsTable(communications, false);
        }

        container.innerHTML = html;
        if (types.length > 1) {
            container.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
                new bootstrap.Tab(tab);
            });
        }
    }

    function buildCommunicationsTable(communicationsData, showGroupHeaders = false) {
        const allComms = [];
        Object.keys(communicationsData).forEach(type => {
            Object.keys(communicationsData[type] || {}).forEach(company => {
                communicationsData[type][company].forEach(comm => {
                    allComms.push({
                        ...comm,
                        communication_type: type,
                        company_name: company
                    });
                });
            });
        });

        if (allComms.length === 0) {
            return `<div class="text-center py-5 text-muted">
                <i class="bi bi-chat-dots fs-1"></i>
                <p class="mt-3">No communications found</p>
            </div>`;
        }

        allComms.sort((a, b) => {
            if (!a.time && !b.time) return 0;
            if (!a.time) return 1;
            if (!b.time) return -1;
            return b.time.localeCompare(a.time);
        });

        let html = `<table class="table"><thead><tr>
            <th style="width: 25%;">Contact</th>
            <th style="width: 15%;">Type</th>
            <th style="width: 45%;">Notes</th>
            <th style="width: 15%;">Time</th>
        </tr></thead><tbody>`;

        let currentType = '';
        allComms.forEach(comm => {
            if (showGroupHeaders && comm.communication_type !== currentType) {
                html += `<tr><td colspan="4" class="table-secondary fw-bold">
                    <i class="bi bi-${getIconForCommType(comm.communication_type)} me-2"></i>
                    ${comm.communication_type}
                </td></tr>`;
                currentType = comm.communication_type;
            }

            const timeFormatted = comm.time ?
                new Date(`1970-01-01T${comm.time}`).toLocaleTimeString([], {
                    hour: '2-digit', minute: '2-digit', hour12: true
                }) : '';

            const notes = comm.notes && comm.notes.trim() ?
                comm.notes : 'No notes recorded';
            const notesClass = comm.notes && comm.notes.trim() ?
                '' : 'text-muted fst-italic';

            html += `<tr>
                <td>
                    <div class="fw-semibold">${comm.contact_name}</div>
                    <div class="small text-muted">
                        ${comm.company_name}
                        ${comm.job_title ? ` • ${comm.job_title}` : ''}
                    </div>
                </td>
                <td>
                    <span class="badge bg-light text-dark">
                        <i class="bi bi-${getIconForCommType(comm.communication_type)} me-1"></i>
                        ${comm.communication_type}
                    </span>
                </td>
                <td><div class="${notesClass}">${notes}</div></td>
                <td class="text-end small text-muted">${timeFormatted}</td>
            </tr>`;
        });

        return html + '</tbody></table>';
    }

    function getIconForCommType(type) {
        const icons = {
            'Phone': 'telephone', 'Call': 'telephone', 'Email': 'envelope',
            'Meeting': 'calendar-event', 'Video Call': 'camera-video',
            'Text': 'chat-text', 'Visit': 'geo-alt', 'Other': 'chat-dots'
        };
        return icons[type] || 'chat-dots';
    }

    function showMonthlyBreakdown(monthIndex, monthLabel) {
        const urlParts = window.location.pathname.split('/');
        const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];
        const isPersonalView = document.getElementById('personal-sales-btn').classList.contains('active');
        const viewType = isPersonalView ? 'personal' : 'account';

        const modal = new bootstrap.Modal(document.getElementById('salesMonthlyBreakdownModal'));
        const modalContent = document.getElementById('salesMonthlyBreakdownContent');
        const modalTitle = document.getElementById('salesMonthlyBreakdownLabel');

        modalTitle.textContent = `${monthLabel} - ${isPersonalView ? 'Personal' : 'Account'} Sales Breakdown`;
        modalContent.innerHTML = `<div class="text-center py-5">
            <div class="spinner-border" role="status"></div>
            <p class="mt-2">Loading sales breakdown...</p>
        </div>`;
        modal.show();

        fetch(`/salespeople/${salespersonId}/monthly_breakdown/${monthIndex}?view=${viewType}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.json())
        .then(data => displayMonthlyBreakdown(data))
        .catch(error => {
            console.error('Error fetching monthly breakdown:', error);
            modalContent.innerHTML = `<div class="text-center py-5">
                <i class="bi bi-exclamation-triangle fs-1 text-warning"></i>
                <h5 class="mt-3">Unable to load breakdown</h5>
                <p class="text-muted">${error.message}</p>
            </div>`;
        });
    }

    function showCustomerMonthlyBreakdown(monthIndex, monthLabel, customer) {
        const urlParts = window.location.pathname.split('/');
        const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

        const modal = new bootstrap.Modal(document.getElementById('salesMonthlyBreakdownModal'));
        const modalContent = document.getElementById('salesMonthlyBreakdownContent');
        const modalTitle = document.getElementById('salesMonthlyBreakdownLabel');

        modalTitle.textContent = `${monthLabel} - ${customer.name} Sales Breakdown`;
        modalContent.innerHTML = `<div class="text-center py-5">
            <div class="spinner-border" role="status"></div>
            <p class="mt-2">Loading customer breakdown...</p>
        </div>`;
        modal.show();

        fetch(`/salespeople/${salespersonId}/monthly_breakdown/${monthIndex}?view=personal&customer_id=${customer.id}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(response => response.json())
        .then(data => displayMonthlyBreakdown(data))
        .catch(error => {
            console.error('Error fetching customer monthly breakdown:', error);
            modalContent.innerHTML = `<div class="text-center py-5">
                <i class="bi bi-exclamation-triangle fs-1 text-warning"></i>
                <h5 class="mt-3">Unable to load breakdown</h5>
            </div>`;
        });
    }

    function displayMonthlyBreakdown(data) {
        const modalContent = document.getElementById('salesMonthlyBreakdownContent');

        if (!data.customers || data.customers.length === 0) {
            modalContent.innerHTML = `<div class="text-center py-5">
                <i class="bi bi-graph-down fs-1 text-muted"></i>
                <h5 class="mt-3">No Sales Data</h5>
                <p class="text-muted">No sales were recorded for ${data.month_label}</p>
            </div>`;
            return;
        }

        const totalValue = data.customers.reduce((sum, customer) => sum + customer.total_value, 0);
        const totalParts = data.customers.reduce((sum, customer) => sum + customer.total_parts, 0);

        let html = `<div class="bg-light p-3 rounded mb-3">
            <div class="row text-center">
                <div class="col-md-4">
                    <h4 class="text-primary mb-1">£${totalValue.toLocaleString()}</h4>
                    <small class="text-muted">Total Sales</small>
                </div>
                <div class="col-md-4">
                    <h4 class="text-success mb-1">${data.customers.length}</h4>
                    <small class="text-muted">Customers</small>
                </div>
                <div class="col-md-4">
                    <h4 class="text-info mb-1">${totalParts}</h4>
                    <small class="text-muted">Part Numbers</small>
                </div>
            </div>
        </div><div class="accordion" id="monthlyBreakdownAccordion">`;

        data.customers.forEach((customer, index) => {
            const customerId = `customer-${index}`;
            html += `<div class="accordion-item">
                <h2 class="accordion-header">
                    <button class="accordion-button collapsed" type="button"
                            data-bs-toggle="collapse" data-bs-target="#${customerId}">
                        <div class="d-flex justify-content-between align-items-center w-100">
                            <div>
                                <strong>${customer.customer_name}</strong>
                                <small class="text-muted ms-2">${customer.parts.length} part number${customer.parts.length !== 1 ? 's' : ''}</small>
                            </div>
                            <div class="text-end">
                                <strong class="text-success">£${customer.total_value.toLocaleString()}</strong>
                                <small class="text-muted ms-2">${((customer.total_value / totalValue) * 100).toFixed(1)}%</small>
                            </div>
                        </div>
                    </button>
                </h2>
                <div id="${customerId}" class="accordion-collapse collapse"
                     data-bs-parent="#monthlyBreakdownAccordion">
                    <div class="accordion-body p-0">
                        <table class="table table-sm mb-0">
                            <thead>
                                <tr>
                                    <th style="width: 40%;">Part Number</th>
                                    <th style="width: 15%;" class="text-center">Quantity</th>
                                    <th style="width: 20%;" class="text-end">Unit Price</th>
                                    <th style="width: 25%;" class="text-end">Total Value</th>
                                </tr>
                            </thead>
                            <tbody>`;

            customer.parts.forEach(part => {
                html += `<tr>
                    <td><code class="small">${part.part_number}</code></td>
                    <td class="text-center">${part.quantity}</td>
                    <td class="text-end">£${part.unit_price.toFixed(2)}</td>
                    <td class="text-end"><strong>£${part.total_value.toFixed(2)}</strong></td>
                </tr>`;
            });

            html += `</tbody></table></div></div></div>`;
        });

        html += '</div>';
        modalContent.innerHTML = html;
    }

// Initialize everything
    initializeDatePicker();
    const commDate = document.getElementById('commDatePicker').value;
    const callListContainer = document.getElementById('callListContent');
    const hasPrefill = callListContainer && callListContainer.dataset.prefilled === '1';

    if (console.time) {
        console.time('activity.call_list');
    }

    let callListPromise;
    if (hasPrefill) {
        logActivityTiming('call_list_prefilled');
        setTimeout(fetchCallListData, 1500);
        callListPromise = Promise.resolve();
    } else {
        callListPromise = fetchCallListData();
    }

    callListPromise
        .then(() => {
            if (console.time) {
                console.time('activity.recent_comms');
            }
            return fetchRecentCommunications(commDate);
        })
        .catch(() => {})
        .finally(() => {
            if (console.time) {
                console.time('activity.customer_list');
            }
            setTimeout(loadCustomerList, 150);
            if (console.time) {
                console.time('activity.sales_data');
            }
            setTimeout(fetchSalesData, 250);
            if (console.timeEnd) {
                console.timeEnd('activity.init');
            }
            logActivityTiming('init_end');
        });
});

// Event delegation for quick contact buttons in call list
// Event delegation for quick contact buttons in call list
document.getElementById('callListContent').addEventListener('click', function(e) {
    const quickActionBtn = e.target.closest('.contact-quick-action-btn');
    if (!quickActionBtn) return;

    e.preventDefault();
    e.stopPropagation();

    const action = quickActionBtn.dataset.action;
    const contactId = quickActionBtn.dataset.contactId;
    const customerId = quickActionBtn.dataset.customerId;
    const contactName = quickActionBtn.dataset.contactName;
    const customerName = quickActionBtn.dataset.customerName;
    const communicationType = action === 'phone' ? 'Phone' : 'Email';
    const callListId = quickActionBtn.dataset.callListId;
    const datasetContact = {
        id: contactId ? parseInt(contactId, 10) : null,
        full_name: contactName,
        customer_name: customerName,
        customer_id: customerId ? parseInt(customerId, 10) : null,
        email: quickActionBtn.dataset.contactEmail || '',
        phone: quickActionBtn.dataset.contactPhone || '',
        job_title: quickActionBtn.dataset.contactJobTitle || '',
        status_name: quickActionBtn.dataset.contactStatus || '',
        status_color: quickActionBtn.dataset.contactStatusColor || ''
    };

    const allContacts = []
        .concat(callListData.no_communications || [])
        .concat(callListData.has_communications || []);
    const hydratedContact = allContacts.find(contact => String(contact.contact_id) === String(contactId));
    const contact = hydratedContact ? {
        id: hydratedContact.contact_id,
        full_name: `${hydratedContact.name}${hydratedContact.second_name ? ' ' + hydratedContact.second_name : ''}`,
        customer_name: hydratedContact.customer_name,
        customer_id: hydratedContact.customer_id,
        email: hydratedContact.email || '',
        phone: hydratedContact.phone || '',
        job_title: hydratedContact.job_title || '',
        status_name: hydratedContact.contact_status || '',
        status_color: hydratedContact.status_color || ''
    } : datasetContact;

    // Get current salesperson ID from URL
    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    // Use UniversalContactPreview directly (not window.UniversalContactPreview)
    try {
        UniversalContactPreview.open(contact, {
            salesperson_id: salespersonId,
            communication_type: communicationType
        });
    } catch (error) {
        console.error('Error opening UniversalContactPreview:', error);
        // Fallback to basic modal
        document.getElementById('callListId').value = callListId;
        document.getElementById('callListContactId').value = contactId;
        document.getElementById('callListCustomerId').value = customerId;
        document.getElementById('callListContactInfo').innerHTML = `
            <strong>${contactName}</strong><br>
            <small class="text-muted">${customerName}</small>
        `;

        const typeSelect = document.querySelector('#logCallListCommForm select[name="communication_type"]');
        if (typeSelect) {
            typeSelect.value = communicationType;
        }

        const modal = new bootstrap.Modal(document.getElementById('logCallListCommModal'));
        modal.show();
    }
});

function saveNextActionResponse(input) {
    const customerId = input.dataset.customerId;
    const targetMonth = input.dataset.targetMonth;
    if (!customerId || !targetMonth) {
        return;
    }

    const urlParts = window.location.pathname.split('/');
    const salespersonId = urlParts[urlParts.indexOf('salespeople') + 1];

    input.classList.remove('is-valid', 'is-invalid');

    fetch('/salespeople/save_monthly_target', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            salesperson_id: salespersonId,
            customer_id: customerId,
            month: targetMonth,
            response: input.value
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            input.classList.add('is-valid');
            setTimeout(() => input.classList.remove('is-valid'), 1500);
        } else {
            input.classList.add('is-invalid');
        }
    })
    .catch(() => {
        input.classList.add('is-invalid');
    });
}

function handleNextActionResponseSave(event) {
    const input = event.target.closest('.next-action-response-input');
    if (!input) {
        return;
    }
    saveNextActionResponse(input);
}

document.addEventListener('change', handleNextActionResponseSave);
document.addEventListener('blur', handleNextActionResponseSave, true);
