#!/usr/bin/env python3
"""GPU Fan Controller Daemon for AMD GPUs.

Reads GPU edge temperature from amdgpu hwmon and sends PWM duty cycle
to an Arduino over USB serial, which outputs 25kHz PWM to a 4-pin fan.

Strategy: progressive curve with zero-RPM idle and 10°C hysteresis.
"""

import time
import glob
import logging
import sys
import os

import serial

# ── Configuration ──────────────────────────────────────────────────────────

# Preferred symlink (created by udev rule), falls back to scanning
SERIAL_PORT = "/dev/gpu-fan-arduino"
SERIAL_FALLBACKS = ["/dev/ttyUSB*", "/dev/ttyACM*"]

BAUD_RATE = 115200
INTERVAL = 0.5  # seconds — sub-second temperature response

TEMP_SOURCE = "edge"

# Hysteresis: fan ON at HYSTERESIS_ON, stays on until temp < HYSTERESIS_OFF
HYSTERESIS_ON = 55.0
HYSTERESIS_OFF = 45.0

# Fan curve: (edge °C, duty 0-255) — progressive (convex)
FAN_CURVE = [
    (55, 60),    # start — 24%
    (65, 85),    # 65°C — 33%
    (75, 140),   # 75°C — 55%
    (85, 200),   # 85°C — 78%
    (90, 255),   # 90°C — 100%
]

# Only log when duty changes by this much (reduces noise from 0.5s polling)
LOG_DUTY_THRESHOLD = 5

# ── Internal ────────────────────────────────────────────────────────────────

log = logging.getLogger("gpu_fan")


def find_hwmon_path():
    for hwmon in sorted(glob.glob(
        "/sys/class/drm/card*/device/hwmon/hwmon*"
    )):
        try:
            with open(f"{hwmon}/name") as f:
                if f.read().strip() == "amdgpu":
                    return hwmon
        except OSError:
            continue
    return None


def identify_temp_sensor(hwmon):
    for i in (1, 2, 3):
        try:
            with open(f"{hwmon}/temp{i}_label") as f:
                if f.read().strip() == TEMP_SOURCE:
                    return f"{hwmon}/temp{i}_input"
        except OSError:
            continue
    for i in (1, 2, 3):
        p = f"{hwmon}/temp{i}_input"
        if glob.glob(p):
            return p
    return None


def read_temp(path):
    with open(path) as f:
        return int(f.read().strip()) / 1000.0


def compute_duty(temp):
    curve = FAN_CURVE
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for i in range(len(curve) - 1):
        t0, d0 = curve[i]
        t1, d1 = curve[i + 1]
        if t0 <= temp <= t1:
            return round(d0 + (d1 - d0) * (temp - t0) / (t1 - t0))
    return curve[-1][1]


def find_serial_port():
    if os.path.exists(SERIAL_PORT):
        return SERIAL_PORT
    for pattern in SERIAL_FALLBACKS:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def connect_serial(port=None):
    if port is None:
        port = find_serial_port()
    if port is None:
        return None
    ser = serial.Serial(port, BAUD_RATE, timeout=0.3)
    time.sleep(2)  # Arduino reset after serial open
    ser.reset_input_buffer()
    return ser


def send_duty(ser, duty):
    ser.write(bytes([duty]))
    ser.flush()
    ser.readline()  # consume echo


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [gpu_fan] %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("starting (off @ <%.0f°C, on @ >=%.0f°C, poll %.1fs)",
             HYSTERESIS_OFF, HYSTERESIS_ON, INTERVAL)

    hwmon = find_hwmon_path()
    if not hwmon:
        log.error("no amdgpu hwmon found")
        sys.exit(1)

    temp_path = identify_temp_sensor(hwmon)
    if not temp_path:
        log.error("no temp sensor found")
        sys.exit(1)
    log.info("hwmon: %s, source: %s", hwmon, TEMP_SOURCE)

    ser = None
    fan_active = False
    last_duty = -1
    last_log_duty = -1

    while True:
        # ── Reconnect serial ──────────────────────────────────────────
        if ser is None or not ser.is_open:
            try:
                ser = connect_serial()
                if ser:
                    log.info("serial: %s", ser.port)
                else:
                    time.sleep(INTERVAL)
                    continue
            except serial.SerialException as e:
                log.warning("serial error: %s", e)
                time.sleep(INTERVAL)
                continue

        # ── Read temperature ──────────────────────────────────────────
        try:
            temp = read_temp(temp_path)
        except OSError:
            time.sleep(INTERVAL)
            continue

        # ── Hysteresis state machine ──────────────────────────────────
        if not fan_active and temp >= HYSTERESIS_ON:
            fan_active = True
            log.info("fan ON @ %.0f°C", temp)
        elif fan_active and temp < HYSTERESIS_OFF:
            fan_active = False
            log.info("fan OFF @ %.0f°C", temp)

        duty = compute_duty(temp) if fan_active else 0

        # ── Send to Arduino ───────────────────────────────────────────
        try:
            send_duty(ser, duty)

            if duty != last_duty:
                # Log on state changes and significant duty shifts
                if (duty == 0 or last_duty == 0 or
                        abs(duty - last_log_duty) >= LOG_DUTY_THRESHOLD):
                    log.info("edge %.0f°C → %d/255 (%.0f%%)%s",
                             temp, duty, duty / 255 * 100,
                             " OFF" if duty == 0 else "")
                    last_log_duty = duty
                last_duty = duty

        except (serial.SerialException, OSError) as e:
            log.warning("serial lost: %s", e)
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            fan_active = False
            continue

        time.sleep(INTERVAL)


if __name__ == "__main__":
    run()
