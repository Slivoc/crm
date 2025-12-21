/**
 * Global Bootstrap Modal Fix
 *
 * This script fixes issues with Bootstrap modals by patching the core methods
 * to handle edge cases and prevent "Cannot read properties of null" errors.
 *
 * Add this script *after* Bootstrap is loaded but *before* your other scripts.
 */

(function() {
    // Only run if Bootstrap is available
    if (typeof bootstrap === 'undefined' || !bootstrap.Modal) {
        console.warn('Bootstrap Modal not found, fix not applied');
        return;
    }

    console.log('Applying global Bootstrap Modal fixes');

    // 1. Fix the getInstance method to never return null
    const originalGetInstance = bootstrap.Modal.getInstance;
    bootstrap.Modal.getInstance = function(element) {
        try {
            return originalGetInstance(element);
        } catch (error) {
            console.warn('Error in Modal.getInstance, returning null safely:', error.message);
            return null;
        }
    };

    // 2. Fix the core component DATA_KEY issue
    if (bootstrap.BaseComponent) {
        const originalBaseGetInstance = bootstrap.BaseComponent.getInstance;
        bootstrap.BaseComponent.getInstance = function(element) {
            try {
                return originalBaseGetInstance(element);
            } catch (error) {
                console.warn('Error in BaseComponent.getInstance, returning null safely:', error.message);
                return null;
            }
        };
    }

    // 3. Add safety checks to Modal.hide and Modal.show
    const originalHide = bootstrap.Modal.prototype.hide;
    bootstrap.Modal.prototype.hide = function() {
        try {
            return originalHide.apply(this, arguments);
        } catch (error) {
            console.warn('Error in Modal.hide, handled gracefully:', error.message);

            // Fallback to manual hiding
            if (this && this._element) {
                this._element.classList.remove('show');
                this._element.style.display = 'none';

                const backdrop = document.querySelector('.modal-backdrop');
                if (backdrop) backdrop.parentNode.removeChild(backdrop);

                document.body.classList.remove('modal-open');
            }
        }
    };

    const originalShow = bootstrap.Modal.prototype.show;
    bootstrap.Modal.prototype.show = function() {
        try {
            return originalShow.apply(this, arguments);
        } catch (error) {
            console.warn('Error in Modal.show, handled gracefully:', error.message);

            // Fallback to manual showing if needed
            if (this && this._element) {
                this._element.classList.add('show');
                this._element.style.display = 'block';
                document.body.classList.add('modal-open');
            }
        }
    };

    // 4. Prevent errors when clicking dismiss buttons
    document.addEventListener('click', function(event) {
        const dismissButton = event.target.closest('[data-bs-dismiss="modal"]');
        if (!dismissButton) return;

        const modalElement = dismissButton.closest('.modal');
        if (!modalElement) return;

        // Get the instance safely
        const instance = bootstrap.Modal.getInstance(modalElement);

        // Only if we don't have an instance, prevent default and handle manually
        if (!instance) {
            event.preventDefault();
            event.stopPropagation();

            // Manual hide
            modalElement.classList.remove('show');
            modalElement.style.display = 'none';
            document.body.classList.remove('modal-open');

            // Remove backdrop
            const backdrop = document.querySelector('.modal-backdrop');
            if (backdrop) backdrop.parentNode.removeChild(backdrop);
        }
    }, true); // Use capture phase to get event before other handlers

    console.log('Global Bootstrap Modal fixes applied successfully');
})();