// Claude Micro - default keymap.
//
// Layer names and purposes mirror config/device.json -> layers, and the test
// suite asserts they still match.
//
// Key order in LAYOUT(): A1..A6, K1..K4, K5..K7, ENC_SW, JOY_SW.

#include QMK_KEYBOARD_H

enum layers {
    L_BASE = 0,   // Agent focus + core Claude Code commands
    L_REVIEW = 1,   // Diff / hunk navigation, accept, reject
    L_GIT = 2,   // Stage, commit, push, PR
    L_TERMINAL = 3,   // Pane + process control
    L_MEDIA = 4,   // System volume + transport
    L_CONFIG = 5,   // RGB, brightness, reset, bootloader
};

// Each custom keycode sends a raw-HID event that the host bridge turns into a
// Claude Code action. Without the bridge running the board still enumerates as
// a normal keyboard; these keys simply do nothing.
enum custom_keycodes {
    AG_1 = QK_USER, AG_2, AG_3, AG_4, AG_5, AG_6,
    AG_NEW, AG_RUN, AG_STOP, AG_DIFF, AG_PLAN, AG_TEST,
    AG_EFFT, AG_EFFU, AG_EFFD, AG_MENU,
};

const uint16_t PROGMEM keymaps[][MATRIX_ROWS][MATRIX_COLS] = {
    /* 0  BASE - Agent focus + core Claude Code commands */
    [0] = LAYOUT(
        AG_1, AG_2, AG_3, AG_4, AG_5, AG_6,
        AG_NEW, AG_RUN, AG_STOP, AG_DIFF,
        AG_PLAN, AG_TEST, MO(1),
        AG_EFFT, AG_MENU
    ),
    /* 1  REVIEW - Diff / hunk navigation, accept, reject */
    [1] = LAYOUT(
        KC_1, KC_2, KC_3, KC_4, KC_5, KC_6,
        C(S(KC_N)), C(S(KC_P)), KC_ESC, C(S(KC_D)),
        C(S(KC_A)), C(S(KC_X)), MO(2),
        KC_ENT, KC_SPC
    ),
    /* 2  GIT - Stage, commit, push, PR */
    [2] = LAYOUT(
        KC_1, KC_2, KC_3, KC_4, KC_5, KC_6,
        C(S(KC_G)), C(S(KC_C)), C(S(KC_U)), C(S(KC_B)),
        C(S(KC_L)), C(S(KC_M)), MO(3),
        KC_ENT, KC_SPC
    ),
    /* 3  TERMINAL - Pane + process control */
    [3] = LAYOUT(
        KC_1, KC_2, KC_3, KC_4, KC_5, KC_6,
        C(S(KC_T)), C(S(KC_W)), C(KC_C), C(KC_L),
        C(KC_R), C(KC_Z), MO(4),
        KC_ENT, KC_SPC
    ),
    /* 4  MEDIA - System volume + transport */
    [4] = LAYOUT(
        KC_MPLY, KC_MPRV, KC_MNXT, KC_MUTE, KC_VOLD, KC_VOLU,
        KC_BRID, KC_BRIU, KC_MSTP, KC_F13,
        KC_F14, KC_F15, MO(5),
        KC_MUTE, KC_MPLY
    ),
    /* 5  CONFIG - RGB, brightness, reset, bootloader */
    [5] = LAYOUT(
        RM_TOGG, RM_NEXT, RM_HUEU, RM_SATU, RM_VALD, RM_VALU,
        RM_SPDD, RM_SPDU, QK_BOOT, QK_RBT,
        EE_CLR, DB_TOGG, KC_TRNS,
        RM_VALU, RM_TOGG
    ),
};

#ifdef ENCODER_MAP_ENABLE
const uint16_t PROGMEM encoder_map[][NUM_ENCODERS][2] = {
    [0] = { ENCODER_CCW_CW(AG_EFFD, AG_EFFU) },
    [1] = { ENCODER_CCW_CW(C(S(KC_P)), C(S(KC_N))) },
    [2] = { ENCODER_CCW_CW(KC_PGUP, KC_PGDN) },
    [3] = { ENCODER_CCW_CW(C(S(KC_LBRC)), C(S(KC_RBRC))) },
    [4] = { ENCODER_CCW_CW(KC_VOLD, KC_VOLU) },
    [5] = { ENCODER_CCW_CW(RM_VALD, RM_VALU) },
};
#endif

// The dial walks reasoning effort: low -> medium -> high -> max.
static uint8_t effort = 1;
static const char *effort_name[] = {"low", "medium", "high", "max"};

static void send_agent_event(uint8_t kind, uint8_t arg) {
    uint8_t report[32] = {0};
    report[0] = 0xA2;      // host bridge: keypad event
    report[1] = kind;
    report[2] = arg;
    raw_hid_send(report, sizeof(report));
}

bool process_record_user(uint16_t keycode, keyrecord_t *record) {
    if (!record->event.pressed) return true;
    switch (keycode) {
        case AG_1 ... AG_6:
            send_agent_event(0x01, keycode - AG_1);   // focus thread n
            return false;
        case AG_NEW:  send_agent_event(0x02, 0); return false;
        case AG_RUN:  send_agent_event(0x03, 0); return false;
        case AG_STOP: send_agent_event(0x04, 0); return false;
        case AG_DIFF: send_agent_event(0x05, 0); return false;
        case AG_PLAN: send_agent_event(0x06, 0); return false;
        case AG_TEST: send_agent_event(0x07, 0); return false;
        case AG_MENU: send_agent_event(0x08, 0); return false;
        case AG_EFFU:
            if (effort < 3) effort++;
            send_agent_event(0x10, effort);
            return false;
        case AG_EFFD:
            if (effort > 0) effort--;
            send_agent_event(0x10, effort);
            return false;
        case AG_EFFT:
            effort = (effort + 1) & 3;
            send_agent_event(0x10, effort);
            return false;
    }
    return true;
}

uint8_t     claude_micro_effort(void)      { return effort; }
const char *claude_micro_effort_name(void) { return effort_name[effort]; }
