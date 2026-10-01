# Creality External Spool RFID Reader Module for Klipper
#
# Connects to the side-panel RFID reader on RS-485 (default addr 0x11 / 17).
# Decodes 40-character Creality RFID tag payloads using cfs_material_db.json.
# Supports persistent spool memory across restarts in JSON.
# Optionally monitors an interrupt/card-detect pin (e.g. ^!PA5).
#
# Bus-sharing:
# - If [creality_cfs] is active, routes RS-485 requests through CFS's thread-safe
#   _bus_lock to prevent collisions on /dev/ttyS5.
# - If [creality_cfs] is not active, manages /dev/ttyS5 directly.

import os
import json
import logging

logger = logging.getLogger(__name__)

PACK_HEAD: int = 0xF7
CMD_GET_RFID: int = 0x02
STATUS_ADDRESSING: int = 0x00
POLY: int = 0x07

def crc8_cfs(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

def build_message(addr: int, status: int, func: int, data: bytes = b"") -> bytes:
    length = len(data) + 3
    crc_scope = bytes([length, status, func]) + data
    crc = crc8_cfs(crc_scope)
    return bytes([PACK_HEAD, addr, length, status, func]) + data + bytes([crc])

def parse_message(raw: bytes) -> dict:
    if len(raw) < 6 or raw[0] != PACK_HEAD:
        return None
    addr = raw[1]
    length = raw[2]
    if len(raw) < length + 3:
        return None
    status = raw[3]
    func = raw[4]
    data = raw[5:2 + length]
    crc = raw[2 + length]
    crc_scope = raw[2:2 + length]
    if crc8_cfs(crc_scope) != crc:
        return None
    return {"addr": addr, "status": status, "func": func, "data": data}


class CrealitySpoolRFID:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")

        self.addr = config.getint("addr", 17, minval=1, maxval=254)
        self.serial_port = config.get("serial_port", "/dev/ttyS5")
        self.baud = config.getint("baud", 230400)
        self.material_db_path = config.get(
            "material_db",
            "/home/klipper/printer_data/config/cfs_material_db.json"
        )
        self.state_file_path = config.get(
            "state_file",
            "/home/klipper/printer_data/config/spool_rfid_state.json"
        )

        # Spool state data
        self.state = {
            "active": False,
            "material": None,
            "vendor": None,
            "color": "none",
            "melt_temp": 220,
            "temp_min": 190,
            "temp_max": 240,
            "initial_length": 0,
            "initial_weight": 0,
            "tag_uid": None,
            "mat_code": None,
        }

        self.material_db = {}
        self.rfid_translation = {}

        # Shared CFS or standalone serial
        self.cfs = None
        self._fd = None
        self._last_read_time = 0.0

        # Cutter configuration & parameters (for solo operation or delegation)
        self.cfs_configured = config.has_section("creality_cfs")
        self.cut_switch_pin = config.get("cut_switch_pin", None)
        self.pre_cut_pos_x = config.getfloat("pre_cut_pos_x", 10.0)
        self.pre_cut_pos_y = config.getfloat("pre_cut_pos_y", 200.0)
        self.cut_pos_x = config.getfloat("cut_pos_x", -10.0)
        self.cut_pos_x_min = config.getfloat("cut_pos_x_min", -10.5)
        self.cut_pos_x_max = config.getfloat("cut_pos_x_max", None)
        self.cut_velocity = config.getfloat("cut_velocity", 30000.0, above=0.)
        self.cut_dwell = config.getfloat("cut_dwell", 0.150, minval=0.0, maxval=5.0)
        self.cut_retries = config.getint("cut_retries", 2, minval=0, maxval=10)
        self.cut_step = config.getfloat("cut_step", 0.5, minval=0.0, maxval=5.0)
        self.safe_pos_x = config.getfloat("safe_pos_x", 205.0)
        self.safe_pos_y = config.getfloat("safe_pos_y", 301.0)
        self.travel_velocity = config.getfloat("travel_velocity", 12000.0, above=0.0)
        self.min_clearance_z = config.getfloat("min_clearance_z", 5.0, minval=0.0)

        # Only register pin directly if [creality_cfs] is not in config to avoid pin collision
        self._cutter_button_state = False
        if not self.cfs_configured and self.cut_switch_pin:
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons([self.cut_switch_pin], self._cut_button_handler)
            logger.info("creality_spool_rfid: cut_switch_pin registered on %s", self.cut_switch_pin)

        # Optional pin trigger (^!PA5)
        self.pin = config.get("pin", None)
        if self.pin:
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons([self.pin], self._button_handler)
            logger.info("creality_spool_rfid: card-detect pin registered on %s", self.pin)

        # Event handlers
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:shutdown", self._handle_shutdown)

        # G-code commands
        self.gcode.register_command(
            "SPOOL_READ_RFID",
            self.cmd_SPOOL_READ_RFID,
            desc="Query external spool holder RFID reader",
        )
        self.gcode.register_command(
            "EXTERNAL_SPOOL_READ_RFID",
            self.cmd_SPOOL_READ_RFID,
            desc="Query external spool holder RFID reader (alias)",
        )
        self.gcode.register_command(
            "SPOOL_CLEAR",
            self.cmd_SPOOL_CLEAR,
            desc="Clear current external spool metadata",
        )
        self.gcode.register_command(
            "SPOOL_SET",
            self.cmd_SPOOL_SET,
            desc="Manually set external spool material and color",
        )
        self.gcode.register_command(
            "SPOOL_CUT",
            self.cmd_SPOOL_CUT,
            desc="Cut filament at toolhead (via CFS or solo mechanical cutter)",
        )
        self.gcode.register_command(
            "CUT_FILAMENT",
            self.cmd_SPOOL_CUT,
            desc="Cut filament at toolhead (alias)",
        )
        self.gcode.register_command(
            "SPOOL_UNLOAD",
            self.cmd_SPOOL_UNLOAD,
            desc="Cut and retract external spool filament from extruder",
        )

    # -----------------------------------------------------------------------
    # Initialization & Persistence
    # -----------------------------------------------------------------------

    def _handle_ready(self) -> None:
        self.cfs = self.printer.lookup_object("creality_cfs", None)
        self._load_material_db()
        self._load_state()

        if self.cfs:
            logger.info(
                "creality_spool_rfid: linked with creality_cfs module on RS-485 bus (addr=0x%02X)",
                self.addr
            )
        else:
            logger.info(
                "creality_spool_rfid: creality_cfs not loaded; operating standalone on %s (addr=0x%02X)",
                self.serial_port, self.addr
            )
            if "CFS_CUT" not in self.gcode.ready_gcode_handlers:
                self.gcode.register_command("CFS_CUT", self.cmd_SPOOL_CUT, desc="Cut filament at toolhead (solo)")
            if "CR_BOX_CUT" not in self.gcode.ready_gcode_handlers:
                self.gcode.register_command("CR_BOX_CUT", self.cmd_SPOOL_CUT, desc="Cut filament at toolhead (solo alias)")

    def _handle_shutdown(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _load_material_db(self) -> None:
        if not os.path.exists(self.material_db_path):
            logger.warning("creality_spool_rfid: material DB not found at %s", self.material_db_path)
            return
        try:
            with open(self.material_db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
            self.material_db = db.get("materials", {})
            self.rfid_translation = db.get("rfid_translation", {})
            logger.info(
                "creality_spool_rfid: loaded material DB (%d materials, %d RFID translations)",
                len(self.material_db), len(self.rfid_translation)
            )
        except Exception as err:
            logger.error("creality_spool_rfid: error loading material DB: %s", err)

    def _load_state(self) -> None:
        if not os.path.exists(self.state_file_path):
            return
        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state.update(data)
            logger.info(
                "creality_spool_rfid: loaded active spool: %s %s (%s)",
                self.state.get("vendor"), self.state.get("material"), self.state.get("color")
            )
        except Exception as err:
            logger.error("creality_spool_rfid: error loading state: %s", err)

    def _save_state(self) -> None:
        try:
            tmp = self.state_file_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, self.state_file_path)
        except Exception as err:
            logger.error("creality_spool_rfid: error saving state: %s", err)

    # -----------------------------------------------------------------------
    # Tag Decoding
    # -----------------------------------------------------------------------

    def decode_tag_payload(self, raw_text: str) -> bool:
        """Decode a 40-character Creality RFID tag string.
        Format:
          s[0:12]   UID (12 hex chars)
          s[12:18]  Material code (6 chars)
          s[18:24]  Color code (6 hex chars)
          s[24:28]  Length in meters (4 chars decimal)
          s[28:34]  Weight in kg (6 chars decimal)
        """
        s = raw_text.strip()
        if len(s) < 24:
            logger.warning("creality_spool_rfid: payload too short (%d chars): %s", len(s), s)
            return False

        tag_uid = s[0:12]
        mat_code = s[12:18]
        color_hex = s[18:24]
        length_m = int(s[24:28]) if len(s) >= 28 and s[24:28].isdigit() else 0
        weight_kg = int(s[28:34]) if len(s) >= 34 and s[28:34].isdigit() else 0

        # Resolve material and vendor
        trans = self.rfid_translation.get(mat_code, {})
        material_name = trans.get("material", None)
        vendor_name = trans.get("vendor", "Creality")

        # If not found directly in translations, check materials table
        if not material_name:
            for m_name, m_info in self.material_db.items():
                if m_info.get("creality_code") == mat_code or m_info.get("default_code") == mat_code:
                    material_name = m_name
                    vendor_name = "Creality"
                    break

        if not material_name:
            material_name = f"PLA-{mat_code}"

        # Resolve temperatures
        mat_info = self.material_db.get(material_name, {})
        melt_temp = mat_info.get("melt_temp", 220)
        temp_min = mat_info.get("temp_min", 190)
        temp_max = mat_info.get("temp_max", 240)

        # Color formatted as hex
        color_formatted = f"#{color_hex.upper()}" if color_hex.isalnum() else "#FFFFFF"

        self.state.update({
            "active": True,
            "material": material_name,
            "vendor": vendor_name,
            "color": color_formatted,
            "melt_temp": melt_temp,
            "temp_min": temp_min,
            "temp_max": temp_max,
            "initial_length": length_m,
            "initial_weight": weight_kg * 1000 if weight_kg else 1000,
            "tag_uid": tag_uid,
            "mat_code": mat_code,
        })
        self._save_state()
        return True

    # -----------------------------------------------------------------------
    # RS-485 Communication
    # -----------------------------------------------------------------------

    def query_rfid(self, timeout: float = 1.5) -> str:
        """Send query frame to RFID reader and return decoded ASCII text, or None."""
        if self.cfs is not None:
            # Delegate via CFS bus lock
            resp = self.cfs._send_command(
                self.addr, STATUS_ADDRESSING, CMD_GET_RFID,
                data=b"", timeout=timeout, retries=1
            )
            if resp and resp.get("data"):
                raw_data = resp["data"]
                return "".join(chr(b) for b in raw_data if 32 <= b < 127)
            return None

        # Standalone serial access if CFS is not loaded
        return self._query_standalone(timeout)

    def _query_standalone(self, timeout: float) -> str:
        try:
            import serial
            msg = build_message(self.addr, STATUS_ADDRESSING, CMD_GET_RFID, b"")
            with serial.Serial(self.serial_port, self.baud, timeout=timeout) as ser:
                ser.write(msg)
                ser.flush()
                # Read response (header F7, addr, len, ...)
                head = ser.read(1)
                if not head or head[0] != PACK_HEAD:
                    return None
                rest = ser.read(4) # addr, length, status, func
                if len(rest) < 4:
                    return None
                length = rest[1]
                data_and_crc = ser.read(length - 2) # payload + crc
                if len(data_and_crc) < length - 2:
                    return None
                frame = head + rest + data_and_crc
                parsed = parse_message(frame)
                if parsed and parsed.get("data"):
                    raw_data = parsed["data"]
                    return "".join(chr(b) for b in raw_data if 32 <= b < 127)
        except Exception as err:
            logger.error("creality_spool_rfid: standalone query error: %s", err)
        return None

    # -----------------------------------------------------------------------
    # Edge Trigger / Pin Handler
    # -----------------------------------------------------------------------

    def _button_handler(self, eventtime, state) -> None:
        # Debounce: minimum 2.0s between tap reads
        if eventtime - self._last_read_time < 2.0:
            return
        self._last_read_time = eventtime
        logger.info("creality_spool_rfid: card-detect pin edge detected (state=%s)", state)
        self.reactor.register_callback(lambda e: self._async_read_on_tap())

    def _async_read_on_tap(self) -> None:
        raw_text = self.query_rfid(timeout=1.5)
        if raw_text and self.decode_tag_payload(raw_text):
            msg = (
                f"Spool RFID read: {self.state['vendor']} {self.state['material']} "
                f"[{self.state['color']}] {self.state['melt_temp']}°C "
                f"({self.state['initial_length']}m / {self.state['initial_weight']}g)"
            )
            self.gcode.respond_info(msg)
            logger.info("creality_spool_rfid: %s", msg)

    # -----------------------------------------------------------------------
    # G-Code Commands
    # -----------------------------------------------------------------------

    def cmd_SPOOL_READ_RFID(self, gcmd) -> None:
        """G-code: SPOOL_READ_RFID [TIMEOUT=<float>]"""
        timeout = gcmd.get_float("TIMEOUT", 1.5)
        gcmd.respond_info(f"Scanning external spool RFID reader (addr=0x{self.addr:02X})...")
        raw_text = self.query_rfid(timeout=timeout)
        if raw_text and self.decode_tag_payload(raw_text):
            gcmd.respond_info(
                f"SPOOL_READ_RFID: Identified {self.state['vendor']} {self.state['material']} "
                f"({self.state['color']}), Temp: {self.state['melt_temp']}°C, "
                f"Length: {self.state['initial_length']}m, Weight: {self.state['initial_weight']}g "
                f"[UID: {self.state['tag_uid']}]"
            )
        else:
            gcmd.respond_info("SPOOL_READ_RFID: No tag detected or read error.")

    def cmd_SPOOL_CLEAR(self, gcmd) -> None:
        """G-code: SPOOL_CLEAR - Clears active external spool metadata."""
        self.state.update({
            "active": False,
            "material": None,
            "vendor": None,
            "color": "none",
            "melt_temp": 220,
            "initial_length": 0,
            "initial_weight": 0,
            "tag_uid": None,
            "mat_code": None,
        })
        self._save_state()
        gcmd.respond_info("External spool cleared.")

    def cmd_SPOOL_SET(self, gcmd) -> None:
        """G-code: SPOOL_SET [MATERIAL=<name>] [COLOR=<hex>] [VENDOR=<name>] [TEMP=<int>]"""
        mat = gcmd.get("MATERIAL", self.state.get("material") or "PLA")
        color = gcmd.get("COLOR", self.state.get("color") or "#FFFFFF")
        vendor = gcmd.get("VENDOR", self.state.get("vendor") or "Generic")
        melt_temp = gcmd.get_int("TEMP", self.state.get("melt_temp") or 220)

        clean_col = color.strip()
        if not clean_col.startswith("#") and len(clean_col) == 6:
            clean_col = "#" + clean_col.upper()

        self.state.update({
            "active": True,
            "material": mat.strip(),
            "vendor": vendor.strip(),
            "color": clean_col,
            "melt_temp": melt_temp,
        })
        self._save_state()
        gcmd.respond_info(
            f"External spool set: {self.state['vendor']} {self.state['material']} ({self.state['color']}) {self.state['melt_temp']}°C"
        )

    # -----------------------------------------------------------------------
    # Cutter Logic (Delegated to CFS or Solo Mechanical Cutter)
    # -----------------------------------------------------------------------

    def _cut_button_handler(self, eventtime, state) -> None:
        self._cutter_button_state = bool(state)

    def _cutter_sensor_detected(self):
        """Toolhead mechanical cutter sensor state: True if blade is depressed,
        False if blade is rebounded/released, or None if no sensor is configured."""
        if self.cfs is not None:
            return self.cfs._cutter_sensor_detected()
        if self.cut_switch_pin:
            return bool(self._cutter_button_state)
        return None

    def _safe_corridor_move(self, gcmd, target_x: float, target_y: float) -> None:
        """Navigate toolhead safely using kinematic corridor."""
        if self.cfs is not None:
            self.cfs._safe_corridor_move(gcmd, target_x, target_y)
            return

        toolhead = self.printer.lookup_object('toolhead')
        cur_pos = toolhead.get_position()
        act_x, act_y, act_z = cur_pos[0], cur_pos[1], cur_pos[2]

        if abs(act_x - target_x) < 0.5 and abs(act_y - target_y) < 0.5:
            return

        cur_status = toolhead.get_status(self.reactor.monotonic())
        homed_axes = cur_status.get('homed_axes', '')
        if 'z' in homed_axes and act_z < self.min_clearance_z:
            gcmd.respond_info(
                "Spool: lifting Z from %.2f mm to %.2f mm for clearance"
                % (act_z, self.min_clearance_z))
            self.gcode.run_script_from_command("G90")
            self.gcode.run_script_from_command("G1 Z%.3f F1200" % self.min_clearance_z)
            self.gcode.run_script_from_command("M400")

        self.gcode.run_script_from_command("G90")
        fr = self.travel_velocity

        if act_y > self.safe_pos_y:
            if target_y > self.safe_pos_y:
                self.gcode.run_script_from_command("G0 X%.3f Y%.3f F%.0f" % (target_x, target_y, fr))
                self.gcode.run_script_from_command("M400")
                return
            if abs(act_x - self.safe_pos_x) > 0.5:
                self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (self.safe_pos_x, fr))
            self.gcode.run_script_from_command("G0 Y%.3f F%.0f" % (self.safe_pos_y, fr))
            act_x = self.safe_pos_x
            act_y = self.safe_pos_y
            if target_y <= 285.0:
                exit_y = min(285.0, target_y)
                self.gcode.run_script_from_command("G0 Y%.3f F%.0f" % (exit_y, fr))
                act_y = exit_y

        if target_y > self.safe_pos_y:
            if act_y < 285.0:
                self.gcode.run_script_from_command("G0 Y285.000 F%.0f" % fr)
            if abs(act_x - self.safe_pos_x) > 0.5:
                self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (self.safe_pos_x, fr))
            self.gcode.run_script_from_command("G0 Y%.3f F%.0f" % (self.safe_pos_y, fr))
            self.gcode.run_script_from_command("G0 X%.3f Y%.3f F%.0f" % (target_x, target_y, fr))
            self.gcode.run_script_from_command("M400")
            return

        self.gcode.run_script_from_command("G0 X%.3f Y%.3f F%.0f" % (target_x, target_y, fr))
        self.gcode.run_script_from_command("M400")

    def cmd_SPOOL_CUT(self, gcmd) -> None:
        """G-code: SPOOL_CUT [CUT_X=] [CUT_Y=] [DWELL=] [RETRY=] [CUT_STEP=] -- mechanical filament cut."""
        if self.cfs is not None:
            # Delegate directly to creality_cfs module
            self.cfs.cmd_CFS_CUT(gcmd)
            return

        if not self.cut_switch_pin:
            raise gcmd.error(
                "SPOOL_CUT aborted: no cut_switch_pin configured in [creality_spool_rfid]. "
                "Refusing to blind-ram the toolhead without a cutter switch.")

        pre_x = self.pre_cut_pos_x
        pre_y = self.pre_cut_pos_y
        cut_x = gcmd.get_float("CUT_X", self.cut_pos_x)
        cut_y = gcmd.get_float("CUT_Y", self.cut_pos_y)
        cut_dwell = gcmd.get_float("DWELL", self.cut_dwell, minval=0.0, maxval=5.0)
        retries = gcmd.get_int("RETRY", self.cut_retries, minval=0, maxval=10)
        cut_step = gcmd.get_float("CUT_STEP", self.cut_step, minval=0.0, maxval=5.0)

        # Pre-check cutter sensor state
        if self._cutter_sensor_detected() is True:
            raise gcmd.error(
                "SPOOL_CUT aborted: cutter sensor is already triggered before starting ram stroke! "
                "Cutter blade lever may be stuck or switch inverted.")

        gcmd.respond_info(
            "SPOOL_CUT: navigating safely to pre-cut (%.2f, %.2f) at F%.0f..."
            % (pre_x, pre_y, self.travel_velocity))
        self._safe_corridor_move(gcmd, pre_x, pre_y)

        fr = self.cut_velocity
        cut_success = False
        attempt = 0
        current_x = cut_x

        while attempt <= retries:
            attempt += 1
            gcmd.respond_info("SPOOL_CUT: ram stroke #%d -> X%.2f at F%.0f" % (attempt, current_x, fr))
            self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (current_x, fr))
            self.gcode.run_script_from_command("M400")

            if cut_dwell > 0:
                self.gcode.run_script_from_command("G4 P%d" % int(cut_dwell * 1000.0))
                self.gcode.run_script_from_command("M400")

            sensor_at_cut = self._cutter_sensor_detected()
            if sensor_at_cut is True:
                gcmd.respond_info("SPOOL_CUT: cutter sensor triggered (confirmed at X=%.2f)." % current_x)
                cut_success = True
            elif sensor_at_cut is False:
                gcmd.respond_info("SPOOL_CUT: cutter sensor NOT triggered at X=%.2f." % current_x)
            else:
                cut_success = True

            # Return toolhead to pre-cut position
            self.gcode.run_script_from_command("G0 X%.3f F%.0f" % (pre_x, fr))
            self.gcode.run_script_from_command("M400")

            if cut_success:
                break

            if attempt <= retries:
                next_x = current_x - cut_step
                if self.cut_pos_x_min is not None and next_x < self.cut_pos_x_min:
                    next_x = self.cut_pos_x_min
                if abs(next_x - current_x) < 0.01:
                    gcmd.respond_info("SPOOL_CUT: reached stroke limit (X=%.2f), cannot step deeper." % current_x)
                    break
                gcmd.respond_info("SPOOL_CUT: stepping deeper: X%.2f -> X%.2f" % (current_x, next_x))
                current_x = next_x

        # Rebound verification
        self.reactor.pause(self.reactor.monotonic() + 0.1)
        sensor_after = self._cutter_sensor_detected()
        if sensor_after is True:
            raise gcmd.error(
                "SPOOL_CUT error: cutter blade did not rebound after stroke (sensor still triggered)! "
                "Cutter blade or return spring may be jammed.")

        if not cut_success:
            raise gcmd.error(
                "SPOOL_CUT failed: cutter sensor was not triggered after %d attempt(s). "
                "Filament is UNCONFIRMED/UNCUT." % attempt)

        gcmd.respond_info("SPOOL_CUT: filament cut confirmed.")

    def cmd_SPOOL_UNLOAD(self, gcmd) -> None:
        """G-code: SPOOL_UNLOAD [RETRUDE_LEN=<mm>] -- cut and retract external spool filament."""
        gcmd.respond_info("SPOOL_UNLOAD: Cutting external spool filament at toolhead...")
        self.cmd_SPOOL_CUT(gcmd)

        retrude_len = gcmd.get_float("RETRUDE_LEN", 35.0, minval=0.0, maxval=100.0)
        if retrude_len > 0:
            gcmd.respond_info("SPOOL_UNLOAD: Retracting %.1f mm from extruder gears..." % retrude_len)
            self.gcode.run_script_from_command("M83")
            self.gcode.run_script_from_command("G1 E-%.2f F600" % retrude_len)
            self.gcode.run_script_from_command("M400")

        self._safe_corridor_move(gcmd, self.safe_pos_x, self.safe_pos_y)
        self.state["active"] = False
        self._save_state()
        gcmd.respond_info("SPOOL_UNLOAD: Complete. Please rewind filament onto external spool holder.")

    # -----------------------------------------------------------------------
    # Status Surface
    # -----------------------------------------------------------------------

    def get_status(self, eventtime=None) -> dict:
        status = dict(self.state)
        status["cutter_sensor"] = self._cutter_sensor_detected()
        return status


def load_config(config):
    return CrealitySpoolRFID(config)

