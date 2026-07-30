// Claude Micro - QMK configuration
// Drop this folder into qmk_firmware/keyboards/ and build with
//     qmk compile -kb claude_micro -km default

#pragma once

// ---------------------------------------------------------------- matrix
#define DEBOUNCE 5

// ------------------------------------------------------ joystick (ADC)
// Two 10k pots into ADC0/ADC1, each behind a 1k / 10nF filter.
#define JOY_X_PIN GP26
#define JOY_Y_PIN GP27
#define JOY_SW_PIN GP22
#define JOY_DEADZONE 380        // counts either side of centre, out of 2048
#define JOY_TRIGGER 900         // counts before a direction fires
#define JOY_REPEAT_MS 220

// ------------------------------------------------- capacitive touch strip
// GP21 drives all five pads through 1 MOhm; GP16..GP20 time the rising edge.
// No touch controller: the RC constant changes when a finger loads the pad.
#define TOUCH_PAD_COUNT 5
#define TOUCH_DRIVE_PIN GP21
#define TOUCH_SENSE_PINS { GP16, GP17, GP18, GP19, GP20 }
#define TOUCH_SAMPLES 8         // averaged per pad, per scan
#define TOUCH_THRESHOLD 22      // counts above the rolling baseline
#define TOUCH_BASELINE_SHIFT 9  // baseline tracks at 1/512 per scan
#define TOUCH_SWIPE_MS 320      // max time across the strip to count as a swipe

// ------------------------------------------------------------------- RGB
// 19 SK6812s: 13 per-key then 6 underglow. Full white on every channel would
// be ~1.1 A, so the effective brightness is capped to keep the whole board
// inside a 500 mA USB budget with headroom for the 1.1 A PPTC.
#define RGB_MATRIX_LED_COUNT 19
#define RGB_MATRIX_MAXIMUM_BRIGHTNESS 160
#define RGB_MATRIX_DEFAULT_MODE RGB_MATRIX_SOLID_COLOR
#define RGB_MATRIX_KEYPRESSES
#define WS2812_DI_PIN GP12
#define AGENT_LED_COUNT 6
#define AGENT_STATUS_TIMEOUT_MS 30000   // fall back to idle if the host goes quiet

// ------------------------------------------------------------------ misc
#define TAP_CODE_DELAY 8
#define ENCODER_RESOLUTION 4
#define DYNAMIC_KEYMAP_LAYER_COUNT 6
#define RAW_USAGE_PAGE 0xFF60
#define RAW_USAGE_ID 0x61
