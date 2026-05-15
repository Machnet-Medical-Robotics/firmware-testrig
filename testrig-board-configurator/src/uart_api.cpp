/**
 * src/uart/uart_api.cpp
 * UART / Serial API Layer — implementation.
 *
 * ONLY file permitted to call Serial.* functions.
 */

#include <Arduino.h>
#include <string.h>
#include <stdlib.h>
#include "uart_api.h"
#include "config_manager.h"

// ─────────────────────────────────────────────────────────────
//  Receive buffer
// ─────────────────────────────────────────────────────────────

static char    _rx[32];
static uint8_t _rx_len   = 0;
static bool    _overflow = false;

// ─────────────────────────────────────────────────────────────
//  Response helpers
// ─────────────────────────────────────────────────────────────

static void _hex2(uint8_t v) {
    if (v < 0x10) Serial.print('0');
    Serial.print(v, HEX);
}

// Print 7 relay bits (bits 6..0) with a 'b' prefix, e.g. b1110000
// Bit 7 is never used in the transformed value so we skip it
static void _bits8(uint8_t v) {
    Serial.print('b');
    for (int8_t i = 6; i >= 0; i--) {
        Serial.print((v >> i) & 1);
    }
}

// Print hex and binary together, e.g. "0x05 b00000101"
static void _hexbits(uint8_t v) {
    Serial.print(F("0x")); _hex2(v);
    Serial.print(' ');
    _bits8(v);
}

static void _ack(uint8_t raw_input) {
    // Echo raw hex (what the user sent) + binary of the transformed relay output
    uint8_t cfg = 0; cfg_get_config_byte(&cfg);
    Serial.print(F("ACK 0x")); _hex2(raw_input);
    Serial.print(' '); _bits8(cfg);
    Serial.println();
}

static void _nak(uint8_t code, const __FlashStringHelper *reason) {
    Serial.print(F("NAK "));
    Serial.print(code);
    Serial.print(' ');
    Serial.println(reason);
}

static void _nak_result(cfg_result_t r) {
    switch (r) {
        case CFG_ERR_INVALID_HEX: _nak(1, F("INVALID_HEX value must be 00-FF"));        break;
        case CFG_ERR_POWER_BUSY:  _nak(2, F("POWER_BUSY cycle already in progress"));   break;
        case CFG_ERR_HAL_FAULT:   _nak(3, F("HAL_FAULT relay driver error"));           break;
        default:                  _nak(9, F("PARSE_ERROR unknown error"));               break;
    }
}

// ─────────────────────────────────────────────────────────────
//  Command handlers
// ─────────────────────────────────────────────────────────────

static void _cmd_set(const char *args) {
    while (*args == ' ') args++;
    if (*args == '\0') { _nak(9, F("PARSE_ERROR SET requires a hex argument")); return; }

    char *end;
    unsigned long val = strtoul(args, &end, 16);
    if (end == args || (*end != '\0' && *end != ' ')) {
        _nak(9, F("PARSE_ERROR invalid hex value")); return;
    }
    // Full byte range accepted; transform (bit-reversal + LSB drop) is
    // applied inside cfg_apply_hex via _transform()
    if (val > 0xFF) {
        _nak(1, F("INVALID_HEX value must be 00-FF")); return;
    }

    cfg_result_t r = cfg_apply_hex((uint8_t)val);
    if (r != CFG_OK) { _nak_result(r); return; }

    // Debug: show the transform so input and output bits can be compared
    uint8_t cfg = 0; cfg_get_config_byte(&cfg);
    Serial.print(F("DBG IN  0x")); _hex2((uint8_t)val); Serial.print(F(" b"));
    for (int8_t i = 6; i >= 0; i--) Serial.print(((uint8_t)val >> i) & 1);
    Serial.println();
    Serial.print(F("DBG OUT 0x")); _hex2(cfg); Serial.print(F(" b"));
    for (int8_t i = 6; i >= 0; i--) Serial.print((cfg >> i) & 1);
    Serial.println();

    _ack((uint8_t)val);
}

static void _cmd_status(void) {
    uint8_t raw = 0, cfg = 0;
    cfg_get_raw_input(&raw);
    cfg_get_config_byte(&cfg);
    Serial.print(F("STATUS INPUT  ")); _hexbits(raw); Serial.println();
    Serial.print(F("STATUS RELAYS ")); _hexbits(cfg); Serial.println();
    if (cfg_is_power_cycling()) {
        Serial.print(F("STATUS POWER CYCLING "));
        Serial.println(cfg_power_cycle_remaining_ms());
    } else {
        Serial.println(F("STATUS POWER ON"));
    }
    Serial.println(F("STATUS END"));
}

static void _cmd_cycle(void) {
    cfg_result_t r = cfg_trigger_power_cycle();
    if (r != CFG_OK) { _nak_result(r); return; }
    uint8_t raw = 0; cfg_get_raw_input(&raw);
    _ack(raw);   // echo last raw input (config unchanged)
}

static void _cmd_help(void) {
    Serial.println(F("Commands:"));
    Serial.println(F("  SET <HH>   Apply hex config 00-FF (triggers power cycle)"));
    Serial.println(F("             Bits 7..1 mapped to relays 0..6 (MSB first, LSB ignored)"));
    Serial.println(F("  STATUS     Show config byte and power state"));
    Serial.println(F("  CYCLE      Power cycle without changing config"));
    Serial.println(F("  HELP       Show this message"));
    Serial.println(F("Responses:"));
    Serial.println(F("  ACK <HH>              Accepted; HH = transformed config applied to relays"));
    Serial.println(F("  NAK <N> <reason>      Rejected; N = error code"));
    Serial.println(F("  EVT POWER_ON <HH>     Board on after cycle"));
}

// ─────────────────────────────────────────────────────────────
//  Line dispatcher
// ─────────────────────────────────────────────────────────────

static void _dispatch(char *line) {
    for (char *p = line; *p; p++) if (*p >= 'a' && *p <= 'z') *p -= 32;

    if      (strncmp(line, "SET", 3) == 0 && (line[3] == ' ' || line[3] == '\0')) _cmd_set(line + 3);
    else if (strcmp(line, "STATUS") == 0)  _cmd_status();
    else if (strcmp(line, "CYCLE")  == 0)  _cmd_cycle();
    else if (strcmp(line, "HELP")   == 0)  _cmd_help();
    else if (line[0] != '\0')              _nak(9, F("PARSE_ERROR unknown command"));
}

// ─────────────────────────────────────────────────────────────
//  Public API
// ─────────────────────────────────────────────────────────────

void uart_init(uint32_t baud) {
    Serial.begin(baud);
    uint32_t t = millis();
    while (!Serial && (millis() - t) < 3000UL) {}

    Serial.println(F("== Robot Board Config Controller =="));
    Serial.println(F("   Arduino Micro / ATmega32U4"));
    Serial.println(F("   Boot config: 0x00"));
    Serial.println(F("   Type HELP for commands."));
}

void uart_tick(void) {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (_overflow)        { _nak(9, F("PARSE_ERROR line too long")); _overflow = false; }
            else if (_rx_len > 0) { _rx[_rx_len] = '\0'; _dispatch(_rx); }
            _rx_len = 0;
        } else {
            if (_rx_len < (uint8_t)(sizeof(_rx) - 1)) { _rx[_rx_len++] = c; }
            else                                       { _overflow = true; _rx_len = 0; }
        }
    }
}

void uart_report_event(config_event_t event) {
    if (event == EVT_POWER_ON) {
        uint8_t raw = 0, cfg = 0;
        cfg_get_raw_input(&raw);
        cfg_get_config_byte(&cfg);
        Serial.print(F("EVT POWER_ON 0x")); _hex2(raw);
        Serial.print(' '); _bits8(cfg);
        Serial.println();
    }
}