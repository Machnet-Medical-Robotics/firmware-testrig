/**
 * src/hal/hal.cpp
 * Hardware Abstraction Layer — implementation.
 *
 * ONLY file permitted to call digitalWrite / pinMode.
 * All other layers interact with hardware exclusively through hal_*.
 */

#include <Arduino.h>
#include "hal.h"

// ─────────────────────────────────────────────────────────────
//  Pin table and shadow register
// ─────────────────────────────────────────────────────────────

static const uint8_t RELAY_PINS[HAL_RELAY_COUNT] = {
    5,   // Relay 0 — config bit 0
    4,   // Relay 1 — config bit 1
    3,   // Relay 2 — config bit 2
    2,    // Relay 3 — config bit 3
    6,   // Relay 4 — config bit 4
    11,  // Relay 5 — config bit 5
    12,  // Relay 6 — config bit 6
    7    // Relay 7 — board power
};

static bool _shadow[HAL_RELAY_COUNT] = {false};

// ─────────────────────────────────────────────────────────────
//  Internal
// ─────────────────────────────────────────────────────────────

static inline void _write(uint8_t pin, bool energise) {
    // Active-HIGH outputs (relay coil driver + optocoupler inputs):
    //   energise = true  → pin HIGH → relay/opto ON
    //   energise = false → pin LOW  → relay/opto OFF
    digitalWrite(pin, energise ? HIGH : LOW);
}

// ─────────────────────────────────────────────────────────────
//  hal_init
// ─────────────────────────────────────────────────────────────

void hal_init(void) {
    for (uint8_t i = 0; i < HAL_RELAY_COUNT; i++) {
        // Drive LOW (inactive) BEFORE setting OUTPUT to prevent
        // a boot glitch that would briefly energise the relay/opto.
        digitalWrite(RELAY_PINS[i], LOW);
        pinMode(RELAY_PINS[i], OUTPUT);
        _shadow[i] = false;
    }
}

// ─────────────────────────────────────────────────────────────
//  hal_relay_set
// ─────────────────────────────────────────────────────────────

hal_result_t hal_relay_set(uint8_t index, bool energise) {
    if (index >= HAL_RELAY_COUNT) return HAL_ERR_INVALID_IDX;
    _write(RELAY_PINS[index], energise);
    _shadow[index] = energise;
    return HAL_OK;
}

// ─────────────────────────────────────────────────────────────
//  hal_relay_get
// ─────────────────────────────────────────────────────────────

hal_result_t hal_relay_get(uint8_t index, bool *out) {
    if (index >= HAL_RELAY_COUNT || out == nullptr) return HAL_ERR_INVALID_IDX;
    *out = _shadow[index];
    return HAL_OK;
}
