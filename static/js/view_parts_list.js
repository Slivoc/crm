// Import all the shared functions from parts_list.js
// Global variables
let allResults = [];
let currentListId = null;

// Shared utility functions (copied from parts_list.js)
function formatCurrency(value) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '-';
    return '£' + parseFloat(value).toFixed(2);
}

function formatCurrencyWithCode(value, code) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '-';
    const numeric = parseFloat(value).toFixed(2);
    const prefix = code ? `${code} ` : '';
    return `${prefix}${numeric}`;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    try { return new Date(dateStr).toLocaleDateString(); }
    catch (e) { return dateStr; }
}

function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.toString().replace(/[&<>"']/g, m => map[m]);
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

// Global handler functions
function handleIlsClick(partIndex) {
    if (allResults && allResults[partIndex]) {
        showIlsDetailsModal(allResults[partIndex]);
    }
}

function handleViewDetails(partIndex) {
    if (allResults && allResults[partIndex]) {
        showPartDetailsModal(allResults[partIndex]);
    }
}

function addSuggestedSupplier(lineId, supplierId, supplierName, sourceType, buttonElement) {
    if (!currentListId) {
        alert('Cannot add suggested suppliers - no list loaded');
        return;
    }

    const originalHtml = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    fetch(`/parts_list/parts-lists/${currentListId}/lines/${lineId}/suggested-suppliers/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supplier_id: supplierId, source_type: sourceType })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            buttonElement.innerHTML = '<i class="bi bi-check-circle-fill"></i>';
            buttonElement.classList.remove('btn-outline-primary');
            buttonElement.classList.add('btn-success');
            showToast(data.message || `Added ${supplierName} to suggested suppliers`, 'success');
        } else {
            buttonElement.innerHTML = originalHtml;
            buttonElement.disabled = false;
            alert('Error: ' + (data.message || 'Could not add supplier'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        buttonElement.innerHTML = originalHtml;
        buttonElement.disabled = false;
        alert('Error adding supplier: ' + error.message);
    });
}

function useCost(lineId, costData, buttonElement) {
    if (!currentListId) {
        alert('Cannot update cost - no list loaded');
        return;
    }

    const originalHtml = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    fetch(`/parts_list/parts-lists/${currentListId}/lines/${lineId}/use-cost`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(costData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            buttonElement.innerHTML = '<i class="bi bi-check-circle-fill"></i> Applied';
            buttonElement.classList.remove('btn-success');
            buttonElement.classList.add('btn-primary');
            showToast(`Cost updated: ${formatCurrency(costData.cost)}`, 'success');
        } else {
            buttonElement.innerHTML = originalHtml;
            buttonElement.disabled = false;
            alert('Error: ' + (data.message || 'Could not update cost'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        buttonElement.innerHTML = originalHtml;
        buttonElement.disabled = false;
        alert('Error updating cost: ' + error.message);
    });
}


function showPartDetailsModal(part) {
    const modalTitle = document.getElementById('partDetailsModalLabel');
    const modalContent = document.getElementById('modalDetailsContent');

    modalTitle.innerHTML = `<i class="bi bi-info-circle-fill" style="color: #0d6efd; font-size: 1.2rem;"></i> Part Details: ${part.input_part_number}`;

    let detailsHtml = '<div class="container-fluid">';

    const showActions = currentListId !== null;
    const lineId = part.line_id;

    const getPriceRange = (items, priceField) => {
        const prices = items.map(item => parseFloat(item[priceField])).filter(p => !isNaN(p) && p > 0);
        if (prices.length === 0) return null;
        return { min: Math.min(...prices), max: Math.max(...prices), avg: prices.reduce((s, p) => s + p, 0) / prices.length };
    };

    // STOCK DETAILS - PURCHASING
    if (part.stock_movement_count > 0 && part.stock_details && part.stock_details.length > 0) {
        const stockToShow = part.stock_details.slice(0, 3);
        const totalCost = part.stock_details.reduce((sum, s) => sum + (parseFloat(s.cost_per_unit) || 0) * s.available_quantity, 0);
        const avgCost = part.total_available_stock ? totalCost / part.total_available_stock : 0;

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-box-seam" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Stock Inventory</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">
                        <i class="bi bi-check-circle-fill me-1"></i>${part.total_available_stock} available
                    </span>
                    <span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg Cost: ${formatCurrency(avgCost)}
                    </span>
                    ${part.stock_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.stock_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Receipt Date</th>
                                <th>Datecode</th>
                                <th>Available / Original</th>
                                <th>Cost/Unit</th>
                                <th>Total Value</th>
                                ${showActions ? '<th style="width: 100px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${stockToShow.map(stock => `
                                <tr>
                                    <td><strong>${formatDate(stock.receipt_date)}</strong></td>
                                    <td>${escapeHtml(stock.datecode) || '-'}</td>
                                    <td>
                                        <strong class="text-success">${stock.available_quantity}</strong>
                                        <span class="text-muted"> / ${stock.original_quantity}</span>
                                    </td>
                                    <td><strong>${formatCurrency(stock.cost_per_unit)}</strong></td>
                                    <td style="color: #0d6efd; font-weight: 600;">${formatCurrency(stock.cost_per_unit * stock.available_quantity)}</td>
                                    ${showActions ? `
                                    <td>
                                        <button class="btn btn-sm btn-success use-cost-btn"
                                                data-cost="${stock.cost_per_unit}"
                                                data-currency="3"
                                                data-source-type="stock"
                                                data-source-ref="${stock.movement_id}">
                                            <i class="bi bi-check-circle me-1"></i>Use Cost
                                        </button>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // PO DETAILS - PURCHASING
    if (part.po_count > 0 && part.po_details && part.po_details.length > 0) {
        const posToShow = part.po_details.slice(0, 3);
        const priceRange = getPriceRange(part.po_details, 'price');
        const uniqueSuppliers = [...new Set(part.po_details.map(po => po.supplier_name))];

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-cart-fill" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Purchase Orders</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.po_details.length} PO${part.po_details.length !== 1 ? 's' : ''}</span>
                    <span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-building me-1"></i>${uniqueSuppliers.length} supplier${uniqueSuppliers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    ${part.po_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.po_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date Issued</th>
                                <th>PO Reference</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                <th>Status</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${posToShow.map(po => `
                                <tr>
                                    <td><strong>${formatDate(po.date_issued)}</strong></td>
                                    <td>${escapeHtml(po.purchase_order_ref)}</td>
                                    <td>${escapeHtml(po.supplier_name)}</td>
                                    <td>${po.quantity || '-'}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(po.price)} ${escapeHtml(po.currency_code)}</td>
                                    <td><span class="badge bg-secondary">${escapeHtml(po.status_name)}</span></td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${po.supplier_id}"
                                                    data-supplier-name="${escapeHtml(po.supplier_name)}"
                                                    data-source-type="po"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${po.supplier_id}"
                                                    data-cost="${po.price}"
                                                    data-currency="${po.currency_id || 3}"
                                                    data-source-type="po"
                                                    data-source-ref="${po.purchase_order_ref}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // EXCESS STOCK DETAILS - PURCHASING
    if (part.excess_count > 0 && part.excess_details && part.excess_details.length > 0) {
        const excessToShow = part.excess_details;
        const priceRange = getPriceRange(part.excess_details, 'unit_price');
        const uniqueSuppliers = [...new Set(part.excess_details.map(ex => ex.supplier_name).filter(Boolean))];

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-boxes" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Excess Stock Lists</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.excess_details.length} line${part.excess_details.length !== 1 ? 's' : ''}</span>
                    ${uniqueSuppliers.length ? `<span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-building me-1"></i>${uniqueSuppliers.length} supplier${uniqueSuppliers.length !== 1 ? 's' : ''}
                    </span>` : ''}
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>List</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${excessToShow.map(ex => `
                                <tr>
                                    <td><strong>${formatDate(ex.upload_date || ex.entered_date)}</strong></td>
                                    <td>${escapeHtml(ex.list_name || '-') }</td>
                                    <td>${escapeHtml(ex.supplier_name || 'Unknown')}</td>
                                    <td>${ex.quantity || '-'}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrencyWithCode(ex.unit_price, ex.currency_code || 'GBP')}</td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            ${ex.supplier_id ? `
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${ex.supplier_id}"
                                                    data-supplier-name="${escapeHtml(ex.supplier_name || '')}"
                                                    data-source-type="excess"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>` : ''}
                                            ${ex.unit_price ? `
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${ex.supplier_id || ''}"
                                                    data-cost="${ex.unit_price}"
                                                    data-currency="${ex.unit_price_currency_id || ''}"
                                                    data-source-type="excess"
                                                    data-source-ref="${ex.excess_stock_list_id}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>` : ''}
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // EXCESS STOCK DETAILS - PURCHASING
    if (part.excess_count > 0 && part.excess_details && part.excess_details.length > 0) {
        const excessToShow = part.excess_details;
        const priceRange = getPriceRange(part.excess_details, 'unit_price');
        const uniqueSuppliers = [...new Set(part.excess_details.map(ex => ex.supplier_name).filter(Boolean))];

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-boxes" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Excess Stock Lists</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.excess_details.length} line${part.excess_details.length !== 1 ? 's' : ''}</span>
                    ${uniqueSuppliers.length ? `<span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-building me-1"></i>${uniqueSuppliers.length} supplier${uniqueSuppliers.length !== 1 ? 's' : ''}
                    </span>` : ''}
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>List</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${excessToShow.map(ex => `
                                <tr>
                                    <td><strong>${formatDate(ex.upload_date || ex.entered_date)}</strong></td>
                                    <td>${escapeHtml(ex.list_name || '-') }</td>
                                    <td>${escapeHtml(ex.supplier_name || 'Unknown')}</td>
                                    <td>${ex.quantity || '-'}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrencyWithCode(ex.unit_price, ex.currency_code || 'GBP')}</td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            ${ex.supplier_id ? `
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${ex.supplier_id}"
                                                    data-supplier-name="${escapeHtml(ex.supplier_name || '')}"
                                                    data-source-type="excess"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>` : ''}
                                            ${ex.unit_price ? `
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${ex.supplier_id || ''}"
                                                    data-cost="${ex.unit_price}"
                                                    data-currency="${ex.unit_price_currency_id || ''}"
                                                    data-source-type="excess"
                                                    data-source-ref="${ex.excess_stock_list_id}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>` : ''}
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // VQ DETAILS - PURCHASING
    if (part.vq_count > 0 && part.vq_details && part.vq_details.length > 0) {
        const vqsToShow = part.vq_details.slice(0, 3);
        const priceRange = getPriceRange(part.vq_details, 'vendor_price');
        const avgLeadTime = part.vq_details.reduce((sum, vq) => sum + (vq.lead_days || 0), 0) / part.vq_details.length;

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-receipt" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Vendor Quotes</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.vq_details.length} quote${part.vq_details.length !== 1 ? 's' : ''}</span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        <i class="bi bi-cash-stack me-1"></i>${formatCurrency(priceRange.min)} - ${formatCurrency(priceRange.max)}
                    </span>` : ''}
                    <span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-clock me-1"></i>${Math.round(avgLeadTime)} days avg
                    </span>
                    ${part.vq_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.vq_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>VQ Number</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                <th>Lead Time</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${vqsToShow.map(vq => `
                                <tr>
                                    <td><strong>${formatDate(vq.entry_date)}</strong></td>
                                    <td>${escapeHtml(vq.vq_number)}</td>
                                    <td>${escapeHtml(vq.supplier_name)}</td>
                                    <td>${vq.quantity_quoted}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(vq.vendor_price)} ${escapeHtml(vq.currency_code)}</td>
                                    <td><span class="badge" style="background: #6610f2; color: white;">${vq.lead_days} days</span></td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${vq.supplier_id}"
                                                    data-supplier-name="${escapeHtml(vq.supplier_name)}"
                                                    data-source-type="vq"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${vq.supplier_id}"
                                                    data-cost="${vq.vendor_price}"
                                                    data-currency="${vq.currency_id || 3}"
                                                    data-lead-days="${vq.lead_days || ''}"
                                                    data-source-type="vq"
                                                    data-source-ref="${vq.vq_number}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // BOM DETAILS - PURCHASING (no actions needed here)
    if (part.bom_usage_count > 0 && part.bom_details && part.bom_details.length > 0) {
        const priceRange = getPriceRange(part.bom_details, 'guide_price');
        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-diagram-3" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>BOM Usage</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.bom_details.length} BOM${part.bom_details.length !== 1 ? 's' : ''}</span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Guide: ${formatCurrency(priceRange.min)} - ${formatCurrency(priceRange.max)}
                    </span>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>BOM Name</th>
                                <th>Qty per BOM</th>
                                <th>Guide Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${part.bom_details.map(bom => `
                                <tr>
                                    <td><strong>${escapeHtml(bom.bom_name)}</strong></td>
                                    <td>${bom.qty_per_bom}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(bom.guide_price)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // CQ DETAILS - SALES (no actions needed)
    if (part.cq_count > 0 && part.cq_details && part.cq_details.length > 0) {
        const cqsToShow = part.cq_details.slice(0, 3);
        const priceRange = getPriceRange(part.cq_details, 'unit_price');
        const uniqueCustomers = [...new Set(part.cq_details.map(cq => cq.customer_name))];

        detailsHtml += `
            <div class="modal-section sales">
                <div class="modal-section-header">
                    <i class="bi bi-file-earmark-text" style="color: #198754; font-size: 1rem;"></i>
                    <span>Customer Quotes (CQs)</span>
                    <span class="modal-section-badge" style="background: #198754; color: white;">${part.cq_details.length} quote${part.cq_details.length !== 1 ? 's' : ''}</span>
                    <span class="modal-section-badge" style="background: #20c997; color: white;">
                        <i class="bi bi-people-fill me-1"></i>${uniqueCustomers.length} customer${uniqueCustomers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    ${part.cq_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.cq_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>CQ Number</th>
                                <th>Customer</th>
                                <th>Status</th>
                                <th>Qty Req / Quoted</th>
                                <th>Unit Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${cqsToShow.map(cq => `
                                <tr>
                                    <td><strong>${formatDate(cq.entry_date)}</strong></td>
                                    <td>${escapeHtml(cq.cq_number)}</td>
                                    <td>${escapeHtml(cq.customer_name)}</td>
                                    <td><span class="badge bg-info">${escapeHtml(cq.status)}</span></td>
                                    <td>${cq.quantity_requested || '-'} / <strong>${cq.quantity_quoted || '-'}</strong></td>
                                    <td style="font-weight: 600; color: #198754;">${formatCurrency(cq.unit_price)} ${escapeHtml(cq.currency_code)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // SALES ORDER DETAILS - SALES (no actions needed)
    if (part.so_count > 0 && part.so_details && part.so_details.length > 0) {
        const sosToShow = part.so_details.slice(0, 3);
        const priceRange = getPriceRange(part.so_details, 'sale_price');
        const uniqueCustomers = [...new Set(part.so_details.map(so => so.customer_name))];

        detailsHtml += `
            <div class="modal-section sales">
                <div class="modal-section-header">
                    <i class="bi bi-cart-check" style="color: #198754; font-size: 1rem;"></i>
                    <span>Sales History</span>
                    <span class="modal-section-badge" style="background: #198754; color: white;">${part.so_details.length} sale${part.so_details.length !== 1 ? 's' : ''}  </span>
                    <span class="modal-section-badge" style="background: #20c997; color: white;">
                        <i class="bi bi-people-fill me-1"></i>${uniqueCustomers.length} customer${uniqueCustomers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0f5132; color: white;">
                        <i class="bi bi-graph-up me-1"></i>${formatCurrency(priceRange.avg)} avg
                    </span>` : ''}
                    ${part.so_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.so_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>SO Reference</th>
                                <th>Customer</th>
                                <th>Quantity</th>
                                <th>Sale Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${sosToShow.map(so => `
                                <tr>
                                    <td><strong>${formatDate(so.date_entered)}</strong></td>
                                    <td>${escapeHtml(so.sales_order_ref)}</td>
                                    <td>${escapeHtml(so.customer_name)}</td>
                                    <td>${so.order_quantity}</td>
                                    <td style="font-weight: 600; color: #198754;">${formatCurrency(so.sale_price)} ${escapeHtml(so.currency_code)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    detailsHtml += '</div>';
    modalContent.innerHTML = detailsHtml;

    // Attach event listeners for action buttons
    if (showActions && lineId) {
        // Add Supplier buttons
        modalContent.querySelectorAll('.add-supplier-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const supplierId = this.getAttribute('data-supplier-id');
                const supplierName = this.getAttribute('data-supplier-name');
                const sourceType = this.getAttribute('data-source-type');
                addSuggestedSupplier(lineId, supplierId, supplierName, sourceType, this);
            });
        });

        // Use Cost buttons
        modalContent.querySelectorAll('.use-cost-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const costData = {
                    supplier_id: this.getAttribute('data-supplier-id') || null,
                    cost: parseFloat(this.getAttribute('data-cost')),
                    currency_id: parseInt(this.getAttribute('data-currency')),
                    lead_days: this.getAttribute('data-lead-days') ? parseInt(this.getAttribute('data-lead-days')) : null,
                    source_type: this.getAttribute('data-source-type'),
                    source_reference: this.getAttribute('data-source-ref') || null
                };
                useCost(lineId, costData, this);
            });
        });
    }

    const modal = new bootstrap.Modal(document.getElementById('partDetailsModal'));
    modal.show();
}

function showIlsDetailsModal(part) {
    const modalTitle = document.getElementById('ilsDetailsModalLabel');
    const modalContent = document.getElementById('ilsModalContent');

    modalTitle.innerHTML = `
        <i class="bi bi-globe" style="font-size: 1.3rem;"></i>
        ILS Market Data: ${escapeHtml(part.input_part_number)}
    `;

    if (!part.ils_details || part.ils_details.length === 0) {
        modalContent.innerHTML = `
            <div class="alert alert-info">
                <i class="bi bi-info-circle me-2"></i>
                No ILS market data available for this part.
            </div>
        `;
        const modal = new bootstrap.Modal(document.getElementById('ilsDetailsModal'));
        modal.show();
        return;
    }

    const showActions = currentListId !== null;
    const lineId = part.line_id;

    const latestSearchRaw = part.ils_latest_search_date;
    const latestSearch = latestSearchRaw ? formatDate(latestSearchRaw) : 'Unknown';

    const mappedRows = part.ils_details.filter(d => d.supplier_name);
    const unmappedRows = part.ils_details.filter(d => !d.supplier_name);

    const supplierGroups = {};
    mappedRows.forEach(row => {
        const supplierName = row.supplier_name;
        if (!supplierGroups[supplierName]) {
            supplierGroups[supplierName] = [];
        }
        supplierGroups[supplierName].push(row);
    });

    const renderIlsRow = (ils) => `
        <tr>
            <td>
                <strong>${escapeHtml(ils.ils_company_name)}</strong>
                ${ils.ils_cage_code ? `<br><small class="text-muted">CAGE: ${escapeHtml(ils.ils_cage_code)}</small>` : ''}
            </td>
            <td><strong>${ils.search_date ? formatDate(ils.search_date) : '-'}</strong></td>
            <td><span class="badge bg-secondary">${escapeHtml(ils.quantity)}</span></td>
            <td><span class="badge bg-info">${escapeHtml(ils.condition_code)}</span></td>
            <td><small>${escapeHtml(ils.description || '-')}</small></td>
            <td><small>${ils.email ? `<a href="mailto:${escapeHtml(ils.email)}">${escapeHtml(ils.email)}</a>` : '-'}</small></td>
            <!-- REMOVED: Actions <td> entirely -->
        </tr>
    `;

    let contentHtml = `
        <!-- ... (existing alert div unchanged) -->
    `;

    if (Object.keys(supplierGroups).length > 0) {
        contentHtml += `
            <div class="modal-section mb-4">
                <div class="modal-section-header" style="background: #d1e7dd; border-color: #0f5132; padding: 0.75rem 1rem;">
    <div class="d-flex align-items-center justify-content-between w-100">
        <div class="d-flex align-items-center gap-2">
            <i class="bi bi-star-fill" style="color: #0f5132; font-size: 1rem;"></i>
            <span style="color: #0f5132;">Preferred Suppliers (Mapped)</span>
        </div>
        <span class="modal-section-badge" style="background: #0f5132; color: white;">
            ${part.ils_preferred_suppliers} supplier${part.ils_preferred_suppliers !== 1 ? 's' : ''}
        </span>
    </div>
</div>


        `;

        Object.keys(supplierGroups).sort().forEach((supplierName, idx) => {
            const rows = supplierGroups[supplierName];
            const totalQty = rows.reduce((sum, r) => {
                const qty = r.quantity;
                return sum + (qty && qty.toString().match(/^\d+$/) ? parseInt(qty) : 0);
            }, 0);

            // NEW: Check for supplier_id on first row (add null-check if groups might vary)
            const hasSupplierId = showActions && rows[0] && rows[0].supplier_id;

            contentHtml += `
                <div class="accordion" id="supplierAccordion${idx}">
                    <div class="accordion-item">
                        <h2 class="accordion-header" style="display: flex; align-items: center;">
                            <button class="accordion-button collapsed" type="button" style="flex-grow: 1;"
                                    data-bs-toggle="collapse" data-bs-target="#supplier${idx}"
                                    aria-expanded="false">
                                <div class="d-flex align-items-center gap-3 w-100">
                                    <i class="bi bi-building text-success"></i>
                                    <strong>${escapeHtml(supplierName)}</strong>
                                    <div class="d-flex align-items-center gap-2 ms-auto">
                                        <span class="badge bg-success">${rows.length} listing${rows.length !== 1 ? 's' : ''}</span>
                                        ${totalQty > 0 ? `<span class="badge bg-info">${totalQty} available</span>` : ''}
                                    </div>
                                </div>
                            </button>
                            ${hasSupplierId ? `
                                            <button class="btn btn-outline-primary btn-sm add-supplier-btn ms-2"
                                                    data-supplier-id="${rows[0].supplier_id}"
                                                    data-supplier-name="${escapeHtml(supplierName)}"
                                                    data-source-type="ils"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                        ` : ''}
                        </h2>
                        <div id="supplier${idx}" class="accordion-collapse collapse"
                             data-bs-parent="#supplierAccordion${idx}">
                            <div class="accordion-body p-0">
                                <div class="table-responsive">
                                    <table class="table table-sm modal-table mb-0">
                                        <thead>
                                            <tr>
                                                <th>ILS Company</th>
                                                <th>Date</th>
                                                <th>Qty</th>
                                                <th>Condition</th>
                                                <th>Description</th>
                                                <th>Email</th>
                                                <!-- REMOVED: Actions <th> entirely -->
                                            </tr>
                                        </thead>
                                        <tbody>
                                            ${rows.map(renderIlsRow).join('')}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });

        contentHtml += `</div>`;
    }

    if (unmappedRows.length > 0) {
        contentHtml += `
            <div class="modal-section">
                <div class="modal-section-header" style="background: #fff3cd; border-color: #856404;">
                    <i class="bi bi-exclamation-triangle-fill" style="color: #856404; font-size: 1rem;"></i>
                    <span style="color: #856404;">Unmapped Suppliers</span>
                    <span class="modal-section-badge" style="background: #856404; color: white;">
                        ${unmappedRows.length} listing${unmappedRows.length !== 1 ? 's' : ''}
                    </span>
                    <a href="/ils/supplier-mapping" target="_blank" rel="noopener"
                       class="btn btn-sm btn-warning ms-auto" style="font-size: 0.8rem;">
                        <i class="bi bi-diagram-2"></i> Map These Suppliers
                    </a>
                </div>
                <div class="accordion" id="unmappedAccordion">
                    <div class="accordion-item">
                        <h2 class="accordion-header">
                            <button class="accordion-button collapsed" type="button"
                                    data-bs-toggle="collapse" data-bs-target="#unmappedCollapse"
                                    aria-expanded="false">
                                <i class="bi bi-list-ul me-2"></i>
                                Click to view ${unmappedRows.length} unmapped supplier listing${unmappedRows.length !== 1 ? 's' : ''}
                            </button>
                        </h2>
                        <div id="unmappedCollapse" class="accordion-collapse collapse"
                             data-bs-parent="#unmappedAccordion">
                            <div class="accordion-body p-0">
                                <div class="table-responsive">
                                    <table class="table table-sm modal-table mb-0">
                                        <thead>
                                            <tr>
                                                <th>ILS Company</th>
                                                <th>Date</th>
                                                <th>Qty</th>
                                                <th>Condition</th>
                                                <th>Description</th>
                                                <th>Email</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            ${unmappedRows.map(renderIlsRow).join('')}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    contentHtml += '</div>';
    modalContent.innerHTML = contentHtml;

    // Attach event listeners for ILS action buttons
    if (showActions && lineId) {
        modalContent.querySelectorAll('.add-supplier-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const supplierId = this.getAttribute('data-supplier-id');
                const supplierName = this.getAttribute('data-supplier-name');
                const sourceType = this.getAttribute('data-source-type');
                addSuggestedSupplier(lineId, supplierId, supplierName, sourceType, this);
            });
        });
    }

    const modal = new bootstrap.Modal(document.getElementById('ilsDetailsModal'));
    modal.show();
}

function showPartDetailsModal(part) {
    const modalTitle = document.getElementById('partDetailsModalLabel');
    const modalContent = document.getElementById('modalDetailsContent');

    modalTitle.innerHTML = `<i class="bi bi-info-circle-fill" style="color: #0d6efd; font-size: 1.2rem;"></i> Part Details: ${part.input_part_number}`;

    let detailsHtml = '<div class="container-fluid">';

    const showActions = currentListId !== null;
    const lineId = part.line_id;

    const getPriceRange = (items, priceField) => {
        const prices = items.map(item => parseFloat(item[priceField])).filter(p => !isNaN(p) && p > 0);
        if (prices.length === 0) return null;
        return { min: Math.min(...prices), max: Math.max(...prices), avg: prices.reduce((s, p) => s + p, 0) / prices.length };
    };

    // STOCK DETAILS - PURCHASING
    if (part.stock_movement_count > 0 && part.stock_details && part.stock_details.length > 0) {
        const stockToShow = part.stock_details.slice(0, 3);
        const totalCost = part.stock_details.reduce((sum, s) => sum + (parseFloat(s.cost_per_unit) || 0) * s.available_quantity, 0);
        const avgCost = part.total_available_stock ? totalCost / part.total_available_stock : 0;

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-box-seam" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Stock Inventory</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">
                        <i class="bi bi-check-circle-fill me-1"></i>${part.total_available_stock} available
                    </span>
                    <span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg Cost: ${formatCurrency(avgCost)}
                    </span>
                    ${part.stock_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.stock_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Receipt Date</th>
                                <th>Datecode</th>
                                <th>Available / Original</th>
                                <th>Cost/Unit</th>
                                <th>Total Value</th>
                                ${showActions ? '<th style="width: 100px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${stockToShow.map(stock => `
                                <tr>
                                    <td><strong>${formatDate(stock.receipt_date)}</strong></td>
                                    <td>${escapeHtml(stock.datecode) || '-'}</td>
                                    <td>
                                        <strong class="text-success">${stock.available_quantity}</strong>
                                        <span class="text-muted"> / ${stock.original_quantity}</span>
                                    </td>
                                    <td><strong>${formatCurrency(stock.cost_per_unit)}</strong></td>
                                    <td style="color: #0d6efd; font-weight: 600;">${formatCurrency(stock.cost_per_unit * stock.available_quantity)}</td>
                                    ${showActions ? `
                                    <td>
                                        <button class="btn btn-sm btn-success use-cost-btn"
                                                data-cost="${stock.cost_per_unit}"
                                                data-currency="3"
                                                data-source-type="stock"
                                                data-source-ref="${stock.movement_id}">
                                            <i class="bi bi-check-circle me-1"></i>Use Cost
                                        </button>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // PO DETAILS - PURCHASING
    if (part.po_count > 0 && part.po_details && part.po_details.length > 0) {
        const posToShow = part.po_details.slice(0, 3);
        const priceRange = getPriceRange(part.po_details, 'price');
        const uniqueSuppliers = [...new Set(part.po_details.map(po => po.supplier_name))];

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-cart-fill" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Purchase Orders</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.po_details.length} PO${part.po_details.length !== 1 ? 's' : ''}</span>
                    <span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-building me-1"></i>${uniqueSuppliers.length} supplier${uniqueSuppliers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    ${part.po_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.po_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date Issued</th>
                                <th>PO Reference</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                <th>Status</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${posToShow.map(po => `
                                <tr>
                                    <td><strong>${formatDate(po.date_issued)}</strong></td>
                                    <td>${escapeHtml(po.purchase_order_ref)}</td>
                                    <td>${escapeHtml(po.supplier_name)}</td>
                                    <td>${po.quantity || '-'}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(po.price)} ${escapeHtml(po.currency_code)}</td>
                                    <td><span class="badge bg-secondary">${escapeHtml(po.status_name)}</span></td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${po.supplier_id}"
                                                    data-supplier-name="${escapeHtml(po.supplier_name)}"
                                                    data-source-type="po"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${po.supplier_id}"
                                                    data-cost="${po.price}"
                                                    data-currency="${po.currency_id || 3}"
                                                    data-source-type="po"
                                                    data-source-ref="${po.purchase_order_ref}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // VQ DETAILS - PURCHASING
    if (part.vq_count > 0 && part.vq_details && part.vq_details.length > 0) {
        const vqsToShow = part.vq_details.slice(0, 3);
        const priceRange = getPriceRange(part.vq_details, 'vendor_price');
        const avgLeadTime = part.vq_details.reduce((sum, vq) => sum + (vq.lead_days || 0), 0) / part.vq_details.length;

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-receipt" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Vendor Quotes</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.vq_details.length} quote${part.vq_details.length !== 1 ? 's' : ''}</span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        <i class="bi bi-cash-stack me-1"></i>${formatCurrency(priceRange.min)} - ${formatCurrency(priceRange.max)}
                    </span>` : ''}
                    <span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-clock me-1"></i>${Math.round(avgLeadTime)} days avg
                    </span>
                    ${part.vq_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.vq_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>VQ Number</th>
                                <th>Supplier</th>
                                <th>Quantity</th>
                                <th>Price</th>
                                <th>Lead Time</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${vqsToShow.map(vq => `
                                <tr>
                                    <td><strong>${formatDate(vq.entry_date)}</strong></td>
                                    <td>${escapeHtml(vq.vq_number)}</td>
                                    <td>${escapeHtml(vq.supplier_name)}</td>
                                    <td>${vq.quantity_quoted}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(vq.vendor_price)} ${escapeHtml(vq.currency_code)}</td>
                                    <td><span class="badge" style="background: #6610f2; color: white;">${vq.lead_days} days</span></td>
                                    ${showActions ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${vq.supplier_id}"
                                                    data-supplier-name="${escapeHtml(vq.supplier_name)}"
                                                    data-source-type="vq"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${vq.supplier_id}"
                                                    data-cost="${vq.vendor_price}"
                                                    data-currency="${vq.currency_id || 3}"
                                                    data-lead-days="${vq.lead_days || ''}"
                                                    data-source-type="vq"
                                                    data-source-ref="${vq.vq_number}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>
                                        </div>
                                    </td>` : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // BOM DETAILS - PURCHASING (no actions needed here)
    if (part.bom_usage_count > 0 && part.bom_details && part.bom_details.length > 0) {
        const priceRange = getPriceRange(part.bom_details, 'guide_price');
        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-diagram-3" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>BOM Usage</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.bom_details.length} BOM${part.bom_details.length !== 1 ? 's' : ''}</span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Guide: ${formatCurrency(priceRange.min)} - ${formatCurrency(priceRange.max)}
                    </span>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>BOM Name</th>
                                <th>Qty per BOM</th>
                                <th>Guide Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${part.bom_details.map(bom => `
                                <tr>
                                    <td><strong>${escapeHtml(bom.bom_name)}</strong></td>
                                    <td>${bom.qty_per_bom}</td>
                                    <td style="font-weight: 600; color: #0d6efd;">${formatCurrency(bom.guide_price)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // CQ DETAILS - SALES (no actions needed)
    if (part.cq_count > 0 && part.cq_details && part.cq_details.length > 0) {
        const cqsToShow = part.cq_details.slice(0, 3);
        const priceRange = getPriceRange(part.cq_details, 'unit_price');
        const uniqueCustomers = [...new Set(part.cq_details.map(cq => cq.customer_name))];

        detailsHtml += `
            <div class="modal-section sales">
                <div class="modal-section-header">
                    <i class="bi bi-file-earmark-text" style="color: #198754; font-size: 1rem;"></i>
                    <span>Customer Quotes (CQs)</span>
                    <span class="modal-section-badge" style="background: #198754; color: white;">${part.cq_details.length} quote${part.cq_details.length !== 1 ? 's' : ''}</span>
                    <span class="modal-section-badge" style="background: #20c997; color: white;">
                        <i class="bi bi-people-fill me-1"></i>${uniqueCustomers.length} customer${uniqueCustomers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        Avg: ${formatCurrency(priceRange.avg)}
                    </span>` : ''}
                    ${part.cq_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.cq_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>CQ Number</th>
                                <th>Customer</th>
                                <th>Status</th>
                                <th>Qty Req / Quoted</th>
                                <th>Unit Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${cqsToShow.map(cq => `
                                <tr>
                                    <td><strong>${formatDate(cq.entry_date)}</strong></td>
                                    <td>${escapeHtml(cq.cq_number)}</td>
                                    <td>${escapeHtml(cq.customer_name)}</td>
                                    <td><span class="badge bg-info">${escapeHtml(cq.status)}</span></td>
                                    <td>${cq.quantity_requested || '-'} / <strong>${cq.quantity_quoted || '-'}</strong></td>
                                    <td style="font-weight: 600; color: #198754;">${formatCurrency(cq.unit_price)} ${escapeHtml(cq.currency_code)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    // SALES ORDER DETAILS - SALES (no actions needed)
    if (part.so_count > 0 && part.so_details && part.so_details.length > 0) {
        const sosToShow = part.so_details.slice(0, 3);
        const priceRange = getPriceRange(part.so_details, 'sale_price');
        const uniqueCustomers = [...new Set(part.so_details.map(so => so.customer_name))];

        detailsHtml += `
            <div class="modal-section sales">
                <div class="modal-section-header">
                    <i class="bi bi-cart-check" style="color: #198754; font-size: 1rem;"></i>
                    <span>Sales History</span>
                    <span class="modal-section-badge" style="background: #198754; color: white;">${part.so_details.length} sale${part.so_details.length !== 1 ? 's' : ''}  </span>
                    <span class="modal-section-badge" style="background: #20c997; color: white;">
                        <i class="bi bi-people-fill me-1"></i>${uniqueCustomers.length} customer${uniqueCustomers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0f5132; color: white;">
                        <i class="bi bi-graph-up me-1"></i>${formatCurrency(priceRange.avg)} avg
                    </span>` : ''}
                    ${part.so_details.length > 3 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.so_details.length - 3} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>SO Reference</th>
                                <th>Customer</th>
                                <th>Quantity</th>
                                <th>Sale Price</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${sosToShow.map(so => `
                                <tr>
                                    <td><strong>${formatDate(so.date_entered)}</strong></td>
                                    <td>${escapeHtml(so.sales_order_ref)}</td>
                                    <td>${escapeHtml(so.customer_name)}</td>
                                    <td>${so.order_quantity}</td>
                                    <td style="font-weight: 600; color: #198754;">${formatCurrency(so.sale_price)} ${escapeHtml(so.currency_code)}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    detailsHtml += '</div>';
    modalContent.innerHTML = detailsHtml;

    // Attach event listeners for action buttons
    if (showActions && lineId) {
        // Add Supplier buttons
        modalContent.querySelectorAll('.add-supplier-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const supplierId = this.getAttribute('data-supplier-id');
                const supplierName = this.getAttribute('data-supplier-name');
                const sourceType = this.getAttribute('data-source-type');
                addSuggestedSupplier(lineId, supplierId, supplierName, sourceType, this);
            });
        });

        // Use Cost buttons
        modalContent.querySelectorAll('.use-cost-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const costData = {
                    supplier_id: this.getAttribute('data-supplier-id') || null,
                    cost: parseFloat(this.getAttribute('data-cost')),
                    currency_id: parseInt(this.getAttribute('data-currency')),
                    lead_days: this.getAttribute('data-lead-days') ? parseInt(this.getAttribute('data-lead-days')) : null,
                    source_type: this.getAttribute('data-source-type'),
                    source_reference: this.getAttribute('data-source-ref') || null
                };
                useCost(lineId, costData, this);
            });
        });
    }

    const modal = new bootstrap.Modal(document.getElementById('partDetailsModal'));
    modal.show();
}
// Document ready
document.addEventListener('DOMContentLoaded', function() {
    const loadingSpinner = document.getElementById('loading-spinner');
    const loadingMessage = document.getElementById('loading-message');

    // Set current list ID
    if (window.LOADED_LIST_DATA && window.LOADED_LIST_DATA.header) {
        currentListId = window.LOADED_LIST_DATA.header.id;
    }

    document.addEventListener('click', function(e) {
        const useBtn = e.target.closest('.use-excess-cost-btn');
        if (useBtn) {
            const lineId = useBtn.dataset.lineId;
            const costData = {
                supplier_id: useBtn.dataset.supplierId || null,
                cost: parseFloat(useBtn.dataset.cost),
                currency_id: useBtn.dataset.currencyId ? parseInt(useBtn.dataset.currencyId) : null,
                source_type: 'excess',
                source_reference: useBtn.dataset.sourceRef || null
            };
            useCost(lineId, costData, useBtn);
            return;
        }

        const addBtn = e.target.closest('.add-excess-supplier-btn');
        if (addBtn) {
            addSuggestedSupplier(
                addBtn.dataset.lineId,
                addBtn.dataset.supplierId,
                addBtn.dataset.supplierName || '',
                'excess',
                addBtn
            );
        }
    });

    // Setup button handlers
    const emailBtn = document.getElementById('email-suppliers-btn');
    if (emailBtn) {
        emailBtn.addEventListener('click', function() {
            if (!allResults || allResults.length === 0) return;

            fetch('/parts_list/email-suppliers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    results: allResults,
                    list_id: currentListId
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success && data.redirect) {
                    window.location.href = data.redirect;
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Error navigating to email page');
            });
        });
    }

    const viewAsTableBtn = document.getElementById('view-as-table-btn');
    if (viewAsTableBtn) {
        viewAsTableBtn.addEventListener('click', function() {
            if (!allResults || allResults.length === 0) {
                alert('No results to display');
                return;
            }

            fetch('/parts_list/table-view', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ results: allResults })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success && data.redirect) {
                    window.location.href = data.redirect;
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Error navigating to table view');
            });
        });
    }



    // Auto-analyze the loaded list
    if (window.LOADED_LIST_DATA && window.LOADED_LIST_DATA.lines) {
        analyzePartsWithLineIds(window.LOADED_LIST_DATA.lines);
    }

    function analyzePartsWithLineIds(partsArray) {
        loadingMessage.textContent = 'Loading parts data...';
        loadingSpinner.style.display = 'flex';

        const partsData = partsArray.map(part => {
            const item = {
                part_number: part.customer_part_number || part.part_number,
                quantity: part.quantity
            };
            if (part.id) {
                item.line_id = part.id;
            }
            return item;
        });

        const requestData = { parts: partsData };

        // Add customer filter if present in header
        if (window.LOADED_LIST_DATA.header.customer_id) {
            requestData.customer_ids = [window.LOADED_LIST_DATA.header.customer_id];
        }

        fetch('/parts_list/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestData)
        })
        .then(response => response.json())
        .then(data => {
            loadingSpinner.style.display = 'none';
            if (data.success) {
                displayResults(data.results);
            } else {
                alert('Error: ' + (data.message || 'Unknown error occurred'));
            }
        })
        .catch(error => {
            loadingSpinner.style.display = 'none';
            console.error('Error:', error);
            alert('Error loading parts: ' + error.message);
        });
    }

    function setupStickyHeader() {
    const container = document.querySelector('.parts-table-container');
    const table = document.querySelector('.parts-table');
    const tableHead = table.querySelector('thead');
    const stickyWrapper = document.getElementById('sticky-header-wrapper');
    if (!container || !table || !tableHead || !stickyWrapper) return;

    const theadClone = tableHead.cloneNode(true);
    const tableClone = document.createElement('table');
    tableClone.className = 'table parts-table';
    tableClone.style.marginBottom = '0';
    tableClone.style.width = table.offsetWidth + 'px';
    tableClone.style.tableLayout = 'fixed';
    tableClone.appendChild(theadClone);

    stickyWrapper.innerHTML = '';
    stickyWrapper.appendChild(tableClone);
    stickyWrapper.style.display = 'block';

    // NEW: Copy exact column widths from body to sticky header
    const bodyHeaders = table.querySelectorAll('thead th');
    const stickyHeaders = tableClone.querySelectorAll('thead th');
    bodyHeaders.forEach((header, index) => {
        if (stickyHeaders[index]) {
            const width = header.offsetWidth;
            stickyHeaders[index].style.width = width + 'px';
            stickyHeaders[index].style.minWidth = width + 'px';
            stickyHeaders[index].style.maxWidth = width + 'px';
        }
    });

    tableHead.style.visibility = 'hidden';

    const scrollHandler = () => { stickyWrapper.scrollLeft = container.scrollLeft; };
    container.removeEventListener('scroll', scrollHandler);
    container.addEventListener('scroll', scrollHandler);
}

    function displayResults(results) {
        allResults = results;

        const tbody = document.getElementById('parts-table-body');
        tbody.innerHTML = '';
        results.forEach((part, index) => {
            const row = createPartRow(part, index + 1);
            tbody.appendChild(row);
        });

        const hasILSData = results.some(r => r.ils_total_suppliers > 0);
        if (emailBtn) {
            emailBtn.style.display = hasILSData ? 'inline-block' : 'none';
        }

        setTimeout(() => { setupStickyHeader(); }, 50);
    }

    function createPartRow(part, index) {
        // [Copy the createPartRow function from parts_list.js]
        // This is the same function - creates the table row HTML
        const tr = document.createElement('tr');
        if (!part.found) tr.style.background = '#fff3cd';
        tr.addEventListener('mouseenter', function() { this.style.background = part.found ? '#f8f9fa' : '#fff3cd'; });
        tr.addEventListener('mouseleave', function() { this.style.background = part.found ? '' : '#fff3cd'; });

        let lastSaleDate = '-';
        if (part.so_details && part.so_details.length > 0) {
            const dates = part.so_details.map(so => new Date(so.date_entered));
            const latestDate = new Date(Math.max(...dates));
            lastSaleDate = formatDate(latestDate);
        }

        let guidePrice = '-';
        if (part.bom_details && part.bom_details.length > 0) {
            const prices = part.bom_details.map(b => parseFloat(b.guide_price)).filter(p => !isNaN(p) && p > 0);
            if (prices.length > 0) guidePrice = formatCurrency(Math.max(...prices));
        }

        let latestVqPrice = '-';
        let latestVqSupplier = '';
        let latestVqDate = '';
        if (part.vq_details && part.vq_details.length > 0) {
            const sortedVqs = [...part.vq_details].sort((a, b) => {
                const dateA = new Date(a.entry_date);
                const dateB = new Date(b.entry_date);
                return dateB - dateA;
            });
            const latestVq = sortedVqs[0];
            latestVqPrice = formatCurrency(latestVq.vendor_price);
            latestVqSupplier = latestVq.supplier_name || '';
            latestVqDate = formatDate(latestVq.entry_date);
        }

        let avgStockCost = '-';
        if (part.stock_details && part.stock_details.length > 0) {
            const validStock = part.stock_details.filter(s => {
                const cost = parseFloat(s.cost_per_unit);
                const qty = parseFloat(s.available_quantity);
                return !isNaN(cost) && cost > 0 && !isNaN(qty) && qty > 0;
            });
            if (validStock.length > 0) {
                const totalCost = validStock.reduce((sum, s) => sum + (parseFloat(s.cost_per_unit) * parseFloat(s.available_quantity)), 0);
                const totalQty = validStock.reduce((sum, s) => sum + parseFloat(s.available_quantity), 0);
                avgStockCost = formatCurrency(totalCost / totalQty);
            }
        }

        let stockDisplay = '0';
        let stockClasses = 'stock-badge badge-muted';
        let stockIcon = '';
        const requestedQty = part.quantity || 1;
        const availableStock = part.total_available_stock || 0;
        stockDisplay = availableStock.toString();
        if (availableStock >= requestedQty) {
            stockClasses = 'stock-badge stock-badge-success';
            stockIcon = '<i class="bi bi-check-circle-fill" style="font-size: 1rem; color: #198754;"></i>';
        } else if (availableStock > 0) {
            stockClasses = 'stock-badge stock-badge-warning';
            stockIcon = '<i class="bi bi-exclamation-triangle-fill" style="font-size: 1rem; color: #ffc107;"></i>';
        } else {
            stockClasses = 'stock-badge stock-badge-danger';
            stockIcon = '<i class="bi bi-x-circle-fill" style="font-size: 1rem; color: #dc3545;"></i>';
        }

        const stockSummaryDisplay = `
            <div style="display: flex; flex-direction: column; gap: 0.2rem;">
                <span class="${stockClasses}">
                    ${stockIcon}
                    <span>${stockDisplay}</span>
                </span>
                <div style="font-weight: 600; color: ${avgStockCost !== '-' ? '#0d6efd' : '#adb5bd'}; font-size: 0.8rem;">
                    ${avgStockCost}
                </div>
            </div>
        `;

        const bomGuideDisplay = `
            <div style="display: flex; flex-direction: column; gap: 0.2rem;">
                <span class="badge-count ${part.bom_usage_count > 0 ? 'badge-success' : 'badge-muted'}">
                    ${part.bom_usage_count > 0 ? part.bom_usage_count : '-'}
                </span>
                <div style="font-weight: 600; color: ${guidePrice !== '-' ? '#0d6efd' : '#adb5bd'}; font-size: 0.8rem;">
                    ${guidePrice}
                </div>
            </div>
        `;

        const avgSalePriceDisplay = formatCurrency(part.avg_sale_price);
        const saleSummaryDisplay = `
            <div style="display: flex; flex-direction: column; gap: 0.2rem;">
                <div style="font-weight: 600; color: ${part.avg_sale_price ? '#198754' : '#adb5bd'}; font-size: 0.8rem;">
                    ${avgSalePriceDisplay}
                </div>
                <div><small>${lastSaleDate}</small></div>
            </div>
        `;

        let ilsDisplay = '-';
        let ilsClickable = '';
        let ilsClickHandler = '';
        let suggestedSuppliers = 0;
        if (part.line_id) {
            suggestedSuppliers = part.suggested_suppliers_count || 0;
        }

        let chosenCostDisplay = '-';
        let chosenCostBadge = 'badge-muted';
        if (part.chosen_cost !== null && part.chosen_cost !== undefined) {
            chosenCostDisplay = formatCurrency(part.chosen_cost);
            chosenCostBadge = 'badge-success';
            if (part.chosen_supplier_name) {
                chosenCostDisplay += `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(part.chosen_supplier_name)}</small>`;
            }
        }

        if (part.ils_total_suppliers > 0) {
            const latestIlsDateHtml = part.ils_latest_search_date
                ? `<div class="mt-1"><small class="text-muted"><i class="bi bi-calendar"></i> ${formatDate(part.ils_latest_search_date)}</small></div>`
                : '';
            ilsDisplay = `
                <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                    <span class="ils-badge ils-badge-suppliers">
                        <i class="bi bi-building"></i>
                        ${part.ils_total_suppliers} ${part.ils_total_suppliers !== 1 ? '' : ''}
                    </span>
                    ${part.ils_preferred_suppliers > 0 ? `
                        <span class="ils-badge ils-badge-preferred">
                            <i class="bi bi-star-fill"></i>
                            ${part.ils_preferred_suppliers} preferred
                        </span>` : ''
                    }
                    ${latestIlsDateHtml}
                </div>
            `;
            ilsClickable = 'ils-cell-clickable';
            ilsClickHandler = `onclick="handleIlsClick(${index - 1})"`;
        }

        let excessDisplay = '-';
        if (part.excess_count > 0 && part.lowest_excess_price !== null && part.lowest_excess_price !== undefined) {
            const excessPrice = formatCurrencyWithCode(part.lowest_excess_price, part.lowest_excess_currency_code || 'GBP');
            const supplierLine = part.lowest_excess_supplier ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(part.lowest_excess_supplier)}</small>` : '';
            const actions = part.line_id ? `
                <div class="mt-1 d-flex gap-1">
                    ${part.lowest_excess_price ? `
                    <button class="btn btn-sm btn-outline-primary use-excess-cost-btn"
                            data-line-id="${part.line_id}"
                            data-cost="${part.lowest_excess_price}"
                            data-currency-id="${part.lowest_excess_currency_id || ''}"
                            data-currency-code="${part.lowest_excess_currency_code || ''}"
                            data-supplier-id="${part.lowest_excess_supplier_id || ''}"
                            data-supplier-name="${escapeHtml(part.lowest_excess_supplier || '')}"
                            data-source-type="excess"
                            data-source-ref="${part.lowest_excess_list_id || ''}">
                        <i class="bi bi-check-circle"></i>
                    </button>` : ''}
                    ${part.lowest_excess_supplier_id ? `
                    <button class="btn btn-sm btn-outline-secondary add-excess-supplier-btn"
                            data-line-id="${part.line_id}"
                            data-supplier-id="${part.lowest_excess_supplier_id}"
                            data-supplier-name="${escapeHtml(part.lowest_excess_supplier || '')}"
                            data-source-type="excess">
                        <i class="bi bi-plus-circle"></i>
                    </button>` : ''}
                </div>` : '';
            excessDisplay = `<div style="font-weight: 600; color: #0d6efd;">${excessPrice}${supplierLine}${actions}</div>`;
        } else if (part.excess_count > 0) {
            excessDisplay = `<span class="text-muted">${part.excess_count} line${part.excess_count !== 1 ? 's' : ''}</span>`;
        }

        let partsListQuotesDisplay = '-';
        if (part.parts_list_quotes_count > 0) {
            partsListQuotesDisplay = `
                <span class="badge-count badge-success">
                    ${part.parts_list_quotes_count}
                </span>
            `;
        }

        tr.innerHTML = `
    <td style="width: 60px; min-width: 60px;">${index}</td>
    <td style="width: 200px; min-width: 200px;">
        <strong>
            ${escapeHtml(part.input_part_number)}
            ${part.chosen_cost !== null && part.chosen_cost !== undefined ? '<i class="bi bi-check-circle-fill text-success ms-1"></i>' : ''}
        </strong>
        ${!part.found ? '<br><small class="text-danger">Not Found</small>' : ''}
    </td>
    <td style="width: 80px; min-width: 80px; text-align: center; font-weight: 600; color: #495057;">
        ${part.quantity || 1}
    </td>
    <td style="width: 170px; min-width: 170px;" class="purchasing-col">
        ${stockSummaryDisplay}
    </td>
    <td style="width: 100px; min-width: 100px;" class="purchasing-col">
        <span class="badge-count ${part.vq_count > 0 ? 'badge-success' : 'badge-muted'}">
            ${part.vq_count > 0 ? part.vq_count : '-'}
        </span>
    </td>
    <td style="width: 170px; min-width: 170px;" class="purchasing-col">
        <div style="font-weight: 600; color: ${latestVqPrice !== '-' ? '#0d6efd' : '#adb5bd'};">
            ${latestVqPrice}
            ${latestVqSupplier ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(latestVqSupplier)}</small>` : ''}
            ${latestVqDate !== '-' ? `<br><small class="text-muted" style="font-size: 0.7rem;"><i class="bi bi-calendar3"></i> ${latestVqDate}</small>` : ''}
        </div>
    </td>
    <td style="width: 100px; min-width: 100px;" class="purchasing-col">
        <div>
            <span class="badge-count ${part.po_count > 0 ? 'badge-success' : 'badge-muted'}">
                ${part.po_count > 0 ? part.po_count : '-'}
            </span>
            ${part.most_recent_po_supplier ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(part.most_recent_po_supplier)}</small>` : ''}
        </div>
    </td>
    <td style="width: 170px; min-width: 170px;" class="purchasing-col">
        ${excessDisplay}
    </td>
    <td style="width: 150px; min-width: 150px;" class="purchasing-col">
        ${partsListQuotesDisplay}
    </td>
    <td style="width: 120px; min-width: 120px; ${part.ils_total_suppliers > 0 ? 'cursor: pointer;' : ''}"
        class="purchasing-col ${ilsClickable}"
        ${ilsClickHandler}
        data-part-index="${index - 1}">
        ${ilsDisplay}
    </td>
    <td style="width: 100px; min-width: 100px;" class="sales-col">
        <span class="badge-count ${part.cq_count > 0 ? 'badge-success' : 'badge-muted'}">
            ${part.cq_count > 0 ? part.cq_count : '-'}
        </span>
    </td>
    <td style="width: 100px; min-width: 100px;" class="sales-col">
        <span class="badge-count ${part.so_count > 0 ? 'badge-success' : 'badge-muted'}">
            ${part.so_count > 0 ? part.so_count : '-'}
        </span>
    </td>
    <td style="width: 170px; min-width: 170px;" class="sales-col">${saleSummaryDisplay}</td>
    <td style="width: 170px; min-width: 170px;">${bomGuideDisplay}</td>
    <td style="width: 120px; min-width: 120px;">
        ${part.found && (part.bom_usage_count > 0 || part.vq_count > 0 || part.so_count > 0 || part.cq_count > 0 || part.po_count > 0 || part.stock_movement_count > 0 || part.ils_total_suppliers > 0 || part.excess_count > 0 || part.parts_list_quotes_count > 0)
            ? `<button class="btn btn-sm btn-outline-primary"
                       onclick="handleViewDetails(${index - 1})"
                       style="cursor: pointer; padding: 0.25rem 0.5rem; font-size: 0.875rem;">
                <i class="bi bi-eye"></i> VIEW
               </button>`
            : '-'}
    </td>
    <td style="width: 120px; min-width: 120px;">
        <span class="badge-count ${suggestedSuppliers > 0 ? 'badge-success' : 'badge-muted'}">
            ${suggestedSuppliers > 0 ? suggestedSuppliers : '-'}
        </span>
    </td>
    <td style="width: 140px; min-width: 140px; font-weight: 600;">
        <span class="${chosenCostBadge}" style="display: block;">
            ${chosenCostDisplay}
        </span>
    </td>
`;

        return tr;
    }
});

// NOTE: You'll need to copy showPartDetailsModal and showIlsDetailsModal functions here too
// They're too long to include in this response but they should be copied exactly from parts_list.js
