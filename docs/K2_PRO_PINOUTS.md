# Creality K2 Pro Hardware Pinout & Architecture Reference

This document details all hardware pin assignments, communication buses, and microcontroller allocations for the Creality K2 Pro (and K2 Plus).

---

## 1. Microcontrollers & Host Mapping

| Node | MCU Model | Package | Host Connection | Baud Rate | Role |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Main MCU** | GD32F303RET6 | LQFP64 | `/dev/ttyS2` (USART2) | 230400 | Motion (X, Y, Z), Bed, Chamber PTC, Filters |
| **Toolhead MCU** | GD32F303CBT6 | LQFP48 | `/dev/ttyS3` (USART3) | 230400 | Extruder, Hotend, CPAP fan, Load Cell, LIS2DW |
| **CFS Box** | Onboard slave MCU | - | `/dev/ttyS5` (RS-485) | 230400 | 4-Color Feed, Buffer, RFID |
| **Closed-Loop Motors**| Onboard motor MCUs| - | Step/Dir + RS-485 (`/dev/ttyS5`) | 230400 | X, Y, E Closed-loop FOC control |

---

## 2. Main Board Pinout (GD32F303RET6)

### Steppers & Endstops
| Function | Pin | Inverted? | Notes |
| :--- | :--- | :--- | :--- |
| **X Step** | `PB8` | No | Step pulse to X closed-loop driver |
| **X Dir** | `PB7` | Yes (`!PB7`) | Direction signal to X closed-loop driver |
| **X Enable**| `PA9` | Yes (`!PA9`) | Driver enable |
| **X Endstop**| `PB11`| No | Optical / stall trigger |
| **Y Step** | `PB10`| No | Step pulse to Y closed-loop driver |
| **Y Dir** | `PB9` | No | Direction signal to Y closed-loop driver |
| **Y Enable**| `PA8` | Yes (`!PA8`) | Driver enable |
| **Y Endstop**| `PB12`| No | Optical / stall trigger |
| **Z Step** | `PB6` | No | Step pulse to TMC2208 |
| **Z Dir** | `PB5` | Yes (`!PB5`) | Direction signal to TMC2208 |
| **Z Enable**| `PA10`| Yes (`!PA10`)| Driver enable |
| **Z TMC UART**| `PC1` | No | TMC2208 single-wire UART |

### Heaters & Thermal Sensors
| Function | Pin | Notes |
| :--- | :--- | :--- |
| **Bed Heater** | `PC8` | High-power MOSFET output |
| **Bed Thermistor** | `PC4` | Custom NTC 100K thermistor |
| **Chamber PTC Heater** | `PC12` | Solid state relay / MOSFET for 350W PTC heater |
| **Chamber Thermistor** | `PC5` | NTC 100K B57560G104F |

### Fans & Relays
| Function | Pin | Notes |
| :--- | :--- | :--- |
| **Chamber PTC Circulation Fan** | `PB14` (PWM), `PB2` (Enable) | High-CFM fan paired with PTC heater |
| **Chamber Filter / Exhaust Fan**| `PB1` | VOC / Carbon filter fan |
| **Mainboard Cooling Fan** | `PA0` | Chasis electronics enclosure cooling |
| **Chamber LED** | `PB0`, `PA12` | High-brightness internal lighting |
| **Main Power Relay** | `PC9` | Power latch pin |
| **Inter-MCU Sync Pin** | `PC7` | Step swap sync line to toolhead `PA15` |

---

## 3. Toolhead Board Pinout (GD32F303CBT6)

### Extruder & Sensors
| Function | Pin | Inverted? | Notes |
| :--- | :--- | :--- | :--- |
| **E Step** | `PB5` | No | Extruder stepper pulse |
| **E Dir** | `PB4` | Yes (`!PB4`) | Extruder direction |
| **E Enable**| `PB2` | Yes (`!PB2`) | Extruder enable |
| **Hotend Heater** | `PB8` | No | Ceramic heating element |
| **Hotend Thermistor**| `PA0` | No | Custom high-temp cartridge thermistor |
| **Part Cooling (Model) Fan** | `PB15` (PWM), `PB6` (Enable) | Yes (`!PB15`) | Dual-blower cooling system |
| **Hotend Heatsink Fan** | `PB7` | No | 3010 axial heatsink fan |
| **Filament Runout Switch**| `PA11` | Yes (`^!PA11`) | Toolhead mechanical switch |
| **Filament Cutter Sensor**| `PB9` | Yes (`!PB9`) | Cutter arm limit switch |

### Load Cell & Probing (CS1237 24-bit ADC)
| Function | Pin | Direction | Notes |
| :--- | :--- | :--- | :--- |
| **CS1237 SCLK (Clock)** | `PB13` | Output (from MCU)| Bit-bang serial clock |
| **CS1237 DOUT (Data)** | `PB14` | Input (to MCU) | 24-bit conversion data & ready signal |
| **Inter-MCU Sync Pin** | `PA15` | I/O | Hardware sync to mainboard `PC7` |

### Accelerometer (LIS2DW via Software SPI)
| Function | Pin | Notes |
| :--- | :--- | :--- |
| **CS (Chip Select)** | `PA4` | SPI Chip Select |
| **SCLK (Clock)** | `PA5` | Software SPI Clock |
| **MISO (Master In)** | `PA6` | Software SPI Data Out |
| **MOSI (Master Out)**| `PA7` | Software SPI Data In |

---

## 4. RS-485 Half-Duplex Bus (`/dev/ttyS5`)

| Device Type | Protocol Address | Interface | Role |
| :--- | :--- | :--- | :--- |
| **CFS Box Controller** | Assigned dynamically (`0x10` - `0x14`) | RS-485 | 4-channel spool feeding and telemetry |
| **CFS RFID Reader** | `0x11` | RS-485 | Spool RFID tag detection |
| **CFS Camera / Steer** | `0x41` | RS-485 | Filament buffer monitoring |
| **X Motor Controller** | Dynamic / Flash address | RS-485 | Closed-loop diagnostics & tuning |
| **Y Motor Controller** | Dynamic / Flash address | RS-485 | Closed-loop diagnostics & tuning |
| **E Motor Controller** | Dynamic / Flash address | RS-485 | Closed-loop diagnostics & tuning |
