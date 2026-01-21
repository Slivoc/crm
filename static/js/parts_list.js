// Global variables - DECLARE ONLY ONCE AT TOP
window.allResults = [];
let currentListId = null;
let selectedContact = null;
let selectedCustomer = null;
const VIEW_ANALYSIS_AUTO_LIMIT = 40;

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

    const originalCols = table.querySelectorAll('thead th');
    const clonedCols = theadClone.querySelectorAll('th');

    originalCols.forEach((col, idx) => {
        if (clonedCols[idx]) {
            clonedCols[idx].style.width = col.offsetWidth + 'px';
            clonedCols[idx].style.minWidth = col.offsetWidth + 'px';
            clonedCols[idx].style.maxWidth = col.offsetWidth + 'px';
        }
    });

    stickyWrapper.innerHTML = '';
    stickyWrapper.appendChild(tableClone);
    stickyWrapper.style.display = 'block';
    tableHead.style.visibility = 'hidden';

    const scrollHandler = () => {
        stickyWrapper.scrollLeft = container.scrollLeft;
    };
    container.removeEventListener('scroll', scrollHandler);
    container.addEventListener('scroll', scrollHandler);
}

function displayResults(results) {
    window.allResults = results;

    const openCostingBtn = document.getElementById('open-costing-btn');
    if (openCostingBtn && currentListId) {
        openCostingBtn.style.display = 'inline-block';
        openCostingBtn.onclick = () => {
            window.location.href = `/parts_list/parts-lists/${currentListId}/costing`;
        };
    }

    const openSourcingBtn = document.getElementById('open-sourcing-btn');
    if (openSourcingBtn && currentListId) {
        openSourcingBtn.style.display = 'inline-block';
        openSourcingBtn.onclick = () => {
            window.location.href = `/parts_list/parts-lists/${currentListId}/sourcing`;
        };
    }

    const tbody = document.getElementById('parts-table-body');

    if (!tbody) {
        console.warn('parts-table-body element not found, skipping displayResults');
        return;
    }

    tbody.innerHTML = '';

    let displayIndex = 1;
    results.forEach((part, index) => {
        const isAlt = part.is_global_alternative || part.line_type === 'alternate';
        const row = createPartRow(part, displayIndex, isAlt, index);
        tbody.appendChild(row);

        if (!isAlt) {
            displayIndex++;
        }
    });

    const resultsSection = document.getElementById('results-section');
    if (resultsSection) {
        resultsSection.style.display = 'block';
    }

    const hasILSData = results.some(r => r.ils_total_suppliers > 0);

    const emailBtn1 = document.getElementById('email-suppliers-btn');
    const emailBtn2 = document.getElementById('email-suppliers-btn-header');
    if (emailBtn1) emailBtn1.style.display = hasILSData ? 'inline-block' : 'none';
    if (emailBtn2) emailBtn2.style.display = hasILSData ? 'inline-block' : 'none';

    const viewAsTableBtn = document.getElementById('view-as-table-btn');
    if (viewAsTableBtn) {
        viewAsTableBtn.style.display = 'inline-block';
    }
}

function buildBasicResultsFromLines(lines) {
    if (!Array.isArray(lines)) return [];
    return lines.map((line, index) => ({
        line_id: line.id || line.line_id || null,
        line_number: line.line_number || index + 1,
        input_part_number: line.customer_part_number || line.part_number || '',
        base_part_number: line.base_part_number || null,
        quantity: line.quantity || 1,
        found: true,
        global_alternatives: [],
        stock_details: [],
        vq_details: [],
        so_details: [],
        bom_details: [],
        line_contacted_suppliers: [],
        line_contacted_suppliers_count: 0,
        line_supplier_quote_count: 0,
        parts_list_quotes_unique_suppliers: 0,
        excess_count: 0,
        total_available_stock: 0,
        stock_movement_count: 0,
        ils_total_suppliers: 0,
        ils_preferred_suppliers: 0,
        ils_latest_search_date: null
    }));
}

function showDeferredAnalysisBanner(lineCount, onRun) {
    const banner = document.getElementById('analysis-deferred-banner');
    const countEl = document.getElementById('analysis-deferred-count');
    const runButton = document.getElementById('run-analysis-btn');
    if (!banner || !runButton) return;
    if (countEl) countEl.textContent = lineCount;
    banner.style.display = 'flex';
    runButton.disabled = false;
    runButton.addEventListener('click', () => {
        runButton.disabled = true;
        banner.style.display = 'none';
        onRun();
    }, { once: true });
}

function createPartRow(part, displayIndex, isAlt, actualIndex) {
    const tr = document.createElement('tr');

    if (isAlt) {
        tr.style.cssText = 'background: #e0f2fe !important; border-left: 4px solid #0ea5e9 !important; height: 38px !important; font-size: 0.82rem !important;';
    } else if (!part.found) {
        tr.style.cssText = 'background: #fff3cd !important; height: 48px !important;';
    } else {
        tr.style.cssText = 'background: #ffffff !important; height: 48px !important;';
    }

    tr.addEventListener('mouseenter', function() {
        if (isAlt) {
            this.style.cssText = 'background: #bae6fd !important; border-left: 4px solid #0ea5e9 !important; height: 38px !important; font-size: 0.82rem !important;';
        } else if (!part.found) {
            this.style.cssText = 'background: #ffe69c !important; height: 48px !important;';
        } else {
            this.style.cssText = 'background: #f8f9fa !important; height: 48px !important;';
        }
    });

    tr.addEventListener('mouseleave', function() {
        if (isAlt) {
            this.style.cssText = 'background: #e0f2fe !important; border-left: 4px solid #0ea5e9 !important; height: 38px !important; font-size: 0.82rem !important;';
        } else if (!part.found) {
            this.style.cssText = 'background: #fff3cd !important; height: 48px !important;';
        } else {
            this.style.cssText = 'background: #ffffff !important; height: 48px !important;';
        }
    });

    let lastSaleDate = '-';
    let lastSaleDateRaw = null;
    if (part.so_details && part.so_details.length > 0) {
        const dates = part.so_details
            .map(so => new Date(so.date_entered))
            .filter(date => !Number.isNaN(date.getTime()));
        if (dates.length > 0) {
            const latestDate = new Date(Math.max(...dates));
            lastSaleDate = formatDate(latestDate);
            lastSaleDateRaw = latestDate;
        }
    }

    let guidePrice = '-';
    if (part.bom_details && part.bom_details.length > 0) {
        const prices = part.bom_details.map(b => parseFloat(b.guide_price)).filter(p => !isNaN(p) && p > 0);
        if (prices.length > 0) guidePrice = formatCurrency(Math.max(...prices));
    }

    let latestVqPrice = '-';
    let latestVqSupplier = '';
    let latestVqDate = '';
    let latestVqDateRaw = null;
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
        latestVqDateRaw = latestVq.entry_date;
    }

    let avgStockCost = '-';
    if (part.stock_details && part.stock_details.length > 0) {
        const validStock = part.stock_details.map(s => ({
            cost: parseFloat(s.cost_per_unit),
            qty: parseFloat(s.available_quantity)
        })).filter(item => !isNaN(item.cost) && item.cost > 0 && !isNaN(item.qty) && item.qty > 0);

        if (validStock.length > 0) {
            const totalCost = validStock.reduce((sum, s) => sum + (s.cost * s.qty), 0);
            const totalQty = validStock.reduce((sum, s) => sum + s.qty, 0);

            if (totalQty > 0) {
                avgStockCost = formatCurrency(totalCost / totalQty);
            }
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



    let ilsDisplay = '-';
    let ilsClickable = '';

    if (part.ils_total_suppliers > 0) {
        const latestIlsDateHtml = part.ils_latest_search_date
            ? `<div class="mt-1">${formatDateIndicator(part.ils_latest_search_date, { icon: 'bi bi-calendar', className: ' date-compact' })}</div>`
            : '';
        ilsDisplay = `
            <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                <span class="ils-badge ils-badge-suppliers">
                    <i class="bi bi-building"></i>
                    ${part.ils_total_suppliers}
                </span>
                ${part.ils_preferred_suppliers > 0 ? `
                    <span class="ils-badge ils-badge-preferred">
                        <i class="bi bi-star-fill"></i>
                        ${part.ils_preferred_suppliers}
                    </span>` : ''
                }
                ${latestIlsDateHtml}
            </div>
        `;
        ilsClickable = 'clickable-cell';
    }

        // Parts List Quotes display
    let plQuotesDisplay = '-';
    let plQuotesClickable = '';
    let plQuotesColor = '#adb5bd';

    if (part.parts_list_quotes_count > 0) {
        plQuotesClickable = 'clickable-cell';
        plQuotesColor = '#0d6efd';

        const lowestPrice = part.lowest_parts_list_quote_price;
        const lowestSupplier = part.lowest_parts_list_quote_supplier;

        plQuotesDisplay = `
            <div style="display: flex; flex-direction: column; gap: 0.25rem;">
                <span class="badge-count badge-success">
                    ${part.parts_list_quotes_count}
                </span>
                ${part.parts_list_quotes_unique_suppliers > 0 ? `
                    <span class="badge" style="background: #6610f2; color: white; font-size: 0.7rem;">
                        <i class="bi bi-building"></i> ${part.parts_list_quotes_unique_suppliers}
                    </span>` : ''
                }
                ${lowestPrice ? `
                    <div style="font-weight: 600; color: #0d6efd; font-size: 0.8rem; margin-top: 0.2rem;">
                        ${formatCurrency(lowestPrice)}
                    </div>
                    ${lowestSupplier ? `<small class="text-muted" style="font-size: 0.68rem;">${escapeHtml(lowestSupplier)}</small>` : ''}
                ` : ''}
            </div>
        `;
    }

    let excessDisplay = '-';
    if (part.excess_count > 0 && part.lowest_excess_price !== null && part.lowest_excess_price !== undefined) {
        const excessPrice = formatCurrencyWithCode(part.lowest_excess_price, part.lowest_excess_currency_code || 'GBP');
        const supplierLine = part.lowest_excess_supplier
            ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(part.lowest_excess_supplier)}</small>`
            : '';
        excessDisplay = `<div style="font-weight: 600; color: #0d6efd;">${excessPrice}${supplierLine}</div>`;
    } else if (part.excess_count > 0) {
        excessDisplay = `<span class="text-muted">${part.excess_count} line${part.excess_count !== 1 ? 's' : ''}</span>`;
    }

    const numericLineNumber = Number(part.line_number);
    const isSubLine = !!part.parent_line_id ||
        part.line_type === 'alternate' ||
        part.line_type === 'price_break' ||
        (Number.isFinite(numericLineNumber) && numericLineNumber % 1 !== 0);
    const canDuplicate = !!part.line_id && part.line_type !== 'alternate';
    const partNumberForCopy = isAlt
        ? (part.input_part_number || part.alt_part_number || part.base_part_number || '')
        : (part.input_part_number || '');
    const copyPartNumberButton = partNumberForCopy
        ? `<button class="btn btn-sm icon-action-btn copy-part-number-btn"
                   data-part-number="${encodeURIComponent(partNumberForCopy)}"
                   title="Copy part number">
              <i class="bi bi-clipboard"></i>
           </button>`
        : '';
    const duplicateButton = canDuplicate
        ? `<button class="btn btn-sm icon-action-btn duplicate-line-btn"
                   data-part-index="${actualIndex}"
                   data-line-id="${part.line_id}"
                   title="Add price break">
              <i class="bi bi-plus-square"></i>
           </button>`
        : '';

    let alternativesDisplay = '-';
    if (!isSubLine && part.global_alternatives && part.global_alternatives.length > 0) {
        const inStockCount = part.global_alternatives.filter(alt => alt.has_stock).length;
        alternativesDisplay = `
            <i class="bi bi-arrow-left-right text-info" style="font-size: 1.1rem; cursor: pointer;" title="${inStockCount} alternatives in stock"></i>
            ${inStockCount > 0 ? `<small class="text-muted ms-1">${inStockCount}</small>` : ''}
        `;
    }

    let lineNumberDisplay = part.line_number || displayIndex;
    if (isAlt) {
        lineNumberDisplay = `<span style="display: flex; align-items: center; justify-content: center; color: #1565c0; font-size: 0.75rem;">
            <i class="bi bi-arrow-return-right" style="font-size: 0.9rem; margin-right: 0.2rem;"></i>
        </span>`;
    }

    const latestVqDateHtml = latestVqDateRaw
        ? `<br>${formatDateIndicator(latestVqDateRaw, { icon: 'bi bi-calendar3', className: ' date-compact' })}`
        : '';

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
    const lastSaleDisplay = formatDateIndicator(lastSaleDateRaw, { className: ' date-compact' });
    const saleSummaryDisplay = `
        <div style="display: flex; flex-direction: column; gap: 0.2rem;">
            <div style="font-weight: 600; color: ${part.avg_sale_price ? '#198754' : '#adb5bd'}; font-size: 0.8rem;">
                ${avgSalePriceDisplay}
            </div>
            <div>${lastSaleDisplay}</div>
        </div>
    `;

    tr.innerHTML = `
        <td style="width: 60px; min-width: 60px;">${lineNumberDisplay}</td>
        <td style="width: 200px; min-width: 200px; ${isAlt ? 'padding-left: 1.2rem;' : ''}">
            <div class="d-flex align-items-start justify-content-between gap-2">
                <div>
                    ${isAlt ? '<span class="badge bg-primary me-2" style="font-size: 0.6rem; padding: 0.15rem 0.35rem;">ALT</span>' : ''}
                    <strong style="${isAlt ? 'color: #0d6efd; font-size: 0.9rem;' : ''}">
                        ${escapeHtml(isAlt ? (part.input_part_number || part.alt_part_number || part.base_part_number) : part.input_part_number)}
                    </strong>
                    ${!part.found ? '<br><small class="text-danger">Not Found</small>' : ''}
                    ${isAlt && part.parent_base_part_number ? `<br><small class="text-muted" style="font-size: 0.68rem; margin-left: 0.25rem;">For: ${escapeHtml(part.parent_base_part_number)}</small>` : ''}
                </div>
                <div class="d-flex align-items-center gap-1">
                    ${copyPartNumberButton}
                    ${duplicateButton}
                </div>
            </div>
        </td>
        <td style="width: 110px; min-width: 110px; text-align: center; cursor: ${!isSubLine && alternativesDisplay !== '-' ? 'pointer' : 'default'};"
            class="${!isSubLine && alternativesDisplay !== '-' ? 'clickable-cell' : ''}"
            ${!isSubLine && alternativesDisplay !== '-' ? `onclick="showAlternativesModal(${actualIndex})"` : ''}>
            ${alternativesDisplay}
        </td>
        <td style="width: 80px; min-width: 80px; text-align: center; font-weight: 600; color: #495057;">
            ${part.quantity || 1}
        </td>
        <td style="width: 220px; min-width: 220px;">
            ${buildStatusDisplay(part)}
        </td>
        <td style="width: 170px; min-width: 170px; cursor: ${part.stock_movement_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.stock_movement_count > 0 ? 'clickable-cell' : ''}"
            ${part.stock_movement_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'stock')"` : ''}>
            ${stockSummaryDisplay}
        </td>
        <td style="width: 100px; min-width: 100px; cursor: ${part.vq_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.vq_count > 0 ? 'clickable-cell' : ''}"
            ${part.vq_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'vq')"` : ''}>
            <span class="badge-count ${part.vq_count > 0 ? 'badge-success' : 'badge-muted'}">
                ${part.vq_count > 0 ? part.vq_count : '-'}
            </span>
        </td>
        <td style="width: 170px; min-width: 170px; cursor: ${part.vq_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.vq_count > 0 ? 'clickable-cell' : ''}"
            ${part.vq_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'vq')"` : ''}>
            <div style="font-weight: 600; color: ${latestVqPrice !== '-' ? '#0d6efd' : '#adb5bd'};">
                ${latestVqPrice}
                ${latestVqSupplier ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(latestVqSupplier)}</small>` : ''}
                ${latestVqDateHtml}
            </div>
        </td>
        <td style="width: 100px; min-width: 100px; cursor: ${part.po_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.po_count > 0 ? 'clickable-cell' : ''}"
            ${part.po_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'po')"` : ''}>
            <div>
                <span class="badge-count ${part.po_count > 0 ? 'badge-success' : 'badge-muted'}">
                    ${part.po_count > 0 ? part.po_count : '-'}
                </span>
                ${part.most_recent_po_supplier ? `<br><small class="text-muted" style="font-size: 0.75rem;">${escapeHtml(part.most_recent_po_supplier)}</small>` : ''}
            </div>
        </td>
        <td style="width: 170px; min-width: 170px;"
            class="purchasing-col">
            ${excessDisplay}
        </td>

         <td style="width: 150px; min-width: 150px; cursor: ${part.parts_list_quotes_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${plQuotesClickable}"
            ${part.parts_list_quotes_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'pl_quotes')"` : ''}>
            ${plQuotesDisplay}
        </td>

        <td style="width: 90px; min-width: 90px; text-align: center; cursor: ${part.qpl_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.qpl_count > 0 ? 'clickable-cell' : ''}"
            ${part.qpl_count > 0 ? `onclick="showQplDetailsModal(${actualIndex})"` : ''}>
            <span class="badge-count ${part.qpl_count > 0 ? 'badge-success' : 'badge-muted'}">
                ${part.qpl_count > 0 ? part.qpl_count : '-'}
            </span>
        </td>

        <td style="width: 120px; min-width: 120px; ${part.ils_total_suppliers > 0 ? 'cursor: pointer;' : ''}"
            class="purchasing-col ${ilsClickable}"
            ${part.ils_total_suppliers > 0 ? `onclick="handleIlsClick(${actualIndex})"` : ''}
            data-part-index="${actualIndex}">
            ${ilsDisplay}
        </td>
        <td style="width: 170px; min-width: 170px; cursor: ${part.bom_usage_count > 0 ? 'pointer' : 'default'};"
            class="purchasing-col ${part.bom_usage_count > 0 ? 'clickable-cell' : ''}"
            ${part.bom_usage_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'bom')"` : ''}>${bomGuideDisplay}</td>
        <td style="width: 100px; min-width: 100px; cursor: ${part.cq_count > 0 ? 'pointer' : 'default'};"
    class="sales-col ${part.cq_count > 0 ? 'clickable-cell' : ''}"
    ${part.cq_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'cq')"` : ''}>
    ${part.cq_count > 0 ? `
        <div style="display: flex; flex-direction: column; gap: 0.25rem;">
            <span class="badge-count badge-success">
                ${part.cq_count}
            </span>
            ${(part.cq_from_cqs > 0 && part.cq_from_parts_lists > 0) ? `
                <div style="font-size: 0.65rem; color: #6c757d;">
                    <span class="badge" style="background: #6c757d; font-size: 0.6rem; padding: 0.15rem 0.3rem;">CQ: ${part.cq_from_cqs}</span>
                    <span class="badge" style="background: #0d6efd; font-size: 0.6rem; padding: 0.15rem 0.3rem;">PL: ${part.cq_from_parts_lists}</span>
                </div>
            ` : ''}
        </div>
    ` : `<span class="badge-count badge-muted">-</span>`}
</td>
        <td style="width: 100px; min-width: 100px; cursor: ${part.so_count > 0 ? 'pointer' : 'default'};"
            class="sales-col ${part.so_count > 0 ? 'clickable-cell' : ''}"
            ${part.so_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'so')"` : ''}>
            <span class="badge-count ${part.so_count > 0 ? 'badge-success' : 'badge-muted'}">
                ${part.so_count > 0 ? part.so_count : '-'}
            </span>
        </td>
        <td style="width: 170px; min-width: 170px; cursor: ${part.so_count > 0 ? 'pointer' : 'default'};"
            class="sales-col ${part.so_count > 0 ? 'clickable-cell' : ''}"
            ${part.so_count > 0 ? `onclick="showPartDetailsModal(window.allResults[${actualIndex}], 'so')"` : ''}>${saleSummaryDisplay}</td>
    `;
    return tr;
}

function removeContact() {
    selectedContact = null;
    updateSelectedContactDisplay();
}

function updateSelectedContactDisplay() {
    const selectedContactDisplay = document.getElementById('selected-contact-display');

    if (!selectedContact) {
        selectedContactDisplay.classList.remove('has-customer');
        selectedContactDisplay.innerHTML = '';
        return;
    }

    selectedContactDisplay.classList.add('has-customer');

    let displayText = selectedContact.full_name;
    if (selectedContact.job_title) {
        displayText += ` (${selectedContact.job_title})`;
    }
    if (selectedContact.customer_name) {
        displayText += ` - ${selectedContact.customer_name}`;
    }

    selectedContactDisplay.innerHTML = `
        <div class="selected-customer-info">
            <div class="customer-name">
                <i class="bi bi-person"></i>
                <strong>${escapeHtml(displayText)}</strong>
            </div>
            <button class="remove-customer-btn" onclick="removeContact()" title="Remove contact">×</button>
        </div>
    `;
}

function updateSelectedCustomerDisplay() {
    const selectedCustomerDisplay = document.getElementById('selected-customer-display');

    if (!selectedCustomer) {
        selectedCustomerDisplay.classList.remove('has-customer');
        selectedCustomerDisplay.innerHTML = '';
        return;
    }

    selectedCustomerDisplay.classList.add('has-customer');
    selectedCustomerDisplay.innerHTML = `
        <div class="selected-customer-info">
            <div class="customer-name">
                <i class="bi bi-building"></i>
                <strong>${escapeHtml(selectedCustomer.name)}</strong>
            </div>
            <button class="remove-customer-btn" onclick="removeCustomer()" title="Remove customer">×</button>
        </div>
    `;
}

function handleIlsClick(partIndex) {
    if (allResults && allResults[partIndex]) {
        showIlsDetailsModal(allResults[partIndex]);
    }
}

function showQplDetailsModal(partIndex) {
    const part = window.allResults && window.allResults[partIndex];
    if (!part) return;

    const basePart = part.base_part_number || part.input_part_number || '';
    const modalEl = document.getElementById('qplDetailsModal');
    const summaryEl = document.getElementById('qplDetailsSummary');
    const bodyEl = document.getElementById('qplDetailsBody');

    if (!modalEl || !summaryEl || !bodyEl) return;

    summaryEl.textContent = basePart ? `Results for ${basePart}` : 'Results';
    bodyEl.innerHTML = `
        <tr>
            <td colspan="3" class="text-center text-muted">
                <span class="spinner-border spinner-border-sm me-2"></span>Loading...
            </td>
        </tr>
    `;

    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    modal.show();

    if (!basePart) {
        bodyEl.innerHTML = `
            <tr>
                <td colspan="3" class="text-center text-muted">No part number available.</td>
            </tr>
        `;
        return;
    }

    fetch(`/parts_list/parts-lists/qpl?part=${encodeURIComponent(basePart)}`)
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                bodyEl.innerHTML = `
                    <tr>
                        <td colspan="3" class="text-center text-danger">${escapeHtml(data.message || 'Error loading QPL data.')}</td>
                    </tr>
                `;
                return;
            }

            if (!data.results || data.results.length === 0) {
                bodyEl.innerHTML = `
                    <tr>
                        <td colspan="3" class="text-center text-muted">No QPL approvals found.</td>
                    </tr>
                `;
                return;
            }

            bodyEl.innerHTML = '';
            data.results.forEach(row => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${escapeHtml(row.manufacturer_name || '-')}</td>
                    <td>${escapeHtml(row.cage_code || '-')}</td>
                    <td>${escapeHtml(row.location || '-')}</td>
                `;
                bodyEl.appendChild(tr);
            });
        })
        .catch(error => {
            console.error('Error loading QPL data:', error);
            bodyEl.innerHTML = `
                <tr>
                    <td colspan="3" class="text-center text-danger">Error loading QPL data.</td>
                </tr>
            `;
        });
}

function openQuickAddSupplier(companyName = '', email = '') {
    // Hide ILS modal if it's open
    const ilsModal = bootstrap.Modal.getInstance(document.getElementById('ilsDetailsModal'));
    if (ilsModal) {
        ilsModal.hide();
    }

    const form = document.getElementById('quickAddSupplierForm');
    form.reset();

    if (companyName) {
        document.getElementById('quick_supplier_name').value = companyName;
    }
    if (email) {
        document.getElementById('quick_contact_email').value = email;
    }

    const modal = new bootstrap.Modal(document.getElementById('quickAddSupplierModal'));
    modal.show();
}

function handleViewDetails(partIndex) {
    if (allResults && allResults[partIndex]) {
        showPartDetailsModal(allResults[partIndex]);
    }
}

function removeCustomer() {
    selectedCustomer = null;
    updateSelectedCustomerDisplay();
}

function formatCurrency(value) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '-';
    return '£' + parseFloat(value).toFixed(2);
}

function formatCurrencyWithSymbol(value, symbol) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '-';
    const numeric = parseFloat(value);
    if (isNaN(numeric)) return '-';
    if (!symbol) return formatCurrency(numeric);
    return `${symbol}${numeric.toFixed(2)}`;
}

function formatCurrencyWithCode(value, code) {
    if (value === null || value === undefined || value === '' || isNaN(value)) return '-';
    const numeric = parseFloat(value);
    if (isNaN(numeric)) return '-';
    const currencyCode = code ? String(code).trim() : '';
    const formatted = formatCurrency(numeric);
    return currencyCode ? `${formatted} ${currencyCode}` : formatted;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    try { return new Date(dateStr).toLocaleDateString(); }
    catch (e) { return dateStr; }
}

function getRecencyClass(dateValue) {
    if (!dateValue || dateValue === '-') return null;
    const parsed = dateValue instanceof Date ? dateValue : new Date(dateValue);
    if (Number.isNaN(parsed.getTime())) return null;

    const now = new Date();
    const diffMs = now - parsed;
    const diffDays = diffMs / (1000 * 60 * 60 * 24);

    if (diffDays <= 7) {
        return 'recent-week';
    }
    if (diffDays <= 30) {
        return 'recent-month';
    }
    return null;
}

function formatDateIndicator(dateValue, options = {}) {
    if (!dateValue || dateValue === '-') {
        return options.empty || '-';
    }

    const parsed = dateValue instanceof Date ? dateValue : new Date(dateValue);
    if (Number.isNaN(parsed.getTime())) {
        const display = options.display || formatDate(dateValue);
        return `<small class="text-muted">${display}</small>`;
    }

    const display = options.display || formatDate(parsed);
    const recencyClass = getRecencyClass(parsed);
    const dotClass = recencyClass ? `date-dot ${recencyClass}` : 'date-dot';
    const iconHtml = options.icon ? `<i class="${options.icon}"></i> ` : '';
    const className = options.className || '';
    const dateAttr = parsed.toISOString().slice(0, 10);

    return `<small class="text-muted date-indicator${className}" data-date="${dateAttr}"><span class="${dotClass}"></span>${iconHtml}${display}</small>`;
}

function escapeHtml(text) {
    if (!text) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
    return text.toString().replace(/[&<>"']/g, m => map[m]);
}

function buildStatusDisplay(part) {
    const quotedPrice = part.line_quote_price;
    const supplierQuoteCount = part.line_supplier_quote_count || 0;
    const supplierQuoteBadge = supplierQuoteCount > 0
        ? `<div><small class="text-muted">Supplier quotes: ${supplierQuoteCount}</small></div>`
        : '';

    if (quotedPrice !== null && quotedPrice !== undefined && quotedPrice !== '') {
        const priceDisplay = formatCurrencyWithSymbol(quotedPrice, part.line_quote_currency_symbol);
        const supplierDisplay = part.line_quote_supplier_name
            ? `<div><small class="text-muted">${escapeHtml(part.line_quote_supplier_name)}</small></div>`
            : '';
        return `
            <div>
                <div style="display: flex; align-items: center; gap: 0.4rem;">
                    <span class="badge bg-success">Quoted</span>
                    <span style="font-weight: 600; color: #0d6efd;">${priceDisplay}</span>
                </div>
                ${supplierDisplay}
                ${supplierQuoteBadge}
            </div>
        `;
    }

    const chosenCost = part.chosen_cost;
    if (chosenCost !== null && chosenCost !== undefined && chosenCost !== '') {
        const costDisplay = formatCurrencyWithSymbol(chosenCost, part.chosen_currency_symbol);
        const costSource = part.chosen_supplier_name ? escapeHtml(part.chosen_supplier_name) : 'Manual';
        const supplierDisplay = `<div><small class="text-muted">${costSource}</small></div>`;
        return `
            <div>
                <div style="display: flex; align-items: center; gap: 0.4rem;">
                    <span class="badge bg-primary">Costed</span>
                    <span style="font-weight: 600; color: #0d6efd;">${costDisplay}</span>
                </div>
                ${supplierDisplay}
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

    const contactedNames = part.line_contacted_suppliers || [];
    const contactedCount = part.line_contacted_suppliers_count || contactedNames.length || 0;
    if (contactedCount > 0) {
        const namesHtml = contactedNames.map(name => (
            `<div><small class="text-muted">${escapeHtml(name)}</small></div>`
        )).join('');
        const moreCount = contactedCount - contactedNames.length;
        const moreHtml = moreCount > 0
            ? `<div><small class="text-muted">+${moreCount} more</small></div>`
            : '';
        return `
            <div>
                <div><span class="badge bg-warning text-dark">Contacted</span></div>
                ${namesHtml}
                ${moreHtml}
            </div>
        `;
    }

    return '';
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

function copyTextToClipboard(text) {
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

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

function showPartDetailsModal(part, filterSection = null) {
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

    const showStock = !filterSection || filterSection === 'stock';
    const showPO = !filterSection || filterSection === 'po';
    const showVQ = !filterSection || filterSection === 'vq';
    const showBOM = !filterSection || filterSection === 'bom';
    const showCQ = !filterSection || filterSection === 'cq';
    const showSO = !filterSection || filterSection === 'so';

    if (showStock && part.stock_movement_count > 0 && part.stock_details && part.stock_details.length > 0) {
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

    if (showPO && part.po_count > 0 && part.po_details && part.po_details.length > 0) {
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

      const showPLQuotes = !filterSection || filterSection === 'pl_quotes';

    if (showPLQuotes && part.parts_list_quotes_count > 0 && part.parts_list_quotes_details && part.parts_list_quotes_details.length > 0) {
        const quotesToShow = part.parts_list_quotes_details.slice(0, 5);
        const priceRange = getPriceRange(part.parts_list_quotes_details, 'unit_price');
        const uniqueSuppliers = [...new Set(part.parts_list_quotes_details.map(q => q.supplier_name))];

        detailsHtml += `
            <div class="modal-section purchasing">
                <div class="modal-section-header">
                    <i class="bi bi-list-check" style="color: #0d6efd; font-size: 1rem;"></i>
                    <span>Parts List Supplier Quotes</span>
                    <span class="modal-section-badge" style="background: #0d6efd; color: white;">${part.parts_list_quotes_details.length} quote${part.parts_list_quotes_details.length !== 1 ? 's' : ''}</span>
                    <span class="modal-section-badge" style="background: #6610f2; color: white;">
                        <i class="bi bi-building me-1"></i>${uniqueSuppliers.length} supplier${uniqueSuppliers.length !== 1 ? 's' : ''}
                    </span>
                    ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                        <i class="bi bi-cash-stack me-1"></i>${formatCurrency(priceRange.min)} - ${formatCurrency(priceRange.max)}
                    </span>` : ''}
                    ${part.parts_list_quotes_details.length > 5 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.parts_list_quotes_details.length - 5} more</small>` : ''}
                </div>
                <div class="table-responsive">
                    <table class="table table-sm modal-table mb-0">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Quote Ref</th>
                                <th>Supplier</th>
                                <th>Qty</th>
                                <th>Price</th>
                                <th>Lead Time</th>
                                <th>Condition</th>
                                <th>Parts List</th>
                                ${showActions ? '<th style="width: 180px;">Actions</th>' : ''}
                            </tr>
                        </thead>
                        <tbody>
                            ${quotesToShow.map(quote => `
                                <tr ${quote.is_no_bid ? 'class="table-warning"' : ''}>
                                    <td><strong>${formatDate(quote.quote_date)}</strong></td>
                                    <td>${escapeHtml(quote.quote_reference || '-')}</td>
                                    <td>${escapeHtml(quote.supplier_name)}</td>
                                    <td>${quote.quantity_quoted || '-'}</td>
                                    <td style="font-weight: 600; color: ${quote.is_no_bid ? '#856404' : '#0d6efd'};">
                                        ${quote.is_no_bid ? 'No Bid' : (formatCurrency(quote.unit_price) + ' ' + escapeHtml(quote.currency_code || ''))}
                                    </td>
                                    <td>${quote.lead_time_days ? `<span class="badge" style="background: #6610f2; color: white;">${quote.lead_time_days} days</span>` : '-'}</td>
                                    <td>${quote.condition_code ? `<span class="badge bg-info">${escapeHtml(quote.condition_code)}</span>` : '-'}</td>
                                    <td>
                                        <a href="/parts_list/parts-lists/${quote.parts_list_id}/supplier-quotes/${quote.quote_id}"
                                           target="_blank"
                                           class="btn btn-sm btn-outline-primary"
                                           title="Open quote in new tab">
                                            <i class="bi bi-box-arrow-up-right"></i>
                                            ${escapeHtml(quote.parts_list_name)}
                                        </a>
                                    </td>
                                    ${showActions && !quote.is_no_bid ? `
                                    <td>
                                        <div class="btn-group btn-group-sm">
                                            <button class="btn btn-outline-primary add-supplier-btn"
                                                    data-supplier-id="${quote.supplier_id}"
                                                    data-supplier-name="${escapeHtml(quote.supplier_name)}"
                                                    data-source-type="pl_quote"
                                                    title="Add to suggested suppliers">
                                                <i class="bi bi-plus-circle"></i>
                                            </button>
                                            <button class="btn btn-success use-cost-btn"
                                                    data-supplier-id="${quote.supplier_id}"
                                                    data-cost="${quote.unit_price}"
                                                    data-currency="${quote.currency_id || 3}"
                                                    data-lead-days="${quote.lead_time_days || ''}"
                                                    data-source-type="pl_quote"
                                                    data-source-ref="${quote.quote_reference}">
                                                <i class="bi bi-check-circle"></i> Use Cost
                                            </button>
                                        </div>
                                    </td>` : showActions ? '<td></td>' : ''}
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }

    if (showVQ && part.vq_count > 0 && part.vq_details && part.vq_details.length > 0) {
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

    if (showBOM && part.bom_usage_count > 0 && part.bom_details && part.bom_details.length > 0) {
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

   // Find this section (around line 740-795) and replace it with:
if (showCQ && part.cq_count > 0 && part.cq_details && part.cq_details.length > 0) {
    const cqsToShow = part.cq_details.slice(0, 5); // Show more initially
    const priceRange = getPriceRange(part.cq_details, 'unit_price');
    const uniqueCustomers = [...new Set(part.cq_details.map(cq => cq.customer_name))];

    detailsHtml += `
        <div class="modal-section sales">
            <div class="modal-section-header">
                <i class="bi bi-file-earmark-text" style="color: #198754; font-size: 1rem;"></i>
                <span>Customer Quotes</span>
                <span class="modal-section-badge" style="background: #198754; color: white;">${part.cq_details.length} quote${part.cq_details.length !== 1 ? 's' : ''}</span>
                ${part.cq_from_cqs > 0 ? `<span class="modal-section-badge" style="background: #6c757d; color: white;">CQ: ${part.cq_from_cqs}</span>` : ''}
                ${part.cq_from_parts_lists > 0 ? `<span class="modal-section-badge" style="background: #0d6efd; color: white;">PL: ${part.cq_from_parts_lists}</span>` : ''}
                <span class="modal-section-badge" style="background: #20c997; color: white;">
                    <i class="bi bi-people-fill me-1"></i>${uniqueCustomers.length} customer${uniqueCustomers.length !== 1 ? 's' : ''}
                </span>
                ${priceRange ? `<span class="modal-section-badge" style="background: #0dcaf0; color: white;">
                    Avg: ${formatCurrency(priceRange.avg)}
                </span>` : ''}
                ${part.cq_details.length > 5 ? `<small style="color: #6c757d; font-weight: normal; margin-left: 0.5rem;">+${part.cq_details.length - 5} more</small>` : ''}
            </div>
            <div class="table-responsive">
                <table class="table table-sm modal-table mb-0">
                    <thead>
                        <tr>
                            <th>Date</th>
                            <th>Reference</th>
                            <th>Type</th>
                            <th>Customer</th>
                            <th>Status</th>
                            <th>Qty Req / Quoted</th>
                            <th>Unit Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${cqsToShow.map(cq => `
                            <tr>
                                <td><strong>${formatDate(cq.quote_date)}</strong></td>
                                <td><strong>${escapeHtml(cq.reference)}</strong></td>
                                <td>
                                    <span class="badge ${cq.quote_type === 'cq' ? 'bg-secondary' : 'bg-primary'}" style="font-size: 0.7rem;">
                                        ${cq.quote_type === 'cq' ? 'CQ' : 'PL'}
                                    </span>
                                </td>
                                <td>${escapeHtml(cq.customer_name)}</td>
                                <td><span class="badge bg-info">${escapeHtml(cq.status)}</span></td>
                                <td>${cq.quantity_requested || '-'} / <strong>${cq.quantity_quoted || '-'}</strong></td>
                                <td style="font-weight: 600; color: ${cq.is_no_quote ? '#856404' : '#198754'};">
                                    ${cq.is_no_quote ? 'No Quote' : (formatCurrency(cq.unit_price) + ' ' + escapeHtml(cq.currency_code || ''))}
                                </td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

    if (showSO && part.so_count > 0 && part.so_details && part.so_details.length > 0) {
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

    if (showActions && lineId) {
        modalContent.querySelectorAll('.add-supplier-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const supplierId = this.getAttribute('data-supplier-id');
                const supplierName = this.getAttribute('data-supplier-name');
                const sourceType = this.getAttribute('data-source-type');
                addSuggestedSupplier(lineId, supplierId, supplierName, sourceType, this);
            });
        });

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

function showAlternativesModal(partIndex) {
    const part = window.allResults[partIndex];
    console.log('Part data:', part);
    console.log('Global alternatives:', part.global_alternatives);

    const alts = part.global_alternatives || [];

    const modalTitle = document.getElementById('partDetailsModalLabel');
    const modalContent = document.getElementById('modalDetailsContent');

    modalTitle.innerHTML = `
        <i class="bi bi-arrow-left-right" style="color: #0dcaf0; font-size: 1.2rem;"></i>
        Available Alternatives: ${escapeHtml(part.input_part_number)}
    `;

    if (alts.length === 0) {
        modalContent.innerHTML = `
            <div class="alert alert-info">
                <i class="bi bi-info-circle me-2"></i>
                No alternatives available for this part.
            </div>
        `;
    } else {
        const altsInStock = alts.filter(alt => alt.has_stock);
        const altsNoStock = alts.filter(alt => !alt.has_stock);

        let contentHtml = '';

        if (altsInStock.length > 0) {
            contentHtml += `
                <div class="modal-section mb-3">
                    <div class="modal-section-header" style="background: #d1e7dd; border-color: #0f5132;">
                        <i class="bi bi-box-seam-fill" style="color: #0f5132;"></i>
                        <span style="color: #0f5132;">In Stock</span>
                        <span class="modal-section-badge" style="background: #0f5132; color: white;">
                            ${altsInStock.length} alternative${altsInStock.length !== 1 ? 's' : ''}
                        </span>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-sm modal-table mb-0">
                            <thead>
                                <tr>
                                    <th>Part Number</th>
                                    <th>Stock Available</th>
                                    <th style="width: 120px;">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${altsInStock.map(alt => `
                                    <tr>
                                        <td><strong>${escapeHtml(alt.input_part_number)}</strong></td>
                                        <td>
                                            <span class="badge bg-success">
                                                <i class="bi bi-box-seam"></i> ${alt.total_available_stock}
                                            </span>
                                        </td>
                                        <td>
                                            <button class="btn btn-sm btn-primary add-alt-btn"
                                                    data-base-part="${escapeHtml(alt.base_part_number)}"
                                                    data-part-number="${escapeHtml(alt.input_part_number)}"
                                                    data-parent-index="${partIndex}">
                                                <i class="bi bi-plus-circle"></i> Add to List
                                            </button>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        if (altsNoStock.length > 0) {
            contentHtml += `
                <div class="modal-section">
                    <div class="modal-section-header" style="background: #fff3cd; border-color: #856404;">
                        <i class="bi bi-exclamation-triangle-fill" style="color: #856404;"></i>
                        <span style="color: #856404;">No Stock Available</span>
                        <span class="modal-section-badge" style="background: #856404; color: white;">
                            ${altsNoStock.length} alternative${altsNoStock.length !== 1 ? 's' : ''}
                        </span>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-sm modal-table mb-0">
                            <thead>
                                <tr>
                                    <th>Part Number</th>
                                    <th>Stock Available</th>
                                    <th style="width: 120px;">Action</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${altsNoStock.map(alt => `
                                    <tr>
                                        <td><strong>${escapeHtml(alt.input_part_number)}</strong></td>
                                        <td>
                                            <span class="badge bg-secondary">
                                                <i class="bi bi-x-circle"></i> 0
                                            </span>
                                        </td>
                                        <td>
                                            <button class="btn btn-sm btn-outline-primary add-alt-btn"
                                                    data-base-part="${escapeHtml(alt.base_part_number)}"
                                                    data-part-number="${escapeHtml(alt.input_part_number)}"
                                                    data-parent-index="${partIndex}">
                                                <i class="bi bi-plus-circle"></i> Add to List
                                            </button>
                                        </td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        modalContent.innerHTML = contentHtml;

        modalContent.querySelectorAll('.add-alt-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const basePartNumber = this.getAttribute('data-base-part');
                const partNumber = this.getAttribute('data-part-number');
                const parentIndex = parseInt(this.getAttribute('data-parent-index'));
                addAlternativeToList(basePartNumber, partNumber, parentIndex, this);
            });
        });
    }

    const modal = new bootstrap.Modal(document.getElementById('partDetailsModal'));
    modal.show();
}

function addAlternativeToList(basePartNumber, partNumber, parentIndex, buttonElement) {
    const originalHtml = buttonElement.innerHTML;
    buttonElement.disabled = true;
    buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';

    const parentPart = window.allResults[parentIndex];

    if (currentListId) {
        fetch(`/parts_list/parts-lists/${currentListId}/lines/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                lines: [{
                    customer_part_number: partNumber,
                    base_part_number: basePartNumber,
                    quantity: parentPart.quantity || 1,
                    parent_line_id: parentPart.line_id,
                    parent_line_number: Math.floor(parentPart.line_number || 1),
                    line_type: 'alternate'
                }]
            })
        })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Failed to add line to database');
            }

            return fetch('/parts_list/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    parts: [{
                        part_number: partNumber,
                        quantity: parentPart.quantity || 1
                    }]
                })
            });
        })
        .then(response => response.json())
        .then(data => {
            if (data.success && data.results.length > 0) {
                const analyzedPart = data.results[0];

                analyzedPart.is_global_alternative = true;
                analyzedPart.parent_base_part_number = parentPart.input_part_number;
                analyzedPart.line_type = 'alternate';
                analyzedPart.parent_line_id = parentPart.line_id;

                window.allResults.splice(parentIndex + 1, 0, analyzedPart);

                displayResults(window.allResults);

                buttonElement.innerHTML = '<i class="bi bi-check-circle-fill"></i> Added!';
                buttonElement.classList.remove('btn-primary', 'btn-outline-primary');
                buttonElement.classList.add('btn-success');

                showToast(`Added ${partNumber} to list`, 'success');

                const modal = bootstrap.Modal.getInstance(document.getElementById('partDetailsModal'));
                if (modal) modal.hide();
            } else {
                throw new Error('Failed to analyze new part');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            buttonElement.innerHTML = originalHtml;
            buttonElement.disabled = false;
            alert('Error adding alternative: ' + error.message);
        });

    } else {
        const newAltPart = {
            input_part_number: partNumber,
            base_part_number: basePartNumber,
            quantity: parentPart.quantity || 1,
            line_type: 'alternate',
            parent_line_id: parentPart.line_id,
            is_global_alternative: true,
            parent_base_part_number: parentPart.input_part_number,
            line_id: null
        };

        window.allResults.splice(parentIndex + 1, 0, newAltPart);

        const partsToAnalyze = [{
            part_number: partNumber,
            quantity: newAltPart.quantity
        }];

        fetch('/parts_list/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ parts: partsToAnalyze })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success && data.results.length > 0) {
                const analyzedPart = data.results[0];
                analyzedPart.is_global_alternative = true;
                analyzedPart.parent_base_part_number = parentPart.input_part_number;
                analyzedPart.line_type = 'alternate';
                analyzedPart.parent_line_id = parentPart.line_id;

                window.allResults[parentIndex + 1] = analyzedPart;
                displayResults(window.allResults);

                buttonElement.innerHTML = '<i class="bi bi-check-circle-fill"></i> Added!';
                buttonElement.classList.remove('btn-primary', 'btn-outline-primary');
                buttonElement.classList.add('btn-success');

                showToast(`Added ${partNumber} as alternative`, 'success');

                const modal = bootstrap.Modal.getInstance(document.getElementById('partDetailsModal'));
                if (modal) modal.hide();
            } else {
                buttonElement.innerHTML = originalHtml;
                buttonElement.disabled = false;
                alert('Error analyzing alternative part');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            buttonElement.innerHTML = originalHtml;
            buttonElement.disabled = false;
            alert('Error adding alternative: ' + error.message);
        });
    }
}

function duplicateLineForPriceBreak(partIndex, buttonElement) {
    const originalHtml = buttonElement ? buttonElement.innerHTML : '';
    if (buttonElement) {
        buttonElement.disabled = true;
        buttonElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
    }

    const parentPart = window.allResults[partIndex];
    if (!parentPart || !parentPart.line_id) {
        if (buttonElement) {
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalHtml;
        }
        alert('This line must be saved before adding a price break.');
        return;
    }

    if (!currentListId) {
        if (buttonElement) {
            buttonElement.disabled = false;
            buttonElement.innerHTML = originalHtml;
        }
        alert('Please save the parts list before adding price breaks.');
        return;
    }

    let duplicateInfo = null;
    fetch(`/parts_list/parts-lists/${currentListId}/lines/${parentPart.line_id}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line_type: 'price_break' })
    })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                throw new Error(data.message || 'Failed to duplicate line');
            }
            duplicateInfo = data;
            return fetch('/parts_list/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    parts: [{
                        part_number: parentPart.input_part_number || parentPart.customer_part_number || parentPart.base_part_number,
                        quantity: parentPart.quantity || 1,
                        line_id: data.line_id,
                        line_number: Number(data.line_number)
                    }]
                })
            });
        })
        .then(response => response.json())
        .then(data => {
            if (!data.success || !data.results || data.results.length === 0) {
                throw new Error('Failed to analyze new line');
            }

            const analyzedPart = data.results[0];
            analyzedPart.line_type = duplicateInfo.line_type || 'price_break';
            analyzedPart.parent_line_id = duplicateInfo.parent_line_id || parentPart.line_id;
            if (duplicateInfo.line_number !== undefined && duplicateInfo.line_number !== null) {
                const numericLineNumber = Number(duplicateInfo.line_number);
                analyzedPart.line_number = Number.isFinite(numericLineNumber) ? numericLineNumber : duplicateInfo.line_number;
            }

            const parentLineId = analyzedPart.parent_line_id;
            const parentIndex = window.allResults.findIndex(p => p.line_id === parentLineId);
            let insertIndex = parentIndex >= 0 ? parentIndex + 1 : partIndex + 1;
            while (insertIndex < window.allResults.length &&
                   window.allResults[insertIndex].parent_line_id === parentLineId) {
                insertIndex += 1;
            }

            window.allResults.splice(insertIndex, 0, analyzedPart);
            displayResults(window.allResults);

            if (buttonElement) {
                buttonElement.disabled = false;
                buttonElement.innerHTML = originalHtml;
            }
            showToast('Added price break line', 'success');
        })
        .catch(error => {
            console.error('Error:', error);
            if (buttonElement) {
                buttonElement.disabled = false;
                buttonElement.innerHTML = originalHtml;
            }
            alert('Error adding price break: ' + error.message);
        });
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

    const renderIlsRow = (ils) => {
        const partNumber = ils.part_number || part.input_part_number || '-';
        const altNumber = ils.alt_part_number || '';
        const showAlt = altNumber && altNumber !== partNumber;
        const altBadge = showAlt ? '<span class="badge bg-warning text-dark ms-1">ALT</span>' : '';
        const altLine = showAlt ? `<br><small class="text-muted">Alt: ${escapeHtml(altNumber)}</small>` : '';

        return `
    <tr>
        <td>
            <strong>${escapeHtml(ils.ils_company_name)}</strong>
            ${ils.ils_cage_code ? `<br><small class="text-muted">CAGE: ${escapeHtml(ils.ils_cage_code)}</small>` : ''}
        </td>
        <td>
            <strong>${escapeHtml(partNumber)}</strong>${altBadge}
            ${altLine}
        </td>
        <td><strong>${ils.search_date ? formatDate(ils.search_date) : '-'}</strong></td>
        <td><span class="badge bg-secondary">${escapeHtml(ils.quantity)}</span></td>
        <td><span class="badge bg-info">${escapeHtml(ils.condition_code)}</span></td>
        <td><small>${escapeHtml(ils.description || '-')}</small></td>
        <td><small>${ils.email ? `<a href="mailto:${escapeHtml(ils.email)}">${escapeHtml(ils.email)}</a>` : '-'}</small></td>
        <td>
            <button class="btn btn-sm btn-success quick-add-from-ils-btn"
                    data-company-name="${escapeHtml(ils.ils_company_name)}"
                    data-email="${escapeHtml(ils.email || '')}"
                    title="Create supplier from this company">
                <i class="bi bi-building-add"></i>
            </button>
        </td>
    </tr>
`;
    };

    const renderSupplierScorePill = (scoreInfo) => {
        if (!scoreInfo || scoreInfo.score === null || scoreInfo.score === undefined) {
            return '<span class="text-muted">No history</span>';
        }

        let badgeClass = 'bg-danger';
        if (scoreInfo.score >= 85) {
            badgeClass = 'bg-success';
        } else if (scoreInfo.score >= 70) {
            badgeClass = 'bg-primary';
        } else if (scoreInfo.score >= 50) {
            badgeClass = 'bg-warning text-dark';
        }

        const noBidRate = scoreInfo.no_bid_rate !== null && scoreInfo.no_bid_rate !== undefined
            ? Math.round(scoreInfo.no_bid_rate * 100)
            : null;
        const tooltip = `Requests: ${scoreInfo.requests_sent} · No-bid: ${scoreInfo.no_bid_count}` +
            (noBidRate !== null ? ` (${noBidRate}%)` : '');

        return `
            <span class="badge ${badgeClass}" title="${tooltip}">${scoreInfo.score}</span>
            <small class="text-muted ms-1">${scoreInfo.rating}</small>
        `;
    };

    const fetchSupplierScores = (supplierIds) => {
        if (!supplierIds.length) {
            return;
        }

        fetch('/parts_list/api/suppliers/no-bid-score', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                supplier_ids: supplierIds
            })
        })
        .then(response => response.json())
        .then(data => {
            if (!data.success) {
                return;
            }

            const scores = data.scores || {};
            modalContent.querySelectorAll('.supplier-score-pill').forEach(pill => {
                const supplierId = pill.dataset.supplierId;
                const scoreInfo = scores[supplierId];
                pill.innerHTML = renderSupplierScorePill(scoreInfo);
            });
        })
        .catch(() => {
            modalContent.querySelectorAll('.supplier-score-pill').forEach(pill => {
                pill.innerHTML = '<span class="text-muted">No history</span>';
            });
        });
    };

    let contentHtml = '';

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

            const hasSupplierId = showActions && rows[0] && rows[0].supplier_id;
            const supplierId = rows[0] && rows[0].supplier_id;

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
                                        ${supplierId ? `<span class="supplier-score-pill" data-supplier-id="${supplierId}"><span class="text-muted">Loading...</span></span>` : ''}
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
                                                <th>Part / Alt</th>
                                                <th>Date</th>
                                                <th>Qty</th>
                                                <th>Condition</th>
                                                <th>Description</th>
                                                <th>Email</th>
                                                <th style="width: 80px;">Action</th>
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
                    <div class="ms-auto d-flex gap-2">
                        <button class="btn btn-sm btn-success" onclick="openQuickAddSupplier()" title="Add new supplier to database">
                            <i class="bi bi-building-add"></i> Add Supplier
                        </button>
                        <a href="/ils/supplier-mapping" target="_blank" rel="noopener"
                           class="btn btn-sm btn-warning" style="font-size: 0.8rem;">
                            <i class="bi bi-diagram-2"></i> Map Suppliers
                        </a>
                    </div>
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
                                                <th>Part / Alt</th>
                                                <th>Date</th>
                                                <th>Qty</th>
                                                <th>Condition</th>
                                                <th>Description</th>
                                                <th>Email</th>
                                                <th style="width: 80px;">Action</th>
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

    modalContent.innerHTML = contentHtml;

    const supplierIds = Object.values(supplierGroups)
        .map(rows => rows[0] && rows[0].supplier_id)
        .filter(Boolean);

    fetchSupplierScores([...new Set(supplierIds)]);

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

    modalContent.querySelectorAll('.quick-add-from-ils-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const companyName = this.getAttribute('data-company-name');
            const email = this.getAttribute('data-email');
            openQuickAddSupplier(companyName, email);
        });
    });

    const modal = new bootstrap.Modal(document.getElementById('ilsDetailsModal'));
    modal.show();
}

// ==================== QUICK ADD SUPPLIER - GLOBAL EVENT DELEGATION ====================
document.addEventListener('click', function(e) {
    const button = e.target.closest('#quickAddSupplierBtn');
    if (button) {
        e.preventDefault();
        e.stopPropagation();

        console.log('Quick add supplier button clicked via delegation');

        const form = document.getElementById('quickAddSupplierForm');
        if (!form) {
            console.error('Form not found - quickAddSupplierForm');
            alert('Form not found - please refresh the page');
            return;
        }

        if (!form.checkValidity()) {
            console.log('Form validation failed');
            form.reportValidity();
            return;
        }

        const formData = new FormData(form);
        const data = {};
        formData.forEach((value, key) => {
            data[key] = value;
        });

        console.log('Sending supplier data:', data);

        const originalHtml = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Creating...';

        fetch('/suppliers/create', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(data)
        })
        .then(response => {
            console.log('Response status:', response.status);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            console.log('Response data:', data);

            if (data.success) {
                const modalElement = document.getElementById('quickAddSupplierModal');
                const modal = bootstrap.Modal.getInstance(modalElement);
                if (modal) modal.hide();

                showToast(`Supplier "${data.supplier_name}" created successfully!`, 'success');

                form.reset();
                button.disabled = false;
                button.innerHTML = originalHtml;

                setTimeout(() => {
                    if (confirm(`Supplier created! Would you like to open the Supplier Mapping page to map ILS companies to "${data.supplier_name}"?`)) {
                        window.open('/ils/supplier-mapping', '_blank');
                    }
                }, 500);
            } else {
                alert(`Error: ${data.error || 'Unknown error'}`);
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('An error occurred while creating the supplier: ' + error.message);
            button.disabled = false;
            button.innerHTML = originalHtml;
        });
    }
});

// ==================== MAIN INITIALIZATION ====================
document.addEventListener('DOMContentLoaded', function() {
    const partsInput = document.getElementById('parts-input');
    const partsCount = document.getElementById('parts-count');
    const extractAiBtn = document.getElementById('extract-ai-btn');
    const loadingSpinner = document.getElementById('loading-spinner');
    const loadingMessage = document.getElementById('loading-message');

    if (window.LOADED_LIST_DATA && window.LOADED_LIST_DATA.header) {
        currentListId = window.LOADED_LIST_DATA.header.id;
    }

    const partsTableBody = document.getElementById('parts-table-body');
    if (partsTableBody) {
        partsTableBody.addEventListener('click', function(event) {
            const copyButton = event.target.closest('.copy-part-number-btn');
            if (copyButton) {
                const encodedPartNumber = copyButton.getAttribute('data-part-number') || '';
                const partNumber = decodeURIComponent(encodedPartNumber);
                copyTextToClipboard(partNumber);
                return;
            }

            const button = event.target.closest('.duplicate-line-btn');
            if (!button) return;
            const partIndex = parseInt(button.getAttribute('data-part-index'), 10);
            if (Number.isNaN(partIndex)) return;
            duplicateLineForPriceBreak(partIndex, button);
        });
    }

    if (!partsInput && window.LOADED_LIST_DATA && window.LOADED_LIST_DATA.lines) {
        console.log('View page detected - auto-loading parts list');
        setTimeout(function() {
            const lines = window.LOADED_LIST_DATA.lines;
            if (lines.length > VIEW_ANALYSIS_AUTO_LIMIT) {
                displayResults(buildBasicResultsFromLines(lines));
                showDeferredAnalysisBanner(lines.length, () => analyzePartsWithLineIds(lines));
            } else {
                analyzePartsWithLineIds(lines);
            }
        }, 100);
        return;
    }

    const contact_id = selectedContact ? Number(selectedContact.id) : null;
    const emailDropzone = document.getElementById('email-dropzone');
    const emailFileInput = document.getElementById('email-file-input');
    const emailContainer = document.getElementById('email-upload-container');

    console.log('Email dropzone element:', emailDropzone);

    if (emailDropzone && emailFileInput) {
        console.log('Setting up email dropzone handlers...');

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            emailDropzone.addEventListener(eventName, preventDefaults, false);
            document.body.addEventListener(eventName, preventDefaults, false);
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            emailDropzone.addEventListener(eventName, () => {
                console.log('Drag over email dropzone');
                emailContainer.classList.add('drag-over');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            emailDropzone.addEventListener(eventName, () => {
                emailContainer.classList.remove('drag-over');
            }, false);
        });

        emailDropzone.addEventListener('drop', function(e) {
            console.log('Email file dropped!');
            const dt = e.dataTransfer;
            const files = dt.files;

            if (files.length > 0) {
                handleEmailFiles(files);
            } else {
                console.log('No files in drop');
                alert('No file detected. Please save the email as .eml or .msg first.');
            }
        }, false);

        emailDropzone.addEventListener('click', () => {
            console.log('Email dropzone clicked');
            emailFileInput.click();
        });

        emailFileInput.addEventListener('change', () => {
            console.log('File input changed');
            handleEmailFiles(emailFileInput.files);
        });

        console.log('Email dropzone setup complete');
    } else {
        console.error('Email dropzone elements not found!');
    }

    function handleEmailFiles(files) {
        console.log('handleEmailFiles called with:', files);

        if (files.length === 0) {
            console.log('No files provided');
            return;
        }

        const file = files[0];
        console.log('Processing file:', file.name);

        if (!file.name.endsWith('.eml') && !file.name.endsWith('.msg')) {
            alert('Please upload a .eml or .msg file.');
            return;
        }

        const formData = new FormData();
        formData.append('file', file);

        loadingSpinner.style.display = 'flex';
        loadingMessage.textContent = 'Parsing email...';

        console.log('Uploading to /parse-email...');

        fetch('/parts_list/parse-email', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            console.log('Response status:', response.status);
            return response.json();
        })
        .then(data => {
            console.log('Response data:', data);
            loadingSpinner.style.display = 'none';

            if (data.success) {
                if (data.subject) {
                    document.getElementById('list-name-input').value = data.subject;
                }

                if (data.customer_id && data.customer_name) {
                    selectCustomer(data.customer_id.toString(), data.customer_name);
                    showToast(`Customer matched: ${data.customer_name}`, 'success');
                } else if (data.sender) {
                    showToast(`No customer found for ${data.sender}`, 'warning');
                }

                if (data.contact_id && data.contact_name) {
                    selectedContact = {
                        id: data.contact_id,
                        full_name: data.contact_name,
                        email: data.sender,
                        customer_id: data.customer_id,
                        customer_name: data.customer_name
                    };
                    updateSelectedContactDisplay();
                    showToast(`Contact matched: ${data.contact_name}`, 'success');
                }

                if (data.parts && data.parts.length > 0) {
                    const partsText = data.parts.map(part => {
                        if (part.quantity && part.quantity !== 1) {
                            return `${part.customer_part_number}, ${part.quantity}`;
                        }
                        return part.customer_part_number;
                    }).join('\n');

                    partsInput.value = partsText;
                    partsCount.textContent = `(${data.parts.length} part${data.parts.length !== 1 ? 's' : ''})`;

                    showToast(`Extracted ${data.parts.length} parts from email`, 'success');

                    document.getElementById('parts-input').scrollIntoView({
                        behavior: 'smooth',
                        block: 'center'
                    });
                }
            } else {
                console.error('Parse failed:', data.message);
                alert('Error: ' + data.message);
            }
        })
        .catch(error => {
            loadingSpinner.style.display = 'none';
            console.error('Upload error:', error);
            alert('Upload failed: ' + error);
        });
    }

    if (window.LOADED_LIST_DATA && window.LOADED_LIST_DATA.lines) {
        const loadedList = window.LOADED_LIST_DATA;
        currentListId = loadedList.header.id;

        document.getElementById('list-name-input').value = loadedList.header.name || '';

        const partsText = loadedList.lines.map(line => {
            if (line.quantity && line.quantity !== 1) {
                return `${line.customer_part_number}, ${line.quantity}`;
            }
            return line.customer_part_number;
        }).join('\n');

        partsInput.value = partsText;
        partsCount.textContent = `(${loadedList.lines.length} part${loadedList.lines.length !== 1 ? 's' : ''})`;

        if (loadedList.header.customer_id && loadedList.header.customer_name) {
            selectCustomer(loadedList.header.customer_id.toString(), loadedList.header.customer_name);
        }

        if (loadedList.header.contact_id) {
            selectedContact = {
                id: loadedList.header.contact_id,
                full_name: loadedList.header.contact_name || 'Unknown Contact',
                customer_id: loadedList.header.customer_id,
                customer_name: loadedList.header.customer_name
            };
            updateSelectedContactDisplay();
        }

        setTimeout(function() {
            analyzePartsWithLineIds(loadedList.lines);
        }, 500);
    }

    const ilsUploadContainer = document.getElementById('ils-upload-container');
    const ilsDropzone = document.getElementById('ils-dropzone');
    const ilsFileInput = document.getElementById('ils-file-input');
    const ilsStatsDisplay = document.getElementById('ils-stats-display');

    if (ilsDropzone && ilsFileInput && ilsUploadContainer && ilsStatsDisplay) {
        ilsDropzone.addEventListener('click', () => ilsFileInput.click());
        ilsDropzone.addEventListener('dragover', (e) => { e.preventDefault(); ilsUploadContainer.classList.add('drag-over'); });
        ilsDropzone.addEventListener('dragleave', () => { ilsUploadContainer.classList.remove('drag-over'); });
        ilsDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            ilsUploadContainer.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.csv')) {
                uploadILSFile(file);
            } else {
                alert('Please upload a CSV file');
            }
        });
        ilsFileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) uploadILSFile(file);
        });

        function uploadILSFile(file) {
            loadingMessage.textContent = 'Uploading and parsing ILS data...';
            loadingSpinner.style.display = 'flex';

            const formData = new FormData();
            formData.append('file', file);

            fetch('/ils/upload', { method: 'POST', body: formData })
            .then(response => response.json())
            .then(data => {
                loadingSpinner.style.display = 'none';
                if (data.success) {
                    ilsStatsDisplay.innerHTML = `
                        <div class="alert alert-success mb-3">
                            <i class="bi bi-check-circle-fill me-2"></i>
                            ${data.message}
                            <a href="/ils/supplier-mapping" target="_blank" rel="noopener" class="btn btn-sm btn-outline-primary ms-2">
                                <i class="bi bi-diagram-2"></i> Open Supplier Mapping
                            </a>
                        </div>
                        <div class="row g-2">
                            <div class="col-md-4">
                                <div class="ils-stat-item">
                                    <span><i class="bi bi-file-earmark-text me-2"></i>Total Records:</span>
                                    <strong>${data.stats.total_records}</strong>
                                </div>
                            </div>
                            <div class="col-md-4">
                                <div class="ils-stat-item">
                                    <span><i class="bi bi-box-seam me-2"></i>Unique Parts:</span>
                                    <strong>${data.stats.unique_parts}</strong>
                                </div>
                            </div>
                            <div class="col-md-4">
                                <div class="ils-stat-item">
                                    <span><i class="bi bi-building me-2"></i>Suppliers:</span>
                                    <strong>${data.stats.unique_suppliers}</strong>
                                </div>
                            </div>
                        </div>
                    `;
                    ilsStatsDisplay.style.display = 'block';
                    ilsFileInput.value = '';
                } else {
                    alert('Error uploading ILS file: ' + data.error);
                }
            })
            .catch(error => {
                loadingSpinner.style.display = 'none';
                console.error('Error:', error);
                alert('Error uploading ILS file: ' + error.message);
            });
        }
    }

    if (document.getElementById('ils-upload-btn-compact')) {
        ILSUpload.init({
            buttonId: 'ils-upload-btn-compact',
            inputId: 'ils-file-input-compact',
            showStats: false,
            onSuccess: function(data) {
                showToast(data.message || 'ILS data uploaded successfully', 'success');
                if (window.allResults && window.allResults.length > 0) {
                    const partsData = window.allResults.map(r => ({
                        part_number: r.input_part_number,
                        quantity: r.quantity || 1,
                        line_id: r.line_id
                    }));
                    analyzePartsWithLineIds(partsData);
                }
            }
        });
    }

    const saveListBtn = document.getElementById('save-list-btn');
    if (saveListBtn) {
        saveListBtn.addEventListener('click', function () {
            if (!window.allResults || window.allResults.length === 0) {
                alert('Run a lookup first — no results to save.');
                return;
            }
            const nameInput = document.getElementById('list-name-input');
            const name = (nameInput.value || '').trim() || `Parts List ${new Date().toLocaleString()}`;

            const customer_id = selectedCustomer ? Number(selectedCustomer.id) : null;
            const contact_id = selectedContact ? Number(selectedContact.id) : null;

            const lines = window.allResults.map((r, idx) => ({
                line_number: idx + 1,
                customer_part_number: r.input_part_number,
                base_part_number: r.base_part_number || null,
                quantity: r.quantity || 1
            }));

            fetch('/parts_list/parts-lists/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, customer_id, contact_id, notes: '', lines })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    if (data.redirect) window.location.href = data.redirect;
                    else alert('Saved!');
                } else {
                    alert('Save failed: ' + (data.message || 'Unknown error'));
                }
            })
            .catch(err => {
                console.error(err);
                alert('Save failed: ' + err.message);
            });
        });
    }

    const customerSearchInput = document.getElementById('customer-search-input');
    const customerSearchResults = document.getElementById('customer-search-results');
    const selectedCustomerDisplay = document.getElementById('selected-customer-display');

    if (customerSearchInput && customerSearchResults && selectedCustomerDisplay) {
        let searchTimeout;
        customerSearchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            const query = this.value.trim();

            if (query.length < 2) {
                customerSearchResults.innerHTML = '';
                return;
            }

            searchTimeout = setTimeout(() => {
                fetch(`/customers/search?q=${encodeURIComponent(query)}&limit=20`)
                    .then(response => response.json())
                    .then(customers => {
                        if (customers.length === 0) {
                            customerSearchResults.innerHTML = '<div style="padding: 0.75rem 1rem; color: #6c757d;">No customers found</div>';
                            return;
                        }

                        let resultsHtml = `
                            <div class="customer-search-results-header">
                                ${customers.length} customer${customers.length !== 1 ? 's' : ''} found
                            </div>
                        `;

                        resultsHtml += customers.map(customer => `
                            <div class="customer-search-item" data-customer-id="${customer.id}" data-customer-name="${escapeHtml(customer.name)}">
                                <i class="bi bi-building" style="color: #6c757d; font-size: 1rem;"></i>
                                <div style="flex: 1;">
                                    <div style="font-weight: 500; color: #212529;">${escapeHtml(customer.name)}</div>
</div>
</div>
`).join('');
customerSearchResults.innerHTML = resultsHtml;

                    customerSearchResults.querySelectorAll('.customer-search-item').forEach(item => {
                        item.addEventListener('click', function() {
                            selectCustomer(
                                this.getAttribute('data-customer-id'),
                                this.getAttribute('data-customer-name')
                            );
                        });
                    });
                })
                .catch(error => {
                    console.error('Error searching customers:', error);
                    customerSearchResults.innerHTML = '<div style="padding: 0.75rem 1rem; color: #dc3545;">Error searching customers</div>';
                });
        }, 300);
    });

    function selectCustomer(customerId, customerName) {
        selectedCustomer = { id: customerId, name: customerName };
        updateSelectedCustomerDisplay();
        customerSearchInput.value = '';
        customerSearchResults.innerHTML = '';
    }

    document.addEventListener('click', function(e) {
        if (!customerSearchInput.contains(e.target) && !customerSearchResults.contains(e.target)) {
            customerSearchResults.innerHTML = '';
        }
    });
}

if (extractAiBtn) {
    extractAiBtn.addEventListener('click', function() {
        const textData = partsInput.value.trim();
        if (!textData) { alert('Please paste some text to extract part numbers from'); return; }

        loadingMessage.textContent = 'Extracting part numbers with AI...';
        loadingSpinner.style.display = 'flex';

        const formData = new FormData();
        formData.append('request_data', textData);

        fetch('/parts_list/extract_parts_data', { method: 'POST', body: formData })
        .then(response => response.json())
        .then(data => {
            loadingSpinner.style.display = 'none';
            const warnings = Array.isArray(data.warnings) && data.warnings.length
                ? `\n\nNotes:\n- ${data.warnings.join('\n- ')}`
                : '';
            if (data.success && data.parts && data.parts.length > 0) {
                const formattedParts = data.parts.map(part => {
                    if (part.quantity && part.quantity !== 1) return `${part.part_number}, ${part.quantity}`;
                    return part.part_number;
                }).join('\n');

                partsInput.value = formattedParts;
                partsCount.textContent = `(${data.parts.length} part${data.parts.length !== 1 ? 's' : ''})`;
                alert(`Successfully extracted ${data.parts.length} part number${data.parts.length !== 1 ? 's' : ''}!${warnings}`);
            } else {
                alert(`${data.error || 'No part numbers could be extracted from the text'}${warnings}`);
            }
        })
        .catch(error => {
            loadingSpinner.style.display = 'none';
            console.error('Error:', error);
            alert('Error extracting part numbers: ' + error.message);
        });
    });
}

if (partsInput) {
    partsInput.addEventListener('input', function() {
        const parts = parsePartNumbers(partsInput.value);
        partsCount.textContent = `(${parts.length} part${parts.length !== 1 ? 's' : ''})`;
    });
}

const clearBtn = document.getElementById('clear-btn');
if (clearBtn) {
    clearBtn.addEventListener('click', function() {
        if (confirm('Clear all data?')) {
            partsInput.value = '';
            partsCount.textContent = '(0 parts)';
            const resultsSection = document.getElementById('results-section');
            if (resultsSection) resultsSection.style.display = 'none';
        }
    });
}

const analyzeBtn = document.getElementById('analyze-btn');
if (analyzeBtn) {
    analyzeBtn.addEventListener('click', function() {
        const parts = parsePartNumbers(partsInput.value);
        if (parts.length === 0) { alert('Please enter at least one part number'); return; }
        analyzePartsWithLineIds(parts);
    });
}

function analyzePartsWithLineIds(partsArray) {
    loadingMessage.textContent = 'Looking up parts...';
    loadingSpinner.style.display = 'flex';

    const partsData = partsArray.map(part => {
        const item = {
            part_number: part.customer_part_number || part.part_number,
            quantity: part.quantity
        };
        if (part.id) {
            item.line_id = part.id;
        }
        if (part.line_number) {
            item.line_number = part.line_number;
        }
        return item;
    });

    const requestData = { parts: partsData };

    if (selectedCustomer) {
        requestData.customer_id = selectedCustomer.id;
    }

    return fetch('/parts_list/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestData)
    })
    .then(response => response.json())
    .then(data => {
        loadingSpinner.style.display = 'none';
        if (data.success) {
            displayResults(data.results);
            return data.results;
        } else {
            alert('Error: ' + (data.message || 'Unknown error occurred'));
            throw new Error(data.message);
        }
    })
    .catch(error => {
        loadingSpinner.style.display = 'none';
        console.error('Error:', error);
        alert('Error looking up parts: ' + error.message);
        throw error;
    });
}

const emailBtns = [
    document.getElementById('email-suppliers-btn'),
    document.getElementById('email-suppliers-btn-header')
];

emailBtns.forEach(btn => {
    if (btn) {
        btn.addEventListener('click', function(e) {
            e.preventDefault();

            if (!window.allResults || window.allResults.length === 0) {
                alert('No ILS results to email');
                return;
            }

            fetch('/parts_list/email-suppliers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    results: window.allResults,
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
                alert('Error navigating to email suppliers page');
            });
        });
    }
});

const viewAsTableBtn = document.getElementById('view-as-table-btn');
if (viewAsTableBtn) {
    viewAsTableBtn.addEventListener('click', function() {
        if (!window.allResults || window.allResults.length === 0) {
            alert('No results to display in table view');
            return;
        }

        fetch('/parts_list/table-view', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ results: window.allResults })
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

function parsePartNumbers(text) {
    if (!text || text.trim() === '') return [];
    const lines = text.split(/\r?\n/);
    const parts = [];
    const seenParts = new Set();

    const parseQuantity = (value) => {
        const qty = parseInt(value, 10);
        if (Number.isNaN(qty) || qty < 1) return 1;
        return qty;
    };

    for (let line of lines) {
        line = line.trim();
        if (!line) continue;

        let partNumber, quantity;

        if (line.includes(',')) {
            const commaParts = line.split(',');
            if (commaParts.length >= 2) {
                partNumber = commaParts[0].trim();
                const qtyStr = (commaParts[1] || '').trim();
                quantity = parseQuantity(qtyStr);
            } else {
                partNumber = line; quantity = 1;
            }
        } else {
            const tokens = line.split(/\s+/);
            if (tokens.length >= 2 && /^\d+$/.test(tokens[1])) {
                partNumber = tokens[0].trim();
                quantity = parseQuantity(tokens[1]);
            } else {
                partNumber = line; quantity = 1;
            }
        }

        const partKey = partNumber.toUpperCase();
        if (!seenParts.has(partKey)) {
            seenParts.add(partKey);
            parts.push({ part_number: partNumber, quantity });
        }
    }
    return parts;
}
});
