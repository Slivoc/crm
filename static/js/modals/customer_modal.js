///**
// * Fixed Bootstrap Modal patch - handles the DATA_KEY error
// * Add this to the TOP of your customer_modal.js file
// */
//
///**
// * Direct Modal Button Fix
// *
// * This script directly intercepts Bootstrap modal buttons and handles them
// * without relying on Bootstrap's possibly broken internal methods.
// *
// * Add this to a <script> tag just before your closing </body> tag
// * to ensure it runs after all other scripts.
// */
//
//(function() {
//    // Create a map to store modal instances
//    const modalInstances = new Map();
//
//    // Directly intercept button clicks that have data-bs-toggle="modal"
//    document.addEventListener('click', function(event) {
//        // Find if the click is on or within a button that triggers a modal
//        const button = event.target.closest('[data-bs-toggle="modal"]');
//        if (!button) return;
//
//        // Prevent default behavior from triggering broken code
//        event.preventDefault();
//        event.stopPropagation();
//
//        // Get the target modal ID from data-bs-target
//        const targetSelector = button.getAttribute('data-bs-target');
//        if (!targetSelector) return;
//
//        // Find the modal element
//        const modalElement = document.querySelector(targetSelector);
//        if (!modalElement) {
//            console.error(`Modal ${targetSelector} not found`);
//            return;
//        }
//
//        // Show the modal with our custom implementation
//        showModalSafely(modalElement);
//    }, true); // Use capture phase to intercept before other handlers
//
//    // Also directly intercept dismiss buttons
//    document.addEventListener('click', function(event) {
//        // Find if the click is on or within a button that dismisses a modal
//        const button = event.target.closest('[data-bs-dismiss="modal"]');
//        if (!button) return;
//
//        // Prevent default behavior from triggering broken code
//        event.preventDefault();
//        event.stopPropagation();
//
//        // Find the parent modal
//        const modalElement = button.closest('.modal');
//        if (!modalElement) return;
//
//        // Hide the modal with our custom implementation
//        hideModalSafely(modalElement);
//    }, true); // Use capture phase to intercept before other handlers
//
//    // Custom function to show a modal safely
//    function showModalSafely(modalElement) {
//        console.log(`Safely showing modal #${modalElement.id}`);
//
//        try {
//            // Try Bootstrap 5 way first
//            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
//                // Check if we already have an instance
//                let instance = modalInstances.get(modalElement.id);
//
//                if (!instance) {
//                    // Create a new instance and store it
//                    instance = new bootstrap.Modal(modalElement);
//                    modalInstances.set(modalElement.id, instance);
//                }
//
//                instance.show();
//                return;
//            }
//
//            // Fallback to jQuery if Bootstrap isn't available
//            if (typeof $ !== 'undefined') {
//                $(modalElement).modal('show');
//                return;
//            }
//
//            // Last resort: manual implementation
//            modalElement.classList.add('show');
//            modalElement.style.display = 'block';
//            document.body.classList.add('modal-open');
//
//            // Create backdrop if it doesn't exist
//            let backdrop = document.querySelector('.modal-backdrop');
//            if (!backdrop) {
//                backdrop = document.createElement('div');
//                backdrop.className = 'modal-backdrop fade show';
//                document.body.appendChild(backdrop);
//            }
//        } catch (error) {
//            console.error('Error showing modal:', error);
//
//            // Last resort fallback
//            modalElement.classList.add('show');
//            modalElement.style.display = 'block';
//        }
//    }
//
//    // Custom function to hide a modal safely
//    function hideModalSafely(modalElement) {
//        console.log(`Safely hiding modal #${modalElement.id}`);
//
//        try {
//            // Try Bootstrap 5 way first
//            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
//                // Check if we have an instance
//                const instance = modalInstances.get(modalElement.id);
//
//                if (instance) {
//                    instance.hide();
//                    return;
//                }
//            }
//
//            // Fallback to jQuery if Bootstrap isn't available
//            if (typeof $ !== 'undefined') {
//                $(modalElement).modal('hide');
//                return;
//            }
//
//            // Last resort: manual implementation
//            modalElement.classList.remove('show');
//            modalElement.style.display = 'none';
//            document.body.classList.remove('modal-open');
//
//            // Remove backdrop
//            const backdrop = document.querySelector('.modal-backdrop');
//            if (backdrop) {
//                backdrop.remove();
//            }
//        } catch (error) {
//            console.error('Error hiding modal:', error);
//
//            // Last resort fallback
//            modalElement.classList.remove('show');
//            modalElement.style.display = 'none';
//
//            // Remove backdrop
//            const backdrop = document.querySelector('.modal-backdrop');
//            if (backdrop) {
//                backdrop.remove();
//            }
//        }
//    }
//
//    // Make functions globally available (optional)
//    window.showModalSafely = showModalSafely;
//    window.hideModalSafely = hideModalSafely;
//
//    console.log('Direct modal button fix installed');
//})();
//
//// Fix for the showModal function - replace your existing showModal function
//// with this improved version that's more defensive
//// Replace your existing showModal function with this version
//window.showModal = function(modalId) {
//    console.log(`Showing modal with ID: ${modalId}`);
//    const modalElement = document.getElementById(modalId);
//
//    if (!modalElement) {
//        console.error(`Modal element #${modalId} not found`);
//        return false;
//    }
//
//    try {
//        // Try Bootstrap 5 way first
//        if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
//            const modal = new bootstrap.Modal(modalElement);
//            modal.show();
//            return true;
//        }
//
//        // Fallback to jQuery if available
//        if (typeof $ !== 'undefined') {
//            $(modalElement).modal('show');
//            return true;
//        }
//
//        // Manual fallback as last resort
//        modalElement.classList.add('show');
//        modalElement.style.display = 'block';
//        document.body.classList.add('modal-open');
//
//        // Create backdrop
//        let backdrop = document.querySelector('.modal-backdrop');
//        if (!backdrop) {
//            backdrop = document.createElement('div');
//            backdrop.className = 'modal-backdrop fade show';
//            document.body.appendChild(backdrop);
//        }
//
//        return true;
//    } catch (error) {
//        console.error(`Error showing modal #${modalId}:`, error);
//        return false;
//    }
//};
//// Define the missing showModal function
//if (typeof window.showModal !== 'function') {
//    window.showModal = function(modalId) {
//        console.log(`Showing modal with ID: ${modalId}`);
//        const modalElement = document.getElementById(modalId);
//
//        if (!modalElement) {
//            console.error(`Modal element #${modalId} not found`);
//            return false;
//        }
//
//        try {
//            // Try Bootstrap 5 way
//            if (typeof bootstrap !== 'undefined') {
//                const bsModal = new bootstrap.Modal(modalElement);
//                bsModal.show();
//                return true;
//            }
//
//            // Fallback to jQuery way for older Bootstrap
//            if (typeof $ !== 'undefined') {
//                $(modalElement).modal('show');
//                return true;
//            }
//
//            console.error('Neither Bootstrap nor jQuery modal methods are available');
//            return false;
//        } catch (error) {
//            console.error(`Error showing modal #${modalId}:`, error);
//            return false;
//        }
//    };
//
//    console.log('Added missing showModal function');
//}
//
//// Apply a safer modal showing technique in openCustomerModal
//if (typeof originalOpenCustomerModal === 'undefined') {
//  const originalOpenCustomerModal = window.openCustomerModal;
//window.openCustomerModal = function(data) {
//    try {
//        console.log("Opening universal customer modal with data:", data);
//
//        const modalId = 'customerModal';
//        const modalElement = document.getElementById(modalId);
//
//        if (!modalElement) {
//            console.warn(`Modal #${modalId} not found in the DOM`);
//            return false;
//        }
//
//        // Reset the state first
//        resetModalState();
//
//        // Then pre-populate form fields with CORRECT IDs
//        const nameField = modalElement.querySelector('#editCustomerName');
//        const descField = modalElement.querySelector('#editCustomerDescription');
//        const revenueField = modalElement.querySelector('#editEstimatedRevenue');
//        const countryField = modalElement.querySelector('#editCountry');
//        const salespersonField = modalElement.querySelector('#editSalesperson');
//        const tagInput = modalElement.querySelector('#selectedTagId');
//
//        // Set values if fields exist
//        if (nameField) nameField.value = data.name || '';
//        if (descField) descField.value = data.description || '';
//        if (revenueField) revenueField.value = data.revenue || '';
//        if (countryField) countryField.value = data.country || '';
//        if (salespersonField && data.salesperson_id) salespersonField.value = data.salesperson_id;
//
//        // Set tag ID if provided
//        if (tagInput && data.tagId) {
//            tagInput.value = data.tagId;
//        }
//
//        // Add missing fields if they don't exist
//        addMissingFormFields(modalElement);
//
//        // Populate salesperson dropdown
//        populateSalespeopleDropdown();
//
//        // Show the modal safely
//        return window.showModal(modalId);
//    } catch (error) {
//        console.error('Error in openCustomerModal:', error);
//        return false;
//    }
//};
//}
//// Make the openCustomerModal function global
//window.openCustomerModal = function(data) {
//    console.log("Opening universal customer modal with data:", data);
//
//    const modalId = 'customerModal';
//    const modalElement = document.getElementById(modalId);
//
//    if (!modalElement) {
//        console.warn(`Modal #${modalId} not found in the DOM`);
//        return false;
//    }
//
//    // Reset the state first
//    resetModalState();
//
//    // Then pre-populate form fields
//    const nameField = modalElement.querySelector('#customerName');
//    const descField = modalElement.querySelector('#customerDescription');
//    const revenueField = modalElement.querySelector('#estimatedRevenue');
//    const countryField = modalElement.querySelector('#country');
//    const tagInput = modalElement.querySelector('#selectedTagId');
//
//    // Set values if fields exist
//    if (nameField) nameField.value = data.name || '';
//    if (descField) descField.value = data.description || '';
//    if (revenueField) revenueField.value = data.revenue || '';
//    if (countryField) countryField.value = data.country || '';
//
//    // Set tag ID if provided
//    if (tagInput && data.tagId) {
//        tagInput.value = data.tagId;
//    }
//
//    // Add missing fields if they don't exist
//    addMissingFormFields(modalElement);
//
//    // Populate salesperson dropdown
//    populateSalespeopleDropdown();
//
//    // Show the modal safely
//    return showModal(modalId);
//};
//
///**
// * Reset modal state to initial view
// */
//// Modify the resetModalState function to be more thorough
//function resetModalState() {
//    const modal = document.getElementById('customerModal');
//    if (!modal) return;
//
//    console.log("Resetting modal state completely");
//
//    // Reset sections visibility
//    const customerInfoSection = modal.querySelector('#customerInfoSection');
//    const apolloSection = modal.querySelector('#apolloSection');
//
//    if (customerInfoSection) customerInfoSection.classList.remove('d-none');
//    if (apolloSection) apolloSection.classList.add('d-none');
//    // Reset progress indicators
//    const step2Circle = modal.querySelector('#step2Circle');
//    const step1to2Line = modal.querySelector('#step1to2Line');
//
//    if (step2Circle) step2Circle.classList.replace('bg-primary', 'bg-secondary');
//    if (step1to2Line) step1to2Line.classList.remove('bg-primary');
//
//    // Reset form completely
//    const form = modal.querySelector('#editCustomerForm'); // FIXED: Changed from addCustomerForm to editCustomerForm
//    if (form) {
//        form.reset();
//
//        // Also clear any dynamically added fields
//        form.querySelectorAll('input[type="hidden"]').forEach(input => {
//            input.value = '';
//        });
//    }
//
//    // Remove any success/error alerts
//    modal.querySelectorAll('.alert').forEach(alert => {
//        alert.remove();
//    });
//
//    // Reset modal footer
//    const modalFooter = modal.querySelector('#modalFooter');
//    if (modalFooter) {
//        modalFooter.innerHTML = `
//            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
//            <button type="button" class="btn btn-primary" onclick="submitCustomer()">Add Customer</button>
//        `;
//    }
//
//    // Reset Apollo section
//    if (apolloSection) {
//        // Clear any previous search results
//        const searchResults = apolloSection.querySelector('#searchResults');
//        if (searchResults) searchResults.innerHTML = '';
//
//        // Reset the search input
//        const searchInput = apolloSection.querySelector('#companySearchInput');
//        if (searchInput) searchInput.value = '';
//
//        // Disable the search button
//        const searchButton = apolloSection.querySelector('#apolloSearchButton');
//        if (searchButton) searchButton.disabled = true;
//
//        // Remove the customer ID
//        const customerIdField = apolloSection.querySelector('#currentCustomerId');
//        if (customerIdField) customerIdField.remove();
//
//        // Reset Apollo match section
//        const apolloMatchSection = apolloSection.querySelector('#apolloMatchSection');
//        if (apolloMatchSection) {
//            apolloMatchSection.innerHTML = `
//                <div class="input-group mb-3">
//                    <input type="text" class="form-control" id="companySearchInput"
//                           placeholder="Enter company name to search...">
//                    <button class="btn btn-primary" type="button" id="apolloSearchButton" disabled>
//                        <i class="bi bi-search"></i> Search
//                    </button>
//                </div>
//                <div id="searchResults" class="mt-3"></div>
//            `;
//        }
//
//        // Reset leads section if it exists
//        const leadsSection = apolloSection.querySelector('#leadsSection');
//        if (leadsSection) leadsSection.innerHTML = '';
//    }
//
//    // Ensure search history is properly initialized
//    updateSearchHistory();
//}
///**
// * Add missing form fields that exist in the original modal
// */
//function addMissingFormFields(modal) {
//    const form = modal.querySelector('#editCustomerForm'); // FIXED: Changed from addCustomerForm to editCustomerForm
//    if (!form) return;
//
//    // Check for payment terms field
//    if (!form.querySelector('#paymentTerms')) {
//        const paymentTermsDiv = document.createElement('div');
//        paymentTermsDiv.className = 'mb-3';
//        paymentTermsDiv.innerHTML = `
//            <label for="paymentTerms" class="form-label">Payment Terms</label>
//            <input type="text" class="form-control" id="paymentTerms" value="Pro-forma">
//        `;
//        form.appendChild(paymentTermsDiv);
//    }
//
//    // Check for incoterms field
//    if (!form.querySelector('#incoterms')) {
//        const incotermsDiv = document.createElement('div');
//        incotermsDiv.className = 'mb-3';
//        incotermsDiv.innerHTML = `
//            <label for="incoterms" class="form-label">Incoterms</label>
//            <input type="text" class="form-control" id="incoterms" value="EXW">
//        `;
//        form.appendChild(incotermsDiv);
//    }
//}
//
///**
// * Populate salesperson dropdown from server
// */
//function populateSalespeopleDropdown() {
//    fetch('/customers/api/salespeople')
//        .then(response => response.json())
//        .then(data => {
//            if (data.success && Array.isArray(data.salespeople)) {
//                // Use the correct selector from our diagnosis
//                const select = document.querySelector('#customerModal #editSalesperson');
//                if (!select) return;
//
//                // Keep the first option and add new ones
//                const firstOption = select.options[0];
//                select.innerHTML = '';
//                select.appendChild(firstOption);
//
//                data.salespeople.forEach(sp => {
//                    const option = document.createElement('option');
//                    option.value = sp.id;
//                    option.textContent = sp.name;
//                    select.appendChild(option);
//                });
//            }
//        })
//        .catch(error => console.error('Error loading salespeople:', error));
//}
//
///**
// * Submit customer form and transition to Apollo search
// */
//window.submitCustomer = function() {
//    const modal = document.getElementById('customerModal');
//    const submitBtn = modal.querySelector('button[onclick="submitCustomer()"]');
//    const originalContent = submitBtn.innerHTML;
//
//    // Show loading state
//    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Adding...';
//    submitBtn.disabled = true;
//
//    // Prepare form data using the correct field IDs
//    const formData = {
//        name: modal.querySelector('#editCustomerName').value,
//        description: modal.querySelector('#editCustomerDescription').value,
//        estimated_revenue: parseInt(modal.querySelector('#editEstimatedRevenue').value) || 0,
//        country: modal.querySelector('#editCountry').value.toUpperCase(),
//        salesperson_id: modal.querySelector('#editSalesperson').value,
//        tag_id: modal.querySelector('#selectedTagId').value || null,
//        payment_terms: modal.querySelector('#paymentTerms')?.value || 'Pro-forma',
//        incoterms: modal.querySelector('#incoterms')?.value || 'EXW'
//    };
//
//    // The rest of your submit function stays the same
//    fetch('/customers/add_suggested', {
//        method: 'POST',
//        headers: {
//            'Content-Type': 'application/json',
//        },
//        body: JSON.stringify(formData)
//    })
//    .then(response => response.json())
//    .then(data => {
//        // Existing success handler
//        if (data.success) {
//            // Store the customer ID
//            const hiddenCustomerId = document.createElement('input');
//            hiddenCustomerId.type = 'hidden';
//            hiddenCustomerId.id = 'currentCustomerId';
//            hiddenCustomerId.value = data.customer_id;
//            modal.querySelector('#apolloSection').appendChild(hiddenCustomerId);
//
//            // Show success message
//            const successAlert = document.createElement('div');
//            successAlert.className = 'alert alert-success';
//            successAlert.innerHTML = '<i class="bi bi-check-circle"></i> Customer added successfully!';
//            modal.querySelector('#customerInfoSection').appendChild(successAlert);
//
//            // Refresh the customers table if function exists
//            if (typeof refreshCustomersTable === 'function') {
//                refreshCustomersTable();
//            }
//
//            // Continue with Apollo section transition
//            setTimeout(() => {
//                modal.querySelector('#step2Circle').classList.replace('bg-secondary', 'bg-primary');
//                modal.querySelector('#step1to2Line').classList.add('bg-primary');
//                modal.querySelector('#customerInfoSection').classList.add('d-none');
//                modal.querySelector('#apolloSection').classList.remove('d-none');
//
//                // Update modal footer
//                modal.querySelector('#modalFooter').innerHTML = `
//                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
//                `;
//
//                // Set up Apollo search
//                const searchButton = modal.querySelector('#apolloSearchButton');
//                searchButton.disabled = false;
//                modal.querySelector('#companySearchInput').value = formData.name;
//
//                // Trigger initial search
//                performApolloSearch(data.customer_id);
//            }, 1000);
//        } else {
//            throw new Error(data.error || 'Failed to add customer');
//        }
//    })
//    .catch(error => {
//        console.error('Error:', error);
//        const errorAlert = document.createElement('div');
//        errorAlert.className = 'alert alert-danger';
//        errorAlert.innerHTML = `<i class="bi bi-exclamation-triangle"></i> ${error.message || 'An error occurred while adding the customer'}`;
//        modal.querySelector('#customerInfoSection').appendChild(errorAlert);
//    })
//    .finally(() => {
//        // Reset button state
//        submitBtn.innerHTML = originalContent;
//        submitBtn.disabled = false;
//    });
//};
///**
// * Perform Apollo organization search
// */
//function performApolloSearch(customerId) {
//    if (!customerId) {
//        console.error('No customer ID provided for Apollo search');
//        const modal = document.getElementById('customerModal');
//        const resultsDiv = modal.querySelector('#searchResults');
//        resultsDiv.innerHTML = `
//            <div class="alert alert-danger">
//                <i class="bi bi-exclamation-triangle"></i>
//                Error: Missing customer ID
//            </div>
//        `;
//        return;
//    }
//
//    const modal = document.getElementById('customerModal');
//    const searchTerm = modal.querySelector('#companySearchInput').value.trim();
//    if (!searchTerm) {
//        console.error('No search term provided');
//        return;
//    }
//
//    console.log('Performing Apollo search for customer:', customerId);
//
//    // Show loading state
//    const resultsDiv = modal.querySelector('#searchResults');
//    resultsDiv.innerHTML = `
//        <div class="text-center">
//            <div class="spinner-border text-primary" role="status">
//                <span class="visually-hidden">Loading...</span>
//            </div>
//        </div>
//    `;
//
//    // Add to search history
//    updateSearchHistory(searchTerm);
//
//    // Perform the search with the correct customer ID
//    fetch(`/customers/${customerId}/apollo_search`, {
//        method: 'POST',
//        headers: {
//            'Content-Type': 'application/json',
//        },
//        body: JSON.stringify({
//            q_organization_name: searchTerm
//        })
//    })
//    .then(response => response.json())
//    .then(data => {
//        if (data.error) {
//            throw new Error(data.error);
//        }
//
//        // Render search results (keeping same UI as original)
//        renderSearchResults(data, resultsDiv);
//    })
//    .catch(error => {
//        resultsDiv.innerHTML = `
//            <div class="alert alert-danger">
//                <i class="bi bi-exclamation-triangle"></i>
//                Error: ${error.message || 'Failed to search organizations'}
//            </div>
//        `;
//    });
//}
//
///**
// * Render Apollo search results
// */
//function renderSearchResults(data, resultsDiv) {
//    const searchTerm = document.querySelector('#customerModal #companySearchInput').value.trim();
//
//    resultsDiv.innerHTML = `
//        <div class="mb-3">
//            <small class="text-muted">
//                Searching for: "${data.search_term}"
//                ${searchTerm !== data.search_term ?
//                    `<br>(Simplified from: "${searchTerm}")` :
//                    ''}
//            </small>
//        </div>
//        ${data.organizations.length === 0 ? `
//            <div class="alert alert-info">
//                <h6>No exact matches found.</h6>
//                <p class="mb-0">Try:</p>
//                <ul class="mb-0">
//                    <li>Removing company type (like s.r.o, Ltd, Inc)</li>
//                    <li>Checking for spelling variations</li>
//                    <li>Using a shorter company name</li>
//                    <li>Removing special characters</li>
//                </ul>
//            </div>
//        ` : `
//            <div class="list-group">
//                ${data.organizations.map(org => `
//                    <div class="list-group-item ${org.raw_match ? 'border-success' : ''}">
//                        <div class="d-flex justify-content-between align-items-start">
//                            <div class="d-flex gap-3">
//                                ${org.logo_url ? `
//                                    <div class="flex-shrink-0">
//                                        <img src="${org.logo_url}"
//                                             alt="${org.name} logo"
//                                             class="company-logo"
//                                             style="width: 48px; height: 48px; object-fit: contain; border: 1px solid #eee; border-radius: 4px; padding: 4px; background: white;"
//                                             onerror="handleLogoError(this)"
//                                        >
//                                    </div>
//                                ` : ''}
//                                <div class="ms-2 me-auto">
//                                    <div class="d-flex align-items-center gap-2">
//                                        <span class="fw-bold">${org.name}</span>
//                                        ${org.raw_match ?
//                                            '<span class="badge bg-success ms-2">Exact Match</span>' :
//                                            ''}
//                                    </div>
//
//                                    <div class="mt-1 text-muted small">
//                                        ${org.primary_industry ? `
//                                            <div>
//                                                <i class="bi bi-building"></i> Industry: ${org.primary_industry}
//                                            </div>
//                                        ` : ''}
//                                        ${org.employee_count ? `
//                                            <div>
//                                                <i class="bi bi-people"></i> Employees: ${org.employee_count.toLocaleString()}
//                                            </div>
//                                        ` : ''}
//                                        ${org.domain ? `
//                                            <div>
//                                                <i class="bi bi-globe"></i> Domain: ${org.domain}
//                                            </div>
//                                        ` : ''}
//                                        ${org.country ? `
//                                            <div>
//                                                <i class="bi bi-geo-alt"></i> Country: ${org.country}
//                                            </div>
//                                        ` : ''}
//                                    </div>
//
//                                    <div class="mt-2">
//                                        ${org.website ? `
//                                            <a href="${org.website}" target="_blank" class="btn btn-sm btn-link">
//                                                <i class="bi bi-globe"></i> Website
//                                            </a>
//                                        ` : ''}
//                                        ${org.linkedin_url ? `
//                                            <a href="${org.linkedin_url}" target="_blank" class="btn btn-sm btn-link">
//                                                <i class="bi bi-linkedin"></i> LinkedIn
//                                            </a>
//                                        ` : ''}
//                                    </div>
//                                </div>
//                            </div>
//                            <button class="btn btn-sm btn-primary align-self-start ms-3"
//                                    onclick="matchWithApollo('${document.getElementById('currentCustomerId').value}', '${org.id}')">
//                                Select Match
//                            </button>
//                        </div>
//                    </div>
//                `).join('')}
//            </div>
//        `}
//
//        <div class="mt-3">
//            <button class="btn btn-outline-primary btn-sm"
//                    onclick="showSearchTips()">
//                <i class="bi bi-question-circle"></i> Search Tips
//            </button>
//        </div>
//    `;
//}
//
///**
// * Update Apollo search history
// */
//function updateSearchHistory(searchTerm) {
//    const modal = document.getElementById('customerModal');
//    const historyDiv = modal.querySelector('#searchHistory');
//    if (!historyDiv) return;
//
//    const searchHistory = JSON.parse(localStorage.getItem('apolloSearchHistory') || '[]');
//
//    // Add current term to history if new
//    if (searchTerm && !searchHistory.includes(searchTerm)) {
//        searchHistory.unshift(searchTerm);
//        if (searchHistory.length > 5) searchHistory.pop();
//        localStorage.setItem('apolloSearchHistory', JSON.stringify(searchHistory));
//    }
//
//    if (searchHistory.length === 0) return;
//
//    historyDiv.innerHTML = `
//        <small class="text-muted me-2">Recent searches:</small>
//        ${searchHistory.map(term => `
//            <button class="btn btn-sm btn-outline-secondary"
//                    onclick="useHistoryTerm('${term.replace(/'/g, "\\'")}')">${term}</button>
//        `).join('')}
//        <button class="btn btn-sm btn-link text-danger" onclick="clearSearchHistory()">
//            <i class="bi bi-x-circle"></i> Clear
//        </button>
//    `;
//}
//
///**
// * Use a term from search history
// */
//window.useHistoryTerm = function(term) {
//    const modal = document.getElementById('customerModal');
//    modal.querySelector('#companySearchInput').value = term;
//    const customerId = modal.querySelector('#currentCustomerId').value;
//    if (customerId) {
//        performApolloSearch(customerId);
//    }
//};
//
///**
// * Clear search history
// */
//window.clearSearchHistory = function() {
//    localStorage.setItem('apolloSearchHistory', '[]');
//    updateSearchHistory();
//};
//
///**
// * Match customer with Apollo organization
// */
//window.matchWithApollo = function(customerId, apolloId) {
//    fetch(`/customers/${customerId}/apollo_match`, {
//        method: 'POST',
//        headers: {
//            'Content-Type': 'application/json',
//        },
//        body: JSON.stringify({ apollo_id: apolloId })
//    })
//    .then(response => response.json())
//    .then(data => {
//        if (data.success) {
//            // Show success message and transition to leads view
//            const modal = document.getElementById('customerModal');
//            const resultsDiv = modal.querySelector('#searchResults');
//            resultsDiv.innerHTML = `
//                <div class="alert alert-success">
//                    <i class="bi bi-check-circle"></i> Successfully matched with Apollo
//                </div>
//            `;
//            setTimeout(() => {
//                showLeadSearchOption();
//            }, 1500);
//        } else {
//            throw new Error(data.error || 'Failed to match with Apollo');
//        }
//    })
//    .catch(error => {
//        const modal = document.getElementById('customerModal');
//        const resultsDiv = modal.querySelector('#searchResults');
//        resultsDiv.innerHTML += `
//            <div class="alert alert-danger">
//                <i class="bi bi-exclamation-triangle"></i>
//                Error: ${error.message || 'Failed to match with Apollo'}
//            </div>
//        `;
//    });
//};
//
///**
// * Show search tips modal
// */
//window.showSearchTips = function() {
//    const modal = new bootstrap.Modal(document.getElementById('searchTipsModal') || createSearchTipsModal());
//    modal.show();
//};
//
///**
// * Create search tips modal if it doesn't exist
// */
//function createSearchTipsModal() {
//    const modalDiv = document.createElement('div');
//    modalDiv.className = 'modal fade';
//    modalDiv.id = 'searchTipsModal';
//    modalDiv.innerHTML = `
//        <div class="modal-dialog">
//            <div class="modal-content">
//                <div class="modal-header">
//                    <h5 class="modal-title">Search Tips</h5>
//                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
//                </div>
//                <div class="modal-body">
//                    <h6>For better results, try:</h6>
//                    <ul>
//                        <li>Remove company type suffixes (Ltd, Inc, GmbH, etc.)</li>
//                        <li>Remove special characters</li>
//                        <li>Try the parent company name</li>
//                        <li>Try alternative spellings</li>
//                        <li>Use shorter versions of the company name</li>
//                    </ul>
//                    <p><strong>Examples:</strong></p>
//                    <div class="table-responsive">
//                        <table class="table table-sm">
//                            <tr>
//                                <td><strong>Instead of:</strong></td>
//                                <td><strong>Try:</strong></td>
//                            </tr>
//                            <tr>
//                                <td>Festo s.r.o.</td>
//                                <td>Festo</td>
//                            </tr>
//                            <tr>
//                                <td>A.B.C. Solutions Ltd</td>
//                                <td>ABC Solutions</td>
//                            </tr>
//                        </table>
//                    </div>
//                </div>
//            </div>
//        </div>
//    `;
//    document.body.appendChild(modalDiv);
//    return modalDiv;
//}
//
///**
// * Handle Apollo logo loading errors
// */
//window.handleLogoError = function(img) {
//    console.log('Logo failed to load:', img.alt);
//    img.style.display = 'none';
//};
//
///**
// * Show lead search options
// */
//window.showLeadSearchOption = function() {
//    const modal = document.getElementById('customerModal');
//    const matchSection = modal.querySelector('#apolloMatchSection');
//    if (!matchSection) return;
//
//    matchSection.innerHTML = `
//        <div class="d-flex justify-content-between align-items-center mb-3">
//            <span class="text-success"><i class="bi bi-check-circle"></i> Matched with Apollo</span>
//            <button class="btn btn-primary" onclick="searchLeads('${modal.querySelector('#currentCustomerId').value}')">
//                Find Leads
//            </button>
//        </div>
//        <div id="leadsSection"></div>
//    `;
//};
//
///**
// * Search for leads at a company
// */
//window.searchLeads = function(customerId, searchType = 'procurement') {
//    const modal = document.getElementById('customerModal');
//    const leadsSection = modal.querySelector('#leadsSection');
//    if (!leadsSection) return;
//
//    // Show loading state
//    leadsSection.innerHTML = `
//        <div class="text-center py-4">
//            <div class="spinner-border text-primary" role="status">
//                <span class="visually-hidden">Loading...</span>
//            </div>
//        </div>
//    `;
//
//    fetch(`/customers/${customerId}/leads?type=${searchType}`)
//        .then(response => response.json())
//        .then(data => {
//            if (data.error) {
//                throw new Error(data.error);
//            }
//
//            const leads = data.leads;
//            let content = '';
//
//            if (leads.length === 0) {
//                if (searchType === 'procurement') {
//                    content = `
//                        <div class="alert alert-info">
//                            <h6 class="alert-heading">No procurement leads found</h6>
//                            <p>We couldn't find any procurement professionals at this company. Would you like to search for other senior contacts instead?</p>
//                            <button class="btn btn-primary" onclick="searchLeads('${customerId}', 'general')">
//                                Search for Senior Contacts
//                            </button>
//                        </div>
//                    `;
//                } else {
//                    content = `
//                        <div class="alert alert-info">
//                            <h6 class="alert-heading">No leads found</h6>
//                            <p>We couldn't find any matching contacts at this company.</p>
//                            <button class="btn btn-outline-primary" onclick="searchLeads('${customerId}', 'procurement')">
//                                Back to Procurement Search
//                            </button>
//                        </div>
//                    `;
//                }
//            } else {
//                content = `
//                    <div class="d-flex justify-content-between align-items-center mb-3">
//                        <h6 class="mb-0">
//                            ${searchType === 'procurement' ? 'Procurement Leads' : 'Senior Contacts'}
//                            <span class="badge bg-primary ms-2">${leads.length}</span>
//                        </h6>
//                        ${searchType === 'procurement' ? `
//                            <button class="btn btn-outline-primary btn-sm" onclick="searchLeads('${customerId}', 'general')">
//                                Search Senior Contacts Instead
//                            </button>
//                        ` : `
//                            <button class="btn btn-outline-primary btn-sm" onclick="searchLeads('${customerId}', 'procurement')">
//                                Search Procurement Instead
//                            </button>
//                        `}
//                    </div>
//                    <div class="list-group">
//                        ${leads.map(lead => `
//                            <div class="list-group-item">
//                                <div class="d-flex justify-content-between align-items-start">
//                                    <div class="flex-grow-1">
//                                        <h6 class="mb-1">${lead.name}</h6>
//                                        <p class="mb-1">
//                                            ${lead.title || 'No title'}
//                                            ${lead.seniority ? `<span class="badge bg-secondary ms-2">${lead.seniority}</span>` : ''}
//                                        </p>
//                                        <div class="small text-muted">
//                                            ${lead.department ? `<span class="me-3"><i class="bi bi-diagram-2"></i> ${lead.department}</span>` : ''}
//                                            ${lead.email_status ? `<span class="me-3"><i class="bi bi-envelope"></i> ${lead.email_status}</span>` : ''}
//                                            ${lead.city ? `<span><i class="bi bi-geo-alt"></i> ${lead.city}${lead.state ? `, ${lead.state}` : ''}</span>` : ''}
//                                        </div>
//                                        ${lead.linkedin_url ? `
//                                            <a href="${lead.linkedin_url}" target="_blank" class="btn btn-link btn-sm px-0">
//                                                <i class="bi bi-linkedin"></i> LinkedIn Profile
//                                            </a>
//                                        ` : ''}
//                                    </div>
//                                    <button class="btn btn-sm btn-success ms-2"
//                                            onclick="enrichAndAddContact('${customerId}', ${JSON.stringify(lead).replace(/"/g, '&quot;')})">
//                                        <i class="bi bi-plus-circle"></i> Add Contact
//                                    </button>
//                                </div>
//                            </div>
//                        `).join('')}
//                    </div>
//                `;
//            }
//
//            leadsSection.innerHTML = content;
//        })
//        .catch(error => {
//            leadsSection.innerHTML = `
//                <div class="alert alert-danger">
//                    <i class="bi bi-exclamation-triangle"></i>
//                    Error: ${error.message || 'Failed to search leads'}
//                </div>
//            `;
//        });
//};
//
///**
// * Enrich contact data and add to customer
// */
///**
// * Enrich contact data and add to customer
// */
//window.enrichAndAddContact = function(customerId, lead) {
//    const button = event.target.closest('button');
//
//    // Show loading state
//    const originalContent = button.innerHTML;
//    button.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading...';
//    button.disabled = true;
//
//    // First, enrich the contact data
//    fetch('/customers/enrich_person', {
//        method: 'POST',
//        headers: {
//            'Content-Type': 'application/json',
//        },
//        body: JSON.stringify({
//            apollo_id: lead.id
//        })
//    })
//    .then(response => response.json())
//    .then(enrichData => {
//        if (!enrichData.success) {
//            throw new Error(enrichData.error || 'Failed to enrich contact data');
//        }
//
//        // Check if we got an email
//        if (!enrichData.data.email) {
//            button.innerHTML = originalContent;
//            button.disabled = false;
//            alert('No email address available for this contact');
//            return Promise.reject(new Error('No email available'));
//        }
//
//        // Then add the contact with the enriched data
//        const contactData = {
//            name: enrichData.data.name || lead.name,
//            email: enrichData.data.email,
//            job_title: enrichData.data.title || lead.title,
//            company_id: customerId  // Fixed: Changed 'company' to 'company_id'
//        };
//
//        return fetch(`/customers/${customerId}/add_contact`, {
//            method: 'POST',
//            headers: {
//                'Content-Type': 'application/json',
//            },
//            body: JSON.stringify(contactData)
//        })
//        .then(response => response.json())
//        .then(addData => {
//            if (addData.success) {
//                // Create a container div for the buttons
//                const buttonContainer = document.createElement('div');
//                buttonContainer.className = 'btn-group';
//
//                // Add the "Added" status button
//                const addedButton = document.createElement('button');
//                addedButton.className = 'btn btn-sm btn-secondary';
//                addedButton.innerHTML = '<i class="bi bi-check-circle"></i> Added';
//                addedButton.disabled = true;
//
//                // Add the "Send Email" button
//                const emailButton = document.createElement('button');
//                emailButton.className = 'btn btn-sm btn-outline-primary';
//                emailButton.setAttribute('data-bs-toggle', 'modal');
//                emailButton.setAttribute('data-bs-target', '#emailModal');
//                emailButton.setAttribute('data-contact-id', addData.contact_id);
//                emailButton.setAttribute('data-contact-name', contactData.name);
//                emailButton.setAttribute('data-contact-email', contactData.email);
//                emailButton.setAttribute('data-customer-id', customerId);
//                emailButton.innerHTML = 'Send Email';
//
//                // Add both buttons to the container
//                buttonContainer.appendChild(addedButton);
//                buttonContainer.appendChild(emailButton);
//
//                // Replace the original button with the button container
//                button.parentNode.replaceChild(buttonContainer, button);
//
//                // Show toast notification
//                const toast = new bootstrap.Toast(createToast('Contact added successfully!'));
//                toast.show();
//            } else {
//                throw new Error(addData.error || 'Failed to add contact');
//            }
//        });
//    })
//    .catch(error => {
//        console.error('Error:', error);
//        // Skip alert if we already handled the no-email case
//        if (error.message !== 'No email available') {
//            // Reset button state
//            button.innerHTML = originalContent;
//            button.disabled = false;
//            alert('Error adding contact: ' + error.message);
//        }
//    });
//};
///**
// * Helper function to create toast notification
// */
//function createToast(message) {
//    const toastContainer = document.querySelector('.toast-container') || (() => {
//        const container = document.createElement('div');
//        container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
//        document.body.appendChild(container);
//        return container;
//    })();
//
//    const toastElement = document.createElement('div');
//    toastElement.className = 'toast';
//    toastElement.setAttribute('role', 'alert');
//    toastElement.setAttribute('aria-live', 'assertive');
//    toastElement.setAttribute('aria-atomic', 'true');
//    toastElement.innerHTML = `
//        <div class="toast-header">
//            <strong class="me-auto">Notification</strong>
//            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
//        </div>
//        <div class="toast-body">
//            ${message}
//        </div>
//    `;
//
//    toastContainer.appendChild(toastElement);
//    return toastElement;
//}
//
///**
// * Initialize the modal when DOM is loaded
// */
//document.addEventListener('DOMContentLoaded', function() {
//    const modal = document.getElementById('customerModal');
//    if (!modal) return;
//
//    // Reset modal when hidden - using both events for redundancy
//    modal.addEventListener('hidden.bs.modal', function() {
//        console.log("Modal hidden event triggered - resetting state");
//        resetModalState();
//    });
//
//    // Also reset when the close button is clicked
//    const closeButtons = modal.querySelectorAll('[data-bs-dismiss="modal"]');
//    closeButtons.forEach(button => {
//        button.addEventListener('click', function() {
//            console.log("Close button clicked - scheduling reset");
//            // Schedule a reset for after the modal is fully closed
//            setTimeout(resetModalState, 300);
//        });
//    });
//
//    // Setup Apollo search input
//    const searchInput = modal.querySelector('#companySearchInput');
//    const searchButton = modal.querySelector('#apolloSearchButton');
//
//    if (searchInput && searchButton) {
//        searchInput.addEventListener('input', function() {
//            searchButton.disabled = !this.value.trim();
//        });
//
//        searchInput.addEventListener('keypress', function(e) {
//            if (e.key === 'Enter' && this.value.trim()) {
//                e.preventDefault();
//                const customerId = modal.querySelector('#currentCustomerId')?.value;
//                if (customerId) {
//                    performApolloSearch(customerId);
//                }
//            }
//        });
//
//        searchButton.addEventListener('click', function() {
//            const customerId = modal.querySelector('#currentCustomerId')?.value;
//            if (customerId && searchInput.value.trim()) {
//                performApolloSearch(customerId);
//            }
//        });
//    }
//});
//
//// Add this function to your JavaScript
//function diagnoseModalFields() {
//  const modal = document.getElementById('customerModal');
//  if (!modal) {
//    console.error("Modal not found!");
//    return;
//  }
//
//  console.log("=== MODAL FIELD DIAGNOSIS ===");
//
//  // Check all input, select, and textarea elements in the modal
//  const allFields = modal.querySelectorAll('input, select, textarea');
//  console.log(`Found ${allFields.length} form fields in total`);
//
//  // Log all fields with their IDs
//  allFields.forEach(field => {
//    console.log(`Field: ${field.tagName} | ID: ${field.id || '[no id]'} | Name: ${field.name || '[no name]'}`);
//  });
//
//  // Specifically check for our expected field IDs
//  const expectedIds = [
//    'editCustomerName',
//    'editCustomerDescription',
//    'editEstimatedRevenue',
//    'estimatedRevenue',
//    'editCountry',
//    'country',
//    'editSalesperson',
//    'salesperson',
//    'selectedTagId'
//  ];
//
//  console.log("=== CHECKING EXPECTED FIELDS ===");
//  expectedIds.forEach(id => {
//    const found = modal.querySelector(`#${id}`);
//    console.log(`Field #${id}: ${found ? "FOUND ✓" : "NOT FOUND ✗"}`);
//  });
//
//  console.log("=== END OF DIAGNOSIS ===");
//}
//
//// Call this after the modal is shown
//setTimeout(diagnoseModalFields, 500);
//
//// Add this function to any of your script files (or create a new one)
//window.fixCustomerModalData = function(data) {
//  // Set a timeout to give the modal time to appear first
//  setTimeout(function() {
//    console.log("Fixing customer modal data:", data);
//
//    // Get the modal element
//    const modal = document.getElementById('customerModal');
//    if (!modal) {
//      console.error("Customer modal not found");
//      return;
//    }
//
//    // Update the fields directly
//    const nameField = modal.querySelector('#editCustomerName');
//    const descField = modal.querySelector('#editCustomerDescription');
//    const revenueField = modal.querySelector('#editEstimatedRevenue');
//    const countryField = modal.querySelector('#editCountry');
//    const tagInput = modal.querySelector('#selectedTagId');
//
//    // Set values if fields exist
//    if (nameField) nameField.value = data.name || '';
//    if (descField) descField.value = data.description || '';
//    if (revenueField) revenueField.value = data.revenue || '';
//    if (countryField) countryField.value = data.country || '';
//
//    // Set tag ID if provided
//    if (tagInput && data.tagId) {
//      tagInput.value = data.tagId;
//    }
//
//    console.log("Modal fields updated successfully");
//  }, 100); // Small delay to ensure modal is visible
//};
//
//// Modify the existing openCustomerModal function
//if (typeof originalOpenCustomerModal === 'undefined') {
//  const originalOpenCustomerModal = window.openCustomerModal;
//  window.openCustomerModal = function(data) {
//    // Call the original function
//    const result = originalOpenCustomerModal(data);
//
//    // Then fix the data
//    window.fixCustomerModalData(data);
//
//    return result;
//  };
//}