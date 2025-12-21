// Add these functions to your existing script
let activityModal;

document.addEventListener('DOMContentLoaded', function() {
    activityModal = new bootstrap.Modal(document.getElementById('activityModal'));
});

async function showActivityDetail(activityType, activityId) {
    const modalTitle = document.getElementById('activityModalTitle');
    const modalContent = document.getElementById('activityModalContent');
    const modalSpinner = document.getElementById('activityModalSpinner');

    // Show modal with spinner
    modalContent.style.display = 'none';
    modalSpinner.style.display = 'block';
    modalTitle.textContent = `${activityType.charAt(0).toUpperCase() + activityType.slice(1)} Details`;
    activityModal.show();

    try {
        const response = await fetch(`/customers/activity/${activityType}/${activityId}`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load activity details');
        }

        modalContent.innerHTML = renderActivityDetail(activityType, data.activity);
    } catch (error) {
        modalContent.innerHTML = `
            <div class="alert alert-danger">
                Error loading activity details: ${error.message}
            </div>
        `;
    } finally {
        modalSpinner.style.display = 'none';
        modalContent.style.display = 'block';
    }
}

function renderActivityDetail(type, activity) {
    switch (type) {
        case 'email':
            const formattedBody = tidyEmailBody(activity.full_body || '');
            return `
                <div class="email-container">
                    <div class="email-header bg-light border-bottom p-3 mb-3">
                        <div class="mb-2">
                            <strong>Subject:</strong>
                            <div class="fs-5">${sanitizeHTML(activity.subject)}</div>
                        </div>
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>From:</strong>
                                    <div>${sanitizeHTML(activity.sender_email)}</div>
                                </div>
                                <div class="mb-2">
                                    <strong>To:</strong>
                                    <div>${sanitizeHTML(activity.recipient_email)}</div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>Date:</strong>
                                    <div>${new Date(activity.sent_date).toLocaleString()}</div>
                                </div>
                                <div class="mb-2">
                                    <strong>Direction:</strong>
                                    <div>${sanitizeHTML(activity.direction)}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="email-body p-3 bg-white border rounded">
                        ${formattedBody}
                    </div>
                </div>
            `;

        case 'rfq':
            return `
                <div class="rfq-container">
                    <div class="rfq-header bg-light border-bottom p-3 mb-3">
                        <div class="mb-2">
                            <strong>Reference:</strong>
                            <div class="fs-5">${sanitizeHTML(activity.customer_ref)}</div>
                        </div>
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>Status:</strong>
                                    <div>${sanitizeHTML(activity.status)}</div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>Date:</strong>
                                    <div>${new Date(activity.entered_date).toLocaleString()}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="rfq-body p-3 bg-white border rounded">
                        ${renderRfqLines(activity.lines)}
                    </div>
                </div>
            `;

        case 'order':
            return `
                <div class="order-container">
                    <div class="order-header bg-light border-bottom p-3 mb-3">
                        <div class="mb-2">
                            <strong>Order Reference:</strong>
                            <div class="fs-5">${sanitizeHTML(activity.sales_order_ref)}</div>
                        </div>
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>PO Reference:</strong>
                                    <div>${sanitizeHTML(activity.customer_po_ref || 'N/A')}</div>
                                </div>
                                <div class="mb-2">
                                    <strong>Status:</strong>
                                    <div>${sanitizeHTML(activity.status_name)}</div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-2">
                                    <strong>Value:</strong>
                                    <div>${activity.total_value} ${sanitizeHTML(activity.currency_id)}</div>
                                </div>
                                <div class="mb-2">
                                    <strong>Date:</strong>
                                    <div>${new Date(activity.date_entered).toLocaleString()}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="order-body p-3 bg-white border rounded">
                        ${renderOrderLines(activity.lines)}
                    </div>
                </div>
            `;

        default:
            return '<div class="alert alert-warning">Unknown activity type</div>';
    }
}
function renderRfqLines(lines = []) {
    if (!lines || lines.length === 0) {
        return '<div>No lines found for this RFQ.</div>';
    }
    let html = '<table class="table table-sm"><thead><tr><th>Line #</th><th>Part #</th><th>Qty</th><th>Line Value</th></tr></thead><tbody>';
    lines.forEach(line => {
        html += `
            <tr>
                <td>${sanitizeHTML(line.line_number)}</td>
                <td>${sanitizeHTML(line.base_part_number)}</td>
                <td>${line.quantity}</td>
                <td>${line.line_value ?? ''}</td>
            </tr>
        `;
    });
    html += '</tbody></table>';
    return html;
}

function renderOrderLines(lines = []) {
    if (!lines || lines.length === 0) {
        return '<div>No lines found for this Order.</div>';
    }
    let html = '<table class="table table-sm"><thead><tr><th>Line #</th><th>Part #</th><th>Qty</th><th>Price</th></tr></thead><tbody>';
    lines.forEach(line => {
        html += `
            <tr>
                <td>${sanitizeHTML(line.line_number)}</td>
                <td>${sanitizeHTML(line.base_part_number)}</td>
                <td>${line.quantity}</td>
                <td>${line.price ?? ''}</td>
            </tr>
        `;
    });
    html += '</tbody></table>';
    return html;
}

function renderRfqLines(lines = []) {
    if (!lines || lines.length === 0) {
        return '<div>No lines found for this RFQ.</div>';
    }
    let html = '<table class="table table-sm"><thead><tr><th>Line #</th><th>Part #</th><th>Qty</th><th>Line Value</th></tr></thead><tbody>';
    lines.forEach(line => {
        html += `
            <tr>
                <td>${sanitizeHTML(line.line_number)}</td>
                <td>${sanitizeHTML(line.base_part_number)}</td>
                <td>${line.quantity}</td>
                <td>${line.line_value ?? ''}</td>
            </tr>
        `;
    });
    html += '</tbody></table>';
    return html;
}

function renderOrderLines(lines = []) {
    if (!lines || lines.length === 0) {
        return '<div>No lines found for this Order.</div>';
    }
    let html = '<table class="table table-sm"><thead><tr><th>Line #</th><th>Part #</th><th>Qty</th><th>Price</th></tr></thead><tbody>';
    lines.forEach(line => {
        html += `
            <tr>
                <td>${sanitizeHTML(line.line_number)}</td>
                <td>${sanitizeHTML(line.base_part_number)}</td>
                <td>${line.quantity}</td>
                <td>${line.price ?? ''}</td>
            </tr>
        `;
    });
    html += '</tbody></table>';
    return html;
}

async function loadFullEmailContent(uid) {
  try {
    const response = await fetch(`/emails/content/uid/${uid}`);
    const data = await response.json();
    // e.g. show the body in a modal or somewhere in the UI
    console.log('Email Content:', data.content);
  } catch (error) {
    console.error('Error fetching email:', error);
  }
}

function tidyEmailBody(raw) {
    // 1) Optional: strip or replace certain lines
    let cleaned = raw.replace(/-------- Messaggio Inoltrato --------/g, '--- Forwarded ---');

    // 2) Convert newlines
    cleaned = cleaned
        .replace(/\r\n/g, '\n') // unify line endings
        .replace(/\n/g, '<br>');

    return cleaned;
}