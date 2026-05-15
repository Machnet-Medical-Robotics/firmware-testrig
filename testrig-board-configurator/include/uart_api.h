/**
 * include/uart/uart_api.h
 * UART / Serial API Layer
 *
 * Responsibilities:
 *   - Only layer permitted to call Serial.*
 *   - Parses newline-delimited ASCII commands from the host
 *   - Formats and sends ACK / NAK / EVT / STATUS responses
 *   - Translates cfg_result_t errors into NAK codes
 *
 * ═══════════════════════════════════════════════════════════
 *  PROTOCOL
 * ═══════════════════════════════════════════════════════════
 *
 *  Host → Device  (newline-terminated, case-insensitive)
 *    SET <HH>    Apply hex config 00–7F; triggers power cycle
 *    STATUS      Query current config and power state
 *    CYCLE       Power cycle without changing config
 *    HELP        List commands
 *
 *  Device → Host
 *    ACK <HH>                 Accepted; HH = active config byte
 *    NAK <CODE> <REASON>      Rejected
 *      Codes: 1=INVALID_HEX  2=POWER_BUSY  3=HAL_FAULT  9=PARSE_ERROR
 *    EVT POWER_ON <HH>        Async: board powered on after cycle
 *    STATUS CONFIG <HH>
 *    STATUS POWER ON | CYCLING <remaining_ms>
 *    STATUS END
 * ═══════════════════════════════════════════════════════════
 */

#pragma once
#include "types.h"

/** Open serial port and print startup banner. Call after cfg_init(). */
void uart_init(uint32_t baud);

/** Consume pending bytes and dispatch complete commands. Call every loop(). */
void uart_tick(void);

/** Format and send an async EVT message. Call when cfg_tick() returns non-EVT_NONE. */
void uart_report_event(config_event_t event);
