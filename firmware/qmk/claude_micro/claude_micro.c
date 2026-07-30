// Claude Micro - board-level firmware.
//
// Three things live here that stock QMK does not provide:
//
//   1. agent status  - six frosted keys mirror the state of six host threads.
//                      The host pushes state over raw HID; the board renders it.
//   2. touch strip   - five pads read by RC charge time, turned into scroll
//                      ticks and taps.
//   3. joystick      - two ADC axes turned into four directional "skill" keys
//                      with a dead zone and auto-repeat.

#include QMK_KEYBOARD_H
#include "analog.h"
#include "raw_hid.h"
#include <string.h>

// ------------------------------------------------------------ agent status

typedef enum {
    AGENT_OFF = 0,
    AGENT_IDLE,
    AGENT_THINKING,
    AGENT_RUNNING,
    AGENT_WAITING,
    AGENT_DONE,
    AGENT_ERROR,
} agent_state_t;

static agent_state_t agent_state[AGENT_LED_COUNT] = {AGENT_IDLE};
static uint32_t      agent_last_update = 0;

// Matches config/device.json -> rgb.status_colors, so the physical board and
// the documentation cannot disagree.
static const uint8_t agent_rgb[][3] = {
    [AGENT_OFF]      = {  0,   0,   0},
    [AGENT_IDLE]     = {255, 255, 255},
    [AGENT_THINKING] = { 60, 120, 255},
    [AGENT_RUNNING]  = {120, 200, 255},
    [AGENT_WAITING]  = {255, 176,  32},
    [AGENT_DONE]     = { 40, 210, 120},
    [AGENT_ERROR]    = {235,  60,  60},
};

// Agent keys are LED indices 0..5 (chain order A1..A6).
static void render_agent_leds(uint8_t led_min, uint8_t led_max) {
    const uint32_t now  = timer_read32();
    const bool     stale = (now - agent_last_update) > AGENT_STATUS_TIMEOUT_MS;
    for (uint8_t i = 0; i < AGENT_LED_COUNT; i++) {
        if (i < led_min || i >= led_max) continue;
        agent_state_t s = stale ? AGENT_IDLE : agent_state[i];
        uint8_t r = agent_rgb[s][0], g = agent_rgb[s][1], b = agent_rgb[s][2];
        if (s == AGENT_THINKING || s == AGENT_RUNNING) {
            // Slow breathe so "working" reads differently from "settled".
            uint8_t phase = (uint8_t)((now / 4) + i * 24);
            uint16_t k    = 110 + (abs8(sin8(phase) - 128) >> 1);
            r = (uint8_t)((r * k) >> 8);
            g = (uint8_t)((g * k) >> 8);
            b = (uint8_t)((b * k) >> 8);
        }
        rgb_matrix_set_color(i, r, g, b);
    }
}

// Host protocol: [0]=0xA1, [1]=count, then one state byte per agent.
void raw_hid_receive(uint8_t *data, uint8_t length) {
    if (length < 2 || data[0] != 0xA1) return;
    uint8_t n = data[1] > AGENT_LED_COUNT ? AGENT_LED_COUNT : data[1];
    for (uint8_t i = 0; i < n && (uint8_t)(2 + i) < length; i++) {
        if (data[2 + i] <= AGENT_ERROR) agent_state[i] = (agent_state_t)data[2 + i];
    }
    agent_last_update = timer_read32();
}

// ------------------------------------------------------------ touch strip

static const pin_t touch_pins[TOUCH_PAD_COUNT] = TOUCH_SENSE_PINS;
static uint16_t    touch_baseline[TOUCH_PAD_COUNT];
static bool        touch_active[TOUCH_PAD_COUNT];
static int8_t      touch_first = -1;
static uint32_t    touch_started = 0;

// Charge the pad through the 1 MOhm resistor and count loops until it reads
// high. A finger adds capacitance, so the count rises.
static uint16_t touch_read(uint8_t i) {
    uint32_t total = 0;
    for (uint8_t s = 0; s < TOUCH_SAMPLES; s++) {
        gpio_set_pin_output(touch_pins[i]);
        gpio_write_pin_low(touch_pins[i]);
        wait_us(30);
        gpio_set_pin_input(touch_pins[i]);
        gpio_write_pin_high(TOUCH_DRIVE_PIN);
        uint16_t n = 0;
        while (!gpio_read_pin(touch_pins[i]) && n < 3000) n++;
        total += n;
        gpio_write_pin_low(TOUCH_DRIVE_PIN);
        wait_us(30);
    }
    return (uint16_t)(total / TOUCH_SAMPLES);
}

static void touch_init(void) {
    gpio_set_pin_output(TOUCH_DRIVE_PIN);
    gpio_write_pin_low(TOUCH_DRIVE_PIN);
    for (uint8_t i = 0; i < TOUCH_PAD_COUNT; i++) {
        gpio_set_pin_input(touch_pins[i]);
        touch_baseline[i] = touch_read(i);
    }
}

static void touch_task(void) {
    for (uint8_t i = 0; i < TOUCH_PAD_COUNT; i++) {
        uint16_t v    = touch_read(i);
        int32_t  diff = (int32_t)v - (int32_t)touch_baseline[i];
        bool     now  = diff > TOUCH_THRESHOLD;

        if (!now) {
            // Track slow drift (temperature, humidity) only while untouched.
            touch_baseline[i] += (int16_t)((diff) >> TOUCH_BASELINE_SHIFT);
        }
        if (now && !touch_active[i]) {
            if (touch_first < 0) {
                touch_first   = (int8_t)i;
                touch_started = timer_read32();
            } else if (timer_elapsed32(touch_started) < TOUCH_SWIPE_MS) {
                // Adjacent pad lit up in time: treat it as a swipe tick.
                if (i > (uint8_t)touch_first) tap_code(KC_WH_D);
                else if (i < (uint8_t)touch_first) tap_code(KC_WH_U);
                touch_first = (int8_t)i;
            }
        }
        if (!now && touch_active[i] && touch_first == (int8_t)i
            && timer_elapsed32(touch_started) < 180) {
            tap_code(KC_ESC);   // quick tap on a single pad = acknowledge
            touch_first = -1;
        }
        touch_active[i] = now;
    }
    if (touch_first >= 0 && timer_elapsed32(touch_started) > 900) touch_first = -1;
}

// -------------------------------------------------------------- joystick

enum joy_dir { JOY_NONE = 0, JOY_UP, JOY_DOWN, JOY_LEFT, JOY_RIGHT };

static uint16_t joy_centre_x = 2048, joy_centre_y = 2048;
static uint8_t  joy_last = JOY_NONE;
static uint32_t joy_next_repeat = 0;

static void joy_init(void) {
    // Calibrate the centre at power-up; the stick must be at rest.
    joy_centre_x = analogReadPin(JOY_X_PIN);
    joy_centre_y = analogReadPin(JOY_Y_PIN);
}

static uint8_t joy_direction(void) {
    int16_t dx = (int16_t)analogReadPin(JOY_X_PIN) - (int16_t)joy_centre_x;
    int16_t dy = (int16_t)analogReadPin(JOY_Y_PIN) - (int16_t)joy_centre_y;
    if (abs(dx) < JOY_DEADZONE && abs(dy) < JOY_DEADZONE) return JOY_NONE;
    if (abs(dx) > abs(dy)) {
        if (abs(dx) < JOY_TRIGGER) return JOY_NONE;
        return dx > 0 ? JOY_RIGHT : JOY_LEFT;
    }
    if (abs(dy) < JOY_TRIGGER) return JOY_NONE;
    return dy > 0 ? JOY_DOWN : JOY_UP;
}

// Each direction launches a Claude Code skill via a host-side shortcut.
static void joy_fire(uint8_t dir) {
    switch (dir) {
        case JOY_UP:    tap_code16(LCTL(LSFT(KC_R))); break;  // review PR
        case JOY_DOWN:  tap_code16(LCTL(LSFT(KC_D))); break;  // debug error
        case JOY_LEFT:  tap_code16(LCTL(LSFT(KC_F))); break;  // refactor
        case JOY_RIGHT: tap_code16(LCTL(LSFT(KC_T))); break;  // run tests
        default: break;
    }
}

static void joy_task(void) {
    uint8_t dir = joy_direction();
    if (dir != joy_last) {
        joy_last = dir;
        if (dir != JOY_NONE) {
            joy_fire(dir);
            joy_next_repeat = timer_read32() + JOY_REPEAT_MS * 3;
        }
    } else if (dir != JOY_NONE && timer_read32() > joy_next_repeat) {
        joy_fire(dir);
        joy_next_repeat = timer_read32() + JOY_REPEAT_MS;
    }
}

// The click itself is folded into the matrix scan in matrix.c.

// ------------------------------------------------------------------ hooks

void keyboard_post_init_kb(void) {
    touch_init();
    joy_init();
    agent_last_update = timer_read32();
    keyboard_post_init_user();
}

void housekeeping_task_kb(void) {
    static uint32_t last_touch = 0;
    if (timer_elapsed32(last_touch) > 12) {
        last_touch = timer_read32();
        touch_task();
    }
    joy_task();
    housekeeping_task_user();
}

bool rgb_matrix_indicators_advanced_kb(uint8_t led_min, uint8_t led_max) {
    render_agent_leds(led_min, led_max);
    return rgb_matrix_indicators_advanced_user(led_min, led_max);
}
