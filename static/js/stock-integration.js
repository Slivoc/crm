// Stock balance integration code

// Function to fetch stock balance for a part number
function fetchStockBalance(basePartNumber) {
  return fetch(`/stock/balances/${encodeURIComponent(basePartNumber)}`)
    .then(response => {
      if (!response.ok) {
        throw new Error(`Failed to fetch stock balance: ${response.status}`);
      }
      return response.json();
    })
    .then(data => {
      // Calculate total available quantity across all movements
      const totalAvailable = data.reduce((sum, item) => sum + item.available_quantity, 0);
      return {
        total: totalAvailable,
        details: data
      };
    })
    .catch(error => {
      console.error(`Error fetching stock balance for ${basePartNumber}:`, error);
      return { total: 0, details: [] };
    });
}

// Function to update all stock cells in the table
function updateAllStockCells() {
  // Find all part number cells
  const partNumberCells = document.querySelectorAll('[id^="display_part_number_"]');

  partNumberCells.forEach(cell => {
    const lineId = cell.id.split('_')[3];
    const tr = cell.closest('tr');

    // Try to get base_part_number from data attribute first
    const basePartNumber = tr.dataset.basePartNumber;
    const partNumber = cell.textContent.trim();

    if (basePartNumber) {
      updateStockCell(lineId, basePartNumber);
    } else {
      updateStockCell(lineId, partNumber);
    }
  });
}

// Function to update a single stock cell
function updateStockCell(lineId, partNumber, basePartNumber) {
  // If basePartNumber was not provided, try to extract it from partNumber
  const basePart = basePartNumber || (partNumber ? partNumber.replace(/[\/\-].+$/, '').trim() : null);

  if (!basePart) {
    console.error(`Base part number not found for line ${lineId}`);
    return;
  }

  const stockCell = document.querySelector(`tr:has(span#display_part_number_${lineId}) td:nth-child(4)`);

  if (!stockCell) {
    console.error(`Stock cell not found for line ${lineId}`);
    return;
  }

  // Show loading indicator
  stockCell.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';

  fetchStockBalance(basePart)
    .then(stockData => {
      // Update the stock cell with the total available quantity
      const total = stockData.total;

      // Create stock display with click handler to show details
      if (total > 0) {
        stockCell.innerHTML = `
          <span class="badge bg-success stock-quantity"
                data-line-id="${lineId}"
                data-part-number="${partNumber}"
                onclick="openStockDetailsModal('${partNumber}')">
            ${total}
          </span>`;
      } else {
        stockCell.innerHTML = `<span class="badge bg-secondary">0</span>`;
      }

      // Store the stock details in a data attribute for later use
      stockCell.dataset.stockDetails = JSON.stringify(stockData.details);
    })
    .catch(error => {
      console.error(`Error updating stock for ${partNumber}:`, error);
      stockCell.innerHTML = '<span class="badge bg-warning">Error</span>';
    });
}

// Function to open stock details modal
function openStockDetailsModal(partNumber) {
  // Find or create the modal
  let stockModal = document.getElementById('stockDetailsModal');

  if (!stockModal) {
    // Create modal if it doesn't exist
    const modalHtml = `
      <div class="modal fade" id="stockDetailsModal" tabindex="-1" aria-labelledby="stockDetailsModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title" id="stockDetailsModalLabel">Stock Details: <span id="modalPartNumber"></span></h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
              <div class="table-responsive">
                <table class="table table-striped table-hover">
                  <thead>
                    <tr>
                      <th>Part Number</th>
                      <th>Date Code</th>
                      <th>Receipt Date</th>
                      <th>Cost</th>
                      <th>Original Qty</th>
                      <th>Available Qty</th>
                      <th>Reference</th>
                    </tr>
                  </thead>
                  <tbody id="stockDetailsTableBody">
                    <!-- Stock details will be populated here -->
                  </tbody>
                  <tfoot>
                    <tr class="table-dark">
                      <td colspan="5"><strong>Total Available</strong></td>
                      <td id="totalAvailableQty"><strong>0</strong></td>
                      <td></td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
            </div>
          </div>
        </div>
      </div>
    `;

    // Add the modal to the document
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    stockModal = document.getElementById('stockDetailsModal');
  }

  // Set the part number in the modal title
  document.getElementById('modalPartNumber').textContent = partNumber;

  // Show loading indicator
  document.getElementById('stockDetailsTableBody').innerHTML = `
    <tr>
      <td colspan="7" class="text-center">
        <div class="spinner-border text-primary" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
        <p class="text-muted mt-3 mb-0">Loading stock details...</p>
      </td>
    </tr>
  `;

  // Load stock details
  fetchStockBalance(partNumber)
    .then(stockData => {
      populateStockDetailsTable(stockData.details);
      document.getElementById('totalAvailableQty').textContent = stockData.total;
    })
    .catch(error => {
      console.error(`Error fetching stock details for ${partNumber}:`, error);
      document.getElementById('stockDetailsTableBody').innerHTML = `
        <tr>
          <td colspan="7" class="text-center text-danger">
            Error loading stock details. Please try again.
          </td>
        </tr>
      `;
    });

  // Show the modal
  const bsModal = new bootstrap.Modal(stockModal);
  bsModal.show();
}

// Function to populate stock details table
function populateStockDetailsTable(details) {
  const tableBody = document.getElementById('stockDetailsTableBody');
  tableBody.innerHTML = '';

  if (details.length === 0) {
    tableBody.innerHTML = `
      <tr>
        <td colspan="7" class="text-center">No stock available</td>
      </tr>
    `;
    return;
  }

  details.forEach(item => {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${item.part_number}</td>
      <td>${item.datecode || 'N/A'}</td>
      <td>${formatDate(item.receipt_date)}</td>
      <td>${formatCurrency(item.cost_per_unit)}</td>
      <td>${item.original_quantity}</td>
      <td>${item.available_quantity}</td>
      <td>${item.reference || 'N/A'}</td>
    `;
    tableBody.appendChild(row);
  });
}

// Helper function to format date
function formatDate(dateString) {
  if (!dateString) return 'N/A';

  try {
    const date = new Date(dateString);
    return date.toLocaleDateString();
  } catch (e) {
    return dateString;
  }
}

// Helper function to format currency
function formatCurrency(amount) {
  if (amount === null || amount === undefined) return 'N/A';

  try {
    return '€' + parseFloat(amount).toFixed(2);
  } catch (e) {
    return amount;
  }
}
