/**
 * src/config/config_manager.cpp
 * Business Logic Layer — implementation.
 *
 * Calls hal_* only. Never calls Serial.
 * millis() is used as a pure time source — acceptable at this layer.
 */

#include <Arduino.h>
#include "config_manager.h"
#include "hal.h"

// ─────────────────────────────────────────────────────────────
//  Private state
// ─────────────────────────────────────────────────────────────

static uint8_t       _raw_input         = 0x00;
static bool          _relay[CFG_SWITCH_COUNT] = {false}; // _relay[0] = relay 0 state
static power_state_t _pwr_state         = POWER_STATE_ON;
static uint32_t      _power_off_time_ms = 0;

// ─────────────────────────────────────────────────────────────
//  Bit transform
//
//  Rule: ignore MSB (bit 7). Bit j of input → _relay[j], for j = 0..6.
//  No reversal — the physical relay ordering on the PCB is handled by
//  the RELAY_PINS[] table in hal.cpp.
//
//  Input 0x07 = b00000111:
//    bit 0 = 1 → _relay[0] = ON
//    bit 1 = 1 → _relay[1] = ON
//    bit 2 = 1 → _relay[2] = ON
//    bit 3 = 0 → _relay[3] = OFF
//    bit 4 = 0 → _relay[4] = OFF
//    bit 5 = 0 → _relay[5] = OFF
//    bit 6 = 0 → _relay[6] = OFF
//    bit 7     → ignored
//  relay 0-6: ON ON ON OFF OFF OFF OFF  = 1110000 ✓
// ─────────────────────────────────────────────────────────────

static void _transform(uint8_t received) {
    for (uint8_t j = 0; j < CFG_SWITCH_COUNT; j++) {
        _relay[j] = (received >> j) & 1;  // bit j → relay j; bit 7 never reached
    }
}

static cfg_result_t _flush_relays(void) {
    for (uint8_t i = 0; i < CFG_SWITCH_COUNT; i++) {
        if (hal_relay_set(i, _relay[i]) != HAL_OK)
            return CFG_ERR_HAL_FAULT;
    }
    return CFG_OK;
}

static void _begin_power_off(void) {
    hal_relay_set(CFG_POWER_RELAY_IDX, false);
    _pwr_state         = POWER_STATE_CYCLING;
    _power_off_time_ms = millis();
}

// ─────────────────────────────────────────────────────────────
//  Public API
// ─────────────────────────────────────────────────────────────

cfg_result_t cfg_init(void) {
    _raw_input = 0x00;
    for (uint8_t i = 0; i < CFG_SWITCH_COUNT; i++) _relay[i] = false;
    cfg_result_t r = _flush_relays();
    if (r != CFG_OK) return r;
    if (hal_relay_set(CFG_POWER_RELAY_IDX, true) != HAL_OK) return CFG_ERR_HAL_FAULT;
    _pwr_state = POWER_STATE_ON;
    return CFG_OK;
}

void cfg_tick(config_event_t *event_out) {
    *event_out = EVT_NONE;
    if (_pwr_state == POWER_STATE_CYCLING) {
        if (millis() - _power_off_time_ms >= CFG_POWER_OFF_DELAY_MS) {
            hal_relay_set(CFG_POWER_RELAY_IDX, true);
            _pwr_state = POWER_STATE_ON;
            *event_out = EVT_POWER_ON;
        }
    }
}

cfg_result_t cfg_apply_hex(uint8_t value) {
    if (_pwr_state == POWER_STATE_CYCLING) return CFG_ERR_POWER_BUSY;

    _raw_input = value;    // remember what the user sent (for ACK echo)
    _transform(value);     // populates _relay[0..6] directly

    cfg_result_t r = _flush_relays();
    if (r != CFG_OK) return r;
    _begin_power_off();
    return CFG_OK;
}

cfg_result_t cfg_trigger_power_cycle(void) {
    if (_pwr_state == POWER_STATE_CYCLING) return CFG_ERR_POWER_BUSY;
    _begin_power_off();
    return CFG_OK;
}

cfg_result_t cfg_get_raw_input(uint8_t *out) {
    if (!out) return CFG_ERR_HAL_FAULT;
    *out = _raw_input;
    return CFG_OK;
}

cfg_result_t cfg_get_config_byte(uint8_t *out) {
    if (!out) return CFG_ERR_HAL_FAULT;
    // Pack _relay[] bool array back into a byte: bit j = _relay[j]
    uint8_t packed = 0;
    for (uint8_t j = 0; j < CFG_SWITCH_COUNT; j++) {
        if (_relay[j]) packed |= (1 << j);
    }
    *out = packed;
    return CFG_OK;
}

bool cfg_is_power_cycling(void) {
    return (_pwr_state == POWER_STATE_CYCLING);
}

uint32_t cfg_power_cycle_remaining_ms(void) {
    if (_pwr_state != POWER_STATE_CYCLING) return 0;
    uint32_t elapsed = millis() - _power_off_time_ms;
    return (elapsed >= CFG_POWER_OFF_DELAY_MS) ? 0 : CFG_POWER_OFF_DELAY_MS - elapsed;
}
