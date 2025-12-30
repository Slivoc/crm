document.addEventListener('DOMContentLoaded', function() {
    let hasUnsavedChanges = false;
    let summaryUpdateTimer = null;

    const BASE_CURRENCY_ID = (() => {
        const baseCurrency = CURRENCIES.find(c => (c.currency_code || '').toUpperCase() === 'GBP');
        return baseCurrency ? baseCurrency.id : 3;
    })();
    let displayCurrencyId = (typeof CUSTOMER_CURRENCY_ID !== 'undefined' && CUSTOMER_CURRENCY_ID) ? CUSTOMER_CURRENCY_ID : BASE_CURRENCY_ID;
    const currencySelect = document.getElementById('quoteCurrencySelect');
    if (currencySelect) {
        const initialId = parseInt(currencySelect.value, 10);
        displayCurrencyId = Number.isFinite(initialId) ? initialId : BASE_CURRENCY_ID;
        currencySelect.addEventListener('change', function() {
            const nextId = parseInt(this.value, 10);
            displayCurrencyId = Number.isFinite(nextId) ? nextId : BASE_CURRENCY_ID;
            updateSummaryDisplay();
            const emailBody = document.getElementById('emailQuoteBody');
            if (emailBody) {
                const customerIdValue = (typeof CUSTOMER_SYSTEM_CODE !== 'undefined' && CUSTOMER_SYSTEM_CODE) ? CUSTOMER_SYSTEM_CODE : '';
                const customerIdHtml = customerIdValue ? `<p><strong>Customer ID:</strong> ${customerIdValue}</p>` : '';
                emailBody.innerHTML = `${customerIdHtml}${buildEmailQuoteTable()}<p></p>`;
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

            // Cache Elements
            const elements = {
                chosenQty: row.querySelector('[data-field="chosen_qty"]'),
                deliveryPerLine: row.querySelector('[data-field="delivery_per_line"]'),
                marginPercent: row.querySelector('[data-field="margin_percent"]'),
                quotePriceGbp: row.querySelector('[data-field="quote_price_gbp"]'),
                deliveryPerUnit: row.querySelector('.delivery-per-unit'),
                baseCostCell: row.querySelector('.base-cost-gbp'),
                lineTotalCost: row.querySelector('.line-total-cost'),
                lineTotalQuote: row.querySelector('.line-total-quote'),
                isNoBid: row.querySelector('[data-field="is_no_bid"]'),
                statusBtn: row.querySelector('.status-btn'),
                lineNotes: row.querySelector('[data-field="line_notes"]'),
                leadDays: row.querySelector('[data-field="lead_days"]'),
                manufacturer: row.querySelector('[data-field="manufacturer"]'),
                displayPartNumber: row.querySelector('[data-field="display_part_number"]'),
                standardCondition: row.querySelector('[data-field="standard_condition"]'),
                standardCerts: row.querySelector('[data-field="standard_certs"]'),
                calcBaseBtn: row.querySelector('.line-calc-btn[data-calc="base"]'),
                calcDeliveryBtn: row.querySelector('.line-calc-btn[data-calc="delivery"]'),
                calcMarginBtn: row.querySelector('.line-calc-btn[data-calc="margin"]')
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
            elements.lineTotalCost.textContent = 'N/A';
            elements.lineTotalQuote.textContent = 'N/A';
            if (elements.deliveryPerUnit) elements.deliveryPerUnit.textContent = 'N/A';
            row.classList.add('no-bid-row');
            row.classList.remove('quoted-row', 'below-minimum');
        } else {
                        const displayCurrencyId = getDisplayCurrencyId();
            const costDisplay = convertFromGbp(fins.cost, displayCurrencyId);
            const quoteDisplay = convertFromGbp(fins.quote, displayCurrencyId);
            const deliveryDisplay = convertFromGbp(fins.deliveryPerUnit, displayCurrencyId);

            elements.lineTotalCost.textContent = formatCurrency(costDisplay, displayCurrencyId);
            elements.lineTotalQuote.textContent = formatCurrency(quoteDisplay, displayCurrencyId);
            if (elements.deliveryPerUnit) elements.deliveryPerUnit.textContent = formatCurrency(deliveryDisplay, displayCurrencyId);

            row.classList.remove('no-bid-row');
            if (status === 'quoted') row.classList.add('quoted-row');
            else row.classList.remove('quoted-row');

            if (fins.isBelowMin) row.classList.add('below-minimum');
            else row.classList.remove('below-minimum');
        }

        // Update Status Button
        const btn = elements.statusBtn;
        btn.className = 'btn btn-sm w-100 status-btn'; // Reset

        if (status === 'quoted') {
            btn.classList.add('btn-success');
            btn.innerHTML = '<i class="bi bi-check-circle me-1"></i>Quoted';
        } else if (status === 'no_bid' || isNoBid) {
            btn.classList.add('btn-warning');
            btn.innerHTML = '<i class="bi bi-x-circle me-1"></i>No Bid';
        } else {
            btn.classList.add('btn-outline-secondary');
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

        summaryElements.createdLinesCount.textContent = globalState.createdCount;
        summaryElements.quotedLinesCount.textContent = globalState.quotedCount;
        summaryElements.noBidLinesCount.textContent = globalState.noBidCount;

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
            else if (cached.lastStatus === 'quoted') globalState.quotedCount--;
            else globalState.createdCount--;

            // Increment new
            if (isNoBid || status === 'no_bid') globalState.noBidCount++;
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
            if (!skipUnsavedFlag) {
                markUnsaved();
            }
        });
    }

    // --- 6. EVENT LISTENERS ---

    document.getElementById('quoteTableBody').addEventListener('click', async function(e) {
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
        const statuses = ['created', 'quoted', 'no_bid'];
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

            const conditionVal = (elements.standardCondition && elements.standardCondition.value.trim()) || (lineData.supplier_condition_code || '').toString().trim();
            const certsVal = (elements.standardCerts && elements.standardCerts.value.trim()) || (lineData.supplier_certifications || '').toString().trim();
            const notesVal = elements.lineNotes ? elements.lineNotes.value.trim() : '';
            const manufacturerVal = (elements.manufacturer && elements.manufacturer.value.trim()) || (lineData.manufacturer || '').toString().trim();

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

    // Build Purchasing Instructions Table
    function buildPurchasingInstructionsTable() {
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
    </tr>
  </thead>
  <tbody>`;

        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements, lastIsNoBid } = cached;

            if (lastIsNoBid) return;
            if (!lineData.chosen_supplier_name || lineData.chosen_supplier_name === '-') return;

            const effectiveQty = parseFloat(elements.chosenQty.value) || lineData.quantity;
            const unitCost = parseFloat(lineData.chosen_cost || 0);
            if (unitCost <= 0) return;

            const lineTotal = unitCost * effectiveQty;
            const currency = lineData.chosen_currency_code || 'GBP';
            const leadDays = lineData.chosen_lead_days || '';
            const cellStyle = 'padding:6px 8px;border:1px solid #dee2e6;';

            html += `<tr>
              <td align="left" style="${cellStyle}">${lineData.line_number || ''}</td>
              <td align="left" style="${cellStyle}">${getRequestedPartNumber(lineData)}</td>
              <td align="right" style="${cellStyle}">${effectiveQty || ''}</td>
              <td align="left" style="${cellStyle}">${lineData.chosen_supplier_name}</td>
              <td align="right" style="${cellStyle}">${unitCost.toFixed(2)}</td>
              <td align="left" style="${cellStyle}">${currency}</td>
              <td align="right" style="${cellStyle}">${lineTotal.toFixed(2)}</td>
              <td align="right" style="${cellStyle}">${leadDays}</td>
            </tr>`;
        });
        html += `</tbody></table>`;
        return html;
    }

   // Build Email Quote Table (Fixed: Shows No Bids, Hides Empty Prices)
    function buildEmailQuoteTable() {
        const displayCurrencyId = getDisplayCurrencyId();
        const displayCurrencyCode = getCurrencyCode(displayCurrencyId);
        // 1. Get Selected Columns
        const selectedCols = {};
        document.querySelectorAll('.email-col-check').forEach(cb => selectedCols[cb.value] = cb.checked);

        // 2. Build Headers
        let headers = '';
        const hStyle = 'padding:4px 6px;border-bottom:1px solid #dee2e6;';
        if (selectedCols.line) headers += `<th align="left" style="${hStyle}">Line</th>`;
        if (selectedCols.requested_pn) headers += `<th align="left" style="${hStyle}">Requested P/N</th>`;
        if (selectedCols.quoted_pn) headers += `<th align="left" style="${hStyle}">Quoted P/N</th>`;
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
          <thead><tr style="background:#f8f9fa;">${headers}</tr></thead><tbody>`;

        // 4. Loop Through Rows
        document.querySelectorAll('.quote-row').forEach(row => {
            const cached = rowCache.get(row);
            if (!cached) return;
            const { lineData, elements, lastIsNoBid } = cached;

            const quotePrice = parseFloat(elements.quotePriceGbp.value) || 0;

            const quotePriceDisplay = convertFromGbp(quotePrice, displayCurrencyId);

            // --- FILTER LOGIC ---
            // Hide row ONLY IF it is NOT "No Bid" AND Price is 0.
            // (This keeps "No Bid" lines visible, but hides unquoted lines)
            if (!lastIsNoBid && quotePrice <= 0) return;

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
            }

            // Highlights
            const requestedPN = getRequestedPartNumber(lineData);
            const quotedPN = elements.displayPartNumber ? elements.displayPartNumber.value.trim() : requestedPN;

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
            if (selectedCols.requested_qty) html += `<td align="right" style="${qtyHighlightStyle}">${requestedQty || ''}</td>`;
            if (selectedCols.qty) html += `<td align="right" style="${qtyHighlightStyle}">${isQtyDifferent && !selectedCols.requested_qty ? effectiveQty + ' *' : effectiveQty}</td>`;
            if (selectedCols.unit_price) html += `<td align="right" style="${rowStyle}">${unitPriceDisplay}</td>`;
            if (selectedCols.line_total) html += `<td align="right" style="${rowStyle}">${lineTotalDisplay}</td>`;
            if (selectedCols.lead_days) html += `<td align="left" style="${rowStyle}">${elements.leadDays.value || ''}</td>`;
            if (selectedCols.quoted_on) html += `<td align="left" style="${rowStyle}">${formatQuotedOn(lineData.quoted_on)}</td>`;
            const manufacturerVal = (elements.manufacturer && elements.manufacturer.value.trim()) || lineData.manufacturer || '';

            if (selectedCols.manufacturer) html += `<td align="left" style="${rowStyle}">${manufacturerVal}</td>`;
            if (selectedCols.condition) html += `<td align="left" style="${rowStyle}">${conditionValue || ''}</td>`;
            if (selectedCols.certs) html += `<td align="left" style="${rowStyle}">${certsValue || ''}</td>`;
            if (selectedCols.notes) html += `<td align="left" style="${rowStyle}">${elements.lineNotes.value || ''}</td>`;
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
                line_notes: elements.lineNotes.value,
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
        const tableHtml = buildPurchasingInstructionsTable();
        document.getElementById('purchasingInstructionsBody').innerHTML = tableHtml;
        new bootstrap.Modal(document.getElementById('purchasingModal')).show();
    });

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

        // 4. Calculate next status (Cycle: Created -> Quoted -> No Bid)
        const statuses = ['created', 'quoted', 'no_bid'];
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

    // 1. Core Function
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

                    if (currentStatus === 'created' && !isNoBid && price > 0 && margin > 0) {
                        row.dataset.status = 'quoted';
                        row.dataset.isNoBid = '0';
                        cached.lineData.quoted_on = quotedOnValue;
                        cached.elements.isNoBid.checked = false;
                        handleRowChange(row, 'quoted', false, true);
                    }
                });
            }

            // E. Generate Table & Show Modal
            const customerIdValue = (typeof CUSTOMER_SYSTEM_CODE !== 'undefined' && CUSTOMER_SYSTEM_CODE) ? CUSTOMER_SYSTEM_CODE : '';
            const subject = customerIdValue ? `Quotation - Customer ${customerIdValue} - Parts List ${LIST_ID}` : `Quotation - Parts List ${LIST_ID}`;
            autoSelectDiffColumns();
            const tableHtml = buildEmailQuoteTable();
            document.getElementById('emailQuoteSubject').textContent = subject;

            document.getElementById('emailQuoteBody').innerHTML = `${tableHtml}<p></p>`;
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
        document.getElementById('emailQuoteBody').innerHTML = `${buildEmailQuoteTable()}<p></p>`;
    });

    document.getElementById('copyEmailQuoteBtn').addEventListener('click', async function() {
        const bodyHtml = document.getElementById('emailQuoteBody').innerHTML;
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
    document.getElementById('bulk-apply-margin-btn').addEventListener('click', function() {
        const modal = new bootstrap.Modal(document.getElementById('bulkMarginModal'));
        document.getElementById('margin-input-step').style.display = 'block';
        document.getElementById('margin-applying-step').style.display = 'none';
        document.getElementById('margin-success-step').style.display = 'none';
        document.getElementById('margin-modal-footer').style.display = 'flex';
        modal.show();
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
                document.getElementById('margin-success-step').style.display = 'block';
                document.getElementById('success-message').textContent = `Updated ${result.updated_count} lines`;
            } else { alert('Error: ' + result.message); window.location.reload(); }
        } catch (e) { alert('Failed'); window.location.reload(); }
    });

    document.getElementById('reload-after-apply-btn').addEventListener('click', function() { window.location.reload(); });

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

    // START
    setSummaryCurrencyLabels();
    initializeTable();
    setupDuplicateLineButtons();
    setupCopyPartNumberButtons();
});
