"""3D C04 (Re=126k) calibration, verification & Dual-Xeon execution pipeline.

Fixes verified pre-calibration gaps non-destructively on a copy of the solved-steady
``1.55M``-element mesh2 model, runs time/tolerance calibration sweeps (A/B/C/D) using
tuned Dual-Xeon NUMA batch flags on E:\\comsol_scratch, normalizes cylinder traction and
integral force probes into CL/CD time series, evaluates last-N complete vortex-shedding
cycles against VALIDATION.md tolerances and subcritical Re~1.26e5 physical envelopes,
and provides a C01~C09 Dual-Socket parallel worker campaign launcher.

Physical & Coordinate Contract (COMSOL extruded-xz vs Canonical C04):
- Canonical X (streamwise) = COMSOL x in [-3.0, 6.0] m  -> inlet Ux = U0*sin(alpha)
- Canonical Y (crossflow)  = COMSOL z in [-3.0, 3.0] m  -> inlet Uy = 0[m/s]
- Canonical Z (spanwise)   = COMSOL y + 3.0 in [0, 3.0] m -> inlet Uz = U0*cos(alpha)
- Therefore, COMSOL Inlet VelocityField u0 MUST be [Ux, Uz, Uy].
- Cylinder average traction probe T_avg (N/m^2) integrates over area pi*D*L_span,
  whereas aero force coefficients normalize by frontal area D*L_span:
    CL = pi * T_tracz_avg / q_inf,   CD = pi * |T_tracx_avg| / q_inf   (q_inf = 0.5*rho0*U0^2)
  Cross-checked against integral force probes F_int (N):
    CL = F_z / (q_inf * D * L_span), CD = |F_x| / (q_inf * D * L_span).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (SRC_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hydro_disturb_bench.comsol_java.bridge import find_comsol_root, get_comsol_bin_dir
from verify_and_compare_s1b1 import (
    compute_cycle_half_amplitude,
    compute_demeaned_rms,
    evaluate_limit_cycle_stability,
    integrate_exact_window,
)

DEFAULT_CALIB_BASE = Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph")
DEFAULT_SCRATCH = Path(r"E:\comsol_scratch")
DEFAULT_INI = Path(r"E:\comsol_scratch\comsolbatch_perf.ini")
DEFAULT_CALIB_ROOT = Path(r"E:\cfd_3d\01_calibration\c04_re126k")
DEFAULT_REPORTS_DIR = Path(r"E:\cfd_3d\05_reports_viz")

# Default C04 physical reference constants (ITTC 2024 15 degC seawater, D=0.30 m, U0=0.50 m/s)
C04_D_M = 0.30
C04_L_SPAN_M = 3.0
C04_RHO0 = 1025.8944
C04_U0 = 0.50

# Canonical calibration sweep mirrors docs/VALIDATION.md (A/B/C/D)
CALIBRATION_CASES: dict[str, dict[str, str]] = {
    "A_rtol5e3": {
        "description": "Original C04 s1_trial solver tolerances (rtol=5e-3, maxstep=auto) with fixed inlet/probes",
        "rtol": "0.005",
        "maxstep_mode": "auto",
        "maxstep_val": "0.1",
    },
    "B_rtol1e3": {
        "description": "3D SST baseline tolerance (rtol=1e-3, maxstep=auto)",
        "rtol": "0.001",
        "maxstep_mode": "auto",
        "maxstep_val": "0.1",
    },
    "B2_rtol5e4_max005": {
        "description": "3D-adapted intermediate tolerance & maxstep check (rtol=5e-4, const maxstep=0.05s)",
        "rtol": "0.0005",
        "maxstep_mode": "const",
        "maxstep_val": "0.05",
    },
    "C_rtol3e4_max005": {
        "description": "3D-adapted tightened tolerance & maxstep check (rtol=3e-4, const maxstep=0.05s)",
        "rtol": "0.0003",
        "maxstep_mode": "const",
        "maxstep_val": "0.05",
    },
    "C_rtol1e4_max005": {
        "description": "Tightened reference baseline (rtol=1e-4, const maxstep=0.05s matching VALIDATION.md)",
        "rtol": "0.0001",
        "maxstep_mode": "const",
        "maxstep_val": "0.05",
    },
    "D_rtol1e4_max0025": {
        "description": "Halved internal step verification (rtol=1e-4, const maxstep=0.025s)",
        "rtol": "0.0001",
        "maxstep_mode": "const",
        "maxstep_val": "0.025",
    },
}

# Literature / empirical physical envelopes for 3D smooth cylinder at subcritical Re ~ 1.26e5 - 1.30e5
# Sources: Lysenko (2025) Re=130k LES, Norberg (2003), Cantwell & Coles (1983), Achenbach (1968).
C04_LITERATURE_ENVELOPE = {
    "ReD_nominal": 126167.0,
    "St_range": [0.175, 0.225],
    "St_typical_les_3d": [0.19, 0.21],
    "Cd_mean_range": [0.90, 1.50],
    "Cd_typical_3d_subcritical": [1.00, 1.35],
    "Cl_rms_min_shedding": 0.05,
    "Cl_rms_typical": [0.10, 0.70],
    "note": (
        "SST-SAS 3D URANS/SAS typically matches St~0.19-0.21 well and can slightly overpredict "
        "Cd_mean/Cl_rms relative to wall-resolved 3D LES due to spanwise coherence; falling inside "
        "these broad envelopes confirms physically active subcritical shedding (not stalled/symmetric), "
        "while strict temporal convergence relies on pairwise B->C->D <=1% calibration gates."
    ),
}

PREPARE_JAVA_SOURCE = r"""import com.comsol.model.*;
import com.comsol.model.util.*;
import java.util.*;

public class PrepareC04Calibration {
    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '\"': sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < ' ' || c > 126) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append("\"").toString();
    }

    private static String arrJson(String[] a) {
        if (a == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < a.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(quote(a[i]));
        }
        return sb.append("]").toString();
    }

    private static String intArrJson(int[] a) {
        if (a == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < a.length; i++) {
            if (i > 0) sb.append(", ");
            sb.append(a[i]);
        }
        return sb.append("]").toString();
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            throw new IllegalArgumentException("Usage: PrepareC04Calibration <src_mph> <dst_mph>");
        }
        String srcPath = args[0];
        String dstPath = args[1];
        if (srcPath.equalsIgnoreCase(dstPath)) {
            throw new IllegalArgumentException("Refusing in-place modification: source equals destination");
        }

        Model model = ModelUtil.loadCopy("CalibPrep", srcPath);
        ModelNode comp = model.component("comp1");
        ModelParam param = model.param();

        // 1. Validate physics parameters exist
        double dVal = param.evaluate("D");
        double u0Val = param.evaluate("U0");
        double rho0Val = param.evaluate("rho0");
        double lSpanVal = param.evaluate("L_span");
        double reVal = param.evaluate("ReD");

        // 2. Fix Inlet boundary: switch from NormalInflowVelocity -> VelocityField with [Ux, Uz, Uy]
        var spf = comp.physics("spf");
        var wallbc1 = spf.feature("wallbc1");
        int[] cylEntities = wallbc1.selection().entities().clone();
        Arrays.sort(cylEntities);
        if (cylEntities.length == 0) {
            throw new IllegalStateException("wallbc1 cylinder selection is empty");
        }

        var inl1 = spf.feature("inl1");
        inl1.set("ComponentWise", "VelocityFieldComponentWise");
        inl1.set("u0", new String[]{"Ux", "Uz", "Uy"});
        String inlMode = inl1.getString("ComponentWise");
        String[] inlVec = inl1.getStringArray("u0");
        int[] inlEnt = inl1.selection().entities();
        if (!"VelocityFieldComponentWise".equals(inlMode) || !Arrays.equals(inlVec, new String[]{"Ux", "Uz", "Uy"})) {
            throw new IllegalStateException("Inlet VelocityFieldComponentWise readback mismatch: " + inlMode + " " + Arrays.toString(inlVec));
        }

        // 3. Configure lift & drag boundary probes (average traction + integral force verification)
        String[][] probeSpecs = new String[][]{
            {"bnd1", "spf.T_tracz", "average", "tbl2", "Cylinder Lift Traction Avg (z_comsol = Y_canon)"},
            {"bnd_drag", "spf.T_tracx", "average", "tbl_drag", "Cylinder Drag Traction Avg (x_comsol = X_canon)"},
            {"bnd_lift_int", "spf.T_tracz", "integral", "tbl_lift_int", "Cylinder Lift Force Integral (N)"},
            {"bnd_drag_int", "spf.T_tracx", "integral", "tbl_drag_int", "Cylinder Drag Force Integral (N)"}
        };

        List<String> existingTables = Arrays.asList(model.result().table().tags());
        for (String[] spec : probeSpecs) {
            if (!Arrays.asList(model.result().table().tags()).contains(spec[3])) {
                model.result().table().create(spec[3], "Table");
            } else {
                try {
                    model.result().table(spec[3]).clearTableData();
                } catch (Throwable ignored) {}
            }
        }

        StringBuilder probesJson = new StringBuilder("[");
        for (int i = 0; i < probeSpecs.length; i++) {
            String[] spec = probeSpecs[i];
            String tag = spec[0];
            var pr = Arrays.asList(comp.probe().tags()).contains(tag)
                ? comp.probe(tag)
                : comp.probe().create(tag, "Boundary");
            pr.label(spec[4]);
            pr.active(true);
            pr.selection().set(cylEntities);
            pr.set("expr", spec[1]);
            pr.set("type", spec[2]);
            pr.set("frame", "spatial");
            pr.set("method", "integration");
            pr.set("intsurface", true);
            pr.set("table", spec[3]);

            boolean hasNumerical = false;
            for (String nTag : model.result().numerical().tags()) {
                try {
                    if (tag.equals(model.result().numerical(nTag).getString("probetag"))) {
                        hasNumerical = true;
                        break;
                    }
                } catch (Throwable ignored) {}
            }
            if (!hasNumerical) {
                pr.genResult(null);
            }
            String linkedNumTag = null;
            String linkedTable = null;
            for (String nTag : model.result().numerical().tags()) {
                try {
                    var nf = model.result().numerical(nTag);
                    if (tag.equals(nf.getString("probetag"))) {
                        linkedNumTag = nTag;
                        if (!spec[3].equals(nf.getString("table"))) {
                            nf.set("table", spec[3]);
                        }
                        linkedTable = nf.getString("table");
                        break;
                    }
                } catch (Throwable ignored) {}
            }
            if (linkedNumTag == null) {
                throw new IllegalStateException("Probe " + tag + " missing result EvalPoint node after genResult(null)");
            }

            if (i > 0) probesJson.append(", ");
            probesJson.append("{")
                .append("\"tag\":").append(quote(tag))
                .append(",\"expr\":").append(quote(pr.getString("expr")))
                .append(",\"type\":").append(quote(pr.getString("type")))
                .append(",\"table\":").append(quote(pr.getString("table")))
                .append(",\"numerical\":").append(quote(linkedNumTag))
                .append(",\"numerical_table\":").append(quote(linkedTable))
                .append(",\"unit\":").append(quote(pr.getString("unit")))
                .append(",\"entities\":").append(intArrJson(pr.selection().entities()))
                .append("}");
        }
        probesJson.append("]");

        // 4. Attach all probes at tsteps on std2/time & sol3/t1 (and verify all 4 probe tables exist)
        try {
            StudyFeature sf = model.study("std2").feature("time");
            sf.set("probesel", "all");
            sf.set("probefreq", "tsteps");
        } catch (Throwable ignored) {}
        SolverFeature t1 = model.sol("sol3").feature("t1");
        t1.set("probesel", "all");
        t1.set("probefreq", "tsteps");
        try {
            t1.set("probes", new String[]{"bnd1", "bnd_drag", "bnd_lift_int", "bnd_drag_int"});
        } catch (Throwable ignored) {}
        String[] solProbes = t1.getStringArray("probes");
        String probeFreq = t1.getString("probefreq");
        String probeSel = t1.getString("probesel");

        // 5. Verify mesh2 and sol1 stationary state remain valid
        boolean mesh2Complete = comp.mesh("mesh2").isComplete();
        int mesh2Elems = comp.mesh("mesh2").getNumElem();
        boolean sol1Empty = model.sol("sol1").isEmpty();
        int sol1Dofs = 0;
        try {
            double[] u1 = model.sol("sol1").getU(1);
            if (u1 != null) sol1Dofs = u1.length;
        } catch (Throwable ignored) {}
        if (!mesh2Complete || sol1Empty || sol1Dofs == 0) {
            throw new IllegalStateException("Prerequisite mesh2 or sol1 steady state invalid: mesh2Complete="
                + mesh2Complete + ", sol1Empty=" + sol1Empty + ", sol1Dofs=" + sol1Dofs);
        }

        // Clear any leftover sol3 transient data so base model is a clean S0->S1 launchpad
        try {
            model.sol("sol3").clearSolutionData();
        } catch (Throwable ignored) {}

        model.save(dstPath);

        String json = "{\n"
            + "  \"status\": \"SUCCESS\",\n"
            + "  \"operation\": \"prepare_c04_calibration_base\",\n"
            + "  \"source_model\": " + quote(srcPath) + ",\n"
            + "  \"output_model\": " + quote(dstPath) + ",\n"
            + "  \"parameters\": {\"D\": " + dVal + ", \"U0\": " + u0Val + ", \"rho0\": " + rho0Val
            + ", \"L_span\": " + lSpanVal + ", \"ReD\": " + reVal + "},\n"
            + "  \"inlet_readback\": {\"ComponentWise\": " + quote(inlMode)
            + ", \"u0\": " + arrJson(inlVec) + ", \"entities\": " + intArrJson(inlEnt) + "},\n"
            + "  \"probes_readback\": " + probesJson + ",\n"
            + "  \"solver_probe_attach\": {\"probefreq\": " + quote(probeFreq)
            + ", \"probesel\": " + quote(probeSel) + ", \"probes\": " + arrJson(solProbes) + "},\n"
            + "  \"prerequisites\": {\"mesh2_complete\": " + mesh2Complete + ", \"mesh2_elements\": " + mesh2Elems
            + ", \"sol1_empty\": " + sol1Empty + ", \"sol1_dofs\": " + sol1Dofs + "}\n"
            + "}\n";

        System.out.println("<<<JSON_START>>>");
        System.out.println(json);
        System.out.println("<<<JSON_END>>>");
    }
}
"""

RUN_JAVA_SOURCE = r"""import com.comsol.model.*;
import com.comsol.model.util.*;
import java.util.*;

public class RunC04Calibration {
    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '\"': sb.append("\\\""); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < ' ' || c > 126) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append("\"").toString();
    }

    private static void emitTableText(Model model, String tableTag, String fileName) {
        TableFeature tf = model.result().table(tableTag);
        String[] headers = tf.getColumnHeaders();
        double[][] rows = tf.getReal();
        System.out.println("<<<PROBE_TABLE_START:" + fileName + ">>>");
        System.out.println("% Table: " + tableTag);
        if (headers != null && headers.length > 0) {
            System.out.println("% " + String.join(" | ", headers));
        }
        if (rows != null) {
            for (double[] row : rows) {
                StringBuilder line = new StringBuilder();
                for (int c = 0; c < row.length; c++) {
                    if (c > 0) line.append(" ");
                    line.append(String.format(Locale.US, "%.17g", row[c]));
                }
                System.out.println(line.toString());
            }
        }
        System.out.println("<<<PROBE_TABLE_END:" + fileName + ">>>");
    }

    private static SolverFeature findTimeFeature(SolverSequence sol) {
        for (String ftag : sol.feature().tags()) {
            SolverFeature sf = sol.feature(ftag);
            try {
                if ("Time".equalsIgnoreCase(sf.getType())) {
                    return sf;
                }
            } catch (Throwable ignored) {}
        }
        return sol.feature("t1");
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 12) {
            throw new IllegalArgumentException(
                "Usage: RunC04Calibration <base_mph> <save_mode:save|nosave> <out_mph> "
                + "<tlist> <rtol> <maxstep_mode:auto|const> <maxstep_val> <init_step> "
                + "<consistent:bweuler|off> <run_std1:true|false> <u0_expr> <alpha_expr>"
            );
        }
        String baseMph = args[0];
        boolean doSaveMph = "save".equalsIgnoreCase(args[1]);
        String outMph = args[2];
        String tlist = args[3];
        String rtol = args[4];
        String maxstepMode = args[5];
        String maxstepVal = args[6];
        String initStep = args[7];
        String consistentMode = args[8];
        boolean runStd1 = "true".equalsIgnoreCase(args[9]);
        String u0Expr = args[10];
        String alphaExpr = args[11];

        Model model = ModelUtil.loadCopy("CalibRun", baseMph);
        ModelParam param = model.param();

        if (!u0Expr.isEmpty() && !"-".equals(u0Expr)) {
            param.set("U0", u0Expr);
        }
        if (!alphaExpr.isEmpty() && !"-".equals(alphaExpr)) {
            param.set("alpha", alphaExpr);
        }
        double dVal = param.evaluate("D");
        double u0Val = param.evaluate("U0");
        double alphaRad = param.evaluate("alpha");
        double rho0Val = param.evaluate("rho0");
        double lSpanVal = param.evaluate("L_span");
        double reVal = param.evaluate("ReD");

        for (String ttag : new String[]{"tbl2", "tbl_drag", "tbl_lift_int", "tbl_drag_int"}) {
            try {
                if (Arrays.asList(model.result().table().tags()).contains(ttag)) {
                    model.result().table(ttag).clearTableData();
                }
            } catch (Throwable ignored) {}
        }

        long tStd1Ns = 0L;
        if (runStd1) {
            try { model.sol("sol1").clearSolutionData(); } catch (Throwable ignored) {}
            long s0 = System.nanoTime();
            model.study("std1").run();
            tStd1Ns = System.nanoTime() - s0;
        }

        Study std2 = model.study("std2");
        try {
            StudyFeature stTime = std2.feature("time");
            stTime.set("tlist", tlist);
            stTime.set("usertol", "on");
            stTime.set("rtol", rtol);
            stTime.set("probesel", "all");
            stTime.set("probefreq", "tsteps");
        } catch (Throwable ignored) {}

        SolverSequence sol3 = model.sol("sol3");
        SolverFeature t1 = findTimeFeature(sol3);
        t1.set("tlist", tlist);
        t1.set("rtol", rtol);
        if ("const".equalsIgnoreCase(maxstepMode)) {
            t1.set("maxstepconstraintbdf", "const");
            t1.set("maxstepbdf", maxstepVal);
        } else {
            t1.set("maxstepconstraintbdf", "auto");
        }
        if (!initStep.isEmpty() && !"-".equals(initStep) && !"auto".equalsIgnoreCase(initStep)) {
            t1.set("initialstepbdfactive", true);
            t1.set("initialstepbdf", initStep);
        } else {
            t1.set("initialstepbdfactive", false);
        }
        t1.set("consistent", consistentMode);
        t1.set("probesel", "all");
        t1.set("probefreq", "tsteps");
        try {
            t1.set("probes", new String[]{"bnd1", "bnd_drag", "bnd_lift_int", "bnd_drag_int"});
        } catch (Throwable ignored) {}

        long tStd2Start = System.nanoTime();
        std2.runNoGen();
        long tStd2Ns = System.nanoTime() - tStd2Start;

        sol3 = model.sol("sol3");
        t1 = findTimeFeature(sol3);
        double[] pvals = sol3.getPVals();
        String t1Msg = t1.getString("message");
        System.out.println("<<<T1_MSG_START>>>");
        System.out.println(t1Msg == null ? "" : t1Msg);
        System.out.println("<<<T1_MSG_END>>>");

        String[][] tblFiles = new String[][]{
            {"tbl2", "probe_lift_avg.txt"},
            {"tbl_drag", "probe_drag_avg.txt"},
            {"tbl_lift_int", "probe_lift_int.txt"},
            {"tbl_drag_int", "probe_drag_int.txt"}
        };
        Map<String, Integer> rowCounts = new LinkedHashMap<>();
        for (String[] pair : tblFiles) {
            if (Arrays.asList(model.result().table().tags()).contains(pair[0])) {
                emitTableText(model, pair[0], pair[1]);
                rowCounts.put(pair[0], model.result().table(pair[0]).getNRows());
            } else {
                rowCounts.put(pair[0], -1);
            }
        }

        if (doSaveMph) {
            model.save(outMph);
        }

        String json = "{\n"
            + "  \"status\": \"SUCCESS\",\n"
            + "  \"operation\": \"run_c04_calibration\",\n"
            + "  \"saved_mph\": " + (doSaveMph ? quote(outMph) : "null") + ",\n"
            + "  \"std1_solve_s\": " + String.format(Locale.US, "%.3f", tStd1Ns / 1e9) + ",\n"
            + "  \"std2_solve_s\": " + String.format(Locale.US, "%.3f", tStd2Ns / 1e9) + ",\n"
            + "  \"pvals_count\": " + (pvals == null ? 0 : pvals.length) + ",\n"
            + "  \"t_min\": " + ((pvals != null && pvals.length > 0) ? pvals[0] : -1.0) + ",\n"
            + "  \"t_max\": " + ((pvals != null && pvals.length > 0) ? pvals[pvals.length - 1] : -1.0) + ",\n"
            + "  \"parameters\": {\"D\": " + dVal + ", \"U0\": " + u0Val + ", \"alpha_rad\": " + alphaRad
            + ", \"rho0\": " + rho0Val + ", \"L_span\": " + lSpanVal + ", \"ReD\": " + reVal + "},\n"
            + "  \"solver_readback\": {\"rtol\": " + quote(t1.getString("rtol"))
            + ", \"maxstepconstraintbdf\": " + quote(t1.getString("maxstepconstraintbdf"))
            + ", \"maxstepbdf\": " + t1.getDouble("maxstepbdf")
            + ", \"initialstepbdfactive\": " + t1.getBoolean("initialstepbdfactive")
            + ", \"initialstepbdf\": " + t1.getDouble("initialstepbdf")
            + ", \"consistent\": " + quote(t1.getString("consistent")) + "},\n"
            + "  \"table_rows\": {\"tbl2\": " + rowCounts.get("tbl2")
            + ", \"tbl_drag\": " + rowCounts.get("tbl_drag")
            + ", \"tbl_lift_int\": " + rowCounts.get("tbl_lift_int")
            + ", \"tbl_drag_int\": " + rowCounts.get("tbl_drag_int") + "}\n"
            + "}\n";
        System.out.println("<<<JSON_START>>>");
        System.out.println(json);
        System.out.println("<<<JSON_END>>>");
    }
}
"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest().upper()


def resolve_default_source_s0_mesh2() -> Path:
    candidates = [
        Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_re126k_s0_mesh2.mph"),
        Path(r"E:\cylinder_3d\c04_re126k_s0_mesh2.mph"),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def ensure_java_compiled(
    comsol_root: Path, work_dir: Path, class_name: str, source_text: str
) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    java_file = work_dir / f"{class_name}.java"
    class_file = work_dir / f"{class_name}.class"
    if not java_file.is_file() or java_file.read_text(encoding="utf-8") != source_text:
        java_file.write_text(source_text, encoding="utf-8")
        if class_file.is_file():
            class_file.unlink()

    if not class_file.is_file() or class_file.stat().st_mtime < java_file.stat().st_mtime:
        comsolcompile = get_comsol_bin_dir(comsol_root) / "comsolcompile.exe"
        proc = subprocess.run(
            [str(comsolcompile), str(java_file)],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0 or not class_file.is_file():
            javac = comsol_root / "java" / "win64" / "jre" / "bin" / "javac.exe"
            proc2 = subprocess.run(
                [
                    str(javac),
                    "-encoding",
                    "UTF-8",
                    "-proc:none",
                    "-cp",
                    str(comsol_root / "plugins" / "*"),
                    str(java_file),
                ],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if proc2.returncode != 0 or not class_file.is_file():
                raise RuntimeError(
                    f"Failed compiling {class_name}.java:\n{proc.stdout}\n{proc2.stderr}"
                )
    return class_file


def extract_json_block(text: str) -> dict[str, Any]:
    m = re.search(r"<<<JSON_START>>>\s*(\{.*?\})\s*<<<JSON_END>>>", text, flags=re.DOTALL)
    if not m:
        raise ValueError("Missing <<<JSON_START>>>...<<<JSON_END>>> payload in COMSOL output")
    return json.loads(m.group(1))


def _priority_creation_flags(priority: str) -> int:
    if sys.platform != "win32":
        return 0
    key = (priority or "normal").lower()
    if key == "abovenormal":
        return int(getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", 0x00008000))
    if key == "high":
        return int(getattr(subprocess, "HIGH_PRIORITY_CLASS", 0x00000080))
    return 0


def parse_worker_specs(
    *,
    workers: int,
    np_per_worker: int,
    default_mpmode: str = "throughput",
    worker_specs_str: str | None = None,
) -> list[dict[str, Any]]:
    """Parse per-slot worker hardware bindings (`np:numasets:numafirst[:mpmode]`)."""
    if worker_specs_str and worker_specs_str.strip():
        specs: list[dict[str, Any]] = []
        for idx, token in enumerate(worker_specs_str.split(",")):
            raw = token.strip()
            if not raw:
                continue
            parts = [p.strip() for p in raw.split(":")]
            if len(parts) not in (3, 4):
                raise ValueError(
                    f"Invalid worker spec #{idx} '{raw}': expected np:numasets:numafirst[:mpmode]"
                )
            specs.append(
                {
                    "np": int(parts[0]),
                    "numasets": int(parts[1]),
                    "numafirst": int(parts[2]),
                    "mpmode": parts[3] if len(parts) == 4 and parts[3] else default_mpmode,
                }
            )
        if not specs:
            raise ValueError("worker_specs_str yielded no valid worker specs")
        return specs

    if workers == 1:
        return [
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 0, "mpmode": default_mpmode},
        ]
    if workers == 2:
        return [
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 0, "mpmode": default_mpmode},
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 1, "mpmode": default_mpmode},
        ]
    if workers == 3:
        return [
            {"np": 32, "numasets": 1, "numafirst": 0, "mpmode": default_mpmode},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": default_mpmode},
            {"np": 16, "numasets": 1, "numafirst": 1, "mpmode": default_mpmode},
        ]
    if workers == 4:
        return [
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 0, "mpmode": default_mpmode},
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 0, "mpmode": default_mpmode},
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 1, "mpmode": default_mpmode},
            {"np": int(np_per_worker), "numasets": 1, "numafirst": 1, "mpmode": default_mpmode},
        ]
    raise ValueError(f"Unsupported parallel workers count: {workers}")


def resolve_save_mph_cases(
    selected_cases: list[str],
    *,
    save_mph: bool,
    save_mph_cases: str | None = None,
) -> set[str]:
    """Determine which case IDs should write full `.mph` snapshots to disk."""
    if save_mph_cases is not None and save_mph_cases.strip():
        raw = save_mph_cases.strip()
        if raw.lower() in {"none", "off", "-"}:
            return set()
        if raw.lower() == "all":
            return set(selected_cases)
        chosen = {c.strip() for c in raw.split(",") if c.strip()}
        unknown = chosen - set(selected_cases)
        if unknown:
            raise ValueError(
                f"--save-mph-cases contains cases not in selected run set {selected_cases}: {sorted(unknown)}"
            )
        return chosen
    return set(selected_cases) if save_mph else set()


def invoke_comsol_batch(
    *,
    comsolbatch: Path,
    class_file: Path,
    class_args: list[str],
    ini_file: Path,
    scratch_root: Path,
    worker_tag: str,
    np_val: int,
    numasets: int,
    numafirst: int,
    log_path: Path,
    mpmode: str = "turnaround",
    alloc: str = "scalable",
    priority: str = "abovenormal",
) -> tuple[subprocess.CompletedProcess[str], float]:
    w_tmp = scratch_root / "tmp" / worker_tag
    w_rec = scratch_root / "recoveries" / worker_tag
    w_tmp.mkdir(parents=True, exist_ok=True)
    w_rec.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TEMP"] = str(w_tmp)
    env["TMP"] = str(w_tmp)

    cmd = [
        str(comsolbatch),
        "-nosave",
        "-comsolinifile",
        str(ini_file),
        "-tmpdir",
        str(w_tmp),
        "-recoverydir",
        str(w_rec),
        "-autosave",
        "off",
        "-np",
        str(np_val),
        "-numasets",
        str(numasets),
        "-numafirst",
        str(numafirst),
        "-mpmode",
        str(mpmode),
        "-alloc",
        str(alloc),
        "-blas",
        "mkl",
        "-inputfile",
        str(class_file),
        *class_args,
    ]
    console_enc = "mbcs" if sys.platform == "win32" else "utf-8"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(class_file.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding=console_enc,
        errors="replace",
        bufsize=1,
        creationflags=_priority_creation_flags(priority),
    )

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)

    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_err.start()
    with log_path.open("w", encoding="utf-8") as lf:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            lf.write(line)
            lf.flush()
    t_err.join(timeout=5.0)
    rc = proc.wait()
    wall_s = time.perf_counter() - t0

    stderr_text = "".join(stderr_chunks)
    if stderr_text:
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write("\n--- STDERR ---\n" + stderr_text)

    status_file = class_file.with_name(f"{class_file.name}.status")
    if status_file.is_file():
        status_file.unlink(missing_ok=True)
    completed = subprocess.CompletedProcess(
        args=cmd,
        returncode=rc,
        stdout="".join(stdout_chunks),
        stderr=stderr_text,
    )
    return completed, wall_s


# ---------------------------------------------------------------------------
# Probe Normalization & Scientific Cycle-by-Cycle Evaluation
# ---------------------------------------------------------------------------


def load_numeric_probe_table(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a COMSOL table text export (comment lines '%' + 2+ numeric columns)."""
    rows: list[list[float]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("%") or s.startswith("#"):
                continue
            parts = s.replace(",", " ").split()
            if len(parts) < 2:
                continue
            rows.append([float(parts[0]), float(parts[-1])])
    if not rows:
        raise ValueError(f"Probe table contains no data rows: {path}")
    arr = np.asarray(rows, dtype=np.float64)
    t = arr[:, 0]
    v = arr[:, 1]
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(v)):
        raise ValueError(f"Probe table contains non-finite values: {path}")
    # Handle duplicate boundary timestamps (rare restart/output coincidence) while enforcing monotonic time
    _, uniq_idx = np.unique(t, return_index=True)
    t = t[uniq_idx]
    v = v[uniq_idx]
    if len(t) < 2 or np.any(np.diff(t) <= 0):
        raise ValueError(f"Probe table timestamps are not strictly increasing: {path}")
    return t, v


def _try_load_probe_table(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    if not path.is_file():
        return None
    try:
        return load_numeric_probe_table(path)
    except ValueError:
        return None


def normalize_c04_probe_tables(
    case_dir: Path,
    *,
    rho0: float = C04_RHO0,
    u0: float = C04_U0,
    d_m: float = C04_D_M,
    l_span_m: float = C04_L_SPAN_M,
    drag_sign: str = "abs_mean",
) -> dict[str, Any]:
    """Convert raw COMSOL cylinder integral force / average traction tables into CL(t) & CD(t).

    Prefers exact boundary integral forces (``probe_lift_int.txt``, ``probe_drag_int.txt``):
      - ``CL(t) = F_z / (q_inf * D * L_span)``
      - ``CD(t) = (+/-) F_x / (q_inf * D * L_span)``
    avoiding polygonal circumference chord bias (~0.29% on mesh2). Falls back cleanly to
    average traction probes (``pi * T_avg / q_inf``) when integral tables are absent, and
    cross-checks both whenever all four probe tables are populated.
    """
    q_inf = 0.5 * rho0 * (u0 ** 2)
    area_frontal = d_m * l_span_m
    area_cyl = math.pi * d_m * l_span_m

    lift_int = _try_load_probe_table(case_dir / "probe_lift_int.txt")
    drag_int = _try_load_probe_table(case_dir / "probe_drag_int.txt")
    lift_avg = _try_load_probe_table(case_dir / "probe_lift_avg.txt")
    drag_avg = _try_load_probe_table(case_dir / "probe_drag_avg.txt")

    consistency: dict[str, Any] = {
        "q_inf_pa": q_inf,
        "area_frontal_m2": area_frontal,
        "area_cylinder_m2": area_cyl,
    }

    if lift_int is not None and drag_int is not None:
        t_lift, raw_lift = lift_int
        t_drag, raw_drag = drag_int
        cl = raw_lift / (q_inf * area_frontal)
        raw_cd_signed = raw_drag / (q_inf * area_frontal)
        sign_factor = -1.0 if (drag_sign == "abs_mean" and float(np.mean(raw_cd_signed)) < 0.0) else 1.0
        cd = sign_factor * raw_cd_signed
        source_kind = "integral_force"
        raw_cols = ("Fz_int_N", "Fx_int_N")
        if lift_avg is not None and drag_avg is not None:
            t_la, tz_avg = lift_avg
            t_da, tx_avg = drag_avg
            cl_from_avg = (math.pi * tz_avg) / q_inf
            cd_from_avg = sign_factor * (math.pi * tx_avg) / q_inf
            cl_cmp = cl_from_avg if (len(t_la) == len(t_lift) and np.allclose(t_la, t_lift, atol=1e-12)) else np.interp(t_lift, t_la, cl_from_avg)
            cd_cmp = cd_from_avg if (len(t_da) == len(t_drag) and np.allclose(t_da, t_drag, atol=1e-12)) else np.interp(t_drag, t_da, cd_from_avg)
            consistency["lift_avg_vs_int_max_abs_diff"] = float(np.max(np.abs(cl_cmp - cl)))
            consistency["drag_avg_vs_int_max_abs_diff"] = float(np.max(np.abs(cd_cmp - cd)))
            cd_mean_abs = float(np.mean(np.abs(cd)))
            if cd_mean_abs > 1e-12:
                consistency["drag_avg_vs_int_rel_pct"] = float((np.mean(cd_cmp) - np.mean(cd)) / np.mean(cd) * 100.0)
    else:
        if lift_avg is None:
            load_numeric_probe_table(case_dir / "probe_lift_avg.txt")
        if drag_avg is None:
            load_numeric_probe_table(case_dir / "probe_drag_avg.txt")
        assert lift_avg is not None and drag_avg is not None
        t_lift, raw_lift = lift_avg
        t_drag, raw_drag = drag_avg
        cl = (math.pi * raw_lift) / q_inf
        raw_cd_signed = (math.pi * raw_drag) / q_inf
        sign_factor = -1.0 if (drag_sign == "abs_mean" and float(np.mean(raw_cd_signed)) < 0.0) else 1.0
        cd = sign_factor * raw_cd_signed
        source_kind = "average_traction"
        raw_cols = ("T_tracz_avg_Pa", "T_tracx_avg_Pa")

    consistency["normalization_source"] = source_kind
    consistency["drag_sign_multiplier"] = sign_factor

    # Write standardized COMSOL-style 1D series + consolidated CSV
    lift_txt = case_dir / "lift_coeff.txt"
    drag_txt = case_dir / "drag_coeff.txt"
    lift_lines = ["% Time (s) CL (1)"] + [f"{ti:.17g} {ci:.17g}" for ti, ci in zip(t_lift, cl)]
    drag_lines = ["% Time (s) CD (1)"] + [f"{ti:.17g} {ci:.17g}" for ti, ci in zip(t_drag, cd)]
    lift_txt.write_text("\n".join(lift_lines) + "\n", encoding="utf-8")
    drag_txt.write_text("\n".join(drag_lines) + "\n", encoding="utf-8")

    # Align drag columns onto lift timestamps when exporting consolidated CSV
    if len(t_drag) == len(t_lift) and np.allclose(t_drag, t_lift, atol=1e-12):
        drag_raw_csv = raw_drag
        cd_csv = cd
        consistency["time_grid_aligned"] = True
    else:
        drag_raw_csv = np.interp(t_lift, t_drag, raw_drag)
        cd_csv = np.interp(t_lift, t_drag, cd)
        consistency["time_grid_aligned"] = False

    csv_path = case_dir / "force_coefficients.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", raw_cols[0], "CL", raw_cols[1], "CD"])
        for i in range(len(t_lift)):
            w.writerow(
                [f"{t_lift[i]:.10g}", f"{raw_lift[i]:.10g}", f"{cl[i]:.10g}", f"{drag_raw_csv[i]:.10g}", f"{cd_csv[i]:.10g}"]
            )

    return {
        "t_lift": t_lift,
        "cl": cl,
        "t_drag": t_drag,
        "cd": cd,
        "consistency": consistency,
        "lift_txt": str(lift_txt),
        "drag_txt": str(drag_txt),
        "csv_path": str(csv_path),
    }


def parse_comsol_bdf_step_log(log_path: Path, late_t_min: float = 20.0) -> dict[str, Any]:
    if not log_path.is_file():
        return {"step_count": None, "late_dt_mean": None, "late_dt_range": []}
    content = log_path.read_text(encoding="utf-8", errors="replace")
    steps: list[tuple[float, float]] = []
    for line in content.splitlines():
        m = re.match(r"^\s*\d+\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+\d+", line)
        if m:
            steps.append((float(m.group(1)), float(m.group(2))))
    if not steps:
        return {"step_count": None, "late_dt_mean": None, "late_dt_range": []}
    late = [dt for t, dt in steps if t >= late_t_min]
    return {
        "step_count": len(steps),
        "late_dt_mean": float(np.mean(late)) if late else None,
        "late_dt_range": [float(min(late)), float(max(late))] if late else [],
    }


def evaluate_c04_series(
    name: str,
    t_cl: np.ndarray,
    cl: np.ndarray,
    t_cd: np.ndarray,
    cd: np.ndarray,
    *,
    d_m: float = C04_D_M,
    u0: float = C04_U0,
    min_shedding_t: float = 15.0,
    min_cycles: int = 5,
    step_info: dict[str, Any] | None = None,
    solve_wall_s: float | None = None,
) -> dict[str, Any]:
    """Compute last-N complete vortex-shedding cycle statistics and literature checks for C04."""
    # Search upward zero crossings after startup transient min_shedding_t (fallback to full trace if short)
    if np.max(t_cl) > min_shedding_t + 5.0:
        mask = t_cl >= min_shedding_t
    else:
        mask = np.ones_like(t_cl, dtype=bool)

    t_s = t_cl[mask]
    cl_s = cl[mask]

    zc: list[float] = []
    for i in range(len(t_s) - 1):
        if cl_s[i] <= 0.0 < cl_s[i + 1]:
            tz = t_s[i] - cl_s[i] * (t_s[i + 1] - t_s[i]) / (cl_s[i + 1] - cl_s[i])
            zc.append(float(tz))

    if len(zc) < min_cycles + 1:
        # Compute preliminary global stats over the second half even when <5 cycles are reached
        half_idx = max(1, len(t_cl) // 2)
        t0_p, t1_p = float(t_cl[half_idx]), float(t_cl[-1])
        _, cd_prelim = integrate_exact_window(t_cd, cd, t0_p, t1_p) if t1_p > t0_p else (0.0, float(np.mean(cd)))
        _, cl_rms_prelim = compute_demeaned_rms(t_cl, cl, t0_p, t1_p) if t1_p > t0_p else (0.0, float(np.std(cl)))
        return {
            "name": name,
            "status": "INSUFFICIENT_CYCLES",
            "is_stable": False,
            "num_cycles_found": max(0, len(zc) - 1),
            "required_cycles": min_cycles,
            "time_range_s": [float(t_cl[0]), float(t_cl[-1])],
            "preliminary_second_half": {"Cd_mean": cd_prelim, "Cl_rms": cl_rms_prelim},
            "step_info": step_info or {},
            "solve_wall_clock_sec": solve_wall_s,
            "message": f"Only {max(0, len(zc)-1)} complete shedding cycles found after t={min_shedding_t}s; need {min_cycles}.",
        }

    sel_zc = zc[-(min_cycles + 1) :]
    t_start, t_end = float(sel_zc[0]), float(sel_zc[-1])
    T_zc = np.diff(sel_zc)
    St_zc = (1.0 / T_zc) * d_m / u0

    cycle_amps: list[float] = []
    cycle_cds: list[float] = []
    for k in range(min_cycles):
        ts, te = float(sel_zc[k]), float(sel_zc[k + 1])
        cycle_amps.append(compute_cycle_half_amplitude(t_cl, cl, ts, te))
        _, c_cd = integrate_exact_window(t_cd, cd, ts, te)
        cycle_cds.append(c_cd)

    is_stable, drifts, stab_msg = evaluate_limit_cycle_stability(
        periods=T_zc, amplitudes=cycle_amps, cds=cycle_cds
    )
    _, cd_mean = integrate_exact_window(t_cd, cd, t_start, t_end)
    _, cd_rms = compute_demeaned_rms(t_cd, cd, t_start, t_end)
    cl_mean, cl_rms = compute_demeaned_rms(t_cl, cl, t_start, t_end)
    cl_amp_mean = float(np.mean(cycle_amps))
    st_mean = float(np.mean(St_zc))

    env = C04_LITERATURE_ENVELOPE
    env_checks = {
        "St_in_literature_envelope": env["St_range"][0] <= st_mean <= env["St_range"][1],
        "Cd_in_literature_envelope": env["Cd_mean_range"][0] <= cd_mean <= env["Cd_mean_range"][1],
        "active_shedding_Cl_rms": cl_rms >= env["Cl_rms_min_shedding"],
        "periodic_mean_Cl_symmetry": abs(cl_mean) <= max(0.05, 0.15 * cl_amp_mean),
    }

    return {
        "name": name,
        "status": "STABLE" if is_stable else "UNSTABLE",
        "is_stable": is_stable,
        "stability_message": stab_msg,
        "window_s": [t_start, t_end],
        "num_cycles": min_cycles,
        "mean_period_s": float(np.mean(T_zc)),
        "St_zc_mean": st_mean,
        "St_zc_range": [float(np.min(St_zc)), float(np.max(St_zc))],
        "Cd_mean": float(cd_mean),
        "Cd_rms": float(cd_rms),
        "Cl_mean": float(cl_mean),
        "Cl_amp_mean": cl_amp_mean,
        "Cl_demeaned_rms": float(cl_rms),
        "stability_drifts": drifts,
        "envelope_checks": env_checks,
        "all_envelope_checks_pass": all(env_checks.values()),
        "step_info": step_info or {},
        "solve_wall_clock_sec": solve_wall_s,
    }


def compare_c04_pair(
    res_a: dict[str, Any],
    res_b: dict[str, Any],
    *,
    tol_pct: float = 1.0,
) -> dict[str, Any]:
    if not res_a.get("is_stable") or not res_b.get("is_stable"):
        return {
            "pair": f"{res_a['name']} -> {res_b['name']}",
            "can_compare": False,
            "conclusion": "UNSTABLE_OR_INCOMPLETE",
        }
    deltas = {}
    exceeded = []
    for key in ("St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"):
        va, vb = float(res_a[key]), float(res_b[key])
        rel = (vb - va) / va * 100.0 if abs(va) > 1e-12 else float("nan")
        deltas[key] = {"base": va, "new": vb, "rel_diff_pct": rel}
        if not math.isfinite(rel) or abs(rel) > tol_pct:
            exceeded.append(f"{key} ({rel:+.3f}% > {tol_pct}%)")

    return {
        "pair": f"{res_a['name']} -> {res_b['name']}",
        "can_compare": True,
        "conclusion": "SENSITIVE" if exceeded else "PASS_WITHIN_1PCT",
        "exceeded": exceeded,
        "deltas": deltas,
    }


# ---------------------------------------------------------------------------
# CLI Subcommands
# ---------------------------------------------------------------------------


def cmd_prepare(args: argparse.Namespace) -> int:
    src = Path(args.model).resolve() if args.model else resolve_default_source_s0_mesh2().resolve()
    dst = Path(args.output).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source model not found: {src}")
    if dst.exists() and not args.allow_overwrite:
        raise FileExistsError(f"Refusing to overwrite existing {dst} without --allow-overwrite")

    src_hash_before = sha256_file(src)
    comsol_root = find_comsol_root()
    comsolbatch = get_comsol_bin_dir(comsol_root) / "comsolbatch.exe"
    scratch = Path(args.scratch).resolve()
    class_file = ensure_java_compiled(comsol_root, scratch / "calib_java", "PrepareC04Calibration", PREPARE_JAVA_SOURCE)

    dst.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = dst.parent / "prepare_manifest.json"
    log_path = scratch / "logs" / f"prepare_c04_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    proc, wall_s = invoke_comsol_batch(
        comsolbatch=comsolbatch,
        class_file=class_file,
        class_args=[str(src), str(dst)],
        ini_file=Path(args.ini).resolve(),
        scratch_root=scratch,
        worker_tag="prep",
        np_val=args.np,
        numasets=args.numasets,
        numafirst=0,
        log_path=log_path,
    )
    src_hash_after = sha256_file(src)
    if src_hash_before != src_hash_after:
        raise RuntimeError("Source model SHA256 changed unexpectedly during prepare!")
    if proc.returncode != 0 or "<<<JSON_START>>>" not in (proc.stdout or "") or not dst.is_file():
        tail = "\n".join((proc.stdout or "").splitlines()[-25:])
        raise RuntimeError(f"prepare failed (exit {proc.returncode}). Log: {log_path}\n{tail}")

    payload = extract_json_block(proc.stdout or "")
    payload["source_sha256_verified_immutable"] = src_hash_after
    payload["output_sha256"] = sha256_file(dst)
    payload["wall_s"] = round(wall_s, 3)
    payload["log_file"] = str(log_path)
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def verify_solver_readback(
    readback: dict[str, Any],
    *,
    rtol: str,
    maxstep_mode: str,
    maxstep_val: str,
    init_step: str,
    consistent: str,
) -> None:
    """Fail closed if post-solve COMSOL solver readback drifted from requested settings."""
    actual_rtol = float(readback["rtol"])
    expected_rtol = float(rtol)
    if not math.isclose(actual_rtol, expected_rtol, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError(f"Solver rtol mismatch: expected {expected_rtol}, got {actual_rtol}")
    if readback.get("maxstepconstraintbdf") != maxstep_mode:
        raise RuntimeError(
            f"Solver maxstepconstraintbdf mismatch: expected {maxstep_mode}, got {readback.get('maxstepconstraintbdf')}"
        )
    if maxstep_mode == "const":
        actual_max = float(readback["maxstepbdf"])
        expected_max = float(maxstep_val)
        if not math.isclose(actual_max, expected_max, rel_tol=1e-9, abs_tol=1e-12):
            raise RuntimeError(f"Solver maxstepbdf mismatch: expected {expected_max}, got {actual_max}")
    if readback.get("consistent") != consistent:
        raise RuntimeError(
            f"Solver consistent mismatch: expected {consistent}, got {readback.get('consistent')}"
        )
    expect_active = bool(init_step and init_step not in {"-", "auto", "AUTO"})
    if bool(readback.get("initialstepbdfactive")) != expect_active:
        raise RuntimeError(
            f"Solver initialstepbdfactive mismatch: expected {expect_active}, got {readback.get('initialstepbdfactive')}"
        )
    if expect_active:
        actual_init = float(readback["initialstepbdf"])
        expected_init = float(init_step)
        if not math.isclose(actual_init, expected_init, rel_tol=1e-9, abs_tol=1e-12):
            raise RuntimeError(f"Solver initialstepbdf mismatch: expected {expected_init}, got {actual_init}")


def _extract_run_artifacts_from_stdout(stdout: str, case_dir: Path) -> None:
    msg_match = re.search(r"<<<T1_MSG_START>>>\r?\n(.*?)\r?\n<<<T1_MSG_END>>>", stdout, flags=re.DOTALL)
    if msg_match:
        (case_dir / "comsol_t1_message.log").write_text(msg_match.group(1), encoding="utf-8")
    for m in re.finditer(
        r"<<<PROBE_TABLE_START:([^>\r\n]+)>>>\r?\n(.*?)\r?\n<<<PROBE_TABLE_END:\1>>>",
        stdout,
        flags=re.DOTALL,
    ):
        (case_dir / m.group(1).strip()).write_text(m.group(2).strip() + "\n", encoding="utf-8")


def _run_one_calibration_case(
    *,
    case_id: str,
    spec: dict[str, str],
    base_model: Path,
    cases_root: Path,
    ini_file: Path,
    scratch: Path,
    class_file: Path,
    comsolbatch: Path,
    t_end: float,
    dt_out: float,
    init_step: str,
    consistent: str,
    save_mph: bool,
    worker_np: int,
    worker_numasets: int,
    worker_numafirst: int,
    mpmode: str = "throughput",
    alloc: str = "scalable",
    priority: str = "abovenormal",
    run_std1: bool = False,
    u0_expr: str = "-",
    alpha_expr: str = "-",
) -> dict[str, Any]:
    case_dir = cases_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    out_mph = case_dir / f"{case_id}.mph"
    tlist = f"range(0, {dt_out}, {t_end})"
    log_path = case_dir / "comsolbatch.log"

    out_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", cases_root.name)[:24] or "root"
    root_hash = hashlib.sha1(str(cases_root).encode("utf-8")).hexdigest()[:6]
    worker_tag = f"run_{out_tag}_{root_hash}_{case_id}"

    proc, wall_s = invoke_comsol_batch(
        comsolbatch=comsolbatch,
        class_file=class_file,
        class_args=[
            str(base_model),
            "save" if save_mph else "nosave",
            str(out_mph),
            tlist,
            spec["rtol"],
            spec["maxstep_mode"],
            spec["maxstep_val"],
            init_step,
            consistent,
            "true" if run_std1 else "false",
            u0_expr,
            alpha_expr,
        ],
        ini_file=ini_file,
        scratch_root=scratch,
        worker_tag=worker_tag,
        np_val=worker_np,
        numasets=worker_numasets,
        numafirst=worker_numafirst,
        log_path=log_path,
        mpmode=mpmode,
        alloc=alloc,
        priority=priority,
    )
    if proc.returncode != 0 or "<<<JSON_START>>>" not in (proc.stdout or ""):
        tail = "\n".join((proc.stdout or "").splitlines()[-25:])
        raise RuntimeError(f"Case {case_id} failed (exit {proc.returncode}). Log: {log_path}\n{tail}")

    raw_res = extract_json_block(proc.stdout or "")
    verify_solver_readback(
        raw_res["solver_readback"],
        rtol=spec["rtol"],
        maxstep_mode=spec["maxstep_mode"],
        maxstep_val=spec["maxstep_val"],
        init_step=init_step,
        consistent=consistent,
    )
    _extract_run_artifacts_from_stdout(proc.stdout or "", case_dir)
    (case_dir / "run_result.json").write_text(json.dumps(raw_res, indent=2, ensure_ascii=False), encoding="utf-8")
    norm = normalize_c04_probe_tables(case_dir)
    step_info = parse_comsol_bdf_step_log(case_dir / "comsol_t1_message.log")
    eval_res = evaluate_c04_series(
        name=case_id,
        t_cl=norm["t_lift"],
        cl=norm["cl"],
        t_cd=norm["t_drag"],
        cd=norm["cd"],
        step_info=step_info,
        solve_wall_s=round(wall_s, 3),
    )
    full_meta = {
        "case_id": case_id,
        "spec": spec,
        "tlist": tlist,
        "init_step": init_step,
        "consistent": consistent,
        "save_mph": save_mph,
        "worker": {
            "np": worker_np,
            "numasets": worker_numasets,
            "numafirst": worker_numafirst,
            "mpmode": mpmode,
            "alloc": alloc,
            "priority": priority,
        },
        "wall_s": round(wall_s, 3),
        "comsol_result": raw_res,
        "probe_normalization": norm["consistency"],
        "evaluation": eval_res,
    }
    (case_dir / "calibration_case_summary.json").write_text(
        json.dumps(full_meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return full_meta


def _resolve_schedule_order(selected: list[str], schedule_order: str | None) -> list[str]:
    if not schedule_order or not schedule_order.strip():
        return list(selected)
    order = [c.strip() for c in schedule_order.split(",") if c.strip()]
    if set(order) != set(selected) or len(order) != len(selected):
        raise ValueError(
            f"--schedule-order {order} must be a permutation of selected cases {selected}"
        )
    return order


def cmd_run(args: argparse.Namespace) -> int:
    base_model = Path(args.base_model).resolve()
    if not base_model.is_file():
        raise FileNotFoundError(
            f"Calibrated base model not found: {base_model}. Run 'prepare' subcommand first."
        )
    cases_root = Path(args.out_dir).resolve()
    cases_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(args.scratch).resolve()
    ini_file = Path(args.ini).resolve()

    selected = [c.strip() for c in args.cases.split(",") if c.strip()]
    for c in selected:
        if c not in CALIBRATION_CASES:
            raise ValueError(f"Unknown case '{c}'. Valid choices: {list(CALIBRATION_CASES)}")

    dispatch_order = _resolve_schedule_order(selected, getattr(args, "schedule_order", None))
    save_mph_set = resolve_save_mph_cases(
        selected,
        save_mph=args.save_mph,
        save_mph_cases=getattr(args, "save_mph_cases", None),
    )

    comsol_root = find_comsol_root()
    comsolbatch = get_comsol_bin_dir(comsol_root) / "comsolbatch.exe"
    class_file = ensure_java_compiled(comsol_root, scratch / "calib_java", "RunC04Calibration", RUN_JAVA_SOURCE)

    base_hash_before = sha256_file(base_model)
    print(f"[INFO] Base model: {base_model} (SHA256: {base_hash_before[:16]}...)", flush=True)
    print(
        f"[INFO] Cases (eval order): {selected}, dispatch={dispatch_order}, "
        f"save_mph={sorted(save_mph_set)}, t_end={args.t_end}s, dt_out={args.dt_out}s, "
        f"workers={args.workers}, mpmode={args.mpmode}, alloc={args.alloc}, priority={args.priority}",
        flush=True,
    )

    by_id: dict[str, dict[str, Any]] = {}
    if args.workers <= 1 or len(selected) == 1:
        single_spec_str = getattr(args, "worker_specs", None)
        if single_spec_str and single_spec_str.strip():
            slot0 = parse_worker_specs(
                workers=1,
                np_per_worker=args.np,
                default_mpmode=args.mpmode,
                worker_specs_str=single_spec_str,
            )[0]
            w_np = int(slot0["np"])
            w_numasets = int(slot0["numasets"])
            w_numafirst = int(slot0["numafirst"])
            w_mpmode = str(slot0["mpmode"])
        else:
            w_np = int(args.np)
            w_numasets = int(args.numasets)
            w_numafirst = int(getattr(args, "numafirst", 0))
            w_mpmode = str(args.mpmode)
        for cid in dispatch_order:
            print(
                f"\n[RUN] {cid} (-np {w_np} -numasets {w_numasets} -numafirst {w_numafirst} -mpmode {w_mpmode} save={cid in save_mph_set})",
                flush=True,
            )
            res = _run_one_calibration_case(
                case_id=cid,
                spec=CALIBRATION_CASES[cid],
                base_model=base_model,
                cases_root=cases_root,
                ini_file=ini_file,
                scratch=scratch,
                class_file=class_file,
                comsolbatch=comsolbatch,
                t_end=args.t_end,
                dt_out=args.dt_out,
                init_step=args.init_step,
                consistent=args.consistent,
                save_mph=(cid in save_mph_set),
                worker_np=w_np,
                worker_numasets=w_numasets,
                worker_numafirst=w_numafirst,
                mpmode=w_mpmode,
                alloc=args.alloc,
                priority=args.priority,
            )
            by_id[cid] = res
            print(f"[DONE] {cid}: status={res['evaluation']['status']}, wall={res['wall_s']}s", flush=True)
    else:
        slot_specs = parse_worker_specs(
            workers=args.workers,
            np_per_worker=args.np_per_worker,
            default_mpmode=args.mpmode,
            worker_specs_str=getattr(args, "worker_specs", None),
        )
        n_slots = min(len(slot_specs), len(dispatch_order))
        work_q: queue.Queue[str] = queue.Queue()
        for cid in dispatch_order:
            work_q.put(cid)
        lock = threading.Lock()

        def _slot_loop(slot_idx: int, slot_cfg: dict[str, Any]) -> None:
            while True:
                try:
                    cid = work_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    with lock:
                        print(
                            f"[RUN slot={slot_idx}] {cid} (-np {slot_cfg['np']} -numasets {slot_cfg['numasets']} "
                            f"-numafirst {slot_cfg['numafirst']} -mpmode {slot_cfg['mpmode']} save={cid in save_mph_set})",
                            flush=True,
                        )
                    res = _run_one_calibration_case(
                        case_id=cid,
                        spec=CALIBRATION_CASES[cid],
                        base_model=base_model,
                        cases_root=cases_root,
                        ini_file=ini_file,
                        scratch=scratch,
                        class_file=class_file,
                        comsolbatch=comsolbatch,
                        t_end=args.t_end,
                        dt_out=args.dt_out,
                        init_step=args.init_step,
                        consistent=args.consistent,
                        save_mph=(cid in save_mph_set),
                        worker_np=int(slot_cfg["np"]),
                        worker_numasets=int(slot_cfg["numasets"]),
                        worker_numafirst=int(slot_cfg["numafirst"]),
                        mpmode=str(slot_cfg["mpmode"]),
                        alloc=args.alloc,
                        priority=args.priority,
                    )
                    with lock:
                        by_id[cid] = res
                        print(
                            f"[DONE slot={slot_idx}] {cid}: status={res['evaluation']['status']}, wall={res['wall_s']}s",
                            flush=True,
                        )
                finally:
                    work_q.task_done()

        with ThreadPoolExecutor(max_workers=n_slots) as pool:
            futs = [pool.submit(_slot_loop, idx, slot_specs[idx]) for idx in range(n_slots)]
            for fut in futs:
                fut.result()

    if sha256_file(base_model) != base_hash_before:
        raise RuntimeError("Calibrated base model SHA256 modified unexpectedly!")

    results = [by_id[cid] for cid in selected]
    evals = [r["evaluation"] for r in results]
    pairwise = [compare_c04_pair(evals[i], evals[i + 1]) for i in range(len(evals) - 1)]
    summary = {
        "timestamp": datetime.now().isoformat(),
        "base_model": str(base_model),
        "t_end_s": args.t_end,
        "dt_out_s": args.dt_out,
        "literature_envelope": C04_LITERATURE_ENVELOPE,
        "cases": results,
        "pairwise_comparisons": pairwise,
    }
    out_json = cases_root / "calibration_summary.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[SAVED] {out_json}", flush=True)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    case_dirs = [Path(p).resolve() for p in args.case_dirs]
    evals: list[dict[str, Any]] = []
    for d in case_dirs:
        norm = normalize_c04_probe_tables(
            d, rho0=args.rho0, u0=args.u0, d_m=args.d_m, l_span_m=args.l_span
        )
        step_info = parse_comsol_bdf_step_log(d / "comsol_t1_message.log")
        eval_res = evaluate_c04_series(
            name=d.name,
            t_cl=norm["t_lift"],
            cl=norm["cl"],
            t_cd=norm["t_drag"],
            cd=norm["cd"],
            d_m=args.d_m,
            u0=args.u0,
            min_shedding_t=args.min_shedding_t,
            min_cycles=args.min_cycles,
            step_info=step_info,
        )
        evals.append(eval_res)

    pairwise = [compare_c04_pair(evals[i], evals[i + 1], tol_pct=args.tol_pct) for i in range(len(evals) - 1)]
    report = {
        "timestamp": datetime.now().isoformat(),
        "literature_envelope": C04_LITERATURE_ENVELOPE,
        "evaluations": evals,
        "pairwise_comparisons": pairwise,
    }
    if args.out_json:
        out_p = Path(args.out_json).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_plan_campaign(args: argparse.Namespace) -> int:
    """Generate C01~C09 Dual-Socket parallel execution plan JSON without launching solves."""
    matrix_path = Path(args.matrix).resolve() if args.matrix else None
    if matrix_path and matrix_path.is_file():
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        conditions = matrix.get("conditions", matrix)
    else:
        # 3x3 U0 x alpha parametric grid from E:\cylinder_3d\三维圆柱绕流数据集方案.png & README.md
        # Outer U0=[0.20, 0.50, 0.80] m/s x Inner alpha=[90, 75, 60] deg places anchor C04 at (0.50 m/s, 90 deg)
        speeds = [0.20, 0.50, 0.80]
        angles = [90, 75, 60]
        conditions = []
        idx = 1
        for u in speeds:
            for a in angles:
                conditions.append(
                    {
                        "case_id": f"C{idx:02d}",
                        "U0": u,
                        "alpha_deg": a,
                        "ReD_approx": round(u * C04_D_M / 1.1889e-6),
                        "needs_std1": not (abs(u - 0.50) < 1e-9 and abs(a - 90.0) < 1e-9),
                    }
                )
                idx += 1

    plan = {
        "schema_version": 1,
        "note": (
            "Verified 3x3 U0 x alpha parametric matrix from E:\\cylinder_3d\\三维圆柱绕流数据集方案.png "
            "& README.md centered on calibrated C04 (U0=0.50 m/s, alpha=90 deg); customize via --matrix JSON."
        ),
        "calibrated_solver": {
            "rtol": "0.0001",
            "maxstepconstraintbdf": "const",
            "maxstepbdf": "0.05",
            "t_end_s": args.t_end,
            "dt_out_s": args.dt_out,
        },
        "dual_xeon_execution": {
            "recommended_mode": "2 concurrent socket-pinned workers for multi-case parametric sweeps",
            "worker_0": {"np": args.np_per_worker, "numasets": 1, "numafirst": 0},
            "worker_1": {"np": args.np_per_worker, "numasets": 1, "numafirst": 1},
            "single_job_mode": {"np": 32, "numasets": 2, "numafirst": 0},
        },
        "conditions": conditions,
    }
    out_p = Path(args.out).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[SAVED] {out_p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="3D C04 COMSOL calibration & verification pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    prep = sub.add_parser(
        "prepare",
        help="Non-destructively fix inlet VelocityField (u0=[Ux,Uz,Uy]) & cylinder probes on base model copy",
    )
    prep.add_argument("--model", default=None, help="Source c04_re126k_s0_mesh2.mph path")
    prep.add_argument("--output", default=str(DEFAULT_CALIB_BASE), help="Calibrated launchpad .mph path")
    prep.add_argument("--ini", default=str(DEFAULT_INI))
    prep.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    prep.add_argument("--np", type=int, default=16)
    prep.add_argument("--numasets", type=int, default=1)
    prep.add_argument("--allow-overwrite", action="store_true", default=False)

    run = sub.add_parser(
        "run",
        help="Execute calibration sweep cases (A/B/C/D) using tuned Dual-Xeon COMSOL batch config",
    )
    run.add_argument("--base-model", default=str(DEFAULT_CALIB_BASE))
    run.add_argument("--out-dir", default=str(DEFAULT_CALIB_ROOT))
    run.add_argument("--ini", default=str(DEFAULT_INI))
    run.add_argument("--scratch", default=str(DEFAULT_SCRATCH))
    run.add_argument("--cases", default="B_rtol1e3,C_rtol1e4_max005,D_rtol1e4_max0025")
    run.add_argument("--t-end", type=float, default=60.0)
    run.add_argument("--dt-out", type=float, default=0.06)
    run.add_argument("--init-step", default="auto", help="BDF initial step in seconds or 'auto'")
    run.add_argument("--consistent", choices=["bweuler", "off"], default="bweuler")
    run.add_argument("--save-mph", action=argparse.BooleanOptionalAction, default=True)
    run.add_argument(
        "--save-mph-cases",
        default=None,
        help="Optional comma-separated subset of cases to save full .mph (e.g. C_rtol1e4_max005, or none/all)",
    )
    run.add_argument("--workers", type=int, default=1, choices=[1, 2, 3, 4])
    run.add_argument("--np", type=int, default=32, help="Cores for 1-worker full-node solve (default: 32)")
    run.add_argument("--numasets", type=int, default=2, help="NUMA sets for 1-worker solve (default: 2)")
    run.add_argument("--numafirst", type=int, default=0, help="Starting NUMA socket for 1-worker solve (default: 0)")
    run.add_argument("--np-per-worker", type=int, default=16, help="Cores per socket for --workers 2")
    run.add_argument(
        "--worker-specs",
        default=None,
        help="Optional per-slot bindings np:numasets:numafirst[:mpmode], comma-separated",
    )
    run.add_argument(
        "--schedule-order",
        default=None,
        help="Optional dispatch permutation of --cases (evaluation/summary remains in --cases order)",
    )
    run.add_argument("--mpmode", choices=["turnaround", "throughput"], default="throughput")
    run.add_argument("--alloc", choices=["auto", "native", "scalable"], default="scalable")
    run.add_argument("--priority", choices=["normal", "abovenormal", "high"], default="abovenormal")

    ev = sub.add_parser(
        "evaluate",
        help="Offline normalization + last-5-cycle stability & tolerance verification on exported probe dirs",
    )
    ev.add_argument("case_dirs", nargs="+", help="One or more case directories containing probe_*.txt")
    ev.add_argument("--rho0", type=float, default=C04_RHO0)
    ev.add_argument("--u0", type=float, default=C04_U0)
    ev.add_argument("--d-m", type=float, default=C04_D_M)
    ev.add_argument("--l-span", type=float, default=C04_L_SPAN_M)
    ev.add_argument("--min-shedding-t", type=float, default=15.0)
    ev.add_argument("--min-cycles", type=int, default=5)
    ev.add_argument("--tol-pct", type=float, default=1.0)
    ev.add_argument("--out-json", default=None)

    plan = sub.add_parser(
        "plan-campaign",
        help="Write C01~C09 Dual-Socket worker execution plan JSON",
    )
    plan.add_argument("--matrix", default=None, help="Optional custom JSON matrix of U0/alpha conditions")
    plan.add_argument("--out", default=r"E:\cfd_3d\02_cases_mph\c01_c09_campaign_plan.json")
    plan.add_argument("--t-end", type=float, default=60.0)
    plan.add_argument("--dt-out", type=float, default=0.06)
    plan.add_argument("--np-per-worker", type=int, default=16)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "evaluate":
        return cmd_evaluate(args)
    if args.cmd == "plan-campaign":
        return cmd_plan_campaign(args)
    raise AssertionError(f"Unhandled command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
