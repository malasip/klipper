# MCU Firmware Build & Flashing Guide for Creality K2 Pro

This guide explains how to compile and flash clean, upstream Klipper firmware for the two microcontrollers on the Creality K2 Pro:
1. **Main MCU**: GigaDevice `GD32F303RET6`
2. **Toolhead MCU**: GigaDevice `GD32F303CBT6`

---

## 1. Safety & Stock Firmware Backup

> [!WARNING]
> Before flashing any custom firmware to your microcontrollers, ensure you have a copy of the stock firmware binaries stored in a safe place.
> The original firmware binaries are located in the parent directory under:
> * `fw/F012/mcu0_120_G32-mcu0_001_000.bin` (Mainboard)
> * `fw/F012/noz0_130_G30-noz0_021_000.bin` (Toolhead)

---

## 2. Building Mainboard Firmware (GD32F303RET6)

The mainboard uses a GD32F303RET6 processor, which is software-compatible with the STMicroelectronics STM32F103 family running with an ARM Cortex-M4 core.

1. Configure Klipper:
   * **Micro-controller Architecture**: `STMicroelectronics STM32`
   * **Processor model**: `STM32F103`
   * **Bootloader offset**: `12KiB bootloader` (Flash starts at `0x08003000`)
   * **Clock Reference**: `8 MHz crystal`
   * **Communication interface**: `Serial (on USART2 PA3/PA2)`
   * **Baud rate for serial port**: `230400`
   * **GPIO pins to set at startup**: `!PB1,!PB2,!PB3,!PB4,!PA0,PA4,PA11,PB0,PA12,!PC8,!PC12`
2. Compile the firmware:
   ```bash
   make clean
   make
   cp out/klipper.bin build_artifacts/klipper_mainboard_12k.bin
   ```

---

## 3. Building Toolhead Firmware (GD32F303CBT6)

The toolhead board runs on a GD32F303CBT6 microcontroller.

1. Configure Klipper:
   * **Micro-controller Architecture**: `STMicroelectronics STM32`
   * **Processor model**: `STM32F103`
   * **Bootloader offset**: `12KiB bootloader` (Flash starts at `0x08003000`)
   * **Clock Reference**: `8 MHz crystal`
   * **Communication interface**: `Serial (on USART2 PA3/PA2)`
   * **Baud rate for serial port**: `230400`
   * **GPIO pins to set at startup**: `!PB1,!PB3,!PB8,!PB15`
   * **Include CS1237 sensor driver**: Enabled by default in `src/Kconfig`
2. Compile the firmware:
   ```bash
   make clean
   make
   cp out/klipper.bin build_artifacts/klipper_toolhead_12k.bin
   ```

---

## 4. Flashing Methods

### Option A: Host Internal Serial Flash via `mcu_util` & `mcu_reset.sh` (Recommended)

The K2 Pro microcontrollers run a stock serial bootloader that is active for **10 seconds** immediately after power is applied.

Power to the MCU rail is controlled by the Allwinner T113-i SoC via GPIO **`PE12`** (Pin **`140`**):
* `0`: Power ON
* `1`: Power OFF

An automated flashing script [`build_artifacts/flash_k2_mcu.sh`](file:///home/mika/Projects/k2_pro_mainline/build_artifacts/flash_k2_mcu.sh) executes the power cycle, handshakes within the 10-second window, and flashes the firmware:

```bash
# Flash Mainboard MCU:
./flash_k2_mcu.sh /dev/ttyS2 klipper_mainboard_12k.bin

# Flash Toolhead MCU:
./flash_k2_mcu.sh /dev/ttyS3 klipper_toolhead_12k.bin
```

#### Manual Flashing Sequence:
1. Power cycle MCU:
   ```bash
   /usr/bin/mcu_reset.sh
   # Or directly:
   echo 1 > /sys/class/gpio/gpio140/value && sleep 2 && echo 0 > /sys/class/gpio/gpio140/value
   ```
2. Within 10 seconds, handshake and flash:
   ```bash
   mcu_util -i /dev/ttyS2 -c -v
   mcu_util -i /dev/ttyS2 -u -f klipper_mainboard_nobootloader.bin -H -v
   mcu_util -i /dev/ttyS2 -s -v
   ```

### Option B: SWD Programmer (ST-Link / J-Link)
Both boards expose standard SWD (SWDIO / SWCLK / GND / 3.3V) programming test points on the PCB. If ever needed, connect an ST-Link V2 or Raspberry Pi Pico running Picoprobe to flash via OpenOCD:

```bash
openocd -f interface/stlink.cfg -f target/stm32f1x.cfg -c "program out/klipper.bin 0x08000000 verify reset exit"
```
