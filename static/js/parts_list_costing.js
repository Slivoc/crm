// ---------- DOM READY: attach listeners & init widgets ----------
function setSupplierSourceBadge(row, source) {
    if (!row) return;
    const badge = row.querySelector('.supplier-source-badge');
    if (!badge) return;
    if (source === 'stock') {
        badge.classList.remove('d-none');
    } else {
        badge.classList.add('d-none');
    }
}

document.addEventListener('DOMContentLoaded', function () {
    // Auto-calculate line totals when cost or quantity changes
    document.querySelectorAll('.cost-input').forEach(input => {
        input.addEventListener('input', function () {
            const lineId = this.dataset.lineId;
            updateLineTotal(lineId);
        });
    });

    // Save individual line
    document.querySelectorAll('.save-line-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            const lineId = this.dataset.lineId;
            saveLineCost(lineId);
        });
    });

    // Duplicate line as price break
    document.querySelectorAll('.duplicate-line-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            const lineId = this.dataset.lineId;
            duplicateLine(lineId, this);
        });
    });

    document.querySelectorAll('.copy-part-number-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            const partNumber = this.dataset.partNumber;
            copyPartNumberToClipboard(partNumber);
        });
    });

    // View quotes for a line
    document.querySelectorAll('.view-quotes-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            const lineId = this.dataset.lineId;
            const partNumber = this.dataset.partNumber;
            showQuotesModal(lineId, partNumber);
        });
    });

    // Save all changes
    const saveAllBtn = document.getElementById('save-all-costs-btn');
    if (saveAllBtn) {
        saveAllBtn.addEventListener('click', function () {
            saveAllCosts();
        });
    }

    // Initialise Select2 for supplier dropdowns
    if (window.jQuery && $('.supplier-select').length) {
        $('.supplier-select').select2({
            ajax: {
                url: '/ils/suppliers/search',
                dataType: 'json',
                delay: 250,
                data: function (params) {
                    return {
                        q: params.term || '',
                        limit: 20
                    };
                },
                processResults: function (data) {
                    return {
                        results: data.suppliers.map(function (item) {
                            return {
                                id: item.id.toString(),
                                text: item.name,
                                currency_id: item.currency_id
                            };
                        })
                    };
                },
                cache: true
            },
            placeholder: 'Search for supplier...',
            minimumInputLength: 2,
            allowClear: true,
            width: '100%',
            dropdownParent: $('.costing-table-container')
        }).on('select2:select', function (e) {
            // Auto-set currency based on supplier's default when selected
            var data = e.params.data;
            if (data.currency_id) {
                var lineId = $(this).data('line-id');
                var row = $(`tr[data-line-id="${lineId}"]`);
                var currencySelect = row.find('.currency-select');
                currencySelect.val(data.currency_id).trigger('change');
                updateLineTotal(lineId);
            }
        });
    }

    document.querySelectorAll('.supplier-select').forEach(select => {
        select.addEventListener('change', function () {
            const row = this.closest('tr');
            if (!row) return;
            if (this.value) {
                row.dataset.costSource = '';
                setSupplierSourceBadge(row, null);
            } else if (row.dataset.costSource === 'stock') {
                setSupplierSourceBadge(row, 'stock');
            }
        });
    });

    document.querySelectorAll('tr[data-cost-source="stock"]').forEach(row => {
        setSupplierSourceBadge(row, 'stock');
    });

    loadEmailedSuppliersForCosting();

    // Email ILS Suppliers functionality
    const emailSuppliersBtn = document.getElementById('email-suppliers-btn');

    // Show button if there are lines
    if (document.querySelectorAll('#costing-table-body tr').length > 0) {
        emailSuppliersBtn.style.display = 'inline-block';
    }

    emailSuppliersBtn.addEventListener('click', function() {
        const lines = document.querySelectorAll('#costing-table-body tr');
        if (lines.length === 0) return;

        // Collect line data to analyze (get ILS data)
        const partsToAnalyze = [];
        lines.forEach(row => {
            const lineId = row.dataset.lineId;
            const partNumber = row.querySelector('td:nth-child(2) strong').textContent.trim();
            const quantity = parseInt(row.querySelector('td:nth-child(3) .badge').textContent.trim());

            partsToAnalyze.push({
                part_number: partNumber,
                quantity: quantity,
                line_id: lineId
            });
        });

        // First, analyze the parts to get ILS data
        fetch('/parts_list/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                parts: partsToAnalyze
            })
        })
        .then(response => response.json())
        .then(data => {
            if (!data.success || !data.results) {
                alert('Error analyzing parts');
                return;
            }

            // Now send the results (with ILS data) to email-suppliers
            return fetch('/parts_list/email-suppliers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    results: data.results,
                    list_id: window.PARTS_LIST_ID
                })
            });
        })
        .then(response => response.json())
        .then(data => {
            if (data.success && data.redirect) {
                window.location.href = data.redirect;
            } else {
                alert('Error: ' + (data.message || 'Failed to navigate to email page'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error navigating to email page');
        });
    });

  // Load quote availability for all lines on page load
    loadQuoteAvailability();

    // Track changes on all inputs
    document.querySelectorAll('.cost-input, .chosen-qty-input, .supplier-select, .currency-select, .lead-days-input, .notes-input').forEach(input => {
        input.addEventListener('input', function() {
            const row = this.closest('tr');
            markRowAsModified(row);
        });

        input.addEventListener('change', function() {
            const row = this.closest('tr');
            markRowAsModified(row);
        });
    });

    // Filter buttons
    document.getElementById('filter-all-btn')?.addEventListener('click', function() {
        filterRows('all');
        updateFilterButtons(this);
    });

    document.getElementById('filter-in-stock-btn')?.addEventListener('click', function() {
        filterRows('in-stock');
        updateFilterButtons(this);
    });

    document.getElementById('filter-no-cost-btn')?.addEventListener('click', function() {
        filterRows('no-cost');
        updateFilterButtons(this);
    });

    // Use stock buttons
    document.querySelectorAll('.use-stock-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const lineId = this.dataset.lineId;
            const stockCost = parseFloat(this.dataset.stockCost);
            const stockQty = parseInt(this.dataset.stockQty);
            const movementId = this.dataset.movementId;
            useStockForLine(lineId, stockCost, stockQty, movementId);
        });
    });

    document.getElementById('use-stock-all-btn')?.addEventListener('click', function() {
        useStockForAllAvailable();
    });

}); // <-- END OF DOMContentLoaded

function loadEmailedSuppliersForCosting() {
    const listId = window.PARTS_LIST_ID;
    const selects = document.querySelectorAll('.emailed-supplier-line-select');
    const headerDropdown = document.getElementById('emailed-suppliers-dropdown');
    const headerToggle = document.getElementById('emailed-suppliers-toggle');
    if (!listId) return;

    fetch(`/parts_list/parts-lists/${listId}/emailed-suppliers`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) return;
            const suppliers = data.suppliers || [];

            if (headerDropdown && headerToggle) {
                if (suppliers.length === 0) {
                    headerDropdown.innerHTML = `
                        <li><h6 class="dropdown-header">Emailed suppliers</h6></li>
                        <li><span class="dropdown-item-text text-muted">No emailed suppliers</span></li>
                    `;
                    headerToggle.disabled = true;
                } else {
                    headerDropdown.innerHTML = '<li><h6 class="dropdown-header">Emailed suppliers</h6></li>';
                    suppliers.forEach(supplier => {
                        const label = supplier.contact_email
                            ? `${supplier.supplier_name} (${supplier.contact_email})`
                            : supplier.supplier_name;
                        const item = document.createElement('li');
                        item.innerHTML = `
                            <a class="dropdown-item" href="/parts_list/parts-lists/${listId}/quick-quote/${supplier.supplier_id}">
                                ${label}
                            </a>
                        `;
                        headerDropdown.appendChild(item);
                    });
                    headerToggle.disabled = false;
                }
            }

            if (suppliers.length > 0 && selects.length > 0) {
                selects.forEach(select => {
                    select.innerHTML = '<option value="">Emailed suppliers...</option>';
                    suppliers.forEach(supplier => {
                        const option = document.createElement('option');
                        option.value = supplier.supplier_id;
                        option.textContent = supplier.contact_email
                            ? `${supplier.supplier_name} (${supplier.contact_email})`
                            : supplier.supplier_name;
                        option.dataset.currencyId = supplier.currency_id || '';
                        select.appendChild(option);
                    });
                    select.style.display = '';
                    select.addEventListener('change', function() {
                        const supplierId = this.value;
                        if (!supplierId) return;
                        const lineId = this.dataset.lineId;
                        const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
                        const supplierSelect = row?.querySelector('.supplier-select');
                        const selectedOption = this.options[this.selectedIndex];
                        const supplierName = selectedOption ? selectedOption.textContent : '';
                        const currencyId = selectedOption?.dataset.currencyId;
                        setSupplierSelectValue(supplierSelect, supplierId, supplierName);
                        if (currencyId && row) {
                            const currencySelect = row.querySelector('.currency-select');
                            if (currencySelect) {
                                currencySelect.value = currencyId;
                                currencySelect.dispatchEvent(new Event('change'));
                            }
                        }
                        this.value = '';
                    });
                });
            }
        })
        .catch(err => console.error('Error loading emailed suppliers:', err));
}

function setSupplierSelectValue(selectEl, supplierId, supplierName) {
    if (!selectEl) return;
    if (window.jQuery && $(selectEl).data('select2')) {
        if ($(selectEl).find(`option[value="${supplierId}"]`).length === 0) {
            const newOption = new Option(supplierName || 'Selected Supplier', supplierId, true, true);
            $(selectEl).append(newOption);
        } else {
            $(selectEl).val(supplierId);
        }
        $(selectEl).trigger('change');
    } else {
        let opt = selectEl.querySelector(`option[value="${supplierId}"]`);
        if (!opt) {
            opt = document.createElement('option');
            opt.value = supplierId;
            opt.textContent = supplierName || 'Selected Supplier';
            selectEl.appendChild(opt);
        }
        selectEl.value = supplierId;
        selectEl.dispatchEvent(new Event('change'));
    }
}

function markRowAsModified(row) {
    if (!row) return;

    // Add visual indicator to row
    row.classList.add('row-modified');

    // Make save button more prominent
    const saveBtn = row.querySelector('.save-line-btn');
    if (saveBtn) {
        saveBtn.classList.remove('btn-success');
        saveBtn.classList.add('btn-warning');
        saveBtn.innerHTML = '<i class="bi bi-exclamation-circle me-1"></i>Save';
    }
}

// ---------- HELPER FUNCTIONS (GLOBAL) ----------
function updateLineTotal(lineId) {
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    if (!row) return;

    const cost = parseFloat(row.querySelector('.cost-input').value) || 0;

    // Try to get chosen_qty first, fall back to requested quantity
    const chosenQtyInput = row.querySelector('.chosen-qty-input');
    const chosenQty = chosenQtyInput ? (parseInt(chosenQtyInput.value) || 0) : 0;

    // If no chosen_qty, use the badge quantity (requested qty)
    let qty = chosenQty;
    if (!qty) {
        const qtyBadge = row.querySelector('td:nth-child(3) .badge');
        qty = qtyBadge ? parseInt(qtyBadge.textContent) || 1 : 1;
    }

    const total = cost * qty;

    const totalEl = row.querySelector(`.line-total[data-line-id="${lineId}"]`);
    if (!totalEl) return;

    totalEl.textContent = total > 0 ? `£${total.toFixed(2)}` : '-';
}

function saveLineCost(lineId) {
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    if (!row) return;

    const listId = window.PARTS_LIST_ID;
    if (!listId) {
        showToast('Error: Parts list ID not found. Please refresh the page.', 'danger');
        return;
    }

    const supplier_id = row.querySelector('.supplier-select').value || null;
    const cost = parseFloat(row.querySelector('.cost-input').value) || null;
    const currency_id = parseInt(row.querySelector('.currency-select').value);
    const lead_days = parseInt(row.querySelector('.lead-days-input').value) || null;
    const internal_notes = row.querySelector('.notes-input').value;
    const sourceType = row.dataset.costSource || (cost ? 'manual' : null);
    const sourceReference = row.dataset.costSourceRef || null;

    // Get chosen_qty if the input exists
    const chosenQtyInput = row.querySelector('.chosen-qty-input');
    const chosen_qty = chosenQtyInput ? (parseInt(chosenQtyInput.value) || null) : null;

    // Check if there's any meaningful data to save
    if (!supplier_id && !cost && !lead_days && !internal_notes && !chosen_qty) {
        // No data entered - skip saving
        console.log(`Skipping line ${lineId} - no data entered`);
        return;
    }

    const costData = {
        supplier_id: supplier_id,
        cost: cost,
        currency_id: currency_id,
        lead_days: lead_days,
        chosen_qty: chosen_qty,
        internal_notes: internal_notes
    };
    if (sourceType !== null) costData.source_type = sourceType;
    if (sourceReference) costData.source_reference = sourceReference;

    fetch(`/parts_list/parts-lists/${listId}/lines/${lineId}/use-cost`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(costData)
    })
        .then(response => response.json())
.then(data => {
    if (data.success) {
        row.classList.remove('missing-cost');
        row.classList.add('has-cost');

        // Clear modified state
        row.classList.remove('row-modified');
        const saveBtn = row.querySelector('.save-line-btn');
        if (saveBtn) {
            saveBtn.classList.remove('btn-warning');
            saveBtn.classList.add('btn-success');
            saveBtn.innerHTML = '<i class="bi bi-check"></i>';
        }

        showToast('Cost saved successfully', 'success');
    } else {
        showToast('Error: ' + (data.message || 'Unknown error'), 'danger');
    }
})
}

function duplicateLine(lineId, button) {
    const listId = window.PARTS_LIST_ID;
    if (!listId) {
        showToast('Error: Parts list ID not found. Please refresh the page.', 'danger');
        return;
    }

    const originalHtml = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    }

    fetch(`/parts_list/parts-lists/${listId}/lines/${lineId}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line_type: 'price_break' })
    })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Failed to duplicate line');
            }
            window.location.reload();
        })
        .catch(error => {
            if (button) {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
            showToast(error.message || 'Failed to duplicate line', 'danger');
        });
}

function saveAllCosts() {
    const rows = document.querySelectorAll('tr[data-line-id]');
    let saveCount = 0;
    let skippedCount = 0;

    rows.forEach((row, index) => {
        setTimeout(() => {
            const lineId = row.dataset.lineId;

            // Check if line has data before saving
            const supplier_id = row.querySelector('.supplier-select').value;
            const cost = row.querySelector('.cost-input').value;
            const lead_days = row.querySelector('.lead-days-input').value;
            const internal_notes = row.querySelector('.notes-input').value;

            if (supplier_id || cost || lead_days || internal_notes) {
                saveLineCost(lineId);
                saveCount++;
            } else {
                skippedCount++;
            }

            if (index === rows.length - 1) {
                if (saveCount > 0) {
                    showToast(`Saved ${saveCount} line${saveCount !== 1 ? 's' : ''}${skippedCount > 0 ? ` (skipped ${skippedCount} empty)` : ''}`, 'success');
                } else {
                    showToast('No lines with data to save', 'info');
                }
            }
        }, index * 200); // 200ms stagger
    });
}

function loadQuoteAvailability() {
    const rows = document.querySelectorAll('tr[data-line-id]');
    const listId = window.PARTS_LIST_ID;
    if (!listId) return;

    fetch(`/parts_list/parts-lists/${listId}/lines/quote-availability`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) return;
            const map = new Map((data.lines || []).map(item => [String(item.line_id), item]));

            rows.forEach(row => {
                const lineId = row.dataset.lineId;
                const quoteBtn = row.querySelector('.view-quotes-btn');
                if (!quoteBtn) return;

                const stats = map.get(String(lineId)) || {};
                const thisListCount = Number(stats.this_list_count || 0);
                const otherOffersCount = Number(stats.other_offers_count || 0);
                updateQuoteIndicator(quoteBtn, thisListCount, otherOffersCount > 0);
            });
        })
        .catch(err => {
            console.error('Error loading quote availability:', err);
        });
}

function updateQuoteIndicator(button, thisListCount, hasOtherOffers) {
    // Remove any existing badges
    const existingBadge = button.querySelector('.quote-badge');
    if (existingBadge) {
        existingBadge.remove();
    }

    if (thisListCount > 0) {
        // Has quotes on THIS parts list - show green
        const badge = document.createElement('span');
        badge.className = 'quote-badge ms-2';
        badge.innerHTML = `<span class="badge bg-success">${thisListCount}</span>`;

        button.classList.remove('btn-outline-secondary', 'btn-outline-warning');
        button.classList.add('btn-success');
        button.setAttribute('title', `${thisListCount} quote${thisListCount > 1 ? 's' : ''} on this parts list`);

        button.appendChild(badge);

        // Update button text to be more compact - just show icon
        const icon = button.querySelector('i');
        if (icon) {
            button.innerHTML = '';
            button.appendChild(icon.cloneNode(true));
            button.appendChild(badge);
        }
    } else if (hasOtherOffers) {
        // No quotes on this list, but has quotes from OTHER parts lists - show warning/orange
        const badge = document.createElement('span');
        badge.className = 'quote-badge ms-2';
        badge.innerHTML = `<span class="badge bg-warning text-dark">!</span>`;

        button.classList.remove('btn-outline-secondary', 'btn-success');
        button.classList.add('btn-outline-warning');
        button.setAttribute('title', 'No quotes on this list, but quotes available from other parts lists');

        button.appendChild(badge);

        // Update button text to be more compact - just show icon
        const icon = button.querySelector('i');
        if (icon) {
            button.innerHTML = '';
            button.appendChild(icon.cloneNode(true));
            button.appendChild(badge);
        }
    } else {
        // No quotes at all - keep default styling
        button.classList.remove('btn-success', 'btn-outline-warning');
        button.classList.add('btn-outline-secondary');
        button.setAttribute('title', 'No quotes available');

        // Just keep icon
        const icon = button.querySelector('i');
        if (icon) {
            button.innerHTML = '';
            button.appendChild(icon.cloneNode(true));
        }
    }
}

function showToast(message, type) {
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
    alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 10000; min-width: 300px;';
    alertDiv.innerHTML = `
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    document.body.appendChild(alertDiv);
    setTimeout(() => alertDiv.remove(), 3000);
}

function copyPartNumberToClipboard(text) {
    if (!text) {
        showToast('No part number found to copy', 'warning');
        return;
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text)
            .then(() => showToast('Part number copied', 'success'))
            .catch(() => showToast('Unable to copy part number', 'danger'));
        return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'absolute';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showToast('Part number copied', 'success');
    } catch (error) {
        console.error('Copy failed:', error);
        showToast('Unable to copy part number', 'danger');
    } finally {
        document.body.removeChild(textarea);
    }
}

// ---------- QUOTES MODAL LOGIC (GLOBAL) ----------
function showQuotesModal(lineId, partNumber) {
    const partSpan = document.getElementById('quote-modal-part-number');
    if (partSpan) {
        partSpan.textContent = partNumber;
    }

    // Get qty from the row
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    const qtySpan = document.getElementById('quote-modal-qty');
    if (qtySpan && row) {
        const chosenQtyInput = row.querySelector('.chosen-qty-input');
        const requestedQty = row.querySelector('.badge[data-requested-qty]');
        const qty = (chosenQtyInput && chosenQtyInput.value) ||
                    (requestedQty && requestedQty.dataset.requestedQty) || '-';
        qtySpan.textContent = qty;
    }

    const modalEl = document.getElementById('quoteSelectionModal');
    if (!modalEl) return;

    const modal = new bootstrap.Modal(modalEl);
    modal.show();

    // Show loading
    const loading = document.getElementById('quotes-loading');
    const content = document.getElementById('quotes-content');
    if (loading) loading.style.display = 'block';
    if (content) content.style.display = 'none';

    // Hide QPL section initially
    const qplSection = document.getElementById('qpl-info-section');
    if (qplSection) qplSection.style.display = 'none';

    // Load quotes
    loadQuotesForLine(lineId);
}

function loadQuotesForLine(lineId) {
    const listId = window.PARTS_LIST_ID;
    if (!listId) {
        showToast('Error: Parts list ID not found.', 'danger');
        return;
    }

    // Get quantity for line cost calculation
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    let requiredQty = 1;
    if (row) {
        const chosenQtyInput = row.querySelector('.chosen-qty-input');
        const requestedQty = row.querySelector('.badge[data-requested-qty]');
        requiredQty = parseInt((chosenQtyInput && chosenQtyInput.value) ||
                    (requestedQty && requestedQty.dataset.requestedQty)) || 1;
    }

    fetch(`/parts_list/parts-lists/${listId}/lines/${lineId}/quotes`)
        .then(response => response.json())
        .then(data => {
            const loading = document.getElementById('quotes-loading');
            const content = document.getElementById('quotes-content');
            if (loading) loading.style.display = 'none';
            if (content) content.style.display = 'block';

            if (data.success && data.quotes && data.quotes.length > 0) {
                displayQuotes(data.quotes, lineId, requiredQty);
            } else {
                const table = document.getElementById('quotes-table');
                const noMsg = document.getElementById('no-quotes-message');
                if (table) table.style.display = 'none';
                if (noMsg) noMsg.style.display = 'block';
            }

            // Display other offers section
            if (data.success && data.other_offers) {
                displayOtherOffers(data.other_offers, lineId, requiredQty);
            }

            // Display QPL info
            if (data.success && data.qpl_approvals && data.qpl_approvals.length > 0) {
                displayQPLInfo(data.qpl_approvals);
            }
        })
        .catch(error => {
            console.error('Error loading quotes:', error);
            showToast('Error loading quotes', 'danger');
        });
}

function displayQuotes(quotes, lineId, requiredQty) {
    const tbody = document.getElementById('quotes-table-body');
    if (!tbody) return;

    tbody.innerHTML = '';
    requiredQty = requiredQty || 1;

    const parseUnitPrice = (value) => {
        const parsed = parseFloat(value);
        return Number.isFinite(parsed) ? parsed : null;
    };

    const formatPrice = (value, currencyCode) => {
        const parsed = parseUnitPrice(value);
        if (parsed === null) return '-';
        const prefix = currencyCode ? `${currencyCode} ` : '';
        return `${prefix}${parsed.toFixed(2)}`;
    };

    const validQuotes = quotes.filter(q => !q.is_no_bid && parseUnitPrice(q.unit_price) !== null);
    const cheapestPrice = validQuotes.length > 0
        ? Math.min(...validQuotes.map(q => parseUnitPrice(q.unit_price)))
        : null;

    // Get QPL approved manufacturers for highlighting
    const qplManufacturers = window._qplApprovedManufacturers || [];

    quotes.forEach(quote => {
        const unitPriceValue = parseUnitPrice(quote.unit_price);
        const isCheapest =
            !quote.is_no_bid &&
            unitPriceValue !== null &&
            unitPriceValue === cheapestPrice;

        // Calculate line cost
        const quotedQty = parseInt(quote.quantity_quoted) || requiredQty;
        const lineCost = unitPriceValue !== null ? unitPriceValue * quotedQty : null;

        // Check if manufacturer is QPL approved
        const manufacturer = quote.manufacturer || '';
        const isQPLApproved = manufacturer && qplManufacturers.some(qpl =>
            qpl.toLowerCase() === manufacturer.toLowerCase() ||
            manufacturer.toLowerCase().includes(qpl.toLowerCase()) ||
            qpl.toLowerCase().includes(manufacturer.toLowerCase())
        );

        const row = document.createElement('tr');

        const quoteNotes = quote.line_notes || '';
        const encodedQuoteNotes = quoteNotes ? encodeURIComponent(quoteNotes) : '';

        if (isCheapest) {
            row.style.background = '#f0f9f4';
        }

        if (quote.is_no_bid) {
            row.style.background = '#f8f9fa';
            row.style.color = '#6c757d';
        }

        row.innerHTML = `
            <td style="padding: 0.75rem;">
                <div style="font-weight: 500;">${quote.supplier_name}</div>
                <div style="font-size: 0.75rem; color: #6c757d;">
                    ${quote.quote_reference ? `Ref: ${quote.quote_reference}` : ''}
                    ${quote.quote_date ? ` · ${quote.quote_date}` : ''}
                </div>
                ${isCheapest ? '<span class="badge" style="background: #198754; font-size: 0.75rem; margin-top: 2px;">Lowest</span>' : ''}
                ${quote.is_no_bid ? '<span class="badge" style="background: #6c757d; font-size: 0.75rem; margin-top: 2px;">No Bid</span>' : ''}
            </td>
            <td style="padding: 0.75rem;">${quote.quoted_part_number || '-'}</td>
            <td style="padding: 0.75rem;">
                ${manufacturer ? `
                    <span>${manufacturer}</span>
                    ${isQPLApproved ? '<span class="badge" style="background: #198754; font-size: 0.7rem; margin-left: 4px;" title="QPL Approved">QPL</span>' : ''}
                ` : '<span style="color: #adb5bd;">-</span>'}
            </td>
            <td style="padding: 0.75rem; text-align: right;">${quote.quantity_quoted || '-'}</td>
            <td style="padding: 0.75rem; text-align: right;">${quote.qty_available || '-'}</td>
            <td style="padding: 0.75rem; text-align: right;">${quote.purchase_increment || '-'}</td>
            <td style="padding: 0.75rem; text-align: right;">${quote.moq || '-'}</td>
            <td style="padding: 0.75rem; text-align: right; font-weight: 500;">
                ${quote.is_no_bid ? '-' : formatPrice(quote.unit_price, quote.currency_code)}
            </td>
            <td style="padding: 0.75rem; text-align: right; font-weight: 600; color: #0d6efd;">
                ${lineCost !== null && !quote.is_no_bid ? formatPrice(lineCost, quote.currency_code) : '-'}
            </td>
            <td style="padding: 0.75rem; text-align: center;">${quote.lead_time_days ? `${quote.lead_time_days}d` : '-'}</td>
            <td style="padding: 0.75rem;">${quote.condition_code || '-'}</td>
            <td style="padding: 0.75rem; font-size: 0.8rem; max-width: 120px; overflow: hidden; text-overflow: ellipsis;" title="${quote.certifications || ''}">${quote.certifications || '-'}</td>
            <td style="padding: 0.75rem; text-align: center;">
                ${!quote.is_no_bid && unitPriceValue !== null ? `
                    <button class="btn btn-sm use-quote-btn" style="background: #0d6efd; color: white; border: none; padding: 0.25rem 0.75rem; font-size: 0.8rem;"
                            data-line-id="${lineId}"
                            data-quote-line-id="${quote.quote_line_id}"
                            data-supplier-id="${quote.supplier_id}"
                            data-supplier-name="${quote.supplier_name}"
                            data-cost="${unitPriceValue}"
                            data-currency-id="${quote.currency_id}"
                            data-lead-days="${quote.lead_time_days || ''}"
                            data-quoted-quantity="${quote.quantity_quoted || ''}"
                            data-quote-notes="${encodedQuoteNotes}">
                        Use
                    </button>
                ` : '-'}
            </td>
        `;

        tbody.appendChild(row);
    });

    // Attach click handlers to "Use" buttons
    tbody.querySelectorAll('.use-quote-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            useQuoteForLine(
                this.dataset.lineId,
                this.dataset.quoteLineId,
                this.dataset.supplierId,
                this.dataset.supplierName,
                parseFloat(this.dataset.cost),
                parseInt(this.dataset.currencyId),
                this.dataset.leadDays ? parseInt(this.dataset.leadDays) : null,
                this.dataset.quotedQuantity ? parseInt(this.dataset.quotedQuantity) : null,
                this.dataset.quoteNotes ? decodeURIComponent(this.dataset.quoteNotes) : ''
            );
        });
    });

    const table = document.getElementById('quotes-table');
    const noMsg = document.getElementById('no-quotes-message');
    if (table) table.style.display = 'table';
    if (noMsg) noMsg.style.display = 'none';
}

function displayQPLInfo(approvals) {
    const section = document.getElementById('qpl-info-section');
    const content = document.getElementById('qpl-info-content');
    if (!section || !content) return;

    if (!approvals || approvals.length === 0) {
        section.style.display = 'none';
        window._qplApprovedManufacturers = [];
        return;
    }

    // Store for use in displayQuotes
    window._qplApprovedManufacturers = approvals.map(a => a.manufacturer_name);

    content.innerHTML = '';
    approvals.forEach(approval => {
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.style.cssText = 'background: #e8f5e9; color: #2e7d32; font-weight: 500; font-size: 0.85rem; padding: 0.45rem 0.7rem;';

        let text = approval.manufacturer_name;
        if (approval.cage_code) {
            text += ` (${approval.cage_code})`;
        }
        badge.textContent = text;

        if (approval.approval_status) {
            badge.title = `Status: ${approval.approval_status}`;
        }

        content.appendChild(badge);
    });

    section.style.display = 'block';
}

function displayOtherOffers(offers, lineId, requiredQty) {
    const container = document.getElementById('other-offers-section');
    if (!container) return;

    if (!offers || offers.length === 0) {
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';
    const tbody = document.getElementById('other-offers-table-body');
    if (!tbody) return;

    tbody.innerHTML = '';
    requiredQty = requiredQty || 1;

    // Get QPL approved manufacturers for highlighting
    const qplManufacturers = window._qplApprovedManufacturers || [];

    offers.forEach(offer => {
        const unitPriceValue = parseFloat(offer.unit_price);
        const hasUnitPrice = Number.isFinite(unitPriceValue);
        const quotedQty = parseInt(offer.quantity_quoted) || requiredQty;
        const lineCost = hasUnitPrice ? unitPriceValue * quotedQty : null;

        // Check if manufacturer is QPL approved
        const manufacturer = offer.manufacturer || '';
        const isQPLApproved = manufacturer && qplManufacturers.some(qpl =>
            qpl.toLowerCase() === manufacturer.toLowerCase() ||
            manufacturer.toLowerCase().includes(qpl.toLowerCase()) ||
            qpl.toLowerCase().includes(manufacturer.toLowerCase())
        );

        const row = document.createElement('tr');
        const offerNotes = offer.line_notes || '';
        const encodedOfferNotes = offerNotes ? encodeURIComponent(offerNotes) : '';
        row.innerHTML = `
            <td style="padding: 0.6rem;">
                <div style="font-weight: 500;">${offer.supplier_name}</div>
                <div style="font-size: 0.7rem; color: #6c757d;">
                    ${offer.quote_reference ? `Ref: ${offer.quote_reference}` : ''}
                    ${offer.quote_date ? ` · ${offer.quote_date}` : ''}
                </div>
            </td>
            <td style="padding: 0.6rem;">
                <a href="/parts_list/parts-lists/${offer.parts_list_id}" target="_blank" style="text-decoration: none; color: #0d6efd; font-size: 0.85rem;">
                    ${offer.parts_list_name}
                    <i class="bi bi-box-arrow-up-right ms-1" style="font-size: 0.7rem;"></i>
                </a>
            </td>
            <td style="padding: 0.6rem;">${offer.quoted_part_number || '-'}</td>
            <td style="padding: 0.6rem;">
                ${manufacturer ? `
                    <span>${manufacturer}</span>
                    ${isQPLApproved ? '<span class="badge" style="background: #198754; font-size: 0.7rem; margin-left: 4px;" title="QPL Approved">QPL</span>' : ''}
                ` : '<span style="color: #adb5bd;">-</span>'}
            </td>
            <td style="padding: 0.6rem; text-align: right;">${offer.quantity_quoted || '-'}</td>
            <td style="padding: 0.6rem; text-align: right;">${offer.qty_available || '-'}</td>
            <td style="padding: 0.6rem; text-align: right;">${offer.purchase_increment || '-'}</td>
            <td style="padding: 0.6rem; text-align: right;">${offer.moq || '-'}</td>
            <td style="padding: 0.6rem; text-align: right; font-weight: 500;">${hasUnitPrice ? `${offer.currency_code || ''} ${unitPriceValue.toFixed(2)}`.trim() : '-'}</td>
            <td style="padding: 0.6rem; text-align: right; font-weight: 600; color: #6c757d;">${lineCost !== null ? `${offer.currency_code || ''} ${lineCost.toFixed(2)}`.trim() : '-'}</td>
            <td style="padding: 0.6rem; text-align: center;">${offer.lead_time_days ? `${offer.lead_time_days}d` : '-'}</td>
            <td style="padding: 0.6rem; text-align: center;">
                ${hasUnitPrice ? `
                    <button class="btn btn-sm use-other-offer-btn" style="background: transparent; color: #0d6efd; border: 1px solid #0d6efd; padding: 0.2rem 0.5rem; font-size: 0.75rem;"
                            data-line-id="${lineId}"
                            data-quote-line-id="${offer.quote_line_id}"
                            data-supplier-id="${offer.supplier_id}"
                            data-supplier-name="${offer.supplier_name}"
                            data-cost="${unitPriceValue}"
                            data-currency-id="${offer.currency_id}"
                            data-lead-days="${offer.lead_time_days || ''}"
                            data-quoted-quantity="${offer.quantity_quoted || ''}"
                            data-quote-notes="${encodedOfferNotes}">
                        Use
                    </button>
                ` : '-'}
            </td>
        `;
        tbody.appendChild(row);
    });

    // Attach click handlers
    tbody.querySelectorAll('.use-other-offer-btn').forEach(btn => {
        btn.addEventListener('click', function () {
            useQuoteForLine(
                this.dataset.lineId,
                this.dataset.quoteLineId,
                this.dataset.supplierId,
                this.dataset.supplierName,
                parseFloat(this.dataset.cost),
                parseInt(this.dataset.currencyId),
                this.dataset.leadDays ? parseInt(this.dataset.leadDays) : null,
                this.dataset.quotedQuantity ? parseInt(this.dataset.quotedQuantity) : null,
                this.dataset.quoteNotes ? decodeURIComponent(this.dataset.quoteNotes) : ''
            );
        });
    });
}

function useQuoteForLine(lineId, quoteLineId, supplierId, supplierName, cost, currencyId, leadDays, quotedQuantity, quoteNotes) {
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    if (!row) return;

    const supplierNotes = quoteNotes || '';

    // Update supplier dropdown
    const supplierSelect = row.querySelector('.supplier-select');
    if (supplierSelect) {
        if (window.jQuery) {
            if ($(supplierSelect).find(`option[value="${supplierId}"]`).length === 0) {
                const newOption = new Option(supplierName, supplierId, true, true);
                $(supplierSelect).append(newOption);
            } else {
                $(supplierSelect).val(supplierId);
            }
            $(supplierSelect).trigger('change');
        } else {
            // Fallback without Select2 (shouldn't really happen)
            let opt = supplierSelect.querySelector(`option[value="${supplierId}"]`);
            if (!opt) {
                opt = document.createElement('option');
                opt.value = supplierId;
                opt.textContent = supplierName;
                supplierSelect.appendChild(opt);
            }
            supplierSelect.value = supplierId;
        }
    }

    row.dataset.costSource = '';
    row.dataset.costSourceRef = '';
    setSupplierSourceBadge(row, null);

    // Update cost
    const costInput = row.querySelector('.cost-input');
    if (costInput) costInput.value = cost.toFixed(2);

    // Update currency
    const currencySelect = row.querySelector('.currency-select');
    if (currencySelect) currencySelect.value = currencyId;

    // Update lead days
    const leadInput = row.querySelector('.lead-days-input');
    if (leadInput && leadDays) leadInput.value = leadDays;

    // Update chosen quantity if provided
    const chosenQtyInput = row.querySelector('.chosen-qty-input');
    if (chosenQtyInput && quotedQuantity) {
        chosenQtyInput.value = quotedQuantity;
    }

    // Pull supplier quote notes into the notes column so they are visible/saved
    const notesInput = row.querySelector('.notes-input');
    if (notesInput && supplierNotes) {
        const existingNotes = notesInput.value.trim();
        if (!existingNotes) {
            notesInput.value = supplierNotes;
        } else if (!existingNotes.includes(supplierNotes)) {
            notesInput.value = `${existingNotes} | Quote notes: ${supplierNotes}`;
        }
    }

    // Update line total
    updateLineTotal(lineId);

    // Save immediately
    saveLineCost(lineId);

    // Close modal
    const modalEl = document.getElementById('quoteSelectionModal');
    if (modalEl) {
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) modalInstance.hide();
    }

    showToast(`Applied quote from ${supplierName}`, 'success');
}

function filterRows(filterType) {
    const rows = document.querySelectorAll('#costing-table-body tr');

    rows.forEach(row => {
        const lineId = row.dataset.lineId;
        const hasCost = row.classList.contains('has-cost');

        // Be more specific - look for stock badge in the stock column (td with .use-stock-btn)
        const stockCell = row.querySelector('td .use-stock-btn')?.closest('td');
        const stockBadge = stockCell?.querySelector('.badge');
        const hasFullStock = stockBadge && stockBadge.classList.contains('bg-success');

        let shouldShow = true;

        switch(filterType) {
            case 'all':
                shouldShow = true;
                break;
            case 'in-stock':
                shouldShow = hasFullStock;
                break;
            case 'no-cost':
                shouldShow = !hasCost;
                break;
        }

        row.style.display = shouldShow ? '' : 'none';
    });
}
function updateFilterButtons(activeBtn) {
    document.querySelectorAll('#filter-all-btn, #filter-in-stock-btn, #filter-no-cost-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    activeBtn.classList.add('active');
}

function useStockForLine(lineId, stockCost, stockQty, movementId) {
    const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
    if (!row) return;

    const requestedQty = parseInt(row.querySelector('.badge[data-requested-qty]').dataset.requestedQty);
    if (Number.isFinite(stockQty) && stockQty < requestedQty) {
        showToast('Selected stock batch does not cover the required quantity. Please choose a full-quantity batch.', 'warning');
        return;
    }

    // Update cost input
    const costInput = row.querySelector('.cost-input');
    if (costInput) costInput.value = stockCost.toFixed(2);

    // Set currency to GBP (assuming stock is in GBP, id=3)
    const currencySelect = row.querySelector('.currency-select');
    if (currencySelect) currencySelect.value = 3;

    // Update chosen quantity to match stock if needed
    const chosenQtyInput = row.querySelector('.chosen-qty-input');
    if (chosenQtyInput) {
        if (stockQty < requestedQty) {
            chosenQtyInput.value = Math.min(stockQty, requestedQty);
        } else {
            chosenQtyInput.value = requestedQty;
        }
    }

    // Clear supplier (stock doesn't have a supplier)
    const supplierSelect = row.querySelector('.supplier-select');
    if (supplierSelect && window.jQuery) {
        row.dataset.costSource = 'stock';
        row.dataset.costSourceRef = movementId || '';
        $(supplierSelect).val(null).trigger('change');
        setSupplierSourceBadge(row, 'stock');
    }
    if (supplierSelect && !window.jQuery) {
        row.dataset.costSource = 'stock';
        row.dataset.costSourceRef = movementId || '';
        supplierSelect.value = '';
        setSupplierSourceBadge(row, 'stock');
    }

    // Update line total
    updateLineTotal(lineId);

    // Mark as modified
    markRowAsModified(row);

    showToast('Stock cost applied - click Save to confirm', 'info');
}

function useStockForAllAvailable() {
    const stockButtons = document.querySelectorAll('.use-stock-btn');

    if (stockButtons.length === 0) {
        showToast('No stock available for any lines', 'info');
        return;
    }

    let count = 0;
    stockButtons.forEach((btn, index) => {
        setTimeout(() => {
            // Only apply if line doesn't already have a chosen cost or if stock is cheaper
            const lineId = btn.dataset.lineId;
            const row = document.querySelector(`tr[data-line-id="${lineId}"]`);
            const currentCost = parseFloat(row.querySelector('.cost-input').value) || Infinity;
            const stockCost = parseFloat(btn.dataset.stockCost);

            // Check if line is fully covered by stock
            const stockQty = parseInt(btn.dataset.stockQty);
            const requestedQty = parseInt(row.querySelector('.badge[data-requested-qty]').dataset.requestedQty);

            if (stockQty >= requestedQty && (currentCost === Infinity || stockCost < currentCost)) {
                useStockForLine(lineId, stockCost, stockQty, btn.dataset.movementId);
                count++;
            }

            if (index === stockButtons.length - 1) {
                showToast(`Applied stock costs to ${count} line${count !== 1 ? 's' : ''}. Click "Save All Changes" to save.`, 'success');
            }
        }, index * 100);
    });
}
