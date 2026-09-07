// Support for bit-banging commands to CS1237 24-bit ADC chip
//
// Copyright (C) 2026 Mika & Antigravity
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <stdbool.h>
#include <stdint.h>
#include "autoconf.h"
#include "basecmd.h" // oid_alloc
#include "board/gpio.h" // gpio_out_write, gpio_in_read
#include "board/irq.h" // irq_poll
#include "board/misc.h" // timer_read_time
#include "command.h" // DECL_COMMAND
#include "sched.h" // sched_add_timer
#include "sensor_bulk.h" // sensor_bulk_report
#include "trigger_analog.h" // trigger_analog_update

struct cs1237_adc {
    struct timer timer;
    uint8_t cfg_reg;        // config register byte (e.g. 0x3C: PGA=128, 1280Hz)
    uint8_t flags;
    uint32_t rest_ticks;
    uint32_t last_error;
    struct gpio_in dout;    // pin used to receive data from the cs1237 (PB14)
    struct gpio_out sclk;   // pin used to generate clock for the cs1237 (PB13)
    struct sensor_bulk sb;
    struct trigger_analog *ta;
};

enum {
    CS_PENDING = 1<<0, CS_OVERFLOW = 1<<1,
};

#define BYTES_PER_SAMPLE 4
#define SAMPLE_ERROR_DESYNC (1L << 31)
#define SAMPLE_ERROR_READ_TOO_LONG (1L << 30)

static struct task_wake wake_cs1237;

/****************************************************************
 * Low-level bit-banging
 ****************************************************************/

#define MIN_PULSE_TIME nsecs_to_ticks(250)

static uint32_t
nsecs_to_ticks(uint32_t ns)
{
    return timer_from_us(ns * 1000) / 1000000;
}

static void
cs1237_delay_noirq(void)
{
    if (CONFIG_MACH_AVR) {
        asm("nop\n    nop");
        return;
    }
    uint32_t end = timer_read_time() + MIN_PULSE_TIME;
    while (timer_is_before(timer_read_time(), end))
        ;
}

static void
cs1237_delay(void)
{
    if (CONFIG_MACH_AVR)
        return;
    uint32_t end = timer_read_time() + MIN_PULSE_TIME;
    while (timer_is_before(timer_read_time(), end))
        irq_poll();
}

// Check if data ready (DOUT low)
static inline uint8_t
cs1237_is_data_ready(struct cs1237_adc *cs)
{
    return !gpio_in_read(cs->dout);
}

// Read 24 bits + 3 status/clock pulses (total 27 clock pulses)
static int32_t
cs1237_raw_read(struct cs1237_adc *cs)
{
    uint32_t raw = 0;
    // Clock in 24 data bits, MSB first
    for (int i = 0; i < 24; i++) {
        irq_disable();
        gpio_out_toggle_noirq(cs->sclk);
        cs1237_delay_noirq();
        gpio_out_toggle_noirq(cs->sclk);
        uint_fast8_t bit = gpio_in_read(cs->dout);
        irq_enable();
        cs1237_delay();
        raw = (raw << 1) | bit;
    }

    // 3 additional pulses (bits 25, 26, 27)
    for (int i = 0; i < 3; i++) {
        irq_disable();
        gpio_out_toggle_noirq(cs->sclk);
        cs1237_delay_noirq();
        gpio_out_toggle_noirq(cs->sclk);
        irq_enable();
        cs1237_delay();
    }

    // Sign extend 24-bit 2's complement to 32-bit signed
    if (raw & 0x800000) {
        raw |= 0xFF000000;
    }
    return (int32_t)raw;
}

static void
add_sample(struct cs1237_adc *cs, uint8_t oid, int32_t sample,
           uint8_t force_flush)
{
    uint32_t counts = (uint32_t)sample;
    cs->sb.data[cs->sb.data_count] = counts;
    cs->sb.data[cs->sb.data_count + 1] = counts >> 8;
    cs->sb.data[cs->sb.data_count + 2] = counts >> 16;
    cs->sb.data[cs->sb.data_count + 3] = counts >> 24;
    cs->sb.data_count += BYTES_PER_SAMPLE;

    if (cs->sb.data_count + BYTES_PER_SAMPLE > ARRAY_SIZE(cs->sb.data)
        || force_flush)
        sensor_bulk_report(&cs->sb, oid);
}

static void
cs1237_read_adc(struct cs1237_adc *cs, uint8_t oid)
{
    irq_disable();
    if (!cs1237_is_data_ready(cs)) {
        cs->flags |= CS_OVERFLOW;
        irq_enable();
        return;
    }
    uint8_t flags = cs->flags;
    cs->flags = 0;
    irq_enable();

    uint32_t read_start = timer_read_time();
    int32_t sample = cs1237_raw_read(cs);
    uint32_t duration = timer_read_time() - read_start;

    if (duration > timer_from_us(2000)) {
        sample = SAMPLE_ERROR_READ_TOO_LONG;
    } else if (flags & CS_OVERFLOW) {
        sample = SAMPLE_ERROR_DESYNC;
    }

    if (sample > 0 || (sample != SAMPLE_ERROR_READ_TOO_LONG
                       && sample != SAMPLE_ERROR_DESYNC)) {
        trigger_analog_update(cs->ta, sample);
    }

    add_sample(cs, oid, sample, false);
}

static uint_fast8_t
cs1237_event(struct timer *timer)
{
    struct cs1237_adc *cs = container_of(timer, struct cs1237_adc, timer);
    if (cs1237_is_data_ready(cs)) {
        cs->flags |= CS_PENDING;
        sched_wake_task(&wake_cs1237);
    }
    cs->timer.waketime += cs->rest_ticks;
    return SF_RESCHEDULE;
}

/****************************************************************
 * Commands and Interface
 ****************************************************************/

void
command_config_cs1237(uint32_t *args)
{
    struct cs1237_adc *cs = oid_alloc(args[0], command_config_cs1237, sizeof(*cs));
    cs->timer.func = cs1237_event;
    cs->cfg_reg = args[1];
    cs->dout = gpio_in_setup(args[2], 1);
    cs->sclk = gpio_out_setup(args[3], 0);
    gpio_out_write(cs->sclk, 0);
}
DECL_COMMAND(command_config_cs1237, "config_cs1237 oid=%c cfg_reg=%c dout_pin=%u sclk_pin=%u");

void
cs1237_attach_trigger_analog(uint32_t *args)
{
    uint8_t oid = args[0];
    struct cs1237_adc *cs = oid_lookup(oid, command_config_cs1237);
    cs->ta = trigger_analog_oid_lookup(args[1]);
}
#if CONFIG_WANT_TRIGGER_ANALOG
DECL_COMMAND(cs1237_attach_trigger_analog, "cs1237_attach_trigger_analog oid=%c trigger_analog_oid=%c");
#endif

void
command_query_cs1237(uint32_t *args)
{
    struct cs1237_adc *cs = oid_lookup(args[0], command_config_cs1237);
    sched_del_timer(&cs->timer);
    cs->flags = 0;
    cs->last_error = 0;
    cs->rest_ticks = args[1];
    if (!cs->rest_ticks) {
        gpio_out_write(cs->sclk, 1); // power down
        return;
    }
    gpio_out_write(cs->sclk, 0); // wake up
    sensor_bulk_reset(&cs->sb);
    irq_disable();
    cs->timer.waketime = timer_read_time() + cs->rest_ticks;
    sched_add_timer(&cs->timer);
    irq_enable();
}
DECL_COMMAND(command_query_cs1237, "query_cs1237 oid=%c rest_ticks=%u");

void
command_query_cs1237_status(const uint32_t *args)
{
    struct cs1237_adc *cs = oid_lookup(args[0], command_config_cs1237);
    irq_disable();
    const uint32_t start_t = timer_read_time();
    uint8_t is_data_ready = cs1237_is_data_ready(cs);
    irq_enable();
    uint8_t pending_bytes = is_data_ready ? BYTES_PER_SAMPLE : 0;
    sensor_bulk_status(&cs->sb, args[0], start_t, 0, pending_bytes);
}
DECL_COMMAND(command_query_cs1237_status, "query_cs1237_status oid=%c");

void
cs1237_capture_task(void)
{
    if (!sched_check_wake(&wake_cs1237))
        return;
    uint8_t oid;
    struct cs1237_adc *cs;
    foreach_oid(oid, cs, command_config_cs1237) {
        if (cs->flags)
            cs1237_read_adc(cs, oid);
    }
}
DECL_TASK(cs1237_capture_task);
