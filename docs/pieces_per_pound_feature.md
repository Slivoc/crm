# Pieces Per Pound (PPP) Feature

## Overview

This feature allows the system to automatically convert between pound-based pricing (from suppliers) and piece-based pricing (for internal use) using a "pieces per pound" conversion factor stored for each part.

## Use Case

Many suppliers, especially for small hardware items (screws, washers, rivets, etc.), quote prices in **pounds (lb)** rather than per piece. However, internally you need to work with **piece quantities and costs**. This feature automates the conversion so you don't have to calculate manually.

## Database Schema

A new column has been added to the `part_numbers` table:

```sql
ALTER TABLE part_numbers ADD COLUMN pieces_per_pound DECIMAL(10,3);
```

This stores the conversion factor (e.g., 1250 pieces per pound for a small screw).

## Importing PPP Data

Use the provided import script to load PPP values:

```bash
python import_pieces_per_pound.py your_data.csv
```

### Input File Format

The script accepts CSV or XLSX files with these columns:

```csv
part_number,pieces_per_pound
MS35338-44,1250
NAS6606D46,850
CR3212-4-04,425.5
```

**Important:** The script automatically:
- Normalizes part numbers (removes special characters, converts to uppercase)
- Matches against `base_part_number` in the database
- Updates existing parts or creates new ones if they don't exist

### Script Options

```bash
# Dry run (preview without saving)
python import_pieces_per_pound.py data.csv --dry-run

# Limit to first 100 rows for testing
python import_pieces_per_pound.py data.csv --limit 100

# Adjust batch size
python import_pieces_per_pound.py data.csv --batch-size 500
```

## How It Works

### On the Costing Page

When viewing supplier quotes for a part that has a `pieces_per_pound` value:

1. **Auto-conversion**: If PPP is set, the modal automatically converts supplier quotes from pounds to pieces
2. **Toggle button**: A small icon button appears next to the quantity that lets you toggle between:
   - **Piece mode** (default): Shows unit cost per piece and quantity in pieces
   - **Pound mode**: Shows unit cost per pound and quantity/weight in pounds

### Conversion Logic

**Pounds → Pieces:**
- `piece_cost = lb_cost / pieces_per_pound`
- `piece_qty = lb_qty * pieces_per_pound` (rounded to nearest whole number)

**Pieces → Pounds:**
- `lb_cost = piece_cost * pieces_per_pound`
- `lb_qty = piece_qty / pieces_per_pound`

### Example

Given:
- Supplier quotes: £50/lb for 2 lbs
- PPP = 1000 pieces/lb

Displayed in piece mode:
- Unit cost: £0.05/ea (£50 ÷ 1000)
- Quantity: 2000 ea (2 × 1000)
- Line total: £100

When you click the toggle to see pound mode:
- Unit cost: £50/lb
- Quantity: 2 lb
- Line total: £100

### Using a Quote

When you click the **"Use"** button:
- The system saves the **piece values** (cost per piece, quantity in pieces)
- This happens regardless of which mode you're viewing
- The conversion is transparent to the rest of the system

## UI Elements

### Toggle Icon
- 📦 **Grid icon** (`bi-grid-3x3`): Currently showing pieces
- 📦 **Box icon** (`bi-box-seam`): Currently showing pounds

### Unit Badge
- Small badge showing "ea" (each/pieces) or "lb" (pounds)
- Appears next to the unit price

## Technical Details

### Files Modified

1. **Database Migration**: `migrations/20260208_add_pieces_per_pound_to_parts.sql`
2. **Import Script**: `import_pieces_per_pound.py`
3. **Backend API**: `routes/parts_list.py` - Modified `/parts-lists/<id>/lines/<id>/quotes` endpoint
4. **Frontend JS**: `static/js/parts_list_costing.js` - Added conversion logic and toggle handlers
5. **Documentation**: This file

### API Response

The quotes endpoint now returns:

```json
{
  "success": true,
  "quotes": [...],
  "other_offers": [...],
  "qpl_approvals": [...],
  "pieces_per_pound": 1250.0
}
```

### JavaScript Functions

- `convertLbToPieces(lbCost, lbQty, ppp)` - Convert pound values to piece values
- `convertPiecesToLb(pieceCost, pieceQty, ppp)` - Convert piece values to pound values

## Troubleshooting

### PPP not showing for a part

Check if the part has the value set:

```sql
SELECT base_part_number, pieces_per_pound
FROM part_numbers
WHERE base_part_number = 'MS3533844';
```

### Toggle button not appearing

1. Verify PPP is set in the database
2. Check browser console for JavaScript errors
3. Ensure the page is loading the updated `parts_list_costing.js`

### Incorrect conversions

Verify the PPP value is accurate. Common values:
- Small screws: 1000-2000 pieces/lb
- Medium washers: 500-1000 pieces/lb
- Large rivets: 100-500 pieces/lb

## Future Enhancements

Possible improvements:
- Bulk edit PPP values from the UI
- Import PPP from manufacturer datasheets
- Historical tracking of PPP changes
- Support for other unit conversions (kg, oz, etc.)
