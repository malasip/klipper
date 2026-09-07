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

1. Navigate to the Klipper directory:
   ```bash
   cd k2_pro_mainline
   ```
2. Open Klipper configuration menu:
   ```bash
   make menuconfig
   ```
3. Set the following parameters:
   * **Micro-controller Architecture**: `STMicroelectronics STM32`
   * **Processor model**: `STM32F103`
   * **Bootloader offset**: `64KiB bootloader` (or match stock bootloader partition offset)
   * **Clock Reference**: `8 MHz crystal`
   * **Communication interface**: `Serial (on USART2 PA3/PA2)`
   * **Baud rate for serial port**: `230400`
   * **GPIO pins to set at micro-controller startup**: `PA0` (turns on mainboard fan)
4. Compile the firmware:
   ```bash
   make clean
   make
   ```
5. The output binary is saved to `out/klipper.bin`.

---

## 3. Building Toolhead Firmware (GD32F303CBT6)

The toolhead board runs on a GD32F303CBT6 microcontroller.

1. Open Klipper configuration menu:
   ```bash
   make menuconfig
   ```
2. Set the following parameters:
   * **Micro-controller Architecture**: `STMicroelectronics STM32`
   * **Processor model**: `STM32F103`
   * **Bootloader offset**: `No bootloader` or `28KiB bootloader` (check partition table)
   * **Clock Reference**: `8 MHz crystal`
   * **Communication interface**: `Serial (on USART3 PB11/PB10)`
   * **Baud rate for serial port**: `230400`
   * **Include CS1237 sensor driver**: Enabled by default in `src/Kconfig`
3. Compile the firmware:
   ```bash
   make clean
   make
   ```
4. Save the compiled binary as `out/klipper_toolhead.bin`.

---

## 4. Flashing Methods

### Option A: Internal Host Serial Flash (Recommended)
Because the microcontrollers are wired to the Allwinner T113-i host via `/dev/ttyS2` and `/dev/ttyS3`, you can use Klipper's `scripts/flash_usb.py` or standard STM32 serial flashing tools (such as `stm32flash`):

```bash
# Flash Main MCU:
stm32flash -b 230400 -w out/klipper.bin -v -g 0x0 /dev/ttyS2

# Flash Toolhead MCU:
stm32flash -b 230400 -w out/klipper_toolhead.bin -v -g 0x0 /dev/ttyS3
```

### Option B: SWD Programmer (ST-Link / J-Link)
Both boards expose standard SWD (SWDIO / SWCLK / GND / 3.3V) programming test points on the PCB. If the bootloader is ever corrupted, connect an ST-Link V2 or Raspberry Pi Pico running Picoprobe to flash via OpenOCD:

```bash
openocd -f interface/stlink.cfg -f target/stm32f1x.cfg -c "program out/klipper.bin 0x08000000 verify reset exit"
```
