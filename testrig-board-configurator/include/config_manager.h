/**
 * include/config/config_manager.h
 * Business Logic Layer
 *
 * Responsibilities:
 *   - Maps config byte bits 0–6 to relay outputs 0–6
 *   - Owns the power relay (relay 7) and its cycle state machine
 *   - Validates incoming config values (range, busy-guard)
 *   - Tracks currently applied config
 *   - Emits config_event_t for asynchronous UART reporting
 *
 * This layer calls hal_* only. Never touches Serial.
 */

#pragma once
#include "types.h"

// ─────────────────────────────────────────────────────────────
//  Tunables
// ─────────────────────────────────────────────────────────────

#define CFG_SWITCH_COUNT        7          // Number of config relay outputs
#define CFG_POWER_RELAY_IDX     7          // Relay index for board power
#define CFG_POWER_OFF_DELAY_MS  5000UL     // Board off-time during power cycle

// ─────────────────────────────────────────────────────────────
//  API
// ─────────────────────────────────────────────────────────────

/**
 * cfg_init()
 * Set all config relays to 0x00, power board ON.
 * Call after hal_init().
 */
cfg_result_t cfg_init(void);

/**
 * cfg_tick(event_out)
 * Advance the power cycle state machine.
 * Call every loop() iteration.
 */
void cfg_tick(config_event_t *event_out);

/**
 * cfg_apply_hex(value)
 * Apply a 7-bit config (0x00–0x7F): sets relays 0–6, triggers power cycle.
 */
cfg_result_t cfg_apply_hex(uint8_t value);

/**
 * cfg_trigger_power_cycle()
 * Power cycle the board without changing config.
 */
cfg_result_t cfg_trigger_power_cycle(void);

/**
 * cfg_get_raw_input(out)
 * Return the last raw value received from UART, before the bit transform.
 * Used by the UART layer to echo back exactly what the user sent in ACK.
 */
cfg_result_t cfg_get_raw_input(uint8_t *out);

/**
 * cfg_get_config_byte(out)
 * Return the transformed value currently applied to relay outputs.
 * Used by STATUS to show what is physically held on the relays.
 */
cfg_result_t cfg_get_config_byte(uint8_t *out);

/** Returns true while a power cycle is in progress. */
bool     cfg_is_power_cycling(void);

/** Milliseconds remaining in the OFF phase; 0 if not cycling. */
uint32_t cfg_power_cycle_remaining_ms(void);
