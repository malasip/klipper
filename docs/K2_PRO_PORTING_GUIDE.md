# Creality K2 Pro Mainline Klipper Porting & Architecture Guide

This guide explains the architecture of the clean mainline Klipper port for the Creality K2 Pro and provides a step-by-step procedure for bringing up the machine safely.

---

## 1. Why a Clean Mainline Port?

Creality's stock software stack heavily modified Klipper by introducing:
* Binary Cython `.so` modules (`prtouch_v3_wrapper`, `motor_control_wrapper`, `box_wrapper`).
* Non-standard internal function signatures in core Klipper modules (`probe.py`, `homing.py`, `gcode.py`, `virtual_sdcard.py`).
* Proprietary JSON error responses and lock-in to Python 3.9 and Creality Cloud.

This project delivers a **100% open-source mainline Klipper port**:
1. **CoreXY Kinematics**: Uses clean upstream Klipper Step/Dir generation.
2. **Closed-Loop Motors**: Run directly via standard hardware Step/Dir signals.
3. **Nozzle Load Cell**: Driven by a clean, open-source CS1237 24-bit ADC driver plugged directly into Klipper's native `[load_cell_probe]` framework.
4. **CFS (Multi-Material)**: Controlled by a non-blocking pure-Python async RS-485 driver (`creality_cfs.py`) that operates on standard Klipper without modifying core files.
5. **Thermals & Chamber**: Standard Klipper `[heater_bed]`, `[extruder]`, and `[heater_generic chamber_heater]` with macro safety interlocks.

---

## 2. Directory Layout & Key Files

```
k2_pro_mainline/
├── config/
│   ├── printer-creality-k2-pro.cfg       # Primary printer configuration
│   ├── generic-creality-k2-cfs.cfg       # CFS multi-color box configuration & macros
│   ├── k2-macros.cfg                     # START_PRINT, END_PRINT, toolchange macros
│   └── generic-cartographer.cfg          # Alternative Eddy/Cartographer probe template
├── klippy/extras/
│   ├── cs1237.py                         # CS1237 ADC Python host driver
│   ├── creality_cfs.py                   # Pure Python async CFS driver (RS-485)
│   └── load_cell.py                      # Extended with CS1237 sensor registration
├── src/
│   ├── sensor_cs1237.c                   # CS1237 24-bit ADC C driver for Klipper MCU
│   ├── Kconfig                           # Includes CONFIG_WANT_CS1237
│   └── Makefile                          # Builds sensor_cs1237.c
└── docs/
    ├── K2_PRO_PINOUTS.md                 # Complete pinout reference
    ├── K2_PRO_FLASHING.md                # MCU firmware build & flashing guide
    └── K2_PRO_PORTING_GUIDE.md           # This document
```

---

## 3. Step-by-Step Bring-Up Procedure

When bringing up a newly flashed printer on clean mainline Klipper, always follow this order:

### Step A: Communication Verification
1. Start Klipper:
   ```bash
   sudo systemctl restart klipper
   ```
2. Check `klippy.log` to verify that both the main MCU (`/dev/ttyS2`) and nozzle MCU (`/dev/ttyS3`) report successful identify handshakes.
3. In Mainsail / Fluidd console, verify temperature readings for:
   * Extruder (`~room temp`)
   * Bed (`~room temp`)
   * Chamber (`~room temp`)
   * MCU internal temperatures

### Step B: Endstops & Manual Stepping
1. Test endstop switches before moving:
   ```gcode
   QUERY_ENDSTOPS
   ```
2. Verify that X and Y show `open`. Manually press the limit switches and verify they report `TRIGGERED`.
3. In small 1mm increments, test manual jogging of X, Y, and Z to verify motor direction:
   ```gcode
   STEPPER_BUZZ STEPPER=stepper_x
   STEPPER_BUZZ STEPPER=stepper_y
   STEPPER_BUZZ STEPPER=stepper_z
   ```

### Step C: Heater PID Tuning
Perform PID calibration on hotend and bed:
```gcode
PID_CALIBRATE HEATER=extruder TARGET=220
PID_CALIBRATE HEATER=heater_bed TARGET=60
SAVE_CONFIG
```

### Step D: Load Cell Calibration & Probing
1. Ensure the nozzle is completely clean and at room temperature.
2. Check live load cell status:
   ```gcode
   LOAD_CELL_DIAGNOSTIC
   ```
3. Tare the sensor:
   ```gcode
   LOAD_CELL_TARE
   ```
4. Calibrate counts per gram:
   * Place a known weight (e.g. 100g calibration weight) onto the nozzle and run:
     ```gcode
     LOAD_CELL_CALIBRATE WEIGHT=100.0
     SAVE_CONFIG
     ```
5. Test a single probe move in the center of the bed:
   ```gcode
   PROBE
   ```

### Step E: CFS (Multi-Color) Testing
1. Verify RS-485 communication:
   ```gcode
   CFS_STATUS
   ```
2. Test tool selection:
   ```gcode
   T0
   ```
3. Test mechanical cutter arm actuation:
   ```gcode
   CFS_CUT_MATERIAL
   ```
