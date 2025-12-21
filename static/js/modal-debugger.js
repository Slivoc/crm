// Modal Debugging Tool
// Add this to your main JavaScript file or include it as a separate script

// 1. First, let's detect all modal-related functions in the global scope
function detectModalFunctions() {
  const modalFunctions = {};
  const possibleFunctionNames = [
    'showModal', 'hideModal', 'openCustomerModal', 'openModal', 'closeModal',
    'TagModal', 'CustomerModal', 'Modal', 'showToast', 'displayModal'
  ];

  console.log('=== MODAL DEBUGGING: Checking for modal functions ===');

  possibleFunctionNames.forEach(funcName => {
    const exists = typeof window[funcName] !== 'undefined';
    modalFunctions[funcName] = exists;
    console.log(`${funcName}: ${exists ? 'EXISTS' : 'NOT FOUND'}`);

    // If it exists, log its source if possible
    if (exists && typeof window[funcName] === 'function') {
      try {
        console.log(`- Source: ${window[funcName].toString().substring(0, 100)}...`);
      } catch (e) {
        console.log('- Could not display source');
      }
    }

    // Check if it's an object with methods
    if (exists && typeof window[funcName] === 'object') {
      console.log(`- Methods: ${Object.keys(window[funcName]).join(', ')}`);
    }
  });

  return modalFunctions;
}

// 2. Track modal opening attempts
function setupModalTracking() {
  console.log('=== MODAL DEBUGGING: Setting up call tracking ===');

  // Intercept openCustomerModal
  if (typeof window.openCustomerModal === 'function') {
    const originalOpenCustomerModal = window.openCustomerModal;
    window.openCustomerModal = function(...args) {
      console.log('openCustomerModal called with:', args);
      try {
        return originalOpenCustomerModal.apply(this, args);
      } catch (error) {
        console.error('Error in openCustomerModal:', error);
        console.log('Call stack:', error.stack);

        // Try to identify what showModal might be
        if (error.message.includes('showModal is not defined')) {
          console.log('Investigating showModal references:');

          if (typeof window.CustomerModal !== 'undefined') {
            console.log('CustomerModal exists:', window.CustomerModal);
            console.log('Methods:', Object.keys(window.CustomerModal).join(', '));
          }

          // Check various objects that might contain showModal
          ['TagModal', 'CustomerModal', 'Modal', 'modalManager'].forEach(obj => {
            if (typeof window[obj] !== 'undefined' && typeof window[obj].show === 'function') {
              console.log(`${obj}.show exists and might be what's needed instead of showModal`);
            }
          });
        }

        // Re-throw the error to preserve original behavior
        throw error;
      }
    };
    console.log('Intercepted openCustomerModal function');
  } else {
    console.warn('Could not find openCustomerModal to intercept');
  }

  // Check for button click handlers that might open modals
  document.addEventListener('click', function(e) {
    if (e.target.tagName === 'BUTTON' || e.target.closest('button')) {
      const button = e.target.tagName === 'BUTTON' ? e.target : e.target.closest('button');
      const onClick = button.getAttribute('onclick');

      if (onClick && (onClick.includes('Modal') || onClick.includes('modal'))) {
        console.log('Modal-related button clicked:', button);
        console.log('onclick attribute:', onClick);
      }
    }
  }, true);
}

// 3. Check for modal HTML structure
function analyzeModalStructure() {
  console.log('=== MODAL DEBUGGING: Analyzing modal HTML structure ===');

  const modalElements = document.querySelectorAll('.modal, [role="dialog"], [aria-modal="true"]');
  console.log(`Found ${modalElements.length} modal-like elements in the DOM`);

  modalElements.forEach((modal, index) => {
    console.log(`Modal #${index + 1}:`);
    console.log(`- ID: ${modal.id || 'No ID'}`);
    console.log(`- Classes: ${modal.className}`);
    console.log(`- Visible: ${window.getComputedStyle(modal).display !== 'none'}`);

    // Check for possible event listeners
    const possibleTriggers = document.querySelectorAll(`[data-toggle="modal"][data-target="#${modal.id}"], [data-bs-toggle="modal"][data-bs-target="#${modal.id}"]`);
    console.log(`- Trigger elements: ${possibleTriggers.length}`);

    // Log child structure
    const childStructure = Array.from(modal.children).map(child =>
      `${child.tagName.toLowerCase()}${child.id ? '#' + child.id : ''}${Array.from(child.classList).map(c => '.' + c).join('')}`
    );
    console.log(`- Structure: ${childStructure.join(' > ')}`);
  });
}

// 4. Check script loading and potential conflicts
function checkScriptLoading() {
  console.log('=== MODAL DEBUGGING: Checking script loading ===');

  const scripts = document.querySelectorAll('script');
  const modalRelatedScripts = [];

  scripts.forEach(script => {
    const src = script.getAttribute('src') || '';
    if (src.includes('modal') || src.includes('bootstrap') || src.includes('jquery') || src.includes('customer')) {
      modalRelatedScripts.push({
        src: src,
        async: script.async,
        defer: script.defer
      });
    }
  });

  console.log(`Found ${modalRelatedScripts.length} potentially modal-related scripts:`);
  modalRelatedScripts.forEach(script => {
    console.log(`- ${script.src} (async: ${script.async}, defer: ${script.defer})`);
  });

  // Check for jQuery and Bootstrap presence
  console.log('jQuery version:', typeof $ !== 'undefined' ? $.fn.jquery : 'Not found');
  console.log('Bootstrap version:', typeof bootstrap !== 'undefined' ? bootstrap.Modal.VERSION : 'Not found');
}

// 5. Create a custom modal opener that tries different methods
function createSafeModalOpener() {
  console.log('=== MODAL DEBUGGING: Creating safe modal opener ===');

  window.safeOpenCustomerModal = function(data) {
    console.log('Attempting to safely open customer modal with:', data);

    // First, try with original method
    if (typeof openCustomerModal === 'function') {
      try {
        console.log('Trying original openCustomerModal...');
        openCustomerModal(data);
        return;
      } catch (e) {
        console.warn('Original openCustomerModal failed:', e.message);
      }
    }

    // Second, try CustomerModal.show if it exists
    if (typeof CustomerModal !== 'undefined' && typeof CustomerModal.show === 'function') {
      try {
        console.log('Trying CustomerModal.show...');
        CustomerModal.show(data);
        return;
      } catch (e) {
        console.warn('CustomerModal.show failed:', e.message);
      }
    }

    // Third, try finding a customer modal by ID and using Bootstrap Modal
    const possibleModalIds = ['customerModal', 'customer-modal', 'addCustomerModal'];
    for (const id of possibleModalIds) {
      const modalElement = document.getElementById(id);
      if (modalElement) {
        try {
          console.log(`Found modal with ID ${id}, trying to open with Bootstrap...`);

          // Store data in a global variable that the modal can access
          window.currentCustomerData = data;

          // Try Bootstrap 5 way
          if (typeof bootstrap !== 'undefined') {
            const bootstrapModal = new bootstrap.Modal(modalElement);
            bootstrapModal.show();
            return;
          }

          // Try jQuery way
          if (typeof $ !== 'undefined') {
            $(modalElement).modal('show');
            return;
          }

          console.warn('Found modal element but could not open it with Bootstrap or jQuery');
        } catch (e) {
          console.warn(`Attempt with modal #${id} failed:`, e.message);
        }
      }
    }

    // As a last resort, log the error and give detailed information for fixing
    console.error('All attempts to open customer modal failed. The modal system appears to be broken.');
    console.log('Potential fixes:');
    console.log('1. Check if showModal is defined in customer_modal.js');
    console.log('2. Check for naming conflicts between different modal systems');
    console.log('3. Inspect the browser console for errors when loading modal-related scripts');

    // Alert the user that there's an issue
    alert('Could not open the customer modal due to a technical issue. Please check the console for details.');
  };

  console.log('Created safeOpenCustomerModal function');

  // Replace onclick attributes that use openCustomerModal
  document.querySelectorAll('[onclick*="openCustomerModal"]').forEach(el => {
    console.log('Found element with openCustomerModal onclick:', el);

    const onclick = el.getAttribute('onclick');
    if (onclick) {
      // Replace with our safe version
      const newOnclick = onclick.replace('openCustomerModal', 'safeOpenCustomerModal');
      el.setAttribute('onclick', newOnclick);
      console.log('Updated onclick to use safeOpenCustomerModal');
    }
  });
}

// Run all diagnostic functions when the page is fully loaded
function runModalDiagnostics() {
  console.log('=== STARTING MODAL DIAGNOSTICS ===');

  // Run diagnostic functions
  detectModalFunctions();
  setupModalTracking();
  analyzeModalStructure();
  checkScriptLoading();
  createSafeModalOpener();

  console.log('=== MODAL DIAGNOSTICS COMPLETE ===');
  console.log('Use safeOpenCustomerModal() as a temporary workaround');
}

// Run when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', runModalDiagnostics);
} else {
  runModalDiagnostics();
}