/**
 * Contacts Tab Functionality
 * Handles all contact-related operations for customer detail page
 */
const customerIdElement = document.getElementById('customer_id');
if (!customerIdElement) {
    console.log('contacts.js: Not on a customer page, skipping initialization');
    // Exit early - don't initialize anything
} else {
    // Only initialize if we're on a customer page
    document.addEventListener('DOMContentLoaded', initializeContactsTab);
}

// Main initialization function
function initializeContactsTab() {
  const t0 = performance.now();
  // Make sure we have a customer ID
  const customerId = document.querySelector('[data-customer-id]')?.dataset?.customerId;
  if (!customerId) {
    console.error('No customer ID found on page');
    console.log(`init.contacts ${Math.round(performance.now() - t0)}ms (no customer)`);
    return;
  }

  // Initialize form submission
  setupContactForm(customerId);

  // Set up suggested contacts
  setupSuggestedContacts(customerId);

  console.log(`init.contacts ${Math.round(performance.now() - t0)}ms`);
}

/**
 * Sets up the contact form submission handler
 */
function setupContactForm(customerId) {
  const form = document.getElementById('add-contact-form');
  const alert = document.getElementById('contact-form-alert');

  if (!form) return;

  form.addEventListener('submit', async function(e) {
    e.preventDefault();

    // Gather form data
    const formData = {
      name: this.first_name.value.trim(),
      second_name: this.second_name.value.trim(),
      email: this.email.value.trim(),
      job_title: this.job_title.value.trim(),
      phone: this.phone ? this.phone.value.trim() : '',
      customer_id: customerId
    };

    // Validate form
    if (!formData.name || !formData.email) {
      showFormAlert('Please fill in required fields', 'danger');
      return;
    }

    try {
      const response = await fetch('/customers/contacts/add', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
      });

      const data = await response.json();

      if (data.success) {
        showFormAlert('Contact added successfully!', 'success');
        form.reset();
        // Reload page after a short delay to show the new contact
        setTimeout(() => window.location.reload(), 1000);
      } else {
        showFormAlert(data.error || 'Failed to add contact', 'danger');
      }
    } catch (err) {
      showFormAlert('Failed to add contact. Please try again.', 'danger');
      console.error('Error adding contact:', err);
    }
  });

  // Helper to show form alerts
  function showFormAlert(message, type) {
    if (!alert) return;

    alert.className = `alert alert-${type}`;
    alert.textContent = message;
    alert.classList.remove('d-none');

    // Auto-hide success messages after 5 seconds
    if (type === 'success') {
      setTimeout(() => {
        alert.classList.add('d-none');
      }, 5000);
    }
  }
}

/**
 * Sets up the suggested contacts section
 */
function setupSuggestedContacts(customerId) {
  // Add event listener to the refresh button
  const refreshBtn = document.getElementById('refreshSuggestedContacts');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadSuggestedContacts(customerId));
  }

  // Add event listener to the tab button
  const contactsTabLink = document.querySelector('a[href="#contacts"]');
  if (contactsTabLink) {
    contactsTabLink.addEventListener('click', () => loadSuggestedContacts(customerId));
  }

  // If contacts tab is active on page load, load suggested contacts
  if (document.querySelector('a[href="#contacts"].active')) {
    loadSuggestedContacts(customerId);
  }
}

/**
 * Loads suggested contacts from the server
 */
function loadSuggestedContacts(customerId) {
  if (!customerId) {
    customerId = document.querySelector('[data-customer-id]')?.dataset?.customerId;
  }

  if (!customerId) {
    console.error('No customer ID found');
    return;
  }

  const container = document.getElementById('suggestedContactsContainer');
  if (!container) return;

  // Show loading indicator
  container.innerHTML = `
    <div class="text-center py-3">
      <div class="spinner-border text-primary" role="status">
        <span class="visually-hidden">Loading...</span>
      </div>
      <p class="mt-2 text-muted">Loading suggested contacts...</p>
    </div>
  `;

  // Fetch contacts from server
  fetch(`/customers/${customerId}/suggest-contacts`)
    .then(response => {
      if (!response.ok) throw new Error('Network response was not ok');
      return response.json();
    })
    .then(data => {
      if (!data.success) {
        throw new Error(data.error || 'Unknown error');
      }

      renderSuggestedContacts(data.suggested_contacts, container);
    })
    .catch(error => {
      container.innerHTML = `
        <div class="alert alert-danger m-3">
          <i class="bi bi-exclamation-triangle me-2"></i>
          Error loading suggested contacts: ${error.message}
        </div>
      `;
    });
}

/**
 * Renders the list of suggested contacts
 */
function renderSuggestedContacts(contacts, container) {
  if (!contacts || contacts.length === 0) {
    container.innerHTML = `
      <div class="alert alert-info m-3">
        <i class="bi bi-info-circle me-2"></i>
        No suggested contacts found. All your email contacts are already in your system.
      </div>
    `;
    return;
  }

  let html = `
    <div class="table-responsive">
      <table class="table table-hover table-sm mb-0">
        <thead class="table-light">
          <tr>
            <th>Email</th>
            <th class="text-center">Emails</th>
            <th>Last Contact</th>
            <th>Recent Subject</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
  `;

  contacts.forEach(contact => {
    // Format the date and get most recent subject
    const lastSeen = new Date(contact.last_seen).toLocaleDateString();
    const recentSubject = contact.recent_subjects && contact.recent_subjects.length > 0
      ? contact.recent_subjects[0]
      : 'N/A';

    // Parse email to suggest name
    const emailParts = contact.email.split('@')[0].split('.');
    const suggestedFirstName = emailParts[0].charAt(0).toUpperCase() + emailParts[0].slice(1);
    const suggestedLastName = emailParts.length > 1
      ? emailParts[1].charAt(0).toUpperCase() + emailParts[1].slice(1)
      : '';

    html += `
      <tr>
        <td>${contact.email}</td>
        <td class="text-center">${contact.email_count}</td>
        <td>${lastSeen}</td>
        <td class="text-truncate" style="max-width: 200px;" title="${recentSubject}">${recentSubject}</td>
        <td>
          <button
            class="btn btn-sm btn-primary"
            onclick="quickAddContact('${contact.email}', '${suggestedFirstName}', '${suggestedLastName}')"
            data-bs-toggle="tooltip"
            title="Add to contacts">
            <i class="bi bi-plus-circle"></i>
          </button>
        </td>
      </tr>
    `;
  });

  html += `
        </tbody>
      </table>
    </div>
  `;

  container.innerHTML = html;

  // Initialize tooltips
  const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
  tooltipTriggerList.map(function (tooltipTriggerEl) {
    return new bootstrap.Tooltip(tooltipTriggerEl);
  });
}

/**
 * Quickly adds a contact from the suggested contacts list
 */
window.quickAddContact = function(email, firstName, lastName) {
  const customerId = document.querySelector('[data-customer-id]')?.dataset?.customerId;
  if (!customerId) {
    console.error('No customer ID found');
    return;
  }

  // Show a modal for final confirmation and editing
  const modal = document.createElement('div');
  modal.className = 'modal fade';
  modal.id = 'quickAddContactModal';
  modal.setAttribute('tabindex', '-1');
  modal.innerHTML = `
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">Add New Contact</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="modal-body">
          <form id="quickAddContactForm" class="row g-3">
            <div class="col-md-6">
              <label for="quick-first-name" class="form-label">First Name</label>
              <input type="text" id="quick-first-name" name="first_name" class="form-control" value="${firstName}" required>
            </div>
            <div class="col-md-6">
              <label for="quick-second-name" class="form-label">Last Name</label>
              <input type="text" id="quick-second-name" name="second_name" class="form-control" value="${lastName}">
            </div>
            <div class="col-md-6">
              <label for="quick-email" class="form-label">Email</label>
              <input type="email" id="quick-email" name="email" class="form-control" value="${email}" required readonly>
            </div>
            <div class="col-md-6">
              <label for="quick-job-title" class="form-label">Job Title</label>
              <input type="text" id="quick-job-title" name="job_title" class="form-control">
            </div>
            <div class="col-12">
              <label for="quick-phone" class="form-label">Phone (optional)</label>
              <input type="text" id="quick-phone" name="phone" class="form-control">
            </div>
            <div class="col-12">
              <div id="quick-form-alert" class="alert d-none"></div>
            </div>
          </form>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
          <button type="button" class="btn btn-primary" id="saveQuickContact">Save Contact</button>
        </div>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  // Initialize and show the modal
  const modalElement = new bootstrap.Modal(document.getElementById('quickAddContactModal'));
  modalElement.show();

  // Handle saving the contact
  document.getElementById('saveQuickContact').addEventListener('click', function() {
    const form = document.getElementById('quickAddContactForm');
    const alert = document.getElementById('quick-form-alert');

    const firstName = form.elements.first_name.value.trim();
    const secondName = form.elements.second_name.value.trim();
    const jobTitle = form.elements.job_title.value.trim();
    const phone = form.elements.phone.value.trim();

    if (!firstName) {
      alert.className = 'alert alert-danger';
      alert.textContent = 'First name is required';
      alert.classList.remove('d-none');
      return;
    }

    fetch('/customers/contacts/add', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: firstName,
        second_name: secondName,
        email: email,
        job_title: jobTitle,
        phone: phone,
        customer_id: customerId
      })
    })
    .then(response => response.json())
    .then(data => {
      if (data.success) {
        modalElement.hide();
        // Remove the modal element after hiding
        document.getElementById('quickAddContactModal').remove();

        // Show a success toast
        showToast('Contact Added', `${firstName} ${secondName} has been added to contacts.`, 'success');

        // Refresh the contacts list and suggested contacts
        window.location.reload();
      } else {
        alert.className = 'alert alert-danger';
        alert.textContent = data.error || 'Failed to add contact';
        alert.classList.remove('d-none');
      }
    })
    .catch(err => {
      alert.className = 'alert alert-danger';
      alert.textContent = 'Failed to add contact. Please try again.';
      alert.classList.remove('d-none');
    });
  });

  // Remove the modal when it's hidden
  document.getElementById('quickAddContactModal').addEventListener('hidden.bs.modal', function() {
    this.remove();
  });
};

/**
 * Shows a toast notification
 */
function showToast(title, message, type = 'info') {
  // Create toast container if it doesn't exist
  let toastContainer = document.getElementById('toast-container');
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    toastContainer.className = 'position-fixed bottom-0 end-0 p-3';
    toastContainer.style.zIndex = '9999';
    document.body.appendChild(toastContainer);
  }

  // Create a unique ID for this toast
  const toastId = 'toast-' + Date.now();

  // Create the toast element
  const toastHtml = `
    <div id="${toastId}" class="toast" role="alert" aria-live="assertive" aria-atomic="true">
      <div class="toast-header ${type === 'success' ? 'bg-success text-white' : ''}">
        <i class="bi ${type === 'success' ? 'bi-check-circle' : 'bi-info-circle'} me-2"></i>
        <strong class="me-auto">${title}</strong>
        <small>Just now</small>
        <button type="button" class="btn-close ${type === 'success' ? 'btn-close-white' : ''}" data-bs-dismiss="toast" aria-label="Close"></button>
      </div>
      <div class="toast-body">
        ${message}
      </div>
    </div>
  `;

  // Add the toast to the container
  toastContainer.insertAdjacentHTML('beforeend', toastHtml);

  // Initialize and show the toast
  const toastElement = document.getElementById(toastId);
  const toast = new bootstrap.Toast(toastElement, {
    delay: 5000
  });
  toast.show();

  // Remove the toast when it's hidden
  toastElement.addEventListener('hidden.bs.toast', function() {
    this.remove();
  });
}

// Initialize when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', initializeContactsTab);
