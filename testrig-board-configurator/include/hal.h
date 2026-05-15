/**
 * include/hal/hal.h
 * Hardware Abstraction Layer — relay output interface.
 *
 * Responsibilities:
 *   - Initialise all relay GPIO pins
 *   - Write relay states (set / clear)
 *   - Maintain a shadow register for readback
 *   - Zero knowledge of what any relay means — that is config logic
 *
 * Relay pin map (Arduino Micro / ATmega32U4):
 *   Relay 0 → D2    Relay 1 → D3    Relay 2 → D9    Relay 3 → D10
 *   Relay 4 → D11   Relay 5 → D12   Relay 6 → D13   Relay 7 → A1
 *
 * Output polarity: active HIGH
 *   Power relay  : energise=true  → pin HIGH → coil ON  → contact CLOSED
 *   Config optos : energise=true  → pin HIGH → opto LED ON → output asserted
 *   energise=false → pin LOW  → output de-asserted
 */

#pragma once
#include "types.h"

#define HAL_RELAY_COUNT 8

/**
 * hal_init()
 * Configure relay pins as outputs; all relays de-energised at startup.
 * Drives HIGH before setting OUTPUT to prevent boot glitch.
 */
void hal_init(void);

/**
 * hal_relay_set(index, energise)
 * @param index     Relay 0–7
 * @param energise  true = coil ON (contact closed)
 * @return HAL_OK or HAL_ERR_INVALID_IDX
 */
hal_result_t hal_relay_set(uint8_t index, bool energise);

/**
 * hal_relay_get(index, out)
 * Read last-commanded state from shadow register (not from hardware pin).
 * @return HAL_OK or HAL_ERR_INVALID_IDX
 */
hal_result_t hal_relay_get(uint8_t index, bool *out);
