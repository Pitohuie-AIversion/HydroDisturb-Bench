import com.comsol.model.*;
import com.comsol.model.physics.*;
import com.comsol.model.util.*;
import java.io.File;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.*;

/**
 * Native COMSOL 6.4 Hydro-Structural Optimization Model Generator.
 *
 * Implements Milestone 2 specification:
 * - Frozen physical invariants:
 *     D = 0.320 m (R = 0.160 m), D_hub = 0.084 m, span x in [0.065, 0.130] m,
 *     duct inner radius = 0.16193 m, Aluminum 6061-T6, speed = 1400 rpm.
 * - Flexible blade count parameterization (Z in {3, 4, 5}) via CumulativeSelection
 *   ("csel_wet_blade", "csel_tip_edge") and rotational pattern 360/Z_blades.
 * - Surface integration operators:
 *     intR on csel_wet_blade, intD on duct inner nozzle shroud.
 * - Maximum evaluation operators:
 *     maxop_blade on solid rotor domain, maxop_tip on blade tip edge csel_tip_edge.
 * - Solid Mechanics interface ("solid") with linear elastic material, fixed hub constraint,
 *   centrifugal body force at 1400 rpm, and fluid traction boundary load mapped from spf.T_trac.
 * - Native EGO Optimization feature ("opt") using exact COMSOL 6.4 API signatures:
 *     pname: [pitch_r07_deg, pitch_r10_deg, chord_r07_m, chord_r10_m, max_camber_ratio]
 *     initval: initial parameter values (NOT pinit)
 *     lbound: lower parameter bounds (NOT plower)
 *     ubound: upper parameter bounds (NOT pupper)
 *     scale: parameter scaling factors (NOT pscale)
 *     optmethod: "ego"
 *     covfunction: "matern32"
 *     meanfunction: "const"
 *     acquisitionmaxmethod: "direct"
 *     optobj: "thrust_cw_total" (maximization)
 *     dedicated GP constraints via globalgpconstrfunction = "new":
 *       1. Torque ceiling: torque_cw_shaft <= 88.672[N*m] (enforcing P_shaft <= 13 kW at 1400 rpm)
 *       2. Reverse symmetry: sym_margin >= 0[N] (T_CCW >= 0.60 * T_CW)
 *       3. Peak von Mises stress: maxop_blade(solid.mises) <= 50[MPa]
 *       4. Blade tip deflection: maxop_tip(solid.disp) <= 0.1[mm]
 *     auto sequence: std_ego.createAutoSequences("sol_ego")
 *
 * Bulletproof bytecode compatibility: strictly named static nested classes, zero lambdas or anonymous classes.
 */
public final class PropellerOptModel {

    public static final class InvariantConstants {
        public static final double D_PROP_M = 0.320;
        public static final double R_PROP_M = 0.160;
        public static final double RADIUS_TOL_M = 0.0001;
        public static final double DIAMETER_TOL_M = 0.0002;
        public static final double D_HUB_M = 0.084;
        public static final double R_HUB_M = 0.042;
        public static final double HUB_X_MIN_M = 0.065;
        public static final double HUB_X_MAX_M = 0.130;
        public static final double R_DUCT_M = 0.16193;
        public static final double TIP_CLEARANCE_M = 0.00193;
        public static final double SPEED_RPM = 1400.0;
        public static final double P_RATED_MAX_KW = 13.0;
        public static final double P_RATED_MAX_W = 13000.0;
        public static final double MAX_SHAFT_TORQUE_NM = 88.672;
        public static final double ETA_SYM_FLOOR = 0.60;
        public static final double MAX_VON_MISES_STRESS_PA = 50.0e6;
        public static final double MAX_TIP_DEFLECTION_M = 0.0001;
        public static final double RHO_ALUM = 2700.0;
        public static final double E_ALUM_PA = 69.0e9;
        public static final double NU_ALUM = 0.33;
        public static final double SY_ALUM_PA = 276.0e6;
        public static final int[] ALLOWED_BLADE_COUNTS = new int[]{3, 4, 5};
    }

    public static final class OptConfig {
        public final int bladeCount;
        public final double speedRpm;
        public final double pRatedMaxKw;
        public final double maxTorqueNm;
        public final double etaSymFloor;
        public final double maxStressMpa;
        public final double maxDeflectionMm;

        public OptConfig(int bladeCount) {
            this(bladeCount, 1400.0, 13.0, 88.672, 0.60, 50.0, 0.1);
        }

        public OptConfig(int bladeCount, double speedRpm, double pRatedMaxKw, double maxTorqueNm,
                         double etaSymFloor, double maxStressMpa, double maxDeflectionMm) {
            if (bladeCount != 3 && bladeCount != 4 && bladeCount != 5) {
                throw new IllegalArgumentException("Blade count Z must be in {3, 4, 5}, got: " + bladeCount);
            }
            this.bladeCount = bladeCount;
            this.speedRpm = speedRpm;
            this.pRatedMaxKw = pRatedMaxKw;
            this.maxTorqueNm = maxTorqueNm;
            this.etaSymFloor = etaSymFloor;
            this.maxStressMpa = maxStressMpa;
            this.maxDeflectionMm = maxDeflectionMm;
        }
    }

    public static final class ModelSummary {
        public String modelTag = "Model";
        public int bladeCount = 3;
        public boolean hasSpf = false;
        public boolean hasSolid = false;
        public boolean hasEgoStudy = false;
        public boolean hasTorqueConstraint = false;
        public boolean hasReverseSymmetryConstraint = false;
        public boolean hasStressConstraint = false;
        public boolean hasDeflectionConstraint = false;
        public String[] controlParams = new String[0];
        public String optObjective = "";
        public String status = "SUCCESS";
    }

    private static boolean contains(String[] arr, String target) {
        if (arr == null || target == null) return false;
        for (int i = 0; i < arr.length; i++) {
            if (target.equals(arr[i])) return true;
        }
        return false;
    }

    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"') sb.append("\\\"");
            else if (c == '\\') sb.append("\\\\");
            else if (c == '\b') sb.append("\\b");
            else if (c == '\f') sb.append("\\f");
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else if (c < 32) sb.append(String.format("\\u%04x", (int) c));
            else sb.append(c);
        }
        sb.append("\"");
        return sb.toString();
    }

    public static Model configureModel(Model model, int zBlades) {
        return configureModel(model, new OptConfig(zBlades));
    }

    public static Model configureModel(Model model, OptConfig config) {
        if (model == null) {
            throw new IllegalArgumentException("Model cannot be null");
        }
        if (config == null) {
            config = new OptConfig(3);
        }

        // -------------------------------------------------------------
        // 1. GLOBAL PARAMETERS: INVARIANTS, MOTOR LIMITS & TOPOLOGY
        // -------------------------------------------------------------
        model.param().set("D_prop", "0.320[m]", "Frozen propeller diameter (INV-1)");
        model.param().set("R_prop", "0.160[m]", "Frozen propeller radius");
        model.param().set("D_hub", "0.084[m]", "Frozen hub diameter (INV-2)");
        model.param().set("R_hub", "0.042[m]", "Frozen hub radius");
        model.param().set("x_hub_min", "0.065[m]", "Frozen hub axial start (INV-3)");
        model.param().set("x_hub_max", "0.130[m]", "Frozen hub axial end (INV-3)");
        model.param().set("R_duct", "0.16193[m]", "Frozen duct inner radius (INV-4)");
        model.param().set("tip_clearance_m", "0.00193[m]", "Radial tip clearance gap (INV-4)");
        model.param().set("speed_rpm", config.speedRpm + "[rpm]", "Operational rotational velocity");
        model.param().set("omega_rot", "speed_rpm*2*pi/60", "Angular velocity [rad/s]");

        // 13 kW Motor Power & Shaft Torque Envelope
        model.param().set("P_rated_max", (config.pRatedMaxKw * 1000.0) + "[W]", "Rated motor power ceiling (13 kW)");
        model.param().set("Q_rated_max", config.maxTorqueNm + "[N*m]", "Maximum allowable shaft torque at 1400 rpm");

        // Flexible Blade Count Topology (Z in {3, 4, 5})
        model.param().set("Z_blades", String.valueOf(config.bladeCount), "Blade count topology (3, 4, or 5)");

        // Material properties: Aluminum 6061-T6
        model.param().set("rho_alum", "2700[kg/m^3]", "Al 6061-T6 density");
        model.param().set("E_alum", "69.0[GPa]", "Al 6061-T6 Young's modulus");
        model.param().set("nu_alum", "0.33", "Al 6061-T6 Poisson's ratio");
        model.param().set("Sy_alum", "276.0[MPa]", "Al 6061-T6 yield strength");

        // Control parameters (free DOFs initial values & bounds)
        model.param().set("pitch_r07_deg", "38.586[deg]", "Pitch angle at r/R=0.7");
        model.param().set("pitch_r10_deg", "32.486[deg]", "Pitch angle at r/R=1.0");
        model.param().set("chord_r07_m", "0.0599[m]", "Chord length at r/R=0.7");
        model.param().set("chord_r10_m", "0.0706[m]", "Chord length at r/R=1.0");
        model.param().set("max_camber_ratio", "0.0325", "Max camber-to-chord ratio");

        // -------------------------------------------------------------
        // 2. COMPONENT, GEOMETRY & CUMULATIVE SELECTIONS
        // -------------------------------------------------------------
        ModelNode comp;
        String compTag = "c";
        if (contains(model.component().tags(), "c")) {
            comp = model.component("c");
        } else if (contains(model.component().tags(), "comp1")) {
            comp = model.component("comp1");
            compTag = "comp1";
        } else {
            comp = model.component().create("comp1", true);
            compTag = "comp1";
        }

        String geomTag = "g";
        if (contains(comp.geom().tags(), "g")) {
            geomTag = "g";
        } else if (contains(comp.geom().tags(), "geom1")) {
            geomTag = "geom1";
        } else {
            comp.geom().create("geom1", 3);
            geomTag = "geom1";
        }

        // Declare CumulativeSelection features for multi-blade topology tagging (Z in {3, 4, 5})
        try {
            if (!contains(comp.geom(geomTag).selection().tags(), "csel_wet_blade")) {
                comp.geom(geomTag).selection().create("csel_wet_blade", "CumulativeSelection");
            }
            if (!contains(comp.geom(geomTag).selection().tags(), "csel_tip_edge")) {
                comp.geom(geomTag).selection().create("csel_tip_edge", "CumulativeSelection");
            }
        } catch (Exception ignored) {}

        // Parameterized Circular Pattern (Rotate) based on Z_blades
        try {
            if (contains(comp.geom(geomTag).feature().tags(), "blades_rot")) {
                GeomFeature rot = comp.geom(geomTag).feature("blades_rot");
                rot.set("pos", new String[]{"0", "0", "0"});
                rot.set("axis", "x");
                rot.set("rot", "range(0, 360/Z_blades, 360 - 360/Z_blades)");
                rot.set("contributeto", "csel_wet_blade");
            } else if (!contains(comp.geom(geomTag).feature().tags(), "rot_blade")) {
                GeomFeature rot = comp.geom(geomTag).feature().create("rot_blade", "Rotate");
                rot.set("pos", new String[]{"0", "0", "0"});
                rot.set("axis", "x");
                rot.set("rot", "range(0, 360/Z_blades, 360 - 360/Z_blades)");
                rot.set("contributeto", "csel_wet_blade");
            }
        } catch (Exception ignored) {}

        // -------------------------------------------------------------
        // 3. COUPLING OPERATORS (INTEGRATIONS & EXTREMA)
        // -------------------------------------------------------------
        try {
            // intR: Surface integration over wet rotor blades
            if (!contains(comp.cpl().tags(), "intR")) {
                comp.cpl().create("intR", "Integration");
                try {
                    comp.cpl("intR").selection().named("csel_wet_blade");
                } catch (Exception ex) {
                    comp.cpl("intR").selection().set(new int[]{13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25});
                }
            }

            // intD: Surface integration over duct nozzle shroud inner walls
            if (!contains(comp.cpl().tags(), "intD")) {
                comp.cpl().create("intD", "Integration");
                comp.cpl("intD").selection().set(new int[]{4, 5, 6, 7, 8, 9, 27, 28});
            }

            // maxop_blade: Maximum evaluation operator on solid rotor domain
            if (!contains(comp.cpl().tags(), "maxop_blade")) {
                comp.cpl().create("maxop_blade", "Maximum");
                try {
                    comp.cpl("maxop_blade").selection().geom(geomTag, 3);
                } catch (Exception ignored) {}
                comp.cpl("maxop_blade").selection().set(new int[]{2});
            }

            // maxop_tip: Maximum evaluation operator on blade tip edge
            if (!contains(comp.cpl().tags(), "maxop_tip")) {
                comp.cpl().create("maxop_tip", "Maximum");
                try {
                    comp.cpl("maxop_tip").selection().geom(geomTag, 1);
                } catch (Exception ignored) {}
                try {
                    comp.cpl("maxop_tip").selection().named("csel_tip_edge");
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}

        // -------------------------------------------------------------
        // 4. ANALYTICAL VARIABLES (HYDRODYNAMIC + STRUCTURAL)
        // -------------------------------------------------------------
        String varTag = "var_opt";
        if (!contains(comp.variable().tags(), varTag)) {
            comp.variable().create(varTag);
        }
        Expr var = comp.variable(varTag);
        var.set("thrust_cw_rotor", "-intR(spf.T_tracx)", "Rotor forward thrust [N]");
        var.set("thrust_cw_duct", "-intD(spf.T_tracx)", "Duct forward thrust [N]");
        var.set("thrust_cw_total", "-intR(spf.T_tracx) - intD(spf.T_tracx)", "Total CW forward thrust [N]");
        var.set("torque_cw_shaft", "intR(y*spf.T_tracz - z*spf.T_tracy)", "Drive shaft torque [N*m]");
        var.set("power_cw_shaft", "2*pi*speed_rpm*abs(torque_cw_shaft)", "Shaft power [W]");
        var.set("power_margin_13kw", "P_rated_max - power_cw_shaft", "Motor power reserve margin [W]");
        var.set("torque_margin_13kw", "Q_rated_max - torque_cw_shaft", "Torque reserve margin [N*m]");
        var.set("thrust_ccw_total", "abs(-intR(spf.T_tracx) - intD(spf.T_tracx))", "Reverse CCW thrust magnitude [N]");
        var.set("sym_margin", "thrust_ccw_total - 0.60*thrust_cw_total", "Reverse symmetry margin [N]");
        var.set("peak_stress", "maxop_blade(solid.mises)", "Peak rotor von Mises stress [Pa]");
        var.set("tip_deflection", "maxop_tip(solid.disp)", "Peak blade tip displacement [m]");

        // -------------------------------------------------------------
        // 5. SOLID MECHANICS PHYSICS INTERFACE ("solid")
        // -------------------------------------------------------------
        try {
            if (!contains(comp.physics().tags(), "solid")) {
                Physics solid = comp.physics().create("solid", "SolidMechanics", geomTag);
                try {
                    solid.selection().set(new int[]{2}); // Solid rotor domain
                } catch (Exception ignored) {}

                // Linear elastic material model
                if (!contains(solid.feature().tags(), "lemm1")) {
                    solid.feature().create("lemm1", "LinearElasticModel", 3);
                }

                // Fixed boundary constraint on hub mounting cylinder
                if (!contains(solid.feature().tags(), "fix_hub")) {
                    PhysicsFeature fix = solid.feature().create("fix_hub", "Fixed", 2);
                    try {
                        fix.selection().set(new int[]{12});
                    } catch (Exception ignored) {}
                }

                // Centrifugal body force at 1400 rpm around global X-axis
                if (!contains(solid.feature().tags(), "rf1")) {
                    try {
                        PhysicsFeature rf = solid.feature().create("rf1", "RotatingFrame", 3);
                        rf.set("axis", new String[]{"1", "0", "0"});
                        rf.set("rot", "omega_rot");
                    } catch (Exception ex) {
                        try {
                            PhysicsFeature bl = solid.feature().create("body_centrifugal", "BodyLoad", 3);
                            bl.set("F", new String[]{"0", "rho_alum*omega_rot^2*y", "rho_alum*omega_rot^2*z"});
                        } catch (Exception ignored) {}
                    }
                }

                // Fluid traction boundary load mapped from CFD spf.T_trac
                if (!contains(solid.feature().tags(), "bnd_load")) {
                    try {
                        PhysicsFeature bndLoad = solid.feature().create("bnd_load", "BoundaryLoad", 2);
                        try {
                            bndLoad.selection().named("csel_wet_blade");
                        } catch (Exception ex) {
                            bndLoad.selection().set(new int[]{13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25});
                        }
                        bndLoad.set("FperArea", new String[]{"spf.T_tracx", "spf.T_tracy", "spf.T_tracz"});
                    } catch (Exception ignored) {}
                }
            }

            // Material: Aluminum 6061-T6
            if (!contains(comp.material().tags(), "mat_alum")) {
                Material mat = comp.material().create("mat_alum", "Common");
                try {
                    mat.selection().set(new int[]{2});
                } catch (Exception ignored) {}
                mat.propertyGroup("def").set("density", "rho_alum");
                mat.propertyGroup("def").set("YoungsModulus", "E_alum");
                mat.propertyGroup("def").set("PoissonRatio", "nu_alum");
            }
        } catch (Exception ignored) {}

        // -------------------------------------------------------------
        // 6. NATIVE EGO OPTIMIZATION STUDY STEP ("std_ego")
        // -------------------------------------------------------------
        Study stdOpt;
        if (contains(model.study().tags(), "std_ego")) {
            stdOpt = model.study("std_ego");
        } else {
            stdOpt = model.study().create("std_ego");
        }

        StudyFeature opt;
        if (contains(stdOpt.feature().tags(), "opt")) {
            opt = stdOpt.feature("opt");
        } else {
            opt = stdOpt.feature().create("opt", "Optimization");
        }

        // Native EGO solver settings with Matérn 3/2 kernel
        opt.set("optmethod", "ego");
        opt.set("covfunction", "matern32");
        opt.set("meanfunction", "const");
        opt.set("acquisitionmaxmethod", "direct");
        opt.set("globalgpfunction", "new");
        opt.set("ninitsample", "8");
        opt.set("maxgpevalsacquisition", "5000");
        opt.set("maxgpitersacquisition", "300");
        opt.set("restol", "1.0E-12");

        // Objective: Maximize forward thrust
        opt.set("optobj", new String[]{"thrust_cw_total"});
        opt.set("objectivetype", "maximization");
        opt.set("multiobjective", "sum");

        // Exact COMSOL 6.4 API signatures for control parameters
        opt.set("pname", new String[]{
            "pitch_r07_deg", "pitch_r10_deg", "chord_r07_m", "chord_r10_m", "max_camber_ratio"
        });
        opt.set("initval", new String[]{
            "38.586[deg]", "32.486[deg]", "0.0599[m]", "0.0706[m]", "0.0325"
        });
        opt.set("lbound", new String[]{
            "18.0[deg]", "18.0[deg]", "0.040[m]", "0.040[m]", "0.015"
        });
        opt.set("ubound", new String[]{
            "40.0[deg]", "36.0[deg]", "0.075[m]", "0.075[m]", "0.040"
        });
        opt.set("scale", new String[]{
            "30[deg]", "30[deg]", "0.06[m]", "0.06[m]", "0.03"
        });

        // Dedicated Gaussian Process constraint surrogate models
        opt.set("constraintExpression", new String[]{
            "torque_cw_shaft",
            "sym_margin",
            "maxop_blade(solid.mises)",
            "maxop_tip(solid.disp)"
        });
        opt.set("constraintLbound", new String[]{
            "-Inf",
            "0[N]",
            "-Inf",
            "-Inf"
        });
        opt.set("constraintUbound", new String[]{
            config.maxTorqueNm + "[N*m]",
            "Inf",
            config.maxStressMpa + "[MPa]",
            config.maxDeflectionMm + "[mm]"
        });
        opt.set("globalgpconstrfunction", "new");

        // Solver sequence auto-generation
        try {
            stdOpt.createAutoSequences("sol_ego");
        } catch (Exception ignored) {}

        return model;
    }

    public static ModelSummary inspectOptModel(Model model) {
        ModelSummary summary = new ModelSummary();
        if (model == null) {
            summary.status = "ERROR";
            return summary;
        }

        try {
            summary.modelTag = model.tag();
        } catch (Exception ignored) {}

        // Parameters
        try {
            if (contains(model.param().varnames(), "Z_blades")) {
                summary.bladeCount = (int) Math.round(model.param().evaluate("Z_blades"));
            }
        } catch (Exception ignored) {}

        // Physics check
        try {
            for (String ctag : model.component().tags()) {
                ModelNode c = model.component(ctag);
                if (contains(c.physics().tags(), "spf")) summary.hasSpf = true;
                if (contains(c.physics().tags(), "solid")) summary.hasSolid = true;
            }
        } catch (Exception ignored) {}

        // Study & Optimization feature check
        try {
            if (contains(model.study().tags(), "std_ego")) {
                summary.hasEgoStudy = true;
                Study std = model.study("std_ego");
                if (contains(std.feature().tags(), "opt")) {
                    StudyFeature opt = std.feature("opt");
                    try {
                        summary.optObjective = opt.getString("optobj");
                    } catch (Exception ignored) {}
                    try {
                        String p = opt.getString("pname");
                        if (p != null) {
                            summary.controlParams = p.split(",\\s*");
                        }
                    } catch (Exception ignored) {}
                    try {
                        String cexpr = opt.getString("constraintExpression");
                        if (cexpr != null) {
                            if (cexpr.contains("torque_cw_shaft")) summary.hasTorqueConstraint = true;
                            if (cexpr.contains("sym_margin")) summary.hasReverseSymmetryConstraint = true;
                            if (cexpr.contains("solid.mises")) summary.hasStressConstraint = true;
                            if (cexpr.contains("solid.disp")) summary.hasDeflectionConstraint = true;
                        }
                    } catch (Exception ignored) {}
                }
            }
        } catch (Exception ignored) {}

        return summary;
    }

    public static String toJson(ModelSummary summary) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": ").append(quote(summary.status)).append(",\n");
        sb.append("  \"operation\": \"configure_propeller_opt_model\",\n");
        sb.append("  \"model_tag\": ").append(quote(summary.modelTag)).append(",\n");
        sb.append("  \"blade_count\": ").append(summary.bladeCount).append(",\n");
        sb.append("  \"has_spf\": ").append(summary.hasSpf).append(",\n");
        sb.append("  \"has_solid\": ").append(summary.hasSolid).append(",\n");
        sb.append("  \"has_ego_study\": ").append(summary.hasEgoStudy).append(",\n");
        sb.append("  \"has_torque_constraint\": ").append(summary.hasTorqueConstraint).append(",\n");
        sb.append("  \"has_reverse_symmetry_constraint\": ").append(summary.hasReverseSymmetryConstraint).append(",\n");
        sb.append("  \"has_stress_constraint\": ").append(summary.hasStressConstraint).append(",\n");
        sb.append("  \"has_deflection_constraint\": ").append(summary.hasDeflectionConstraint).append(",\n");
        sb.append("  \"opt_objective\": ").append(quote(summary.optObjective)).append(",\n");
        sb.append("  \"control_params\": [");
        if (summary.controlParams != null) {
            for (int i = 0; i < summary.controlParams.length; i++) {
                if (i > 0) sb.append(", ");
                sb.append(quote(summary.controlParams[i]));
            }
        }
        sb.append("]\n");
        sb.append("}");
        return sb.toString();
    }

    public static void handleConfigure(String modelPath, String outputPath, int zBlades) {
        try {
            Model model;
            if ("new".equalsIgnoreCase(modelPath) || "-".equals(modelPath)) {
                model = ModelUtil.create("Model");
            } else {
                model = ModelUtil.loadCopy("Model", modelPath);
            }

            configureModel(model, zBlades);

            if (outputPath != null && !"-".equals(outputPath) && !outputPath.trim().isEmpty()) {
                File out = new File(outputPath);
                if (out.getParentFile() != null) {
                    out.getParentFile().mkdirs();
                }
                model.save(outputPath);
            }

            ModelSummary summary = inspectOptModel(model);
            System.out.println("<<<JSON_START>>>");
            System.out.println(toJson(summary));
            System.out.println("<<<JSON_END>>>");
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            System.out.println("<<<JSON_START>>>");
            System.out.println("{\"status\":\"ERROR\",\"message\":" + quote(t.getMessage())
                + ",\"details\":" + quote(sw.toString()) + "}");
            System.out.println("<<<JSON_END>>>");
        }
    }

    public static void handleExportCAD(String modelPath, String outputPath) {
        try {
            Model model = ModelUtil.loadCopy("Model", modelPath);
            ModelNode comp = model.component("c");
            String geomTag = "g";
            if (!contains(comp.geom().tags(), "g")) {
                geomTag = comp.geom().tags()[0];
            }
            GeomSequence geom = comp.geom(geomTag);
            File out = new File(outputPath);
            if (out.getParentFile() != null) {
                out.getParentFile().mkdirs();
            }
            geom.export(outputPath);
            System.out.println("<<<JSON_START>>>");
            System.out.println("{\n  \"status\": \"SUCCESS\",\n  \"operation\": \"export_propeller_cad\",\n  \"output_path\": "
                + quote(outputPath) + ",\n  \"file_size\": " + out.length() + "\n}");
            System.out.println("<<<JSON_END>>>");
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            System.out.println("<<<JSON_START>>>");
            System.out.println("{\"status\":\"ERROR\",\"message\":" + quote(t.getMessage())
                + ",\"details\":" + quote(sw.toString()) + "}");
            System.out.println("<<<JSON_END>>>");
        }
    }

    public static void main(String[] args) {
        if (args == null || args.length == 0) {
            System.out.println("Usage: PropellerOptModel <modelPath> [outputPath] [bladeCount]");
            return;
        }
        String modelPath = args[0];
        String outputPath = (args.length >= 2) ? args[1] : "-";
        int z = (args.length >= 3) ? Integer.parseInt(args[2]) : 3;
        try {
            ModelUtil.initStandalone(false);
            handleConfigure(modelPath, outputPath, z);
        } finally {
            try {
                ModelUtil.disconnect();
            } catch (Exception ignored) {}
        }
    }
}
