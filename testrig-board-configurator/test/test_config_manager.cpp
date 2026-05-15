/**
 * test/test_config_manager.cpp
 * Unit tests for config_manager business logic.
 *
 * Run with: pio test -e native
 *
 * The native env uses a HAL stub (hal stub is ifdef-guarded in hal.cpp
 * with NATIVE_BUILD) so no Arduino hardware is needed.
 *
 * Framework: Unity (bundled with PlatformIO)
 */

#include <unity.h>
#include "config_manager.h"

void setUp(void)    { cfg_init(); }
void tearDown(void) {}

// ─────────────────────────────────────────────────────────────

void test_init_config_is_zero(void) {
    uint8_t cfg = 0xFF;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x00, cfg);
}

void test_init_raw_input_is_zero(void) {
    uint8_t raw = 0xFF;
    cfg_get_raw_input(&raw);
    TEST_ASSERT_EQUAL_HEX8(0x00, raw);
}

// ─── Transform: bit j of input → relay[j], bit 7 ignored ─────
// cfg_get_config_byte packs _relay[] back as bit j = relay[j],
// so packed output == (input & 0x7F) for all inputs.

// 0x07 = b0000111 → relay[0,1,2]=ON → packed=0x07
void test_transform_0x07(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x07));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x07, cfg);
}

// 0x05 = b0000101 → relay[0,2]=ON → packed=0x05
void test_transform_0x05(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x05));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x05, cfg);
}

// 0x01 → relay[0] only → packed=0x01
void test_transform_bit0_maps_to_relay0(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x01));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x01, cfg);
}

// 0x40 = b1000000 → relay[6] only → packed=0x40
void test_transform_bit6_maps_to_relay6(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x40));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x40, cfg);
}

// 0x7F = b1111111 → all relays ON → packed=0x7F
void test_transform_all_bits_set(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x7F));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x7F, cfg);
}

// 0xFF: bit7 ignored → same relay state as 0x7F
void test_transform_msb_ignored(void) {
    cfg_apply_hex(0xFF);
    uint8_t cfg_ff = 0;
    cfg_get_config_byte(&cfg_ff);
    cfg_init();
    cfg_apply_hex(0x7F);
    uint8_t cfg_7f = 0;
    cfg_get_config_byte(&cfg_7f);
    TEST_ASSERT_EQUAL_HEX8(cfg_7f, cfg_ff);
}

// 0x80: only bit7 → ignored → all relays OFF
void test_transform_msb_only_gives_zero(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x80));
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x00, cfg);
}

void test_apply_zero_all_relays_off(void) {
    TEST_ASSERT_EQUAL(CFG_OK, cfg_apply_hex(0x00));
    uint8_t cfg = 0xFF;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x00, cfg);
}

// Raw input echoes exactly what was sent
void test_raw_input_echoes_user_value(void) {
    cfg_apply_hex(0x07);
    uint8_t raw = 0;
    cfg_get_raw_input(&raw);
    TEST_ASSERT_EQUAL_HEX8(0x07, raw);
    uint8_t cfg = 0;
    cfg_get_config_byte(&cfg);
    TEST_ASSERT_EQUAL_HEX8(0x07, cfg);  // packed relay state equals input (bit7 was 0)
}

void test_power_busy_while_cycling(void) {
    cfg_apply_hex(0x07);
    TEST_ASSERT_TRUE(cfg_is_power_cycling());
    TEST_ASSERT_EQUAL(CFG_ERR_POWER_BUSY, cfg_apply_hex(0x0A));
    TEST_ASSERT_EQUAL(CFG_ERR_POWER_BUSY, cfg_trigger_power_cycle());
}

// ─────────────────────────────────────────────────────────────

int main(void) {
    UNITY_BEGIN();
    RUN_TEST(test_init_config_is_zero);
    RUN_TEST(test_init_raw_input_is_zero);
    RUN_TEST(test_transform_0x07);
    RUN_TEST(test_transform_0x05);
    RUN_TEST(test_transform_bit0_maps_to_relay0);
    RUN_TEST(test_transform_bit6_maps_to_relay6);
    RUN_TEST(test_transform_all_bits_set);
    RUN_TEST(test_transform_msb_ignored);
    RUN_TEST(test_transform_msb_only_gives_zero);
    RUN_TEST(test_apply_zero_all_relays_off);
    RUN_TEST(test_raw_input_echoes_user_value);
    RUN_TEST(test_power_busy_while_cycling);
    return UNITY_END();
}
