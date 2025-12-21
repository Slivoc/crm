// Create a global state manager for salesperson data
class SalespersonManager {
    constructor() {
        this.callbacks = [];
        // Try to get from localStorage, otherwise get from dropdown's selected value
        this.currentSalespersonId = this.getCurrentSalespersonFromPage();
        this.initializeEventListener();
    }

    getCurrentSalespersonFromPage() {
        // First try localStorage
        const storedId = localStorage.getItem('salesperson_id');
        if (storedId) return storedId;

        // If no stored ID, try to get from dropdown
        const dropdown = document.getElementById('salesperson_select');
        if (dropdown && dropdown.value) {
            localStorage.setItem('salesperson_id', dropdown.value);
            return dropdown.value;
        }

        return null;
    }

    initializeEventListener() {
        const dropdown = document.getElementById('salesperson_select');
        if (dropdown) {
            // Set initial value if none selected
            if (!dropdown.value && this.currentSalespersonId) {
                dropdown.value = this.currentSalespersonId;
            }

            dropdown.addEventListener('change', (e) => {
                this.setCurrentSalesperson(e.target.value);
            });
        }
    }

    setCurrentSalesperson(id) {
        if (!id) return; // Don't set empty IDs

        this.currentSalespersonId = id;
        localStorage.setItem('salesperson_id', id);

        // Dispatch a custom event
        const event = new CustomEvent('salespersonChanged', {
            detail: { salespersonId: id }
        });
        document.dispatchEvent(event);

        // Execute all registered callbacks
        this.callbacks.forEach(callback => callback(id));
    }

    getCurrentSalesperson() {
        // If no current ID, try to get it from the page
        if (!this.currentSalespersonId) {
            this.currentSalespersonId = this.getCurrentSalespersonFromPage();
        }
        return this.currentSalespersonId;
    }

    onSalespersonChange(callback) {
        this.callbacks.push(callback);
    }
}

// Initialize the manager
document.addEventListener('DOMContentLoaded', () => {
    window.salespersonManager = new SalespersonManager();

    // Add salesperson_id to all AJAX requests
    $(document).ajaxSend(function(e, xhr, options) {
        const salespersonId = window.salespersonManager.getCurrentSalesperson();
        if (salespersonId) {
            // Append to URL parameters
            const separator = options.url.includes('?') ? '&' : '?';
            options.url += `${separator}salesperson_id=${salespersonId}`;

            // Also add to headers if needed
            xhr.setRequestHeader('X-Salesperson-ID', salespersonId);
        }
    });
});