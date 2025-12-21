/**
 * Bootstrap Modal Fix for Customer Modal
 *
 * This file fixes issues with the customer modal including:
 * 1. Missing safeShowModal function
 * 2. Field ID mismatches
 * 3. Proper data population
 *
 * Add this code to your bootstrap-modal-fix.js file or create a new JS file
 * and include it after bootstrap.js but before your other modal scripts.
 */

// First, make sure safeShowModal exists
if (typeof window.safeShowModal !== 'function') {
  window.safeShowModal = function(modalElement) {
    console.log("Using safeShowModal function");

    try {
      // Try Bootstrap 5 way first
      if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
        // Create a new instance instead of trying to get an existing one
        // This avoids the DATA_KEY error
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
        return true;
      }

      // Fallback to jQuery if available
      if (typeof $ !== 'undefined') {
        $(modalElement).modal('show');
        return true;
      }

      // Manual fallback
      modalElement.classList.add('show');
      modalElement.style.display = 'block';
      document.body.classList.add('modal-open');

      // Create backdrop if needed
      let backdrop = document.querySelector('.modal-backdrop');
      if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop fade show';
        document.body.appendChild(backdrop);
      }

      return true;
    } catch (error) {
      console.error('Error in safeShowModal:', error);

      // Last resort
      try {
        modalElement.classList.add('show');
        modalElement.style.display = 'block';
        return true;
      } catch (e) {
        console.error('Failed to show modal:', e);
        return false;
      }
    }
  };
  console.log("Added missing safeShowModal function");
}

// Fix showModal to use our safe implementation
window.showModal = function(modalId) {
  console.log(`Showing modal with ID: ${modalId}`);
  const modalElement = document.getElementById(modalId);

  if (!modalElement) {
    console.error(`Modal element #${modalId} not found`);
    return false;
  }

  // Use Bootstrap's Modal directly without getInstance
  try {
    if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
      const modal = new bootstrap.Modal(modalElement);
      modal.show();
      return true;
    }

    // Fallback to jQuery
    if (typeof $ !== 'undefined') {
      $(modalElement).modal('show');
      return true;
    }

    // Manual fallback
    modalElement.classList.add('show');
    modalElement.style.display = 'block';
    document.body.classList.add('modal-open');

    // Create backdrop if needed
    let backdrop = document.querySelector('.modal-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'modal-backdrop fade show';
      document.body.appendChild(backdrop);
    }

    return true;
  } catch (error) {
    console.error(`Error showing modal #${modalId}:`, error);

    // Last resort
    modalElement.classList.add('show');
    modalElement.style.display = 'block';
    return true;
  }
};

// Create a direct data population function
window.populateCustomerModalData = function(data) {
  console.log("Directly populating customer modal data:", data);

  // Ensure we have data to populate
  if (!data) {
    console.warn("No data provided for modal population");
    return false;
  }

  const modal = document.getElementById('customerModal');
  if (!modal) {
    console.error("Customer modal not found");
    return false;
  }

  // Get all the fields using the correct IDs
  const nameField = modal.querySelector('#editCustomerName');
  const descField = modal.querySelector('#editCustomerDescription');
  const revenueField = modal.querySelector('#editEstimatedRevenue');
  const countryField = modal.querySelector('#editCountry');
  const salespersonField = modal.querySelector('#editSalesperson');
  const tagInput = modal.querySelector('#selectedTagId');

  // Log field availability for debugging
  console.log("Form field availability:", {
    nameField: !!nameField,
    descField: !!descField,
    revenueField: !!revenueField,
    countryField: !!countryField,
    salespersonField: !!salespersonField,
    tagInput: !!tagInput
  });

  // Set values if fields exist
  if (nameField) nameField.value = data.name || '';
  if (descField) descField.value = data.description || '';
  if (revenueField) revenueField.value = data.revenue || '';
  if (countryField) countryField.value = data.country || '';
  if (salespersonField && data.salesperson_id) salespersonField.value = data.salesperson_id;

  // Set tag ID if provided
  if (tagInput && data.tagId) {
    tagInput.value = data.tagId;
  }

  return true;
};

// Fix the openCustomerModal function (guard against double-load)
if (!window.__customerModalFixApplied) {
  window.__customerModalFixApplied = true;
  window._originalOpenCustomerModal = window.openCustomerModal;
  window.openCustomerModal = function(data) {
  try {
    console.log("Opening universal customer modal with data:", data);

    const modalId = 'customerModal';
    const modalElement = document.getElementById(modalId);

    if (!modalElement) {
      console.warn(`Modal #${modalId} not found in the DOM`);
      return false;
    }

    // Reset the state first
    if (typeof resetModalState === 'function') {
      resetModalState();
    }

    // Show the modal first
    let result = false;

    try {
      // First try using showModal
      if (typeof window.showModal === 'function') {
        result = window.showModal(modalId);
      } else {
        // Otherwise create a new bootstrap modal
        const bsModal = new bootstrap.Modal(modalElement);
        bsModal.show();
        result = true;
      }
    } catch (error) {
      console.error("Error showing modal:", error);

      // Manual fallback
      modalElement.classList.add('show');
      modalElement.style.display = 'block';
      result = true;
    }

    // After showing the modal, populate data with slight delay to ensure DOM is ready
    setTimeout(() => {
      window.populateCustomerModalData(data);
    }, 50);

    return result;
  } catch (error) {
    console.error('Error in openCustomerModal:', error);
    return false;
  }
  };

  // Fix submitCustomer to use correct field IDs
  window._originalSubmitCustomer = window.submitCustomer;
  window.submitCustomer = function() {
  const modal = document.getElementById('customerModal');

  if (!modal) {
    console.error("Customer modal not found");
    return;
  }

  const submitBtn = modal.querySelector('button[onclick="submitCustomer()"]');
  const originalContent = submitBtn ? submitBtn.innerHTML : '';

  // Show loading state
  if (submitBtn) {
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Adding...';
    submitBtn.disabled = true;
  }

  // Prepare form data with CORRECT FIELD IDs
  const formData = {
    name: modal.querySelector('#editCustomerName')?.value || '',
    description: modal.querySelector('#editCustomerDescription')?.value || '',
    estimated_revenue: parseInt(modal.querySelector('#editEstimatedRevenue')?.value) || 0,
    country: modal.querySelector('#editCountry')?.value?.toUpperCase() || '',
    salesperson_id: modal.querySelector('#editSalesperson')?.value || '',
    tag_id: modal.querySelector('#selectedTagId')?.value || null,
    payment_terms: modal.querySelector('#paymentTerms')?.value || 'Pro-forma',
    incoterms: modal.querySelector('#incoterms')?.value || 'EXW'
  };

  console.log("Submitting customer data:", formData);

  // Submit the form using the original fetch call
  fetch('/customers/add_suggested', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(formData)
  })
  .then(response => response.json())
  .then(data => {
    if (data.success) {
      // Store the customer ID
      const hiddenCustomerId = document.createElement('input');
      hiddenCustomerId.type = 'hidden';
      hiddenCustomerId.id = 'currentCustomerId';
      hiddenCustomerId.value = data.customer_id;
      modal.querySelector('#apolloSection').appendChild(hiddenCustomerId);

      // Show success message
      const successAlert = document.createElement('div');
      successAlert.className = 'alert alert-success';
      successAlert.innerHTML = '<i class="bi bi-check-circle"></i> Customer added successfully!';
      modal.querySelector('#customerInfoSection').appendChild(successAlert);

      // Refresh the customers table if function exists
      if (typeof refreshCustomersTable === 'function') {
        refreshCustomersTable();
      }

      // Continue with Apollo section transition
      setTimeout(() => {
        modal.querySelector('#step2Circle').classList.replace('bg-secondary', 'bg-primary');
        modal.querySelector('#step1to2Line').classList.add('bg-primary');
        modal.querySelector('#customerInfoSection').classList.add('d-none');
        modal.querySelector('#apolloSection').classList.remove('d-none');

        // Update modal footer
        modal.querySelector('#modalFooter').innerHTML = `
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        `;

        // Set up Apollo search
        const searchButton = modal.querySelector('#apolloSearchButton');
        if (searchButton) searchButton.disabled = false;

        const searchInput = modal.querySelector('#companySearchInput');
        if (searchInput) searchInput.value = formData.name;

        // Trigger initial search
        if (typeof performApolloSearch === 'function') {
          performApolloSearch(data.customer_id);
        }
      }, 1000);
    } else {
      throw new Error(data.error || 'Failed to add customer');
    }
  })
  .catch(error => {
    console.error('Error:', error);
    const errorAlert = document.createElement('div');
    errorAlert.className = 'alert alert-danger';
    errorAlert.innerHTML = `<i class="bi bi-exclamation-triangle"></i> ${error.message || 'An error occurred while adding the customer'}`;
    modal.querySelector('#customerInfoSection').appendChild(errorAlert);
  })
  .finally(() => {
    // Reset button state
    if (submitBtn) {
      submitBtn.innerHTML = originalContent;
      submitBtn.disabled = false;
    }
  });
  };
}

// Fix populateSalespeopleDropdown to use the correct selector
const originalPopulateSalespeopleDropdown = window.populateSalespeopleDropdown;
window.populateSalespeopleDropdown = function() {
  fetch('/customers/api/salespeople')
    .then(response => response.json())
    .then(data => {
      if (data.success && Array.isArray(data.salespeople)) {
        // Use the correct selector from our diagnosis
        const select = document.querySelector('#customerModal #editSalesperson');
        if (!select) {
          console.warn('#editSalesperson not found, check selector');
          return;
        }

        // Keep the first option and add new ones
        const firstOption = select.options[0];
        select.innerHTML = '';
        select.appendChild(firstOption);

        data.salespeople.forEach(sp => {
          const option = document.createElement('option');
          option.value = sp.id;
          option.textContent = sp.name;
          select.appendChild(option);
        });
      }
    })
    .catch(error => console.error('Error loading salespeople:', error));
};

console.log("Customer modal fix script loaded");

// Initialize the modal fix on DOM content loaded
document.addEventListener('DOMContentLoaded', function() {
  console.log("Initializing customer modal fixes");

  // Ensure all modal buttons work correctly
  document.querySelectorAll('[data-bs-toggle="modal"]').forEach(button => {
    button.addEventListener('click', function(event) {
      // Prevent multiple handlers
      event.stopPropagation();

      const targetSelector = this.getAttribute('data-bs-target');
      if (!targetSelector) return;

      const modalElement = document.querySelector(targetSelector);
      if (!modalElement) return;

      // Show modal using our fixed function
      if (typeof window.showModal === 'function') {
        window.showModal(modalElement.id);
      } else {
        const modal = new bootstrap.Modal(modalElement);
        modal.show();
      }
    });
  });
});
