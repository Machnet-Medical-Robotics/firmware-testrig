/**
 * src/main.cpp
 * Thin orchestrator — no GPIO, no Serial, no business logic.
 *
 * Startup order:
 *   1. hal_init()   — pins safe before anything else
 *   2. cfg_init()   — relays to 0x00, board powered ON
 *   3. uart_init()  — banner printed with correct initial state
 */

#include <Arduino.h>
#include "hal.h"
#include "config_manager.h"
#include "uart_api.h"
#include "types.h"

// ─────────────────────────────────────────────────────────────

void setup() {
    hal_init();

    if (cfg_init() != CFG_OK) {
        // Unrecoverable hardware fault — blink LED and halt
        pinMode(LED_BUILTIN, OUTPUT);
        while (true) {
            digitalWrite(LED_BUILTIN, HIGH); delay(100);
            digitalWrite(LED_BUILTIN, LOW);  delay(100);
        }
    }

    uart_init(9600);
}

void loop() {
    config_event_t event = EVT_NONE;
    cfg_tick(&event);

    if (event != EVT_NONE) {
        uart_report_event(event);
    }

    uart_tick();
}
