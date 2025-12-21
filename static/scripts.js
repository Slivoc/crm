// Function to apply the default margin to all margin inputs
function applyDefaultMargin() {
    var defaultMargin = parseFloat(document.getElementById('default_margin').value) || 0;
    var marginInputs = document.querySelectorAll('input[name^="margin_"]');
    marginInputs.forEach(function(input) {
        input.value = defaultMargin.toFixed(2);
        updateLineValue(input);
    });
}


// Function to update line value based on cost, margin, and quantity
function updateLineValue(element) {
    var lineId = element.name.split('_')[1];
    updatePrice(lineId);
}

// Function to update price and line value based on cost and margin
function updatePrice(lineId) {
    var costInput = document.querySelector('input[name="cost_' + lineId + '"]');
    var marginInput = document.querySelector('input[name="margin_' + lineId + '"]');
    var priceInput = document.querySelector('input[name="price_' + lineId + '"]');
    var quantityInput = document.querySelector('input[name="quantity_' + lineId + '"]');
    var lineValueInput = document.querySelector('input[name="line_value_' + lineId + '"]');

    if (costInput && marginInput && priceInput) {
        var cost = parseFloat(costInput.value) || 0;
        var margin = parseFloat(marginInput.value) || 0;
        var price = cost / (1 - (margin / 100));
        priceInput.value = price.toFixed(2);
    }

    if (priceInput && quantityInput && lineValueInput) {
        var price = parseFloat(priceInput.value) || 0;
        var quantity = parseFloat(quantityInput.value) || 0;
        var lineValue = price * quantity;
        lineValueInput.value = lineValue.toFixed(2);
    }
}
// Function to validate part number and apply formatting
function validatePartNumber(input) {
    var partNumber = input.value.trim().toUpperCase();  // Normalize input to uppercase and trim spaces

    if (!partNumber) {  // Avoid validating empty input
        input.style.backgroundColor = '';
        return;
    }

    fetch('/api/validate_part_number?part_number=' + encodeURIComponent(partNumber))
        .then(response => response.json())
        .then(data => {
            console.log(`Part number: ${partNumber}, Valid: ${data.valid}`);
            input.style.backgroundColor = data.valid ? '#d4edda' : '#f8d7da';  // Apply color based on validity
        })
        .catch(error => {
            console.error('Error validating part number:', error);
            input.style.backgroundColor = '#f8d7da';  // Assume invalid if there is an error
        });
}

document.addEventListener('DOMContentLoaded', function() {
    var partNumberInputs = document.querySelectorAll('input[name^="part_number_"], #part_number');
    partNumberInputs.forEach(input => {
        input.addEventListener('input', debounce(() => validatePartNumber(input), 500));
        validatePartNumber(input);  // Validate on page load
    });
});

// Debounce function to limit the rate at which validatePartNumber is called
function debounce(func, delay) {
    let timeout;
    return function() {
        const context = this, args = arguments;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), delay);
    };
}

document.addEventListener('DOMContentLoaded', function() {
    const partNumberInput = document.getElementById('part_number');
    const manufacturerSelect = document.getElementById('manufacturer_id'); // Ensure this is the correct ID for the add section

    if (partNumberInput && manufacturerSelect) {
        partNumberInput.addEventListener('blur', function() {
            const partNumber = this.value.trim().toUpperCase();

            fetch(`/api/get_manufacturers_by_part?part_number=${encodeURIComponent(partNumber)}`)
                .then(response => {
                    if (!response.ok) throw new Error('Network response was not ok');
                    return response.json();
                })
                .then(data => {
                    manufacturerSelect.innerHTML = '<option value="">Select Manufacturer</option>';

                    // Populate dropdown with all manufacturers
                    data.all_manufacturers.forEach(manufacturer => {
                        manufacturerSelect.innerHTML += `<option value="${manufacturer.id}">${manufacturer.name}</option>`;
                    });

                    // Set default if only one associated manufacturer is returned
                    if (data.associated_manufacturers.length === 1) {
                        manufacturerSelect.value = data.associated_manufacturers[0].id;
                    }
                })
                .catch(error => {
                    console.error('Error fetching manufacturers:', error);
                });
        });
    } else {
        console.log('Manufacturer Select or Part Number Input element not found:', { partNumberInput, manufacturerSelect });
    }
});

document.addEventListener('DOMContentLoaded', function() {
    const partNumberInput = document.getElementById('rfq_part_number');
    const manufacturerSelect = document.getElementById('rfq_manufacturer_id');

    console.log('Part Number Input:', partNumberInput);
    console.log('Manufacturer Select:', manufacturerSelect);

    if (partNumberInput && manufacturerSelect) {
        partNumberInput.addEventListener('blur', function() {
            const partNumber = this.value.trim().toUpperCase();
            console.log('Part Number:', partNumber);

            fetch(`/api/get_manufacturers_by_part?part_number=${encodeURIComponent(partNumber)}`)
                .then(response => {
                    console.log('Fetch response:', response);
                    if (!response.ok) throw new Error('Network response was not ok');
                    return response.json();
                })
                .then(data => {
                    console.log('Manufacturers data:', data);

                    manufacturerSelect.innerHTML = '<option value="">Select Manufacturer</option>';

                    if (data.all_manufacturers && data.all_manufacturers.length > 0) {
                        // Populate dropdown with all manufacturers
                        data.all_manufacturers.forEach(manufacturer => {
                            manufacturerSelect.innerHTML += `<option value="${manufacturer.id}">${manufacturer.name}</option>`;
                        });

                        // Set default if only one associated manufacturer is returned
                        if (data.associated_manufacturers.length === 1) {
                            manufacturerSelect.value = data.associated_manufacturers[0].id;
                        }
                    } else {
                        console.warn('No manufacturers available for the part number:', partNumber);
                    }
                })
                .catch(error => {
                    console.error('Error fetching manufacturers:', error);
                });
        });
    } else {
        console.log('Manufacturer Select or Part Number Input element not found:', { partNumberInput, manufacturerSelect });
    }
});


function openInNewWindow(url) {
    window.open(url, '_blank', 'toolbar=0,location=0,menubar=0,width=800,height=600');
}

function viewOfferFile(offerId) {
    const fileId = document.getElementById('file_id').value;
    const url = `/offers/view_pdf_text?offer_id=${offerId}&file_id=${fileId}`;
    window.open(url, '_blank', 'toolbar=0,location=0,menubar=0,width=800,height=600');
}

document.addEventListener('DOMContentLoaded', function () {
    const lineNumberInput = document.getElementById('rfq_line_number');
    // Set the line number input to the next line number
    if (lineNumberInput) {
        lineNumberInput.value = maxLineNumber + 1;
    } else {
        console.error('Could not find element with id "rfq_line_number"');
    }
});

// This handles all .dropdown-menu.show in the page (customize selector if needed)
document.addEventListener('shown.bs.dropdown', function(e) {
    var menu = e.target.querySelector('.dropdown-menu');
    var toggle = e.relatedTarget || e.target.querySelector('[data-bs-toggle="dropdown"]') || e.target;
    if (menu && toggle) {
        // Move the menu to body and position it
        document.body.appendChild(menu);
        var rect = toggle.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.top = (rect.bottom + 2) + 'px';
        menu.style.left = (rect.left) + 'px';
        menu.style.zIndex = 4000;
        menu.style.minWidth = '340px';
        menu.style.background = '#fff';
        menu.style.boxShadow = '0 12px 48px rgba(0,0,0,0.14)';
        menu.setAttribute('data-freed', '1'); // So we know we moved it
    }
});

document.addEventListener('hide.bs.dropdown', function(e) {
    var menu = document.querySelector('.dropdown-menu[data-freed="1"]');
    if (menu && e.target.contains(menu)) {
        // Move it back to the original parent for Bootstrap to hide properly
        e.target.appendChild(menu);
        menu.style.position = '';
        menu.style.top = '';
        menu.style.left = '';
        menu.style.zIndex = '';
        menu.style.minWidth = '';
        menu.style.background = '';
        menu.style.boxShadow = '';
        menu.removeAttribute('data-freed');
    }
});
