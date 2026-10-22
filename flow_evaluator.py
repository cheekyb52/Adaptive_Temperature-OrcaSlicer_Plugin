# /// script
# requires-python = ">=3.12"
# [tool.orcaslicer.plugin]
# name = "Predictive Flow Evaluator"
# description = "Analysis-only, acceleration-aware volumetric flow report. Does not alter G-code."
# author = "Adaptive Temperature contributors"
# version = "0.1.0"
# type = "slicing-pipeline"
# ///
# SPDX-License-Identifier: GPL-3.0-only
# Motion look-ahead adapted from Klipper klippy/toolhead.py,
# Copyright (C) 2016-2025 Kevin O'Connor <kevin@koconnor.net>.
# See LICENSE.txt for attribution and license information.
"""Offline flow forecast and Orca 2.5 plugin. Never emits heater commands.

CLI: python flow_evaluator.py input.gcode --report report.json --csv moves.csv
The Orca capability keeps the latest report in its Config tab's last_report field.
The model approximates Klipper planning; firmware differences affect timing.
"""
import argparse
from collections import Counter
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

DEFAULTS = {
    "logical_tool": 2,
    "filament_diameter_mm": 1.75,
    "max_velocity_mm_s": 750.0,
    "max_accel_mm_s2": 30000.0,
    "square_corner_velocity_mm_s": 15.0,
    "minimum_cruise_ratio": 0.5,
    "max_z_velocity_mm_s": 15.0,
    "max_z_accel_mm_s2": 200.0,
    "extrude_only_velocity_mm_s": 100.0,
    "extrude_only_accel_mm_s2": 5000.0,
    "instantaneous_extruder_corner_velocity_mm_s": 1.0,
    "high_flow_threshold_mm3_s": 60.0,
    "calibration": {
        "material": "Unconfigured filament", "physical_head": None,
        "hotend": "Unconfigured hotend", "heater_watts": None,
        "nozzle_diameter_mm": None,
        "user_reported_point": None,
        "hard_test_bounds": {"max_commanded_flow_mm3_s": 75.0,
                             "max_temperature_target_c": 245.0,
                             "automatically_expand": False},
        "heating_response": None, "cooling_response": None,
    },
}
PARAM = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
KEYVAL = re.compile(r"([A-Z_]+)=([-+]?(?:\d+\.?\d*|\.\d+))", re.I)


def options(config=None):
    out = {**DEFAULTS, **(config or {})}
    for key in DEFAULTS:
        if key in ("calibration", "logical_tool"):
            continue
        val = float(out[key])
        if not math.isfinite(val) or val < 0 or (val == 0 and key not in (
                "square_corner_velocity_mm_s", "minimum_cruise_ratio")):
            raise ValueError(f"Invalid setting: {key}")
        out[key] = val
    if out["minimum_cruise_ratio"] >= 1:
        raise ValueError("minimum_cruise_ratio must be less than 1")
    if isinstance(out["logical_tool"], bool) or int(out["logical_tool"]) != out["logical_tool"] or not 0 <= out["logical_tool"] < 32:
        raise ValueError("logical_tool must be an integer from 0 to 31")
    return out


@dataclass
class Move:
    line: int
    tool: int | None
    role: str
    delta: tuple
    e_delta: float
    length: float
    speed: float
    accel: float
    pseudo_accel: float
    jd: float
    deposited_mm3: float
    kinematic: bool = True
    start_limit: float = 0.0
    mcr_start_limit: float = 0.0
    start_speed: float = 0.0
    cruise_speed: float = 0.0
    end_speed: float = 0.0
    accel_time: float = 0.0
    cruise_time: float = 0.0
    decel_time: float = 0.0
    time: float = 0.0

    @property
    def delta_v2(self):
        return 2 * self.length * self.accel

    @property
    def mcr_delta_v2(self):
        return 2 * self.length * min(self.accel, self.pseudo_accel)

    @property
    def duration(self):
        return self.accel_time + self.cruise_time + self.decel_time

    @property
    def volume_per_mm(self):
        return self.deposited_mm3 / self.length


def plan_moves(moves, extruder_corner_velocity=1.0):
    """Full-batch Klipper-style junction and minimum-cruise look-ahead.

    Boundaries are at rest. Adapted from Klipper's Move.calc_junction and
    LookAheadQueue.flush, GPL-3.0-only. No hardware/MCU dependencies.
    """
    for i, move in enumerate(moves):
        move.start_limit = move.mcr_start_limit = 0.0
        if i == 0 or not move.kinematic or not moves[i-1].kinematic:
            continue
        prev = moves[i-1]
        limit = min(move.speed**2, prev.speed**2, prev.start_limit + prev.delta_v2)
        diff_e = move.e_delta / move.length - prev.e_delta / prev.length
        if diff_e:
            limit = min(limit, (extruder_corner_velocity / abs(diff_e))**2)
        cos_theta = max(-1.0, min(1.0, -sum(a*b for a, b in zip(move.delta, prev.delta)) / (move.length * prev.length)))
        sin_half = math.sqrt(max(0.0, (1-cos_theta)/2))
        cos_half = math.sqrt(max(0.0, (1+cos_theta)/2))
        if 1-sin_half > 0 and cos_half > 0:
            radius = sin_half / (1-sin_half)
            quarter_tan = 0.25 * sin_half / cos_half
            limit = min(limit, radius*move.jd*move.accel,
                        radius*prev.jd*prev.accel,
                        move.delta_v2*quarter_tan, prev.delta_v2*quarter_tan)
        move.start_limit = limit
        move.mcr_start_limit = min(limit, prev.mcr_start_limit + prev.mcr_delta_v2)
    next_v2 = next_mcr_v2 = peak_v2 = 0.0
    pending = 0
    junctions = [None] * len(moves)
    for i in range(len(moves)-1, -1, -1):
        move = moves[i]
        reachable = next_v2 + move.delta_v2
        start = min(move.start_limit, reachable)
        pending += 1
        reach_mcr = next_mcr_v2 + move.mcr_delta_v2
        mcr_start = min(move.mcr_start_limit, reach_mcr)
        cruise = None
        if mcr_start < reach_mcr:
            if mcr_start + move.mcr_delta_v2 > next_mcr_v2 or pending > 1:
                peak_v2 = (mcr_start + reach_mcr) / 2
            cruise = min((start + reachable)/2, move.speed**2, peak_v2)
            pending = 0
        junctions[i] = (start, cruise, next_v2)
        next_v2, next_mcr_v2 = start, mcr_start
    prev_cruise = 0.0
    for move, (start, cruise, end) in zip(moves, junctions):
        if cruise is None:
            cruise = min(prev_cruise, start)
        start, end = min(start, cruise), min(end, cruise)
        move.start_speed, move.cruise_speed, move.end_speed = map(math.sqrt, (start, cruise, end))
        accel_d = max(0, (cruise-start)/(2*move.accel))
        decel_d = max(0, (cruise-end)/(2*move.accel))
        move.accel_time = (move.cruise_speed-move.start_speed)/move.accel
        move.decel_time = (move.cruise_speed-move.end_speed)/move.accel
        move.cruise_time = max(0, move.length-accel_d-decel_d)/move.cruise_speed if cruise else 0
        prev_cruise = cruise


class Analyzer:
    def __init__(self, config=None):
        self.cfg = options(config)
        c = self.cfg
        self.velocity = c["max_velocity_mm_s"]
        self.accel = c["max_accel_mm_s2"]
        self.scv = c["square_corner_velocity_mm_s"]
        self.mcr = c["minimum_cruise_ratio"]
        self.xyz = [None, None, None]
        self.e = None
        self.absolute_xyz = self.absolute_e = True
        self.feed = None
        self.speed_factor = self.flow_factor = 1.0
        self.tool = None
        self.debt = Counter()
        self.role = "Unclassified"
        self.batch = []
        self.moves = []
        self.clock = 0.0
        self.unknown = Counter()
        self.skipped = Counter()
        self.observed = Counter()
        self.flow_supported = True
        self.temperature_commands = Counter()
        self.lines = 0

    def flush(self):
        plan_moves(self.batch, self.cfg["instantaneous_extruder_corner_velocity_mm_s"])
        for move in self.batch:
            move.time = self.clock
            self.clock += move.duration
        self.moves.extend(self.batch)
        self.batch = []

    def barrier(self, command, invalidate=True):
        self.flush()
        self.unknown[command] += 1
        if invalidate:
            self.xyz = [None, None, None]
            self.e = None
            self.feed = None
            self.role = "Unclassified"

    def read(self, lines):
        for line_no, raw in enumerate(lines, 1):
            self.lines = line_no
            code, _, comment = raw.partition(";")
            if comment.startswith("TYPE:") or comment.startswith(" FEATURE:"):
                self.role = comment.split(":", 1)[1].strip()
            code = re.sub(r"\([^)]*\)", "", code).strip()
            if not code:
                continue
            match = re.match(r"([GMT]\d+(?:\.\d+)?|[A-Z_][A-Z_0-9]*)", code, re.I)
            if not match:
                self.barrier("unparsed line")
                continue
            cmd = match[1].upper()
            tail = code[match.end():]
            p = {k.upper(): float(v) for k, v in PARAM.findall(tail)}
            kv = {k.upper(): float(v) for k, v in KEYVAL.findall(tail)}
            if cmd in ("G0", "G00", "G1", "G01"):
                self.motion(line_no, p)
            elif cmd in ("G90", "G91"):
                self.absolute_xyz = cmd == "G90"
            elif cmd in ("M82", "M83"):
                self.absolute_e = cmd == "M82"
            elif cmd == "G92":
                for i, axis in enumerate("XYZ"):
                    if axis in p:
                        self.xyz[i] = p[axis]
                if "E" in p:
                    self.e = p["E"]
            elif re.fullmatch(r"T\d+", cmd):
                self.flush()
                self.tool = int(cmd[1:])
                self.barrier(cmd)
                self.observed[f"logical_tool_{self.tool}"] += 1
            elif cmd == "SET_VELOCITY_LIMIT":
                self.observed[cmd] += 1
                if "VELOCITY" in kv:
                    self.velocity = self.positive(kv["VELOCITY"], cmd)
                if "ACCEL" in kv:
                    self.accel = self.positive(kv["ACCEL"], cmd)
                if "SQUARE_CORNER_VELOCITY" in kv:
                    self.scv = max(0, kv["SQUARE_CORNER_VELOCITY"])
                if "MINIMUM_CRUISE_RATIO" in kv:
                    self.mcr = kv["MINIMUM_CRUISE_RATIO"]
                    if not 0 <= self.mcr < 1:
                        raise ValueError("Invalid MINIMUM_CRUISE_RATIO")
                elif "ACCEL_TO_DECEL" in kv:
                    self.mcr = max(0, 1-self.positive(kv["ACCEL_TO_DECEL"], cmd)/self.accel)
                    self.observed["legacy_accel_to_decel"] += 1
            elif cmd == "M204":
                value = p.get("S")
                if value is None and "P" in p and "T" in p:
                    value = min(p["P"], p["T"])
                if value is not None:
                    self.accel = self.positive(value, cmd)
                else:
                    self.skipped["M204 without S or both P and T"] += 1
            elif cmd == "M220" and "S" in p:
                self.speed_factor = self.positive(p["S"], cmd)/100
            elif cmd == "M221" and "S" in p:
                self.flow_factor = self.positive(p["S"], cmd)/100
            elif cmd in ("M104", "M109", "SET_HEATER_TEMPERATURE", "TEMPERATURE_WAIT"):
                self.temperature_commands[cmd] += 1
                if cmd in ("M109", "TEMPERATURE_WAIT"):
                    self.barrier(cmd, invalidate=False)
            elif cmd in ("G4", "G04"):
                self.flush()
                self.clock += max(0, p.get("P", 0)/1000 + p.get("S", 0))
            elif cmd == "M400":
                self.flush()
            elif cmd == "G21":
                pass
            elif cmd in ("G20", "M200"):
                raise ValueError(f"Unsupported units/extrusion mode {cmd} at line {line_no}")
            elif cmd in ("M201", "M203", "M205"):
                self.skipped[f"{cmd}: Marlin limits/jerk not interpreted as Klipper limits"] += 1
            elif cmd in ("G2", "G3", "G02", "G03", "G10", "G11"):
                self.flow_supported = False
                self.skipped[f"unsupported motion {cmd}"] += 1
                self.barrier(cmd)
            elif cmd in ("M106", "M107", "M140", "M117", "M73", "M486", "M900",
                          "SET_PRESSURE_ADVANCE", "SET_FAN_SPEED", "SET_PRINT_STATS_INFO",
                          "EXCLUDE_OBJECT_DEFINE", "EXCLUDE_OBJECT_START", "EXCLUDE_OBJECT_END",
                          "TIMELAPSE_TAKE_FRAME", "RESPOND"):
                if cmd == "TIMELAPSE_TAKE_FRAME":
                    self.barrier(cmd)
            else:
                self.barrier(cmd)
        self.flush()
        return self.report()

    @staticmethod
    def positive(value, name):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} requires positive finite value")
        return value

    def motion(self, line, p):
        if "F" in p:
            self.feed = self.positive(p["F"], "feedrate")/60
        delta = [0.0]*3
        known = True
        for i, axis in enumerate("XYZ"):
            if axis not in p:
                continue
            if self.absolute_xyz:
                if self.xyz[i] is None:
                    known = False
                else:
                    delta[i] = p[axis]-self.xyz[i]
                self.xyz[i] = p[axis]
            else:
                delta[i] = p[axis]
                if self.xyz[i] is not None:
                    self.xyz[i] += p[axis]
        e_delta = 0.0
        if "E" in p:
            if self.absolute_e:
                if self.e is None:
                    known = False
                else:
                    e_delta = p["E"]-self.e
                self.e = p["E"]
            else:
                e_delta = p["E"]
                if self.e is not None:
                    self.e += e_delta
        e_delta *= self.flow_factor
        length = math.sqrt(sum(d*d for d in delta))
        kinematic = length > 1e-9
        if not kinematic:
            length = abs(e_delta)
        if not known or (length > 0 and self.feed is None):
            self.flush()
            self.skipped["move with unknown position/feed after macro or startup"] += 1
            return
        if length < 1e-9:
            return
        speed = self.feed*self.speed_factor
        accel = self.accel
        if kinematic:
            speed = min(speed, self.velocity)
            if abs(delta[2]) > 1e-9:
                z_ratio = abs(delta[2])/length
                speed = min(speed, self.cfg["max_z_velocity_mm_s"]/z_ratio)
                accel = min(accel, self.cfg["max_z_accel_mm_s2"]/z_ratio)
        else:
            speed = min(speed, self.cfg["extrude_only_velocity_mm_s"])
            accel = self.cfg["extrude_only_accel_mm_s2"]
        self.debt[self.tool] += max(0, -e_delta)
        recovered = min(max(0, e_delta), self.debt[self.tool])
        self.debt[self.tool] -= recovered
        deposited_e = max(0, e_delta-recovered) if kinematic else 0
        volume = deposited_e*math.pi*(self.cfg["filament_diameter_mm"]/2)**2
        self.batch.append(Move(
            line, self.tool, self.role, tuple(delta), e_delta, length, speed, accel,
            self.accel*(1-self.mcr), self.scv**2*(math.sqrt(2)-1)/self.accel,
            volume, kinematic))

    def report(self):
        selected = [m for m in self.moves if m.tool == self.cfg["logical_tool"] and m.deposited_mm3 > 0]
        intervals = []
        threshold = self.cfg["high_flow_threshold_mm3_s"]
        for m in selected:
            v_threshold = threshold/m.volume_per_mm
            offset = m.time
            for duration, v0, v1 in ((m.accel_time, m.start_speed, m.cruise_speed),
                                      (m.cruise_time, m.cruise_speed, m.cruise_speed),
                                      (m.decel_time, m.cruise_speed, m.end_speed)):
                if duration > 0 and max(v0, v1) >= v_threshold:
                    lo, hi = 0.0, duration
                    if v0 < v_threshold:
                        lo = duration*(v_threshold-v0)/(v1-v0)
                    if v1 < v_threshold:
                        hi = duration*(v0-v_threshold)/(v0-v1)
                    if hi > lo:
                        start, end = offset+lo, offset+hi
                        if intervals and start <= intervals[-1][1]+1e-8:
                            intervals[-1][1] = end
                        else:
                            intervals.append([start, end])
                offset += duration
        duration = sum(m.duration for m in selected)
        warnings = [
            "Analysis only: no temperature commands are generated and G-code is unchanged.",
            "Flow is a forecast of commanded deposition, not measured delivered melt flow.",
            "Times exclude unknown macro execution and heater waits; not absolute print timestamps.",
            "Logical tool selection is not proof of the intended physical head mapping for a future print.",
            "Klipper-like full-batch planner; firmware differences, queue starvation, pressure advance, runtime overrides and bed mesh are not fully simulated.",
            "Startup/purge moves can appear as Custom or Unclassified; do not calibrate from them.",
            "Flow capacity depends on the configured filament and hotend calibration.",
        ]
        if self.observed["legacy_accel_to_decel"]:
            warnings.append("ACCEL_TO_DECEL modeled as legacy smoothed acceleration; fork compatibility must be verified.")
        if self.skipped or self.unknown:
            warnings.append("Coverage is partial: inspect skipped commands and unknown timing barriers before using these estimates.")
        return {
            "version": "0.1.0", "mode": "analysis_only", "gcode_modified": False,
            "ready_for_temperature_control": False,
            "motion_model": "Klipper-like junction look-ahead, acceleration trapezoids and minimum cruise ratio",
            "selected_logical_tool": self.cfg["logical_tool"],
            "calibration": self.cfg["calibration"],
            "lines": self.lines, "modeled_moves": len(self.moves),
            "supported_motion_only": self.flow_supported,
            "modeled_time_s_excluding_unknowns": self.clock,
            "target": {
                "deposition_moves": len(selected), "deposition_time_s": duration,
                "deposited_volume_mm3": sum(m.deposited_mm3 for m in selected),
                "mean_deposition_flow_mm3_s": sum(m.deposited_mm3 for m in selected)/duration if duration else 0,
                "peak_planned_flow_mm3_s": max((m.volume_per_mm*m.cruise_speed for m in selected), default=0),
                "peak_requested_flow_mm3_s": max((m.volume_per_mm*m.speed for m in selected), default=0),
                "high_flow_threshold_mm3_s": threshold,
                "time_above_threshold_s": sum(end-start for start, end in intervals),
                "longest_continuous_high_flow_s": max((end-start for start, end in intervals), default=0),
                "longest_high_flow_intervals": sorted(intervals, key=lambda p: p[1]-p[0], reverse=True)[:20],
                "roles": dict(Counter(m.role for m in selected)),
            },
            "observed_commands": dict(self.observed),
            "skipped": dict(self.skipped), "unknown_timing_barriers": dict(self.unknown),
            "temperature_commands_preserved": dict(self.temperature_commands),
            "warnings": warnings,
        }


def analyze_file(path, config=None):
    analyzer = Analyzer(config)
    with open(path, encoding="utf-8-sig", errors="strict") as stream:
        report = analyzer.read(stream)
    return analyzer, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gcode")
    parser.add_argument("--config")
    parser.add_argument("--report", required=True)
    parser.add_argument("--csv")
    parser.add_argument("--tool", type=int)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig")) if args.config else {}
    if args.tool is not None:
        config["logical_tool"] = args.tool
    # Refuse output paths that alias the input, including symlinks/hardlinks.
    source = Path(args.gcode).resolve()
    outputs = [Path(p).resolve() for p in (args.report, args.csv) if p]
    if any(p == source or (p.exists() and p.samefile(source)) for p in outputs) or len(set(outputs)) != len(outputs):
        parser.error("Input and output paths must be distinct")
    analyzer, report = analyze_file(source, config)
    Path(args.report).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["line", "logical_tool", "role", "modeled_time_s", "duration_s", "length_mm", "accel_mm_s2", "requested_speed_mm_s", "start_speed_mm_s", "cruise_speed_mm_s", "end_speed_mm_s", "deposited_mm3", "peak_planned_flow_mm3_s"])
            for m in analyzer.moves:
                writer.writerow([m.line, m.tool, m.role, m.time, m.duration, m.length, m.accel, m.speed, m.start_speed, m.cruise_speed, m.end_speed, m.deposited_mm3, m.volume_per_mm*m.cruise_speed])
    print(json.dumps(report["target"], indent=2))


if __name__ == "__main__":
    main()
else:
    try:
        import orca
    except ModuleNotFoundError as exc:
        if exc.name != "orca":
            raise
    else:
        class FlowEvaluator(orca.slicing.SlicingPipelineCapabilityBase):
            def get_name(self):
                return "Predictive Flow Evaluator"

            def get_default_config(self):
                return DEFAULTS

            def execute(self, ctx):
                if ctx.step != orca.slicing.Step.psGCodePostProcess or not ctx.gcode_path:
                    return orca.ExecutionResult.success()
                config = json.loads(self.get_config())
                _, report = analyze_file(ctx.gcode_path, config)
                # Persist in plugin-owned configuration via the official host API.
                # Never write the exported G-code or any printer/profile setting.
                config["last_report"] = report
                if not self.save_config(json.dumps(config, allow_nan=False)):
                    raise RuntimeError("Could not save the flow report to plugin configuration")
                t = report["target"]
                message = (f"Flow analysis: logical T{report['selected_logical_tool']}, "
                           f"planned peak {t['peak_planned_flow_mm3_s']:.2f} mm3/s. "
                           "See Config > last_report. G-code unchanged.")
                return orca.ExecutionResult.success(message, json.dumps(report))

        @orca.plugin
        class FlowEvaluatorPackage(orca.base):
            def register_capabilities(self):
                orca.register_capability(FlowEvaluator)
