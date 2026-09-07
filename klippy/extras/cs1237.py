# CS1237 24-Bit ADC Support for Klipper Load Cell Probing
#
# Copyright (C) 2026 Mika & Antigravity
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import bulk_sensor

UPDATE_INTERVAL = 0.10
SAMPLE_ERROR_DESYNC = -0x80000000
SAMPLE_ERROR_LONG_READ = 0x40000000

# Supported CS1237 Sample Rates (ODR) and Gains
CS1237_SAMPLE_RATES = {
    10: 10,
    40: 40,
    640: 640,
    1280: 1280
}

CS1237_GAINS = {
    1: 0x00,
    2: 0x01,
    64: 0x02,
    128: 0x03
}

class CS1237:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.last_error_count = 0
        self.consecutive_fails = 0
        self.sensor_type = "cs1237"

        # Chip pin connections
        dout_pin_name = config.get('dout_pin')
        sclk_pin_name = config.get('sclk_pin')
        ppins = printer.lookup_object('pins')
        dout_ppin = ppins.lookup_pin(dout_pin_name)
        sclk_ppin = ppins.lookup_pin(sclk_pin_name)
        self.mcu = mcu = dout_ppin['chip']
        self.oid = mcu.create_oid()
        if sclk_ppin['chip'] is not mcu:
            raise config.error("%s config error: All pins must be "
                               "connected to the same MCU" % (self.name,))
        self.dout_pin = dout_ppin['pin']
        self.sclk_pin = sclk_ppin['pin']

        # Sample rate (default: 1280 Hz on K2 Pro)
        self.sps = config.getchoice('sample_rate', CS1237_SAMPLE_RATES, default=1280)

        # Gain (default: 128 on K2 Pro)
        self.gain = config.getchoice('gain', CS1237_GAINS, default=128)

        # Build CS1237 configuration register byte
        # Bits 6:4 = PGA (0=1, 1=2, 2=64, 3=128) -> (gain << 4)
        # Bits 3:2 = SPEED (0=10Hz, 1=40Hz, 2=640Hz, 3=1280Hz)
        speed_bits = {10: 0, 40: 1, 640: 2, 1280: 3}[self.sps]
        gain_bits = self.gain
        self.cfg_reg = config.getint('cfg_reg', default=(gain_bits << 4) | (speed_bits << 2))

        # Clock tracking for bulk reader
        chip_smooth = self.sps * UPDATE_INTERVAL * 2
        self.ffreader = bulk_sensor.FixedFreqReader(mcu, chip_smooth, "<i")

        # Process messages in batches
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch, self._start_measurements,
            self._finish_measurements, UPDATE_INTERVAL)

        # MCU Command Configuration
        self.query_cs1237_cmd = None
        mcu.add_config_cmd(
            "config_cs1237 oid=%d cfg_reg=%d dout_pin=%s sclk_pin=%s"
            % (self.oid, self.cfg_reg, self.dout_pin, self.sclk_pin))
        mcu.add_config_cmd("query_cs1237 oid=%d rest_ticks=0"
                           % (self.oid,), on_restart=True)

        mcu.register_config_callback(self._build_config)

    def setup_trigger_analog(self, trigger_analog_oid):
        self.mcu.add_config_cmd(
            "cs1237_attach_trigger_analog oid=%d trigger_analog_oid=%d"
            % (self.oid, trigger_analog_oid), is_init=True)

    def _build_config(self):
        cmd_queue = self.mcu.alloc_command_queue()
        self.query_cs1237_cmd = self.mcu.lookup_command(
            "query_cs1237 oid=%c rest_ticks=%u", cq=cmd_queue)
        self.ffreader.setup_query_command("query_cs1237_status oid=%c",
                                          oid=self.oid, cq=cmd_queue)

    def get_mcu(self):
        return self.mcu

    def get_samples_per_second(self):
        return self.sps

    def get_status(self, eventtime):
        return {
            'errors': self.last_error_count,
            'overflows': self.ffreader.get_last_overflows(),
            'sample_rate': self.get_samples_per_second(),
        }

    def lookup_sensor_error(self, error_code):
        return "Unknown cs1237 error %d" % (error_code,)

    def get_range(self):
        return -0x800000, 0x7FFFFF

    def add_client(self, callback):
        self.batch_bulk.add_client(callback)

    def _convert_samples(self, samples):
        adc_factor = 1. / (1 << 23)
        count = 0
        for ptime, val in samples:
            if val == SAMPLE_ERROR_DESYNC or val == SAMPLE_ERROR_LONG_READ:
                self.last_error_count += 1
                break
            samples[count] = (round(ptime, 6), val, round(val * adc_factor, 9))
            count += 1
        del samples[count:]

    def _start_measurements(self):
        self.consecutive_fails = 0
        self.last_error_count = 0
        rest_ticks = self.mcu.seconds_to_clock(1. / (10. * self.sps))
        self.query_cs1237_cmd.send([self.oid, rest_ticks])
        logging.info("%s starting '%s' measurements",
                     self.sensor_type, self.name)
        self.ffreader.note_start()

    def _finish_measurements(self):
        if self.printer.is_shutdown():
            return
        self.query_cs1237_cmd.send_wait_ack([self.oid, 0])
        self.ffreader.note_end()
        logging.info("%s finished '%s' measurements",
                     self.sensor_type, self.name)

    def _process_batch(self, eventtime):
        prev_overflows = self.ffreader.get_last_overflows()
        prev_error_count = self.last_error_count
        samples = self.ffreader.pull_samples()
        self._convert_samples(samples)
        overflows = self.ffreader.get_last_overflows() - prev_overflows
        errors = self.last_error_count - prev_error_count
        if errors > 0:
            logging.error("%s: Forced sensor restart due to error", self.name)
            self._finish_measurements()
            self._start_measurements()
        elif overflows > 0:
            self.consecutive_fails += 1
            if self.consecutive_fails > 4:
                logging.error("%s: Forced sensor restart due to overflows",
                              self.name)
                self._finish_measurements()
                self._start_measurements()
        else:
            self.consecutive_fails = 0
        return {'data': samples, 'errors': self.last_error_count,
                'overflows': self.ffreader.get_last_overflows()}

CS1237_SENSOR_TYPE = {
    "cs1237": CS1237
}
