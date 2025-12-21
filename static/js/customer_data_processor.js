/**
 * Customer Data Processor - Adapts data formats for the customer modal
 * This script intercepts company data and ensures it's properly formatted
 * before passing it to the existing openCustomerModal function
 */
(function() {
    // Save reference to original function
    const originalOpenCustomerModal = window.openCustomerModal;

    // Enhanced data processor function
    window.processCustomerData = async function(data) {
        console.log("Processing customer data:", data);

        // Clone the data to avoid modifying the original object
        let processedData = typeof data === 'string' ? JSON.parse(data) : {...data};

        // Handle company/name field normalization
        if (processedData.company_name && !processedData.name) {
            processedData.name = processedData.company_name;
        }

        // Handle revenue normalization
        if (typeof processedData.estimated_revenue === 'string' && processedData.estimated_revenue) {
            // Extract numeric value from revenue string (remove currency symbols, commas, etc.)
            processedData.revenue = processedData.estimated_revenue.replace(/[^0-9.]/g, '');
        } else if (processedData.estimated_revenue && !processedData.revenue) {
            processedData.revenue = processedData.estimated_revenue;
        }

        // Handle country code lookup if needed
        if (processedData.country && processedData.country.length > 2 && !/^[A-Z]{2}$/.test(processedData.country)) {
            try {
                const response = await fetch(`/customers/api/country-lookup?name=${encodeURIComponent(processedData.country)}`);
                if (response.ok) {
                    const result = await response.json();
                    if (result.code) {
                        processedData.country = result.code;
                    }
                }
            } catch (error) {
                console.warn("Country lookup failed:", error);
                // Let the original function handle the country string
            }
        }

        // Handle tag ID normalization
        if (processedData.tag_id && !processedData.tagId) {
            processedData.tagId = processedData.tag_id;
        }

        // Call the original function with processed data
        return originalOpenCustomerModal(processedData);
    };

    // Override click handlers for Add buttons to use our processor
    document.addEventListener('DOMContentLoaded', function() {
        const addButtonHandler = () => {
            document.querySelectorAll('button.btn-success:not([data-processor-attached])').forEach(button => {
                // Check if this is an "Add" button with customer data
                if (button.innerHTML.includes('Add') || button.innerHTML.includes('plus-circle')) {
                    // Get the original onclick handler
                    const originalOnClick = button.getAttribute('onclick');
                    if (originalOnClick && originalOnClick.includes('openCustomerModal')) {
                        // Extract the data from the onclick
                        const dataMatch = originalOnClick.match(/openCustomerModal\((.*)\)/);
                        if (dataMatch && dataMatch[1]) {
                            // Replace with our processor
                            button.setAttribute('onclick', `processCustomerData(${dataMatch[1]})`);
                            button.setAttribute('data-processor-attached', 'true');
                        }
                    }
                }
            });
        };

        // Run initially and set up a mutation observer to catch dynamically added buttons
        addButtonHandler();

        // Set up mutation observer to watch for new buttons
        const observer = new MutationObserver(mutations => {
            for (const mutation of mutations) {
                if (mutation.type === 'childList') {
                    addButtonHandler();
                }
            }
        });

        // Start observing the document
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    });
})();