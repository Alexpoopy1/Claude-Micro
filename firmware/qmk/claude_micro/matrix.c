// Claude Micro - custom matrix (CUSTOM_MATRIX = lite).
//
// A plain 4x4 COL2ROW scan, plus one extra cell. The thumbstick's click has
// one terminal committed to ground inside the module, so it cannot sit in the
// matrix like the other switches; it gets its own GPIO and is folded into the
// unused [3][3] intersection here. That keeps it an ordinary key everywhere
// else -- keymap, layers, VIA -- instead of a special case in the keymap.

#include "quantum.h"
#include "matrix.h"

static const pin_t row_pins[MATRIX_ROWS] = MATRIX_ROW_PINS;
static const pin_t col_pins[MATRIX_COLS] = MATRIX_COL_PINS;

#define JOY_CELL_ROW 3
#define JOY_CELL_COL 3

void matrix_init_custom(void) {
    for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
        gpio_set_pin_input_high(row_pins[r]);
    }
    for (uint8_t c = 0; c < MATRIX_COLS; c++) {
        gpio_set_pin_input_high(col_pins[c]);
    }
    gpio_set_pin_input_high(JOY_SW_PIN);
}

bool matrix_scan_custom(matrix_row_t current_matrix[]) {
    bool changed = false;

    for (uint8_t c = 0; c < MATRIX_COLS; c++) {
        // Drive one column low, read which rows follow it down.
        gpio_set_pin_output(col_pins[c]);
        gpio_write_pin_low(col_pins[c]);
        matrix_io_delay();

        for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
            matrix_row_t last = current_matrix[r];
            if (!gpio_read_pin(row_pins[r])) current_matrix[r] |= (MATRIX_ROW_SHIFTER << c);
            else                             current_matrix[r] &= ~(MATRIX_ROW_SHIFTER << c);
            if (last != current_matrix[r]) changed = true;
        }

        gpio_set_pin_input_high(col_pins[c]);
    }

    // The thumbstick click, folded into the spare intersection.
    matrix_row_t last = current_matrix[JOY_CELL_ROW];
    if (!gpio_read_pin(JOY_SW_PIN)) current_matrix[JOY_CELL_ROW] |= (MATRIX_ROW_SHIFTER << JOY_CELL_COL);
    else                            current_matrix[JOY_CELL_ROW] &= ~(MATRIX_ROW_SHIFTER << JOY_CELL_COL);
    if (last != current_matrix[JOY_CELL_ROW]) changed = true;

    return changed;
}
