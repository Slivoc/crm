document.addEventListener('DOMContentLoaded', function() {
    let hasUnsavedChanges = false;
    let summaryUpdateTimer = null;
    const ADMIN_CC_EMAIL = 'harry@mgcaero.co.uk';

    const BASE_CURRENCY_ID = (() => {
        const baseCurrency = CURRENCIES.find(c => (c.currency_code || '').toUpperCase() === 'GBP');
        return baseCurrency ? baseCurrency.id : 3;
    })();
    const CUSTOMER_CURRENCY_ID_NUMBER = (() => {
        const parsed = Number.parseInt(CUSTOMER_CURRENCY_ID, 10);
        return Number.isFinite(parsed) ? parsed : null;
    })();
    let displayCurrencyId = CUSTOMER_CURRENCY_ID_NUMBER || BASE_CURRENCY_ID;
    const currencySelect = document.getElementById('quoteCurrencySelect');
    if (currencySelect) {
        if (CUSTOMER_CURRENCY_ID_NUMBER !== null) {
            const hasCustomerCurrencyOption = Array.from(currencySelect.options).some(opt => Number.parseInt(opt.value, 10) === CUSTOMER_CURRENCY_ID_NUMBER);
            if (hasCustomerCurrencyOption) {
                currencySelect.value = CUSTOMER_CURRENCY_ID_NUMBER;
            }
        }
        const initialId = Number.parseInt(currencySelect.value, 10);
        displayCurrencyId = Number.isFinite(initialId) ? initialId : displayCurrencyId;
        currencySelect.addEventListener('change', function() {
            const nextId = Number.parseInt(this.value, 10);
            displayCurrencyId = Number.isFinite(nextId) ? nextId : BASE_CURRENCY_ID;
            updateSummaryDisplay();
            const emailBody = document.getElementById('emailQuoteBody');
            if (emailBody) {
                emailBody.innerHTML = `${buildEmailBodyHtml()}<p></p>`;
            }
        });
    }

    function toNumber(value) {
        const num = parseFloat(value);
        return Number.isFinite(num) ? num : 0;
    }

    function getRequestedPartNumber(lineData) {
        return (lineData.requested_part_number || lineData.customer_part_number || '').toString().trim();
    }

    function getSupplierDisplay(lineData) {
        const supplierName = (lineData.chosen_supplier_name || '').toString().trim();
        if (supplierName) return supplierName;
        if ((lineData.chosen_source_type || '').toString().toLowerCase() === 'stock') return 'Stock';
        return '';
    }

    function getCurrencyMeta(currencyId) {
        return CURRENCIES.find(c => c.id == currencyId);
    }

    function getCurrencyCode(currencyId) {
        const currency = getCurrencyMeta(currencyId);
        return currency ? currency.currency_code : 'GBP';
    }

    function getCurrencySymbol(currencyId) {
        const currency = getCurrencyMeta(currencyId);
        if (!currency) return '';
        return currency.symbol || currency.currency_code || '';
    }

    function getDisplayCurrencyId() {
        return displayCurrencyId || BASE_CURRENCY_ID;
    }

    function formatQuotedOn(value) {
        if (!value) return '';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return '';
        return parsed.toLocaleDateString();
    }

    function setSummaryCurrencyLabels() {
        const baseCode = getCurrencyCode(BASE_CURRENCY_ID);
        const costLabel = document.getElementById('summaryCurrencyCodeCost');
        const quoteLabel = document.getElementById('summaryCurrencyCodeQuote');
        const marginLabel = document.getElementById('summaryCurrencyCodeMargin');
        if (costLabel) costLabel.textContent = baseCode;
        if (quoteLabel) quoteLabel.textContent = baseCode;
        if (marginLabel) marginLabel.textContent = baseCode;
    }

    function convertFromGbp(amount, currencyId) {
        const base = CURRENCIES.find(c => c.id == BASE_CURRENCY_ID);
        const target = CURRENCIES.find(c => c.id == currencyId);
        const baseRate = base ? toNumber(base.exchange_rate_to_eur) : 0;
        const targetRate = target ? toNumber(target.exchange_rate_to_eur) : 0;
        if (baseRate <= 0 || targetRate <= 0) {
            return toNumber(amount);
        }
        return toNumber(amount) * (targetRate / baseRate);
    }

    function formatCurrency(amount, currencyId) {
        const numericAmount = toNumber(amount);
        if (!Number.isFinite(numericAmount)) {
            return '-';
        }
        const symbol = getCurrencySymbol(currencyId);
        const prefix = symbol ? symbol : `${getCurrencyCode(currencyId)} `;
        return `${prefix}${numericAmount.toFixed(2)}`;
    }

    // --- 1. GLOBAL STATE (The Performance Fix) ---
    // We maintain running totals so we NEVER have to loop 2000 lines on every click.
    const globalState = {
        totalCost: 0,
        totalQuote: 0,
        createdCount: 0,
        inProgressCount: 0,
        quotedCount: 0,
        noBidCount: 0,
        belowMinCount: 0
    };

    // Cache DOM queries
    const summaryElements = {
        totalCost: document.getElementById('totalCost'),
        totalQuote: document.getElementById('totalQuote'),
        totalMargin: document.getElementById('totalMargin'),
        avgMargin: document.getElementById('avgMargin'),
        createdLinesCount: document.getElementById('createdLinesCount'),
        inProgressLinesCount: document.getElementById('inProgressLinesCount'),
        quotedLinesCount: document.getElementById('quotedLinesCount'),
        noBidLinesCount: document.getElementById('noBidLinesCount'),
        minValBadge: document.getElementById('below-minimum-count'),
        minValBtn: document.getElementById('minimum-line-value-btn')
    };

    // Main Cache: Stores Elements + Current Financial State
    const rowCache = new Map();

    // --- 2. CORE UTILITIES ---

    function setRowLockedState(row, isLocked, elementRefs = null) {
        const cached = rowCache.get(row);
        const elements = elementRefs || (cached ? cached.elements : null);
        if (!elements) return;

        row.dataset.locked = isLocked ? '1' : '0';

        const lockableInputs = [
            elements.chosenQty,
            elements.deliveryPerLine,
            elements.marginPercent,
            elements.quotePriceGbp,
            elements.leadDays,
            elements.isNoBid
        ];

        lockableInputs.forEach(el => {
            if (!el) return;
            el.disabled = isLocked;
            el.classList.remove('changed-input');
        });

        const calcButtons = [
            elements.calcBaseBtn,
            elements.calcDeliveryBtn,
            elements.calcMarginBtn
        ];
        calcButtons.forEach(btn => {
            if (!btn) return;
            btn.disabled = isLocked;
        });
    }

    function markUnsaved() {
        if (hasUnsavedChanges) return;
        hasUnsavedChanges = true;
        const saveBtn = document.getElementById('save-all-btn');
        saveBtn.classList.remove('btn-primary');
        saveBtn.classList.add('btn-warning');
        saveBtn.innerHTML = '<i class="bi bi-exclamation-circle me-1"></i>Save Changes';
    }

    function markSaved() {
        hasUnsavedChanges = false;
        const saveBtn = document.getElementById('save-all-btn');
        saveBtn.classList.remove('btn-warning');
        saveBtn.classList.add('btn-primary');
        saveBtn.innerHTML = '<i class="bi bi-save me-1"></i>Save All';
        document.querySelectorAll('.changed-input').forEach(el => el.classList.remove('changed-input'));
    }

    // Pure Math: Calculate financials for a single line without touching DOM
    function calculateLineFinancials(lineData, elements, status, isNoBid) {
        if (isNoBid) {
            return { cost: 0, quote: 0, isBelowMin: false };
        }

        const baseCost = toNumber(lineData.base_cost_gbp);
        const deliveryLine = parseFloat(elements.deliveryPerLine.value) || 0;
        const chosenQty = parseFloat(elements.chosenQty.value) || lineData.quantity;
        const quotePrice = parseFloat(elements.quotePriceGbp.value) || 0;

        const deliveryPerUnit = chosenQty > 0 ? deliveryLine / chosenQty : 0;
        const totalCost = (baseCost + deliveryPerUnit) * chosenQty;
        const totalQuote = quotePrice * chosenQty;

        // Check Minimum Value (Only if quoted and has value)
        const isBelowMin = (totalQuote > 0 && totalQuote < MIN_LINE_VALUE);

        return {
            cost: totalCost,
            quote: totalQuote,
            isBelowMin: isBelowMin,
            deliveryPerUnit: deliveryPerUnit
        };
    }

    // --- 3. INITIALIZATION ---

    function initializeTable() {
        const rows = document.querySelectorAll('.quote-row');

        rows.forEach((row, idx) => {
            const lineData = LINES_DATA[idx];
            const lineId = row.dataset.lineId;
            const detailRow = document.querySelector(`.detail-row[data-parent-line-id="${lineId}"]`);

            // Cache Elements - check detail row for elements moved there
            const elements = {
                chosenQty: row.querySelector('[data-field="chosen_qty"]'),
                deliveryPerLine: row.querySelector('[data-field="delivery_per_line"]'),
                marginPercent: row.querySelector('[data-field="margin_percent"]'),
                quotePriceGbp: row.querySelector('[data-field="quote_price_gbp"]'),
                deliveryPerUnit: row.querySelector('.delivery-per-unit'),
                baseCostCell: row.querySelector('.base-cost-gbp'),
                lineTotalCost: detailRow ? detailRow.querySelector('.line-total-cost') : null,
                lineTotalQuote: row.querySelector('.line-total-quote'),
                isNoBid: row.querySelector('[data-field="is_no_bid"]'),
                statusBtn: row.querySelector('.status-btn'),
                lineNotes: detailRow ? detailRow.querySelector('[data-field="line_notes"]') : null,
                leadDays: row.querySelector('[data-field="lead_days"]'),
                manufacturer: row.querySelector('[data-field="manufacturer"]'),
                displayPartNumber: row.querySelector('[data-field="display_part_number"]'),
                standardCondition: detailRow ? detailRow.querySelector('[data-field="standard_condition"]') : null,
                standardCerts: detailRow ? detailRow.querySelector('[data-field="standard_certs"]') : null,
                calcBaseBtn: row.querySelector('.line-calc-btn[data-calc="base"]'),
                calcDeliveryBtn: row.querySelector('.line-calc-btn[data-calc="delivery"]'),
                calcMarginBtn: row.querySelector('.line-calc-btn[data-calc="margin"]'),
                detailRow: detailRow
            };

            const status = row.dataset.status || 'created';
            const isNoBid = row.dataset.isNoBid == '1';

            // Calculate Initial Financials
            const fins = calculateLineFinancials(lineData, elements, status, isNoBid);

            // Update Visuals for this row
            updateRowVisuals(row, elements, fins, status, isNoBid);

            // Add to Global Totals
            globalState.totalCost += fins.cost;
            globalState.totalQuote += fins.quote;
            if (fins.isBelowMin) globalState.belowMinCount++;

            if (isNoBid || status === 'no_bid') globalState.noBidCount++;
            else if (status === 'in_progress') globalState.inProgressCount++;
            else if (status === 'quoted') globalState.quotedCount++;
            else globalState.createdCount++;

            // Store in Cache
            rowCache.set(row, {
                lineData,
                elements,
                lastFinancials: fins,
                lastStatus: status,
                lastIsNoBid: isNoBid
            });
        });

        updateSummaryDisplay();
    }

    // --- 4. DOM UPDATERS ---

    function updateRowVisuals(row, elements, fins, status, isNoBid) {
        // Update Totals Text
        if (isNoBid) {
            if (elements.lineTotalCost) elements.lineTotalCost.textContent = 'N/A';
            if (elements.lineTotalQuote) elements.lineTotalQuote.textContent = 'N/A';
            if (elements.deliveryPerUnit) elements.deliveryPerUnit.textContent = 'N/A';
            row.classList.add('no-bid-row');
            row.classList.remove('quoted-row', 'in-progress-row', 'below-minimum');
        } else {
                        const displayCurrencyId = getDisplayCurrencyId();
            const costDisplay = convertFromGbp(fins.cost, displayCurrencyId);
            const quoteDisplay = convertFromGbp(fins.quote, displayCurrencyId);
            const deliveryDisplay = convertFromGbp(fins.deliveryPerUnit, displayCurrencyId);

            if (elements.lineTotalCost) elements.lineTotalCost.textContent = formatCurrency(costDisplay, displayCurrencyId);
            if (elements.lineTotalQuote) elements.lineTotalQuote.textContent = formatCurrency(quoteDisplay, displayCurrencyId);
            if (elements.deliveryPerUnit) elements.deliveryPerUnit.textContent = formatCurrency(deliveryDisplay, displayCurrencyId);

            row.classList.remove('no-bid-row');
            row.classList.remove('in-progress-row');
            if (status === 'quoted') row.classList.add('quoted-row');
            else row.classList.remove('quoted-row');
            if (status === 'in_progress') row.classList.add('in-progress-row');

            if (fins.isBelowMin) row.classList.add('below-minimum');
            else row.classList.remove('below-minimum');
        }

        // Update Status Button
        const btn = elements.statusBtn;
        btn.className = 'status-btn status-pill'; // Reset

        if (status === 'quoted') {
            btn.classList.add('status-quoted');
            btn.innerHTML = '<i class="bi bi-check-circle me-1"></i>Quoted';
        } else if (status === 'in_progress') {
            btn.classList.add('status-in-progress');
            btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>In Progress';
        } else if (status === 'no_bid' || isNoBid) {
            btn.classList.add('status-no-bid');
            btn.innerHTML = '<i class="bi bi-x-circle me-1"></i>No Bid';
        } else {
            btn.classList.add('status-created');
            btn.innerHTML = '<i class="bi bi-circle me-1"></i>Created';
        }

        setRowLockedState(row, status === 'quoted', elements);
    }

    function updateSummaryDisplay() {
        const displayCurrencyId = getDisplayCurrencyId();
        const displayCurrencyCode = getCurrencyCode(displayCurrencyId);
        const totalCostDisplay = convertFromGbp(globalState.totalCost, displayCurrencyId);
        const totalQuoteDisplay = convertFromGbp(globalState.totalQuote, displayCurrencyId);

        const costLabel = document.getElementById('summaryCurrencyCodeCost');
        const quoteLabel = document.getElementById('summaryCurrencyCodeQuote');
        const marginLabel = document.getElementById('summaryCurrencyCodeMargin');
        if (costLabel) costLabel.textContent = displayCurrencyCode;
        if (quoteLabel) quoteLabel.textContent = displayCurrencyCode;
        if (marginLabel) marginLabel.textContent = displayCurrencyCode;

        summaryElements.totalCost.textContent = formatCurrency(totalCostDisplay, displayCurrencyId);
        summaryElements.totalQuote.textContent = formatCurrency(totalQuoteDisplay, displayCurrencyId);

        const margin = globalState.totalQuote - globalState.totalCost;
        const marginPct = globalState.totalQuote > 0 ? (margin / globalState.totalQuote) * 100 : 0;
        const marginDisplay = convertFromGbp(margin, displayCurrencyId);

        summaryElements.totalMargin.textContent = formatCurrency(marginDisplay, displayCurrencyId);
        summaryElements.avgMargin.textContent = marginPct.toFixed(1) + '%';

        if (summaryElements.createdLinesCount) summaryElements.createdLinesCount.textContent = globalState.createdCount;
        if (summaryElements.inProgressLinesCount) summaryElements.inProgressLinesCount.textContent = globalState.inProgressCount;
        if (summaryElements.quotedLinesCount) summaryElements.quotedLinesCount.textContent = globalState.quotedCount;
        if (summaryElements.noBidLinesCount) summaryElements.noBidLinesCount.textContent = globalState.noBidCount;

        summaryElements.minValBadge.textContent = globalState.belowMinCount;
        if (globalState.belowMinCount > 0) {
            summaryElements.minValBtn.classList.remove('btn-outline-warning');
            summaryElements.minValBtn.classList.add('btn-warning');
        } else {
            summaryElements.minValBtn.classList.remove('btn-warning');
            summaryElements.minValBtn.classList.add('btn-outline-warning');
        }
    }

    function updateBaseCostCell(elements, baseCostGbp) {
        if (!elements.baseCostCell) return;
        const numericBaseCost = toNumber(baseCostGbp);
        elements.baseCostCell.textContent = `GBP ${numericBaseCost.toFixed(2)}`;
    }

    function setInputValue(input, value, decimals = 2) {
        if (!input) return;
        const numericValue = toNumber(value);
        input.value = Number.isFinite(numericValue) ? numericValue.toFixed(decimals) : '';
        input.classList.remove('changed-input');
    }

    // --- 5. THE DELTA UPDATER (O(1) Lag Fix) ---
    function handleRowChange(row, newStatus = null, newIsNoBid = null, skipUnsavedFlag = false) {
        const cached = rowCache.get(row);
        if (!cached) return;

        // Determine effective new state
        const status = newStatus !== null ? newStatus : row.dataset.status;
        const isNoBid = newIsNoBid !== null ? newIsNoBid : (row.dataset.isNoBid == '1');

        // Calculate NEW financials
        const newFins = calculateLineFinancials(cached.lineData, cached.elements, status, isNoBid);

        // --- GLOBAL STATE UPDATES (Subtract Old, Add New) ---
        globalState.totalCost = globalState.totalCost - cached.lastFinancials.cost + newFins.cost;
        globalState.totalQuote = globalState.totalQuote - cached.lastFinancials.quote + newFins.quote;

        if (cached.lastFinancials.isBelowMin) globalState.belowMinCount--;
        if (newFins.isBelowMin) globalState.belowMinCount++;

        // Update Counts if status changed
        if (status !== cached.lastStatus) {
            // Decrement old
            if (cached.lastIsNoBid || cached.lastStatus === 'no_bid') globalState.noBidCount--;
            else if (cached.lastStatus === 'in_progress') globalState.inProgressCount--;
            else if (cached.lastStatus === 'quoted') globalState.quotedCount--;
            else globalState.createdCount--;

            // Increment new
            if (isNoBid || status === 'no_bid') globalState.noBidCount++;
            else if (status === 'in_progress') globalState.inProgressCount++;
            else if (status === 'quoted') globalState.quotedCount++;
            else globalState.createdCount++;
        }

        // --- UPDATE CACHE ---
        cached.lastFinancials = newFins;
        cached.lastStatus = status;
        cached.lastIsNoBid = isNoBid;

        // --- UPDATE VISUALS ---
        requestAnimationFrame(() => {
            updateRowVisuals(row, cached.elements, newFins, status, isNoBid);
            updateSummaryDisplay();
            updateEmailQuoteWarnings();
            if (!skipUnsavedFlag) {
                markUnsaved();
            }
        });
    }

    // --- 6. EVENT LISTENERS ---

    document.getElementById('quoteTableBody').addEventListener('click', async function(e) {
        const resetBtn = e.target.closest('.reset-line-btn');
        if (resetBtn) {
            const row = resetBtn.closest('tr');
            if (!row || row.dataset.locked === '1') return;

            const cached = rowCache.get(row);
            if (!cached) return;

            if (cached.elements.quotePriceGbp) {
                cached.elements.quotePriceGbp.value = '0.00';
                cached.elements.quotePriceGbp.classList.add('changed-input');
            }
            if (cached.elements.marginPercent) {
                cached.elements.marginPercent.value = '0.0';
                cached.elements.marginPercent.classList.add('changed-input');
            }
            if (cached.elements.deliveryPerLine) {
                cached.elements.deliveryPerLine.value = '0.00';
                cached.elements.deliveryPerLine.classList.add('changed-input');
            }
            if (cached.elements.isNoBid) {
                cached.elements.isNoBid.checked = false;
                cached.elements.isNoBid.classList.add('changed-input');
            }

            row.dataset.isNoBid = '0';
            row.dataset.status = 'created';
            handleRowChange(row, 'created', false);
            return;
        }

        const supplierBtn = e.target.closest('.use-supplier-pn-btn');
        if (supplierBtn) {
            const row = supplierBtn.closest('tr');
            if (!row || row.dataset.locked === '1') return;
            const cached = rowCache.get(row);
            if (!cached) return;
            const supplierPN = (supplierBtn.dataset.supplierPn || '').trim();
            if (!supplierPN) return;
            const displayInput = cached.elements.displayPartNumber;
            if (!displayInput) return;
            displayInput.value = supplierPN;
            displayInput.classList.add('changed-input');
            cached.lineData.display_part_number = supplierPN;
            markUnsaved();
            updateEmailQuoteWarnings();
            return;
        }

        const bulkMarginBtn = e.target.closest('.apply-bulk-margin-btn');
        if (bulkMarginBtn) {
            const row = bulkMarginBtn.closest('tr');
            if (!row || row.dataset.locked === '1') return;
            const cached = rowCache.get(row);
            if (!cached || !cached.elements.marginPercent) return;

            const bulkMarginInput = document.getElementById('bulk-margin-input');
            const bulkValue = bulkMarginInput ? parseFloat(bulkMarginInput.value) : NaN;
            if (!Number.isFinite(bulkValue)) {
                alert('Bulk margin value is not set.');
                return;
            }

            cached.elements.marginPercent.value = bulkValue.toFixed(1);
            cached.elements.marginPercent.dispatchEvent(new Event('change', { bubbles: true }));
            return;
        }

        const btn = e.target.closest('.line-calc-btn');
        if (!btn) return;

        const row = btn.closest('tr');
        if (!row || row.dataset.locked === '1') return;

        const cached = rowCache.get(row);
        if (!cached) return;

        const lineId = cached.lineData.id;
        const calcType = btn.dataset.calc;

        let url = '';
        let payload = null;
        let quoteUpdateMode = 'always';

        if (calcType === 'base') {
            url = `/customer-quoting/parts-lists/${LIST_ID}/customer-quote/line/${lineId}/calculate-base-cost`;
            quoteUpdateMode = 'onFlag';
        } else if (calcType === 'delivery') {
            url = `/customer-quoting/parts-lists/${LIST_ID}/customer-quote/line/${lineId}/calculate-delivery`;
        } else if (calcType === 'margin') {
            url = `/customer-quoting/parts-lists/${LIST_ID}/customer-quote/line/${lineId}/calculate-margin`;
            payload = { margin_percent: toNumber(cached.elements.marginPercent.value) };
        } else {
            return;
        }

        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Working...';

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: payload ? { 'Content-Type': 'application/json' } : {},
                body: payload ? JSON.stringify(payload) : undefined
            });

            const result = await response.json();
            if (!result.success) {
                alert('Error: ' + (result.message || 'Failed to calculate'));
                return;
            }
            if (result.skipped) {
                alert(result.message || 'Line is locked');
                return;
            }

            if (typeof result.base_cost_gbp !== 'undefined') {
                cached.lineData.base_cost_gbp = result.base_cost_gbp;
                updateBaseCostCell(cached.elements, result.base_cost_gbp);
            }

            if (typeof result.delivery_per_line !== 'undefined') {
                setInputValue(cached.elements.deliveryPerLine, result.delivery_per_line, 2);
            }

            if (typeof result.margin_percent !== 'undefined') {
                setInputValue(cached.elements.marginPercent, result.margin_percent, 1);
            }

            if (typeof result.quote_price_gbp !== 'undefined') {
                const shouldUpdateQuote = quoteUpdateMode === 'always' || result.update_quote_price;
                if (shouldUpdateQuote) {
                    setInputValue(cached.elements.quotePriceGbp, result.quote_price_gbp, 2);
                }
            }

            if (typeof result.lead_days !== 'undefined' && cached.elements.leadDays) {
                cached.elements.leadDays.value = result.lead_days || '';
                cached.elements.leadDays.classList.remove('changed-input');
            }

            if (result.quoted_status) {
                row.dataset.status = result.quoted_status;
            }

            handleRowChange(row, null, null, true);
        } catch (error) {
            console.error('Line calculation failed:', error);
            alert('Failed to calculate this line');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    });

    // Optimized Click Handler (Status Button)
    document.getElementById('quoteTableBody').addEventListener('click', function(e) {
        const btn = e.target.closest('.status-btn');
        if (!btn) return;

        const row = btn.closest('tr');
        const currentStatus = row.dataset.status || 'created';
        const isCurrentlyNoBid = row.dataset.isNoBid == '1';

        // Calculate next status logic
        const statuses = ['created', 'in_progress', 'quoted', 'no_bid'];
        let currentIndex = statuses.indexOf(currentStatus);

        const nextStatus = statuses[(currentIndex + 1) % statuses.length];
        const nextIsNoBid = nextStatus === 'no_bid';

        // Update Data Attributes
        row.dataset.status = nextStatus;
        row.dataset.isNoBid = nextIsNoBid ? '1' : '0';

        // Update Checkbox silently
        const cached = rowCache.get(row);
        if (cached) cached.elements.isNoBid.checked = nextIsNoBid;

        // Trigger Delta Update
        handleRowChange(row, nextStatus, nextIsNoBid);
    });

    // Optimized Input Change Handler
    document.getElementById('quoteTableBody').addEventListener('change', function(e) {
        const input = e.target;
        if (!input.classList.contains('editable-field')) return;

        const row = input.closest('tr');
        if (row.dataset.locked === '1') return;

        input.classList.add('changed-input');
        markUnsaved();

        const cached = rowCache.get(row);
        const field = input.dataset.field;

        // Handle auto-calc fields
        if (field === 'delivery_per_line' || field === 'margin_percent') {
            const baseCost = toNumber(cached.lineData.base_cost_gbp);
            const deliveryLine = parseFloat(cached.elements.deliveryPerLine.value) || 0;
            const effectiveQty = parseFloat(cached.elements.chosenQty.value) || cached.lineData.quantity;
            const deliveryPerUnit = effectiveQty > 0 ? deliveryLine / effectiveQty : 0;

            if (field === 'delivery_per_line') {
                const margin = parseFloat(cached.elements.marginPercent.value) || 0;
                let price = baseCost;
                if (margin > 0 && margin < 100) price = baseCost / (1 - margin/100);
                cached.elements.quotePriceGbp.value = (price + deliveryPerUnit).toFixed(2);
            }
            else if (field === 'margin_percent') {
                const margin = parseFloat(input.value) || 0;
                let price = baseCost;
                if (margin > 0 && margin < 100) price = baseCost / (1 - margin/100);
                cached.elements.quotePriceGbp.value = (price + deliveryPerUnit).toFixed(2);
            }
        }

        // Handle "No Bid" Checkbox specifically
        if (field === 'is_no_bid') {
            const isChecked = input.checked;
            row.dataset.isNoBid = isChecked ? '1' : '0';
            const nextStatus = isChecked ? 'no_bid' : 'created';
            row.dataset.status = nextStatus;
            handleRowChange(row, nextStatus, isChecked);
            return;
        }

        // Handle standard update
        handleRowChange(row);
    });

    // Recalculate delivery per unit live when qty or delivery/line changes
    document.getElementById('quoteTableBody').addEventListener('input', function(e) {
        const input = e.target;
        if (!input.classList.contains('editable-field')) return;
        const field = input.dataset.field;
        if (field !== 'delivery_per_line' && field !== 'chosen_qty') return;

        const row = input.closest('tr');
        if (row.dataset.locked === '1') return;

        input.classList.add('changed-input');
        markUnsaved();
        handleRowChange(row);
    });

    // --- 7. UTILITY FEATURES (Restored Full Logic) ---

    function autoSelectDiffColumns() {
        let pnDiff = false;
        let qtyDiff = false;
        let revisionFilled = false;
        let conditionFilled = false;
        let certsFilled = false;
        let notesFilled = false;
        let manufacturerFilled = false;

        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements } = cached;

            const requestedPN = getRequestedPartNumber(lineData);
            const quotedPN = elements.displayPartNumber ? elements.displayPartNumber.value.trim() : requestedPN;
            if (quotedPN && quotedPN !== requestedPN) pnDiff = true;

            const requestedQty = lineData.quantity;
            const effectiveQty = parseFloat(elements.chosenQty.value) || requestedQty;
            if (effectiveQty !== requestedQty) qtyDiff = true;

            const revisionVal = (lineData.revision || '').toString().trim();
            const conditionVal = (elements.standardCondition && elements.standardCondition.value.trim()) || (lineData.supplier_condition_code || '').toString().trim();
            const certsVal = (elements.standardCerts && elements.standardCerts.value.trim()) || (lineData.supplier_certifications || '').toString().trim();
            const notesVal = elements.lineNotes ? elements.lineNotes.value.trim() : '';
            const manufacturerVal = (elements.manufacturer && elements.manufacturer.value.trim()) || (lineData.manufacturer || '').toString().trim();

            if (revisionVal) revisionFilled = true;
            if (conditionVal) conditionFilled = true;
            if (certsVal) certsFilled = true;
            if (notesVal) notesFilled = true;
            if (manufacturerVal) manufacturerFilled = true;
        });

        if (pnDiff) {
            const pnCheckbox = document.getElementById('col-requested-pn');
            if (pnCheckbox) pnCheckbox.checked = true;
        }
        if (qtyDiff) {
            const qtyCheckbox = document.getElementById('col-requested-qty');
            if (qtyCheckbox) qtyCheckbox.checked = true;
        }
        if (revisionFilled) {
            const revisionCheckbox = document.getElementById('col-revision');
            if (revisionCheckbox) revisionCheckbox.checked = true;
        }
        if (conditionFilled) {
            const conditionCheckbox = document.getElementById('col-condition');
            if (conditionCheckbox) conditionCheckbox.checked = true;
        }
        if (certsFilled) {
            const certsCheckbox = document.getElementById('col-certs');
            if (certsCheckbox) certsCheckbox.checked = true;
        }
        if (notesFilled) {
            const notesCheckbox = document.getElementById('col-notes');
            if (notesCheckbox) notesCheckbox.checked = true;
        }
        if (manufacturerFilled) {
            const mfrCheckbox = document.getElementById('col-manufacturer');
            if (mfrCheckbox) mfrCheckbox.checked = true;
        }
    }

    function collectPurchasingInstructionRows() {
        const rows = [];
        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements, lastIsNoBid } = cached;

            if (lastIsNoBid) return;
            const supplierDisplay = getSupplierDisplay(lineData);
            if (!supplierDisplay) return;

            const effectiveQty = parseFloat(elements.chosenQty.value) || lineData.quantity;
            const unitCost = parseFloat(lineData.chosen_cost || 0);
            if (unitCost <= 0) return;

            rows.push({
                rowKey: lineData.id,
                lineData,
                effectiveQty,
                unitCost,
                supplierDisplay,
                basePartNumber: lineData.base_part_number,
                supplierId: lineData.chosen_supplier_id || null
            });
        });
        return rows;
    }

    function updatePurchasingSupplierFilter(rows) {
        const filterSelect = document.getElementById('purchasingSupplierFilter');
        if (!filterSelect) return;

        const previousValue = filterSelect.value;
        const suppliers = Array.from(new Set(rows.map(row => row.supplierDisplay))).sort();

        filterSelect.innerHTML = '<option value="">All suppliers</option>';
        suppliers.forEach(supplier => {
            const option = document.createElement('option');
            option.value = supplier;
            option.textContent = supplier;
            filterSelect.appendChild(option);
        });

        if (previousValue && suppliers.includes(previousValue)) {
            filterSelect.value = previousValue;
        }
    }

    const purchasingInsightsByLineId = new Map();

    function getPriceInsightLabel(insight) {
        if (!insight || !insight.price_insight) return '-';
        if (!insight.price_insight.has_history) return 'No history';
        return insight.price_insight.label || '-';
    }

    function getSuggestedQtyLabel(instructionRow, insight) {
        if (insight && insight.quantity_recommendation && Number.isFinite(insight.quantity_recommendation.suggested_quantity)) {
            const suggested = Number(insight.quantity_recommendation.suggested_quantity);
            const current = Number(instructionRow.effectiveQty || 0);
            if (suggested > current) {
                return `${suggested} (buy +${suggested - current})`;
            }
            return `${suggested}`;
        }
        return '-';
    }

    function renderPurchasingInstructionsTable(filterSupplier) {
        const rows = collectPurchasingInstructionRows();
        updatePurchasingSupplierFilter(rows);
        const resolvedFilter = filterSupplier ?? document.getElementById('purchasingSupplierFilter')?.value ?? '';
        const filteredRows = resolvedFilter
            ? rows.filter(row => row.supplierDisplay === resolvedFilter)
            : rows;

        document.getElementById('purchasingInstructionsBody').innerHTML = buildPurchasingInstructionsTable(filteredRows);
    }

    // Build Purchasing Instructions Table
    function buildPurchasingInstructionsTable(rows) {
        let html = `
<table class="table table-sm table-bordered" style="border-collapse:collapse;font-family:Arial, sans-serif;font-size:0.9rem;margin:auto;max-width:1200px;">
  <thead>
    <tr style="background:#f8f9fa;">
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Line</th>
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Part Number</th>
      <th align="right" style="padding:6px 8px;border:1px solid #dee2e6;">Quantity</th>
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Supplier</th>
      <th align="right" style="padding:6px 8px;border:1px solid #dee2e6;">Unit Cost</th>
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Currency</th>
      <th align="right" style="padding:6px 8px;border:1px solid #dee2e6;">Line Total</th>
      <th align="right" style="padding:6px 8px;border:1px solid #dee2e6;">Lead Time (days)</th>
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Suggested Buy Qty</th>
      <th align="left" style="padding:6px 8px;border:1px solid #dee2e6;">Price vs History</th>
    </tr>
  </thead>
  <tbody>`;

        rows.forEach(row => {
            const { lineData, effectiveQty, unitCost, supplierDisplay } = row;
            const lineTotal = unitCost * effectiveQty;
            const currency = lineData.chosen_currency_code || 'GBP';
            const leadDays = lineData.chosen_lead_days || '';
            const cellStyle = 'padding:6px 8px;border:1px solid #dee2e6;';
            const lineInsight = purchasingInsightsByLineId.get(row.rowKey);

            html += `<tr>
              <td align="left" style="${cellStyle}">${lineData.line_number || ''}</td>
              <td align="left" style="${cellStyle}">${getRequestedPartNumber(lineData)}</td>
              <td align="right" style="${cellStyle}">${effectiveQty || ''}</td>
                <td align="left" style="${cellStyle}">${supplierDisplay}</td>
              <td align="right" style="${cellStyle}">${unitCost.toFixed(2)}</td>
              <td align="left" style="${cellStyle}">${currency}</td>
              <td align="right" style="${cellStyle}">${lineTotal.toFixed(2)}</td>
              <td align="right" style="${cellStyle}">${leadDays}</td>
              <td align="left" style="${cellStyle}">${getSuggestedQtyLabel(row, lineInsight)}</td>
              <td align="left" style="${cellStyle}">${getPriceInsightLabel(lineInsight)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
        return html;
    }

    async function loadPurchasingInsights() {
        const statusEl = document.getElementById('purchasingInsightsStatus');
        const supplierFilter = document.getElementById('purchasingSupplierFilter')?.value || '';
        const allRows = collectPurchasingInstructionRows();
        const filteredRows = supplierFilter
            ? allRows.filter(row => row.supplierDisplay === supplierFilter)
            : allRows;

        const rowsToFetch = filteredRows.filter(row => row.basePartNumber && !purchasingInsightsByLineId.has(row.rowKey));
        if (rowsToFetch.length === 0) {
            if (statusEl) statusEl.textContent = 'Suggestions already loaded for current filter.';
            renderPurchasingInstructionsTable();
            return;
        }

        if (statusEl) statusEl.textContent = `Loading suggestions for ${rowsToFetch.length} line(s)...`;

        let successCount = 0;
        for (const row of rowsToFetch) {
            const payload = {
                base_part_number: row.basePartNumber,
                po_quantity: parseInt(row.effectiveQty, 10) || 0,
                supplier_id: row.supplierId,
                supplier_cost: row.unitCost
            };

            try {
                const response = await fetch('/parts-list/po-check/supplier-insight', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await response.json();
                if (response.ok && result.success) {
                    purchasingInsightsByLineId.set(row.rowKey, result);
                    successCount += 1;
                }
            } catch (error) {
                console.warn('Failed to load purchasing insight', row.rowKey, error);
            }
        }

        if (statusEl) {
            statusEl.textContent = successCount > 0
                ? `Loaded ${successCount}/${rowsToFetch.length} suggestion(s).`
                : 'Could not load suggestions.';
        }

        renderPurchasingInstructionsTable();
    }

   // Build Email Quote Table (Fixed: Shows No Bids, Hides Empty Prices)
    function buildEmailQuoteTable() {
        const displayCurrencyId = getDisplayCurrencyId();
        const displayCurrencyCode = getCurrencyCode(displayCurrencyId);
        // 1. Get Selected Columns
        const selectedCols = {};
        document.querySelectorAll('.email-col-check').forEach(cb => selectedCols[cb.value] = cb.checked);
        const selectedColumnCount = Math.max(
            1,
            Object.values(selectedCols).filter(Boolean).length
        );

        // 2. Build Headers
        let headers = '';
        const hStyle = 'padding:4px 6px;border-bottom:1px solid #dee2e6;';
        if (selectedCols.line) headers += `<th align="left" style="${hStyle}">Line</th>`;
        if (selectedCols.requested_pn) headers += `<th align="left" style="${hStyle}">Requested P/N</th>`;
        if (selectedCols.quoted_pn) headers += `<th align="left" style="${hStyle}">Quoted P/N</th>`;
        if (selectedCols.revision) headers += `<th align="left" style="${hStyle}">Rev</th>`;
        if (selectedCols.requested_qty) headers += `<th align="right" style="${hStyle}">Requested Qty</th>`;
        if (selectedCols.qty) headers += `<th align="right" style="${hStyle}">Quoted Qty</th>`;
        if (selectedCols.unit_price) headers += `<th align="right" style="${hStyle}">Unit Price (${displayCurrencyCode})</th>`;
        if (selectedCols.line_total) headers += `<th align="right" style="${hStyle}">Line Total (${displayCurrencyCode})</th>`;
        if (selectedCols.lead_days) headers += `<th align="left" style="${hStyle}">Lead (days)</th>`;
        if (selectedCols.quoted_on) headers += `<th align="left" style="${hStyle}">Quoted On</th>`;
        if (selectedCols.manufacturer) headers += `<th align="left" style="${hStyle}">Mfr</th>`;
        if (selectedCols.condition) headers += `<th align="left" style="${hStyle}">Condition</th>`;
        if (selectedCols.certs) headers += `<th align="left" style="${hStyle}">Certs</th>`;
        if (selectedCols.notes) headers += `<th align="left" style="${hStyle}">Notes</th>`;

        // 3. Start Table HTML
        let html = `<table class="table table-sm" style="border-collapse:collapse;font-family:Arial, sans-serif;font-size:0.9rem;margin:auto;max-width:900px;">
          <thead>
            <tr style="background:#eef3ff;">
              <th align="left" colspan="${selectedColumnCount}" style="${hStyle}font-weight:700;">Parts List ID: ${LIST_ID}</th>
            </tr>
            <tr style="background:#f8f9fa;">${headers}</tr>
          </thead><tbody>`;

        // 4. Loop Through Rows
        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements, lastIsNoBid } = cached;

            const status = row.dataset.status || cached.lastStatus || 'created';
            const quotePrice = parseFloat(elements.quotePriceGbp.value) || 0;

            const quotePriceDisplay = convertFromGbp(quotePrice, displayCurrencyId);

            // --- FILTER LOGIC ---
            // Keep No Bid and In Progress lines visible even without pricing.
            if (!lastIsNoBid && status !== 'in_progress' && quotePrice <= 0) return;

            // --- VISUAL LOGIC ---
            const effectiveQty = parseFloat(elements.chosenQty.value) || lineData.quantity;
            const requestedQty = lineData.quantity;
            const lineTotal = quotePrice * effectiveQty;
            const lineTotalDisplayAmount = convertFromGbp(lineTotal, displayCurrencyId);

            // Default Styles
            let rowStyle = 'padding:4px 6px;border-bottom:1px solid #dee2e6;';
            let unitPriceDisplay = quotePriceDisplay.toFixed(2);
            let lineTotalDisplay = lineTotalDisplayAmount.toFixed(2);

            // Handle "No Bid" Styling
            if (lastIsNoBid) {
                unitPriceDisplay = 'NO BID';
                lineTotalDisplay = '-';
                rowStyle += 'background-color:#fff3cd; color:#856404; font-style:italic;';
            } else if (status === 'in_progress') {
                unitPriceDisplay = 'IN PROGRESS';
                lineTotalDisplay = '-';
                rowStyle += 'background-color:#e7f1ff; color:#0c63e4; font-style:italic;';
            }

            // Highlights
            const requestedPN = getRequestedPartNumber(lineData);
            const quotedPN = elements.displayPartNumber ? elements.displayPartNumber.value.trim() : requestedPN;
            const revisionValue = (lineData.revision || '').toString().trim() || '-';

            const isPNDifferent = !lastIsNoBid && (quotedPN !== requestedPN && quotedPN !== '');
            const isQtyDifferent = !lastIsNoBid && (effectiveQty !== requestedQty);
            const conditionValue = (elements.standardCondition && elements.standardCondition.value.trim()) || (lineData.supplier_condition_code || '');
            const certsValue = (elements.standardCerts && elements.standardCerts.value.trim()) || (lineData.supplier_certifications || '');

            const highlightStyle = isPNDifferent ? `${rowStyle}background-color:#fff3cd;font-weight:600;` : rowStyle;
            const qtyHighlightStyle = isQtyDifferent ? `${rowStyle}background-color:#e3f2fd;font-weight:600;` : rowStyle;

            // --- BUILD ROW HTML ---
            html += '<tr>';
            if (selectedCols.line) html += `<td align="left" style="${rowStyle}">${lineData.line_number || ''}</td>`;
            if (selectedCols.requested_pn) html += `<td align="left" style="${highlightStyle}">${requestedPN}</td>`;
            if (selectedCols.quoted_pn) html += `<td align="left" style="${highlightStyle}">${isPNDifferent && !selectedCols.requested_pn ? quotedPN + ' *' : quotedPN}</td>`;
            if (selectedCols.revision) html += `<td align="left" style="${rowStyle}">${revisionValue}</td>`;
            if (selectedCols.requested_qty) html += `<td align="right" style="${qtyHighlightStyle}">${requestedQty || ''}</td>`;
            if (selectedCols.qty) html += `<td align="right" style="${qtyHighlightStyle}">${isQtyDifferent && !selectedCols.requested_qty ? effectiveQty + ' *' : effectiveQty}</td>`;
            if (selectedCols.unit_price) html += `<td align="right" style="${rowStyle}">${unitPriceDisplay}</td>`;
            if (selectedCols.line_total) html += `<td align="right" style="${rowStyle}">${lineTotalDisplay}</td>`;
            if (selectedCols.lead_days) html += `<td align="left" style="${rowStyle}">${elements.leadDays.value || ''}</td>`;
            if (selectedCols.quoted_on) html += `<td align="left" style="${rowStyle}">${status === 'in_progress' ? 'In Progress' : formatQuotedOn(lineData.quoted_on)}</td>`;
            const manufacturerVal = (elements.manufacturer && elements.manufacturer.value.trim()) || lineData.manufacturer || '';

            if (selectedCols.manufacturer) html += `<td align="left" style="${rowStyle}">${manufacturerVal}</td>`;
            if (selectedCols.condition) html += `<td align="left" style="${rowStyle}">${conditionValue || ''}</td>`;
            if (selectedCols.certs) html += `<td align="left" style="${rowStyle}">${certsValue || ''}</td>`;
            if (selectedCols.notes) html += `<td align="left" style="${rowStyle}">${elements.lineNotes ? elements.lineNotes.value : ''}</td>`;
            html += '</tr>';
        });

        html += `</tbody></table>`;
        return html;
    }

    // --- 8. BUTTON ACTIONS ---

    // Save All
    document.getElementById('save-all-btn').addEventListener('click', async function() {
        const btn = this;
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving...';

        const rows = document.querySelectorAll('.quote-row');
        const updates = [];

        rows.forEach((row) => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements } = cached;

            updates.push({
                parts_list_line_id: lineData.id,
                chosen_qty: parseFloat(elements.chosenQty.value) || null,
                manufacturer: elements.manufacturer ? elements.manufacturer.value : '',
                display_part_number: elements.displayPartNumber.value,
                margin_percent: parseFloat(elements.marginPercent.value) || 0,
                quote_price_gbp: parseFloat(elements.quotePriceGbp.value) || 0,
                delivery_per_line: parseFloat(elements.deliveryPerLine.value) || 0,
                lead_days: parseInt(elements.leadDays.value) || null,
                is_no_bid: elements.isNoBid.checked ? 1 : 0,
                quoted_status: row.dataset.status,
                line_notes: elements.lineNotes ? elements.lineNotes.value : '',
                standard_condition: elements.standardCondition ? elements.standardCondition.value : '',
                standard_certs: elements.standardCerts ? elements.standardCerts.value : ''
            });
        });

        try {
            const response = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/customer-quote/bulk-update`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ updates })
            });

            const result = await response.json();
            if (result.success) {
                markSaved();
                btn.innerHTML = '<i class="bi bi-check me-1"></i>Saved!';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.disabled = false; }, 2000);
            } else {
                alert('Error saving: ' + result.message);
                btn.disabled = false;
                btn.innerHTML = originalHtml;
            }
        } catch (error) {
            console.error('Error saving:', error);
            alert('Failed to save changes');
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    });

    // Purchasing Instructions
    document.getElementById('purchasing-instructions-btn').addEventListener('click', function() {
        renderPurchasingInstructionsTable();
        const statusEl = document.getElementById('purchasingInsightsStatus');
        if (statusEl) statusEl.textContent = '';
        new bootstrap.Modal(document.getElementById('purchasingModal')).show();
    });

    const purchasingSupplierFilter = document.getElementById('purchasingSupplierFilter');
    if (purchasingSupplierFilter) {
        purchasingSupplierFilter.addEventListener('change', function() {
            renderPurchasingInstructionsTable(this.value);
        });
    }

    const loadPurchasingInsightsBtn = document.getElementById('loadPurchasingInsightsBtn');
    if (loadPurchasingInsightsBtn) {
        loadPurchasingInsightsBtn.addEventListener('click', async function() {
            const originalHtml = this.innerHTML;
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
            await loadPurchasingInsights();
            this.innerHTML = originalHtml;
            this.disabled = false;
        });
    }

    // Copy Purchasing
    document.getElementById('copyPurchasingBtn').addEventListener('click', async function() {
        const bodyHtml = document.getElementById('purchasingInstructionsBody').innerHTML;
        const plainText = bodyHtml.replace(/<br\s*\/?>/gi, '\n').replace(/<\/p>/gi, '\n\n').replace(/<[^>]+>/g, '').replace(/\n{3,}/g, '\n\n').trim();
        const btn = this;
        const originalHtml = btn.innerHTML;

        try {
            await navigator.clipboard.write([
                new ClipboardItem({ 'text/html': new Blob([bodyHtml], { type: 'text/html' }), 'text/plain': new Blob([plainText], { type: 'text/plain' }) })
            ]);
            btn.innerHTML = '<i class="bi bi-check me-1"></i>Copied!';
            btn.className = 'btn btn-success';
            setTimeout(() => { btn.innerHTML = originalHtml; btn.className = 'btn btn-primary'; }, 1500);
        } catch (e) { alert('Copy failed'); }
    });

    // --- RIGHT CLICK CONTEXT MENU (The "Fast Mode" Fix) ---
    document.getElementById('quoteTableBody').addEventListener('contextmenu', function(e) {
        // 1. Find the row
        const row = e.target.closest('tr');
        if (!row) return;

        // 2. Prevent the standard browser right-click menu
        e.preventDefault();

        // 3. Get current state
        const currentStatus = row.dataset.status || 'created';

        // 4. Calculate next status (Cycle: Created -> In Progress -> Quoted -> No Bid)
        const statuses = ['created', 'in_progress', 'quoted', 'no_bid'];
        let currentIndex = statuses.indexOf(currentStatus);
        const nextStatus = statuses[(currentIndex + 1) % statuses.length];
        const nextIsNoBid = nextStatus === 'no_bid';

        // 5. Update Data Attributes
        row.dataset.status = nextStatus;
        row.dataset.isNoBid = nextIsNoBid ? '1' : '0';

        // 6. Sync the Checkbox (silently)
        // We use your existing cache system
        const cached = rowCache.get(row);
        if (cached && cached.elements.isNoBid) {
            cached.elements.isNoBid.checked = nextIsNoBid;
        }

        // 7. Trigger the Delta Update (Your O(1) logic)
        handleRowChange(row, nextStatus, nextIsNoBid);
    });

    // Email Quote
   // --- REFACTORED EMAIL QUOTE LOGIC ---
    let relatedEmailsLoaded = false;

    async function loadRelatedEmailsForReply() {
        if (relatedEmailsLoaded) {
            return;
        }
        const replySelect = document.getElementById('emailQuoteReplySelect');
        if (!replySelect) {
            return;
        }
        replySelect.innerHTML = '<option value="">Send new email (no reply)</option>';
        try {
            const response = await fetch(`/parts_list/parts-lists/${LIST_ID}/related-emails/data`);
            const result = await response.json();
            if (!response.ok || !result.success) {
                return;
            }
            const emails = result.emails || [];
            emails.forEach(email => {
                if (!email.id) {
                    return;
                }
                const fromLabel = email.from_name ? `${email.from_name} <${email.from_address || ''}>` : (email.from_address || 'Unknown');
                const dateLabel = email.receivedDateTime_display || '';
                const subjectLabel = email.subject || '(No subject)';
                const option = document.createElement('option');
                option.value = email.id;
                option.textContent = `${subjectLabel} - ${fromLabel}${dateLabel ? ` (${dateLabel})` : ''}`;
                if (email.is_source) {
                    option.textContent = `[Source] ${option.textContent}`;
                }
                replySelect.appendChild(option);
            });
            relatedEmailsLoaded = true;
        } catch (error) {
            console.warn('Failed to load related emails for reply.', error);
        }
    }

    let manualSubjectValue = '';

    async function loadReplyEmailPreview(messageId) {
        const previewBox = document.getElementById('emailQuoteReplyPreview');
        const previewSubject = document.getElementById('emailQuoteReplyPreviewSubject');
        const previewMeta = document.getElementById('emailQuoteReplyPreviewMeta');
        const previewBody = document.getElementById('emailQuoteReplyPreviewBody');
        const subjectInput = document.getElementById('emailQuoteSubjectInput');
        if (!previewBox || !previewSubject || !previewMeta || !previewBody) {
            return;
        }

        if (!messageId) {
            previewBox.classList.add('d-none');
            previewSubject.textContent = '';
            previewMeta.textContent = '';
            previewBody.textContent = '';
            return;
        }

        previewSubject.textContent = 'Loading reply preview...';
        previewMeta.textContent = '';
        previewBody.textContent = '';
        previewBox.classList.remove('d-none');

        try {
            const response = await fetch(`/emails/graph/message/${encodeURIComponent(messageId)}`);
            const result = await response.json();
            if (!response.ok || !result.success || !result.message) {
                previewSubject.textContent = 'Unable to load reply preview.';
                return;
            }
            const msg = result.message || {};
            const fromAddr = msg.from?.emailAddress?.address || 'Unknown';
            const fromName = msg.from?.emailAddress?.name || '';
            const received = msg.receivedDateTime ? new Date(msg.receivedDateTime).toLocaleString() : 'Unknown date';
            previewSubject.textContent = msg.subject || '(No subject)';
            previewMeta.textContent = `From: ${fromName ? `${fromName} <${fromAddr}>` : fromAddr} | Received: ${received}`;
            previewBody.innerHTML = msg.body?.content || msg.bodyPreview || '';
            if (subjectInput) {
                subjectInput.value = msg.subject || '(No subject)';
            }
        } catch (error) {
            previewSubject.textContent = 'Unable to load reply preview.';
        }
    }

    function updateReplyModeState() {
        const replySelect = document.getElementById('emailQuoteReplySelect');
        const toInput = document.getElementById('emailQuoteTo');
        const ccInput = document.getElementById('emailQuoteCc');
        const subjectInput = document.getElementById('emailQuoteSubjectInput');
        const isReply = replySelect && replySelect.value;
        if (toInput) {
            toInput.disabled = Boolean(isReply);
        }
        if (ccInput) {
            ccInput.disabled = Boolean(isReply);
        }
        if (subjectInput) {
            if (isReply) {
                if (!subjectInput.disabled) {
                    manualSubjectValue = subjectInput.value;
                }
                subjectInput.disabled = true;
            } else {
                subjectInput.disabled = false;
                if (!subjectInput.value || subjectInput.value === '(No subject)') {
                    subjectInput.value = manualSubjectValue || getDefaultSubject();
                }
            }
        }
        loadReplyEmailPreview(isReply ? replySelect.value : '');
    }

    // 1. Core Function
    function getDefaultSubject() {
        return `Quotation - Parts List ${LIST_ID}`;
    }

    function getDefaultMessageText() {
        const firstName = (typeof CONTACT_FIRST_NAME !== 'undefined' && CONTACT_FIRST_NAME) ? CONTACT_FIRST_NAME : 'there';
        const userName = (typeof CURRENT_USER_NAME !== 'undefined' && CURRENT_USER_NAME) ? CURRENT_USER_NAME : 'your team';
        return `Hi ${firstName}\n\nPlease see our quote below.\n\nThanks\n${userName}`;
    }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value || '';
        return div.innerHTML;
    }

    function formatMessageToHtml(messageText) {
        const escaped = escapeHtml(messageText || '');
        return escaped.replace(/\n/g, '<br>');
    }

    function isMissingNumericValue(value) {
        if (value === null || value === undefined) return true;
        if (typeof value === 'string' && value.trim() === '') return true;
        return Number.isNaN(Number.parseFloat(value));
    }

    function isMissingPositiveValue(value) {
        if (isMissingNumericValue(value)) return true;
        const parsed = Number.parseFloat(value);
        return !Number.isFinite(parsed) || parsed <= 0;
    }

    function isStockLine(lineData) {
        return lineData?.chosen_source_type === 'stock';
    }

    function hasPendingSupplierPartNumberAction(lineData, elements) {
        const supplierQuotedPartNumber = (lineData?.supplier_quoted_part_number || '').toString().trim();
        if (!supplierQuotedPartNumber) return false;

        const requestedPartNumber = getRequestedPartNumber(lineData);
        if (!requestedPartNumber || supplierQuotedPartNumber === requestedPartNumber) return false;

        const displayPartNumber = (
            elements?.displayPartNumber?.value ||
            lineData?.display_part_number ||
            requestedPartNumber
        ).toString().trim();

        return displayPartNumber !== supplierQuotedPartNumber;
    }

    function getEmailQuoteMissingFields() {
        const missingMarginLines = [];
        const missingShippingLines = [];
        const pendingSupplierPnLines = [];

        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements, lastIsNoBid } = cached;

            if (lastIsNoBid) return;

            const quotePrice = Number.parseFloat(elements.quotePriceGbp.value) || 0;
            if (quotePrice <= 0) return;

            const lineNumber = lineData.line_number || '';
            if (isMissingPositiveValue(elements.marginPercent?.value)) {
                missingMarginLines.push(lineNumber);
            }
            if (!isStockLine(lineData) && isMissingPositiveValue(elements.deliveryPerLine?.value)) {
                missingShippingLines.push(lineNumber);
            }
            if (hasPendingSupplierPartNumberAction(lineData, elements)) {
                pendingSupplierPnLines.push(lineNumber);
            }
        });

        return {
            missingMarginLines,
            missingShippingLines,
            pendingSupplierPnLines
        };
    }

    let lastEmailWarningsKey = '';
    let emailWarningsAcknowledged = true;

    function setEmailQuoteActionsEnabled(enabled) {
        const copyBtn = document.getElementById('copyEmailQuoteBtn');
        const copyTableOnlyBtn = document.getElementById('copyEmailQuoteTableOnlyBtn');
        const sendBtn = document.getElementById('sendEmailQuoteBtn');
        if (copyBtn) copyBtn.disabled = !enabled;
        if (copyTableOnlyBtn) copyTableOnlyBtn.disabled = !enabled;
        if (sendBtn) sendBtn.disabled = !enabled;
    }

    function updateEmailQuoteWarnings() {
        const warningBox = document.getElementById('emailQuoteWarnings');
        if (!warningBox) return;
        const ackRow = document.getElementById('emailQuoteWarningAckRow');
        const ackCheckbox = document.getElementById('emailQuoteWarningAck');

        const { missingMarginLines, missingShippingLines, pendingSupplierPnLines } = getEmailQuoteMissingFields();
        const warningsKey = `${missingMarginLines.join(',')}|${missingShippingLines.join(',')}|${pendingSupplierPnLines.join(',')}`;
        if (missingMarginLines.length === 0 && missingShippingLines.length === 0 && pendingSupplierPnLines.length === 0) {
            warningBox.classList.add('d-none');
            warningBox.innerHTML = '';
            if (ackRow) ackRow.classList.add('d-none');
            if (ackCheckbox) ackCheckbox.checked = false;
            emailWarningsAcknowledged = true;
            lastEmailWarningsKey = '';
            setEmailQuoteActionsEnabled(true);
            return;
        }

        if (warningsKey !== lastEmailWarningsKey) {
            emailWarningsAcknowledged = false;
            if (ackCheckbox) ackCheckbox.checked = false;
            lastEmailWarningsKey = warningsKey;
        }

        let html = '<strong>Missing values for quoted lines:</strong><ul class="mb-0">';
        if (missingMarginLines.length) {
            html += `<li>Margin missing on line(s): ${missingMarginLines.join(', ')}</li>`;
        }
        if (missingShippingLines.length) {
            html += `<li>Shipping missing on line(s): ${missingShippingLines.join(', ')}</li>`;
        }
        if (pendingSupplierPnLines.length) {
            html += `<li>Use supplier P/N pending on line(s): ${pendingSupplierPnLines.join(', ')}</li>`;
        }
        html += '</ul>';
        warningBox.innerHTML = html;
        warningBox.classList.remove('d-none');
        if (ackRow) ackRow.classList.remove('d-none');
        setEmailQuoteActionsEnabled(emailWarningsAcknowledged);
    }

    const warningAckCheckbox = document.getElementById('emailQuoteWarningAck');
    if (warningAckCheckbox) {
        warningAckCheckbox.addEventListener('change', () => {
            emailWarningsAcknowledged = warningAckCheckbox.checked;
            setEmailQuoteActionsEnabled(emailWarningsAcknowledged);
        });
    }

    function buildEmailBodyHtml() {
        const messageText = document.getElementById('emailQuoteMessage')?.value || '';
        const messageHtml = formatMessageToHtml(messageText);
        const tableHtml = buildEmailQuoteTable();
        updateEmailQuoteWarnings();
        return `${messageHtml}<br><br>${tableHtml}`;
    }

    function htmlToPlainText(html) {
        return (html || '')
            .replace(/<br\s*\/?>/gi, '\n')
            .replace(/<\/p>/gi, '\n\n')
            .replace(/<[^>]+>/g, '')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    }

    function getEmailQuoteTableHtml() {
        const emailBody = document.getElementById('emailQuoteBody');
        const tableEl = emailBody ? emailBody.querySelector('table') : null;
        if (tableEl) {
            return tableEl.outerHTML;
        }
        return buildEmailQuoteTable();
    }

    async function copyHtmlToClipboard(htmlContent) {
        const plainText = htmlToPlainText(htmlContent);
        await navigator.clipboard.write([
            new ClipboardItem({
                'text/html': new Blob([htmlContent], { type: 'text/html' }),
                'text/plain': new Blob([plainText], { type: 'text/plain' })
            })
        ]);
    }

    async function executeEmailQuote(statusId = null, statusName = null) {
        const mainBtn = document.getElementById('email-quote-btn');
        const originalHtml = mainBtn.innerHTML;

        // Visual Feedback on the main button
        mainBtn.disabled = true;
        mainBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Processing...';

        try {
            // A. Save Unsaved Changes first
            if (hasUnsavedChanges) {
                document.getElementById('save-all-btn').click();
                // Wait a moment for the save to complete (simple delay to ensure DB consistency)
                await new Promise(resolve => setTimeout(resolve, 1000));
            }

            // B. (OPTIONAL) Update Main Parts List Status
            if (statusId) {
                const statusResponse = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/update-status`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status_id: statusId })
                });
                const statusResult = await statusResponse.json();

                if (!statusResult.success) {
                    throw new Error('Failed to update list status: ' + statusResult.message);
                }

                // Optional: Toast notification that status updated
                // alert(`List status updated to ${statusName}`);
            }

            // C. Mark Lines as Quoted (Backend lock-in)
            const markResponse = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/customer-quote/mark-as-quoted`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const markResult = await markResponse.json();

            if (!markResult.success) {
                throw new Error('Failed to prepare quote: ' + markResult.message);
            }

            // D. Sync UI for newly quoted lines so totals stay locked
            if (markResult.marked_count > 0) {
                const quotedOnValue = new Date().toISOString();
                document.querySelectorAll('.quote-row').forEach(row => {
                    const cached = rowCache.get(row);
                    if (!cached) return;

                    const price = parseFloat(cached.elements.quotePriceGbp.value) || 0;
                    const margin = parseFloat(cached.elements.marginPercent.value) || 0;
                    const currentStatus = row.dataset.status || cached.lastStatus || 'created';
                    const isNoBid = row.dataset.isNoBid === '1';

                    if ((currentStatus === 'created' || currentStatus === 'in_progress') && !isNoBid && price > 0 && margin > 0) {
                        row.dataset.status = 'quoted';
                        row.dataset.isNoBid = '0';
                        cached.lineData.quoted_on = quotedOnValue;
                        cached.elements.isNoBid.checked = false;
                        handleRowChange(row, 'quoted', false, true);
                    }
                });
            }

            // E. Generate Table & Show Modal
            const subject = getDefaultSubject();
            autoSelectDiffColumns();
            const subjectInput = document.getElementById('emailQuoteSubjectInput');
            if (subjectInput) {
                subjectInput.value = subject;
                subjectInput.disabled = false;
            }
            const messageInput = document.getElementById('emailQuoteMessage');
            if (messageInput && !messageInput.value) {
                messageInput.value = getDefaultMessageText();
            }
            const toInput = document.getElementById('emailQuoteTo');
            if (toInput && !toInput.value) {
                const contactEmail = (typeof CONTACT_EMAIL !== 'undefined' && CONTACT_EMAIL) ? CONTACT_EMAIL : '';
                if (contactEmail) {
                    toInput.value = contactEmail;
                }
            }
            document.getElementById('emailQuoteBody').innerHTML = `${buildEmailBodyHtml()}<p></p>`;
            loadRelatedEmailsForReply();
            updateReplyModeState();
            new bootstrap.Modal(document.getElementById('emailQuoteModal')).show();

        } catch (error) {
            console.error(error);
            alert('Error: ' + error.message);
        } finally {
            mainBtn.disabled = false;
            mainBtn.innerHTML = originalHtml;
        }
    }

    // 2. Event Listener: Default Button (No Status Change)
    document.getElementById('email-quote-btn').addEventListener('click', function() {
        executeEmailQuote(null, null);
    });

    // 3. Event Listener: Dropdown Items (With Status Change)
    document.querySelectorAll('.email-quote-status-action').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const statusId = this.dataset.statusId;
            const statusName = this.dataset.statusName;

            // Confirm with user? (Optional, but good practice)
            // if(!confirm(`Generate quote and set list status to "${statusName}"?`)) return;

            executeEmailQuote(statusId, statusName);
        });
    });

    document.getElementById('refreshPreviewBtn').addEventListener('click', function() {
        autoSelectDiffColumns();
        document.getElementById('emailQuoteBody').innerHTML = `${buildEmailBodyHtml()}<p></p>`;
    });

    document.getElementById('copyEmailQuoteBtn').addEventListener('click', async function() {
        const bodyHtml = document.getElementById('emailQuoteBody').innerHTML;
        const btn = this;
        const originalHtml = btn.innerHTML;
        try {
            await copyHtmlToClipboard(bodyHtml);
            btn.innerHTML = '<i class="bi bi-check me-1"></i>Copied!';
            btn.className = 'btn btn-success';
            setTimeout(() => { btn.innerHTML = originalHtml; btn.className = 'btn btn-primary'; }, 1500);
        } catch (e) { alert('Copy failed'); }
    });

    const copyTableOnlyBtn = document.getElementById('copyEmailQuoteTableOnlyBtn');
    if (copyTableOnlyBtn) {
        copyTableOnlyBtn.addEventListener('click', async function() {
            const tableHtml = getEmailQuoteTableHtml();
            const btn = this;
            const originalHtml = btn.innerHTML;
            try {
                await copyHtmlToClipboard(tableHtml);
                btn.innerHTML = '<i class="bi bi-check me-1"></i>Copied!';
                btn.className = 'btn btn-success';
                setTimeout(() => { btn.innerHTML = originalHtml; btn.className = 'btn btn-outline-primary'; }, 1500);
            } catch (e) { alert('Copy failed'); }
        });
    }

    const replySelect = document.getElementById('emailQuoteReplySelect');
    if (replySelect) {
        replySelect.addEventListener('change', updateReplyModeState);
    }

    const messageInput = document.getElementById('emailQuoteMessage');
    if (messageInput) {
        messageInput.addEventListener('input', () => {
            const emailBody = document.getElementById('emailQuoteBody');
            if (emailBody) {
                emailBody.innerHTML = `${buildEmailBodyHtml()}<p></p>`;
            }
        });
    }

    const sendEmailBtn = document.getElementById('sendEmailQuoteBtn');
    if (sendEmailBtn) {
        sendEmailBtn.addEventListener('click', async function() {
            const btn = this;
            const subject = document.getElementById('emailQuoteSubjectInput')?.value || '';
            const bodyHtml = document.getElementById('emailQuoteBody')?.innerHTML || '';
            const replyToMessageId = document.getElementById('emailQuoteReplySelect')?.value || '';
            const toEmails = document.getElementById('emailQuoteTo')?.value || '';
            const ccEmails = document.getElementById('emailQuoteCc')?.value || '';
            const copyHarry = Boolean(document.getElementById('emailQuoteCcHarry')?.checked);
            const ccList = ccEmails
                .split(/[;,]/)
                .map(email => email.trim())
                .filter(Boolean);
            const hasHarryAlready = ccList.some(email => email.toLowerCase() === ADMIN_CC_EMAIL);
            if (copyHarry && !hasHarryAlready) {
                ccList.push(ADMIN_CC_EMAIL);
            }
            const finalCcEmails = ccList.join(', ');

            btn.disabled = true;
            const originalHtml = btn.innerHTML;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Sending...';

            try {
                const isReply = Boolean(replyToMessageId);
                const endpoint = isReply
                    ? '/emails/graph/reply'
                    : `/customer-quoting/parts-lists/${LIST_ID}/customer-quote/send-email`;
                const payload = isReply
                    ? {
                        message_id: replyToMessageId,
                        html_body: bodyHtml,
                        cc_emails: finalCcEmails
                    }
                    : {
                        subject: subject,
                        body_html: bodyHtml,
                        to_emails: toEmails,
                        cc_emails: finalCcEmails,
                        reply_to_message_id: replyToMessageId
                    };
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.error || result.message || 'Failed to send email');
                }
                btn.innerHTML = '<i class="bi bi-check-circle me-1"></i>Sent!';
                btn.classList.remove('btn-success');
                btn.classList.add('btn-outline-success');
                setTimeout(() => {
                    btn.innerHTML = originalHtml;
                    btn.classList.remove('btn-outline-success');
                    btn.classList.add('btn-success');
                    btn.disabled = false;
                }, 2000);
            } catch (error) {
                alert(error.message || 'Failed to send email');
                btn.innerHTML = originalHtml;
                btn.disabled = false;
            }
        });
    }

    // Recalculate Base Costs
    document.getElementById('calculate-base-costs-btn').addEventListener('click', async function() {
        const btn = this;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Recalculating...';
        try {
            const response = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/customer-quote/calculate-base-costs`, { method: 'POST' });
            const result = await response.json();
            if (result.success) window.location.reload();
            else alert('Error: ' + result.message);
        } catch (error) { alert('Failed'); }
        finally { btn.disabled = false; if(!btn.innerHTML.includes('check')) btn.innerHTML = '<i class="bi bi-calculator me-1"></i>Recalculate Base Costs'; }
    });

    // Calculate Delivery
    document.getElementById('calculate-delivery-btn').addEventListener('click', async function() {
        const btn = this;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Calculating...';
        try {
            const response = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/customer-quote/calculate-delivery-costs`, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
            const result = await response.json();
            if (result.success) window.location.reload();
            else alert('Error: ' + result.message);
        } catch (error) { alert('Failed'); }
        finally { btn.disabled = false; btn.innerHTML = '<i class="bi bi-truck me-1"></i>Calculate Delivery'; }
    });

      // Bulk Apply Margin
      const bulkMarginModalElement = document.getElementById('bulkMarginModal');
      const bulkMarginModalInstance = bulkMarginModalElement ? bootstrap.Modal.getOrCreateInstance(bulkMarginModalElement) : null;

      document.getElementById('bulk-apply-margin-btn').addEventListener('click', function() {
          if (!bulkMarginModalInstance) return;
          document.getElementById('margin-input-step').style.display = 'block';
          document.getElementById('margin-applying-step').style.display = 'none';
          document.getElementById('margin-modal-footer').style.display = 'flex';
          bulkMarginModalInstance.show();
      });

      document.getElementById('apply-margin-btn').addEventListener('click', async function() {
          const margin = parseFloat(document.getElementById('bulk-margin-input').value);
          const scope = document.querySelector('input[name="bulk-margin-scope"]:checked').value;
          if (isNaN(margin) || margin < 0 || margin >= 100) return alert('Invalid margin');

          document.getElementById('margin-input-step').style.display = 'none';
          document.getElementById('margin-modal-footer').style.display = 'none';
          document.getElementById('margin-applying-step').style.display = 'block';

          try {
              const response = await fetch(`/customer-quoting/parts-lists/${LIST_ID}/customer-quote/bulk-margin-apply`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ margin_percent: margin, scope: scope })
              });
              const result = await response.json();
              if (result.success) {
                  document.getElementById('margin-applying-step').style.display = 'none';
                  document.getElementById('margin-modal-footer').style.display = 'flex';
                  bulkMarginModalInstance?.hide();
                  window.location.reload();
              } else { alert('Error: ' + result.message); window.location.reload(); }
          } catch (e) { alert('Failed'); window.location.reload(); }
      });

      function setupDuplicateLineButtons() {
        document.querySelectorAll('.duplicate-line-btn').forEach(btn => {
            btn.addEventListener('click', async function() {
                const lineId = this.dataset.lineId;
                if (!lineId) return;
                const originalHtml = this.innerHTML;
                this.disabled = true;
                this.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
                try {
                    const response = await fetch(`/parts_list/parts-lists/${LIST_ID}/lines/${lineId}/duplicate`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ line_type: 'price_break' })
                    });
                    const result = await response.json();
                    if (!result.success) {
                        throw new Error(result.message || 'Failed to duplicate line');
                    }
                    window.location.reload();
                } catch (error) {
                    this.disabled = false;
                    this.innerHTML = originalHtml;
                    alert(error.message || 'Failed to duplicate line');
                }
            });
        });
    }

    function setupCopyPartNumberButtons() {
        document.querySelectorAll('.copy-part-number-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const partNumber = this.dataset.partNumber;
                copyPartNumberToClipboard(partNumber, this);
            });
        });
    }

    function copyPartNumberToClipboard(text, buttonEl) {
        if (!text) {
            alert('No part number found to copy.');
            return;
        }

        const setFeedback = () => {
            if (!buttonEl) return;
            const original = buttonEl.innerHTML;
            buttonEl.innerHTML = '<i class="bi bi-check2"></i>';
            buttonEl.classList.remove('btn-outline-secondary');
            buttonEl.classList.add('btn-success');
            setTimeout(() => {
                buttonEl.innerHTML = original;
                buttonEl.classList.remove('btn-success');
                buttonEl.classList.add('btn-outline-secondary');
            }, 1500);
        };

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(setFeedback).catch(() => {
                alert('Unable to copy part number.');
            });
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
            setFeedback();
        } catch (error) {
            console.error('Copy failed:', error);
            alert('Unable to copy part number.');
        } finally {
            document.body.removeChild(textarea);
        }
    }

    // --- EXPANDABLE DETAIL ROWS ---
    function setupExpandToggle() {
        document.querySelectorAll('.expand-toggle-btn').forEach(btn => {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                const lineId = this.dataset.lineId;
                const detailRow = document.querySelector(`.detail-row[data-parent-line-id="${lineId}"]`);

                if (detailRow) {
                    const isExpanded = detailRow.classList.toggle('expanded');
                    this.classList.toggle('expanded', isExpanded);

                    // Update icon
                    const icon = this.querySelector('i');
                    if (icon) {
                        icon.style.transform = isExpanded ? 'rotate(90deg)' : '';
                    }
                }
            });
        });

        const tableBody = document.getElementById('quoteTableBody');
        if (tableBody) {
            tableBody.addEventListener('click', function(e) {
                if (e.target.closest('.detail-row')) return;
                if (e.target.closest('.expand-toggle-btn')) return;
                if (e.target.closest('input, select, textarea, button, a, .btn')) return;

                const row = e.target.closest('.quote-row');
                if (!row) return;

                const lineId = row.dataset.lineId;
                const detailRow = document.querySelector(`.detail-row[data-parent-line-id="${lineId}"]`);
                if (!detailRow) return;

                const isExpanded = detailRow.classList.toggle('expanded');
                const toggleBtn = row.querySelector('.expand-toggle-btn');
                if (toggleBtn) {
                    toggleBtn.classList.toggle('expanded', isExpanded);
                    const icon = toggleBtn.querySelector('i');
                    if (icon) {
                        icon.style.transform = isExpanded ? 'rotate(90deg)' : '';
                    }
                }
            });
        }

        // Also handle editable fields in detail rows
        document.querySelectorAll('.detail-row .editable-field').forEach(input => {
            input.addEventListener('change', function() {
                const lineId = this.dataset.lineId;
                const field = this.dataset.field;
                const mainRow = document.querySelector(`.quote-row[data-line-id="${lineId}"]`);

                if (mainRow) {
                    // Mark as changed
                    this.classList.add('changed-input');
                    hasUnsavedChanges = true;
                    updateSaveButtonState();

                    // Update the cached lineData if available
                    const cached = rowCache.get(mainRow);
                    if (cached && cached.lineData) {
                        cached.lineData[field] = this.value;
                    }
                }
            });
        });
    }

    // START
    setSummaryCurrencyLabels();
    initializeTable();
    setupDuplicateLineButtons();
    setupCopyPartNumberButtons();
    setupExpandToggle();
});
