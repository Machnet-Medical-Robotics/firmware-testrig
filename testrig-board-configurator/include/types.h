/**
 * include/types.h
 * Shared enumerations and result types used across all firmware layers.
 *
 * Rules:
 *   - No Arduino includes here (keeps types portable / unit-testable)
 *   - No layer includes another layer's header directly;
 *     all cross-layer types live in this file only
 */

#pragma once
#include <stdint.h>
#include <stdbool.h>

// ─────────────────────────────────────────────────────────────
//  HAL result codes
// ─────────────────────────────────────────────────────────────
typedef enum {
    HAL_OK              = 0,
    HAL_ERR_INVALID_IDX = 1,   // Relay index out of range
} hal_result_t;

// ─────────────────────────────────────────────────────────────
//  Business logic result codes
// ─────────────────────────────────────────────────────────────
typedef enum {
    CFG_OK              = 0,
    CFG_ERR_INVALID_HEX = 1,   // Value outside 7-bit range (> 0x7F)
    CFG_ERR_POWER_BUSY  = 2,   // Power cycle already in progress
    CFG_ERR_HAL_FAULT   = 3,   // HAL returned an error
} cfg_result_t;

// ─────────────────────────────────────────────────────────────
//  Power cycle state machine
// ─────────────────────────────────────────────────────────────
typedef enum {
    POWER_STATE_ON      = 0,   // Board is powered and running
    POWER_STATE_CYCLING = 1,   // In the OFF phase of a power cycle
} power_state_t;

// ─────────────────────────────────────────────────────────────
//  Async events: emitted by config layer, consumed by UART layer
// ─────────────────────────────────────────────────────────────
typedef enum {
    EVT_NONE     = 0,
    EVT_POWER_ON = 1,   // Board came back on after cycle completed
} config_event_t;
