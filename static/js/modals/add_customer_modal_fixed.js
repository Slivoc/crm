// Add this country mapping object at the top of your customer modal script
const COUNTRY_NAME_TO_ISO = {
    'United States': 'US',
    'United Kingdom': 'GB',
    'Canada': 'CA',
    'Australia': 'AU',
    'Germany': 'DE',
    'France': 'FR',
    'Italy': 'IT',
    'Spain': 'ES',
    'Netherlands': 'NL',
    'Belgium': 'BE',
    'Switzerland': 'CH',
    'Austria': 'AT',
    'Sweden': 'SE',
    'Norway': 'NO',
    'Denmark': 'DK',
    'Finland': 'FI',
    'Poland': 'PL',
    'Czech Republic': 'CZ',
    'Hungary': 'HU',
    'Slovakia': 'SK',
    'Slovenia': 'SI',
    'Croatia': 'HR',
    'Romania': 'RO',
    'Bulgaria': 'BG',
    'Greece': 'GR',
    'Portugal': 'PT',
    'Ireland': 'IE',
    'Luxembourg': 'LU',
    'Estonia': 'EE',
    'Latvia': 'LV',
    'Lithuania': 'LT',
    'Malta': 'MT',
    'Cyprus': 'CY',
    'Japan': 'JP',
    'South Korea': 'KR',
    'China': 'CN',
    'India': 'IN',
    'Singapore': 'SG',
    'Hong Kong': 'HK',
    'Taiwan': 'TW',
    'Thailand': 'TH',
    'Malaysia': 'MY',
    'Indonesia': 'ID',
    'Philippines': 'PH',
    'Vietnam': 'VN',
    'South Africa': 'ZA',
    'Brazil': 'BR',
    'Mexico': 'MX',
    'Argentina': 'AR',
    'Chile': 'CL',
    'Colombia': 'CO',
    'Peru': 'PE',
    'Venezuela': 'VE',
    'Ecuador': 'EC',
    'Uruguay': 'UY',
    'Paraguay': 'PY',
    'Bolivia': 'BO',
    'Israel': 'IL',
    'Turkey': 'TR',
    'Russia': 'RU',
    'Ukraine': 'UA',
    'Belarus': 'BY',
    'Kazakhstan': 'KZ',
    'Egypt': 'EG',
    'Saudi Arabia': 'SA',
    'United Arab Emirates': 'AE',
    'Qatar': 'QA',
    'Kuwait': 'KW',
    'Bahrain': 'BH',
    'Oman': 'OM',
    'Jordan': 'JO',
    'Lebanon': 'LB',
    'Morocco': 'MA',
    'Tunisia': 'TN',
    'Algeria': 'DZ',
    'Nigeria': 'NG',
    'Kenya': 'KE',
    'Ghana': 'GH',
    'Ethiopia': 'ET',
    'New Zealand': 'NZ'
};

// Function to convert country name to ISO code
function getCountryISOCode(countryName) {
    if (!countryName) return '';

    // If it's already a 2-digit code, return it
    if (countryName.length === 2 && countryName.match(/^[A-Z]{2}$/)) {
        return countryName;
    }

    // Try to find exact match first
    let isoCode = COUNTRY_NAME_TO_ISO[countryName];
    if (isoCode) return isoCode;

    // Try case-insensitive match
    const lowerCountry = countryName.toLowerCase();
    for (const [name, code] of Object.entries(COUNTRY_NAME_TO_ISO)) {
        if (name.toLowerCase() === lowerCountry) {
            return code;
        }
    }

    // Try partial match (for cases like "United States of America")
    for (const [name, code] of Object.entries(COUNTRY_NAME_TO_ISO)) {
        if (countryName.toLowerCase().includes(name.toLowerCase()) ||
            name.toLowerCase().includes(countryName.toLowerCase())) {
            return code;
        }
    }

    // If no match found, return empty string
    return '';
}

// Updated selectApolloCompany function - now focuses on Apollo data, not notes
window.selectApolloCompany = function(element) {
    console.log('selectApolloCompany called', element);
    const apolloId = element.dataset.apolloId;
    const apolloSearchResults = document.getElementById('apolloSearchResults');
    const organizations = JSON.parse(apolloSearchResults?.getAttribute('data-organizations') || '[]');
    const selectedOrg = organizations.find(org => org.id === apolloId);

    if (!selectedOrg) {
        console.error('Selected organization not found');
        return;
    }

    selectedApolloData = selectedOrg;

    // Fill form fields
    document.getElementById('addCustomerName').value = selectedOrg.name || '';
    document.getElementById('addCustomerWebsite').value = selectedOrg.website || '';
    document.getElementById('addCustomerApolloId').value = selectedOrg.id || '';

    // Convert country name to ISO code
    const countryISO = getCountryISOCode(selectedOrg.country || '');
    document.getElementById('addCustomerCountry').value = countryISO;

    // Log for debugging
    console.log('Country conversion:', selectedOrg.country, '->', countryISO);
    console.log('Logo URL:', selectedOrg.logo_url);

    // Don't auto-fill notes - let user add their own context
    // The notes field stays empty for user to add their own setup notes

    // Show selected company info
    displaySelectedApolloCompany(selectedOrg);

    // Clear search and hide results
    const apolloSearchInput = document.getElementById('addCustomerApolloSearchInput');
    if (apolloSearchInput) apolloSearchInput.value = '';
    hideApolloResults();
};

// Ensure this runs immediately when the script loads
(function() {
    console.log('Customer modal script loading...');

    // Helper function to safely get a Bootstrap modal instance
    function getModalInstance(modalId) {
        const modalElement = document.getElementById(modalId);
        if (!modalElement) {
            console.warn(`Modal element #${modalId} not found in the DOM`);
            return null;
        }

        try {
            return bootstrap.Modal.getOrCreateInstance(modalElement);
        } catch (error) {
            console.warn(`Error creating modal instance for #${modalId}:`, error);
            return null;
        }
    }

    // Show modal function - define it immediately
function showAddCustomerModal(prefillName = null) {
    console.log('showAddCustomerModal called with prefillName:', prefillName);

    const customerNameInput = document.getElementById('addCustomerName');
    const apolloSearchInput = document.getElementById('addCustomerApolloSearchInput');

    // Option 1: Use passed parameter if provided
    if (prefillName && prefillName.trim()) {
        if (customerNameInput) {
            customerNameInput.value = prefillName.trim();
        }
        if (apolloSearchInput) {
            apolloSearchInput.value = prefillName.trim();
        }
        console.log('Pre-filled customer name from parameter:', prefillName);
    }
    // Option 2: Fall back to search input if no parameter
    else {
        const customerSearchInput = document.querySelector('input[name="customer_search"]');
        if (customerSearchInput && customerNameInput) {
            const searchValue = customerSearchInput.value.trim();
            if (searchValue) {
                customerNameInput.value = searchValue;
                if (apolloSearchInput) {
                    apolloSearchInput.value = searchValue;
                }
                console.log('Pre-filled customer name from search input:', searchValue);
            }
        }
    }

    // Show the modal
    const modal = getModalInstance('addCustomerModal');
    if (modal) {
        console.log('Showing modal...');
        modal.show();

        // Trigger Apollo search after modal is shown if we have a value
        setTimeout(() => {
            if (apolloSearchInput && apolloSearchInput.value.trim()) {
                const event = new Event('input', { bubbles: true });
                apolloSearchInput.dispatchEvent(event);
            }
        }, 300);
    } else {
        console.error('Cannot show customer modal - modal instance not found');
        // Fallback: try to show modal directly
        const modalElement = document.getElementById('addCustomerModal');
        if (modalElement) {
            try {
                const fallbackModal = new bootstrap.Modal(modalElement);
                fallbackModal.show();
                console.log('Fallback modal shown');
            } catch (error) {
                console.error('Fallback modal failed:', error);
            }
        }
    }
}

// Also create the legacy function name for compatibility
function openAddCustomerModal(customerName = null) {
    showAddCustomerModal(customerName);
}

    // Make functions globally available immediately
    window.showAddCustomerModal = showAddCustomerModal;
    window.openAddCustomerModal = openAddCustomerModal;

    console.log('Customer modal functions registered:', {
        showAddCustomerModal: typeof window.showAddCustomerModal,
        openAddCustomerModal: typeof window.openAddCustomerModal
    });

    // Global variables for Apollo search
    let selectedApolloData = null;

   // Update your selectApolloCompany function to use this converter
// Updated selectApolloCompany function - now focuses on Apollo data, not notes
window.selectApolloCompany = function(element) {
    console.log('selectApolloCompany called', element);
    const apolloId = element.dataset.apolloId;
    const apolloSearchResults = document.getElementById('apolloSearchResults');
    const organizations = JSON.parse(apolloSearchResults?.getAttribute('data-organizations') || '[]');
    const selectedOrg = organizations.find(org => org.id === apolloId);

    if (!selectedOrg) {
        console.error('Selected organization not found');
        return;
    }

    selectedApolloData = selectedOrg;

    // Fill form fields
    document.getElementById('addCustomerName').value = selectedOrg.name || '';
    document.getElementById('addCustomerWebsite').value = selectedOrg.website || '';
    document.getElementById('addCustomerApolloId').value = selectedOrg.id || '';

    // Convert country name to ISO code
    const countryISO = getCountryISOCode(selectedOrg.country || '');
    document.getElementById('addCustomerCountry').value = countryISO;

    // Log for debugging
    console.log('Country conversion:', selectedOrg.country, '->', countryISO);
    console.log('Logo URL:', selectedOrg.logo_url);

    // Don't auto-fill notes - let user add their own context
    // The notes field stays empty for user to add their own setup notes

    // Show selected company info
    displaySelectedApolloCompany(selectedOrg);

    // Clear search and hide results
    const apolloSearchInput = document.getElementById('addCustomerApolloSearchInput');
    if (apolloSearchInput) apolloSearchInput.value = '';
    hideApolloResults();
};

    // Updated clearApolloSelection function
window.clearApolloSelection = function() {
    selectedApolloData = null;
    const selectedApolloCompany = document.getElementById('selectedApolloCompany');
    if (selectedApolloCompany) selectedApolloCompany.classList.add('d-none');

    // Clear form fields that were auto-filled
    document.getElementById('addCustomerName').value = '';
    document.getElementById('addCustomerWebsite').value = '';
    document.getElementById('addCustomerApolloId').value = '';
    document.getElementById('addCustomerCountry').value = '';
    // Don't clear notes - user might have added their own context
};

    // Updated displaySelectedApolloCompany function (replace the existing one)
// Add the displaySelectedApolloCompany function
function displaySelectedApolloCompany(org) {
    const apolloCompanyDetails = document.getElementById('apolloCompanyDetails');
    const selectedApolloCompany = document.getElementById('selectedApolloCompany');

    if (!apolloCompanyDetails || !selectedApolloCompany) return;

    apolloCompanyDetails.innerHTML = `
        <div class="row">
            <div class="col-md-2">
                ${org.logo_url ? `<img src="${org.logo_url}" alt="${org.name} logo" class="img-fluid rounded" style="max-height: 60px;">` : '<div class="bg-light rounded d-flex align-items-center justify-content-center" style="height: 60px; width: 60px;"><i class="bi bi-building text-muted"></i></div>'}
            </div>
            <div class="col-md-5">
                <strong>${org.name}</strong><br>
                ${org.website ? `<a href="${org.website}" target="_blank">${org.website}</a><br>` : ''}
                ${org.country ? `<span class="text-muted">Country: ${org.country}</span><br>` : ''}
                ${org.description ? `<span class="text-muted">${org.description}</span><br>` : ''}
            </div>
            <div class="col-md-5">
                ${org.employee_count ? `<span class="text-muted">Employees: ${org.employee_count}</span><br>` : ''}
                ${org.domain ? `<span class="text-muted">Domain: ${org.domain}</span><br>` : ''}
                ${org.linkedin_url ? `<a href="${org.linkedin_url}" target="_blank" class="text-muted">LinkedIn</a><br>` : ''}
                ${org.logo_url ? `<span class="text-muted">Logo: Available</span><br>` : ''}
            </div>
        </div>
    `;
    selectedApolloCompany.classList.remove('d-none');
}

    function hideApolloResults() {
        const apolloSearchResults = document.getElementById('apolloSearchResults');
        if (apolloSearchResults) apolloSearchResults.style.display = 'none';
    }

    // DOM ready handler
    function initializeCustomerModal() {
        console.log('Initializing customer modal...');

        // Get DOM elements
        const addCustomerForm = document.getElementById('addCustomerForm');
        const saveCustomerBtn = document.getElementById('saveCustomerBtn');
        const customerCountry = document.getElementById('addCustomerCountry');

        // Apollo search elements
        const apolloSearchInput = document.getElementById('addCustomerApolloSearchInput');
        const apolloSearchResults = document.getElementById('apolloSearchResults');
        const apolloSearchLoading = document.getElementById('apolloSearchLoading');
        const selectedApolloCompany = document.getElementById('selectedApolloCompany');

        // Apollo search variables
        let apolloSearchTimeout;

        // Check if elements exist
        if (!addCustomerForm || !saveCustomerBtn) {
            console.log("Customer form elements not found on this page");
            return;
        }

        console.log('Customer modal elements found, setting up handlers...');

        // Convert country input to uppercase
        customerCountry?.addEventListener('input', function() {
            this.value = this.value.toUpperCase();
        });

        // Apollo search functionality
        if (apolloSearchInput) {
            console.log('Apollo search input found, setting up event listener');
            apolloSearchInput.addEventListener('input', function() {
                const searchTerm = this.value.trim();
                console.log('Search term:', searchTerm);

                clearTimeout(apolloSearchTimeout);

                if (searchTerm.length < 2) {
                    hideApolloResults();
                    return;
                }

                apolloSearchTimeout = setTimeout(() => {
                    searchApollo(searchTerm);
                }, 500);
            });
        } else {
            console.warn('Apollo search input not found');
        }

        // Hide results when clicking outside
        document.addEventListener('click', function(e) {
            if (!e.target.closest('#addCustomerApolloSearchInput') && !e.target.closest('#apolloSearchResults')) {
                hideApolloResults();
            }
        });

        // Apollo search functions
        async function searchApollo(searchTerm) {
            try {
                showApolloLoading();

                console.log('Searching Apollo for:', searchTerm);

                const response = await fetch('/api/apollo-search-general', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': typeof API_KEY !== 'undefined' ? API_KEY : ''
                    },
                    body: JSON.stringify({ q_organization_name: searchTerm })
                });

                console.log('Response status:', response.status);

                if (!response.ok) {
                    throw new Error('Apollo search failed');
                }

                const data = await response.json();
                console.log('Apollo response:', data);

                if (data.success) {
                    displayApolloResults(data.data?.organizations || []);
                } else {
                    throw new Error(data.error || 'Search failed');
                }
            } catch (error) {
                console.error('Error searching Apollo:', error);
                showApolloError('Failed to search Apollo. Please try again.');
            } finally {
                hideApolloLoading();
            }
        }

        function showApolloLoading() {
            apolloSearchLoading?.classList.remove('d-none');
            if (apolloSearchResults) apolloSearchResults.style.display = 'none';
        }

        function hideApolloLoading() {
            apolloSearchLoading?.classList.add('d-none');
        }

        function displayApolloResults(organizations) {
            if (!apolloSearchResults) return;

            if (!organizations.length) {
                apolloSearchResults.innerHTML = `
                    <div class="p-3 text-center text-muted">
                        <i class="bi bi-search"></i>
                        <p class="mb-0">No companies found</p>
                    </div>
                `;
                apolloSearchResults.style.display = 'block';
                return;
            }

            const resultsHtml = organizations.map(org => `
                <div class="apollo-search-item" data-apollo-id="${org.id}" onclick="selectApolloCompany(this)">
                    <div class="company-name">${org.name}</div>
                    <div class="company-details">
                        ${org.website ? `<a href="${org.website}" target="_blank" class="company-website" onclick="event.stopPropagation()">${org.website}</a><br>` : ''}
                        ${org.employee_count ? `<span class="me-3"><i class="bi bi-people"></i> ${org.employee_count} employees</span>` : ''}
                        ${org.country ? `<span class="me-3"><i class="bi bi-geo-alt"></i> ${org.country}</span>` : ''}
                        ${org.domain ? `<span class="me-3"><i class="bi bi-globe"></i> ${org.domain}</span>` : ''}
                    </div>
                </div>
            `).join('');

            apolloSearchResults.innerHTML = resultsHtml;
            apolloSearchResults.style.display = 'block';
            apolloSearchResults.setAttribute('data-organizations', JSON.stringify(organizations));
        }

        function showApolloError(message) {
            if (!apolloSearchResults) return;

            apolloSearchResults.innerHTML = `
                <div class="p-3 text-center text-danger">
                    <i class="bi bi-exclamation-triangle"></i>
                    <p class="mb-0">${message}</p>
                </div>
            `;
            apolloSearchResults.style.display = 'block';
        }

        // Form submission handler
        // Form submission handler (replace the existing one in your customer modal script)
saveCustomerBtn.addEventListener('click', async function(e) {
    e.preventDefault();

    // Basic form validation
    const nameInput = addCustomerForm.querySelector('#addCustomerName');
    if (!nameInput?.value.trim()) {
        showToast('Error', 'Company name is required', 'error');
        nameInput?.focus();
        return;
    }

    // Collect form data
    const formData = {
        name: nameInput.value.trim(),
        country: addCustomerForm.querySelector('#addCustomerCountry')?.value || '',
        apollo_id: addCustomerForm.querySelector('#addCustomerApolloId')?.value || '',
        website: addCustomerForm.querySelector('#addCustomerWebsite')?.value || '',
        notes: addCustomerForm.querySelector('#addCustomerNotes')?.value || '',  // Changed from description to notes
        payment_terms: addCustomerForm.querySelector('#addCustomerPaymentTerms')?.value || 'Pro-forma',
        incoterms: addCustomerForm.querySelector('#addCustomerIncoterms')?.value || 'EXW'
    };

    // Add logo_url from Apollo data if available
    if (selectedApolloData && selectedApolloData.logo_url) {
        formData.logo_url = selectedApolloData.logo_url;
    }

    // Add Apollo data if available
    if (selectedApolloData) {
        formData.apollo_data = selectedApolloData;
    }

    try {
        const response = await fetch('/customers/customers/new', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(formData)
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const result = await response.json();

        if (result.success) {
            // Handle success
            handleCustomerAddSuccess(result);
        } else {
            throw new Error(result.error || 'Failed to add customer');
        }
    } catch (error) {
        console.error('Error adding customer:', error);
        showToast('Error', error.message || 'Failed to add customer', 'error');
    }
});

        // Success handler function
        // Success handler function
function handleCustomerAddSuccess(result) {
    // Store the created customer ID
    const createdCustomerId = result.customer_id;
    const createdCustomerName = result.customer_name;

    // If there's a select widget, update it
    if (window.customerSelect?.addOption) {
        try {
            window.customerSelect.addOption({
                value: result.customer_id,
                text: result.customer_name
            });
            window.customerSelect.setValue(result.customer_id);
        } catch (error) {
            console.warn('Select widget update failed:', error);
        }
    }

    // Show success state with action buttons
    showCustomerSuccessState(createdCustomerId, createdCustomerName);

    // Refresh customer list if the function exists
    if (typeof refreshCustomerList === 'function') {
        refreshCustomerList();
    } else if (typeof loadCustomers === 'function') {
        loadCustomers(1);
    }
}
// New function to show success state
function showCustomerSuccessState(customerId, customerName) {
    const addCustomerModal = document.getElementById('addCustomerModal');
    const modalBody = addCustomerModal.querySelector('.modal-body');
    const modalFooter = addCustomerModal.querySelector('.modal-footer');

    // Hide form and footer
    addCustomerForm.style.display = 'none';
    modalFooter.style.display = 'none';

    // Create success state div
    const successDiv = document.createElement('div');
    successDiv.id = 'customerSuccessState';
    successDiv.className = 'text-center py-4';
    successDiv.innerHTML = `
        <div class="mb-4">
            <i class="bi bi-check-circle-fill text-success" style="font-size: 4rem;"></i>
            <h5 class="mt-3">Customer Created Successfully!</h5>
            <p class="text-muted">${customerName}</p>
        </div>
        <div class="d-grid gap-2">
            <button type="button" class="btn btn-primary btn-lg" id="findLeadsBtn">
                <i class="bi bi-search me-2"></i>Find Leads for This Customer
            </button>
            <button type="button" class="btn btn-outline-secondary" id="viewCustomerBtn">
                <i class="bi bi-eye me-2"></i>View Customer Details
            </button>
            <button type="button" class="btn btn-outline-secondary" id="closeSuccessBtn">
                <i class="bi bi-x-circle me-2"></i>Close
            </button>
        </div>
    `;

    modalBody.appendChild(successDiv);

    // Add event listeners for new buttons
    document.getElementById('findLeadsBtn').addEventListener('click', function() {
        // Close the modal
        const modal = getModalInstance('addCustomerModal');
        if (modal) modal.hide();

        // Wait for modal to close, then open Apollo Lead Finder
        setTimeout(() => {
            if (window.apolloLeadFinder) {
                window.apolloLeadFinder.show(customerId);
            } else if (window.showApolloLeadFinder) {
                window.showApolloLeadFinder(customerId);
            } else {
                console.error('Apollo Lead Finder not available');
                showToast('Error', 'Apollo Lead Finder is not available', 'error');
            }
        }, 300);
    });

    document.getElementById('viewCustomerBtn').addEventListener('click', function() {
        window.location.href = `/customers/${customerId}/edit`;
    });

    document.getElementById('closeSuccessBtn').addEventListener('click', function() {
        const modal = getModalInstance('addCustomerModal');
        if (modal) modal.hide();
    });
}

// Function to reset modal to initial state
function resetCustomerModal() {
    // Remove success state if exists
    const successState = document.getElementById('customerSuccessState');
    if (successState) {
        successState.remove();
    }

    // Show form and footer again
    addCustomerForm.style.display = 'block';
    const modalFooter = document.getElementById('addCustomerModal').querySelector('.modal-footer');
    if (modalFooter) modalFooter.style.display = 'flex';

    // Reset form
    addCustomerForm.reset();
    window.clearApolloSelection();
    hideApolloResults();
}
        // Toast notification function
        function showToast(title, message, type = 'success') {
            const toastContainer = document.getElementById('toastContainer');
            if (!toastContainer) return;

            const toastHTML = `
                <div class="toast align-items-center text-white bg-${type === 'success' ? 'success' : 'danger'} border-0"
                     role="alert"
                     aria-live="assertive"
                     aria-atomic="true">
                    <div class="d-flex">
                        <div class="toast-body">
                            <strong>${title}:</strong> ${message}
                        </div>
                        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
                    </div>
                </div>`;

            toastContainer.insertAdjacentHTML('beforeend', toastHTML);
            const toastElement = toastContainer.lastElementChild;
            const toast = new bootstrap.Toast(toastElement, {
                autohide: true,
                delay: 3000
            });
            toast.show();

            toastElement.addEventListener('hidden.bs.toast', function() {
                this.remove();
            });
        }

        // Reset form when modal is hidden
        // Reset form when modal is hidden
const addCustomerModal = document.getElementById('addCustomerModal');
if (addCustomerModal) {
    addCustomerModal.addEventListener('hidden.bs.modal', function() {
        resetCustomerModal();
    });
}

        console.log('Customer modal initialization complete');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            const t0 = performance.now();
            if (console.time) {
                console.time('init.add_customer_modal');
            }
            initializeCustomerModal();
            if (console.timeEnd) {
                console.timeEnd('init.add_customer_modal');
            }
            console.log(`init.add_customer_modal ${Math.round(performance.now() - t0)}ms`);
        });
    } else {
        initializeCustomerModal();
    }

    console.log('Customer modal script loaded');
})();
